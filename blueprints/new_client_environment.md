# Blueprint: Nieuwe klantomgeving opzetten

## Doel

Voor een nieuwe klant (bv. "Het Achterhuis") een eigen, volledig gescheiden
Lead Dashboard live zetten — eigen database, eigen login, eigen branding —
zonder een nieuwe codebase te hoeven onderhouden.

## Architectuur

Eén GitHub-repo (deze) wordt **meerdere keren gedeployed** op Streamlit
Community Cloud. Elke deployment is een aparte "app" met:

- Een eigen Neon-database (leeg, wordt automatisch gevuld door `init_db()`)
- Eigen `secrets.toml` (Streamlit Cloud → app → Settings → Secrets)
- Eigen loginscherm (gebruikersnaam/wachtwoord per omgeving)
- Eigen GitHub Actions-cron voor het ophalen van leads (aparte secrets per
  workflow-run, of een aparte workflow-file met andere `DATABASE_URL`)

Er is dus **geen multi-tenant logica** nodig in de code: elke omgeving denkt
dat hij de enige is, maar wijst naar zijn eigen database.

## Stappenplan: nieuwe klant toevoegen

### 1. Nieuwe Neon-database

- Maak een nieuw Neon-project aan (of nieuwe database binnen bestaand project).
- Kopieer de connection string (`DATABASE_URL`).
- Geen verdere actie nodig: `init_db()` (in `systems/database.py`) maakt bij
  de eerste run automatisch alle tabellen aan — de omgeving start dus
  gegarandeerd leeg, zonder klanten/leads van andere omgevingen.

### 2. Nieuwe Streamlit Cloud app

- Deploy dezelfde GitHub-repo (`main`-branch, `systems/app.py`) als nieuwe app.
- Zet in **Secrets** van deze app:

  ```toml
  DATABASE_URL = "postgresql://...nieuwe-neon-db..."
  META_ACCESS_TOKEN = "...zelfde token als WMP, zelfde Business Manager..."
  ANTHROPIC_API_KEY = "...zelfde als WMP..."

  APP_TITLE = "⚡ Het Achterhuis - Leads Dashboard"

  LOGIN_USERNAME = "info@achterhuis.nl"
  LOGIN_PASSWORD = "Achterhuis2026!"
  ```

- `META_ACCESS_TOKEN` en `ANTHROPIC_API_KEY` mogen hetzelfde zijn als bij WMP
  zolang de Facebook-pagina van de nieuwe klant binnen dezelfde Business
  Manager valt (Topmedia heeft daar al toegang toe).

### 3. Klant + Facebook-pagina toevoegen

- Log in op de nieuwe omgeving met de hierboven ingestelde gebruikersnaam/
  wachtwoord.
- Ga naar **⚙️ Instellingen** → voeg de klant toe met naam + Facebook
  Page ID (de Page ID van Het Achterhuis moet hiervoor bekend zijn).
- Klik op **🔄 Vernieuwen** in de sidebar om direct leads + logo op te halen.

### 4. Automatische sync (cron)

- De bestaande GitHub Action (`.github/workflows/sync_leads.yml`) draait op
  de `DATABASE_URL`/`META_ACCESS_TOKEN` uit de **GitHub repo secrets** — die
  zijn dus gedeeld tussen alle omgevingen op basis van deze repo.
- Als de nieuwe klant via een **andere** database moet syncen, is een losse
  workflow-file nodig (bv. `sync_leads_achterhuis.yml`) met eigen secrets
  (`DATABASE_URL_ACHTERHUIS`, etc.) die `systems/fetch_leads.py` aanroept met
  een andere `DATABASE_URL` env var.
- **Tot die workflow er is**: gebruik de "🔄 Vernieuwen"-knop in het dashboard
  om handmatig te syncen.

## Login

- Geregeld via `_check_login()` in `systems/app.py`.
- Als `LOGIN_USERNAME`/`LOGIN_PASSWORD` niet zijn ingesteld in de secrets van
  een omgeving, is er **geen** login vereist (backwards compatible voor
  omgevingen die dat (nog) niet willen).
- Branding (titel in sidebar + browsertab + loginscherm) via `APP_TITLE`.

## Geleerd / aandachtspunten

- Elke nieuwe omgeving start volledig leeg — er is geen data-migratie of
  "kopieer zonder vulling"-stap nodig, dat gebeurt vanzelf door een lege
  database + `init_db()`.
- Houd per omgeving de combinatie {Neon-DB, Streamlit-app, login} bij in een
  wachtwoordmanager — er is (bewust) geen centraal overzicht in de code.
