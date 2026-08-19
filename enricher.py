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

    def enrich_contact(self, company_name: str, city: str, dirigeant: Optional[Dict[str, str]] = None, tranche_effectif: str = "", annee_ouverture: str = "") -> Dict[str, Any]:
        """Trouve le téléphone, email, site web/social, et concept du restaurant."""
        dirigeant_nom = f"{dirigeant.get('prenom', '')} {dirigeant.get('nom', '')}".strip() if dirigeant else ""
        dirigeant_role = dirigeant.get('qualite', 'Dirigeant') if dirigeant else ""
        dirigeant_age_info = f", âgé de {dirigeant.get('age')} ans" if dirigeant and dirigeant.get('age') else ""
        
        # Nettoyage du nom commercial
        clean_name = re.sub(r'\b(SAS|SARL|EURL|SA|SCI|SOCIETE|MONSIEUR|MADAME)\b', '', company_name, flags=re.IGNORECASE).strip()
        search_query = f"restaurant {clean_name} {city}"
        
        search_results = self._search_web(search_query)

        prompt = f"""Tu es un enquêteur et analyste commercial expert dans la restauration B2B.

Ton objectif est de rédiger une note ultra-qualitative sur le parcours humain, entrepreneurial et le contexte de l'établissement.

Informations officielles de départ (données légales à intégrer absolument dans ta synthèse) :
- Nom commercial : {clean_name} ({company_name})
- Ville : {city}
- Dirigeant légal : {dirigeant_nom} ({dirigeant_role}){dirigeant_age_info}
- Effectif : {tranche_effectif}
- Année de création : {annee_ouverture}

Voici ce que Google (articles, annuaires, réseaux sociaux) a trouvé sur le dirigeant et le restaurant :
\"\"\"
{search_results if search_results else "Aucun extrait web trouvé."}
\"\"\"

CONSIGNES D'EXTRACTION :
1. "phone" : Extrais le numéro de téléphone direct du restaurant (format français 03..., 06..., 07..., 09... ou international).
2. "website" : Donne en priorité le site web officiel du restaurant, ou le lien de sa page Facebook/Instagram/TripAdvisor.
3. "email" : Extrais l'email de contact si présent dans les extraits.
4. "summary" : Rédige un profil très détaillé et dense (façon "Storytelling commercial"). Inclus toutes les données fournies (effectif, année d'ouverture, âge du dirigeant) et combine-les avec les extraits web pour créer une note riche et fluide !

Voici deux exemples parfaits du style attendu (phrases nominales, informations denses, symboles comme |) :
Exemple 1 : "{dirigeant_nom if dirigeant_nom else 'Le gérant'}, président du restaurant {clean_name} depuis {annee_ouverture if annee_ouverture else 'sa création'}, exploite son restaurant à {city}. Il dirige cet établissement avec {tranche_effectif if tranche_effectif else 'une équipe réduite'}."
Exemple 2 : "Président (né à Woippy, 57) du restaurant enseigne MAISON BACI, place Saint-Louis (emplacement premium). 10-19 salariés. Continuité économique reprise par PORKYNETTE en 2025 (à vérifier : possible restructuration récente). Bon potentiel. | 🌐 Origines familiales des Pouilles (parents ouvriers sidérurgie lorraine). Cuisine italienne authentique, gestion familiale avec épouse Roselyne et fille Adeline. Gérant également du Café Rubis contigu."

Inclus tout ce que tu trouves sur : 
- Le parcours du dirigeant (origines, famille, âge).
- L'historique (anciennes affaires, dates clés, effectifs).
- L'emplacement et son potentiel (ex: emplacement premium).
- Le concept, la cuisine et les spécialités de l'établissement.
Sois exhaustif et ultra-qualitatif, c'est pour un briefing avant rendez-vous clé !

RÉPONDS STRICTEMENT AU FORMAT JSON avec ces clés :
{{
  "contact_name": "{dirigeant_nom if dirigeant_nom else 'null'}",
  "job_title": "{dirigeant_role if dirigeant_role else 'Gérant'}",
  "email": "email ou null",
  "phone": "numéro de téléphone ou null",
  "linkedin_url": null,
  "website": "url web ou null",
  "summary": "Note analytique détaillée du parcours, famille, affaires et concept"
}}"""

        try:
            response = self.claude.messages.create(
                model=self.model,
                max_tokens=500,
                temperature=0.2,
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
