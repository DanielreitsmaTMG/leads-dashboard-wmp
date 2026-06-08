import os
import sys
import json
from datetime import datetime
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import streamlit as st

STATUSES = [
    "Review nodig",
    "Contact mislukt",
    "Gesproken",
    "Gaat op gesprek",
    "Geplaatst bij klant",
    "Afgewezen",
]

STATUS_COLORS = {
    "Review nodig":        "warning",
    "Contact mislukt":     "danger",
    "Gesproken":           "info",
    "Gaat op gesprek":     "primary",
    "Geplaatst bij klant": "success",
    "Afgewezen":           "secondary",
}


def _db_url():
    """Leest de database-URL uit Streamlit secrets of omgevingsvariabelen."""
    try:
        return st.secrets["DATABASE_URL"]
    except Exception:
        url = os.getenv("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL niet gevonden in secrets of .env")
        return url


@contextmanager
def _conn():
    con = psycopg2.connect(_db_url(), cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with _conn() as con:
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id         SERIAL PRIMARY KEY,
                name       TEXT NOT NULL,
                page_id    TEXT NOT NULL UNIQUE,
                active     BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS leads (
                id                SERIAL PRIMARY KEY,
                meta_lead_id      TEXT UNIQUE NOT NULL,
                client_id         INTEGER REFERENCES clients(id),
                created_time      TEXT,
                full_name         TEXT,
                email             TEXT,
                phone             TEXT,
                form_data         TEXT,
                status            TEXT DEFAULT 'Review nodig',
                status_updated_at TIMESTAMP DEFAULT NOW(),
                notes             TEXT DEFAULT '',
                inserted_at       TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS status_history (
                id         SERIAL PRIMARY KEY,
                lead_id    INTEGER NOT NULL REFERENCES leads(id),
                status     TEXT NOT NULL,
                changed_at TIMESTAMP DEFAULT NOW()
            );
        """)


# ── Clients ───────────────────────────────────────────────────────────────────

def get_all_clients():
    with _conn() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM clients ORDER BY name")
        return cur.fetchall()


def add_client(name, page_id):
    with _conn() as con:
        con.cursor().execute(
            "INSERT INTO clients (name, page_id) VALUES (%s, %s) ON CONFLICT (page_id) DO NOTHING",
            (name, page_id),
        )


def delete_client(client_id):
    with _conn() as con:
        con.cursor().execute("DELETE FROM clients WHERE id = %s", (client_id,))


# ── Leads ─────────────────────────────────────────────────────────────────────

def upsert_lead(data):
    with _conn() as con:
        cur = con.cursor()
        cur.execute("SELECT id FROM leads WHERE meta_lead_id = %s", (data["meta_lead_id"],))
        if cur.fetchone():
            return

        cur.execute(
            """INSERT INTO leads
               (meta_lead_id, client_id, created_time, full_name, email, phone,
                form_data, status, status_updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'Review nodig', NOW())
               RETURNING id""",
            (
                data["meta_lead_id"],
                data["client_id"],
                data["created_time"],
                data["full_name"],
                data["email"],
                data["phone"],
                json.dumps(data.get("form_data", {}), ensure_ascii=False),
            ),
        )
        lead_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO status_history (lead_id, status) VALUES (%s, 'Review nodig')",
            (lead_id,),
        )


def update_status(lead_id, status):
    with _conn() as con:
        cur = con.cursor()
        cur.execute(
            "UPDATE leads SET status = %s, status_updated_at = NOW() WHERE id = %s",
            (status, lead_id),
        )
        cur.execute(
            "INSERT INTO status_history (lead_id, status) VALUES (%s, %s)",
            (lead_id, status),
        )


def update_notes(lead_id, notes):
    with _conn() as con:
        con.cursor().execute(
            "UPDATE leads SET notes = %s WHERE id = %s", (notes, lead_id)
        )


def get_leads(client_id=None, status_filter=None, search=None):
    query = """
        SELECT l.*, c.name AS client_name
        FROM leads l
        LEFT JOIN clients c ON l.client_id = c.id
        WHERE 1=1
    """
    params = []
    if client_id:
        query += " AND l.client_id = %s"
        params.append(client_id)
    if status_filter:
        query += " AND l.status = %s"
        params.append(status_filter)
    if search:
        query += " AND (l.full_name ILIKE %s OR l.email ILIKE %s OR l.phone ILIKE %s)"
        s = f"%{search}%"
        params.extend([s, s, s])
    query += " ORDER BY l.created_time DESC"
    with _conn() as con:
        cur = con.cursor()
        cur.execute(query, params)
        return cur.fetchall()


def get_status_counts(client_id=None):
    counts = {s: 0 for s in STATUSES}
    with _conn() as con:
        cur = con.cursor()
        if client_id:
            cur.execute(
                "SELECT status, COUNT(*) AS n FROM leads WHERE client_id = %s GROUP BY status",
                (client_id,),
            )
        else:
            cur.execute("SELECT status, COUNT(*) AS n FROM leads GROUP BY status")
        for row in cur.fetchall():
            counts[row["status"]] = row["n"]
    return counts


def get_lead(lead_id):
    with _conn() as con:
        cur = con.cursor()
        cur.execute(
            """SELECT l.*, c.name AS client_name
               FROM leads l LEFT JOIN clients c ON l.client_id = c.id
               WHERE l.id = %s""",
            (lead_id,),
        )
        lead = cur.fetchone()
        cur.execute(
            "SELECT * FROM status_history WHERE lead_id = %s ORDER BY changed_at DESC",
            (lead_id,),
        )
        history = cur.fetchall()
    return lead, history
