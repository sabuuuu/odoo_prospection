import argparse
import logging
import sys
from config import Config
from odoo_client import OdooClient
from pappers_client import PappersClient
from enricher import ContactEnricher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("ProspectionPipeline")

def run_pipeline(dry_run: bool = False, limit_override: int = None):
    logger.info("=" * 60)
    logger.info("\U0001f680 D\u00e9marrage du pipeline de prospection automatis\u00e9")
    if dry_run:
        logger.info("\u26a0\ufe0f  MODE SIMULATION (DRY RUN) ACTIV\u00c9 : Aucune \u00e9criture dans Odoo.")
    logger.info("=" * 60)

    try:
        Config.validate()
    except ValueError as e:
        logger.error(f"Erreur de configuration : {e}")
        sys.exit(1)

    logger.info("Connexion aux services (Odoo, Pappers, Claude AI)...")
    odoo = OdooClient(
        url=Config.ODOO_URL,
        db=Config.ODOO_DB,
        username=Config.ODOO_USER,
        api_key=Config.ODOO_API_KEY
    )
    pappers = PappersClient(api_key=Config.PAPPERS_API_KEY)
    enricher = ContactEnricher(
        anthropic_key=Config.ANTHROPIC_API_KEY,
        serper_key=Config.SERPER_API_KEY,
        model=Config.CLAUDE_MODEL
    )

    # R\u00e9cup\u00e9ration des IDs d'\u00e9tapes et des \u00c9tiquettes
    target_stage_id = odoo.get_stage_id(Config.ODOO_TARGET_STAGE)
    dedup_stage_ids = odoo.get_stage_ids(Config.ODOO_DEDUP_STAGES)
    
    tag_id_oxo = odoo.get_or_create_tag("Prospection IA OXO")
    tag_id_dirigeant = odoo.get_or_create_tag("Dirigeant")

    limit = limit_override or Config.DAILY_PROSPECT_LIMIT
    total_added = 0
    total_skipped = 0

    naf_query = ",".join(Config.TARGET_NAF_CODES)
    dep_list = Config.TARGET_DEPARTMENTS if Config.TARGET_DEPARTMENTS else [None]

    for dep in dep_list:
        page = 1
        max_pages = 10

        while total_added < limit and page <= max_pages:
            dep_display = f"Dept {dep}" if dep else "Toute France"
            logger.info(f"\n\U0001f50e Recherche Pappers (Page {page} | NAF: {naf_query} | Zone: {dep_display} | Objectif: {limit - total_added} restants)...")
            
            companies = pappers.search_companies(
                code_naf=naf_query,
                departement=dep,
                ca_min=Config.MIN_TURNOVER,
                limit=20,
                page=page
            )

            if not companies:
                logger.info("Fin des r\u00e9sultats disponibles sur Pappers.")
                break

            for company in companies:
                if total_added >= limit:
                    break

                siren = company["siren"]
                name = company["denomination"]
                city = company["ville"]
                dirigeant = company["dirigeant"]

                # 4. Anti-doublon Odoo
                if odoo.lead_or_partner_exists(siren, name, stage_ids=dedup_stage_ids):
                    logger.info(f"\u23ed\ufe0f  [DOUBLON] '{name}' (SIREN: {siren}) est d\u00e9j\u00e0 dans Odoo. Ignor\u00e9.")
                    total_skipped += 1
                    continue

                logger.info(f"\u2728 Nouveau prospect : '{name}' ({city}) - SIREN: {siren}")

                # 5. Enrichissement Web + IA (avec SIREN pour fallback data.gouv.fr)
                logger.info(f"\U0001f9e0 Recherche & qualification IA pour '{name}'...")
                contact_info = enricher.enrich_contact(
                    company_name=name,
                    city=city,
                    siren=siren,
                    dirigeant=dirigeant,
                    tranche_effectif=company.get("tranche_effectif", ""),
                    annee_ouverture=company.get("annee_ouverture", "")
                )

                # Formatage du contact dirigeant
                contact_name = contact_info.get("contact_name")
                if not contact_name and dirigeant:
                    contact_name = f"{dirigeant.get('prenom', '')} {dirigeant.get('nom', '')}".strip()
                
                full_contact = f"{name}, {contact_name}" if contact_name else name
                contact_role = contact_info.get("job_title") or (dirigeant.get('qualite') if dirigeant else "G\u00e9rant")
                
                director_phone = contact_info.get("phone")
                director_email = contact_info.get("email")
                director_has_real_contact = bool(director_phone or director_email)

                # ========== 6. CR\u00c9ATION DES CONTACTS (res.partner) ==========
                company_partner_id = None
                director_partner_id = None
                
                if not dry_run:
                    # 6a. Toujours cr\u00e9er le contact entreprise (restaurant)
                    company_partner_id = odoo.create_company_contact({
                        'name': name,
                        'street': company.get("adresse"),
                        'city': city,
                        'zip': company.get("code_postal"),
                        'phone': director_phone if not contact_name else False,
                        'email': director_email if not contact_name else False,
                        'website': contact_info.get("website"),
                        'comment': f"SIREN: {siren} | NAF: {company.get('code_naf')} {company.get('libelle_code_naf', '')} | Effectif: {company.get('tranche_effectif', 'N/A')}"
                    })
                    
                    # 6b. Si on a trouv\u00e9 un dirigeant avec de vraies coordonn\u00e9es, on cr\u00e9e aussi son contact individuel
                    if contact_name and director_has_real_contact and company_partner_id:
                        director_partner_id = odoo.create_director_contact({
                            'name': contact_name,
                            'function': contact_role,
                            'phone': director_phone,
                            'email': director_email,
                            'comment': contact_info.get("summary", "")
                        }, company_partner_id)

                # ========== 7. CHAMPS STANDARDS LEAD ==========
                lead_data = {
                    'name': name,
                    'partner_name': name,
                    'contact_name': full_contact,
                    'function': contact_role,
                    'email_from': director_email or False,
                    'phone': director_phone or False,
                    'website': contact_info.get("website") or False,
                    'street': company.get("adresse") or False,
                    'city': city or False,
                    'zip': company.get("code_postal") or False,
                    'description': contact_info.get("summary", "")
                }

                # 8. Valeurs des champs personnalis\u00e9s
                custom_values = {
                    'siren': siren,
                    'annee_ouverture': company.get("annee_ouverture"),
                    'raison_sociale': company.get("raison_sociale"),
                    'forme_juridique': company.get("forme_juridique"),
                    'nombre_salaries': company.get("tranche_effectif"),
                    'naf': f"{company.get('code_naf')} {company.get('libelle_code_naf', '')}".strip(),
                    'linkedin': contact_info.get("linkedin_url"),
                    'chiffre_affaires': float(company.get("chiffre_affaires")) if company.get("chiffre_affaires") else None,
                    'annee_ca': str(company.get("annee_ca")) if company.get("annee_ca") else None
                }

                # 9. Tags dynamiques
                tags_to_add = []
                if tag_id_oxo:
                    tags_to_add.append((4, tag_id_oxo))
                # Tag "Dirigeant" UNIQUEMENT si on a des coordonn\u00e9es r\u00e9elles (t\u00e9l\u00e9phone ou email)
                if director_has_real_contact and tag_id_dirigeant:
                    tags_to_add.append((4, tag_id_dirigeant))
                    logger.info(f"\U0001f3f7\ufe0f  Tag 'Dirigeant' activ\u00e9 pour '{name}' (T\u00e9l: {director_phone}, Email: {director_email})")

                # 10. Insertion du Lead dans Odoo
                if dry_run:
                    logger.info(f"[SIMULATION] Lead : {lead_data['name']} | Contact entreprise + dirigeant | Tags: {tags_to_add}")
                    total_added += 1
                else:
                    lead_id = odoo.create_lead(
                        lead_data=lead_data,
                        custom_values=custom_values,
                        stage_id=target_stage_id,
                        tag_ids=tags_to_add if tags_to_add else None,
                        partner_id=company_partner_id
                    )
                    if lead_id:
                        total_added += 1

            page += 1

    logger.info("=" * 60)
    logger.info("\U0001f4ca BILAN DE L'EX\u00c9CUTION DU PIPELINE")
    logger.info(f"   \u2022 Nouveaux prospects ajout\u00e9s : {total_added}")
    logger.info(f"   \u2022 Doublons \u00e9vit\u00e9s : {total_skipped}")
    logger.info("=" * 60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline de Prospection Automatis\u00e9")
    parser.add_argument("--dry-run", action="store_true", help="Mode simulation")
    parser.add_argument("--limit", type=int, help="Nombre maximum de prospects")
    args = parser.parse_args()

    run_pipeline(dry_run=args.dry_run, limit_override=args.limit)
