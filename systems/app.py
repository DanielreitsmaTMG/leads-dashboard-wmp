import os
import sys
import json
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

sys.path.insert(0, os.path.dirname(__file__))
from database import (
    init_db, get_all_clients, add_client, delete_client,
    get_leads, get_lead, get_status_counts,
    update_status, update_notes,
    get_forms_for_client, set_form_active,
    get_vacancies_for_client,
    STATUSES, STATUS_COLORS,
)
from fetch_leads import fetch_all_clients


@st.cache_data(ttl=120, show_spinner=False)
def cached_clients():
    return get_all_clients()

@st.cache_data(ttl=60, show_spinner=False)
def cached_leads(client_id, status_filter, search, days, vacancy_name=None):
    return get_leads(client_id=client_id, status_filter=status_filter, search=search, days=days, vacancy_name=vacancy_name)

@st.cache_data(ttl=60, show_spinner=False)
def cached_vacancies(client_id):
    return get_vacancies_for_client(client_id) if client_id else []

@st.cache_data(ttl=60, show_spinner=False)
def cached_counts(client_id):
    return get_status_counts(client_id)

@st.cache_data(ttl=120, show_spinner=False)
def cached_forms(client_id):
    return get_forms_for_client(client_id)

def clear_cache():
    cached_clients.clear()
    cached_leads.clear()
    cached_counts.clear()
    cached_forms.clear()
    cached_vacancies.clear()

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# ── Eenmalig: DB + scheduler ──────────────────────────────────────────────────
try:
    init_db()
except Exception as e:
    st.error(f"Database verbindingsfout: {e}")
    url = ""
    try:
        url = st.secrets["DATABASE_URL"]
        url = url[:30] + "..." + url[-20:]
    except Exception:
        url = "(niet gevonden)"
    st.code(f"DATABASE_URL (deels): {url}")
    st.stop()

if "scheduler_started" not in st.session_state:
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_all_clients, "interval", minutes=15, id="auto_sync")
    scheduler.start()
    st.session_state.scheduler_started = True

# ── Sessiestaat ───────────────────────────────────────────────────────────────
if "page" not in st.session_state:
    st.session_state.page = "leads"
if "active_client_id" not in st.session_state:
    st.session_state.active_client_id = None
if "active_vacancy" not in st.session_state:
    st.session_state.active_vacancy = None
if "selected_lead_id" not in st.session_state:
    st.session_state.selected_lead_id = None

# ── Kleurmapping → Streamlit badge-kleuren ────────────────────────────────────
BADGE_EMOJI = {
    "Review nodig":        "🟡",
    "Contact mislukt":     "🔴",
    "Gesproken":           "🔵",
    "Gaat op gesprek":     "🟣",
    "Geplaatst bij klant": "🟢",
    "Afgewezen":           "⚫",
}


def fmt_dt(value):
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%d-%m-%Y %H:%M")
    except Exception:
        return value


# ── Sidebar ───────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Leads Dashboard", page_icon="⚡", layout="wide")

