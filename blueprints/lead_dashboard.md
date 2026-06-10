# Blueprint: Lead Dashboard

## Doel

Leads ophalen uit Meta Ads leadformulieren en tonen in een webdashboard met statusbeheer.

## Benodigde input

- `META_ACCESS_TOKEN` — Meta Page Access Token (in `.env`)
- `META_PAGE_ID` — ID van de Facebook-pagina (in `.env`)

## Hoe de Meta API werkt

1. `GET /{page-id}/leadgen_forms` — alle formulieren van de pagina
2. `GET /{form-id}/leads?fields=id,created_time,field_data` — leads per formulier
3. Paginering via `paging.next` in de response

Veldnamen zijn formulier-specifiek. Standaard herkende namen:
- Naam: `full_name`, `name`, `first_name`, `last_name`
- E-mail: `email`, `e_mail`, `email_address`
- Telefoon: `phone_number`, `phone`, `mobile`
- Overige velden → `form_data` (JSON)

## Systems

| System | Taak |
|--------|------|
| `systems/database.py` | SQLite-setup, upsert leads, status bijwerken |
| `systems/fetch_leads.py` | Meta API aanroepen, leads verwerken |
| `systems/app.py` | Flask-webserver, routes, auto-sync scheduler |

## Starten

```bash
pip install -r requirements.txt
# Vul .env in met META_ACCESS_TOKEN en META_PAGE_ID
python systems/app.py
# Dashboard: http://localhost:5000
```

## Statussen / fases

De fases vormen de Kanban-pijplijn (zie sectie "Kanban-weergave" hieronder) en zijn
bewust herkenbaar gehouden voor toekomstige automations per fase.

| Status / fase | Kleur | Emoji |
|--------|-------|-------|
| Instroom | geel | 🟡 |
| Nog geen contact | oranje | 🟠 |
| Gesproken | blauw | 🔵 |
| Komt op gesprek | paars | 🟣 |
| Voorstel gedaan | bruin | 🟤 |
| Geplaatst bij klant | groen | 🟢 |
| Afgewezen | grijs | ⚫ |

"Nog geen contact" zit tussen "Instroom" en "Gesproken": leads die al wel
bekeken zijn maar waarmee nog geen contact is gelegd.

**Migratiehistorie**: dit project gebruikte eerder de namen "Review nodig",
"Contact mislukt" en "Gaat op gesprek". Bij het opstarten van de app (init_db())
worden bestaande leads + statushistorie automatisch eenmalig omgezet:
- "Review nodig" → "Instroom"
- "Gaat op gesprek" → "Komt op gesprek"
- "Contact mislukt" → "Afgewezen" (geen aparte fase meer)

## Kopieren voor nieuwe klant

1. Kopieer de hele projectmap
2. Pas `.env` aan met de nieuwe klant-credentials
3. Verwijder `leads.db` (of laat het aanmaken)
4. Start opnieuw met `python systems/app.py`

## Edge cases

- Leads worden niet dubbel opgeslagen (`meta_lead_id` is UNIQUE)
- Veldnamen die niet herkend worden als naam/email/telefoon komen in `form_data`
- Bij API-fout: volledige foutmelding in de terminal, dashboard blijft draaien

## Rate limits

Meta Graph API: 200 calls per uur per token. Bij 15-minuten interval is dit ruim voldoende.

## AI-assistent functies (toegevoegd: stap 1 richting "Maxim"-vergelijking)

Het dashboard heeft een AI-laag (`systems/ai_assistant.py`) die de Anthropic (Claude)
API gebruikt. Geïnspireerd door een vergelijking met de tool meetmaxim.com/super-assistent —
dit is een eerste, losstaande stap; een volledige geautomatiseerde intake-conversatie
(zoals Maxim doet) is een veel groter traject en is bewust nog niet gebouwd.

Functies:

