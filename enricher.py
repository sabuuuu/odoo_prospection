import html
import json
import logging
import re
import requests
import anthropic
import urllib3
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List
from urllib.parse import urljoin, urlparse
from urllib3.util import Retry
from requests.adapters import HTTPAdapter
from models import CompanyProspect, DirigeantContact, EnrichedContact

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)

# Platforms and directories to exclude when searching for official restaurant websites
SPAM_AND_DIRECTORY_DOMAINS = [
    'blogspot.com', 'wordpress.com', 'wixsite.com', 'weebly.com', 'carrd.co',
    'over-blog.com', 'canalblog.com', 'tumblr.com', 'medium.com', 'site123.me',
    'facebook.com', 'instagram.com', 'tripadvisor', 'pagesjaunes', 'societe.com',
    'pappers.fr', 'infogreffe.fr', 'annuaire-entreprises.data.gouv.fr', 'verif.com',
    'manageo.fr', 'creditsafe.com', 'nomao.com', 'lefigaro.fr', 'linternaute.com',
    'yelp.com', 'foursquare.com', 'thefork.com', 'lafourchette.com', 'ubereats.com',
    'deliveroo.fr', 'just-eat.fr', 'petitfute.com', 'restaurant-guru.com', 'editus.lu', 'mappy.com',
    'insurance', 'assurance', 'casino', 'crypto', 'loan', 'credit', 'horaire', 'annuaire',
    # Company / phone directories
    'kompass.com', 'le-site-de.com', 'lesentreprises.com', 'telephone.city', '118000.fr', '118712.fr',
    'societeinfo', 'corporama', 'b-reputation', 'northdata', 'dnb.com', 'infonet.fr', 'score3.fr',
    'bilansgratuits', 'firmania', 'hoodspot', 'cylex', 'justacote', 'waze.com', 'google.com', 'google.fr'
]

# Generic or placeholder domains to ignore during email extraction
IGNORED_EMAIL_DOMAINS = [
    'example.com', 'sentry.io', 'schema.org', 'googleapis.com', 'w3.org',
    'wordpress.org', 'typemade.mx', 'indiantypefoundry.com', 'astigmatic.com',
    'lab6.com', 'wixpress.com', 'cloudflare.com', 'doubleclick.net', 'google.com',
    'domain.com', 'email.com', 'test.com'
]

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

# Official website pages likely to name the owner/manager (legal notice, about, team...)
SITE_PAGE_KEYWORDS = ('contact', 'mention', 'legal', 'propos', 'qui-sommes', 'equipe', 'team', 'histoire', 'about', 'chef')
DEFAULT_SITE_PAGES = ['/contact', '/mentions-legales']
MAX_SITE_PAGES = 6
MAX_PAGE_CHARS = 8000
MAX_DIRIGEANTS_SEARCHED = 3

# Facebook and Instagram only serve page content (Facebook "Contact info" block, Instagram bio) to search engine crawlers
CRAWLER_USER_AGENT = 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'
MAX_SOCIAL_PROFILES = 2  # Per network
MAX_SOCIAL_TEXTS = 40
MAX_SOCIAL_TEXT_CHARS = 400
# Labels that follow each value in the Facebook "Coordonnées" block
FACEBOOK_LABELS = {'Adresse', 'Mobile', 'Téléphone', 'WhatsApp', 'E-mail', 'Site web', 'Instagram', 'TikTok', 'YouTube', 'Heures', 'Prix'}
FACEBOOK_NON_PAGES = {
    'profile.php', 'groups', 'events', 'story.php', 'photo', 'photo.php', 'photos', 'watch', 'reel', 'reels', 'share', 'hashtag',
    'sharer', 'sharer.php', 'login', 'media', 'search', 'public', 'help', 'policies', 'marketplace', 'dialog', 'plugins',
    'permalink.php', 'video.php', 'videos', 'notes', 'home.php', 'l.php', 'tr', 'gaming', 'fundraisers'
}
INSTAGRAM_NON_PROFILES = {'p', 'reel', 'reels', 'explore', 'stories', 'tv', 'accounts', 'about', 'legal'}

# Company registries: they never give contact details, so they are excluded from Google queries to leave room for useful pages
REGISTRY_EXCLUSIONS = " ".join(f"-site:{d}" for d in (
    "societe.com", "pappers.fr", "verif.com", "infogreffe.fr", "annuaire-entreprises.data.gouv.fr",
    "entreprises.lefigaro.fr", "kompass.com", "busyplace.fr", "lesentreprises.com", "le-site-de.com", "manageo.fr"
))

# Full Pappers profile keys sent to Claude (the full profile is kept in the saved dossier)
PAPPERS_FICHE_KEYS = [
    'siren', 'nom_entreprise', 'denomination', 'sigle', 'forme_juridique', 'date_creation', 'capital',
    'statut_rcs', 'tranche_effectif', 'effectif', 'code_naf', 'libelle_code_naf', 'domaine_activite',
    'objet_social', 'siege', 'representants', 'beneficiaires_effectifs', 'finances', 'etablissements'
]

