# Blueprint: Meta Access Token ophalen

## Wat je nodig hebt

- Toegang tot het Meta Business Manager account
- Beheerdersrechten op de pagina's van de klanten
- Een Meta Developer App (eenmalig aanmaken)

---

## Stap 1 — Meta Developer App aanmaken (eenmalig)

1. Ga naar https://developers.facebook.com/apps
2. Klik op **Create App**
3. Kies type: **Business**
4. Vul in:
   - App name: bijv. `Leads Dashboard`
   - Business account: kies jouw bedrijfsaccount
5. Klik **Create App**

---

## Stap 2 — Lead Ads Retrieval toevoegen

1. Ga in je app naar **Add a product**
2. Voeg toe: **Lead Ads Retrieval** (of "Webhooks" + "Lead Ads")
3. Ga naar **App Review → Permissions**
4. Zorg dat `leads_retrieval` is toegevoegd (voor live data)

> Voor testen binnen je eigen Business account heb je géén App Review nodig.

---

## Stap 3 — Langlopend Page Access Token genereren

### 3a. Korte token via Graph API Explorer

1. Ga naar https://developers.facebook.com/tools/explorer
2. Kies jouw app rechtsboven
3. Klik **Generate Access Token**
4. Vink aan:
   - `pages_show_list`
   - `leads_retrieval`
   - `pages_read_engagement`
5. Klik **Generate** → kopieer de token

### 3b. Omzetten naar langlopende token (60 dagen)

Voer dit in je browser of via curl uit:

```
https://graph.facebook.com/v21.0/oauth/access_token
  ?grant_type=fb_exchange_token
  &client_id={APP_ID}
  &client_secret={APP_SECRET}
  &fb_exchange_token={KORTE_TOKEN}
```

Vervang `{APP_ID}` en `{APP_SECRET}` met je app-gegevens (te vinden onder App Settings > Basic).

### 3c. Page Access Token ophalen

Met de langlopende user-token:

```
https://graph.facebook.com/v21.0/me/accounts?access_token={LANGLOPENDE_TOKEN}
```

Dit geeft een lijst van pagina's waartoe je toegang hebt.  
Kopieer de `access_token` van de gewenste pagina → dit is je **Page Access Token**.

> Page Access Tokens verlopen nooit als ze gegenereerd zijn vanuit een langlopende user-token.

---

## Stap 4 — Page ID ophalen

Uit de response van stap 3c heb je per pagina ook het `id` veld.  
Dit is je **Page ID** — voer dit in bij het toevoegen van een client in het dashboard.

---

## Stap 5 — Invullen in .env

```
META_ACCESS_TOKEN=EAAxxxxxxxxxxxxx...
```

Start daarna het dashboard:

```bash
python systems/app.py
```

---

## Troubleshooting

| Fout | Oplossing |
|------|-----------|
| `OAuthException: Invalid OAuth token` | Token verlopen → herhaal stap 3 |
| `Unsupported get request` op leads | `leads_retrieval` permissie ontbreekt |
| Lege forms-lijst | Pagina heeft geen Lead Ads formulieren aangemaakt |
| `#200` Permission error | App heeft geen toegang tot de pagina; voeg pagina toe als tester |