1. **AI-samenvatting per lead** — wordt nu AUTOMATISCH gegenereerd op het moment dat
   `systems/fetch_leads.py` een nieuwe lead binnenhaalt (in `_process()`, direct na
   `upsert_lead()`, alleen als de lead formulierantwoorden heeft). Dit loopt dus mee
   in de bestaande GitHub Actions sync (elke 30 min) — de samenvatting staat al klaar
   zodra de recruiter de lead voor het eerst opent, geen wachttijd in de browser.
   Resultaat wordt opgeslagen in `leads.ai_summary` / `ai_summary_at`.

   `upsert_lead()` retourneert `(lead_id, is_new)` zodat `_process()` weet of het
   om een nieuwe lead gaat (en dus alleen dán een samenvatting hoeft te genereren).

   **Backfill voor oudere leads** (`backfill_summaries()` in `fetch_leads.py`):
   leads zonder `ai_summary` (bv. van vóór deze functie, of gereset door een
   migratie) worden geleidelijk alsnog voorzien van een samenvatting — max. 5 per
   sync-run, om de Anthropic-rate-limit (5/min) niet te raken. Draait mee in:
   - de GitHub Actions cron-sync (elke 30 min)
   - de achtergrond-scheduler in `app.py` (`_scheduled_sync`, elke 15 min)
   - de "🔄 Vernieuwen"-knop in de sidebar

   ⚠️ **BELANGRIJKE LES (performance)**: eerder genereerde het dashboard
   (`app.py`) zelf live een samenvatting bij het tonen van de leadstabel als
   `ai_summary` ontbrak. Dit maakte de pagina extreem traag: elke ontbrekende
   samenvatting = een blokkerende Anthropic API-call, en met de rate-limit van
   5/min liep een pagina met >5 ontbrekende samenvattingen op tot enkele
   minuten laadtijd. Dit is VERWIJDERD — `render_summary()` in `app.py` toont nu
   alleen wat al in de database staat (of "⏳ samenvatting volgt"). Genereer
   NOOIT AI-content live tijdens het renderen van een lijst/tabel — gebruik
   altijd de sync/backfill (systems-laag) hiervoor.

2. **Follow-up signalering** — bovenaan het leadsoverzicht verschijnt een waarschuwing
   zodra er leads zijn die langer dan 24 uur op status "Instroom" staan
   (`get_stale_leads()` in `database.py`, gecached 5 min via `cached_stale_leads`).

3. **AI-vacaturetekst-assistent** — onderaan de instellingenpagina. Recruiter geeft
   functietitel + losse kernpunten op, Claude genereert een conceptvacaturetekst
   (intro / taken / eisen / aanbod / call-to-action). Puur een hulpmiddel — recruiter
   controleert en past aan voor publicatie.

### Benodigde secret

`ANTHROPIC_API_KEY` — moet worden toegevoegd aan:
- Lokale `.env` (voor CLI-gebruik) ✅
- Streamlit Cloud secrets (Manage app → Settings → Secrets) ✅
- **GitHub Actions secrets** (Settings → Secrets and variables → Actions) — VEREIST,
  want de samenvatting wordt nu gegenereerd tijdens de cron-sync (`sync_leads.yml`
  geeft `ANTHROPIC_API_KEY` door als env-variabele aan `fetch_leads.py`)

Zonder deze key tonen de AI-knoppen een duidelijke melding ("ANTHROPIC_API_KEY ontbreekt")
in plaats van te crashen — zie `_client()` in `ai_assistant.py`.

### Model & kosten

Gebruikt `claude-haiku-4-5` (snel en goedkoop, geschikt voor korte samenvattingen/teksten).
Per samenvatting/tekst kost dit een fractie van een cent. Bij hoog volume (honderden
samenvattingen per dag) kan dit oplopen — overweeg dan caching/batchverwerking.

## Leadsoverzicht: twee secties

Het leadsoverzicht bestaat uit twee secties die los van elkaar werken:

1. **Fasekaarten** (boven): één klikbare kaart per fase uit `STATUSES`, met het
   aantal leads in die fase. Klikken filtert de tabel hieronder
   (`st.session_state.status_filter_override`) en markeert de actieve kaart
   (`type="primary"`). Er is GEEN "Totaal"-kaart meer (bewust verwijderd).

   **Standaardgedrag per dag**: bij elke nieuwe kalenderdag (vergeleken via
   `st.session_state._filter_reset_date`) wordt de filter automatisch
   teruggezet naar de fase **"Instroom"** — zodat de recruiter elke dag
   begint met de nieuwste binnengekomen leads. Binnen dezelfde dag blijft een
   handmatig gekozen fase/​"Alle" gewoon staan.

