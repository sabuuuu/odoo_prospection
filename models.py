from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class Dirigeant:
    """Company officer / legal representative."""
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
    """Target business prospect sourced from registry data."""
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
    enseigne: Optional[str] = None  # Trade name (often the real restaurant name)
    enseignes: List[str] = field(default_factory=list)  # All trade names of open establishments
    etablissement_ferme: bool = False  # Every establishment is closed
    dirigeants: List[Dirigeant] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)  # Raw Pappers search result

    @property
    def dirigeant(self) -> Optional[Dirigeant]:
        return self.dirigeants[0] if self.dirigeants else None

    @property
    def full_address(self) -> str:
        return f"{self.adresse} {self.code_postal} {self.ville}".strip()

@dataclass
class DirigeantContact:
    """Officer/owner identified during enrichment, with their own contact details."""
    nom_complet: str
    role: Optional[str] = None
    telephone: Optional[str] = None
    email: Optional[str] = None
    linkedin_url: Optional[str] = None
    sources: List[str] = field(default_factory=list)

    @property
    def has_direct_contact(self) -> bool:
        """Personal phone or email found for this person."""
        return bool(self.telephone or self.email)

@dataclass
class EnrichedContact:
    """Enriched venue/officer contact information and AI qualification note."""
    phone: Optional[str] = None      # Venue phone
    email: Optional[str] = None      # Venue email
    website: Optional[str] = None
    facebook_url: Optional[str] = None
    instagram_url: Optional[str] = None
    dirigeants: List[DirigeantContact] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)  # Cuisine, price range, Google rating, opening hours...
    summary: str = ""

    @property
    def principal(self) -> Optional[DirigeantContact]:
        """Main contact: first officer with direct contact details, else the first officer."""
        return next((d for d in self.dirigeants if d.has_direct_contact), self.dirigeants[0] if self.dirigeants else None)

    @property
    def has_direct_director_contact(self) -> bool:
        return any(d.has_direct_contact for d in self.dirigeants)
