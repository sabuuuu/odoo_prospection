import xmlrpc.client
import logging
import re
from typing import Dict, Any, Optional, List
from models import CompanyProspect, EnrichedContact

logger = logging.getLogger(__name__)

class OdooClient:
    """XML-RPC client for managing Odoo CRM leads and contacts."""

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
        """Authenticate with Odoo server via XML-RPC."""
        try:
            common = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/common', allow_none=True)
            self.uid = common.authenticate(self.db, self.username, self.api_key, {})
            if not self.uid:
                raise PermissionError("Échec d'authentification Odoo. Vérifiez vos identifiants/clé API.")
            self.models = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/object', allow_none=True)
            logger.info(f"Connecté à Odoo avec succès (UID: {self.uid})")
        except Exception as e:
            logger.error(f"Erreur de connexion à Odoo : {e}")
            raise

    def _discover_fields(self):
        """Introspect crm.lead and res.partner schemas for configured Studio custom fields."""
        target_lead_keys = {
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

        try:
            lead_fields = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'crm.lead', 'fields_get',
                [],
                {'attributes': ['string', 'type', 'selection']}
            )
            for key, field_name in target_lead_keys.items():
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

        target_partner_keys = {
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
            for key, field_name in target_partner_keys.items():
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
        """Convert and validate values based on target Odoo field types."""
        if val is None or val is False or val == "":
            return False
        
        f_type = field_info.get('type')
        try:
            if f_type == 'selection':
                selection_opts = field_info.get('selection', [])
                val_str = str(val).strip().lower()
                for opt_key, opt_label in selection_opts:
                    if str(opt_key).lower() == val_str or str(opt_label).lower() == val_str:
                        return opt_key
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
        """Find an existing CRM tag or create it if not present."""
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
            logger.warning(f"Erreur gestion étiquette '{tag_name}' : {e}")
            return None

    def get_stage_id(self, stage_name: str) -> Optional[int]:
        """Find a CRM stage ID by its display name."""
        try:
            stage_ids = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'crm.stage', 'search',
                [[['name', 'ilike', stage_name]]],
                {'limit': 1}
            )
            return stage_ids[0] if stage_ids else None
        except Exception as e:
            logger.error(f"Erreur recherche étape '{stage_name}' : {e}")
            return None

    def get_stage_ids(self, stage_names: list) -> list:
        """Find multiple CRM stage IDs from a list of stage names."""
        return [sid for name in stage_names if (sid := self.get_stage_id(name))]

    def lead_or_partner_exists(self, siren: str, company_name: str, stage_ids: Optional[list] = None) -> bool:
        """Check whether a lead or partner already exists using SIREN or company name."""
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
            logger.warning(f"Erreur vérification doublon {company_name} : {e}")
            return False

    def create_company_contact(self, company: CompanyProspect, enriched: EnrichedContact) -> Optional[int]:
        """Create or retrieve a company partner record (res.partner) with custom fields."""
        try:
            existing = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'res.partner', 'search',
                [[['name', 'ilike', company.denomination], ['is_company', '=', True]]],
                {'limit': 1}
            )
            if existing:
                logger.info(f"💼 Contact entreprise existant trouvé (ID: {existing[0]}) pour '{company.denomination}'")
                return existing[0]

            payload = {
                'name': company.denomination,
                'is_company': True,
                'street': company.adresse or False,
                'city': company.ville or False,
                'zip': company.code_postal or False,
                'country_id': 75,
                'phone': enriched.phone or False,
                'email': enriched.email or False,
                'website': enriched.website or False,
                'company_registry': company.siret or False,
                'comment': enriched.summary or False,
            }

            custom_values = {
                'siren': company.siren,
                'annee_ouverture': company.annee_ouverture or False,
                'raison_sociale': company.raison_sociale or False,
                'forme_juridique': company.forme_juridique or False,
                'nombre_salaries': company.tranche_effectif or False,
                'naf': f"{company.code_naf} {company.libelle_code_naf}".strip() or False,
                'linkedin': enriched.linkedin_url or False,
                'chiffre_affaires': company.chiffre_affaires or False,
                'annee_ca': company.annee_ca or False,
                'web': enriched.website or False,
                'est_une_entreprise': True
            }

            for key, val in custom_values.items():
                if val is not None and key in self.partner_field_map:
                    f_info = self.partner_field_map[key]
                    formatted = self._format_custom_value(val, f_info)
                    if formatted is not False:
                        payload[f_info['name']] = formatted

            # Odoo XML-RPC requires False rather than None for empty/null field values
            sanitized_payload = {k: (False if v is None else v) for k, v in payload.items()}

            partner_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'res.partner', 'create',
                [sanitized_payload]
            )
            logger.info(f"💼 Contact entreprise créé (ID: {partner_id}) pour '{company.denomination}'")
            return partner_id
        except Exception as e:
            logger.error(f"Erreur création contact entreprise '{company.denomination}' : {e}")
            return None

    def create_director_contact(self, enriched: EnrichedContact, company_partner_id: int, company_name: str) -> Optional[int]:
        """Create an individual contact (res.partner) linked to the company."""
        try:
            payload = {
                'name': enriched.contact_name,
                'is_company': False,
                'parent_id': company_partner_id,
                'function': enriched.job_title or 'Gérant',
                'phone': enriched.phone or False,
                'email': enriched.email or False,
                'comment': f"Dirigeant de {company_name}",
            }
            sanitized_payload = {k: (False if v is None else v) for k, v in payload.items()}

            partner_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'res.partner', 'create',
                [sanitized_payload]
            )
            logger.info(f"👤 Contact dirigeant créé (ID: {partner_id}) : '{enriched.contact_name}'")
            return partner_id
        except Exception as e:
            logger.error(f"Erreur création contact dirigeant '{enriched.contact_name}' : {e}")
            return None

    def create_lead(
        self,
        company: CompanyProspect,
        enriched: EnrichedContact,
        stage_id: Optional[int] = None,
        tag_ids: Optional[list] = None,
        partner_id: Optional[int] = None
    ) -> Optional[int]:
        """Create a CRM lead (crm.lead) populated with company data and AI qualification note."""
        try:
            full_contact = f"{company.denomination}, {enriched.contact_name}" if enriched.contact_name else company.denomination

            payload = {
                'name': company.denomination,
                'partner_name': company.denomination,
                'contact_name': full_contact,
                'function': enriched.job_title or "Direction",
                'email_from': enriched.email or False,
                'phone': enriched.phone or False,
                'website': enriched.website or False,
                'street': company.adresse or False,
                'city': company.ville or False,
                'zip': company.code_postal or False,
                'description': enriched.summary or ""
            }

            if stage_id:
                payload['stage_id'] = stage_id
            if tag_ids:
                payload['tag_ids'] = tag_ids
            if partner_id:
                payload['partner_id'] = partner_id

            custom_values = {
                'siren': company.siren,
                'annee_ouverture': company.annee_ouverture or False,
                'raison_sociale': company.raison_sociale or False,
                'forme_juridique': company.forme_juridique or False,
                'nombre_salaries': company.tranche_effectif or False,
                'naf': f"{company.code_naf} {company.libelle_code_naf}".strip() or False,
                'linkedin': enriched.linkedin_url or False,
                'chiffre_affaires': company.chiffre_affaires or False,
                'annee_ca': company.annee_ca or False,
                'type_de_restaurant': company.libelle_code_naf or False,
                'adresse_du_siege_social': company.full_address or False
            }

            for key, val in custom_values.items():
                if val is not None and key in self.lead_field_map:
                    f_info = self.lead_field_map[key]
                    formatted = self._format_custom_value(val, f_info)
                    if formatted is not False:
                        payload[f_info['name']] = formatted

            sanitized_payload = {k: (False if v is None else v) for k, v in payload.items()}

            lead_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'crm.lead', 'create',
                [sanitized_payload]
            )
            logger.info(f"✅ Lead créé dans Odoo (ID: {lead_id}) pour '{payload.get('name')}'")
            return lead_id
        except Exception as e:
            logger.error(f"Erreur création Lead Odoo : {e}")
            return None