2. **Kandidatentabel** (onder): één horizontale rij per lead, gefilterd op de
   gekozen fase + de overige filters (periode/zoeken/sorteren/paginering).
   Kolommen: tijd geleden, naam, "Gesolliciteerd op" (vacature/leadformulier +
   pagina/klant), e-mail, bel-icoon, AI-samenvatting, fase-dropdown,
   notitie-icoon, detail-icoon (→ volledige kandidaatkaart).
   De fase-dropdown roept `update_status()` aan — wijzigen verplaatst de lead
   direct naar een andere fase (en dus uit de huidige gefilterde weergave als
   die op een specifieke fase staat).

Gedeelde logica zit in de hulpfuncties `render_summary()` (toont alleen
bestaande `ai_summary`, genereert NIET live — zie performance-sectie) en
`render_notes_editor()` in `app.py`.

## Styling: Apple-achtige uitstraling

`app.py` injecteert via `st.markdown(..., unsafe_allow_html=True)` direct na
`st.set_page_config()` een CSS-blok dat het hele dashboard rustiger en strakker
maakt: systeemfont (SF Pro/-apple-system), ronde hoeken en zachte schaduwen op
knoppen/kaarten/inputs, subtiele hover-animatie op knoppen, en een lichte
scheidingslijn voor de sidebar. Geen losse stylesheet-bestanden — alles zit in
één CSS-blok bovenaan `app.py` zodat het makkelijk terug te vinden en aan te
passen is.

## Performance: database-laag (connection pool + init_db éénmalig)

⚠️ **BELANGRIJKE LES (performance, 2e ronde)**: het dashboard werd weer traag
door twee dingen op de database-laag:

1. **`init_db()` liep bij ÉLKE rerun** (elke knopklik, statuswijziging,
   pagina-navigatie veroorzaakt in Streamlit een volledige herrun van
   `app.py`). `init_db()` bevat naast `CREATE TABLE`/`ALTER TABLE` ook
   `UPDATE`-migraties die de hele `leads`- en `status_history`-tabel scannen
   (vacancy_name-reset + `_STATUS_MIGRATION`-loop). Bij honderden/duizenden
   leads is dat een zware query die bij elke interactie opnieuw werd
   uitgevoerd. **Fix**: `init_db()` wordt nu aangeroepen via een functie
   gewrapt in `@st.cache_resource`, zodat het maar ÉÉN keer per app-proces
   draait (gedeeld over alle gebruikers/sessies), niet bij elke rerun.

2. **Elke query opende een nieuwe databaseconnectie** (`_conn()` deed
   `psycopg.connect(...)` per call). Neon (serverless Postgres) heeft
   merkbare opzet-vertraging per nieuwe connectie (TLS-handshake +
   eventuele "cold start" van het compute-endpoint) — bij een paginalaad met
   meerdere queries (clients, counts, leads, vacatures, dagteller, ...) liep
   dit op tot seconden per pagina. **Fix**: `database.py` gebruikt nu een
   process-brede `psycopg_pool.ConnectionPool` (lazy aangemaakt,
   `min_size=1, max_size=5`) — losse queries hergebruiken bestaande
   connecties en zijn vrijwel instant. Vereist `psycopg-pool` in
   `requirements.txt`.

3. Daarnaast: de "dagsamenvatting" (nieuwe leads vandaag) deed eerst een
   volledige `SELECT *`-query (`get_leads(days=1)`) en telde in Python.
   Vervangen door een lichte `COUNT(*)`-query (`get_leads_today_count()`),
   gecached via `cached_today_count`.

**Algemene les**: in Streamlit draait bij elke interactie het hele script
opnieuw — alles wat niet per-rerun hoeft (eenmalige setup, schema-migraties,
dure aggregaties) hoort in `@st.cache_resource` (proces-breed, één keer) of
`@st.cache_data` (per input, met TTL), nooit los op module-niveau.

## UI/UX-pijplijn-styling (fasekleuren, stepper, tijdlijn)

Naast de algemene Apple-achtige CSS is er een tweede ronde polish doorgevoerd
gericht op de recruitment-pijplijn zelf:

- **`STATUS_HEX`** (app.py) — hex-kleur per fase, gebruikt als visuele "bron
  van waarheid" voor kleuraccenten door de hele UI (naast `BADGE_EMOJI`).