def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}

_STRING = {"type": "string"}
_ETABLISSEMENT_FIELDS = {
    "telephone": _nullable(_STRING),
    "email": _nullable(_STRING),
    "site_web": _nullable(_STRING),
    "facebook_url": _nullable(_STRING),
    "instagram_url": _nullable(_STRING),
    "type_cuisine": _nullable(_STRING),
    "gamme_prix": _nullable(_STRING),
    "note_google": _nullable({"type": "number"}),
    "nombre_avis": _nullable({"type": "integer"}),
    "horaires": _nullable(_STRING),
    "specialites": {"type": "array", "items": _STRING},
}
_DIRIGEANT_FIELDS = {
    "nom_complet": _STRING,
    "role": _nullable(_STRING),
    "telephone": _nullable(_STRING),
    "email": _nullable(_STRING),
    "linkedin_url": _nullable(_STRING),
    "sources": {"type": "array", "items": _STRING},
}

# Schema enforced on Claude's answer via structured outputs
ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "confiance_identification": {"type": "string", "enum": ["forte", "moyenne", "faible"]},
        "etablissement": {
            "type": "object",
            "properties": _ETABLISSEMENT_FIELDS,
            "required": list(_ETABLISSEMENT_FIELDS),
            "additionalProperties": False
        },
        "dirigeants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _DIRIGEANT_FIELDS,
                "required": list(_DIRIGEANT_FIELDS),
                "additionalProperties": False
            }
        },
        "reseau_franchise": _nullable(_STRING),
        "points_a_verifier": {"type": "array", "items": _STRING},
        "note_recap": _STRING
    },
    "required": ["confiance_identification", "etablissement", "dirigeants", "reseau_franchise", "points_a_verifier", "note_recap"],
    "additionalProperties": False
}

ANALYSIS_INSTRUCTIONS = """Tu es analyste commercial B2B spécialisé dans la restauration. Tu prépares la fiche d'un prospect pour un commercial qui va l'appeler.

Tu reçois, entre balises <dossier>, un dossier JSON : registre Pappers (fiche complète : dirigeants, finances, établissements), registre public, résultats Google Maps, recherches Google sur le restaurant, ses réseaux sociaux et ses dirigeants, pages du site officiel. "meta.adresse_restaurant" est l'adresse de l'établissement visé.
"web.pages_reseaux_sociaux" contient les pages Facebook (bloc "coordonnees" : adresse, téléphone, email, site web ; "contenu" : infos et dernières publications) et les profils Instagram (bio) lus directement. Ce sont souvent les seules coordonnées des petits restaurants : vérifie que la page est bien celle du restaurant (nom + adresse ou ville), puis exploite-les. "Fermé en ce moment" indique seulement l'horaire au moment de la lecture ; les publications ne sont pas datées : n'en déduis une fermeture que si elles l'annoncent explicitement.

RÈGLE ABSOLUE : n'utilise que ce qui est écrit dans le dossier. N'invente jamais un numéro, un email, une URL, une note ou un prix : mets null (ou une liste vide) si l'information est absente.

1. Identifier le restaurant en ligne
- Les résultats Google Maps et web peuvent concerner d'autres commerces de la même rue ou de la même ville. Un résultat ne concerne le prospect que si son nom correspond à la dénomination ou à l'enseigne ET que son adresse ou sa ville correspond. Une simple adresse commune (centre commercial, galerie) ne suffit pas.
- "confiance_identification" : "forte" si une fiche Google Maps ou un site correspond clairement (nom + adresse) ; "moyenne" si seules des pages secondaires (avis, réseaux sociaux, annuaires) correspondent ; "faible" si rien ne permet d'identifier le restaurant en ligne.

2. "etablissement" : uniquement des informations sur CE restaurant
- telephone, email, site_web, facebook_url, instagram_url : uniquement issus de résultats qui le concernent.
- type_cuisine : libellé court (2 à 5 mots, ex. "Pizzeria", "Cuisine italienne", "Restauration rapide burgers"), déduit de la catégorie Google Maps, du site, des avis ou des réseaux sociaux. Ne recopie pas le libellé du code NAF ; null si seul le code NAF est connu.
- gamme_prix : uniquement le "priceLevel" de la fiche Google Maps du restaurant ou des prix lus sur son site ; sinon null.
- note_google / nombre_avis : uniquement "rating" / "ratingCount" de sa fiche Google Maps.
- horaires : résumé court ("Lun-Sam 11h30-14h, 19h-22h").
- specialites : plats ou concepts cités par les sources (liste vide si aucun).

3. "dirigeants" : personnes physiques qui dirigent ou possèdent le restaurant
- Registres (dirigeants, bénéficiaires effectifs) + toute personne présentée comme gérant/propriétaire/chef-patron dans les sources web.
- nom_complet : "Prénom NOM" avec le prénom usuel seulement (ex. "Francis Marc Gilbert OBERLE" devient "Francis OBERLE").
- role : court ("Gérant", "Présidente", "Propriétaire", "Chef-patron"). Les précisions (nom de naissance, ancienneté) vont dans la note.
- telephone, email, linkedin_url : seulement si une source les relie explicitement à cette personne (mentions légales, profil LinkedIn à son nom et lié au restaurant, page "contact" qui la nomme). Les coordonnées générales du restaurant restent dans "etablissement". Exception : un email qui contient le nom ou le prénom d'un dirigeant (ex. "mathieuthiam...@gmail.com" pour Mathieu THIAM), même publié sur la page du restaurant, est aussi son email direct. Un profil LinkedIn d'un homonyme sans lien avec le restaurant ou la ville : null.
- sources : d'où viennent la personne et ses coordonnées ("pappers", "registre_public", "site_officiel:/mentions-legales", "google_maps", "google_dirigeant", "linkedin", "facebook").

4. "reseau_franchise" : nom de la chaîne ou franchise si le dossier indique que le restaurant en fait partie (enseigne, offres d'emploi, articles), sinon null.

5. "points_a_verifier" : 0 à 3 points courts que le commercial doit vérifier avant d'appeler (ex. "Activité réelle à confirmer : aucune présence en ligne"). Signale en priorité tout indice de fermeture, de vente ou de changement d'exploitant (publication "fermeture", "fermé définitivement", "à vendre", liquidation).

6. "note_recap" : un paragraphe de 100 à 160 mots, en français, utile au commercial. Ne répète pas SIREN, capital ou adresse. Couvre, selon ce que le dossier permet : qui dirige et depuis quand ; le concept ; la réputation (note, avis) ; la taille (effectif) et la santé financière (CA, résultat, tendance) ; les signaux d'opportunité (création récente, reprise, recrutement, croissance, difficultés) ; l'angle d'approche commercial. Dis brièvement ce qui manque, sans t'étendre."""