with st.sidebar:
    st.markdown("## ⚡ Leads Dashboard")
    st.divider()

    st.markdown("**Clients**")
    clients = cached_clients()

    if st.button("🌐 Alle clients", use_container_width=True,
                 type="primary" if st.session_state.active_client_id is None and st.session_state.page == "leads" else "secondary"):
        st.session_state.active_client_id = None
        st.session_state.active_vacancy = None
        st.session_state.page = "leads"
        st.rerun()

    for c in clients:
        is_active_client = st.session_state.active_client_id == c["id"] and st.session_state.page == "leads"
        is_active_no_vacancy = is_active_client and st.session_state.active_vacancy is None
        if st.button(f"👤 {c['name']}", use_container_width=True,
                     type="primary" if is_active_no_vacancy else "secondary",
                     key=f"client_{c['id']}"):
            st.session_state.active_client_id = c["id"]
            st.session_state.active_vacancy = None
            st.session_state.page = "leads"
            st.rerun()

        # Vacatures als sub-items onder actieve client
        if is_active_client:
            vacancies = cached_vacancies(c["id"])
            for v in vacancies:
                is_active_vac = st.session_state.active_vacancy == v
                if st.button(f"  💼 {v}", use_container_width=True,
                             type="primary" if is_active_vac else "secondary",
                             key=f"vac_{c['id']}_{v}"):
                    st.session_state.active_vacancy = v
                    st.session_state.page = "leads"
                    st.rerun()

    st.divider()

    if st.button("🔄 Vernieuwen", use_container_width=True):
        with st.spinner("Leads ophalen..."):
            n, log = fetch_all_clients()
            clear_cache()
            for line in log:
                if "⚠️" in line:
                    st.warning(line)
                else:
                    st.success(line)
            st.rerun()

    if st.button("⚙️ Instellingen", use_container_width=True,
                 type="primary" if st.session_state.page == "settings" else "secondary"):
        st.session_state.page = "settings"
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# Pagina: Instellingen
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.page == "settings":
    st.title("⚙️ Instellingen")

    # ── Token status ──────────────────────────────────────────────────────────
    from fetch_leads import _token
    token = _token()
    if token:
        st.success("✅ Meta Access Token is ingesteld.")
    else:
        st.error("❌ Meta Access Token ontbreekt. Voeg `META_ACCESS_TOKEN` toe aan Streamlit Secrets.")

    st.divider()

    # ── Pagina's ontdekken via Meta API ───────────────────────────────────────
    st.subheader("📄 Pagina's ontdekken")
    if token:
        if st.button("🔍 Haal al mijn pagina's op uit Meta", type="primary"):
            import requests
            pages = []
            try:
                # Stap 1: directe pagina's via gebruiker
                r = requests.get(
                    "https://graph.facebook.com/v21.0/me/accounts",
                    params={"access_token": token, "limit": 100},
                    timeout=15,
                )
                r.raise_for_status()
                pages += r.json().get("data", [])

                # Stap 2: pagina's via Business Manager
                biz_r = requests.get(
                    "https://graph.facebook.com/v21.0/me/businesses",
                    params={"access_token": token, "limit": 100},
                    timeout=15,
                )
                biz_r.raise_for_status()
                businesses = biz_r.json().get("data", [])

                seen_ids = {p["id"] for p in pages}
                for biz in businesses:
                    for endpoint in ["owned_pages", "client_pages"]:
                        pr = requests.get(
                            f"https://graph.facebook.com/v21.0/{biz['id']}/{endpoint}",
                            params={"access_token": token, "fields": "id,name", "limit": 100},
                            timeout=15,
                        )
                        if pr.ok:
                            for page in pr.json().get("data", []):
                                if page["id"] not in seen_ids:
                                    pages.append(page)
                                    seen_ids.add(page["id"])

                st.session_state["discovered_pages"] = pages
                # Debug output
                st.session_state["debug_me"] = r.json()
                st.session_state["debug_biz"] = biz_r.json()
            except Exception as e:
                st.error(f"Fout bij ophalen pagina's: {e}")

        if "debug_me" in st.session_state:
            with st.expander("🔧 Debug: /me/accounts"):
                st.json(st.session_state["debug_me"])
        if "debug_biz" in st.session_state:
            with st.expander("🔧 Debug: /me/businesses"):
                st.json(st.session_state["debug_biz"])

        if "discovered_pages" in st.session_state:
            pages = st.session_state["discovered_pages"]
            existing_ids = {c["page_id"] for c in cached_clients()}
            new_pages = [p for p in pages if p["id"] not in existing_ids]

            if not pages:
                st.warning("Geen pagina's gevonden. Controleer of je token `pages_show_list` rechten heeft.")
            else:
                st.caption(f"{len(pages)} pagina's gevonden — {len(new_pages)} nog niet toegevoegd")
                for page in pages:
                    already = page["id"] in existing_ids
                    col_a, col_b = st.columns([4, 1])
                    col_a.markdown(f"**{page['name']}**  \n`{page['id']}`")
                    if already:
                        col_b.markdown("✅ Actief")
                    else:
                        if col_b.button("➕ Toevoegen", key=f"add_page_{page['id']}"):
                            add_client(page["name"], page["id"])
                            clear_cache()
                            st.success(f"'{page['name']}' toegevoegd!")
                            st.session_state.pop("discovered_pages", None)
                            st.rerun()
    else:
        st.info("Stel eerst een geldig Meta Access Token in om pagina's op te halen.")

    st.divider()

    # ── Handmatig toevoegen ───────────────────────────────────────────────────
    with st.expander("✏️ Client handmatig toevoegen"):
        with st.form("add_client_form"):
            name    = st.text_input("Naam client", placeholder="bijv. Werken met Passie")
            page_id = st.text_input("Facebook Page ID", placeholder="bijv. 123456789012345")
            if st.form_submit_button("➕ Toevoegen", type="primary"):
                if name and page_id:
                    add_client(name.strip(), page_id.strip())
                    clear_cache()
                    st.success(f"'{name}' toegevoegd!")
                    st.rerun()
                else:
                    st.warning("Vul naam én Page ID in.")

    # ── Clients + formulieren ─────────────────────────────────────────────────
    st.subheader("Clients & formulieren")
    clients = cached_clients()
    if not clients:
        st.info("Nog geen clients toegevoegd.")
    for c in clients:
        with st.expander(f"**{c['name']}** — `{c['page_id']}`", expanded=True):
            forms = cached_forms(c["id"])
            if forms:
                st.caption("Zet formulieren aan of uit:")
                for f in forms:
                    new_val = st.toggle(
                        f["form_name"] or f["form_id"],
                        value=bool(f["active"]),
                        key=f"form_{f['form_id']}",
                    )
                    if new_val != bool(f["active"]):
                        set_form_active(f["form_id"], new_val)
                        clear_cache()
                        st.rerun()
            else:
                st.caption("Nog geen formulieren — klik op 🔄 Vernieuwen om ze op te halen.")
            if st.button("🗑️ Client verwijderen", key=f"del_{c['id']}"):
                delete_client(c["id"])
                clear_cache()
                st.rerun()

    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Pagina: Lead detail
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.page == "detail" and st.session_state.selected_lead_id:
    lead, history = get_lead(st.session_state.selected_lead_id)

    if not lead:
        st.error("Lead niet gevonden.")
        st.session_state.page = "leads"
        st.rerun()

    if st.button("← Terug"):
        st.session_state.page = "leads"
        st.rerun()

    form_data = json.loads(lead["form_data"] or "{}")

    col_left, col_right = st.columns([3, 2], gap="large")

    with col_left:
        st.markdown(f"## {lead['full_name'] or 'Onbekende naam'}")
        if lead["client_name"]:
            st.caption(f"Client: {lead['client_name']}")

        st.markdown(f"**Status:** {BADGE_EMOJI.get(lead['status'], '')} {lead['status']}")

        with st.container(border=True):
            cols = st.columns(2)
            cols[0].markdown(f"**Aangemaakt**  \n{fmt_dt(lead['created_time'])}")
            cols[1].markdown(f"**Laatste update**  \n{fmt_dt(lead['status_updated_at'])}")

            if lead["email"]:
                st.markdown(f"**E-mail**  \n[{lead['email']}](mailto:{lead['email']})")
            else:
                st.markdown("**E-mail**  \n—")

            st.markdown(f"**Telefoon**  \n{lead['phone'] or '—'}")

            for key, val in form_data.items():
                st.markdown(f"**{key}**  \n{val}")

        st.subheader("📝 Aantekeningen")
        notes_val = st.text_area("", value=lead["notes"] or "", height=150, label_visibility="collapsed")
        if st.button("💾 Opslaan", type="primary"):
            update_notes(lead["id"], notes_val)
            st.success("Opgeslagen!")
            st.rerun()

    with col_right:
        st.subheader("🏷️ Status wijzigen")
        for s in STATUSES:
            is_current = lead["status"] == s
            label = f"{BADGE_EMOJI.get(s, '')} {s}" + (" ✓" if is_current else "")
            if st.button(label, use_container_width=True,
                         type="primary" if is_current else "secondary",
                         key=f"status_{s}"):
                update_status(lead["id"], s)
                clear_cache()
                st.rerun()

        st.subheader("🕒 Geschiedenis")
        if history:
            for h in history:
                st.markdown(
                    f"{BADGE_EMOJI.get(h['status'], '')} **{h['status']}**  \n"
                    f"<small style='color:gray'>{fmt_dt(h['changed_at'])}</small>",
                    unsafe_allow_html=True,
                )
        else:
            st.caption("Geen geschiedenis beschikbaar.")

    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Pagina: Leadsoverzicht
