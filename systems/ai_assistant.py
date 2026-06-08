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
import json

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


def summarize_lead(full_name, vacancy_name, form_data, client_name=None):
    """
    Genereert een korte (2-3 zinnen) profielsamenvatting van een lead op
    basis van de ingevulde formulierantwoorden, plus een korte inschatting
    van de geschiktheid. Retourneert een string, of None bij een fout
    (bv. ontbrekende API-key).
    """
    client = _client()
    if client is None:
        return None

    antwoorden = "\n".join(f"- {k}: {v}" for k, v in (form_data or {}).items())
    if not antwoorden:
        antwoorden = "(geen aanvullende formulierantwoorden beschikbaar)"

    prompt = f"""Je bent een recruitment-assistent. Hieronder staan de gegevens van een sollicitant
die via een Meta Ads leadformulier heeft gereageerd op een vacature.

Naam: {full_name or "onbekend"}
Klant (opdrachtgever waarbij gesolliciteerd is): {client_name or "onbekend"}
Vacature: {vacancy_name or "onbekend"}
Formulierantwoorden:
{antwoorden}

Schrijf een korte samenvatting (max 3 zinnen, in het Nederlands) voor de recruiter:
- Vermeld bij welke klant/opdrachtgever deze persoon heeft gesolliciteerd.
- Wie is deze persoon en wat is relevant uit de antwoorden?
- Geef een korte inschatting van de match met de vacature (bijv. "lijkt goede match",
  "twijfelachtig vanwege...", "onvoldoende informatie om te beoordelen").

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
