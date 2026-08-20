import json
import logging
import re
import requests
import anthropic
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

PHONE_REGEX = re.compile(r'(?:\+33|0033|0)\s*[1-9](?:[\s.\-]?\d{2}){4}')
EMAIL_REGEX = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')

class ContactEnricher:
    """Enrichit les informations de contact via Serper.dev (Places + Web rapide), data.gouv.fr et Claude AI."""

    def __init__(self, anthropic_key: str, serper_key: Optional[str] = None, model: str = "claude-haiku-4-5-20251001"):
        self.claude = anthropic.Anthropic(api_key=anthropic_key)
        self.serper_key = (serper_key or "").strip()
        self.model = model or "claude-haiku-4-5-20251001"
        if self.serper_key:
            logger.info(f"⚡ Moteur Serper.dev actif ({self.serper_key[:6]}...).")
        else:
            logger.warning("⚠️ Aucune clé SERPER_API_KEY.")

    def _search_dirigeant_gouv(self, siren: str) -> Optional[Dict[str, str]]:
        """Recherche le dirigeant via l'API gratuite annuaire-entreprises.data.gouv.fr."""
        try:
            url = f"https://recherche-entreprises.api.gouv.fr/search?q={siren}&page=1&per_page=1"
            response = requests.get(url, timeout=6)
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
                        logger.info(f"🏛️  Dirigeant trouvé via data.gouv.fr : {prenoms} {nom} ({qualite})")
                        return {
                            "nom": nom,
                            "prenom": prenoms,
                            "qualite": qualite,
                            "age": "",
                            "date_de_naissance_formatee": "",
                            "nationalite": ""
                        }
        except Exception as e:
            logger.warning(f"Recherche data.gouv.fr échouée : {e}")
        return None

    def _serper_places(self, query: str) -> str:
        """Recherche Google Maps dédiée via Serper.dev /places (téléphone, site, adresse structurés)."""
        if not self.serper_key:
            return ""
        try:
            headers = {'X-API-KEY': self.serper_key, 'Content-Type': 'application/json'}
            payload = json.dumps({"q": query, "gl": "fr", "hl": "fr"})
            response = requests.post("https://google.serper.dev/places", headers=headers, data=payload, timeout=6)
            if response.status_code == 200:
                data = response.json()
                snippets = []
                for place in data.get("places", [])[:3]:
                    title = place.get("title", "")
                    phone = place.get("phoneNumber", "")
                    website = place.get("website", "")
                    address = place.get("address", "")
                    rating = place.get("rating", "")
                    reviews = place.get("reviewsCount", "")
                    category = place.get("category", "")
                    snippets.append(
                        f"GOOGLE MAPS: {title} | Téléphone: {phone} | Site: {website} | "
                        f"Adresse: {address} | Note: {rating}/5 ({reviews} avis) | Catégorie: {category}"
                    )
                if snippets:
                    logger.info(f"📍 Serper Places : {len(snippets)} fiches Google Maps trouvées !")
                return "\n\n".join(snippets)
        except Exception as e:
            logger.warning(f"❌ Serper Places erreur : {e}")
        return ""

    def _serper_search(self, query: str) -> str:
        """Recherche Google Web via Serper.dev /search."""
        if not self.serper_key:
            return ""
        try:
            headers = {'X-API-KEY': self.serper_key, 'Content-Type': 'application/json'}
            payload = json.dumps({"q": query, "gl": "fr", "hl": "fr", "num": 6})
            response = requests.post("https://google.serper.dev/search", headers=headers, data=payload, timeout=6)
            if response.status_code == 200:
                data = response.json()
                snippets = []
                kg = data.get("knowledgeGraph", {})
                if kg:
                    snippets.append(f"Fiche Google: {kg.get('title', '')} | Tél: {kg.get('phoneNumber', '')} | Site: {kg.get('website', '')} | Description: {kg.get('description', '')}")
                for place in data.get("places", [])[:2]:
                    snippets.append(f"Google Maps: {place.get('title', '')} | Tél: {place.get('phoneNumber', '')} | Adresse: {place.get('address', '')}")
                for item in data.get("organic", [])[:5]:
                    snippets.append(f"Titre: {item.get('title', '')} | Lien: {item.get('link', '')} | Extrait: {item.get('snippet', '')}")
                return "\n\n".join(snippets)
        except Exception as e:
            logger.warning(f"❌ Serper Search erreur : {e}")
        return ""

    def _extract_phones_emails(self, text: str) -> Dict[str, list]:
        """Extraction directe par regex des téléphones et emails dans le texte brut."""
        phones = list(set(PHONE_REGEX.findall(text)))
        emails = list(set(EMAIL_REGEX.findall(text)))
        emails = [e for e in emails if not any(x in e.lower() for x in ['example.com', 'sentry.io', 'schema.org', 'googleapis', 'google.com', 'w3.org'])]
        return {"phones": phones[:5], "emails": emails[:5]}

    def enrich_contact(self, company_name: str, city: str, siren: str = "", dirigeant: Optional[Dict[str, str]] = None, tranche_effectif: str = "", annee_ouverture: str = "") -> Dict[str, Any]:
        """Pipeline d'enrichissement ultra-rapide (2 requêtes Serper au total)."""
        
        # 1. Fallback dirigeant data.gouv.fr
        if not dirigeant and siren:
            logger.info(f"🔎 Dirigeant absent de Pappers, recherche via data.gouv.fr (SIREN: {siren})...")
            dirigeant = self._search_dirigeant_gouv(siren)
        
        dirigeant_nom = f"{dirigeant.get('prenom', '')} {dirigeant.get('nom', '')}".strip() if dirigeant else ""
        dirigeant_role = dirigeant.get('qualite', 'Dirigeant') if dirigeant else ""
        dirigeant_age_info = f", âgé de {dirigeant.get('age')} ans" if dirigeant and dirigeant.get('age') else ""
        
        clean_name = re.sub(r'\b(SAS|SARL|EURL|SA|SCI|SOCIETE|MONSIEUR|MADAME)\b', '', company_name, flags=re.IGNORECASE).strip()
        
        all_results = []
        
        # 2. Requête Google Maps Places
        places_query = f"{clean_name} {city} restaurant"
        logger.info(f"🔍 [1/2] Google Maps Places : '{places_query}'...")
        places_results = self._serper_places(places_query)
        if places_results:
            all_results.append("=== GOOGLE MAPS ===\n" + places_results)
        
        # 3. Requête Google Web combinée (avis, PagesJaunes, dirigeant)
        dir_clause = f'"{dirigeant_nom}"' if dirigeant_nom else ""
        web_query = f'restaurant "{clean_name}" {city} {dir_clause} avis téléphone site:pagesjaunes.fr OR site:societe.com OR site:tripadvisor.fr OR site:facebook.com'
        logger.info(f"🔍 [2/2] Google Web combiné : '{clean_name} {city}'...")
        web_results = self._serper_search(web_query)
        if web_results:
            all_results.append("=== WEB & ANNUAIRES ===\n" + web_results)
        
        combined_results = "\n\n".join(all_results)
        
        # 4. Extraction regex
        extracted = self._extract_phones_emails(combined_results)
        if extracted["phones"]:
            logger.info(f"📞 Téléphones détectés par regex : {extracted['phones']}")
        if extracted["emails"]:
            logger.info(f"📧 Emails détectés par regex : {extracted['emails']}")
        
        regex_hint = ""
        if extracted["phones"] or extracted["emails"]:
            regex_hint = f"\n\nINFOS DÉJÀ EXTRAITES AUTOMATIQUEMENT (utilise-les en priorité) :\n"
            if extracted["phones"]:
                regex_hint += f"- Téléphones trouvés : {', '.join(extracted['phones'])}\n"
            if extracted["emails"]:
                regex_hint += f"- Emails trouvés : {', '.join(extracted['emails'])}\n"

        prompt = f"""Tu es un enquêteur et analyste commercial expert dans la restauration B2B.

Ton objectif est de rédiger une note ultra-qualitative sur le parcours humain, entrepreneurial et le contexte de l'établissement.

Informations officielles de départ :
- Nom commercial : {clean_name} ({company_name})
- Ville : {city}
- Dirigeant légal : {dirigeant_nom if dirigeant_nom else "Non identifié"} ({dirigeant_role if dirigeant_role else "Non identifié"}){dirigeant_age_info}
- Effectif : {tranche_effectif if tranche_effectif else "Non précisé"}
- Année de création : {annee_ouverture if annee_ouverture else "Non précisée"}
- SIREN : {siren}
{regex_hint}

Voici ce que Google (Maps, articles, annuaires, réseaux sociaux) a trouvé :
\"\"\"
{combined_results if combined_results else "Aucun extrait web trouvé."}
\"\"\"

CONSIGNES D'EXTRACTION :
1. "phone" : Extrais le numéro de téléphone direct du restaurant ou du dirigeant (format français 03..., 04..., 05..., 06..., 07..., 09... ou +33...).
2. "website" : Site web officiel du restaurant, ou page Facebook/Instagram/TripAdvisor.
3. "email" : Email de contact pro si présent dans les extraits.
4. "summary" : Rédige un profil très détaillé et dense (façon "Storytelling commercial"). Inclus toutes les données fournies (effectif, année d'ouverture, âge du dirigeant) et combine-les avec les extraits web pour créer une note riche et fluide !

Voici deux exemples parfaits du style attendu :
Exemple 1 : "Zafer Kocabey, président du Palais du Kebab depuis 2022, exploite son restaurant boulevard Saint-Symphorien à Longeville-lès-Metz. Né à Metz, il dirige cet établissement de restauration rapide avec 1 à 2 salariés."
Exemple 2 : "Président (né à Woippy, 57) du restaurant enseigne MAISON BACI, place Saint-Louis (emplacement premium). 10-19 salariés. | 🌐 Cuisine italienne authentique, gestion familiale avec épouse Roselyne et fille Adeline."

RÉPONDS STRICTEMENT AU FORMAT JSON avec ces clés :
{{
  "contact_name": "{dirigeant_nom if dirigeant_nom else 'null'}",
  "job_title": "{dirigeant_role if dirigeant_role else 'Gérant'}",
  "email": "email ou null",
  "phone": "numéro de téléphone ou null",
  "linkedin_url": null,
  "website": "url web ou null",
  "summary": "Note analytique détaillée"
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
            
            if (not data.get("phone") or data.get("phone") == "null") and extracted["phones"]:
                data["phone"] = extracted["phones"][0]
            if (not data.get("email") or data.get("email") == "null") and extracted["emails"]:
                data["email"] = extracted["emails"][0]
            
            if dirigeant_nom and (not data.get("contact_name") or data.get("contact_name") == "null"):
                data["contact_name"] = dirigeant_nom
            if dirigeant_role and (not data.get("job_title") or data.get("job_title") == "null"):
                data["job_title"] = dirigeant_role
                
            data["_dirigeant_found"] = bool(dirigeant_nom)
            
            logger.info(f"✅ Qualification réussie pour '{clean_name}' (Tél: {data.get('phone')}, Email: {data.get('email')}, Site: {data.get('website')}, Dirigeant: {data.get('contact_name')})")
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
                "summary": f"Restaurant {company_name} situé à {city}.",
                "_dirigeant_found": bool(dirigeant_nom)
            }
