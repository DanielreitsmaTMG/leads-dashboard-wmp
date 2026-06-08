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

## Statussen

| Status | Kleur |
|--------|-------|
| Review nodig | geel |
| Contact mislukt | rood |
| Gesproken | blauw |
| Gaat op gesprek | donkerblauw |
| Geplaatst bij klant | groen |
| Afgewezen | grijs |

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
