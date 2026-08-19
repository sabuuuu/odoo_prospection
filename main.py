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
    logger.info("🚀 Démarrage du pipeline de prospection automatisé")
    if dry_run:
        logger.info("⚠️  MODE SIMULATION (DRY RUN) ACTIVÉ : Aucune écriture dans Odoo.")
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

    # Récupération des IDs d'étapes et des Étiquettes
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
            logger.info(f"\n🔎 Recherche Pappers (Page {page} | NAF: {naf_query} | Zone: {dep_display} | Objectif: {limit - total_added} restants)...")
            
            companies = pappers.search_companies(
                code_naf=naf_query,
                departement=dep,
                ca_min=Config.MIN_TURNOVER,
                limit=20,
                page=page
            )

            if not companies:
                logger.info("Fin des résultats disponibles sur Pappers.")
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
                    logger.info(f"⏭️  [DOUBLON] '{name}' (SIREN: {siren}) est déjà dans Odoo. Ignoré.")
                    total_skipped += 1
                    continue

                logger.info(f"✨ Nouveau prospect : '{name}' ({city}) - SIREN: {siren}")

                # 5. Enrichissement Web + IA
                logger.info(f"🧠 Recherche & qualification IA pour '{name}'...")
                
                # On passe les informations de base au script d'enrichissement pour la Note
                contact_info = enricher.enrich_contact(
                    company_name=name,
                    city=city,
                    dirigeant=dirigeant,
                    tranche_effectif=company.get("tranche_effectif", ""),
                    annee_ouverture=company.get("annee_ouverture", "")
                )

                # Formatage du contact dirigeant
                contact_name = contact_info.get("contact_name")
                if not contact_name and dirigeant:
                    contact_name = f"{dirigeant.get('prenom', '')} {dirigeant.get('nom', '')}".strip()
                
                full_contact = f"{name}, {contact_name}" if contact_name else name
                contact_role = contact_info.get("job_title") or (dirigeant.get('qualite') if dirigeant else "Gérant")

                # 6. Champs standards Odoo
                lead_data = {
                    'name': name,  # Titre épuré comme "MONSIEUR JEAN"
                    'partner_name': name,
                    'contact_name': full_contact,
                    'function': contact_role,
                    'email_from': contact_info.get("email") or False,
                    'phone': contact_info.get("phone") or False,
                    'website': contact_info.get("website") or False,
                    'street': company.get("adresse") or False,
                    'city': city or False,
                    'zip': company.get("code_postal") or False,
                    'description': contact_info.get("summary", "") # Note IA détaillée
                }

                # 7. Valeurs des champs personnalisés
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

                # Tags dynamiques
                tags_to_add = []
                if tag_id_oxo:
                    tags_to_add.append((4, tag_id_oxo))
                if dirigeant and tag_id_dirigeant:
                    tags_to_add.append((4, tag_id_dirigeant))

                # 8. Insertion dans Odoo
                if dry_run:
                    logger.info(f"[SIMULATION] Lead qui serait créé : {lead_data['name']} avec SIREN {siren} et étiquettes {tags_to_add}")
                    total_added += 1
                else:
                    lead_id = odoo.create_lead(
                        lead_data=lead_data,
                        custom_values=custom_values,
                        stage_id=target_stage_id,
                        tag_ids=tags_to_add if tags_to_add else None
                    )
                    if lead_id:
                        total_added += 1

            page += 1

    logger.info("=" * 60)
    logger.info("📊 BILAN DE L'EXÉCUTION DU PIPELINE")
    logger.info(f"   • Nouveaux prospects ajoutés : {total_added}")
    logger.info(f"   • Doublons évités : {total_skipped}")
    logger.info("=" * 60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline de Prospection Automatisé")
    parser.add_argument("--dry-run", action="store_true", help="Mode simulation")
    parser.add_argument("--limit", type=int, help="Nombre maximum de prospects")
    args = parser.parse_args()

    run_pipeline(dry_run=args.dry_run, limit_override=args.limit)
