import csv
import logging
import os
from typing import Optional
from models import CompanyProspect, EnrichedContact

logger = logging.getLogger(__name__)

COLUMNS = [
    "siren", "siret", "denomination", "enseigne", "raison_sociale", "forme_juridique", "code_naf", "libelle_naf",
    "adresse", "code_postal", "ville", "date_creation", "tranche_effectif", "chiffre_affaires", "annee_ca",
    "telephone_etablissement", "email_etablissement", "site_web", "facebook", "instagram",
    "type_cuisine", "gamme_prix", "note_google", "nombre_avis", "horaires", "specialites",
    "dirigeant_principal", "role_dirigeant", "telephone_dirigeant", "email_dirigeant", "linkedin_dirigeant",
    "sources_dirigeant", "autres_dirigeants", "contact_direct_dirigeant", "confiance_identification",
    "reseau_franchise", "points_a_verifier", "analyse_claude_ok", "note_recap", "dossier_json", "lead_odoo",
]

class CsvExporter:
    """Single cumulative CSV of all prospects: one row per SIREN, updated in place when a prospect is processed again.

    The file is rewritten after each prospect so a crash keeps earlier rows.
    Semicolon-separated, UTF-8 with BOM: opens directly in French Excel.
    """

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.rows = {}
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f, delimiter=";"):
                    if row.get("siren"):
                        self.rows[row["siren"]] = row
        logger.info(f"📄 Export CSV : {path} ({len(self.rows)} prospect(s) déjà présents)")

    def _write(self):
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS, delimiter=";", extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self.rows.values())
        os.replace(tmp_path, self.path)

    def add(self, company: CompanyProspect, enriched: EnrichedContact, analysis_ok: bool,
            dossier_path: Optional[str], lead_id: Optional[int]):
        principal = enriched.principal
        others = [
            " ".join(x for x in (d.nom_complet, f"({d.role})" if d.role else None, d.telephone, d.email, d.linkedin_url) if x)
            for d in enriched.dirigeants if d is not principal
        ]
        details = enriched.details
        row = {
            "siren": company.siren,
            "siret": company.siret,
            "denomination": company.denomination,
            "enseigne": company.enseigne,
            "raison_sociale": company.raison_sociale,
            "forme_juridique": company.forme_juridique,
            "code_naf": company.code_naf,
            "libelle_naf": company.libelle_code_naf,
            "adresse": company.adresse,
            "code_postal": company.code_postal,
            "ville": company.ville,
            "date_creation": company.date_creation,
            "tranche_effectif": company.tranche_effectif,
            "chiffre_affaires": company.chiffre_affaires,
            "annee_ca": company.annee_ca,
            "telephone_etablissement": enriched.phone,
            "email_etablissement": enriched.email,
            "site_web": enriched.website,
            "facebook": enriched.facebook_url,
            "instagram": enriched.instagram_url,
            "type_cuisine": details.get("type_cuisine"),
            "gamme_prix": details.get("gamme_prix"),
            "note_google": details.get("note_google"),
            "nombre_avis": details.get("nombre_avis"),
            "horaires": details.get("horaires"),
            "specialites": ", ".join(details.get("specialites") or []),
            "dirigeant_principal": principal.nom_complet if principal else None,
            "role_dirigeant": principal.role if principal else None,
            "telephone_dirigeant": principal.telephone if principal else None,
            "email_dirigeant": principal.email if principal else None,
            "linkedin_dirigeant": principal.linkedin_url if principal else None,
            "sources_dirigeant": ", ".join(principal.sources) if principal else None,
            "autres_dirigeants": " | ".join(others),
            "contact_direct_dirigeant": "oui" if enriched.has_direct_director_contact else "non",
            "confiance_identification": details.get("confiance_identification"),
            "reseau_franchise": details.get("reseau_franchise"),
            "points_a_verifier": " | ".join(details.get("points_a_verifier") or []),
            "analyse_claude_ok": "oui" if analysis_ok else "non",
            "note_recap": enriched.summary,
            "dossier_json": dossier_path,
            "lead_odoo": lead_id,
        }
        self.rows[company.siren] = {k: ("" if v is None else v) for k, v in row.items()}
        self._write()
