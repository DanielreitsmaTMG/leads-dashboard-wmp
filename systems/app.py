import os
import sys
import json
from datetime import datetime, timezone, timedelta

import streamlit as st
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

sys.path.insert(0, os.path.dirname(__file__))
from database import (
    init_db, get_all_clients, add_client, delete_client,
    get_leads, get_lead, get_status_counts, get_leads_today_count,
    update_status, update_notes,
    get_forms_for_client, set_form_active, set_form_vacancy_url, get_form,
    get_vacancies_for_client,
    update_ai_summary, get_stale_leads,
    STATUSES, STATUS_COLORS,
)
from fetch_leads import fetch_all_clients
from ai_assistant import summarize_lead, suggest_vacancy_text


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

@st.cache_data(ttl=60, show_spinner=False)
def cached_today_count(client_id, vacancy_name=None):
    return get_leads_today_count(client_id, vacancy_name)

@st.cache_data(ttl=120, show_spinner=False)
def cached_forms(client_id):
    return get_forms_for_client(client_id)

@st.cache_data(ttl=300, show_spinner=False)
def cached_stale_leads(client_id, hours=24):
    return get_stale_leads(client_id=client_id, status="Instroom", hours=hours)

@st.cache_data(ttl=300, show_spinner=False)
def cached_vacancy_url(form_id):
    if not form_id:
        return None
    f = get_form(form_id)
    return f.get("vacancy_url") if f else None

def clear_cache():
    cached_clients.clear()
    cached_leads.clear()
    cached_counts.clear()
    cached_today_count.clear()
    cached_forms.clear()
    cached_vacancies.clear()
    cached_stale_leads.clear()
    cached_vacancy_url.clear()

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# ── Eenmalig: DB + scheduler ──────────────────────────────────────────────────
# BELANGRIJK (performance): init_db() bevat o.a. UPDATE-migraties die de hele
# leads/status_history-tabel scannen. st.cache_resource zorgt dat dit maar één
# keer per app-proces draait (gedeeld over alle gebruikers/sessies), in plaats
# van bij elke knopklik/rerun opnieuw — dat maakte de tool eerder erg traag.
@st.cache_resource(show_spinner=False)
def _init_db_once():
    init_db()
    return True

try:
    _init_db_once()
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

def _scheduled_sync():
    fetch_all_clients()
    from fetch_leads import backfill_summaries
    backfill_summaries(limit=5)


if "scheduler_started" not in st.session_state:
    scheduler = BackgroundScheduler()
    scheduler.add_job(_scheduled_sync, "interval", minutes=15, id="auto_sync")
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
if "leads_page" not in st.session_state:
    st.session_state.leads_page = 0
if "selected_leads" not in st.session_state:
    st.session_state.selected_leads = set()
if "status_filter_override" not in st.session_state:
    st.session_state.status_filter_override = None
if "open_notes_for" not in st.session_state:
    st.session_state.open_notes_for = None

# Elke dag opnieuw standaard starten in de "Instroom"-fase
_today = datetime.now().date().isoformat()
if st.session_state.get("_filter_reset_date") != _today:
    st.session_state.status_filter_override = "Instroom"
    st.session_state._filter_reset_date = _today

PAGE_SIZE = 25

# ── Kleurmapping → Streamlit badge-kleuren ────────────────────────────────────
BADGE_EMOJI = {
    "Instroom":            "🟡",
    "Nog geen contact":    "🟠",
    "Gesproken":           "🔵",
    "Komt op gesprek":     "🟣",
    "Voorstel gedaan":     "🟤",
    "Geplaatst bij klant": "🟢",
    "Afgewezen":           "⚫",
}

# Hex-kleuren per fase — gebruikt voor accentbalken/stippen in de UI, zodat de
# pijplijn ook zonder emoji's in één oogopslag herkenbaar is.
STATUS_HEX = {
    "Instroom":            "#FFD60A",
    "Nog geen contact":    "#FF9F0A",
    "Gesproken":           "#0A84FF",
    "Komt op gesprek":     "#BF5AF2",
    "Voorstel gedaan":     "#A2845E",
    "Geplaatst bij klant": "#30D158",
    "Afgewezen":           "#8E8E93",
}


def fmt_dt(value):
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%d-%m-%Y %H:%M")
    except Exception:
        return value


