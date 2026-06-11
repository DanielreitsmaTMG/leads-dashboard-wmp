import os
import json
from datetime import datetime
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

try:
    import streamlit as st
    _HAS_ST = True
except Exception:
    _HAS_ST = False

STATUSES = [
    "Instroom",
    "Nog geen contact",
    "Gesproken",
    "Komt op gesprek",
    "Voorstel gedaan",
    "Geplaatst bij klant",
    "Afgewezen",
]

STATUS_COLORS = {
    "Instroom":            "warning",
    "Nog geen contact":    "orange",
    "Gesproken":           "info",
    "Komt op gesprek":     "primary",
    "Voorstel gedaan":     "violet",
    "Geplaatst bij klant": "success",
    "Afgewezen":           "secondary",
}

# Omzetting van de oude statusnamen naar de nieuwe fases (eenmalige migratie van
# bestaande leads, zie init_db()). "Contact mislukt" en "Selectie" bestaan niet
# meer als aparte fase en vallen samen met "Afgewezen" resp. zijn vervallen.
_STATUS_MIGRATION = {
    "Review nodig":     "Instroom",
    "Gaat op gesprek":  "Komt op gesprek",
    "Contact mislukt":  "Afgewezen",
}


def _db_url():
    if _HAS_ST:
        try:
            return st.secrets["DATABASE_URL"]
        except Exception:
            pass
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL niet gevonden in secrets of .env")
    return url


def _conn_params():
    import urllib.parse
    p = urllib.parse.urlparse(_db_url())
    qs = urllib.parse.parse_qs(p.query)
    return {
        "host":     p.hostname,
        "port":     p.port or 5432,
        "dbname":   p.path.lstrip("/"),
        "user":     urllib.parse.unquote(p.username),
        "password": urllib.parse.unquote(p.password),
        "sslmode":  qs.get("sslmode", ["require"])[0],
    }


def _conninfo():
    params = _conn_params()
    # Bouw conninfo string handmatig zodat libpq de punt in de username niet afknipt
    return (
        f"host={params['host']} port={params['port']} dbname={params['dbname']} "
        f"user='{params['user']}' password='{params['password']}' sslmode={params['sslmode']}"
    )


# ── Connection pool ──────────────────────────────────────────────────────────
# BELANGRIJK (performance): Neon (serverless Postgres) heeft per nieuwe
# connectie een merkbare opzet-vertraging (TLS-handshake + eventueel
# "cold start" van het compute-endpoint). Eerder opende _conn() voor élke
# query een gloednieuwe connectie — bij een paginalaad met meerdere queries
# (clients, counts, leads, vacatures, ...) liep dit al snel op tot seconden
# vertraging per pagina. Een hergebruikte connection pool (1 process-brede
# pool, lazy aangemaakt) maakt losse queries vrijwel instant.
_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        from psycopg_pool import ConnectionPool
        _pool = ConnectionPool(
            _conninfo(),
            min_size=1,
            max_size=5,
            kwargs={"row_factory": dict_row},
            open=True,
            # Neon zet de compute na inactiviteit op "slapen"; een hergebruikte
            # pool-connectie naar een geslapen endpoint geeft dan
            # psycopg.errors.AdminShutdown. check_connection valideert elke
            # connectie bij het lenen uit de pool en vervangt 'm zo nodig.
            check=ConnectionPool.check_connection,
        )
    return _pool


@contextmanager
def _conn():
    global _pool
    pool = _get_pool()
    try:
        with pool.connection() as con:
            yield con
            con.commit()
    except psycopg.OperationalError:
        # Pool zelf is in een slechte staat (bv. na een Neon-restart) — sluit
        # 'm en bouw 'm opnieuw op voor de volgende poging.
        pool.close()
        _pool = None
        raise