- **Fasekaarten** (sectie 1 van het leadsoverzicht): tonen nu het AANTAL groot
  bovenaan en de fasenaam klein eronder (label-formaat `"{count}\n{emoji} {s}"`),
  met een gekleurde onderrand per fase (dikker bij de actieve/geselecteerde
  fase). Werkt via een CSS "anchor + adjacent sibling"-trucje: een onzichtbare
  `<div class="fase-anchor">` vlak vóór `st.columns(...)`, gevolgd door
  `nth-child(N)`-regels gegenereerd uit `STATUSES`/`STATUS_HEX`.
- **Dagsamenvatting**: onder de paginatitel staat nu `📅 X nieuwe lead(s)
  vandaag · Y wachten in Instroom`.
- **Kandidatentabel**: elke rij krijgt een gekleurde linkerrand o.b.v.
  `lead["status"]` + subtiele hover-highlight, via hetzelfde
  anchor+adjacent-sibling-trucje maar dan per lead-id (`row-anchor-{id}`).
  Actie-knoppen (notitie/detail) in de rij zijn in dezelfde stylesheet
  compacter en "ghost"-stijl gemaakt (geen rand/schaduw, alleen hover-highlight).
- **Lege staten**: per fase een passende boodschap (bijv. "✅ Instroom is
  leeg, mooi rustig!" / "🎉 Geen afgewezen leads").
- **Detailpagina**: horizontale pijplijn-stepper bovenaan (cirkels met
  vinkje/bullet per fase, verbonden door een lijn die de voortgang toont) en
  de statusgeschiedenis is een verticale tijdlijn geworden (gekleurde stip +
  verbindingslijn per gebeurtenis) i.p.v. een platte lijst.
- **Sidebar**: klanten zonder `logo_url` tonen nu initialen in een gekleurde
  cirkel (`.client-avatar`) i.p.v. een 👤-emoji, voor een consistentere look.
  De onderste knoppen (Vernieuwen/Vacature maker/Instellingen) staan onder een
  klein "TOOLS"-label.

⚠️ **Let op bij toekomstige wijzigingen**: het anchor+adjacent-sibling
CSS-trucje (`.fase-anchor + div[data-testid="stHorizontalBlock"]` en
`.row-anchor-{id} + div[data-testid="stHorizontalBlock"]`) gaat ervan uit dat
Streamlit een `st.markdown(...)`-element en het daaropvolgende
`st.columns(...)`-blok als directe siblings in de DOM rendert. Dit klopt in
de huidige Streamlit-versie maar is geen officieel gegarandeerde API — als een
toekomstige Streamlit-update de DOM-structuur wijzigt, kunnen deze
kleuraccenten wegvallen (de tabel/kaarten blijven dan wel gewoon functioneel,
alleen zonder kleuraccent). Test dit na een Streamlit-versie-upgrade.

## Klantlogo's in de sidebar

Elke klant in de sidebar toont het profielfoto-logo van de bijbehorende
Facebook-pagina (kolom naast de klantknop, 28x28px afgeronde hoek). Werking:

1. `clients.logo_url` (database.py, `init_db()`-migratie) slaat de URL op.
2. `fetch_leads.py` → `_get_page_logo(page_id, page_token)` haalt
   `GET /{page-id}/picture?type=normal&redirect=false` op (retourneert
   `data.url`) en `_fetch_client()` slaat dit op via `set_client_logo()` —
   dit loopt dus automatisch mee in elke sync (cron, scheduler, "Vernieuwen").
3. `app.py` sidebar: `st.columns([1,5])` per klant — logo (of 👤 fallback als
   `logo_url` ontbreekt) in de eerste kolom, klantknop in de tweede.

Let op: Facebook-profielfoto-URL's met `redirect=false` zijn doorgaans
langlevende CDN-links, maar kunnen periodiek wijzigen — vandaar dat dit bij
elke sync wordt ververst i.p.v. eenmalig opgeslagen.

### Mogelijke vervolgstappen (niet gebouwd, zie gesprek met gebruiker)

- Geautomatiseerd eerste-contact via WhatsApp/SMS direct na binnenkomst van een lead
- Automatische profielverrijking uit gesprekken (vergt punt hierboven)
- Workflow-/talent-journey-engine met triggers en wachttijden
- Database-reactivatie van koude leads
- Let op AVG: geautomatiseerde verwerking van persoonsgegevens van sollicitanten
  via AI vergt zorgvuldige juridische afweging (transparantie/toestemming/opslag)
