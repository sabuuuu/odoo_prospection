import requests
import logging
import re
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Liste noire des chaînes, franchises et réseaux nationaux
NATIONAL_CHAINS_EXCLUDE = [
    "mcdonald", "mc donald", "burger king", "kfc", "quick", "subway", "o'tacos", "otacos",
    "buffalo grill", "courtepaille", "hippopotamus", "bistrot regent", "bistrot régent",
    "flunch", "autogrill", "crescendo", "del arte", "pizza del arte", "domino's", "dominos",
    "pizza hut", "class croute", "class'croute", "brioche doree", "brioche dorée", "paul",
    "starbucks", "columbus cafe", "columbus café", "la mie caline", "la mie câline", "point chaud", 
    "marie blachere", "marie blachère", "pitaya", "vapiano", "leon de bruxelles", "léon de bruxelles", 
    "pataterie", "la pataterie", "nabab kebab", "chamas tacos", "g la dalle", "point b", "five guys",
    "popeyes", "krispy kreme", "bagel stein", "bagelstein", "cojean", "exki", "sushi shop", "planet sushi",
    "crous", "elior", "sodexo", "compass group", "serenest", "api restauration", "dupont restauration",
    "ansemble", "score services", "eurest", "medirest", "newrest"
]

class PappersClient:
    """Client API pour récupérer les entreprises et filtrer les franchises/chaînes nationales."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.pappers.fr/v2"

    def _is_national_chain(self, company_name: str, enseigne: str = "") -> bool:
        """Détecte si le restaurant appartient à une chaîne ou franchise nationale connue."""
        full_text = f"{company_name} {enseigne}".lower()
        for chain in NATIONAL_CHAINS_EXCLUDE:
            if chain in full_text:
                return True
        return False

    def search_companies(
        self,
        code_naf: Optional[str] = None,
        departement: Optional[str] = None,
        ca_min: Optional[int] = None,
        limit: int = 10,
        page: int = 1
    ) -> List[Dict[str, Any]]:
        """Recherche des restaurants indépendants et groupes locaux, en excluant les franchises nationales."""
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
                name = item.get("nom_entreprise") or item.get("denomination", "")
                siege = item.get("siege", {})
                enseigne = siege.get("enseigne", "")
                
                # 1. FILTRE : Exclusion des chaînes & franchises nationales
                if self._is_national_chain(name, enseigne):
                    logger.info(f"🚫 [CHAÎNE/FRANCHISE EXCLUE] '{name}' ({enseigne}) ignoré.")
                    continue

                # 2. FILTRE : Siège distant pour grand groupe (ex: siège à Paris/Marseille)
                siege_cp = str(siege.get("code_postal", ""))
                matching_etabs = item.get("matching_etablissements") or item.get("etablissements", [])
                
                # Si le département est spécifié (ex: 57) et que le siège est hors région
                if departement and not siege_cp.startswith(str(departement)):
                    # Si c'est un grand groupe (> 5 établissements hors région), on l'exclut
                    if len(matching_etabs) > 5 or item.get("effectif_min", 0) > 50:
                        logger.info(f"🚫 [GROUPE NATIONAL EXCLU] '{name}' (Siège hors 57: {siege.get('ville')}) ignoré.")
                        continue
                    
                    # Sinon, on récupère l'adresse de l'établissement local en Moselle
                    adresse_locale = None
                    for etab in matching_etabs:
                        if str(etab.get("code_postal", "")).startswith(str(departement)):
                            adresse_locale = etab
                            break
                    if not adresse_locale:
                        continue
                else:
                    adresse_locale = siege

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

                date_crea = item.get("date_creation", "")
                annee_ouverture = date_crea.split("-")[0] if date_crea else None

                forme_juridique = item.get("forme_juridique", "")
                if "actions simplifiée" in forme_juridique.lower():
                    forme_juridique = "SAS"
                elif "responsabilité limitée" in forme_juridique.lower():
                    forme_juridique = "SARL"
                elif "unipersonnelle" in forme_juridique.lower():
                    forme_juridique = "SASU" if "actions" in forme_juridique.lower() else "EURL"

                results.append({
                    "siren": item.get("siren"),
                    "siret": adresse_locale.get("siret") or item.get("siret"),
                    "denomination": name,
                    "raison_sociale": item.get("denomination") or name,
                    "forme_juridique": forme_juridique,
                    "code_naf": item.get("code_naf", ""),
                    "libelle_code_naf": item.get("libelle_code_naf", ""),
                    "chiffre_affaires": item.get("chiffre_affaires"),
                    "annee_ca": item.get("annee_chiffre_affaires"),
                    "tranche_effectif": item.get("tranche_effectif") or "Non précisé",
                    "date_creation": date_crea,
                    "annee_ouverture": annee_ouverture,
                    "adresse": adresse_locale.get("adresse_ligne_1", ""),
                    "code_postal": adresse_locale.get("code_postal", ""),
                    "ville": adresse_locale.get("ville", ""),
                    "dirigeant": dirigeant
                })

            logger.info(f"Pappers : {len(results)} restaurants locaux qualifiés (page {page}).")
            return results

        except requests.RequestException as e:
            logger.error(f"Erreur lors de l'appel API Pappers : {e}")
            return []
