import requests
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(__file__))
from database import get_all_clients, upsert_lead, upsert_form, update_ai_summary, get_form
from ai_assistant import summarize_lead

META_API_BASE = "https://graph.facebook.com/v21.0"

# Trefwoorden — veld wordt herkend als het sleutelwoord ERGENS in de veldnaam zit
NAME_KEYWORDS    = {"full_name", "fullname", "naam", "name"}
FIRST_KEYWORDS   = {"first_name", "firstname", "voornaam"}
LAST_KEYWORDS    = {"last_name", "lastname", "achternaam"}
EMAIL_KEYWORDS   = {"email", "e_mail", "emailadres", "mail"}
PHONE_KEYWORDS   = {"phone", "telefoon", "telefoonnummer", "mobile", "mobiel", "gsm", "tel"}

# Vacature-keywords: bewust strenger — geen "functie" of "position" want die zitten
# ook in vragen als "heb je ervaring in een soortgelijke functie?" (antwoord: ja/nee).
# Matching verloopt via _matches_vacancy(): key moet STARTEN met of GELIJK zijn aan
# het keyword, niet alleen ergens de substring bevatten.
VACANCY_KEYWORDS = {"vacaturenaam", "vacancy_name", "vacancy", "vacature_naam", "job_title", "jobtitle"}


def _token():
    try:
        import streamlit as st
        val = st.secrets.get("META_ACCESS_TOKEN")
        if val:
            return val
    except Exception:
        pass
    return os.getenv("META_ACCESS_TOKEN")


def fetch_all_clients():
    token = _token()
    if not token:
        return 0, ["META_ACCESS_TOKEN ontbreekt"]

    clients = get_all_clients()
    if not clients:
        return 0, ["Geen clients geconfigureerd"]

    total = 0
    log = []

    # Alle clients parallel ophalen
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_fetch_client, c["id"], c["page_id"], c["name"], token): c["name"]
            for c in clients
        }
        for future in as_completed(futures):
            name = futures[future]
            count, errors = future.result()
            total += count
            log.append(f"**{name}**: {count} nieuwe leads opgehaald")
            log.extend([f"  ⚠️ {e}" for e in errors])

    return total, log


def _get_page_token(page_id, user_token):
    r = requests.get(
        f"{META_API_BASE}/{page_id}",
        params={"fields": "access_token", "access_token": user_token},
        timeout=15,
    )
    data = r.json()
    if "access_token" in data:
        return data["access_token"], None
    return None, data.get("error", {}).get("message", "Onbekende fout bij ophalen page token")


def _fetch_client(client_id, page_id, client_name, token):
    errors = []
    page_token, err = _get_page_token(page_id, token)
    if not page_token:
        return 0, [f"Page token ophalen mislukt voor {client_name}: {err}"]

    url = f"{META_API_BASE}/{page_id}/leadgen_forms"
    try:
        r = requests.get(url, params={"access_token": page_token, "fields": "id,name", "limit": 100}, timeout=30)
        data = r.json()
        if "error" in data:
            return 0, [f"Formulieren ophalen mislukt voor {client_name}: {data['error'].get('message')}"]
        forms = data.get("data", [])
        if not forms:
            return 0, [f"Geen leadformulieren gevonden voor {client_name} (pagina {page_id})"]

        count = 0
        for form in forms:
            form_id   = form["id"]
            form_name = form.get("name", form_id)
            upsert_form(client_id, form_id, form_name)
            c, e = _fetch_form(form_id, form_name, client_id, client_name, page_token)
            count += c
            errors.extend(e)
        return count, errors
    except Exception as e:
        return 0, [str(e)]


def _fetch_form(form_id, form_name, client_id, client_name, token):
    url = f"{META_API_BASE}/{form_id}/leads"
    params = {"access_token": token, "fields": "id,created_time,field_data", "limit": 100}
    count = 0
    errors = []
    try:
        while url:
            r = requests.get(url, params=params, timeout=30)
            data = r.json()
            if "error" in data:
                errors.append(f"Formulier '{form_name}': {data['error'].get('message')}")
                break
            for lead in data.get("data", []):
                _process(lead, client_id, client_name, form_id, form_name)
                count += 1
            url = data.get("paging", {}).get("next")
            params = {}
    except Exception as e:
        errors.append(f"Formulier '{form_name}': {e}")
    return count, errors


def _matches(key, keywords):
    return any(kw in key for kw in keywords)

def _matches_vacancy(key, keywords):
    """Striktere match voor vacature-velden: key moet exact gelijk zijn aan of
    beginnen met het keyword, zodat 'soortgelijke_functie' e.d. NIET matchen."""
    return any(key == kw or key.startswith(kw + "_") for kw in keywords)


def _process(raw, client_id, client_name, form_id, form_name=None):
    first, last = [], []
    full = email = phone = vacancy = None
    extra = {}

    for field in raw.get("field_data", []):
        key = field["name"].lower().replace(" ", "_").replace("-", "_")
        val = (field.get("values") or [""])[0]
        if not val:
            continue

        if _matches_vacancy(key, VACANCY_KEYWORDS):
            vacancy = val
        elif _matches(key, NAME_KEYWORDS) and "first" not in key and "last" not in key:
            full = val
        elif _matches(key, FIRST_KEYWORDS):
            first.append(val)
        elif _matches(key, LAST_KEYWORDS):
            last.append(val)
        elif _matches(key, EMAIL_KEYWORDS):
            email = val
        elif _matches(key, PHONE_KEYWORDS):
            phone = val
        else:
            extra[field["name"]] = val

    # Combineer voor- en achternaam als volledige naam ontbreekt
    if not full and (first or last):
        full = " ".join(first + last).strip()

    # Laatste redmiddel: eerste veld gebruiken als naam
    if not full and raw.get("field_data"):
        first_val = (raw["field_data"][0].get("values") or [""])[0]
        if first_val:
            full = first_val

    # Vacature: als er geen expliciet "Vacaturenaam"-veld is ingevuld, val terug op de
    # naam van het leadformulier zelf — zo is er altijd een concrete vacature-referentie
    # beschikbaar voor weergave én voor de AI-samenvatting/matchmaker.
    if not vacancy:
        vacancy = form_name

    lead_id, is_new = upsert_lead({
        "meta_lead_id": raw["id"],
        "client_id":    client_id,
        "form_id":      form_id,
        "created_time": raw.get("created_time"),
        "full_name":    full,
        "email":        email,
        "phone":        phone,
        "vacancy_name": vacancy,
        "form_data":    extra,
    })

    # Genereer direct een AI-samenvatting voor nieuwe leads met formulierantwoorden,
    # zodat deze al klaarstaat zodra de recruiter het dashboard opent.
    if is_new and extra:
        try:
            form_row = get_form(form_id)
            vacancy_url = form_row.get("vacancy_url") if form_row else None
            summary = summarize_lead(full, vacancy, extra, client_name, vacancy_url)
            if summary and not summary.startswith("⚠️"):
                update_ai_summary(lead_id, summary)
        except Exception:
            pass  # AI-samenvatting is een extraatje — sync mag hier niet op stranden


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    from database import init_db
    init_db()
    total, log = fetch_all_clients()
    for line in log:
        print(line)
    print(f"Totaal: {total} nieuwe leads")
