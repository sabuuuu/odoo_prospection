import json
import logging
import re
import requests
import anthropic
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class ContactEnricher:
    """Enrichit les informations de contact d'une entreprise via Serper.dev Google API et Claude AI."""

    def __init__(self, anthropic_key: str, serper_key: Optional[str] = None, model: str = "claude-haiku-4-5-20251001"):
        self.claude = anthropic.Anthropic(api_key=anthropic_key)
        self.serper_key = (serper_key or "").strip()
        self.model = model or "claude-haiku-4-5-20251001"
        if self.serper_key:
            logger.info(f"⚡ Moteur Serper.dev actif ({self.serper_key[:6]}...).")
        else:
            logger.warning("⚠️ Aucune clé SERPER_API_KEY. Utilisation du fallback.")

    def _search_web(self, query: str) -> str:
        """Effectue une recherche Google ultra-rapide via Serper.dev."""
        if self.serper_key:
            try:
                url = "https://google.serper.dev/search"
                headers = {
                    'X-API-KEY': self.serper_key,
                    'Content-Type': 'application/json'
                }
                payload = json.dumps({
                    "q": query,
                    "gl": "fr",
                    "hl": "fr",
                    "num": 6
                })
                
                logger.info(f"🔍 Requête Google Serper : '{query}'...")
                response = requests.post(url, headers=headers, data=payload, timeout=8)
                
                if response.status_code == 200:
                    data = response.json()
                    snippets = []
                    
                    # 1. Fiche Google My Business / Knowledge Graph (Téléphone, Site, Type)
                    kg = data.get("knowledgeGraph", {})
                    if kg:
                        snippets.append(f"Fiche Google: {kg.get('title', '')} | Tél: {kg.get('phoneNumber', '')} | Site: {kg.get('website', '')} | Description: {kg.get('description', '')}")

                    # 2. Fiches Google Maps / Places locales (Téléphone, Adresse, Note)
                    for place in data.get("places", []):
                        snippets.append(f"Google Maps: {place.get('title', '')} | Tél: {place.get('phoneNumber', '')} | Adresse: {place.get('address', '')} | Catégorie: {place.get('category', '')}")

                    # 3. Résultats de recherche organiques (Sites web, TripAdvisor, Facebook)
                    for item in data.get("organic", []):
                        title = item.get("title", "")
                        link = item.get("link", "")
                        snip = item.get("snippet", "")
                        snippets.append(f"Titre: {title} | Lien: {link} | Extrait: {snip}")

                    logger.info(f"🌐 Serper : {len(snippets)} extraits Google récupérés en ~0.3s !")
                    return "\n\n".join(snippets)
                else:
                    logger.warning(f"❌ Erreur Serper HTTP {response.status_code} : {response.text}")

            except Exception as e:
                logger.warning(f"❌ Erreur connexion Serper : {e}")

        # Fallback de secours
        return ""

    def enrich_contact(self, company_name: str, city: str, dirigeant: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Trouve le téléphone, email, site web/social, et concept du restaurant."""
        dirigeant_nom = f"{dirigeant.get('prenom', '')} {dirigeant.get('nom', '')}".strip() if dirigeant else ""
        dirigeant_role = dirigeant.get('qualite', 'Dirigeant') if dirigeant else ""

        # Nettoyage du nom commercial
        clean_name = re.sub(r'\b(SAS|SARL|EURL|SA|SCI|SOCIETE|MONSIEUR|MADAME)\b', '', company_name, flags=re.IGNORECASE).strip()
        search_query = f"restaurant {clean_name} {city}"
        
        search_results = self._search_web(search_query)

        prompt = f"""Tu es un assistant expert en prospection commerciale pour les restaurants.

Informations officielles :
- Nom commercial : {clean_name} ({company_name})
- Ville : {city}
- Dirigeant légal : {dirigeant_nom} ({dirigeant_role})

Résultats Google en direct :
\"\"\"
{search_results if search_results else "Aucun extrait web trouvé."}
\"\"\"

CONSIGNES D'EXTRACTION :
1. "phone" : Extrais le numéro de téléphone direct du restaurant (format français 03..., 06..., 07..., 09... ou international).
2. "website" : Donne en priorité le site web officiel du restaurant, ou le lien de sa page Facebook/Instagram/TripAdvisor trouvée.
3. "email" : Extrais l'email de contact si présent dans les extraits.
4. "summary" : Résume en 1 phrase claire le style de cuisine, spécialités et concept de l'établissement.

RÉPONDS STRICTEMENT AU FORMAT JSON avec ces clés :
{{
  "contact_name": "{dirigeant_nom if dirigeant_nom else 'null'}",
  "job_title": "{dirigeant_role if dirigeant_role else 'Gérant'}",
  "email": "email ou null",
  "phone": "numéro de téléphone ou null",
  "linkedin_url": null,
  "website": "url web ou null",
  "summary": "Résumé du concept et spécialités"
}}"""

        try:
            response = self.claude.messages.create(
                model=self.model,
                max_tokens=400,
                temperature=0.1,
                messages=[{"role": "user", "content": prompt}]
            )
            raw_text = response.content[0].text.strip()
            
            if "```json" in raw_text:
                raw_text = raw_text.split("```json")[1].split("```")[0].strip()
            elif "```" in raw_text:
                raw_text = raw_text.split("```")[1].split("```")[0].strip()

            data = json.loads(raw_text)
            logger.info(f"✅ Qualification réussie pour '{clean_name}' (Tél: {data.get('phone')}, Site: {data.get('website')})")
            return data

        except Exception as e:
            logger.error(f"Erreur d'enrichissement Claude pour {company_name} : {e}")
            return {
                "contact_name": dirigeant_nom,
                "job_title": dirigeant_role,
                "email": None,
                "phone": None,
                "linkedin_url": None,
                "website": None,
                "summary": f"Restaurant {company_name} situé à {city}."
            }