def rel_time(value):
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = datetime.now(timezone.utc) - dt
        s = diff.total_seconds()
        if s < 60:       return "Zojuist"
        if s < 3600:     return f"{int(s/60)}m geleden"
        if s < 86400:    return f"{int(s/3600)}u geleden"
        if s < 172800:   return "Gisteren"
        if s < 604800:   return f"{int(s/86400)} dagen geleden"
        return dt.strftime("%d-%m-%Y")
    except Exception:
        return str(value)


def is_new(value):
    """True als lead minder dan 24 uur oud is."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() < 86400
    except Exception:
        return False


# ── Sidebar ───────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Leads Dashboard", page_icon="⚡", layout="wide")

# ── Apple-achtige styling: rustige fonts, ronde hoeken, subtiele schaduwen ───
st.markdown("""
<style>
/* Algemeen lettertype */
html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text",
                 "Segoe UI", Roboto, Helvetica, Arial, sans-serif !important;
}

/* Meer ademruimte rond de hoofdcontainer */
.block-container {
    padding-top: 2rem;
    padding-bottom: 3rem;
}

/* Knoppen: rond, zachte schaduw, subtiele hover */
.stButton > button {
    border-radius: 12px;
    border: 1px solid rgba(0,0,0,0.06);
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
    transition: all 0.15s ease-in-out;
    font-weight: 500;
}
.stButton > button:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 10px rgba(0,0,0,0.08);
    border-color: rgba(0,0,0,0.12);
}
.stButton > button[kind="primary"] {
    box-shadow: 0 2px 8px rgba(0, 122, 255, 0.25);
}

/* Inputvelden en selectboxen */
.stTextInput > div > div, .stSelectbox > div > div, .stMultiSelect > div > div {
    border-radius: 10px !important;
}

/* Containers met rand → kaartjes met zachte schaduw en ronde hoeken */
div[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 16px !important;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
}

/* Sidebar: lichte achtergrond en strakke afscheiding */
section[data-testid="stSidebar"] {
    border-right: 1px solid rgba(0,0,0,0.06);
}
section[data-testid="stSidebar"] .stButton > button {
    text-align: left;
    justify-content: flex-start;
}

/* Dividers iets subtieler */
hr {
    margin: 0.6rem 0;
    opacity: 0.15;
}

/* Contact-iconen (e-mail/telefoon) in de leadstabel: klein en uitgelijnd */
.contact-icon {
    font-size: 0.8rem;
    vertical-align: middle;
    display: inline-block;
    width: 1.1em;
}

/* Klant-logo in de sidebar */
.client-logo, .client-avatar {
    width: 32px;
    height: 32px;
    flex-shrink: 0;
}
.client-logo {
    border-radius: 8px;
    object-fit: cover;
    display: block;
}

