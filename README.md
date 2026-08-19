# 🚀 Pipeline Automatisé de Prospection B2B (Odoo + Pappers + Claude AI)

Ce projet automatise l'alimentation quotidienne de votre CRM Odoo avec de nouveaux prospects qualifiés.
Il s'exécute automatiquement **tous les jours à 23h00** via **GitHub Actions**.

---

## 📋 Fonctionnement du Pipeline

1. **Recherche ciblée (Pappers API)** : Recherche d'entreprises selon des critères précis (Codes NAF/APE, Départements, CA minimum, statut actif).
2. **Dédoublonnage intelligent (Odoo XML-RPC)** : Vérifie si le SIREN ou le nom de l'entreprise existe déjà dans votre Odoo (`crm.lead` ou `res.partner`). Si oui, l'entreprise est ignorée pour ne jamais créer de doublon.
3. **Enrichissement de contact (Web Search & Claude AI)** :
   - Recherche du dirigeant et de l'entreprise sur Google / LinkedIn.
   - Claude 3.5 Haiku extrait et structure l'interlocuteur clé, son email pro, son téléphone, son profil LinkedIn et synthétise un résumé d'activité.
4. **Création du Lead (Odoo CRM)** : Création d'une nouvelle piste prête à être contactée dans votre CRM Odoo.

---

## 🛠️ Configuration & Installation Locale

### 1. Installation des dépendances
```bash
pip install -r requirements.txt
```

### 2. Configuration des variables d'environnement
Copiez le fichier `.env.example` en `.env` et remplissez vos identifiants :
```bash
cp .env.example .env
```

| Variable | Description |
| :--- | :--- |
| `ODOO_URL` | URL de votre instance Odoo (ex: `https://monentreprise.odoo.com`) |
| `ODOO_DB` | Nom de votre base de données Odoo |
| `ODOO_USER` | Email de votre compte utilisateur Odoo |
| `ODOO_API_KEY` | Clé API générée dans Odoo (*Préférences Utilisateur > Sécurité du compte > Clés API*) |
| `ODOO_TARGET_STAGE` | Étape Odoo où insérer les nouveaux leads (défaut : `Liste Restaurant`) |
| `ODOO_DEDUP_STAGES` | Étapes vérifiées pour le dédoublonnage (défaut : `Liste Restaurant,À contacter`) |
| `PAPPERS_API_KEY` | Clé API obtenue sur [Pappers.fr](https://www.pappers.fr/api) |
| `ANTHROPIC_API_KEY` | Clé API Claude AI obtenue sur [Anthropic Console](https://console.anthropic.com) |
| `SERPER_API_KEY` | *(Optionnel)* Clé Google Search API obtenue sur [Serper.dev](https://serper.dev) (2500 requêtes gratuites) |
| `TARGET_NAF_CODES` | Codes NAF cibles (ex: `5610A,5610C` pour les restaurants) |
| `TARGET_DEPARTMENTS` | Départements cibles (ex: `75,92,69` ou vide pour toute la France) |
| `DAILY_PROSPECT_LIMIT` | Nombre maximum de prospects à traiter par exécution (défaut : `10`) |

### 3. Tester localement

- **Mode Simulation (Dry-Run)** : Teste la recherche et l'enrichissement sans écrire dans Odoo :
  ```bash
  python main.py --dry-run
  ```

- **Mode Réel (avec limite)** :
  ```bash
  python main.py --limit 5
  ```

---

## ⏰ Déploiement Automatique sur GitHub Actions

Le workflow `.github/workflows/daily_prospecting.yml` s'exécute automatiquement chaque soir à 23h00.

### Configuration des Secrets sur GitHub :
1. Allez sur votre dépôt GitHub.
2. Cliquez sur **Settings > Secrets and variables > Actions > New repository secret**.
3. Ajoutez les secrets suivants :
   - `ODOO_URL`
   - `ODOO_DB`
   - `ODOO_USER`
   - `ODOO_API_KEY`
   - `PAPPERS_API_KEY`
   - `ANTHROPIC_API_KEY`
   - `SERPER_API_KEY` *(optionnel)*

### Lancement manuel depuis GitHub :
Vous pouvez déclencher le script à tout moment depuis l'onglet **Actions > Daily Prospecting Pipeline > Run workflow** (avec possibilité de cocher le mode simulation).