# ══════════════════════════════════════════════════════════════════════════════
client_id     = st.session_state.active_client_id
active_vacancy = st.session_state.active_vacancy
clients       = cached_clients()

# Titel
if active_vacancy:
    client_name = next((c["name"] for c in clients if c["id"] == client_id), "")
    st.title(f"💼 {active_vacancy}")
    st.caption(f"{client_name}")
elif client_id:
    client_name = next((c["name"] for c in clients if c["id"] == client_id), "Onbekend")
    st.title(f"👤 {client_name}")
else:
    st.title("🌐 Alle clients")

# Statuskaarten
counts = cached_counts(client_id)
total  = sum(counts.values())
cols   = st.columns(len(STATUSES) + 1)
cols[0].metric("Totaal", total)
for i, s in enumerate(STATUSES):
    cols[i + 1].metric(s, counts[s])

st.divider()

# Filters
PERIODE_OPTIES = {
    "Afgelopen 24 uur":  1,
    "Afgelopen 48 uur":  2,
    "Afgelopen 7 dagen": 7,
    "Afgelopen 14 dagen": 14,
    "Afgelopen 31 dagen": 31,
    "Afgelopen 60 dagen": 60,
    "Afgelopen 90 dagen": 90,
    "Alle tijd": None,
}

col_f1, col_f2, col_f3 = st.columns([2, 2, 3])
periode       = col_f1.selectbox("Periode", list(PERIODE_OPTIES.keys()), index=2)
status_filter = col_f2.selectbox("Status", ["Alle"] + STATUSES, index=0)
search        = col_f3.text_input("Zoeken", placeholder="Naam, e-mail of telefoon...")

