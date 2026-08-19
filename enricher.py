import json
import logging
import re
import requests
import anthropic
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class ContactEnricher:
    """Enrichit les informations de contact d'une entreprise via recherche Web et Claude AI."""

    def __init__(self, anthropic_key: str, serper_key: Optional[str] = None, model: str = "claude-3-haiku-20240307"):
        self.claude = anthropic.Anthropic(api_key=anthropic_key)
        self.serper_key = serper_key
        self.model = model or "claude-3-haiku-20240307"

    def _search_web(self, query: str) -> str:
        """Effectue une recherche Web (via Serper si clé fournie, sinon DuckDuckGo gratuit)."""
        # 1. Option Serper (Google Search API)
        if self.serper_key:
            try:
                url = "https://google.serper.dev/search"
                payload = json.dumps({"q": query, "gl": "fr", "hl": "fr", "num": 5})
                headers = {'X-API-KEY': self.serper_key, 'Content-Type': 'application/json'}
                response = requests.post(url, headers=headers, data=payload, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    snippets = []
                    for item in data.get("organic", []):
                        snippets.append(f"Titre: {item.get('title')}\nLien: {item.get('link')}\nExtrait: {item.get('snippet')}")
                    return "\n\n".join(snippets)
            except Exception as e:
                logger.warning(f"Recherche Serper en échec : {e}")

        # 2. Fallback gratuit sans clé API via DuckDuckGo
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            resp = requests.get(f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}", headers=headers, timeout=10)
            if resp.status_code == 200:
                snippets = []
                results = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', resp.text, re.DOTALL)
                for r in results[:5]:
                    clean = re.sub(r'<[^>]+>', '', r).strip()
                    snippets.append(f"Extrait Web : {clean}")
                return "\n\n".join(snippets)
        except Exception as e:
            logger.warning(f"Recherche DuckDuckGo en échec : {e}")

        return ""

    def enrich_contact(self, company_name: str, city: str, dirigeant: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Trouve le téléphone, email, site web, profil et concept du restaurant."""
        dirigeant_nom = f"{dirigeant.get('prenom', '')} {dirigeant.get('nom', '')}".strip() if dirigeant else ""
        dirigeant_role = dirigeant.get('qualite', 'Dirigeant') if dirigeant else ""

        # Requête ciblée pour restaurants
        search_query = f'restaurant "{company_name}" "{city}" téléphone contact avis carte'
        search_results = self._search_web(search_query)

        prompt = f"""Tu es un assistant de qualification B2B pour le secteur de la restauration.

Voici les informations sur le restaurant :
- Nom : {company_name}
- Ville : {city}
- Dirigeant : {dirigeant_nom} ({dirigeant_role})
- Résultats de recherche Web :
\"\"\"
{search_results if search_results else "Aucun extrait web trouvé."}
\"\"\"

TÂCHE :
1. Extrais le numéro de téléphone professionnel/direct du restaurant (ex: 03 XX XX XX XX).
2. Extrais ou déduis l'adresse email de contact (si trouvée).
3. Trouve l'URL du site web ou de la page Facebook/Instagram officielle du restaurant.
4. Rédige un résumé court (1 phrase) décrivant le style de cuisine et le concept (ex: Pizzeria au feu de bois, Brasserie traditionnelle lorraine, etc.).

RÉPONDS STRICTEMENT AU FORMAT JSON avec ces clés :
{{
  "contact_name": "{dirigeant_nom if dirigeant_nom else 'null'}",
  "job_title": "{dirigeant_role if dirigeant_role else 'Gérant'}",
  "email": "email ou null",
  "phone": "numéro de téléphone ou null",
  "linkedin_url": null,
  "website": "url du site ou page web ou null",
  "summary": "Description en 1 phrase du type de cuisine et spécialités"
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

            return json.loads(raw_text)

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
