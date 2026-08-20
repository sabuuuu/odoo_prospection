import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from config import Config
from odoo_client import OdooClient
from pappers_client import PappersClient
from enricher import ContactEnricher

def setup_logging():
    """Configure un système de logging propre avec console et fichier rotatif."""
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        "prospection.log",
        maxBytes=5 * 1024 * 1024, # 5 Mo
        backupCount=3,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers = []
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

setup_logging()
logger = logging.getLogger("ProspectionPipeline")

def run_pipeline(dry_run: bool = False, limit_override: int = None):
    logger.info("=" * 60)
    logger.info("🚀 Démarrage du pipeline de prospection automatisé (Senior Edition)")
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

                # 1. Anti-doublon Odoo
                if odoo.lead_or_partner_exists(company.siren, company.denomination, stage_ids=dedup_stage_ids):
                    logger.info(f"⏭️  [DOUBLON] '{company.denomination}' (SIREN: {company.siren}) est déjà dans Odoo. Ignoré.")
                    total_skipped += 1
                    continue

                logger.info(f"✨ Nouveau prospect : '{company.denomination}' ({company.ville}) - SIREN: {company.siren}")

                # 2. Enrichissement Web + IA
                logger.info(f"🧠 Recherche & qualification IA pour '{company.denomination}'...")
                contact_info = enricher.enrich_contact(
                    company_name=company.denomination,
                    city=company.ville,
                    siren=company.siren,
                    dirigeant=company.dirigeant,
                    tranche_effectif=company.tranche_effectif,
                    annee_ouverture=company.annee_ouverture or ""
                )

                # 3. Création des contacts (res.partner)
                company_partner_id = None
                director_partner_id = None
                
                if not dry_run:
                    company_partner_id = odoo.create_company_contact(company, contact_info)
                    
                    if contact_info.contact_name and contact_info.has_real_contact and company_partner_id:
                        director_partner_id = odoo.create_director_contact(
                            contact_info,
                            company_partner_id,
                            company.denomination
                        )

                linked_partner_id = director_partner_id or company_partner_id

                # 4. Tags dynamiques
                tags_to_add = []
                if tag_id_oxo:
                    tags_to_add.append((4, tag_id_oxo))
                if contact_info.has_real_contact and tag_id_dirigeant:
                    tags_to_add.append((4, tag_id_dirigeant))
                    logger.info(f"🏷️  Tag 'Dirigeant' activé pour '{company.denomination}' (Tél: {contact_info.phone}, Email: {contact_info.email})")

                # 5. Création du Lead CRM
                if dry_run:
                    logger.info(f"[SIMULATION] Lead : {company.denomination} | Contact lié: {linked_partner_id} | Tags: {tags_to_add}")
                    total_added += 1
                else:
                    lead_id = odoo.create_lead(
                        company=company,
                        enriched=contact_info,
                        stage_id=target_stage_id,
                        tag_ids=tags_to_add if tags_to_add else None,
                        partner_id=linked_partner_id
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
