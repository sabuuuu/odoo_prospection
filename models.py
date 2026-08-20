from dataclasses import dataclass
from typing import Optional

@dataclass
class Dirigeant:
    """Modèle représentant un dirigeant d'entreprise."""
    nom: str
    prenom: str
    qualite: str = "Dirigeant"
    age: Optional[str] = None
    date_naissance: Optional[str] = None
    nationalite: Optional[str] = None

    @property
    def full_name(self) -> str:
        return f"{self.prenom} {self.nom}".strip()

@dataclass
class CompanyProspect:
    """Modèle représentant une entreprise ciblée issue de Pappers."""
    siren: str
    siret: str
    denomination: str
    raison_sociale: str
    forme_juridique: str
    code_naf: str
    libelle_code_naf: str
    adresse: str
    code_postal: str
    ville: str
    chiffre_affaires: Optional[float] = None
    annee_ca: Optional[str] = None
    tranche_effectif: str = "Non précisé"
    date_creation: Optional[str] = None
    annee_ouverture: Optional[str] = None
    dirigeant: Optional[Dirigeant] = None

    @property
    def full_address(self) -> str:
        return f"{self.adresse} {self.code_postal} {self.ville}".strip()

@dataclass
class EnrichedContact:
    """Modèle représentant les coordonnées et l'analyse IA enrichies."""
    contact_name: Optional[str] = None
    job_title: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    linkedin_url: Optional[str] = None
    summary: str = ""
    dirigeant_found: bool = False

    @property
    def has_real_contact(self) -> bool:
        """Vérifie si des coordonnées directes (téléphone ou email) ont été trouvées."""
        return bool(self.phone or self.email)