def init_db():
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id         SERIAL PRIMARY KEY,
                name       TEXT NOT NULL,
                page_id    TEXT NOT NULL UNIQUE,
                active     BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS forms (
                id         SERIAL PRIMARY KEY,
                client_id  INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                form_id    TEXT NOT NULL UNIQUE,
                form_name  TEXT,
                active     BOOLEAN DEFAULT TRUE
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id                SERIAL PRIMARY KEY,
                meta_lead_id      TEXT UNIQUE NOT NULL,
                client_id         INTEGER REFERENCES clients(id),
                form_id           TEXT,
                created_time      TEXT,
                full_name         TEXT,
                email             TEXT,
                phone             TEXT,
                form_data         TEXT,
                status            TEXT DEFAULT 'Instroom',
                status_updated_at TIMESTAMP DEFAULT NOW(),
                notes             TEXT DEFAULT '',
                inserted_at       TIMESTAMP DEFAULT NOW()
            )
        """)
        # Migraties
        con.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS logo_url TEXT")
        con.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS form_id TEXT")
        con.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS vacancy_name TEXT")
        con.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS ai_summary TEXT")
        con.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS ai_summary_at TIMESTAMP")
        con.execute("ALTER TABLE forms ADD COLUMN IF NOT EXISTS vacancy_url TEXT")
        # Reset foutief gematche vacancy_name waarden (ja/nee/vergelijkbare korte antwoorden
        # die afkomstig zijn van vragen als "soortgelijke functie?") naar NULL zodat
        # de volgende sync ze correct herdet via de striktere VACANCY_KEYWORDS matching.
        con.execute("""
            UPDATE leads SET vacancy_name = NULL, ai_summary = NULL, ai_summary_at = NULL
            WHERE vacancy_name IS NOT NULL
              AND lower(trim(vacancy_name)) = ANY(ARRAY['ja','nee','yes','no','true','false'])
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS status_history (
                id         SERIAL PRIMARY KEY,
                lead_id    INTEGER NOT NULL REFERENCES leads(id),
                status     TEXT NOT NULL,
                changed_at TIMESTAMP DEFAULT NOW()
            )
        """)
        # Migratie naar nieuwe fase-namen (Kanban-pijplijn): bestaande leads en hun
        # statushistorie krijgen de nieuwe fasenaam volgens _STATUS_MIGRATION.
        for old_status, new_status in _STATUS_MIGRATION.items():
            con.execute(
                "UPDATE leads SET status = %s WHERE status = %s",
                (new_status, old_status),
            )
            con.execute(
                "UPDATE status_history SET status = %s WHERE status = %s",
                (new_status, old_status),
            )


# ── Clients ───────────────────────────────────────────────────────────────────

def get_all_clients():
    with _conn() as con:
        return con.execute("SELECT * FROM clients ORDER BY name").fetchall()


def add_client(name, page_id):
    with _conn() as con:
        con.execute(
            "INSERT INTO clients (name, page_id) VALUES (%s, %s) ON CONFLICT (page_id) DO NOTHING",
            (name, page_id),
        )


def delete_client(client_id):
    with _conn() as con:
        con.execute("DELETE FROM clients WHERE id = %s", (client_id,))


def set_client_logo(client_id, logo_url):
    with _conn() as con:
        con.execute("UPDATE clients SET logo_url = %s WHERE id = %s", (logo_url, client_id))


# ── Forms ─────────────────────────────────────────────────────────────────────

def upsert_form(client_id, form_id, form_name):
    with _conn() as con:
        con.execute("""
            INSERT INTO forms (client_id, form_id, form_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (form_id) DO UPDATE SET form_name = EXCLUDED.form_name
        """, (client_id, form_id, form_name))


def get_forms_for_client(client_id):
    with _conn() as con:
        return con.execute(
            "SELECT * FROM forms WHERE client_id = %s ORDER BY form_name",
            (client_id,)
        ).fetchall()


def set_form_active(form_id, active):
    with _conn() as con:
        con.execute("UPDATE forms SET active = %s WHERE form_id = %s", (active, form_id))


def set_form_vacancy_url(form_id, url):
    with _conn() as con:
        con.execute("UPDATE forms SET vacancy_url = %s WHERE form_id = %s", (url or None, form_id))


def get_form(form_id):
    with _conn() as con:
        return con.execute("SELECT * FROM forms WHERE form_id = %s", (form_id,)).fetchone()


def get_active_form_ids(client_id=None):
    with _conn() as con:
        if client_id:
            rows = con.execute(
                "SELECT form_id FROM forms WHERE active = TRUE AND client_id = %s", (client_id,)
            ).fetchall()
        else:
            rows = con.execute("SELECT form_id FROM forms WHERE active = TRUE").fetchall()
    return {r["form_id"] for r in rows}


# ── Leads ─────────────────────────────────────────────────────────────────────

def upsert_lead(data):
    with _conn() as con:
        existing = con.execute(
            "SELECT id, full_name, email, phone FROM leads WHERE meta_lead_id = %s",
            (data["meta_lead_id"],)
        ).fetchone()
        if existing:
            updates, params = [], []
            for field in ("full_name", "email", "phone", "vacancy_name"):
                if not existing.get(field) and data.get(field):
                    updates.append(f"{field} = %s")
                    params.append(data[field])
            if updates:
                params.append(existing["id"])
                con.execute(f"UPDATE leads SET {', '.join(updates)} WHERE id = %s", params)
            return existing["id"], False
        row = con.execute(
            """INSERT INTO leads
               (meta_lead_id, client_id, form_id, created_time, full_name, email, phone,
                form_data, vacancy_name, status, status_updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'Instroom', NOW())
               RETURNING id""",
            (
                data["meta_lead_id"],
                data["client_id"],
                data.get("form_id"),
                data["created_time"],
                data["full_name"],
                data["email"],
                data["phone"],
                json.dumps(data.get("form_data", {}), ensure_ascii=False),
                data.get("vacancy_name"),
            ),
        ).fetchone()
        con.execute(
            "INSERT INTO status_history (lead_id, status) VALUES (%s, 'Instroom')",
            (row["id"],),
        )
        return row["id"], True


def update_status(lead_id, status):
    with _conn() as con:
        con.execute(
            "UPDATE leads SET status = %s, status_updated_at = NOW() WHERE id = %s",
            (status, lead_id),
        )
        con.execute(
            "INSERT INTO status_history (lead_id, status) VALUES (%s, %s)",
            (lead_id, status),
        )


def update_notes(lead_id, notes):
    with _conn() as con:
        con.execute("UPDATE leads SET notes = %s WHERE id = %s", (notes, lead_id))


def update_ai_summary(lead_id, summary):
    with _conn() as con:
        con.execute(
            "UPDATE leads SET ai_summary = %s, ai_summary_at = NOW() WHERE id = %s",
            (summary, lead_id),
        )


def get_leads_missing_summary(limit=5):
    """Leads met formulierantwoorden maar nog zonder AI-samenvatting (bv. oudere
    leads van vóór deze functie, of leads waarvoor de migratie ai_summary heeft
    gereset). Gebruikt door fetch_leads.py om geleidelijk te backfillen, met een
    limiet per run i.v.m. de Anthropic-rate-limit."""
    with _conn() as con:
        return con.execute(
            """SELECT l.*, c.name AS client_name, f.form_name
               FROM leads l
               LEFT JOIN clients c ON l.client_id = c.id
               LEFT JOIN forms f ON l.form_id = f.form_id
               WHERE l.ai_summary IS NULL
                 AND l.form_data IS NOT NULL AND l.form_data != '{}'
               ORDER BY l.created_time DESC
               LIMIT %s""",
            (limit,),
        ).fetchall()


def get_stale_leads(client_id=None, status="Instroom", hours=24):
    """Leads die al langer dan `hours` uur in `status` staan (op basis van status_updated_at)."""
    query = """
        SELECT l.*, c.name AS client_name
        FROM leads l
        LEFT JOIN clients c ON l.client_id = c.id
        WHERE l.status = %s
          AND l.status_updated_at <= NOW() - make_interval(hours => %s)
    """
    params = [status, hours]
    if client_id:
        query += " AND l.client_id = %s"
        params.append(client_id)
    query += " ORDER BY l.status_updated_at ASC"
    with _conn() as con:
        return con.execute(query, params).fetchall()


def get_vacancies_for_client(client_id):
    with _conn() as con:
        rows = con.execute(
            """SELECT DISTINCT vacancy_name FROM leads
               WHERE client_id = %s AND vacancy_name IS NOT NULL AND vacancy_name != ''
               ORDER BY vacancy_name""",
            (client_id,),
        ).fetchall()
    return [r["vacancy_name"] for r in rows]


def get_leads(client_id=None, status_filter=None, search=None, days=7, vacancy_name=None):
    query = """
        SELECT l.*, c.name AS client_name, f.form_name
        FROM leads l
        LEFT JOIN clients c ON l.client_id = c.id
        LEFT JOIN forms f ON l.form_id = f.form_id
        WHERE (l.form_id IS NULL OR f.active = TRUE OR f.form_id IS NULL)
    """
    params = []
    if days is not None:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query += " AND l.created_time >= %s"
        params.append(cutoff)
    if client_id:
        query += " AND l.client_id = %s"
        params.append(client_id)
    if status_filter:
        query += " AND l.status = %s"
        params.append(status_filter)
    if vacancy_name:
        query += " AND l.vacancy_name = %s"
        params.append(vacancy_name)
    if search:
        query += " AND (l.full_name ILIKE %s OR l.email ILIKE %s OR l.phone ILIKE %s)"
        s = f"%{search}%"
        params.extend([s, s, s])
    query += " ORDER BY l.created_time DESC"
    with _conn() as con:
        return con.execute(query, params).fetchall()


def get_leads_today_count(client_id=None, vacancy_name=None):
    """Lichtgewicht COUNT-query voor de dagsamenvatting (i.p.v. alle leads van
    vandaag op te halen en in Python te tellen)."""
    from datetime import datetime, timezone
    cutoff = datetime.now(timezone.utc).date().isoformat()
    query = "SELECT COUNT(*) AS n FROM leads WHERE created_time >= %s"
    params = [cutoff]
    if client_id:
        query += " AND client_id = %s"
        params.append(client_id)
    if vacancy_name:
        query += " AND vacancy_name = %s"
        params.append(vacancy_name)
    with _conn() as con:
        row = con.execute(query, params).fetchone()
    return row["n"]


def get_status_counts(client_id=None):
    counts = {s: 0 for s in STATUSES}
    with _conn() as con:
        if client_id:
            rows = con.execute(
                "SELECT status, COUNT(*) AS n FROM leads WHERE client_id = %s GROUP BY status",
                (client_id,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT status, COUNT(*) AS n FROM leads GROUP BY status"
            ).fetchall()
    for row in rows:
        counts[row["status"]] = row["n"]
    return counts


def get_lead(lead_id):
    with _conn() as con:
        lead = con.execute(
            """SELECT l.*, c.name AS client_name
               FROM leads l LEFT JOIN clients c ON l.client_id = c.id
               WHERE l.id = %s""",
            (lead_id,),
        ).fetchone()
        history = con.execute(
            "SELECT * FROM status_history WHERE lead_id = %s ORDER BY changed_at DESC",
            (lead_id,),
        ).fetchall()
    return lead, history
