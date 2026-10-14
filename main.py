import argparse
import json
import logging
import sys
import os
from dataclasses import asdict
from typing import Optional
from datetime import datetime
from logging.handlers import RotatingFileHandler
from config import Config
from odoo_client import OdooClient
from pappers_client import PappersClient
from enricher import ContactEnricher
from csv_export import CsvExporter

STATE_FILE = "state.json"

def setup_logging():
    """Configure console and rotating file logging."""
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        "prospection.log",
        maxBytes=5 * 1024 * 1024,
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

def load_state() -> dict:
    """Load pagination state from disk."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Impossible de lire {STATE_FILE} : {e}")
    return {}

def save_state(state: dict):
    """Save pagination state to disk."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Impossible d'enregistrer {STATE_FILE} : {e}")

def save_dossier(dossier: dict) -> Optional[str]:
    """Save a prospect's dossier (raw sources + Claude analysis) as JSON; returns its path."""
    try:
        os.makedirs(Config.DOSSIER_DIR, exist_ok=True)
        path = os.path.join(Config.DOSSIER_DIR, f"{dossier['meta']['siren']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dossier, f, indent=2, ensure_ascii=False)
        logger.info(f"💾 Dossier enregistré : {path}")
        return path
    except Exception as e:
        logger.warning(f"Impossible d'enregistrer le dossier JSON : {e}")
        return None

def run_pipeline(dry_run: bool = False, limit_override: int = None, reset_state: bool = False, csv_path: Optional[str] = None):
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

    state = {} if reset_state else load_state()
    if reset_state:
        logger.info("🔄 Réinitialisation de l'état demandée (--reset-state). Reprise à la page 1.")

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
    
    tag_id_oxo = odoo.get_or_create_tag("Prospection IA OXO", create=not dry_run)
    tag_id_dirigeant = odoo.get_or_create_tag("Dirigeant", create=not dry_run)

    limit = limit_override or Config.DAILY_PROSPECT_LIMIT
    csv_export = CsvExporter(csv_path) if csv_path else None
    total_added = 0
    total_skipped = 0

    naf_query = ",".join(Config.TARGET_NAF_CODES)
    dep_list = Config.TARGET_DEPARTMENTS if Config.TARGET_DEPARTMENTS else ["national"]

    for dep in dep_list:
        dep_key = str(dep)
        dep_state = state.get(dep_key, {})
        
        # Resume from the last processed page
        start_page = dep_state.get("last_page", 0) + 1
        page = start_page
        max_pages = start_page + 15

        dep_param = None if dep == "national" else dep
        dep_display = f"Dept {dep}" if dep != "national" else "Toute France"

        logger.info(f"\n📍 Zone : {dep_display} | Reprise directe à la **Page {page}** de Pappers (0 crédit gaspillé sur les pages 1 à {page-1})")

        while total_added < limit and page <= max_pages:
            logger.info(f"\n🔎 Requête Pappers (Page {page} | NAF: {naf_query} | {dep_display} | Objectif: {limit - total_added} restants)...")
            
            search_result = pappers.search_companies(
                code_naf=naf_query,
                departement=dep_param,
                ca_min=Config.MIN_TURNOVER,
                limit=20,
                page=page
            )

            # API error: keep the cursor untouched so the next run retries this page
            if search_result is None:
                logger.warning(f"Requête Pappers échouée pour {dep_display} (page {page}). Curseur conservé à la page {page - 1}.")
                break

            companies, has_more = search_result
            page_added = 0
            page_completed = True

            for company in companies:
                if total_added >= limit:
                    page_completed = False
                    break

                # Dev trials: get new prospects each time instead of reprocessing those already in the CSV
                if dry_run and csv_export and company.siren in csv_export.rows:
                    logger.info(f"⏭️  [DÉJÀ DANS LE CSV] '{company.denomination}' (SIREN: {company.siren}). Ignoré.")
                    total_skipped += 1
                    continue

                # Deduplicate against existing CRM records
                exists = odoo.lead_or_partner_exists(company.siren, company.denomination, stage_ids=dedup_stage_ids)
                if exists is None:
                    # Odoo unreachable: stop here and retry this page next run rather than risk duplicates
                    page_completed = False
                    break
                if exists:
                    logger.info(f"⏭️  [DOUBLON] '{company.denomination}' (SIREN: {company.siren}) est déjà dans Odoo. Ignoré.")
                    total_skipped += 1
                    continue

                logger.info(f"✨ Nouveau prospect : '{company.denomination}' ({company.ville}) - SIREN: {company.siren}")

                # Full Pappers profile (all officers, beneficial owners, financials), only for new prospects
                fiche = pappers.get_company_details(company.siren)
                if fiche:
                    pappers.apply_company_details(company, fiche)
                    if pappers.is_chain(company):
                        logger.info(f"🚫 [CHAÎNE/FRANCHISE EXCLUE] '{company.denomination}' (enseignes : {company.enseignes or company.enseigne}) ignoré.")
                        continue
                    if company.etablissement_ferme:
                        logger.info(f"🚫 [ÉTABLISSEMENT FERMÉ] '{company.denomination}' : aucun établissement ouvert. Ignoré.")
                        continue

                dossier = enricher.collect(company, fiche)
                contact_info, analysis = enricher.analyze(company, dossier)
                dossier["analyse_claude"] = analysis
                dossier["resultat_final"] = asdict(contact_info)
                dossier_path = save_dossier(dossier)
                lead_id = None

                # Company partner, then one contact per officer with their own details
                company_partner_id = None
                director_partner_ids = {}

                if not dry_run:
                    company_partner_id = odoo.create_company_contact(company, contact_info)

                    if company_partner_id:
                        for dirigeant in contact_info.dirigeants:
                            if dirigeant.has_direct_contact or dirigeant.linkedin_url:
                                partner_id = odoo.create_director_contact(dirigeant, company_partner_id, company.denomination)
                                if partner_id:
                                    director_partner_ids[dirigeant.nom_complet] = partner_id

                principal = contact_info.principal
                linked_partner_id = (director_partner_ids.get(principal.nom_complet) if principal else None) or company_partner_id

                tags_to_add = []
                if tag_id_oxo:
                    tags_to_add.append((4, tag_id_oxo))
                # Only when an officer's own phone or email was found
                if contact_info.has_direct_director_contact and tag_id_dirigeant:
                    tags_to_add.append((4, tag_id_dirigeant))
                    logger.info(f"🏷️  Tag 'Dirigeant' activé pour '{company.denomination}' ({principal.nom_complet} - Tél: {principal.telephone}, Email: {principal.email})")

                if dry_run:
                    logger.info(f"[SIMULATION] Lead : {company.denomination} | Dirigeants: {[d.nom_complet for d in contact_info.dirigeants]} | Tags: {tags_to_add}")
                    total_added += 1
                    page_added += 1
                else:
                    lead_id = odoo.create_lead(
                        company=company,
                        enriched=contact_info,
                        stage_id=target_stage_id,
                        tag_ids=tags_to_add if tags_to_add else None,
                        partner_id=linked_partner_id
                    )
                    if lead_id:
                        odoo.attach_json('crm.lead', lead_id, f"dossier_{company.siren}.json", dossier)
                        total_added += 1
                        page_added += 1

                if csv_export:
                    csv_export.add(company, contact_info, analysis is not None, dossier_path, lead_id)

            # Limit reached mid-page: resume this page next run (already-created leads are skipped as duplicates)
            if not page_completed:
                dep_state["last_page"] = page - 1
            elif has_more:
                dep_state["last_page"] = page
            else:
                logger.info(f"Fin des résultats Pappers pour {dep_display}. Remise à zéro du curseur pour les futures créations.")
                dep_state["last_page"] = 0
            dep_state["last_updated"] = datetime.now().isoformat()
            dep_state["total_added"] = dep_state.get("total_added", 0) + page_added
            state[dep_key] = dep_state

            if not dry_run:
                save_state(state)

            if not page_completed or not has_more:
                break

            page += 1

    logger.info("=" * 60)
    logger.info("📊 BILAN DE L'EXÉCUTION DU PIPELINE")
    logger.info(f"   • Nouveaux prospects ajoutés : {total_added}")
    logger.info(f"   • Doublons évités : {total_skipped}")
    for dep, s in state.items():
        logger.info(f"   • Curseur Pappers [{dep}] : Page {s.get('last_page', 1)} enregistrée")
    if csv_export:
        logger.info(f"   • Export CSV : {csv_export.path}")
    logger.info("=" * 60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Automated B2B Prospecting Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Simulate pipeline execution without writing to Odoo")
    parser.add_argument("--limit", type=int, help="Maximum number of prospects to process")
    parser.add_argument("--reset-state", action="store_true", help="Reset Pappers pagination cursor back to page 1")
    parser.add_argument("--csv", metavar="PATH", default=Config.CSV_PATH,
                        help=f"Cumulative CSV of processed prospects (default: {Config.CSV_PATH})")
    parser.add_argument("--no-csv", action="store_true", help="Disable the CSV export")
    args = parser.parse_args()

    run_pipeline(dry_run=args.dry_run, limit_override=args.limit, reset_state=args.reset_state,
                 csv_path=None if args.no_csv else args.csv)
