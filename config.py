import os
from dotenv import load_dotenv

# Charge les variables d'environnement depuis le fichier .env en local
load_dotenv()

class Config:
    # Odoo
    ODOO_URL = os.getenv("ODOO_URL", "").rstrip("/")
    ODOO_DB = os.getenv("ODOO_DB", "")
    ODOO_USER = os.getenv("ODOO_USER", "")
    ODOO_API_KEY = os.getenv("ODOO_API_KEY", "")

    # Odoo Stages / Listes
    ODOO_TARGET_STAGE = os.getenv("ODOO_TARGET_STAGE", "Liste Restaurant")
    ODOO_DEDUP_STAGES = [s.strip() for s in os.getenv("ODOO_DEDUP_STAGES", "Liste Restaurant,À contacter").split(",") if s.strip()]

    # Pappers
    PAPPERS_API_KEY = os.getenv("PAPPERS_API_KEY", "")

    # Claude AI (Anthropic)
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-3-5-haiku-20241022")

    # Serper (Google Search API)
    SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")

    # Critères de prospection (5610A: Restauration traditionnelle, 5610B: Cafétérias, 5610C: Restauration rapide)
    TARGET_NAF_CODES = [code.strip() for code in os.getenv("TARGET_NAF_CODES", "5610A,5610B,5610C").split(",") if code.strip()]
    TARGET_DEPARTMENTS = [dep.strip() for dep in os.getenv("TARGET_DEPARTMENTS", "57").split(",") if dep.strip()]
    MIN_TURNOVER = int(os.getenv("MIN_TURNOVER", "0")) if os.getenv("MIN_TURNOVER") else None
    DAILY_PROSPECT_LIMIT = int(os.getenv("DAILY_PROSPECT_LIMIT", "10"))

    @classmethod
    def validate(cls):
        """Vérifie la présence des clés obligatoires."""
        missing = []
        if not cls.ODOO_URL: missing.append("ODOO_URL")
        if not cls.ODOO_DB: missing.append("ODOO_DB")
        if not cls.ODOO_USER: missing.append("ODOO_USER")
        if not cls.ODOO_API_KEY: missing.append("ODOO_API_KEY")
        if not cls.PAPPERS_API_KEY: missing.append("PAPPERS_API_KEY")
        if not cls.ANTHROPIC_API_KEY: missing.append("ANTHROPIC_API_KEY")
        
        if missing:
            raise ValueError(f"Variables d'environnement manquantes : {', '.join(missing)}")
