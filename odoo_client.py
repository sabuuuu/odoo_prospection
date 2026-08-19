import xmlrpc.client
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

class OdooClient:
    """Client XML-RPC pour interagir avec Odoo CRM, gérer les champs personnalisés et les étiquettes."""

    def __init__(self, url: str, db: str, username: str, api_key: str):
        clean_url = url.strip()
        if clean_url and not clean_url.startswith(('http://', 'https://')):
            clean_url = f"https://{clean_url}"
        self.url = clean_url.rstrip("/")
        self.db = db.strip()
        self.username = username.strip()
        self.api_key = api_key.strip()
        self.uid = None
        self.models = None
        self.field_map = {}
        self._authenticate()
        self._discover_custom_fields()

    def _authenticate(self):
        """Authentifie l'utilisateur via le endpoint XML-RPC d'Odoo."""
        try:
            common = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/common')
            self.uid = common.authenticate(self.db, self.username, self.api_key, {})
            if not self.uid:
                raise PermissionError("Échec d'authentification Odoo. Vérifiez vos identifiants/clé API.")
            self.models = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/object')
            logger.info(f"Connecté à Odoo avec succès (UID: {self.uid})")
        except Exception as e:
            logger.error(f"Erreur de connexion à Odoo : {e}")
            raise

    def _discover_custom_fields(self):
        """Découvre dynamiquement les noms techniques des champs personnalisés dans crm.lead."""
        try:
            fields = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'crm.lead', 'fields_get',
                [],
                {'attributes': ['string', 'type']}
            )
            
            # Mapping basé sur les libellés affichés sur l'écran
            target_labels = {
                'siren': ['siren'],
                'annee_ouverture': ['année d\'ouverture', 'annee d\'ouverture', 'ouverture', 'année de création'],
                'raison_sociale': ['raison sociale'],
                'forme_juridique': ['forme juridique', 'forme'],
                'nombre_salaries': ['nombre de salariés', 'nombre de salaries', 'effectif', 'salariés'],
                'naf': ['naf', 'code naf', 'activité'],
                'linkedin': ['lien linkedin', 'linkedin'],
                'chiffre_affaires': ['chiffre d\'affaires', 'chiffre d\'affaire', 'ca'],
                'annee_ca': ['année ca', 'annee ca'],
            }

            for f_name, f_info in fields.items():
                label = f_info.get('string', '').lower().strip()
                for key, matches in target_labels.items():
                    if key not in self.field_map and any(m in label for m in matches):
                        self.field_map[key] = f_name
                        logger.info(f"✨ Champ Odoo détecté : '{f_info.get('string')}' -> {f_name}")

        except Exception as e:
            logger.warning(f"Impossible de récupérer les champs personnalisés : {e}")

    def get_or_create_tag(self, tag_name: str = "Prospection IA OXO") -> Optional[int]:
        """Récupère ou crée une étiquette (crm.tag) dans Odoo."""
        try:
            tag_ids = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'crm.tag', 'search',
                [[['name', '=ilike', tag_name]]],
                {'limit': 1}
            )
            if tag_ids:
                return tag_ids[0]
            
            # Création de l'étiquette si elle n'existe pas encore
            new_tag_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'crm.tag', 'create',
                [{'name': tag_name, 'color': 10}] # Couleur verte
            )
            logger.info(f"🏷️ Nouvelle étiquette créée : '{tag_name}' (ID: {new_tag_id})")
            return new_tag_id
        except Exception as e:
            logger.warning(f"Erreur lors de la gestion de l'étiquette '{tag_name}' : {e}")
            return None

    def get_stage_id(self, stage_name: str) -> Optional[int]:
        """Récupère l'ID d'une étape CRM (crm.stage) par son nom."""
        try:
            stage_ids = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'crm.stage', 'search',
                [[['name', 'ilike', stage_name]]],
                {'limit': 1}
            )
            if stage_ids:
                return stage_ids[0]
            logger.warning(f"Étape Odoo '{stage_name}' non trouvée.")
            return None
        except Exception as e:
            logger.error(f"Erreur lors de la recherche de l'étape '{stage_name}' : {e}")
            return None

    def get_stage_ids(self, stage_names: list) -> list:
        """Récupère les IDs pour une liste de noms d'étapes."""
        ids = []
        for name in stage_names:
            sid = self.get_stage_id(name)
            if sid:
                ids.append(sid)
        return ids

    def lead_or_partner_exists(self, siren: str, company_name: str, stage_ids: Optional[list] = None) -> bool:
        """Vérifie si une entreprise existe déjà dans Odoo."""
        try:
            domain_lead = [
                '|',
                ['description', 'ilike', siren],
                ['partner_name', 'ilike', company_name]
            ]
            
            # Si le champ technique SIREN est connu, on cherche aussi dessus
            if 'siren' in self.field_map:
                domain_lead = ['|', [self.field_map['siren'], 'ilike', siren]] + domain_lead

            if stage_ids:
                domain_lead.append(['stage_id', 'in', stage_ids])

            lead_ids = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'crm.lead', 'search',
                [domain_lead],
                {'limit': 1}
            )
            return len(lead_ids) > 0
        except Exception as e:
            logger.warning(f"Erreur vérification doublon pour {company_name} ({siren}) : {e}")
            return False

    def create_lead(self, lead_data: Dict[str, Any], custom_values: Dict[str, Any], stage_id: Optional[int] = None, tag_ids: Optional[list] = None) -> Optional[int]:
        """Crée une nouvelle piste en renseignant les champs standards et personnalisés."""
        try:
            payload = dict(lead_data)
            if stage_id:
                payload['stage_id'] = stage_id
            if tag_ids:
                payload['tag_ids'] = tag_ids # Liste de tuples (4, id)

            # Affectation dynamique des champs personnalisés
            for key, val in custom_values.items():
                if val is not None and key in self.field_map:
                    tech_field = self.field_map[key]
                    payload[tech_field] = val

            lead_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'crm.lead', 'create',
                [payload]
            )
            logger.info(f"✅ Lead créé dans Odoo (ID: {lead_id}) avec champs personnalisés et étiquettes pour '{payload.get('name')}'")
            return lead_id
        except Exception as e:
            logger.error(f"Erreur lors de la création du Lead Odoo : {e}")
            return None
