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
    STATUSES, STATUS_COLORS,
)
from fetch_leads import fetch_all_clients

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# ── Eenmalig: DB + scheduler ──────────────────────────────────────────────────
init_db()

if "scheduler_started" not in st.session_state:
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_all_clients, "interval", minutes=15, id="auto_sync")
    scheduler.start()
    st.session_state.scheduler_started = True

# ── Sessiestaat ───────────────────────────────────────────────────────────────
if "page" not in st.session_state:
    st.session_state.page = "leads"          # "leads" | "detail" | "settings"
if "active_client_id" not in st.session_state:
    st.session_state.active_client_id = None  # None = alle clients
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
    clients = get_all_clients()

    if st.button("🌐 Alle clients", use_container_width=True,
                 type="primary" if st.session_state.active_client_id is None and st.session_state.page == "leads" else "secondary"):
        st.session_state.active_client_id = None
        st.session_state.page = "leads"
        st.rerun()

    for c in clients:
        is_active = st.session_state.active_client_id == c["id"] and st.session_state.page == "leads"
        if st.button(f"👤 {c['name']}", use_container_width=True,
                     type="primary" if is_active else "secondary",
                     key=f"client_{c['id']}"):
            st.session_state.active_client_id = c["id"]
            st.session_state.page = "leads"
            st.rerun()

    st.divider()

    if st.button("🔄 Vernieuwen", use_container_width=True):
        with st.spinner("Leads ophalen..."):
            try:
                n = fetch_all_clients()
                st.success(f"{n} nieuwe leads opgehaald")
                st.rerun()
            except Exception as e:
                st.error(str(e))

    if st.button("⚙️ Instellingen", use_container_width=True,
                 type="primary" if st.session_state.page == "settings" else "secondary"):
        st.session_state.page = "settings"
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# Pagina: Instellingen
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.page == "settings":
    st.title("⚙️ Instellingen")

    col1, col2 = st.columns([1, 1], gap="large")

    with col1:
        st.subheader("Client toevoegen")
        with st.form("add_client_form"):
            name    = st.text_input("Naam client", placeholder="bijv. Werken met Passie")
            page_id = st.text_input("Facebook Page ID", placeholder="bijv. 123456789012345")
            if st.form_submit_button("➕ Toevoegen", type="primary"):
                if name and page_id:
                    add_client(name.strip(), page_id.strip())
                    st.success(f"'{name}' toegevoegd!")
                    st.rerun()
                else:
                    st.warning("Vul naam én Page ID in.")

    with col2:
        st.subheader("Actieve clients")
        clients = get_all_clients()
        if not clients:
            st.info("Nog geen clients toegevoegd.")
        for c in clients:
            with st.container(border=True):
                col_a, col_b = st.columns([3, 1])
                col_a.markdown(f"**{c['name']}**  \n`Page ID: {c['page_id']}`")
                if col_b.button("🗑️", key=f"del_{c['id']}", help="Verwijder client"):
                    delete_client(c["id"])
                    st.rerun()

    st.divider()
    st.subheader("🔑 API Token")
    st.info(
        "Zet je Meta Access Token in het `.env` bestand naast dit project:\n\n"
        "```\nMETA_ACCESS_TOKEN=jouw_token_hier\n```\n\n"
        "Instructies: zie `blueprints/meta_token.md`"
    )
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
client_id = st.session_state.active_client_id
clients   = get_all_clients()

# Titel
if client_id:
    client_name = next((c["name"] for c in clients if c["id"] == client_id), "Onbekend")
    st.title(f"👤 {client_name}")
else:
    st.title("🌐 Alle clients")

# Statuskaarten
counts = get_status_counts(client_id)
total  = sum(counts.values())
cols   = st.columns(len(STATUSES) + 1)
cols[0].metric("Totaal", total)
for i, s in enumerate(STATUSES):
    cols[i + 1].metric(s, counts[s])

st.divider()

# Filters
col_f1, col_f2 = st.columns([2, 3])
status_filter = col_f1.selectbox("Filter op status", ["Alle"] + STATUSES, index=0)
search        = col_f2.text_input("Zoeken", placeholder="Naam, e-mail of telefoon...")

leads = get_leads(
    client_id=client_id,
    status_filter=status_filter if status_filter != "Alle" else None,
    search=search or None,
)

st.caption(f"{len(leads)} leads gevonden")

# Tabel
if not leads:
    st.info("Geen leads gevonden.")
else:
    header = st.columns([1.5, 2, 2.5, 1.8, 2, 1.5, 0.6])
    headers = ["Datum", "Naam", "E-mail", "Telefoon", "Status", "Laatste update", ""]
    for h_col, h_txt in zip(header, headers):
        h_col.markdown(f"**{h_txt}**")

    st.divider()
    for lead in leads:
        row = st.columns([1.5, 2, 2.5, 1.8, 2, 1.5, 0.6])
        row[0].caption(fmt_dt(lead["created_time"]))
        row[1].markdown(lead["full_name"] or "—")
        if lead["email"]:
            row[2].markdown(f"[{lead['email']}](mailto:{lead['email']})")
        else:
            row[2].markdown("—")
        row[3].markdown(lead["phone"] or "—")
        row[4].markdown(f"{BADGE_EMOJI.get(lead['status'], '')} {lead['status']}")
        row[5].caption(fmt_dt(lead["status_updated_at"]))
        if row[6].button("→", key=f"open_{lead['id']}"):
            st.session_state.selected_lead_id = lead["id"]
            st.session_state.page = "detail"
            st.rerun()
