import xmlrpc.client
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

class OdooClient:
    """Client XML-RPC pour interagir avec Odoo CRM, g\u00e9rer les champs personnalis\u00e9s, les \u00e9tiquettes et les contacts."""

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
                raise PermissionError("\u00c9chec d'authentification Odoo. V\u00e9rifiez vos identifiants/cl\u00e9 API.")
            self.models = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/object')
            logger.info(f"Connect\u00e9 \u00e0 Odoo avec succ\u00e8s (UID: {self.uid})")
        except Exception as e:
            logger.error(f"Erreur de connexion \u00e0 Odoo : {e}")
            raise

    def _discover_custom_fields(self):
        """D\u00e9couvre dynamiquement les noms techniques des champs personnalis\u00e9s dans crm.lead."""
        try:
            fields = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'crm.lead', 'fields_get',
                [],
                {'attributes': ['string', 'type']}
            )
            
            target_labels = {
                'siren': ['siren'],
                'annee_ouverture': ['ann\u00e9e d\'ouverture', 'annee d\'ouverture', 'ouverture', 'ann\u00e9e de cr\u00e9ation'],
                'raison_sociale': ['raison sociale'],
                'forme_juridique': ['forme juridique', 'forme'],
                'nombre_salaries': ['nombre de salari\u00e9s', 'nombre de salaries', 'effectif', 'salari\u00e9s'],
                'naf': ['naf', 'code naf', 'activit\u00e9'],
                'linkedin': ['lien linkedin', 'linkedin'],
                'chiffre_affaires': ['chiffre d\'affaires', 'chiffre d\'affaire', 'ca'],
                'annee_ca': ['ann\u00e9e ca', 'annee ca'],
            }

            for f_name, f_info in fields.items():
                label = f_info.get('string', '').lower().strip()
                for key, matches in target_labels.items():
                    if key not in self.field_map and any(m in label for m in matches):
                        self.field_map[key] = f_name
                        logger.info(f"\u2728 Champ Odoo d\u00e9tect\u00e9 : '{f_info.get('string')}' -> {f_name}")

        except Exception as e:
            logger.warning(f"Impossible de r\u00e9cup\u00e9rer les champs personnalis\u00e9s : {e}")

    def get_or_create_tag(self, tag_name: str = "Prospection IA OXO") -> Optional[int]:
        """R\u00e9cup\u00e8re ou cr\u00e9e une \u00e9tiquette (crm.tag) dans Odoo."""
        try:
            tag_ids = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'crm.tag', 'search',
                [[['name', '=ilike', tag_name]]],
                {'limit': 1}
            )
            if tag_ids:
                return tag_ids[0]
            
            new_tag_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'crm.tag', 'create',
                [{'name': tag_name, 'color': 10}]
            )
            logger.info(f"\U0001f3f7\ufe0f Nouvelle \u00e9tiquette cr\u00e9\u00e9e : '{tag_name}' (ID: {new_tag_id})")
            return new_tag_id
        except Exception as e:
            logger.warning(f"Erreur lors de la gestion de l'\u00e9tiquette '{tag_name}' : {e}")
            return None

    def get_stage_id(self, stage_name: str) -> Optional[int]:
        """R\u00e9cup\u00e8re l'ID d'une \u00e9tape CRM (crm.stage) par son nom."""
        try:
            stage_ids = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'crm.stage', 'search',
                [[['name', 'ilike', stage_name]]],
                {'limit': 1}
            )
            if stage_ids:
                return stage_ids[0]
            logger.warning(f"\u00c9tape Odoo '{stage_name}' non trouv\u00e9e.")
            return None
        except Exception as e:
            logger.error(f"Erreur lors de la recherche de l'\u00e9tape '{stage_name}' : {e}")
            return None

    def get_stage_ids(self, stage_names: list) -> list:
        """R\u00e9cup\u00e8re les IDs pour une liste de noms d'\u00e9tapes."""
        ids = []
        for name in stage_names:
            sid = self.get_stage_id(name)
            if sid:
                ids.append(sid)
        return ids

    def lead_or_partner_exists(self, siren: str, company_name: str, stage_ids: Optional[list] = None) -> bool:
        """V\u00e9rifie si une entreprise existe d\u00e9j\u00e0 dans Odoo."""
        try:
            domain_lead = [
                '|',
                ['description', 'ilike', siren],
                ['partner_name', 'ilike', company_name]
            ]
            
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
            logger.warning(f"Erreur v\u00e9rification doublon pour {company_name} ({siren}) : {e}")
            return False

    # ========== MODULE CONTACTS (res.partner) ==========

    def create_company_contact(self, company_data: Dict[str, Any]) -> Optional[int]:
        """Cr\u00e9e un contact de type Soci\u00e9t\u00e9 (restaurant) dans res.partner."""
        try:
            # V\u00e9rifier si le contact entreprise existe d\u00e9j\u00e0
            existing = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'res.partner', 'search',
                [[['name', 'ilike', company_data.get('name', '')], ['is_company', '=', True]]],
                {'limit': 1}
            )
            if existing:
                logger.info(f"\U0001f4bc Contact entreprise existant trouv\u00e9 (ID: {existing[0]}) pour '{company_data.get('name')}'")
                return existing[0]

            payload = {
                'name': company_data.get('name'),
                'is_company': True,
                'company_type': 'company',
                'street': company_data.get('street') or False,
                'city': company_data.get('city') or False,
                'zip': company_data.get('zip') or False,
                'phone': company_data.get('phone') or False,
                'email': company_data.get('email') or False,
                'website': company_data.get('website') or False,
                'comment': company_data.get('comment') or False,
            }

            partner_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'res.partner', 'create',
                [payload]
            )
            logger.info(f"\U0001f4bc Contact entreprise cr\u00e9\u00e9 (ID: {partner_id}) pour '{company_data.get('name')}'")
            return partner_id
        except Exception as e:
            logger.error(f"Erreur cr\u00e9ation contact entreprise '{company_data.get('name')}' : {e}")
            return None

    def create_director_contact(self, director_data: Dict[str, Any], company_partner_id: int) -> Optional[int]:
        """Cr\u00e9e un contact de type Individu (dirigeant) rattach\u00e9 \u00e0 l'entreprise dans res.partner."""
        try:
            payload = {
                'name': director_data.get('name'),
                'is_company': False,
                'company_type': 'person',
                'parent_id': company_partner_id,  # Rattach\u00e9 \u00e0 l'entreprise
                'function': director_data.get('function') or 'G\u00e9rant',
                'phone': director_data.get('phone') or False,
                'email': director_data.get('email') or False,
                'comment': director_data.get('comment') or False,
            }

            partner_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'res.partner', 'create',
                [payload]
            )
            logger.info(f"\U0001f464 Contact dirigeant cr\u00e9\u00e9 (ID: {partner_id}) : '{director_data.get('name')}' rattach\u00e9 \u00e0 entreprise ID {company_partner_id}")
            return partner_id
        except Exception as e:
            logger.error(f"Erreur cr\u00e9ation contact dirigeant '{director_data.get('name')}' : {e}")
            return None

    # ========== CR\u00c9ATION DE LEAD ==========

    def create_lead(self, lead_data: Dict[str, Any], custom_values: Dict[str, Any], stage_id: Optional[int] = None, tag_ids: Optional[list] = None, partner_id: Optional[int] = None) -> Optional[int]:
        """Cr\u00e9e une nouvelle piste en renseignant les champs standards et personnalis\u00e9s."""
        try:
            payload = dict(lead_data)
            if stage_id:
                payload['stage_id'] = stage_id
            if tag_ids:
                payload['tag_ids'] = tag_ids
            if partner_id:
                payload['partner_id'] = partner_id

            # Affectation dynamique des champs personnalis\u00e9s
            for key, val in custom_values.items():
                if val is not None and key in self.field_map:
                    tech_field = self.field_map[key]
                    payload[tech_field] = val

            lead_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'crm.lead', 'create',
                [payload]
            )
            logger.info(f"\u2705 Lead cr\u00e9\u00e9 dans Odoo (ID: {lead_id}) pour '{payload.get('name')}'")
            return lead_id
        except Exception as e:
            logger.error(f"Erreur lors de la cr\u00e9ation du Lead Odoo : {e}")
            return None
