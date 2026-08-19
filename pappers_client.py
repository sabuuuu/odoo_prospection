import requests
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class PappersClient:
    """Client API pour récupérer les entreprises et leurs dirigeants depuis Pappers."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.pappers.fr/v2"

    def search_companies(
        self,
        code_naf: Optional[str] = None,
        departement: Optional[str] = None,
        ca_min: Optional[int] = None,
        limit: int = 10,
        page: int = 1
    ) -> List[Dict[str, Any]]:
        """Recherche des entreprises françaises selon des critères ciblés."""
        params = {
            "api_token": self.api_key,
            "par_page": min(limit, 100),
            "page": page,
            "precision": "standard",
            "statut_rcs": "inscrit",
            "entreprise_cessee": "false"
        }

        if code_naf:
            params["code_naf"] = code_naf
        if departement:
            params["departement"] = departement
        if ca_min:
            params["chiffre_affaires_min"] = ca_min

        try:
            response = requests.get(f"{self.base_url}/recherche", params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            results = []
            for item in data.get("resultats", []):
                # Dirigeant principal
                representants = item.get("representants", [])
                dirigeant = None
                if representants:
                    rep = representants[0]
                    dirigeant = {
                        "nom": rep.get("nom", "").strip(),
                        "prenom": rep.get("prenom", "").strip(),
                        "qualite": rep.get("qualite", "Dirigeant").strip(),
                        "age": rep.get("age", ""),
                        "date_de_naissance_formatee": rep.get("date_de_naissance_formatee", ""),
                        "nationalite": rep.get("nationalite", "")
                    }

                # Année d'ouverture
                date_crea = item.get("date_creation", "")
                annee_ouverture = date_crea.split("-")[0] if date_crea else None

                # Forme juridique abrégée ou complète
                forme_juridique = item.get("forme_juridique", "")
                if "actions simplifiée" in forme_juridique.lower():
                    forme_juridique = "SAS"
                elif "responsabilité limitée" in forme_juridique.lower():
                    forme_juridique = "SARL"
                elif "unipersonnelle" in forme_juridique.lower():
                    forme_juridique = "SASU" if "actions" in forme_juridique.lower() else "EURL"

                siege = item.get("siege", {})
                results.append({
                    "siren": item.get("siren"),
                    "siret": item.get("siret"),
                    "denomination": item.get("nom_entreprise") or item.get("denomination", ""),
                    "raison_sociale": item.get("denomination") or item.get("nom_entreprise", ""),
                    "forme_juridique": forme_juridique,
                    "code_naf": item.get("code_naf", ""),
                    "libelle_code_naf": item.get("libelle_code_naf", ""),
                    "chiffre_affaires": item.get("chiffre_affaires"),
                    "annee_ca": item.get("annee_chiffre_affaires"),
                    "tranche_effectif": item.get("tranche_effectif") or "Non précisé",
                    "date_creation": date_crea,
                    "annee_ouverture": annee_ouverture,
                    "adresse": siege.get("adresse_ligne_1", ""),
                    "code_postal": siege.get("code_postal", ""),
                    "ville": siege.get("ville", ""),
                    "dirigeant": dirigeant
                })

            logger.info(f"Pappers : {len(results)} entreprises trouvées (page {page}).")
            return results

        except requests.RequestException as e:
            logger.error(f"Erreur lors de l'appel API Pappers : {e}")
            return []
