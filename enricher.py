import json
import logging
import re
import requests
import anthropic
import urllib3
from typing import Dict, Any, Optional, Tuple, List
from urllib3.util import Retry
from requests.adapters import HTTPAdapter
from models import Dirigeant, EnrichedContact

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
    'insurance', 'assurance', 'casino', 'crypto', 'loan', 'credit', 'horaire', 'annuaire'
]

# Generic or placeholder domains to ignore during email extraction
IGNORED_EMAIL_DOMAINS = [
    'example.com', 'sentry.io', 'schema.org', 'googleapis.com', 'w3.org', 
    'wordpress.org', 'typemade.mx', 'indiantypefoundry.com', 'astigmatic.com',
    'lab6.com', 'wixpress.com', 'cloudflare.com', 'doubleclick.net', 'google.com',
    'domain.com', 'email.com', 'test.com'
]

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
    raw_matches = re.findall(r'(?:(?:\+|00)33[\s.-]?|0)[1-79](?:[\s.-]?\d{2}){4}', text)
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
    """Multi-source enrichment engine combining Places API, web search, web scraping, and Claude AI."""

    def __init__(self, anthropic_key: str, serper_key: Optional[str] = None, model: str = "claude-haiku-4-5-20251001"):
        self.claude = anthropic.Anthropic(api_key=anthropic_key)
        self.serper_key = (serper_key or "").strip()
        self.model = model or "claude-haiku-4-5-20251001"
        self.session = create_resilient_session()
        if self.serper_key:
            logger.info(f"⚡ Moteur Serper.dev actif ({self.serper_key[:6]}...).")
        else:
            logger.warning("⚠️ Aucune clé SERPER_API_KEY.")

    def _search_dirigeant_gouv(self, siren: str) -> Optional[Dirigeant]:
        """Fallback lookup for legal representatives via data.gouv.fr API."""
        try:
            url = f"https://recherche-entreprises.api.gouv.fr/search?q={siren}&page=1&per_page=1"
            response = self.session.get(url, timeout=6)
            if response.status_code == 200:
                data = response.json()
                results = data.get("results", [])
                if results:
                    dirigeants = results[0].get("dirigeants", [])
                    for d in dirigeants:
                        nom = (d.get("nom") or d.get("denomination") or "").strip()
                        prenoms = (d.get("prenoms") or "").strip()
                        qualite = (d.get("qualite") or "Dirigeant").strip()
                        if nom:
                            logger.info(f"🏛️  Dirigeant trouvé via data.gouv.fr : {prenoms} {nom} ({qualite})")
                            return Dirigeant(nom=nom, prenom=prenoms, qualite=qualite)
        except Exception as e:
            logger.warning(f"Recherche data.gouv.fr échouée : {e}")
        return None

    def _clean_html(self, html_content: str) -> str:
        """Strip script tags, style sheets, SVGs, comments, and extra whitespace from HTML."""
        text = re.sub(r'<script.*?</script>', ' ', html_content, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style.*?</style>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<svg.*?</svg>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<!--.*?-->', ' ', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        return re.sub(r'\s+', ' ', text)

    def _scrape_website(self, website_url: str) -> Tuple[List[str], List[str], str]:
        """Scrape venue homepage and contact page for direct phone and email."""
        if not is_valid_restaurant_website(website_url):
            return [], [], ""
        
        url = website_url.strip()
        if not url.startswith(('http://', 'https://')):
            url = f"https://{url}"

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

        clean_text_content = ""
        try:
            logger.info(f"🌐 Scraping direct du site officiel : {url}...")
            resp = self.session.get(url, headers=headers, timeout=4, verify=False)
            if resp.status_code == 200:
                clean_text_content += " " + self._clean_html(resp.text)

            contact_url = url.rstrip('/') + '/contact'
            try:
                resp_c = self.session.get(contact_url, headers=headers, timeout=3, verify=False)
                if resp_c.status_code == 200:
                    clean_text_content += " " + self._clean_html(resp_c.text)
            except Exception:
                pass

        except Exception as e:
            logger.warning(f"Scraping {url} ignoré ({e})")

        phones = extract_valid_french_phones(clean_text_content)
        emails = extract_valid_emails(clean_text_content)

        if phones:
            logger.info(f"📞 Téléphone extrait DIRECTEMENT du site : {phones}")
        if emails:
            logger.info(f"📧 Email extrait DIRECTEMENT du site : {emails}")

        return phones, emails, clean_text_content[:1500]

    def _serper_places(self, query: str) -> Tuple[str, Optional[str], Optional[str]]:
        """Query Google Maps Places for verified GMB phone and website."""
        if not self.serper_key:
            return "", None, None
        try:
            headers = {'X-API-KEY': self.serper_key, 'Content-Type': 'application/json'}
            payload = json.dumps({"q": query, "gl": "fr", "hl": "fr"})
            response = self.session.post("https://google.serper.dev/places", headers=headers, data=payload, timeout=6)
            if response.status_code == 200:
                data = response.json()
                snippets = []
                found_website = None
                found_phone = None
                for place in data.get("places", [])[:3]:
                    site = place.get("website")
                    phone = place.get("phoneNumber") or place.get("phone")
                    
                    if site and is_valid_restaurant_website(site) and not found_website:
                        found_website = site
                    if phone and not found_phone:
                        formatted_p = format_french_phone(phone)
                        if formatted_p:
                            found_phone = formatted_p
                    
                    snippets.append(
                        f"GOOGLE MAPS: {place.get('title', '')} | Téléphone: {phone} | "
                        f"Site: {site} | Adresse: {place.get('address', '')} | "
                        f"Note: {place.get('rating', '')}/5 ({place.get('reviewsCount', '')} avis) | Catégorie: {place.get('category', '')}"
                    )
                if snippets:
                    logger.info(f"📍 Serper Places : {len(snippets)} fiches (Tél GMB: {found_phone}, Site: {found_website}).")
                return "\n\n".join(snippets), found_website, found_phone
        except Exception as e:
            logger.warning(f"❌ Serper Places erreur : {e}")
        return "", None, None

    def _serper_search(self, query: str, num_results: int = 10) -> Tuple[str, Optional[str]]:
        """Perform general Google search via Serper."""
        if not self.serper_key:
            return "", None
        try:
            headers = {'X-API-KEY': self.serper_key, 'Content-Type': 'application/json'}
            payload = json.dumps({"q": query, "gl": "fr", "hl": "fr", "num": num_results})
            response = self.session.post("https://google.serper.dev/search", headers=headers, data=payload, timeout=6)
            if response.status_code == 200:
                data = response.json()
                snippets = []
                found_website = None
                kg = data.get("knowledgeGraph", {})
                if kg:
                    kg_site = kg.get("website")
                    if is_valid_restaurant_website(kg_site):
                        found_website = kg_site
                    snippets.append(f"Fiche Google: {kg.get('title', '')} | Tél: {kg.get('phoneNumber', '')} | Site: {found_website}")
                
                for item in data.get("organic", []):
                    link = item.get("link", "")
                    if not found_website and is_valid_restaurant_website(link):
                        found_website = link
                    snippets.append(f"Titre: {item.get('title', '')} | Lien: {link} | Extrait: {item.get('snippet', '')}")
                return "\n\n".join(snippets), found_website
        except Exception as e:
            logger.warning(f"❌ Serper Search erreur : {e}")
        return "", None

    def enrich_contact(
        self,
        company_name: str,
        city: str,
        siren: str = "",
        dirigeant: Optional[Dirigeant] = None,
        tranche_effectif: str = "",
        annee_ouverture: str = ""
    ) -> EnrichedContact:
        """Execute full enrichment pipeline: Places, web search, scraping, and Claude synthesis."""
        if not dirigeant and siren:
            logger.info(f"🔎 Dirigeant absent de Pappers, recherche via data.gouv.fr (SIREN: {siren})...")
            dirigeant = self._search_dirigeant_gouv(siren)

        dirigeant_nom = dirigeant.full_name if dirigeant else ""
        dirigeant_role = dirigeant.qualite if dirigeant else ""
        dirigeant_age_info = f", âgé de {dirigeant.age} ans" if dirigeant and dirigeant.age else ""

        clean_name = re.sub(r'\b(SAS|SARL|EURL|SA|SCI|SOCIETE|MONSIEUR|MADAME)\b', '', company_name, flags=re.IGNORECASE).strip()

        all_results = []
        website_to_scrape = None
        gmb_phone = None

        places_query = f'"{clean_name}" {city} restaurant'
        logger.info(f"🔍 [1/3] Google Maps Places : '{places_query}'...")
        places_res, site_places, phone_places = self._serper_places(places_query)
        if places_res:
            all_results.append("=== GOOGLE MAPS ===\n" + places_res)
        if site_places:
            website_to_scrape = site_places
        if phone_places:
            gmb_phone = phone_places

        web_query = f'"{clean_name}" {city} restaurant téléphone'
        logger.info(f"🔍 [2/3] Google Web général (top 10) : '{clean_name} {city}'...")
        web_res, site_web = self._serper_search(web_query, num_results=10)
        if web_res:
            all_results.append("=== WEB & AVIS ===\n" + web_res)
        if not website_to_scrape and site_web:
            website_to_scrape = site_web

        dir_query = f'"{clean_name}" {city} site:tripadvisor.fr OR site:pagesjaunes.fr OR site:editus.lu OR site:mappy.com'
        logger.info(f"🔍 [3/3] Annuaires spécialisés : '{clean_name}'...")
        dir_res, _ = self._serper_search(dir_query, num_results=5)
        if dir_res:
            all_results.append("=== ANNUAIRES (TRIPADVISOR / PAGESJAUNES / EDITUS) ===\n" + dir_res)

        scraped_phones = []
        scraped_emails = []
        if website_to_scrape and is_valid_restaurant_website(website_to_scrape):
            s_phones, s_emails, site_text = self._scrape_website(website_to_scrape)
            scraped_phones.extend(s_phones)
            scraped_emails.extend(s_emails)
            if site_text:
                all_results.append(f"=== CONTENU DU SITE WEB ({website_to_scrape}) ===\n" + site_text)
        else:
            website_to_scrape = None

        combined_results = "\n\n".join(all_results)

        extracted_phones = extract_valid_french_phones(combined_results)
        extracted_emails = extract_valid_emails(combined_results)

        # Merge contact details with priority: GMB > scraped > web regex
        all_phones = []
        if gmb_phone:
            all_phones.append(gmb_phone)
        for p in scraped_phones + extracted_phones:
            if p not in all_phones:
                all_phones.append(p)

        all_emails = list(dict.fromkeys(scraped_emails + extracted_emails))

        chosen_phone = all_phones[0] if all_phones else None
        chosen_email = all_emails[0] if all_emails else None

        if chosen_phone:
            logger.info(f"🎯 Téléphone officiel retenu : {chosen_phone}")
        if chosen_email:
            logger.info(f"🎯 Email officiel retenu : {chosen_email}")

        regex_hint = ""
        if chosen_phone or chosen_email or website_to_scrape:
            regex_hint = "\n\nDONNÉES OFFICIELLES VÉRIFIÉES (utilise-les obligatoirement) :\n"
            if chosen_phone:
                regex_hint += f"- Téléphone vérifié : {chosen_phone}\n"
            if chosen_email:
                regex_hint += f"- Email vérifié : {chosen_email}\n"
            if website_to_scrape:
                regex_hint += f"- Site web officiel : {website_to_scrape}\n"

        prompt = f"""Tu es un enquêteur et analyste commercial expert dans la restauration B2B.

Rédige une note ultra-qualitative (synthétique et percutante) sur le parcours du dirigeant, l'établissement et son potentiel.

Informations officielles de départ :
- Nom commercial : {clean_name} ({company_name})
- Ville : {city}
- Dirigeant légal : {dirigeant_nom if dirigeant_nom else "Non identifié"} ({dirigeant_role if dirigeant_role else "Non identifié"}){dirigeant_age_info}
- Effectif : {tranche_effectif if tranche_effectif else "Non précisé"}
- Année de création : {annee_ouverture if annee_ouverture else "Non précisée"}
- SIREN : {siren}
{regex_hint}

Extraits collectés (Google Maps, TripAdvisor, PagesJaunes, Editus, Site officiel) :
\"\"\"
{combined_results if combined_results else "Aucun extrait web trouvé."}
\"\"\"

CONSIGNES :
1. "phone" : {chosen_phone if chosen_phone else 'numéro au format français (ex: 03 XX XX XX XX) ou null'}.
2. "website" : {website_to_scrape if website_to_scrape else 'url web ou null'}.
3. "email" : {chosen_email if chosen_email else 'email ou null'}.
4. "summary" : Note analytique complète (storytelling, spécialités, réputation, avis).

RÉPONDS STRICTEMENT AU FORMAT JSON VALIDE :
{{
  "contact_name": "{dirigeant_nom if dirigeant_nom else 'null'}",
  "job_title": "{dirigeant_role if dirigeant_role else 'Gérant'}",
  "email": {f'"{chosen_email}"' if chosen_email else 'null'},
  "phone": {f'"{chosen_phone}"' if chosen_phone else 'null'},
  "linkedin_url": null,
  "website": {f'"{website_to_scrape}"' if website_to_scrape else 'null'},
  "summary": "Synthèse commerciale dense"
}}"""

        try:
            response = self.claude.messages.create(
                model=self.model,
                max_tokens=1000,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}]
            )
            raw_text = response.content[0].text.strip()
            
            if "```json" in raw_text:
                raw_text = raw_text.split("```json")[1].split("```")[0].strip()
            elif "```" in raw_text:
                raw_text = raw_text.split("```")[1].split("```")[0].strip()

            data = json.loads(raw_text)
            
            final_phone = chosen_phone or format_french_phone(data.get("phone"))
            final_email = chosen_email or (data.get("email") if data.get("email") != "null" else None)
            final_website = website_to_scrape or (data.get("website") if is_valid_restaurant_website(data.get("website")) else None)

            contact_name = data.get("contact_name")
            if (not contact_name or contact_name == "null") and dirigeant_nom:
                contact_name = dirigeant_nom

            job_title = data.get("job_title")
            if (not job_title or job_title == "null") and dirigeant_role:
                job_title = dirigeant_role

            enriched = EnrichedContact(
                contact_name=contact_name if contact_name != "null" else None,
                job_title=job_title if job_title != "null" else None,
                phone=final_phone,
                email=final_email,
                website=final_website,
                linkedin_url=data.get("linkedin_url") if data.get("linkedin_url") != "null" else None,
                summary=data.get("summary", ""),
                dirigeant_found=bool(dirigeant_nom)
            )

            logger.info(f"✅ Qualification réussie pour '{clean_name}' (Tél: {enriched.phone}, Email: {enriched.email}, Site: {enriched.website}, Dirigeant: {enriched.contact_name})")
            return enriched

        except Exception as e:
            logger.error(f"Erreur d'enrichissement Claude pour {company_name} : {e}")
            return EnrichedContact(
                contact_name=dirigeant_nom or None,
                job_title=dirigeant_role or None,
                email=chosen_email,
                phone=chosen_phone,
                website=website_to_scrape,
                summary=f"Restaurant {company_name} situé à {city}.",
                dirigeant_found=bool(dirigeant_nom)
            )
