import json
import logging
import requests
import anthropic
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class ContactEnricher:
    """Enrichit les informations de contact d'une entreprise via recherche Web et Claude AI."""

    def __init__(self, anthropic_key: str, serper_key: Optional[str] = None, model: str = "claude-3-5-haiku-20241022"):
        self.claude = anthropic.Anthropic(api_key=anthropic_key)
        self.serper_key = serper_key
        self.model = model

    def _search_web(self, query: str) -> str:
        """Effectue une recherche Google via l'API Serper.dev pour obtenir des extraits pertinents."""
        if not self.serper_key:
            return ""
        
        try:
            url = "https://google.serper.dev/search"
            payload = json.dumps({"q": query, "gl": "fr", "hl": "fr", "num": 5})
            headers = {
                'X-API-KEY': self.serper_key,
                'Content-Type': 'application/json'
            }
            response = requests.post(url, headers=headers, data=payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                snippets = []
                for item in data.get("organic", []):
                    snippets.append(f"Titre: {item.get('title')}\nLien: {item.get('link')}\nExtrait: {item.get('snippet')}")
                return "\n\n".join(snippets)
        except Exception as e:
            logger.warning(f"Recherche Serper en échec pour '{query}' : {e}")
        
        return ""

    def enrich_contact(self, company_name: str, city: str, dirigeant: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Trouve les coordonnées de contact (Email, Tél, LinkedIn, Site) et génère un résumé commercial.
        """
        dirigeant_nom = f"{dirigeant.get('prenom', '')} {dirigeant.get('nom', '')}".strip() if dirigeant else ""
        dirigeant_role = dirigeant.get('qualite', 'Dirigeant') if dirigeant else ""

        # Requêtes de recherche ciblées
        search_query = f'"{company_name}" "{dirigeant_nom}" email contact téléphone linkedin {city}'
        search_results = self._search_web(search_query)

        prompt = f"""Tu es un expert en enrichissement B2B et qualification d'établissements de restauration.

À partir des éléments suivants :
- Nom du restaurant / société : {company_name}
- Ville : {city}
- Dirigeant légal : {dirigeant_nom} ({dirigeant_role})
- Résultats de recherche Web récents :
\"\"\"
{search_results if search_results else "Aucun résultat de recherche fourni, effectue une déduction prudente."}
\"\"\"

TÂCHE :
1. Identifie l'interlocuteur clé (le gérant, propriétaire, chef ou contact principal).
2. Détecte le site web officiel, l'email de contact pro, le numéro de téléphone direct de l'établissement, et la page LinkedIn/Instagram si disponible.
3. Rédige un résumé court (1 phrase) sur le type de cuisine et le concept de l'établissement (ex: Brasserie traditionnelle, Pizzeria artisanale, Fast-food burger, Restaurant gastronomique, etc.).

RÈGLE : Si une information (ex: téléphone ou email) n'est pas trouvable ou incertaine, indique null. Ne fabrique pas de fausses coordonnées.

Format STRICT de réponse (JSON valide uniquement sans texte avant ou après) :
{{
  "contact_name": "Prénom Nom ou null",
  "job_title": "Gérant / Propriétaire / Chef ou null",
  "email": "email pro ou null",
  "phone": "numéro de téléphone ou null",
  "linkedin_url": "url profil ou page ou null",
  "website": "url du site web ou null",
  "summary": "Résumé du concept et spécialité du restaurant"
}}
"""

        try:
            response = self.claude.messages.create(
                model=self.model,
                max_tokens=500,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}]
            )
            raw_text = response.content[0].text.strip()
            
            # Nettoyage Markdown ```json si présent
            if "```json" in raw_text:
                raw_text = raw_text.split("```json")[1].split("```")[0].strip()
            elif "```" in raw_text:
                raw_text = raw_text.split("```")[1].split("```")[0].strip()

            data = json.loads(raw_text)
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
                "summary": f"Entreprise {company_name} basée à {city}."
            }