# Words too generic to tell two restaurants apart
GENERIC_NAME_WORDS = {
    'restaurant', 'restaurants', 'resto', 'restauration', 'cafe', 'café', 'bar', 'brasserie', 'bistrot', 'bistro',
    'pizzeria', 'pizza', 'snack', 'grill', 'traiteur', 'cuisine', 'table', 'auberge', 'hotel', 'hôtel',
    'le', 'la', 'les', 'l', 'au', 'aux', 'du', 'de', 'des', 'chez', 'et', 'the', 'sarl', 'sas', 'sasu', 'eurl', 'sci'
}

def name_tokens(text: Optional[str]) -> set:
    """Distinctive words of a business name (lowercased, generic words removed)."""
    words = re.findall(r"[a-zà-ÿ0-9]+", (text or "").lower())
    return {w for w in words if len(w) >= 3 and w not in GENERIC_NAME_WORDS}

def domain_matches(url: Optional[str], names: set) -> bool:
    """True if the website's domain contains a distinctive word of the business name (e.g. dueisole.com for DUE ISOLE)."""
    host = urlparse(url or "").netloc.lower().removeprefix("www.")
    host_words = set(re.split(r"[.\-]", host))
    return any((len(n) >= 4 and n in host) or n in host_words for n in names)

def facebook_page_key(url: Optional[str]) -> Optional[str]:
    """Numeric ID or username of a Facebook page URL (/p/Name-123/, /people/Name/123/, /123/posts/..., /username), else None."""
    parsed = urlparse(url or "")
    if not parsed.netloc.endswith("facebook.com"):
        return None
    if parsed.path.rstrip("/") == "/profile.php":
        match = re.search(r'(?:^|&)id=(\d+)', parsed.query)
        return match.group(1) if match else None
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None
    if parts[0] in ("p", "people", "pages"):
        match = re.search(r'(\d{8,})$', "/".join(parts[1:3]))
        return match.group(1) if match else None
    return parts[0] if parts[0] not in FACEBOOK_NON_PAGES and re.fullmatch(r'[\w.\-]+', parts[0]) else None

def instagram_username(url: Optional[str]) -> Optional[str]:
    """Username of an Instagram profile URL, else None (posts, reels...)."""
    parsed = urlparse(url or "")
    if not parsed.netloc.endswith("instagram.com"):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if not parts or parts[0] in INSTAGRAM_NON_PROFILES or not re.fullmatch(r'[\w.]+', parts[0]):
        return None
    return parts[0].lower()

def _json_texts(html_content: str) -> List[str]:
    """Distinct "text" values of the JSON embedded in a Facebook page, in page order."""
    texts = []
    for raw in re.findall(r'"text":"((?:[^"\\]|\\.)*)"', html_content):
        try:
            value = json.loads(f'"{raw}"').strip()
        except ValueError:
            continue
        if value and value not in texts:
            texts.append(value)
    return texts