days = PERIODE_OPTIES[periode]

leads = cached_leads(
    client_id=client_id,
    status_filter=status_filter if status_filter != "Alle" else None,
    search=search or None,
    days=days,
    vacancy_name=active_vacancy,
)

st.caption(f"{len(leads)} leads gevonden")

# Tabel
if not leads:
    st.info("Geen leads gevonden.")
else:
    if not client_id:
        col_sizes = [1.5, 1.5, 2, 2.5, 1.8, 3, 2.5, 0.6]
        headers   = ["Datum", "Pagina", "Naam", "E-mail", "Telefoon", "Antwoorden", "Status", ""]
    else:
        col_sizes = [1.5, 2, 2.5, 1.8, 3, 2.5, 0.6]
        headers   = ["Datum", "Naam", "E-mail", "Telefoon", "Antwoorden", "Status", ""]

    header = st.columns(col_sizes)
    for h_col, h_txt in zip(header, headers):
        h_col.markdown(f"**{h_txt}**")

    st.divider()
    for lead in leads:
        row = st.columns(col_sizes)
        row[0].caption(fmt_dt(lead["created_time"]))
        if not client_id:
            row[1].caption(lead["client_name"] or "—")
            offset = 2
        else:
            offset = 1
        row[offset].markdown(lead["full_name"] or "—")
        if lead["email"]:
            row[offset + 1].markdown(f"[{lead['email']}](mailto:{lead['email']})")
        else:
            row[offset + 1].markdown("—")
        row[offset + 2].markdown(lead["phone"] or "—")

        # Formulier antwoorden
        form_data = json.loads(lead["form_data"] or "{}")
        if form_data:
            antwoorden = "  \n".join(f"**{k}:** {v}" for k, v in form_data.items())
            row[offset + 3].markdown(antwoorden)
        else:
            row[offset + 3].caption("—")

        # Status dropdown
        current_idx = STATUSES.index(lead["status"]) if lead["status"] in STATUSES else 0
        new_status = row[offset + 4].selectbox(
            "",
            options=STATUSES,
            index=current_idx,
            key=f"status_{lead['id']}",
            label_visibility="collapsed",
        )
        if new_status != lead["status"]:
            update_status(lead["id"], new_status)
            clear_cache()
            st.rerun()

        if row[offset + 5].button("→", key=f"open_{lead['id']}"):
            st.session_state.selected_lead_id = lead["id"]
            st.session_state.page = "detail"
            st.rerun()
