import requests
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from database import get_all_clients, upsert_lead

META_API_BASE = "https://graph.facebook.com/v21.0"

NAME_FIELDS  = {"full_name", "name", "naam", "voornaam"}
FIRST_FIELDS = {"first_name"}
LAST_FIELDS  = {"last_name", "achternaam"}
EMAIL_FIELDS = {"email", "e_mail", "emailadres", "email_address"}
PHONE_FIELDS = {"phone_number", "phone", "telefoon", "telefoonnummer", "mobile", "mobiel"}


def _token():
    try:
        import streamlit as st
        return st.secrets["META_ACCESS_TOKEN"]
    except Exception:
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
    for client in clients:
        count, errors = _fetch_client(client["id"], client["page_id"], client["name"], token)
        total += count
        log.append(f"**{client['name']}**: {count} leads opgehaald")
        log.extend([f"  ⚠️ {e}" for e in errors])
    return total, log


def _get_page_token(page_id, user_token):
    """Wissel user/system token in voor een page access token."""
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
    # Haal page access token op
    page_token, err = _get_page_token(page_id, token)
    if not page_token:
        return 0, [f"Page token ophalen mislukt: {err}"]

    url = f"{META_API_BASE}/{page_id}/leadgen_forms"
    try:
        r = requests.get(url, params={"access_token": page_token, "fields": "id,name", "limit": 100}, timeout=30)
        data = r.json()
        if "error" in data:
            return 0, [f"Formulieren ophalen mislukt: {data['error'].get('message', data['error'])}"]
        forms = data.get("data", [])
        if not forms:
            return 0, [f"Geen leadformulieren gevonden op pagina {page_id}"]
        count = 0
        for form in forms:
            c, e = _fetch_form(form["id"], form.get("name", form["id"]), client_id, page_token)
            count += c
            errors.extend(e)
        return count, errors
    except Exception as e:
        return 0, [str(e)]


def _fetch_form(form_id, form_name, client_id, token):
    url = f"{META_API_BASE}/{form_id}/leads"
    params = {"access_token": token, "fields": "id,created_time,field_data", "limit": 100}
    count = 0
    errors = []
    try:
        while url:
            r = requests.get(url, params=params, timeout=30)
            data = r.json()
            if "error" in data:
                errors.append(f"Formulier '{form_name}': {data['error'].get('message', data['error'])}")
                break
            for lead in data.get("data", []):
                _process(lead, client_id)
                count += 1
            url = data.get("paging", {}).get("next")
            params = {}
    except Exception as e:
        errors.append(f"Formulier '{form_name}': {e}")
    return count, errors


def _process(raw, client_id):
    first, last = [], []
    full = email = phone = None
    extra = {}

    for field in raw.get("field_data", []):
        key = field["name"].lower().replace(" ", "_")
        val = (field.get("values") or [""])[0]
        if key in NAME_FIELDS:
            full = val
        elif key in FIRST_FIELDS:
            first.append(val)
        elif key in LAST_FIELDS:
            last.append(val)
        elif key in EMAIL_FIELDS:
            email = val
        elif key in PHONE_FIELDS:
            phone = val
        else:
            extra[field["name"]] = val

    if not full and (first or last):
        full = " ".join(first + last).strip()

    upsert_lead({
        "meta_lead_id": raw["id"],
        "client_id":    client_id,
        "created_time": raw.get("created_time"),
        "full_name":    full,
        "email":        email,
        "phone":        phone,
        "form_data":    extra,
    })