def _meta_content(html_content: str, name: str) -> Optional[str]:
    match = re.search(rf'<meta (?:property|name)="{name}" content="([^"]*)"', html_content)
    return html.unescape(match.group(1)).strip() if match else None

def is_valid_restaurant_website(url: Optional[str]) -> bool:
    """Verify whether a URL is likely a real restaurant website rather than an aggregator or directory."""
    if not url:
        return False
    url_lower = url.lower()
    return not any(spam in url_lower for spam in SPAM_AND_DIRECTORY_DOMAINS)

def format_french_phone(raw_phone: str) -> Optional[str]:
    """Validate and format a standard 10-digit French phone number."""
    if not raw_phone:
        return None

    digits = re.sub(r'[^\d+]', '', raw_phone)
    if digits.startswith('+33'):
        digits = '0' + digits[3:]
    elif digits.startswith('0033'):
        digits = '0' + digits[4:]

    if len(digits) == 10 and digits.startswith(('01', '02', '03', '04', '05', '06', '07', '09')):
        if digits == digits[0] * 10 or digits == '0123456789':
            return None
        return f"{digits[0:2]} {digits[2:4]} {digits[4:6]} {digits[6:8]} {digits[8:10]}"

    return None

def extract_valid_french_phones(text: str) -> List[str]:
    """Extract and format valid French phone numbers from text, prioritizing regional landlines and mobiles."""
    raw_matches = re.findall(r'(?<![\d+])(?:(?:\+|00)33[\s.-]?|0)[1-79](?:[\s.-]?\d{2}){4}(?!\d)', text)
    valid_phones = []
    for m in raw_matches:
        formatted = format_french_phone(m)
        if formatted and formatted not in valid_phones:
            valid_phones.append(formatted)

    valid_phones.sort(key=lambda p: (0 if p.startswith('03') else (1 if p.startswith(('06', '07')) else 2)))
    return valid_phones

def extract_valid_emails(text: str) -> List[str]:
    """Extract valid email addresses from text, excluding known placeholder domains."""
    raw_emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    valid_emails = []
    for e in raw_emails:
        e_lower = e.lower()
        if not any(domain in e_lower for domain in IGNORED_EMAIL_DOMAINS) and not e_lower.endswith(('.png', '.jpg', '.webp', '.js', '.css')):
            if e not in valid_emails:
                valid_emails.append(e)
    return valid_emails

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

