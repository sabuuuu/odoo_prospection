import xmlrpc.client
import logging
import re
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

class OdooClient:
    """Client XML-RPC pour interagir avec Odoo CRM, gérer les champs personnalisés Studio, les étiquettes et les contacts."""

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
        self.lead_field_map = {}
        self.partner_field_map = {}
        self._authenticate()
        self._discover_fields()

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

    def _discover_fields(self):
        """Découvre les champs et leurs types/options de sélection pour crm.lead et res.partner."""
        target_keys = {
            'siren': 'x_studio_siren',
            'annee_ouverture': 'x_studio_annee_douverture',
            'raison_sociale': 'x_studio_raison_sociale',
            'forme_juridique': 'x_studio_forme_juridique',
            'nombre_salaries': 'x_studio_nombre_de_salaries',
            'naf': 'x_studio_naf',
            'linkedin': 'x_studio_lien_linkedin',
            'chiffre_affaires': 'x_studio_chiffre_daffaires',
            'annee_ca': 'x_studio_annee_ca',
            'type_de_restaurant': 'x_studio_type_de_restaurant',
            'adresse_du_siege_social': 'x_studio_adresse_du_siege_social',
        }

        # 1. Inspecter crm.lead
        try:
            lead_fields = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'crm.lead', 'fields_get',
                [],
                {'attributes': ['string', 'type', 'selection']}
            )
            for key, field_name in target_keys.items():
                if field_name in lead_fields:
                    f_info = lead_fields[field_name]
                    self.lead_field_map[key] = {
                        'name': field_name,
                        'type': f_info.get('type'),
                        'selection': f_info.get('selection', [])
                    }
            logger.info(f"✨ Champs Studio Lead configurés : {list(self.lead_field_map.keys())}")
        except Exception as e:
            logger.warning(f"Erreur découverte crm.lead : {e}")

        # 2. Inspecter res.partner
        partner_target_keys = {
            'siren': 'x_studio_siren',
            'annee_ouverture': 'x_studio_annee_douverture',
            'raison_sociale': 'x_studio_raison_sociale',
            'forme_juridique': 'x_studio_forme_juridique',
            'nombre_salaries': 'x_studio_effectif',
            'naf': 'x_studio_naf',
            'linkedin': 'x_studio_lien_linkedin',
            'chiffre_affaires': 'x_studio_chiffre_daffaire_dernier_exercice',
            'annee_ca': 'x_studio_annee_ca',
            'web': 'x_studio_web',
            'est_une_entreprise': 'x_studio_est_une_entreprise',
        }
        try:
            partner_fields = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'res.partner', 'fields_get',
                [],
                {'attributes': ['string', 'type', 'selection']}
            )
            for key, field_name in partner_target_keys.items():
                if field_name in partner_fields:
                    f_info = partner_fields[field_name]
                    self.partner_field_map[key] = {
                        'name': field_name,
                        'type': f_info.get('type'),
                        'selection': f_info.get('selection', [])
                    }
            logger.info(f"✨ Champs Studio Contact configurés : {list(self.partner_field_map.keys())}")
        except Exception as e:
            logger.warning(f"Erreur découverte res.partner : {e}")

    def _format_custom_value(self, val: Any, field_info: Dict[str, Any]) -> Any:
        """Adapte proprement la valeur au type Odoo (selection, integer, float, char, boolean)."""
        if val is None or val is False or val == "":
            return False
        
        f_type = field_info.get('type')
        try:
            if f_type == 'selection':
                # Valider contre les options autorisées du menu déroulant
                selection_opts = field_info.get('selection', [])
                val_str = str(val).strip().lower()
                for opt_key, opt_label in selection_opts:
                    if str(opt_key).lower() == val_str or str(opt_label).lower() == val_str:
                        return opt_key
                # Si aucune correspondance exacte, ne pas envoyer de valeur invalide
                return False
            elif f_type in ('integer', 'int'):
                s = re.sub(r'[^\d]', '', str(val))
                return int(s) if s else False
            elif f_type in ('float', 'monetary'):
                s = str(val).replace(',', '.').replace(' ', '').replace('€', '')
                return float(s) if s else False
            elif f_type == 'boolean':
                return bool(val)
            else:
                return str(val)
        except Exception:
            return False

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
            
            new_tag_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'crm.tag', 'create',
                [{'name': tag_name, 'color': 10}]
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
                ['x_studio_siren', 'ilike', siren],
                ['name', 'ilike', company_name]
            ]

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

    # ========== MODULE CONTACTS (res.partner) ==========

    def create_company_contact(self, company_data: Dict[str, Any], custom_values: Dict[str, Any]) -> Optional[int]:
        """Crée un contact de type Société (restaurant) dans res.partner avec tous ses champs Studio."""
        try:
            existing = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'res.partner', 'search',
                [[['name', 'ilike', company_data.get('name', '')], ['is_company', '=', True]]],
                {'limit': 1}
            )
            if existing:
                logger.info(f"💼 Contact entreprise existant trouvé (ID: {existing[0]}) pour '{company_data.get('name')}'")
                return existing[0]

            payload = {
                'name': company_data.get('name'),
                'is_company': True,
                'street': company_data.get('street') or False,
                'city': company_data.get('city') or False,
                'zip': company_data.get('zip') or False,
                'country_id': 75, # France
                'phone': company_data.get('phone') or False,
                'email': company_data.get('email') or False,
                'website': company_data.get('website') or False,
                'company_registry': company_data.get('siret') or False,
                'comment': company_data.get('comment') or False,
            }

            for key, val in custom_values.items():
                if val is not None and key in self.partner_field_map:
                    f_info = self.partner_field_map[key]
                    formatted = self._format_custom_value(val, f_info)
                    if formatted is not False:
                        payload[f_info['name']] = formatted

            if 'est_une_entreprise' in self.partner_field_map:
                payload[self.partner_field_map['est_une_entreprise']['name']] = True

            partner_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'res.partner', 'create',
                [payload]
            )
            logger.info(f"💼 Contact entreprise créé (ID: {partner_id}) avec champs Studio pour '{company_data.get('name')}'")
            return partner_id
        except Exception as e:
            logger.error(f"Erreur création contact entreprise '{company_data.get('name')}' : {e}")
            return None

    def create_director_contact(self, director_data: Dict[str, Any], company_partner_id: int) -> Optional[int]:
        """Crée un contact de type Individu (dirigeant) rattaché à l'entreprise dans res.partner."""
        try:
            payload = {
                'name': director_data.get('name'),
                'is_company': False,
                'parent_id': company_partner_id,
                'function': director_data.get('function') or 'Gérant',
                'phone': director_data.get('phone') or False,
                'email': director_data.get('email') or False,
                'comment': director_data.get('comment') or False,
            }

            partner_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'res.partner', 'create',
                [payload]
            )
            logger.info(f"👤 Contact dirigeant créé (ID: {partner_id}) : '{director_data.get('name')}' rattaché à entreprise ID {company_partner_id}")
            return partner_id
        except Exception as e:
            logger.error(f"Erreur création contact dirigeant '{director_data.get('name')}' : {e}")
            return None

    # ========== CRÉATION DE LEAD ==========

    def create_lead(self, lead_data: Dict[str, Any], custom_values: Dict[str, Any], stage_id: Optional[int] = None, tag_ids: Optional[list] = None, partner_id: Optional[int] = None) -> Optional[int]:
        """Crée une nouvelle piste en renseignant les champs standards et personnalisés Studio Lead."""
        try:
            payload = dict(lead_data)
            if stage_id:
                payload['stage_id'] = stage_id
            if tag_ids:
                payload['tag_ids'] = tag_ids
            if partner_id:
                payload['partner_id'] = partner_id

            for key, val in custom_values.items():
                if val is not None and key in self.lead_field_map:
                    f_info = self.lead_field_map[key]
                    formatted = self._format_custom_value(val, f_info)
                    if formatted is not False:
                        payload[f_info['name']] = formatted

            lead_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'crm.lead', 'create',
                [payload]
            )
            logger.info(f"✅ Lead créé dans Odoo (ID: {lead_id}) avec champs Studio Lead et Contact lié pour '{payload.get('name')}'")
            return lead_id
        except Exception as e:
            logger.error(f"Erreur lors de la création du Lead Odoo : {e}")
            return None
