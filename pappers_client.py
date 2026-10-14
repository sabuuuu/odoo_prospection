import re
import requests
import logging
from typing import List, Optional, Tuple
from urllib3.util import Retry
from requests.adapters import HTTPAdapter
from models import CompanyProspect, Dirigeant

logger = logging.getLogger(__name__)

NATIONAL_CHAINS_EXCLUDE = [
    "mcdonald", "mcdonalds", "mc donald", "burger king", "kfc", "quick", "subway", "o'tacos", "otacos",
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

# Whole-word matching so "paul" doesn't exclude "Chez Pauline" nor "crous" exclude "La Croustade"
NATIONAL_CHAINS_PATTERN = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(chain) for chain in NATIONAL_CHAINS_EXCLUDE) + r")(?!\w)"
)

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

def parse_dirigeants(representants: Optional[list]) -> List[Dirigeant]:
    """Convert Pappers representatives into officers (natural persons only)."""
    dirigeants = []
    for rep in representants or []:
        if rep.get("personne_morale"):
            continue
        nom = (rep.get("nom") or "").strip()
        if not nom:
            continue
        dirigeants.append(Dirigeant(
            nom=nom,
            prenom=(rep.get("prenom_usuel") or rep.get("prenom") or "").strip(),
            qualite=(rep.get("qualite") or "Dirigeant").strip(),
            age=str(rep.get("age")) if rep.get("age") else None,
            date_naissance=rep.get("date_de_naissance_formatee"),
            nationalite=rep.get("nationalite")
        ))
    return dirigeants

class PappersClient:
    """Client for the Pappers API with franchise filtering and structured data extraction."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.pappers.fr/v2"
        self.session = create_resilient_session()

    def is_chain(self, prospect: CompanyProspect) -> bool:
        """Chain/franchise check on the legal name and every trade name (known only once the full profile is loaded)."""
        return self._is_national_chain(prospect.denomination, " ".join(filter(None, [prospect.enseigne, *prospect.enseignes])))

    def _is_national_chain(self, company_name: str, enseigne: str = "") -> bool:
        """Check if the business matches a known national chain or franchise."""
        full_text = f"{company_name} {enseigne}".lower()
        return NATIONAL_CHAINS_PATTERN.search(full_text) is not None

    def search_companies(
        self,
        code_naf: Optional[str] = None,
        departement: Optional[str] = None,
        ca_min: Optional[int] = None,
        limit: int = 10,
        page: int = 1
    ) -> Optional[Tuple[List[CompanyProspect], bool]]:
        """Search for active independent businesses and local groups.

        Returns (prospects, has_more_pages), or None if the API call failed.
        prospects may be empty even when has_more_pages is True (whole page filtered out).
        """
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

            raw_results = data.get("resultats", [])
            total = data.get("total")
            if isinstance(total, int):
                has_more = page * params["par_page"] < total
            else:
                has_more = len(raw_results) == params["par_page"]

            results: List[CompanyProspect] = []
            for item in raw_results:
                name = item.get("nom_entreprise") or item.get("denomination", "")
                siege = item.get("siege", {})
                enseigne = siege.get("enseigne", "")
                
                if self._is_national_chain(name, enseigne):
                    logger.info(f"🚫 [CHAÎNE/FRANCHISE EXCLUE] '{name}' ({enseigne}) ignoré.")
                    continue

                # Voluntary winding-up only shows in the name ("SOCIETE EN LIQUIDATION"), not as a court procedure
                if "liquidation" in name.lower():
                    logger.info(f"🚫 [EN LIQUIDATION] '{name}' ignoré.")
                    continue

                siege_cp = str(siege.get("code_postal", ""))
                matching_etabs = item.get("matching_etablissements") or item.get("etablissements", [])
                
                # Exclude distant large enterprises (>5 establishments or >50 employees); retain local branch address
                if departement and not siege_cp.startswith(str(departement)):
                    if len(matching_etabs) > 5 or (item.get("effectif_min") or 0) > 50:
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
                    enseigne=adresse_locale.get("enseigne") or enseigne or None,
                    dirigeants=parse_dirigeants(item.get("representants")),
                    raw=item
                )
                results.append(prospect)

            logger.info(f"Pappers : {len(results)}/{len(raw_results)} restaurants locaux qualifiés (page {page}).")
            return results, has_more

        except requests.RequestException as e:
            logger.error(f"Erreur API Pappers : {e}")
            return None

    def get_company_details(self, siren: str) -> Optional[dict]:
        """Fetch the full company profile (all officers, beneficial owners, financials, establishments)."""
        try:
            response = self.session.get(
                f"{self.base_url}/entreprise",
                params={"api_token": self.api_key, "siren": siren},
                timeout=15
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Erreur fiche Pappers {siren} : {e}")
            return None

    def apply_company_details(self, prospect: CompanyProspect, fiche: dict) -> None:
        """Complete a search result with data only available in the full profile."""
        dirigeants = parse_dirigeants(fiche.get("representants"))
        if dirigeants:
            prospect.dirigeants = dirigeants
        finances = [f for f in fiche.get("finances") or [] if f.get("chiffre_affaires") is not None]
        if prospect.chiffre_affaires is None and finances:
            prospect.chiffre_affaires = float(finances[0]["chiffre_affaires"])
            prospect.annee_ca = str(finances[0].get("annee")) if finances[0].get("annee") else None
        # The registered office is often just an admin address: the restaurant is the open establishment
        # in the same department, preferably one with a trade name
        etablissements = fiche.get("etablissements") or []
        ouverts = [e for e in etablissements if not e.get("etablissement_cesse")]
        prospect.etablissement_ferme = bool(etablissements) and not ouverts
        prospect.enseignes = [e["enseigne"] for e in ouverts if e.get("enseigne")]
        dept = prospect.code_postal[:2]
        locaux = [e for e in ouverts if str(e.get("code_postal", "")).startswith(dept)]
        restaurant = next((e for e in locaux if e.get("enseigne")), None)
        if restaurant:
            prospect.siret = restaurant.get("siret") or prospect.siret
            prospect.adresse = restaurant.get("adresse_ligne_1") or prospect.adresse
            prospect.code_postal = restaurant.get("code_postal") or prospect.code_postal
            prospect.ville = restaurant.get("ville") or prospect.ville
            prospect.enseigne = restaurant["enseigne"]

        candidates = [(fiche.get("siege") or {}).get("enseigne"), fiche.get("nom_commercial"), *(fiche.get("enseignes") or [])]
        prospect.enseigne = prospect.enseigne or next((e for e in candidates if e), None)
        if prospect.tranche_effectif == "Non précisé" and fiche.get("tranche_effectif"):
            prospect.tranche_effectif = fiche["tranche_effectif"]
