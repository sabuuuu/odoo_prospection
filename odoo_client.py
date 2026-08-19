import xmlrpc.client
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class OdooClient:
    """Client XML-RPC pour interagir avec Odoo CRM et gérer le dédoublonnage."""

    def __init__(self, url: str, db: str, username: str, api_key: str):
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self.api_key = api_key
        self.uid = None
        self.models = None
        self._authenticate()

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
            logger.warning(f"Étape Odoo '{stage_name}' non trouvée dans crm.stage.")
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
        """
        Vérifie si une entreprise existe déjà dans Odoo.
        - Si stage_ids est renseigné : vérifie si le lead existe dans ces étapes spécifiques (ex: 'Liste Restaurant' ou 'À contacter').
        - Vérifie aussi par SIREN et nom dans la base des contacts / pistes.
        """
        try:
            # 1. Recherche ciblée dans crm.lead
            domain_lead = [
                '|',
                ['description', 'ilike', siren],
                ['partner_name', 'ilike', company_name]
            ]
            if stage_ids:
                domain_lead.append(['stage_id', 'in', stage_ids])

            lead_ids = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'crm.lead', 'search',
                [domain_lead],
                {'limit': 1}
            )
            if lead_ids:
                return True

            # 2. Recherche dans res.partner (Contacts / Sociétés existantes)
            domain_partner = [
                '|',
                ['comment', 'ilike', siren],
                ['name', 'ilike', company_name]
            ]
            partner_ids = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'res.partner', 'search',
                [domain_partner],
                {'limit': 1}
            )
            return len(partner_ids) > 0

        except Exception as e:
            logger.warning(f"Erreur lors de la vérification de doublon pour {company_name} ({siren}) : {e}")
            return False

    def create_lead(self, lead_data: Dict[str, Any], stage_id: Optional[int] = None) -> Optional[int]:
        """Crée une nouvelle piste (Lead) dans le CRM Odoo en lui assignant l'étape cible si fournie."""
        try:
            payload = dict(lead_data)
            if stage_id:
                payload['stage_id'] = stage_id

            lead_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'crm.lead', 'create',
                [payload]
            )
            logger.info(f"Piste créée avec succès dans Odoo (ID: {lead_id}) pour '{payload.get('partner_name')}' (Étape ID: {stage_id})")
            return lead_id
        except Exception as e:
            logger.error(f"Impossible de créer la piste Odoo pour '{lead_data.get('partner_name')}' : {e}")
            return None
