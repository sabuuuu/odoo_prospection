import json
import logging
import re
import requests
import anthropic
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Regex pour extraire les numeros FR et emails directement des textes
PHONE_REGEX = re.compile(r'(?:\+33|0033|0)\s*[1-9](?:[\s.\-]?\d{2}){4}')
EMAIL_REGEX = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')

class ContactEnricher:
    """Enrichit les informations de contact via Serper.dev (Search + Places), data.gouv.fr et Claude AI."""

    def __init__(self, anthropic_key: str, serper_key: Optional[str] = None, model: str = "claude-haiku-4-5-20251001"):
        self.claude = anthropic.Anthropic(api_key=anthropic_key)
        self.serper_key = (serper_key or "").strip()
        self.model = model or "claude-haiku-4-5-20251001"
        if self.serper_key:
            logger.info(f"\u26a1 Moteur Serper.dev actif ({self.serper_key[:6]}...).")
        else:
            logger.warning("\u26a0\ufe0f Aucune cl\u00e9 SERPER_API_KEY.")

    def _search_dirigeant_gouv(self, siren: str) -> Optional[Dict[str, str]]:
        """Recherche le dirigeant via l'API gratuite annuaire-entreprises.data.gouv.fr."""
        try:
            url = f"https://recherche-entreprises.api.gouv.fr/search?q={siren}&page=1&per_page=1"
            response = requests.get(url, timeout=8)
            if response.status_code == 200:
                data = response.json()
                results = data.get("results", [])
                if results:
                    entreprise = results[0]
                    dirigeants = entreprise.get("dirigeants", [])
                    if dirigeants:
                        d = dirigeants[0]
                        nom = d.get("nom", "").strip()
                        prenoms = d.get("prenoms", "").strip()
                        qualite = d.get("qualite", "Dirigeant").strip()
                        logger.info(f"\U0001f3db\ufe0f  Dirigeant trouv\u00e9 via data.gouv.fr : {prenoms} {nom} ({qualite})")
                        return {
                            "nom": nom,
                            "prenom": prenoms,
                            "qualite": qualite,
                            "age": "",
                            "date_de_naissance_formatee": "",
                            "nationalite": ""
                        }
        except Exception as e:
            logger.warning(f"Recherche data.gouv.fr \u00e9chou\u00e9e : {e}")
        return None

    def _serper_search(self, query: str) -> str:
        """Recherche Google classique via Serper.dev /search."""
        if not self.serper_key:
            return ""
        try:
            headers = {'X-API-KEY': self.serper_key, 'Content-Type': 'application/json'}
            payload = json.dumps({"q": query, "gl": "fr", "hl": "fr", "num": 6})
            response = requests.post("https://google.serper.dev/search", headers=headers, data=payload, timeout=8)
            if response.status_code == 200:
                data = response.json()
                snippets = []
                kg = data.get("knowledgeGraph", {})
                if kg:
                    snippets.append(f"Fiche Google: {kg.get('title', '')} | T\u00e9l: {kg.get('phoneNumber', '')} | Site: {kg.get('website', '')} | Description: {kg.get('description', '')}")
                for place in data.get("places", []):
                    snippets.append(f"Google Maps: {place.get('title', '')} | T\u00e9l: {place.get('phoneNumber', '')} | Adresse: {place.get('address', '')} | Cat\u00e9gorie: {place.get('category', '')}")
                for item in data.get("organic", []):
                    snippets.append(f"Titre: {item.get('title', '')} | Lien: {item.get('link', '')} | Extrait: {item.get('snippet', '')}")
                return "\n\n".join(snippets)
            else:
                logger.warning(f"\u274c Serper Search HTTP {response.status_code}")
        except Exception as e:
            logger.warning(f"\u274c Serper Search erreur : {e}")
        return ""

    def _serper_places(self, query: str) -> str:
        """Recherche Google Maps d\u00e9di\u00e9e via Serper.dev /places (t\u00e9l\u00e9phone, site, adresse structur\u00e9s)."""
        if not self.serper_key:
            return ""
        try:
            headers = {'X-API-KEY': self.serper_key, 'Content-Type': 'application/json'}
            payload = json.dumps({"q": query, "gl": "fr", "hl": "fr"})
            response = requests.post("https://google.serper.dev/places", headers=headers, data=payload, timeout=8)
            if response.status_code == 200:
                data = response.json()
                snippets = []
                for place in data.get("places", []):
                    title = place.get("title", "")
                    phone = place.get("phoneNumber", "")
                    website = place.get("website", "")
                    address = place.get("address", "")
                    rating = place.get("rating", "")
                    reviews = place.get("reviewsCount", "")
                    category = place.get("category", "")
                    cid = place.get("cid", "")
                    snippets.append(
                        f"GOOGLE MAPS: {title} | T\u00e9l\u00e9phone: {phone} | Site: {website} | "
                        f"Adresse: {address} | Note: {rating}/5 ({reviews} avis) | Cat\u00e9gorie: {category}"
                    )
                if snippets:
                    logger.info(f"\U0001f4cd Serper Places : {len(snippets)} fiches Google Maps trouv\u00e9es !")
                return "\n\n".join(snippets)
            else:
                logger.warning(f"\u274c Serper Places HTTP {response.status_code}")
        except Exception as e:
            logger.warning(f"\u274c Serper Places erreur : {e}")
        return ""

    def _extract_phones_emails(self, text: str) -> Dict[str, list]:
        """Extraction directe par regex des t\u00e9l\u00e9phones et emails dans le texte brut."""
        phones = list(set(PHONE_REGEX.findall(text)))
        emails = list(set(EMAIL_REGEX.findall(text)))
        # Nettoyage des emails parasites
        emails = [e for e in emails if not any(x in e.lower() for x in ['example.com', 'sentry.io', 'schema.org', 'googleapis', 'google.com', 'w3.org'])]
        return {"phones": phones[:5], "emails": emails[:5]}

    def enrich_contact(self, company_name: str, city: str, siren: str = "", dirigeant: Optional[Dict[str, str]] = None, tranche_effectif: str = "", annee_ouverture: str = "") -> Dict[str, Any]:
        """Pipeline complet d'enrichissement : Places + Search + data.gouv + Claude AI."""
        
        # Si pas de dirigeant depuis Pappers, on cherche via data.gouv.fr (GRATUIT)
        if not dirigeant and siren:
            logger.info(f"\U0001f50e Dirigeant absent de Pappers, recherche via data.gouv.fr (SIREN: {siren})...")
            dirigeant = self._search_dirigeant_gouv(siren)
        
        dirigeant_nom = f"{dirigeant.get('prenom', '')} {dirigeant.get('nom', '')}".strip() if dirigeant else ""
        dirigeant_role = dirigeant.get('qualite', 'Dirigeant') if dirigeant else ""
        dirigeant_age_info = f", \u00e2g\u00e9 de {dirigeant.get('age')} ans" if dirigeant and dirigeant.get('age') else ""
        
        clean_name = re.sub(r'\b(SAS|SARL|EURL|SA|SCI|SOCIETE|MONSIEUR|MADAME)\b', '', company_name, flags=re.IGNORECASE).strip()
        
        all_results = []
        
        # ===== RECHERCHE 1 : Google Maps (API Places) - T\u00e9l\u00e9phone + Site structur\u00e9s =====
        places_query = f"{clean_name} {city} restaurant"
        logger.info(f"\U0001f50d [1/4] Google Maps Places : '{places_query}'...")
        places_results = self._serper_places(places_query)
        if places_results:
            all_results.append("=== GOOGLE MAPS (FICHES LOCALES) ===\n" + places_results)
        
        # ===== RECHERCHE 2 : Google Search classique (avis, articles) =====
        search_query = f"restaurant {clean_name} {city}"
        logger.info(f"\U0001f50d [2/4] Google Search : '{search_query}'...")
        search_results = self._serper_search(search_query)
        if search_results:
            all_results.append("=== RESULTATS GOOGLE ===\n" + search_results)
        
        # ===== RECHERCHE 3 : PagesJaunes + annuaires (t\u00e9l\u00e9phone quasi garanti) =====
        pj_query = f"{clean_name} {city} t\u00e9l\u00e9phone email site:pagesjaunes.fr OR site:118000.fr OR site:horaires.lefigaro.fr"
        logger.info(f"\U0001f50d [3/4] Annuaires (PagesJaunes) : '{clean_name} {city}'...")
        pj_results = self._serper_search(pj_query)
        if pj_results:
            all_results.append("=== ANNUAIRES (PAGESJAUNES / 118000) ===\n" + pj_results)
        
        # ===== RECHERCHE 4 : Parcours dirigeant (si identifi\u00e9) =====
        if dirigeant_nom:
            dir_query = f'"{dirigeant_nom}" {city} restaurant'
            logger.info(f"\U0001f50d [4/4] Parcours dirigeant : '{dirigeant_nom}'...")
            dir_results = self._serper_search(dir_query)
            if dir_results:
                all_results.append("=== PARCOURS DIRIGEANT ===\n" + dir_results)
        
        combined_results = "\n\n".join(all_results)
        
        # ===== PRE-EXTRACTION REGEX (t\u00e9l\u00e9phones et emails rep\u00e9r\u00e9s dans le texte brut) =====
        extracted = self._extract_phones_emails(combined_results)
        if extracted["phones"]:
            logger.info(f"\U0001f4de T\u00e9l\u00e9phones d\u00e9tect\u00e9s par regex : {extracted['phones']}")
        if extracted["emails"]:
            logger.info(f"\U0001f4e7 Emails d\u00e9tect\u00e9s par regex : {extracted['emails']}")
        
        # Ajouter les extractions regex au prompt pour guider Claude
        regex_hint = ""
        if extracted["phones"] or extracted["emails"]:
            regex_hint = f"\n\nINFOS D\u00c9J\u00c0 EXTRAITES AUTOMATIQUEMENT (v\u00e9rifie et utilise-les) :\n"
            if extracted["phones"]:
                regex_hint += f"- T\u00e9l\u00e9phones trouv\u00e9s : {', '.join(extracted['phones'])}\n"
            if extracted["emails"]:
                regex_hint += f"- Emails trouv\u00e9s : {', '.join(extracted['emails'])}\n"

        prompt = f"""Tu es un enqu\u00eateur et analyste commercial expert dans la restauration B2B.

Ton objectif est de r\u00e9diger une note ultra-qualitative sur le parcours humain, entrepreneurial et le contexte de l'\u00e9tablissement.

Informations officielles de d\u00e9part :
- Nom commercial : {clean_name} ({company_name})
- Ville : {city}
- Dirigeant l\u00e9gal : {dirigeant_nom if dirigeant_nom else "Non identifi\u00e9"} ({dirigeant_role if dirigeant_role else "Non identifi\u00e9"}){dirigeant_age_info}
- Effectif : {tranche_effectif if tranche_effectif else "Non pr\u00e9cis\u00e9"}
- Ann\u00e9e de cr\u00e9ation : {annee_ouverture if annee_ouverture else "Non pr\u00e9cis\u00e9e"}
- SIREN : {siren}
{regex_hint}

Voici ce que Google (Maps, articles, annuaires, r\u00e9seaux sociaux) a trouv\u00e9 :
\"\"\"
{combined_results if combined_results else "Aucun extrait web trouv\u00e9."}
\"\"\"

CONSIGNES D'EXTRACTION :
1. "phone" : PRIORIT\u00c9 ABSOLUE. Extrais le num\u00e9ro de t\u00e9l\u00e9phone direct du restaurant ou du dirigeant. Cherche dans les fiches Google Maps, PagesJaunes, 118000. Format fran\u00e7ais (03..., 04..., 05..., 06..., 07..., 09... ou +33...). Si plusieurs num\u00e9ros, prends celui du restaurant en priorit\u00e9.
2. "website" : Site web officiel du restaurant, ou page Facebook/Instagram/TripAdvisor.
3. "email" : Email de contact pro si pr\u00e9sent dans les extraits (PagesJaunes, site web, Facebook).
4. "summary" : R\u00e9dige un profil tr\u00e8s d\u00e9taill\u00e9 et dense (fa\u00e7on "Storytelling commercial"). Inclus toutes les donn\u00e9es fournies et combine-les avec les extraits web.

Voici deux exemples parfaits du style attendu :
Exemple 1 : "Zafer Kocabey, pr\u00e9sident du Palais du Kebab depuis 2022, exploite son restaurant boulevard Saint-Symphorien \u00e0 Longeville-l\u00e8s-Metz. N\u00e9 \u00e0 Metz, il dirige cet \u00e9tablissement de restauration rapide avec 1 \u00e0 2 salari\u00e9s."
Exemple 2 : "Pr\u00e9sident (n\u00e9 \u00e0 Woippy, 57) du restaurant enseigne MAISON BACI, place Saint-Louis (emplacement premium). 10-19 salari\u00e9s. | \U0001f310 Cuisine italienne authentique, gestion familiale avec \u00e9pouse Roselyne et fille Adeline."

Sois exhaustif et ultra-qualitatif !

R\u00c9PONDS STRICTEMENT AU FORMAT JSON avec ces cl\u00e9s :
{{
  "contact_name": "{dirigeant_nom if dirigeant_nom else 'null'}",
  "job_title": "{dirigeant_role if dirigeant_role else 'G\u00e9rant'}",
  "email": "email ou null",
  "phone": "num\u00e9ro de t\u00e9l\u00e9phone ou null",
  "linkedin_url": null,
  "website": "url web ou null",
  "summary": "Note analytique d\u00e9taill\u00e9e"
}}"""

        try:
            response = self.claude.messages.create(
                model=self.model,
                max_tokens=600,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}]
            )
            raw_text = response.content[0].text.strip()
            
            if "```json" in raw_text:
                raw_text = raw_text.split("```json")[1].split("```")[0].strip()
            elif "```" in raw_text:
                raw_text = raw_text.split("```")[1].split("```")[0].strip()

            data = json.loads(raw_text)
            
            # Fallback : si Claude n'a pas trouv\u00e9 le t\u00e9l\u00e9phone mais que le regex l'a, on l'injecte
            if (not data.get("phone") or data.get("phone") == "null") and extracted["phones"]:
                data["phone"] = extracted["phones"][0]
                logger.info(f"\U0001f4de T\u00e9l\u00e9phone inject\u00e9 par regex : {data['phone']}")
            
            if (not data.get("email") or data.get("email") == "null") and extracted["emails"]:
                data["email"] = extracted["emails"][0]
                logger.info(f"\U0001f4e7 Email inject\u00e9 par regex : {data['email']}")
            
            # Si le dirigeant a \u00e9t\u00e9 trouv\u00e9 (Pappers ou data.gouv) mais pas par l'IA, on force le nom
            if dirigeant_nom and (not data.get("contact_name") or data.get("contact_name") == "null"):
                data["contact_name"] = dirigeant_nom
            if dirigeant_role and (not data.get("job_title") or data.get("job_title") == "null"):
                data["job_title"] = dirigeant_role
                
            data["_dirigeant_found"] = bool(dirigeant_nom)
            
            logger.info(f"\u2705 Qualification r\u00e9ussie pour '{clean_name}' (T\u00e9l: {data.get('phone')}, Email: {data.get('email')}, Site: {data.get('website')}, Dirigeant: {data.get('contact_name')})")
            return data

        except Exception as e:
            logger.error(f"Erreur d'enrichissement Claude pour {company_name} : {e}")
            return {
                "contact_name": dirigeant_nom or None,
                "job_title": dirigeant_role or None,
                "email": extracted["emails"][0] if extracted["emails"] else None,
                "phone": extracted["phones"][0] if extracted["phones"] else None,
                "linkedin_url": None,
                "website": None,
                "summary": f"Restaurant {company_name} situ\u00e9 \u00e0 {city}.",
                "_dirigeant_found": bool(dirigeant_nom)
            }