class ContactEnricher:
    """Collects a JSON dossier per prospect (registries, Google, official website), then has Claude analyse it."""

    def __init__(self, anthropic_key: str, serper_key: Optional[str] = None, model: str = "claude-haiku-5-5"):
        self.claude = anthropic.Anthropic(api_key=anthropic_key)
        self.serper_key = (serper_key or "").strip()
        self.model = model or "claude-haiku-5-5"
        self.session = create_resilient_session()
        # No retries when scraping restaurant websites: a dead site should fail fast
        self.scrape_session = requests.Session()
        self.scrape_session.headers['User-Agent'] = USER_AGENT
        if self.serper_key:
            logger.info(f"⚡ Moteur Serper.dev actif ({self.serper_key[:6]}...).")
        else:
            logger.warning("⚠️ Aucune clé SERPER_API_KEY.")

    # ------------------------------------------------------------------ sources

    def _search_registre_public(self, siren: str) -> Optional[dict]:
        """Company record from the public registry API (recherche-entreprises.api.gouv.fr)."""
        try:
            url = f"https://recherche-entreprises.api.gouv.fr/search?q={siren}&page=1&per_page=1"
            response = self.session.get(url, timeout=6)
            if response.status_code == 200:
                results = response.json().get("results", [])
                if results:
                    return results[0]
        except Exception as e:
            logger.warning(f"Recherche data.gouv.fr échouée : {e}")
        return None

    def _serper(self, endpoint: str, payload: dict) -> Optional[dict]:
        if not self.serper_key:
            return None
        try:
            response = self.session.post(
                f"https://google.serper.dev/{endpoint}",
                headers={'X-API-KEY': self.serper_key, 'Content-Type': 'application/json'},
                data=json.dumps(payload),
                timeout=8
            )
            if response.status_code == 200:
                return response.json()
            logger.warning(f"❌ Serper {endpoint} : HTTP {response.status_code}")
        except Exception as e:
            logger.warning(f"❌ Serper {endpoint} erreur : {e}")
        return None

    def _google_maps(self, query: str) -> dict:
        """Google Maps listings with phone, website, rating, reviews, opening hours (Serper /maps, 3 credits)."""
        logger.info(f"🔍 Google Maps : '{query}'...")
        data = self._serper("maps", {"q": query, "gl": "fr", "hl": "fr"}) or {}
        return {"requete": query, "resultats": data.get("places", [])[:5]}

    def _google_search(self, query: str, num_results: int = 10) -> dict:
        """Google web results (title, link, snippet) plus the knowledge panel if any."""
        logger.info(f"🔍 Google : '{query}'...")
        data = self._serper("search", {"q": query, "gl": "fr", "hl": "fr", "num": num_results}) or {}
        return {
            "requete": query,
            "fiche_google": data.get("knowledgeGraph"),
            "resultats": [
                {"titre": r.get("title"), "lien": r.get("link"), "extrait": r.get("snippet")}
                for r in data.get("organic", [])
            ]
        }

    def _clean_html(self, html_content: str) -> str:
        """Strip script tags, style sheets, SVGs, comments, and extra whitespace from HTML."""
        text = re.sub(r'<script.*?</script>', ' ', html_content, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style.*?</style>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<svg.*?</svg>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<!--.*?-->', ' ', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        return re.sub(r'\s+', ' ', text).strip()

    def _fetch_html(self, url: str) -> Optional[str]:
        try:
            resp = self.scrape_session.get(url, timeout=5, verify=False)
            if resp.status_code == 200 and 'html' in resp.headers.get('Content-Type', 'text/html'):
                return resp.text
        except Exception as e:
            logger.info(f"Page {url} inaccessible ({type(e).__name__})")
        return None

    def _scrape_website(self, website_url: str) -> dict:
        """Scrape the homepage plus contact / legal notice / about / team pages of the official website."""
        url = website_url.strip()
        if not url.startswith(('http://', 'https://')):
            url = f"https://{url}"
        logger.info(f"🌐 Scraping du site officiel : {url}...")

        home = self._fetch_html(url)
        if home is None:
            return {"url": url, "accessible": False, "pages": {}}

        pages = {"/": self._clean_html(home)[:MAX_PAGE_CHARS]}
        host = urlparse(url).netloc
        candidates, social_links = [], []
        for href in re.findall(r'href=["\']([^"\'#]+)', home, flags=re.IGNORECASE):
            link = urljoin(url, href)
            parsed = urlparse(link)
            if parsed.netloc == host and any(k in parsed.path.lower() for k in SITE_PAGE_KEYWORDS) and link not in candidates:
                candidates.append(link)
            elif (facebook_page_key(link) or instagram_username(link)) and link not in social_links:
                social_links.append(link)
        if not candidates:
            candidates = [url.rstrip('/') + path for path in DEFAULT_SITE_PAGES]

        for link in candidates[:MAX_SITE_PAGES - 1]:
            page_html = self._fetch_html(link)
            if page_html:
                pages[urlparse(link).path or "/"] = self._clean_html(page_html)[:MAX_PAGE_CHARS]

        logger.info(f"🌐 {len(pages)} page(s) récupérée(s) : {list(pages)}")
        return {"url": url, "accessible": True, "pages": pages, "liens_reseaux_sociaux": social_links}

    def _fetch_as_crawler(self, url: str) -> Optional[str]:
        try:
            resp = self.scrape_session.get(url, timeout=15, headers={'User-Agent': CRAWLER_USER_AGENT, 'Accept-Language': 'fr-FR,fr;q=0.9'})
            if resp.status_code == 200:
                return resp.text
            logger.info(f"Page {url} : HTTP {resp.status_code}")
        except Exception as e:
            logger.info(f"Page {url} inaccessible ({type(e).__name__})")
        return None

    def _facebook_page(self, key: str) -> dict:
        """Public "Contact info" tab of a Facebook page: address, phone, email, website, linked Instagram, recent posts."""
        url = f"https://www.facebook.com/{key}"
        logger.info(f"📘 Page Facebook : {url}...")
        page = self._fetch_as_crawler(f"{url}/about_contact_and_basic_info")
        texts = _json_texts(page or "")
        if not texts:
            return {"url": url, "accessible": False}

        # Each value is followed by its label: '+33 3 87 64 75 17', 'Mobile', 'contact@x.fr', 'E-mail'...
        coordonnees = {}
        for value, label in zip(texts, texts[1:]):
            if label in FACEBOOK_LABELS and value not in FACEBOOK_LABELS:
                coordonnees.setdefault(label, []).append(value)
        return {
            "url": url,
            "accessible": True,
            "titre": _meta_content(page, "og:title"),
            "description": _meta_content(page, "og:description"),
            "coordonnees": coordonnees,
            # Page facts (category, rating, price range) and recent posts, which may announce a closure or a sale
            "contenu": [t[:MAX_SOCIAL_TEXT_CHARS] for t in texts[:MAX_SOCIAL_TEXTS]]
        }

    def _instagram_profile(self, username: str) -> dict:
        """Public Instagram profile: display name, bio (often phone, hours, booking link), followers."""
        url = f"https://www.instagram.com/{username}/"
        logger.info(f"📸 Profil Instagram : {url}...")
        page = self._fetch_as_crawler(url) or ""
        start = page.find('"xig_user_by_igid_v2":{')
        profile = page[start:start + 20000] if start >= 0 else ""

        def field(name):
            match = re.search(rf'"{name}":("(?:[^"\\]|\\.)*"|\d+|true|false)', profile)
            return json.loads(match.group(1)) if match else None

        if field("username") != username:
            return {"url": url, "accessible": False, "description": _meta_content(page, "og:description")}
        return {
            "url": url,
            "accessible": True,
            "nom": field("full_name"),
            "bio": field("biography"),
            "abonnes": field("follower_count"),
            "prive": field("is_private"),
            "description": _meta_content(page, "og:description")
        }

    def _social_profiles(self, searches: List[dict], places: dict, site: Optional[dict], names: set, fallback_query: str) -> dict:
        """Fetch the restaurant's own Facebook pages and Instagram profiles.

        Kept: links from its website or matching Google Maps listing, and results of any Google search whose title
        matches the business name (other pages of the same town are skipped).
        """
        def candidates(results):
            links = [p.get("website") for p in places["resultats"] if names & name_tokens(p.get("title"))]
            links += list((site or {}).get("liens_reseaux_sociaux") or [])
            links += [r["lien"] for r in results if names & name_tokens(r.get("titre"))]
            fb, ig = [], []
            for link in filter(None, links):
                key, user = facebook_page_key(link), instagram_username(link)
                if key and key not in fb:
                    fb.append(key)
                if user and user not in ig:
                    ig.append(user)
            return fb, ig

        results = [r for s in searches for r in s["resultats"]]
        fb_keys, ig_users = candidates(results)
        extra = None
        if not fb_keys and not ig_users:
            # Google results vary between calls: one broader query before giving up
            extra = self._google_search(fallback_query, 10)
            fb_keys, ig_users = candidates(results + extra["resultats"])

        facebook = [self._facebook_page(k) for k in fb_keys[:MAX_SOCIAL_PROFILES]]
        # The Facebook page often links the Instagram account
        for fb in facebook:
            for user in (fb.get("coordonnees") or {}).get("Instagram", []):
                user = user.strip().lstrip("@").lower()
                if re.fullmatch(r'[\w.]+', user) and user not in ig_users:
                    ig_users.append(user)
        instagram = [self._instagram_profile(u) for u in ig_users[:MAX_SOCIAL_PROFILES]]
        return {"facebook": facebook, "instagram": instagram, "recherche_complementaire": extra}

    @staticmethod
    def _find_website(places: dict, google: dict, names: set) -> Optional[str]:
        """Official website of the business, or None.

        Accepted: the website declared on the business's own Google Maps listing or knowledge panel (name match),
        or a web result whose domain contains the business name. A directory page that merely mentions the
        name in its path (villepratique.fr/.../l-express-bar) is rejected.
        """
        for place in places["resultats"]:
            if names & name_tokens(place.get("title")) and is_valid_restaurant_website(place.get("website")):
                return place["website"]
        kg = google.get("fiche_google") or {}
        if names & name_tokens(kg.get("title")) and is_valid_restaurant_website(kg.get("website")):
            return kg["website"]
        return next(
            (r["lien"] for r in google["resultats"] if is_valid_restaurant_website(r.get("lien")) and domain_matches(r.get("lien"), names)),
            None
        )

    @staticmethod
    def _dirigeant_names(company: CompanyProspect, registre: Optional[dict]) -> List[Dict[str, str]]:
        """Officers (natural persons) from Pappers and the public registry, deduplicated."""
        people = [{"nom_complet": d.full_name, "prenom": d.prenom, "nom": d.nom, "role": d.qualite} for d in company.dirigeants]
        for d in (registre or {}).get("dirigeants", []):
            if d.get("type_dirigeant") == "personne morale" or not d.get("nom"):
                continue
            prenom = (d.get("prenoms") or "").strip()
            people.append({"nom_complet": f"{prenom} {d['nom']}".strip(), "prenom": prenom, "nom": d["nom"], "role": d.get("qualite") or "Dirigeant"})

        unique, seen = [], set()
        for p in people:
            # Search key: first given name + surname (registries list every given name)
            key = f"{p['prenom'].split()[0] if p['prenom'] else ''} {p['nom']}".strip().lower()
            if key not in seen:
                seen.add(key)
                unique.append({**p, "nom_recherche": key.title()})
        return unique

    def collect(self, company: CompanyProspect, fiche: Optional[dict] = None) -> Dict[str, Any]:
        """Gather everything known about a prospect into a JSON-serializable dossier."""
        clean_name = re.sub(r'\b(SAS|SARL|EURL|SA|SCI|SOCIETE|MONSIEUR|MADAME)\b', '', company.denomination, flags=re.IGNORECASE).strip()
        city = company.ville

        registre = self._search_registre_public(company.siren)
        siege = company.raw.get("siege") or {}
        names = name_tokens(" ".join(filter(None, [
            company.denomination, company.raison_sociale, company.enseigne, *company.enseignes, siege.get("enseigne"),
            siege.get("nom_commercial"), (fiche or {}).get("nom_commercial"), *((fiche or {}).get("enseignes") or [])
        ]))) - name_tokens(city)  # "SARL LES ARCHES" must not match every restaurant in Jouy-aux-Arches

        # Trade name + exact establishment address so the listing found is this venue, not another restaurant in town
        maps_name = company.enseigne or clean_name
        places = self._google_maps(f'{maps_name} {company.adresse} {company.code_postal} {city}'.strip())
        if not any(names & name_tokens(p.get("title")) for p in places["resultats"]):
            retry = self._google_maps(f'{maps_name} {city}')
            places = {"requete": [places["requete"], retry["requete"]], "resultats": places["resultats"] + retry["resultats"]}

        google = self._google_search(f'"{maps_name}" {city} restaurant {REGISTRY_EXCLUSIONS}', 10)
        annuaires = self._google_search(
            f'"{maps_name}" {city} site:tripadvisor.fr OR site:pagesjaunes.fr OR site:thefork.fr OR site:mappy.com', 5
        )
        # Small restaurants often only have a Facebook/Instagram page, whose snippet shows phone and hours
        reseaux = self._google_search(f'"{maps_name}" {city} site:facebook.com OR site:instagram.com', 5)

        dirigeants = self._dirigeant_names(company, registre)
        recherches_dirigeants = []
        for i, person in enumerate(dirigeants[:MAX_DIRIGEANTS_SEARCHED]):
            name = person["nom_recherche"]
            entry = {"dirigeant": person["nom_complet"], "web": self._google_search(f'"{name}" {city} {REGISTRY_EXCLUSIONS}', 10)}
            if i == 0:
                entry["reseaux"] = self._google_search(
                    f'"{name}" ({maps_name} OR restaurant OR {city}) site:linkedin.com OR site:facebook.com OR site:instagram.com', 10
                )
            recherches_dirigeants.append(entry)

        website = self._find_website(places, google, names)
        site = self._scrape_website(website) if website else None
        searches = [google, annuaires, reseaux] + [s for e in recherches_dirigeants for s in (e["web"], e.get("reseaux")) if s]
        pages_sociales = self._social_profiles(searches, places, site, names, f'{maps_name} {city} facebook')
        if not website:
            # Website declared on the restaurant's own Facebook page
            website = next((
                w for fb in pages_sociales["facebook"] for w in (fb.get("coordonnees") or {}).get("Site web", [])
                if w.startswith("http") and is_valid_restaurant_website(w)
            ), None)
            site = self._scrape_website(website) if website else None
        if not website:
            logger.info(f"🌐 Aucun site web correspondant au nom '{company.denomination}' trouvé.")

        # Google Maps phone only from a listing whose name matches the company
        gmb_phone = next(
            (p for p in (
                format_french_phone(pl.get("phoneNumber") or pl.get("phone"))
                for pl in places["resultats"] if names & name_tokens(pl.get("title"))
            ) if p),
            None
        )

        dossier = {
            "meta": {
                "siren": company.siren,
                "denomination": company.denomination,
                "enseigne": company.enseigne,
                "adresse_restaurant": company.full_address,
                "ville": city,
                "collecte_le": datetime.now().isoformat(timespec="seconds"),
                "recherche_google_active": bool(self.serper_key)
            },
            "pappers": {"recherche": company.raw, "fiche": fiche},
            "registre_public": registre,
            "dirigeants_identifies": dirigeants,
            "web": {
                "google_maps": places,
                "google_restaurant": google,
                "annuaires": annuaires,
                "reseaux_sociaux_restaurant": reseaux,
                "pages_reseaux_sociaux": pages_sociales,
                "site_officiel": site,
                "recherches_dirigeants": recherches_dirigeants
            }
        }

        full_text = json.dumps(dossier, ensure_ascii=False)
        dossier["extraction_automatique"] = {
            "site_web": website,
            "telephone_google_maps": gmb_phone,
            "telephones": extract_valid_french_phones(full_text),
            "emails": extract_valid_emails(full_text)
        }
        return dossier

    # ----------------------------------------------------------------- analysis

    @staticmethod
    def _dossier_for_claude(dossier: Dict[str, Any]) -> Dict[str, Any]:
        """Dossier copy without bulky Pappers sections (full data stays in the saved file)."""
        view = dict(dossier)
        fiche = dossier["pappers"].get("fiche")
        if fiche:
            trimmed = {k: fiche[k] for k in PAPPERS_FICHE_KEYS if k in fiche}
            trimmed["finances"] = (fiche.get("finances") or [])[:3]
            trimmed["etablissements"] = (fiche.get("etablissements") or [])[:5]
            view["pappers"] = {"fiche": trimmed}
        return view

    @staticmethod
    def _fallback_contact(company: CompanyProspect, dossier: Dict[str, Any]) -> EnrichedContact:
        """Deterministic result used when Claude is unavailable, and to fill gaps in its answer."""
        auto = dossier["extraction_automatique"]
        return EnrichedContact(
            phone=auto["telephone_google_maps"] or next(iter(auto["telephones"]), None),
            email=next(iter(auto["emails"]), None),
            website=auto["site_web"],
            dirigeants=[
                DirigeantContact(nom_complet=d["nom_complet"], role=d["role"], sources=["registre"])
                for d in dossier["dirigeants_identifies"]
            ],
            summary=f"Restaurant {company.denomination} situé à {company.ville}."
        )

    def analyze(self, company: CompanyProspect, dossier: Dict[str, Any]) -> Tuple[EnrichedContact, Optional[dict]]:
        """Have Claude analyse the dossier; returns the validated contact data and Claude's raw JSON answer."""
        fallback = self._fallback_contact(company, dossier)
        logger.info(f"🧠 Analyse Claude du dossier '{company.denomination}'...")
        try:
            response = self.claude.messages.create(
                model=self.model,
                max_tokens=16000,
                system=ANALYSIS_INSTRUCTIONS,
                output_config={
                    "effort": "medium",
                    "format": {"type": "json_schema", "schema": ANALYSIS_SCHEMA}
                },
                messages=[{
                    "role": "user",
                    "content": "<dossier>\n" + json.dumps(self._dossier_for_claude(dossier), ensure_ascii=False) + "\n</dossier>"
                }]
            )
            if response.stop_reason != "end_turn":
                raise ValueError(f"Réponse Claude incomplète (stop_reason={response.stop_reason})")
            analysis = json.loads(next(b.text for b in response.content if b.type == "text"))
        except Exception as e:
            logger.error(f"Erreur d'analyse Claude pour {company.denomination} : {e}")
            return fallback, None

        enriched = self._validate(analysis, dossier, fallback)
        principal = enriched.principal
        logger.info(
            f"✅ Analyse OK pour '{company.denomination}' (Tél: {enriched.phone}, Email: {enriched.email}, "
            f"Site: {enriched.website}, Dirigeant: {principal.nom_complet if principal else None}, "
            f"contact direct: {enriched.has_direct_director_contact})"
        )
        return enriched, analysis

    @staticmethod
    def _validate(analysis: dict, dossier: Dict[str, Any], fallback: EnrichedContact) -> EnrichedContact:
        """Keep only phones, emails and URLs that actually appear in the collected dossier."""
        dossier_text = json.dumps(dossier, ensure_ascii=False)
        known_phones = set(extract_valid_french_phones(dossier_text))
        known_emails = {e.lower() for e in extract_valid_emails(dossier_text)}

        def phone(value):
            formatted = format_french_phone(value) if value else None
            if value and formatted not in known_phones:
                logger.warning(f"Téléphone absent des sources, ignoré : {value}")
                return None
            return formatted

        def email(value):
            if value and value.lower() not in known_emails:
                logger.warning(f"Email absent des sources, ignoré : {value}")
                return None
            return value or None

        def url(value):
            if value and value.rstrip('/') not in dossier_text:
                logger.warning(f"URL absente des sources, ignorée : {value}")
                return None
            return value or None

        # Claude may deliberately return null (e.g. a listing belonging to another restaurant): no automatic gap-filling
        etab = analysis["etablissement"]
        venue_phone = phone(etab["telephone"])
        venue_email = email(etab["email"])
        site = url(etab["site_web"])

        dirigeants = []
        for d in analysis["dirigeants"]:
            d_phone, d_email = phone(d["telephone"]), email(d["email"])
            # The venue's own email is not a direct line to the officer, unless it bears their name (mathieuthiam@...)
            if d_email and venue_email and d_email.lower() == venue_email.lower():
                local_part = d_email.lower().split("@")[0]
                if not any(n in local_part for n in name_tokens(d["nom_complet"])):
                    d_email = None
            dirigeants.append(DirigeantContact(
                nom_complet=d["nom_complet"],
                role=d["role"],
                # The venue's own phone is not a direct line to the officer
                telephone=d_phone if d_phone != venue_phone else None,
                email=d_email,
                linkedin_url=url(d["linkedin_url"]),
                sources=d["sources"]
            ))

        details = {k: etab[k] for k in ("type_cuisine", "gamme_prix", "note_google", "nombre_avis", "horaires", "specialites") if etab[k]}
        details.update({k: analysis[k] for k in ("confiance_identification", "reseau_franchise", "points_a_verifier") if analysis.get(k)})
        return EnrichedContact(
            phone=venue_phone,
            email=venue_email,
            website=site if is_valid_restaurant_website(site) else None,
            facebook_url=url(etab["facebook_url"]),
            instagram_url=url(etab["instagram_url"]),
            dirigeants=dirigeants or fallback.dirigeants,
            details=details,
            summary=analysis["note_recap"] or fallback.summary
        )
