import argparse
import logging
import sys
from config import Config
from odoo_client import OdooClient
from pappers_client import PappersClient
from enricher import ContactEnricher

# Configuration du format des logs
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

    # 1. Validation de la configuration
    try:
        Config.validate()
    except ValueError as e:
        logger.error(f"Erreur de configuration : {e}")
        sys.exit(1)

    # 2. Initialisation des clients
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

    # Récupération des IDs d'étapes Odoo configurées
    target_stage_id = odoo.get_stage_id(Config.ODOO_TARGET_STAGE)
    if target_stage_id:
        logger.info(f"Étape cible configurée : '{Config.ODOO_TARGET_STAGE}' (ID: {target_stage_id})")
    else:
        logger.warning(f"L'étape '{Config.ODOO_TARGET_STAGE}' n'a pas été trouvée dans Odoo. Le lead sera placé dans l'étape par défaut.")

    dedup_stage_ids = odoo.get_stage_ids(Config.ODOO_DEDUP_STAGES)
    logger.info(f"Étapes surveillées pour anti-doublon : {Config.ODOO_DEDUP_STAGES} -> IDs {dedup_stage_ids}")

    limit = limit_override or Config.DAILY_PROSPECT_LIMIT
    total_added = 0
    total_skipped = 0

    # 3. Requête Pappers globale avec pagination
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

                # 4. Dédoublonnage avec la base Odoo
                if odoo.lead_or_partner_exists(siren, name, stage_ids=dedup_stage_ids):
                    logger.info(f"⏭️  [DOUBLON] '{name}' (SIREN: {siren}) est déjà présent dans Odoo. Ignoré.")
                    total_skipped += 1
                    continue

                logger.info(f"✨ Nouveau prospect détecté : '{name}' ({city}) - SIREN: {siren}")

                # 5. Enrichissement IA du contact & coordonnées (TalorData + Claude AI)
                logger.info(f"🧠 Recherche Web & enrichissement IA pour '{name}'...")
                contact_info = enricher.enrich_contact(
                    company_name=name,
                    city=city,
                    dirigeant=dirigeant
                )

                # 6. Formatage de la Piste pour Odoo CRM avec le type de restaurant
                restaurant_type = company.get("libelle_code_naf") or "Restauration"
                contact_name = contact_info.get("contact_name")
                if not contact_name and dirigeant:
                    contact_name = f"{dirigeant.get('prenom', '')} {dirigeant.get('nom', '')}".strip()

                contact_role = contact_info.get("job_title") or (dirigeant.get('qualite') if dirigeant else "")
                ca_val = company.get("chiffre_affaires")
                ca_str = f"{ca_val:,} €" if ca_val else "Non communiqué"
                
                description_parts = [
                    f"=== TYPE D'ÉTABLISSEMENT : {restaurant_type.upper()} ===",
                    f"Catégorie : {restaurant_type}",
                    f"Code NAF : {company.get('code_naf')}",
                    f"SIREN : {siren}",
                    f"SIRET : {company.get('siret', '')}",
                    f"Chiffre d'affaires : {ca_str}",
                    f"Effectif : {company.get('tranche_effectif', 'Non précisé')}",
                    f"Date création : {company.get('date_creation', 'Inconnue')}",
                    "",
                    "=== ENRICHISSEMENT IA (CLAUDE & WEB) ===",
                    f"LinkedIn : {contact_info.get('linkedin_url') or 'Non trouvé'}",
                    f"Site Web : {contact_info.get('website') or 'Non trouvé'}",
                    f"Résumé / Spécialité : {contact_info.get('summary', '')}"
                ]

                lead_data = {
                    'name': f"Prospection - {name} [{restaurant_type}]",
                    'partner_name': name,
                    'contact_name': contact_name or name,
                    'function': contact_role or "Direction",
                    'email_from': contact_info.get("email") or False,
                    'phone': contact_info.get("phone") or False,
                    'website': contact_info.get("website") or False,
                    'street': company.get("adresse") or False,
                    'city': city or False,
                    'zip': company.get("code_postal") or False,
                    'description': "\n".join(description_parts)
                }

                # 7. Insertion dans Odoo
                if dry_run:
                    logger.info(f"[SIMULATION] Lead qui serait créé dans Odoo : {lead_data['name']}")
                    total_added += 1
                else:
                    lead_id = odoo.create_lead(lead_data, stage_id=target_stage_id)
                    if lead_id:
                        total_added += 1

            page += 1

    # 8. Rapport final
    logger.info("=" * 60)
    logger.info("📊 BILAN DE L'EXÉCUTION DU PIPELINE")
    logger.info(f"   • Nouveaux prospects ajoutés à Odoo : {total_added}")
    logger.info(f"   • Doublons évités : {total_skipped}")
    logger.info("=" * 60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline de Prospection Automatisé")
    parser.add_argument("--dry-run", action="store_true", help="Exécute la recherche et l'enrichissement sans écrire dans Odoo")
    parser.add_argument("--limit", type=int, help="Nombre maximum de prospects à traiter pour ce run")
    args = parser.parse_args()

    run_pipeline(dry_run=args.dry_run, limit_override=args.limit)