/* Klant-avatar (initialen of icoon) als fallback wanneer er geen logo bekend is */
.client-avatar {
    border-radius: 8px;
    background: linear-gradient(135deg, #8e8e93, #636366);
    color: white;
    font-size: 0.85rem;
    font-weight: 600;
    display: flex;
    align-items: center;
    justify-content: center;
}

/* Klantrij (logo + naam) als één samenhangend, klikbaar geheel: gebruikt
   st.container(border=True), zodat logo + knop binnen dezelfde "doos"
   vallen i.p.v. los naast elkaar te staan. */
section[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 10px;
    margin-bottom: 4px;
    transition: background 0.15s ease-in-out, border-color 0.15s ease-in-out;
}
section[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"]:hover {
    background: rgba(0,0,0,0.04);
}
section[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"]:has(.active-row-marker) {
    background: rgba(10,132,255,0.1);
    border-color: rgba(10,132,255,0.35);
}
section[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] [data-testid="stHorizontalBlock"] {
    align-items: center;
    gap: 0.6rem;
}
section[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] [data-testid="stColumn"]:first-child {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-grow: 0;
    width: 32px;
    min-width: 32px;
}
section[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] [data-testid="stColumn"]:nth-child(2) {
    overflow: hidden;
}
section[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] .stButton > button {
    border: none;
    box-shadow: none;
    background: transparent;
    text-align: left;
    justify-content: flex-start;
    padding: 0.4rem 0.5rem;
    font-weight: 500;
    height: 38px;
    width: 100%;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    display: block;
}
section[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] .stButton > button p {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
section[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] .stButton > button:hover {
    background: transparent;
    transform: none;
    box-shadow: none;
    text-decoration: underline;
}

/* "Alle klanten"-knop: volle breedte, geen apart icoon. Actieve primaire
   sidebarknoppen krijgen een subtiele blauwe pil i.p.v. het felrode thema. */
section[data-testid="stSidebar"] .stButton > button[kind="primary"] {
    background: rgba(10,132,255,0.12) !important;
    color: #0A84FF !important;
    border: none !important;
    box-shadow: none !important;
    font-weight: 700;
}

/* Labels van inputs/selects iets rustiger en kleiner */
[data-testid="stWidgetLabel"] p {
    font-size: 0.8rem;
    color: #6e6e73;
    font-weight: 500;
}

/* ── Fasekaarten: groot getal, kleinere fase-naam, kleuraccent onderaan ────── */
.fase-anchor + div[data-testid="stHorizontalBlock"] .stButton > button {
    white-space: pre-line;
    line-height: 1.35;
    border-bottom-width: 4px;
    border-bottom-style: solid;
    padding-top: 0.8rem;
    padding-bottom: 0.6rem;
}
.fase-anchor + div[data-testid="stHorizontalBlock"] .stButton > button p {
    white-space: pre-line;
}
.fase-anchor + div[data-testid="stHorizontalBlock"] .stButton > button p::first-line {
    font-size: 1.5rem;
    font-weight: 700;
    letter-spacing: -0.02em;
}
.fase-anchor + div[data-testid="stHorizontalBlock"] .stButton > button[kind="primary"] {
    border-bottom-width: 6px;
}
</style>
""", unsafe_allow_html=True)

# Per-fase kleuraccent op de fasekaarten (onderrand). De volgorde komt overeen
# met STATUSES, dus nth-child(N) == fase N in de rij.
_fase_card_css = "\n".join(
    f'.fase-anchor + div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-child({i+1}) .stButton > button {{ border-bottom-color: {STATUS_HEX.get(s, "#8E8E93")}; }}'
    for i, s in enumerate(STATUSES)
)
st.markdown(f"<style>{_fase_card_css}</style>", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("## ⚡ Leads Dashboard")
    st.divider()

    st.markdown("**Clients**")
    clients = cached_clients()

    _all_active = st.session_state.active_client_id is None and st.session_state.page == "leads"
    if st.button("Alle klanten", use_container_width=True,
                 type="primary" if _all_active else "secondary",
                 key="client_all"):
        st.session_state.active_client_id = None
        st.session_state.active_vacancy = None
        st.session_state.page = "leads"
        st.rerun()

    for c in clients:
        is_active_client = st.session_state.active_client_id == c["id"] and st.session_state.page == "leads"
        is_active_no_vacancy = is_active_client and st.session_state.active_vacancy is None

        marker = '<span class="active-row-marker"></span>' if is_active_no_vacancy else ""
        with st.container(border=True):
            col_logo, col_btn = st.columns([1, 5], gap="small", vertical_alignment="center")
            if c.get("logo_url"):
                col_logo.markdown(
                    f'<img src="{c["logo_url"]}" class="client-logo">{marker}',
                    unsafe_allow_html=True,
                )
            else:
                initials = "".join(w[0] for w in c["name"].split()[:2]).upper() or "?"
                col_logo.markdown(f'<div class="client-avatar">{initials}</div>{marker}', unsafe_allow_html=True)

            if col_btn.button(c["name"], use_container_width=True,
                         type="primary" if is_active_no_vacancy else "secondary",
                         key=f"client_{c['id']}"):
                st.session_state.active_client_id = c["id"]
                st.session_state.active_vacancy = None
                st.session_state.page = "leads"
                st.rerun()

    st.divider()
    st.markdown(
        "<p style='font-size:0.75rem; color:#8e8e93; font-weight:600; "
        "text-transform:uppercase; letter-spacing:0.04em; margin-bottom:0.4rem;'>Tools</p>",
        unsafe_allow_html=True,
    )

    if st.button("🔄 Vernieuwen", use_container_width=True):
        with st.spinner("Leads ophalen..."):
            n, log = fetch_all_clients()
            from fetch_leads import backfill_summaries
            backfill_summaries(limit=5)
            clear_cache()
            for line in log:
                if "⚠️" in line:
                    st.warning(line)
                else:
                    st.success(line)
            st.rerun()

    if st.button("✨ Vacature maker", use_container_width=True,
                 type="primary" if st.session_state.page == "vacature_maker" else "secondary"):
        st.session_state.page = "vacature_maker"
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
                st.caption("Zet formulieren aan of uit, en koppel optioneel een vacaturelink (zodat de AI-assistent de eisen uit de vacaturetekst kan vergelijken met de antwoorden van de kandidaat):")
                for f in forms:
                    fcol1, fcol2 = st.columns([1, 2])
                    new_val = fcol1.toggle(
                        f["form_name"] or f["form_id"],
                        value=bool(f["active"]),
                        key=f"form_{f['form_id']}",
                    )
                    if new_val != bool(f["active"]):
                        set_form_active(f["form_id"], new_val)
                        clear_cache()
                        st.rerun()

                    url_val = fcol2.text_input(
                        "Vacaturelink",
                        value=f.get("vacancy_url") or "",
                        key=f"vacurl_{f['form_id']}",
                        placeholder="https://werkenmetpassie.nl/vacatures/onderhoudsschilder",
                        label_visibility="collapsed",
                    )
                    if url_val != (f.get("vacancy_url") or ""):
                        set_form_vacancy_url(f["form_id"], url_val.strip())
                        clear_cache()
                        st.success("Vacaturelink opgeslagen.")
                        st.rerun()
            else:
                st.caption("Nog geen formulieren — klik op 🔄 Vernieuwen om ze op te halen.")
            if st.button("🗑️ Client verwijderen", key=f"del_{c['id']}"):
                delete_client(c["id"])
                clear_cache()
                st.rerun()

    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Pagina: Vacature maker
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.page == "vacature_maker":
    st.title("✨ Vacature maker")
    st.caption("Genereer een conceptvacaturetekst op basis van een korte briefing — handig als startpunt, controleer en pas aan voor publicatie.")

    with st.form("vacancy_text_form"):
        v_titel = st.text_input("Functietitel", placeholder="bijv. Onderhoudsschilder")
        v_punten = st.text_area(
            "Kernpunten (eisen, aanbod, bijzonderheden — los meegeven, AI verwerkt het tot lopende tekst)",
            placeholder="bijv. ervaring met buitenschilderwerk, rijbewijs B, fulltime, marktconform salaris, doorgroeimogelijkheden...",
            height=120,
        )
        v_toon = st.selectbox("Toon", ["professioneel en uitnodigend", "informeel en enthousiast", "zakelijk en to-the-point"])
        v_submit = st.form_submit_button("✨ Genereer concept", type="primary")

    if v_submit:
        if not v_titel.strip():
            st.warning("Vul minimaal een functietitel in.")
        else:
            with st.spinner("Concepttekst wordt geschreven..."):
                tekst = suggest_vacancy_text(v_titel, v_punten or "(geen aanvullende punten meegegeven)", v_toon)
            if tekst is None:
                st.warning("ANTHROPIC_API_KEY ontbreekt — voeg deze toe aan de Streamlit secrets om deze functie te gebruiken.")
            else:
                st.session_state["_vacancy_draft"] = tekst

    if st.session_state.get("_vacancy_draft"):
        st.divider()
        st.subheader("📄 Conceptvacaturetekst")
        st.text_area("Kopieer en pas aan waar nodig",
                     value=st.session_state["_vacancy_draft"], height=400, key="_vacancy_draft_view")

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

    st.markdown(f"## {lead['full_name'] or 'Onbekende naam'}")
    if lead["client_name"]:
        st.caption(f"Client: {lead['client_name']}")
    if lead["vacancy_name"]:
        st.caption(f"💼 Gesolliciteerd op: {lead['vacancy_name']}")

    # ── Pijplijn-stepper: visueel overzicht van de huidige fase ─────────────────
    _cur_idx = STATUSES.index(lead["status"]) if lead["status"] in STATUSES else 0
    _step_html = ['<div style="display:flex; align-items:flex-start; margin: 0.75rem 0 1.25rem 0;">']
    for idx, s in enumerate(STATUSES):
        color = STATUS_HEX.get(s, "#8E8E93")
        if idx < _cur_idx:
            dot_style = f"background:{color}; color:white;"
            dot_content = "✓"
        elif idx == _cur_idx:
            dot_style = f"background:{color}; color:white; box-shadow: 0 0 0 4px {color}33;"
            dot_content = "●"
        else:
            dot_style = "background:#E5E5EA; color:#8E8E93;"
            dot_content = ""
        line_color = color if idx < _cur_idx else "#E5E5EA"
        _step_html.append('<div style="flex:1; text-align:center; position:relative;">')
        if idx > 0:
            _step_html.append(
                f'<div style="position:absolute; top:11px; left:-50%; width:100%; height:2px; '
                f'background:{line_color}; z-index:0;"></div>'
            )
        _step_html.append(
            f'<div style="position:relative; z-index:1; width:24px; height:24px; border-radius:50%; '
            f'{dot_style} display:flex; align-items:center; justify-content:center; '
            f'font-size:0.7rem; font-weight:700; margin:0 auto;">{dot_content}</div>'
        )
        weight = "700" if idx == _cur_idx else "400"
        _step_html.append(
            f'<div style="font-size:0.7rem; margin-top:6px; font-weight:{weight}; '
            f'color:{"#1d1d1f" if idx == _cur_idx else "#8e8e93"};">{s}</div>'
        )
        _step_html.append('</div>')
    _step_html.append('</div>')
    st.markdown("".join(_step_html), unsafe_allow_html=True)

    col_left, col_right = st.columns([3, 2], gap="large")

    with col_left:
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

        st.subheader("🤖 AI-samenvatting")
        if lead["ai_summary"]:
            st.info(lead["ai_summary"])
            st.caption(f"Gegenereerd: {fmt_dt(lead['ai_summary_at'])}")
            btn_label = "🔄 Opnieuw genereren"
        else:
            st.caption("Nog geen samenvatting gegenereerd voor deze lead.")
            btn_label = "✨ Genereer samenvatting"
        if st.button(btn_label, key="gen_summary"):
            with st.spinner("Bezig met analyseren..."):
                summary = summarize_lead(lead["full_name"], lead["vacancy_name"], form_data, lead["client_name"], cached_vacancy_url(lead.get("form_id")))
            if summary is None:
                st.warning("ANTHROPIC_API_KEY ontbreekt — voeg deze toe aan de secrets om deze functie te gebruiken.")
            else:
                update_ai_summary(lead["id"], summary)
                clear_cache()
                st.rerun()

        st.subheader("📝 Aantekeningen")
        notes_val = st.text_area("", value=lead["notes"] or "", height=150, label_visibility="collapsed")
        if st.button("💾 Opslaan", type="primary"):
            update_notes(lead["id"], notes_val)
            st.success("Opgeslagen!")
            st.rerun()

    with col_right:
        st.subheader("🏷️ Fase wijzigen")
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
            timeline_html = ['<div style="margin-top:0.25rem;">']
            for idx, h in enumerate(history):
                color = STATUS_HEX.get(h["status"], "#8E8E93")
                is_last = idx == len(history) - 1
                timeline_html.append(
                    '<div style="display:flex; gap:10px; position:relative; padding-bottom:16px;">'
                )
                if not is_last:
                    timeline_html.append(
                        '<div style="position:absolute; left:5px; top:16px; bottom:0; '
                        'width:2px; background:rgba(0,0,0,0.08);"></div>'
                    )
                timeline_html.append(
                    f'<div style="width:12px; height:12px; min-width:12px; border-radius:50%; '
                    f'background:{color}; margin-top:4px; z-index:1;"></div>'
                )
                timeline_html.append(
                    f'<div><strong>{h["status"]}</strong><br>'
                    f'<small style="color:gray">{fmt_dt(h["changed_at"])}</small></div>'
                )
                timeline_html.append('</div>')
            timeline_html.append('</div>')
            st.markdown("".join(timeline_html), unsafe_allow_html=True)
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

# ── Korte dagsamenvatting ─────────────────────────────────────────────────────
counts = cached_counts(client_id)
_n_today = cached_today_count(client_id, active_vacancy)
st.caption(f"📅 **{_n_today}** nieuwe lead(s) vandaag binnengekomen · **{counts.get('Instroom', 0)}** wachten in Instroom")

# ── Follow-up signalering: leads die te lang wachten op opvolging ────────────
# Tijdelijk uitgeschakeld op verzoek (te dominant nu de "Instroom"-fase elke dag
# default is en standaard veel leads bevat). Logica blijft staan voor later.
SHOW_STALE_BANNER = False
if SHOW_STALE_BANNER:
    stale = cached_stale_leads(client_id, hours=24)
    if stale:
        names = ", ".join(
            f"{(s['full_name'] or 'Onbekend')} ({s['client_name']})" if not client_id else (s['full_name'] or 'Onbekend')
            for s in stale[:8]
        )
        extra = f" en {len(stale) - 8} meer" if len(stale) > 8 else ""
        st.warning(
            f"⏰ **{len(stale)} lead(s)** staan al langer dan 24 uur op 'Instroom' zonder opvolging: "
            f"{names}{extra}. Geef ze voorrang!"
        )

# ── Sectie 1: fasekaarten (klikbaar als filter op de tabel hieronder) ────────
st.markdown('<div class="fase-anchor"></div>', unsafe_allow_html=True)
cols = st.columns(len(STATUSES))

for i, s in enumerate(STATUSES):
    emoji = BADGE_EMOJI.get(s, "")
    if cols[i].button(f"{counts[s]}\n{emoji} {s}", use_container_width=True, key=f"f_{s}",
                      type="primary" if st.session_state.status_filter_override == s else "secondary"):
        st.session_state.status_filter_override = s
        st.session_state.leads_page = 0
        st.rerun()

st.divider()

# ── Filters ───────────────────────────────────────────────────────────────────
PERIODE_OPTIES = {
    "Afgelopen 24 uur": 1, "Afgelopen 48 uur": 2, "Afgelopen 7 dagen": 7,
    "Afgelopen 14 dagen": 14, "Afgelopen 31 dagen": 31,
    "Afgelopen 60 dagen": 60, "Afgelopen 90 dagen": 90, "Alle tijd": None,
}
SORTEER_OPTIES = {
    "Datum (nieuw→oud)": ("created_time", True),
    "Datum (oud→nieuw)": ("created_time", False),
    "Naam (A→Z)":        ("full_name", False),
    "Status":            ("status", False),
}

col_f1, col_f2, col_f3, col_f4 = st.columns([2, 2, 2, 3])
periode       = col_f1.selectbox("Periode", list(PERIODE_OPTIES.keys()), index=2)
default_status_idx = (["Alle"] + STATUSES).index(st.session_state.status_filter_override) \
    if st.session_state.status_filter_override in STATUSES else 0
status_filter = col_f2.selectbox("Status", ["Alle"] + STATUSES, index=default_status_idx, key="status_filter_select")
sorteer       = col_f3.selectbox("Sorteren", list(SORTEER_OPTIES.keys()), index=0)
search        = col_f4.text_input("Zoeken", placeholder="Naam, e-mail of telefoon...")

# Sync override met selectbox
if status_filter != (st.session_state.status_filter_override or "Alle"):
    st.session_state.status_filter_override = status_filter if status_filter != "Alle" else None

days = PERIODE_OPTIES[periode]
sort_key, sort_rev = SORTEER_OPTIES[sorteer]

all_leads = cached_leads(
    client_id=client_id,
    status_filter=st.session_state.status_filter_override,
    search=search or None,
    days=days,
    vacancy_name=active_vacancy,
)

# Sorteren
def sort_val(lead):
    v = lead.get(sort_key) or ""
    return str(v).lower()

all_leads = sorted(all_leads, key=sort_val, reverse=sort_rev)

# ── Bulk actie ────────────────────────────────────────────────────────────────
if st.session_state.selected_leads:
    n_sel = len(st.session_state.selected_leads)
    bc1, bc2, bc3 = st.columns([3, 2, 1])
    bc1.info(f"**{n_sel} lead{'s' if n_sel > 1 else ''}** geselecteerd")
    bulk_status = bc2.selectbox("Status instellen", STATUSES, key="bulk_status_select", label_visibility="collapsed")
    if bc3.button("✅ Toepassen", type="primary"):
        for lid in st.session_state.selected_leads:
            update_status(lid, bulk_status)
        st.session_state.selected_leads = set()
        clear_cache()
        st.rerun()

# ── Hulpfunctie: AI-samenvatting tonen ───────────────────────────────────────
# Let op: genereert NIET live tijdens het laden van het overzicht — dat maakte de
# pagina erg traag (elke ontbrekende samenvatting = een blokkerende API-call,
# en met de Anthropic-rate-limit van 5/min liep dit al snel op tot minuten).
# Samenvattingen worden nu gegenereerd:
#  1. tijdens de GitHub Actions sync voor nieuwe leads (fetch_leads.py), en
#  2. als achtergrond-backfill voor oudere leads zonder samenvatting (zie
#     backfill_summaries() in fetch_leads.py, ook tijdens de sync).
# Hier tonen we gewoon wat er al in de database staat.
def render_summary(lead, container=None):
    target = container or st
    if lead["ai_summary"]:
        target.markdown(lead["ai_summary"])
    else:
        target.caption("⏳ samenvatting volgt")


# ── Hulpfunctie: aantekening-editor ──────────────────────────────────────────
def render_notes_editor(lead):
    with st.container(border=True):
        st.markdown(f"**📝 Aantekening — {lead['full_name'] or 'Lead'}**")
        new_note = st.text_area("", value=lead["notes"] or "", key=f"note_area_{lead['id']}",
                                label_visibility="collapsed", height=100)
        ncol1, ncol2 = st.columns([1, 5])
        if ncol1.button("💾 Opslaan", key=f"save_note_{lead['id']}"):
            update_notes(lead["id"], new_note)
            clear_cache()
            st.session_state.open_notes_for = None
            st.success("Opgeslagen!")
            st.rerun()
        if ncol2.button("Sluiten", key=f"close_note_{lead['id']}"):
            st.session_state.open_notes_for = None
            st.rerun()


# ── Sectie 2: kandidatentabel (gefilterd door de fasekaart hierboven) ───────
# ── Paginering ────────────────────────────────────────────────────────────────
total_leads = len(all_leads)
total_pages = max(1, (total_leads + PAGE_SIZE - 1) // PAGE_SIZE)
cur_page    = min(st.session_state.leads_page, total_pages - 1)
leads       = all_leads[cur_page * PAGE_SIZE:(cur_page + 1) * PAGE_SIZE]

pg_col1, pg_col2, pg_col3 = st.columns([1, 3, 1])
pg_col2.caption(f"{total_leads} leads · pagina {cur_page + 1} van {total_pages}")
if pg_col1.button("‹", disabled=cur_page == 0, help="Vorige pagina"):
    st.session_state.leads_page = cur_page - 1
    st.rerun()
if pg_col3.button("›", disabled=cur_page >= total_pages - 1, help="Volgende pagina"):
    st.session_state.leads_page = cur_page + 1
    st.rerun()

# ── Tabel ─────────────────────────────────────────────────────────────────────
if not all_leads:
    _empty_messages = {
        "Instroom":            "✅ Even niets nieuws — Instroom is leeg. Mooi rustig!",
        "Nog geen contact":    "👍 Niemand wacht nog op een eerste contactmoment.",
        "Gesproken":           "Geen leads in deze fase.",
        "Komt op gesprek":     "Geen leads in deze fase.",
        "Voorstel gedaan":     "Geen leads in deze fase.",
        "Geplaatst bij klant": "Geen leads in deze fase.",
        "Afgewezen":           "🎉 Geen afgewezen leads in deze selectie.",
    }
    _filter = st.session_state.status_filter_override
    if _filter in _empty_messages:
        if _filter in ("Instroom", "Afgewezen", "Nog geen contact"):
            st.success(_empty_messages[_filter])
        else:
            st.info(_empty_messages[_filter])
    else:
        st.info("Geen leads gevonden voor deze selectie.")
else:
    show_page_col = not client_id
    if show_page_col:
        col_sizes = [0.4, 1.3, 1.7, 2.6, 1.6, 3.4, 2, 0.5, 0.5]
        headers   = ["", "Tijd", "Naam", "Gesolliciteerd op", "Contact", "Samenvatting", "Status", "", ""]
    else:
        col_sizes = [0.4, 1.3, 1.7, 2.1, 1.6, 3.4, 2, 0.5, 0.5]
        headers   = ["", "Tijd", "Naam", "Gesolliciteerd op", "Contact", "Samenvatting", "Status", "", ""]

    hdr = st.columns(col_sizes)
    for h_col, h_txt in zip(hdr, headers):
        h_col.markdown(f"**{h_txt}**")
    st.divider()

    # Per-rij stylesheet: kleuraccent (linkerrand) o.b.v. fase + compactere
    # actieknoppen + subtiele hover, via "anchor + adjacent sibling" CSS-trucje.
    _row_css = []
    for lead in leads:
        color = STATUS_HEX.get(lead["status"], "#8E8E93")
        _row_css.append(f"""
.row-anchor-{lead['id']} + div[data-testid="stHorizontalBlock"] {{
    border-left: 4px solid {color};
    padding: 0.35rem 0 0.35rem 0.6rem;
    border-radius: 8px;
    transition: background 0.15s ease-in-out;
}}
.row-anchor-{lead['id']} + div[data-testid="stHorizontalBlock"]:hover {{
    background: rgba(0,0,0,0.025);
}}
.row-anchor-{lead['id']} + div[data-testid="stHorizontalBlock"] .stButton > button {{
    padding: 0.2rem 0.5rem;
    font-size: 0.85rem;
    box-shadow: none;
    border: none;
    background: transparent;
}}
.row-anchor-{lead['id']} + div[data-testid="stHorizontalBlock"] .stButton > button:hover {{
    background: rgba(0,0,0,0.05);
    transform: none;
}}
""")
    st.markdown(f"<style>{''.join(_row_css)}</style>", unsafe_allow_html=True)

    for lead in leads:
        new_badge = " 🆕" if is_new(lead["created_time"]) else ""
        st.markdown(f'<div class="row-anchor-{lead["id"]}"></div>', unsafe_allow_html=True)
        row = st.columns(col_sizes)
        i = 0

        # Checkbox voor bulk
        checked = lead["id"] in st.session_state.selected_leads
        if row[i].checkbox("", value=checked, key=f"chk_{lead['id']}", label_visibility="collapsed"):
            st.session_state.selected_leads.add(lead["id"])
        else:
            st.session_state.selected_leads.discard(lead["id"])
        i += 1

        # Moment van reageren (relatief)
        row[i].caption(rel_time(lead["created_time"]))
        i += 1

        # Naam + nieuw-indicator
        row[i].markdown(f"{lead['full_name'] or '—'}{new_badge}")
        i += 1

        # Waarop gesolliciteerd: leadformulier/vacature + pagina (klant)
        vacature_label = lead["vacancy_name"] or lead["form_name"] or "—"
        if show_page_col:
            row[i].markdown(f"💼 {vacature_label}  \n🏢 {lead['client_name'] or '—'}")
        else:
            row[i].markdown(f"💼 {vacature_label}")
        i += 1

        # Contact: e-mail + telefoon samengevoegd in één compacte kolom.
        # unsafe_allow_html zodat de iconen als kleine, uitgelijnde <span>
        # getoond worden i.p.v. als oversized emoji-glyphs.
        contact_lines = []
        if lead["email"]:
            contact_lines.append(
                f'<span class="contact-icon">✉️</span> '
                f'<a href="mailto:{lead["email"]}">{lead["email"]}</a>'
            )
        if lead["phone"]:
            contact_lines.append(
                f'<span class="contact-icon">📞</span> '
                f'<a href="tel:{lead["phone"]}">{lead["phone"]}</a>'
            )
        row[i].markdown(
            "<br>".join(contact_lines) if contact_lines else "—",
            unsafe_allow_html=True,
        )
        i += 1

        # AI-samenvatting i.p.v. ruwe formulierantwoorden — direct tonen, automatisch genereren indien nodig
        render_summary(lead, row[i])
        i += 1

        # Status / fase dropdown
        current_idx = STATUSES.index(lead["status"]) if lead["status"] in STATUSES else 0
        new_status = row[i].selectbox("", STATUSES, index=current_idx,
                                      key=f"status_{lead['id']}", label_visibility="collapsed")
        if new_status != lead["status"]:
            update_status(lead["id"], new_status)
            clear_cache()
            st.rerun()
        i += 1

        # Aantekening knop
        note_icon = "📝" if lead["notes"] else "🗒️"
        if row[i].button(note_icon, key=f"note_btn_{lead['id']}", help="Aantekening toevoegen/bewerken"):
            if st.session_state.open_notes_for == lead["id"]:
                st.session_state.open_notes_for = None
            else:
                st.session_state.open_notes_for = lead["id"]
            st.rerun()
        i += 1

        # Detail knop (volledige kaart)
        if row[i].button("→", key=f"open_{lead['id']}", help="Volledige kaart van kandidaat"):
            st.session_state.selected_lead_id = lead["id"]
            st.session_state.page = "detail"
            st.rerun()

        # Inline aantekening direct onder de rij van de lead
        if st.session_state.open_notes_for == lead["id"]:
            render_notes_editor(lead)

    # Paginering onderaan
    st.divider()
    pg2_col1, pg2_col2, pg2_col3 = st.columns([1, 3, 1])
    pg2_col2.caption(f"Pagina {cur_page + 1} van {total_pages}")
    if pg2_col1.button("← Vorige ", disabled=cur_page == 0, key="prev2"):
        st.session_state.leads_page = cur_page - 1
        st.rerun()
    if pg2_col3.button("Volgende → ", disabled=cur_page >= total_pages - 1, key="next2"):
        st.session_state.leads_page = cur_page + 1
        st.rerun()
