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
| Gesproken | blauw | 🔵 |
| Komt op gesprek | paars | 🟣 |
| Voorstel gedaan | oranje | 🟠 |
| Geplaatst bij klant | groen | 🟢 |
| Afgewezen | grijs | ⚫ |

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

## Kanban-weergave (leadsoverzicht)

Het leadsoverzicht heeft twee weergaven, te kiezen via de toggle bovenaan
("🗂️ Kanban" / "📋 Tabel"), opgeslagen in `st.session_state.view_mode`.

- **Kanban (standaard)**: één kolom per status uit `STATUSES`. Elke lead is een
  kaart met naam, vacature, klant (in het totaaloverzicht), datum, contactlinks,
  een inklapbare AI-samenvatting, een statusdropdown en knoppen voor notitie/details.
  Het wijzigen van de statusdropdown op een kaart roept `update_status()` aan en
  verplaatst de lead direct naar de bijbehorende kolom (`clear_cache()` + `st.rerun()`).
  Per kolom worden max. 30 kaarten getoond (`MAX_PER_COLUMN`); bij meer leads wordt
  geadviseerd de filters te verfijnen (periode/zoeken/sortering werken in beide
  weergaven hetzelfde).
- **Tabel**: de oorspronkelijke rij-per-lead weergave met paginering, klikbare
  statuskaarten als filter en bulkacties — ongewijzigd, voor wie liever een
  compact overzicht met meer leads tegelijk wil.

Gedeelde logica (AI-samenvatting tonen/genereren, notitie-editor) zit in de
hulpfuncties `render_summary()` en `render_notes_editor()` in `app.py`, zodat
beide weergaven consistent blijven.

### Mogelijke vervolgstappen (niet gebouwd, zie gesprek met gebruiker)

- Geautomatiseerd eerste-contact via WhatsApp/SMS direct na binnenkomst van een lead
- Automatische profielverrijking uit gesprekken (vergt punt hierboven)
- Workflow-/talent-journey-engine met triggers en wachttijden
- Database-reactivatie van koude leads
- Let op AVG: geautomatiseerde verwerking van persoonsgegevens van sollicitanten
  via AI vergt zorgvuldige juridische afweging (transparantie/toestemming/opslag)
