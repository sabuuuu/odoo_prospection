import requests
import logging
from typing import List, Optional
from urllib3.util import Retry
from requests.adapters import HTTPAdapter
from models import CompanyProspect, Dirigeant

logger = logging.getLogger(__name__)

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

def create_resilient_session(retries: int = 3, backoff_factor: float = 0.5) -> requests.Session:
    """Create an HTTP session configured with automatic retry on transient errors."""
    session = requests.Session()
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "POST", "OPTIONS"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

class PappersClient:
    """Client for the Pappers API with franchise filtering and structured data extraction."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.pappers.fr/v2"
        self.session = create_resilient_session()

    def _is_national_chain(self, company_name: str, enseigne: str = "") -> bool:
        """Check if the business matches a known national chain or franchise."""
        full_text = f"{company_name} {enseigne}".lower()
        return any(chain in full_text for chain in NATIONAL_CHAINS_EXCLUDE)

    def search_companies(
        self,
        code_naf: Optional[str] = None,
        departement: Optional[str] = None,
        ca_min: Optional[int] = None,
        limit: int = 10,
        page: int = 1
    ) -> List[CompanyProspect]:
        """Search for active independent businesses and local groups."""
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
            response = self.session.get(f"{self.base_url}/recherche", params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            results: List[CompanyProspect] = []
            for item in data.get("resultats", []):
                name = item.get("nom_entreprise") or item.get("denomination", "")
                siege = item.get("siege", {})
                enseigne = siege.get("enseigne", "")
                
                if self._is_national_chain(name, enseigne):
                    logger.info(f"🚫 [CHAÎNE/FRANCHISE EXCLUE] '{name}' ({enseigne}) ignoré.")
                    continue

                siege_cp = str(siege.get("code_postal", ""))
                matching_etabs = item.get("matching_etablissements") or item.get("etablissements", [])
                
                # Exclude distant large enterprises (>5 establishments or >50 employees); retain local branch address
                if departement and not siege_cp.startswith(str(departement)):
                    if len(matching_etabs) > 5 or item.get("effectif_min", 0) > 50:
                        logger.info(f"🚫 [GROUPE NATIONAL EXCLU] '{name}' (Siège: {siege.get('ville')}) ignoré.")
                        continue
                    
                    adresse_locale = None
                    for etab in matching_etabs:
                        if str(etab.get("code_postal", "")).startswith(str(departement)):
                            adresse_locale = etab
                            break
                    if not adresse_locale:
                        continue
                else:
                    adresse_locale = siege

                representants = item.get("representants", [])
                dirigeant = None
                if representants:
                    rep = representants[0]
                    dirigeant = Dirigeant(
                        nom=rep.get("nom", "").strip(),
                        prenom=rep.get("prenom", "").strip(),
                        qualite=rep.get("qualite", "Dirigeant").strip(),
                        age=str(rep.get("age")) if rep.get("age") else None,
                        date_naissance=rep.get("date_de_naissance_formatee"),
                        nationalite=rep.get("nationalite")
                    )

                date_crea = item.get("date_creation", "")
                annee_ouverture = date_crea.split("-")[0] if date_crea else None

                forme_juridique = item.get("forme_juridique", "")
                if "actions simplifiée" in forme_juridique.lower():
                    forme_juridique = "SAS"
                elif "responsabilité limitée" in forme_juridique.lower():
                    forme_juridique = "SARL"
                elif "unipersonnelle" in forme_juridique.lower():
                    forme_juridique = "SASU" if "actions" in forme_juridique.lower() else "EURL"

                ca_val = item.get("chiffre_affaires")
                ca_float = float(ca_val) if ca_val is not None else None

                prospect = CompanyProspect(
                    siren=item.get("siren", ""),
                    siret=adresse_locale.get("siret") or item.get("siret", ""),
                    denomination=name,
                    raison_sociale=item.get("denomination") or name,
                    forme_juridique=forme_juridique,
                    code_naf=item.get("code_naf", ""),
                    libelle_code_naf=item.get("libelle_code_naf", ""),
                    chiffre_affaires=ca_float,
                    annee_ca=str(item.get("annee_chiffre_affaires")) if item.get("annee_chiffre_affaires") else None,
                    tranche_effectif=item.get("tranche_effectif") or "Non précisé",
                    date_creation=date_crea,
                    annee_ouverture=annee_ouverture,
                    adresse=adresse_locale.get("adresse_ligne_1", ""),
                    code_postal=adresse_locale.get("code_postal", ""),
                    ville=adresse_locale.get("ville", ""),
                    dirigeant=dirigeant
                )
                results.append(prospect)

            logger.info(f"Pappers : {len(results)} restaurants locaux qualifiés (page {page}).")
            return results

        except requests.RequestException as e:
            logger.error(f"Erreur API Pappers : {e}")
            return []
