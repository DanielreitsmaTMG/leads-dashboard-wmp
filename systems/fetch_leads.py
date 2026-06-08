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
        print("[fetch_leads] META_ACCESS_TOKEN ontbreekt")
        return 0

    clients = get_all_clients()
    if not clients:
        print("[fetch_leads] Geen clients geconfigureerd")
        return 0

    total = 0
    for client in clients:
        total += _fetch_client(client["id"], client["page_id"], token)
    print(f"[fetch_leads] {total} nieuwe leads — {datetime.now().strftime('%H:%M:%S')}")
    return total


def _fetch_client(client_id, page_id, token):
    url = f"{META_API_BASE}/{page_id}/leadgen_forms"
    r = requests.get(url, params={"access_token": token, "fields": "id,name", "limit": 100}, timeout=30)
    r.raise_for_status()
    forms = r.json().get("data", [])
    count = 0
    for form in forms:
        count += _fetch_form(form["id"], client_id, token)
    return count


def _fetch_form(form_id, client_id, token):
    url = f"{META_API_BASE}/{form_id}/leads"
    params = {"access_token": token, "fields": "id,created_time,field_data", "limit": 100}
    count = 0
    while url:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        for lead in data.get("data", []):
            _process(lead, client_id)
            count += 1
        url = data.get("paging", {}).get("next")
        params = {}
    return count


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
