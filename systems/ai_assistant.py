"""
AI-assistent functies voor het Lead Management Dashboard.

Bevat:
- summarize_lead(): genereert een korte profielomschrijving van een lead
  op basis van de formulierantwoorden (vervangt handmatig doorlezen).
- suggest_vacancy_text(): genereert een conceptvacaturetekst op basis van
  een korte briefing (functie, eisen, toon).

Gebruikt de Anthropic (Claude) API. De key wordt gelezen uit Streamlit
secrets (cloud) of de lokale .env (CLI/GitHub Actions), net als de
overige credentials in dit project.
"""

import os
import re
import json
import requests

try:
    import streamlit as st
    _HAS_ST = True
except Exception:
    _HAS_ST = False

import anthropic

MODEL = "claude-haiku-4-5"


def _api_key():
    if _HAS_ST:
        try:
            val = st.secrets.get("ANTHROPIC_API_KEY")
            if val:
                return val
        except Exception:
            pass
    return os.getenv("ANTHROPIC_API_KEY")


def _client():
    key = _api_key()
    if not key:
        return None
    return anthropic.Anthropic(api_key=key)


def _fetch_vacancy_text(url, max_chars=4000):
    """
    Haalt de platte tekst van een vacaturepagina op (eenvoudige HTML-strip).
    Retourneert None als het ophalen mislukt — de samenvatting werkt dan
    gewoon verder zonder vacaturetekst.
    """
    if not url:
        return None
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (compatible; LeadDashboardBot/1.0)"})
        r.raise_for_status()
        html = r.text
        # Verwijder script/style-blokken en alle overige tags
        html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", html)
        text = re.sub(r"&nbsp;|&amp;|&#\d+;|&[a-z]+;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars] if text else None
    except Exception:
        return None


def summarize_lead(full_name, vacancy_name, form_data, client_name=None, vacancy_url=None):
    """
    Genereert een korte profielsamenvatting van een lead op basis van de
    ingevulde formulierantwoorden, plus een inschatting van de geschiktheid.
    Als er een vacancy_url is meegegeven, wordt de inhoud van die vacaturepagina
    opgehaald en gebruikt om de match concreter te beoordelen (eisen uit de
    vacaturetekst vergelijken met de antwoorden van de kandidaat).
    Retourneert een string, of None bij een fout (bv. ontbrekende API-key).
    """
    client = _client()
    if client is None:
        return None

    antwoorden = "\n".join(f"- {k}: {v}" for k, v in (form_data or {}).items())
    if not antwoorden:
        antwoorden = "(geen aanvullende formulierantwoorden beschikbaar)"

    vacature_tekst = _fetch_vacancy_text(vacancy_url)
    vacature_blok = ""
    if vacature_tekst:
        vacature_blok = f"""

Hieronder staat de tekst van de vacaturepagina (eisen, taken, aanbod). Gebruik dit om
de match tussen kandidaat en vacature concreet te beoordelen — vergelijk specifiek de
eisen uit de vacature met wat de kandidaat heeft ingevuld:

VACATURETEKST:
{vacature_tekst}"""

    prompt = f"""Je bent een recruitment-assistent. Hieronder staan de gegevens van een sollicitant
die via een Meta Ads leadformulier heeft gereageerd op een vacature.

Naam: {full_name or "onbekend"}
Klant (opdrachtgever waarbij gesolliciteerd is): {client_name or "onbekend"}
Vacature: {vacancy_name or "onbekend"}
Formulierantwoorden (vraag: antwoord):
{antwoorden}{vacature_blok}

Schrijf een korte, CONCRETE samenvatting (max 4-5 zinnen, in het Nederlands) voor de recruiter.
Belangrijkste eis: noem de daadwerkelijk ingevulde antwoorden letterlijk/concreet, niet vaag
samengevat. Dus bijvoorbeeld:
- "Heeft 3 jaar ervaring als schilder" in plaats van "heeft relevante ervaring"
- "Heeft rijbewijs B" of "Heeft geen rijbewijs" — benoem dit altijd expliciet als het gevraagd is
- "Woont in Sliedrecht" in plaats van "woont in de buurt"
- "Beschikbaar vanaf 1 juli, 32 uur per week" in plaats van "is beschikbaar"

Loop dus de formulierantwoorden langs en verwerk de concrete waarden (aantal jaren ervaring,
rijbewijs ja/nee, woonplaats, beschikbaarheid, opleiding, etc.) letterlijk in de samenvatting.
Vat NIET vaag samen — wees specifiek en feitelijk, alsof je de antwoorden navertelt.

Sluit af met:
- Vermeld bij welke klant/opdrachtgever deze persoon heeft gesolliciteerd.
- Een inschatting van de match met de vacature (bijv. "lijkt goede match",
  "twijfelachtig vanwege...", "onvoldoende informatie om te beoordelen").
  {"Als er een vacaturetekst is meegegeven: vergelijk de concrete eisen uit die tekst expliciet met wat de kandidaat heeft ingevuld (bijv. 'vacature vraagt 2+ jaar ervaring en rijbewijs B — kandidaat heeft beide' of 'vacature vraagt rijbewijs B, kandidaat geeft aan dit niet te hebben — mogelijk knelpunt')." if vacature_tekst else ""}

Geef alleen de samenvatting terug, zonder inleidende tekst."""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        return f"⚠️ AI-samenvatting mislukt: {e}"


def suggest_vacancy_text(functietitel, kernpunten, toon="professioneel en uitnodigend"):
    """
    Genereert een conceptvacaturetekst op basis van een korte briefing.
    Retourneert een string, of None bij ontbrekende API-key.
    """
    client = _client()
    if client is None:
        return None

    prompt = f"""Je bent een recruitment-copywriter. Schrijf een wervende vacaturetekst in het Nederlands
voor de volgende functie:

Functietitel: {functietitel}
Belangrijkste punten / eisen / aanbod (los meegegeven door de recruiter):
{kernpunten}

Schrijf in een {toon} toon. Structuur:
1. Korte pakkende intro (2-3 zinnen)
2. "Wat ga je doen" — bullet points
3. "Wat vragen we" — bullet points
4. "Wat bieden we" — bullet points
5. Korte call-to-action om te solliciteren

Geef alleen de vacaturetekst terug, geen extra uitleg."""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=900,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        return f"⚠️ Genereren mislukt: {e}"
