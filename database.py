import sqlite3
import os
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gold_updater.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS rate_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            rate_14kt   REAL    NOT NULL,
            rate_18kt   REAL    NOT NULL,
            rate_fine    REAL,
            session     TEXT,
            rate_date   TEXT,
            rate_9kt    REAL
        )
    """)

    # Add rate_9kt column if upgrading from older schema
    try:
        c.execute("ALTER TABLE rate_history ADD COLUMN rate_9kt REAL")
    except sqlite3.OperationalError:
        pass  # column already exists

    c.execute("""
        CREATE TABLE IF NOT EXISTS update_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp           TEXT    NOT NULL,
            old_rate_14kt       REAL    NOT NULL,
            old_rate_18kt       REAL    NOT NULL,
            new_rate_14kt       REAL    NOT NULL,
            new_rate_18kt       REAL    NOT NULL,
            input_file          TEXT    NOT NULL,
            output_file         TEXT    NOT NULL,
            variants_updated    INTEGER NOT NULL,
            products_updated    INTEGER NOT NULL,
            status              TEXT    NOT NULL DEFAULT 'success'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS diamond_rate_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            rate_i1i2   REAL    NOT NULL,   -- GH I1-I2 rate per carat (₹)
            rate_si     REAL    NOT NULL    -- GH SI rate per carat (₹)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS diamond_update_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp           TEXT    NOT NULL,
            old_rate_i1i2       REAL    NOT NULL,
            old_rate_si         REAL    NOT NULL,
            new_rate_i1i2       REAL    NOT NULL,
            new_rate_si         REAL    NOT NULL,
            input_file          TEXT    NOT NULL,
            output_file         TEXT    NOT NULL,
            variants_updated    INTEGER NOT NULL,
            products_updated    INTEGER NOT NULL,
            status              TEXT    NOT NULL DEFAULT 'success'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS base_rates (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            rate_14kt   REAL    NOT NULL,
            rate_18kt   REAL    NOT NULL,
            rate_9kt    REAL
        )
    """)

    # Add rate_9kt column if upgrading from older schema
    try:
        c.execute("ALTER TABLE base_rates ADD COLUMN rate_9kt REAL")
    except sqlite3.OperationalError:
        pass  # column already exists

    # ── Rate Config (editable fields from rate card) ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS rate_config (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp             TEXT    NOT NULL,
            diamond_i1i2          REAL    NOT NULL DEFAULT 39500,
            diamond_si            REAL    NOT NULL DEFAULT 49500,
            colorstone_rate       REAL    NOT NULL DEFAULT 1500,
            huid_per_pc           REAL    NOT NULL DEFAULT 100,
            certification         REAL    NOT NULL DEFAULT 500,
            making_charge         REAL    NOT NULL DEFAULT 2500,
            cmp_diamond_i1i2      REAL    NOT NULL DEFAULT 100000,
            cmp_diamond_si        REAL    NOT NULL DEFAULT 125000,
            cmp_colorstone_rate   REAL    NOT NULL DEFAULT 1500,
            cmp_huid_per_pc       REAL    NOT NULL DEFAULT 100,
            cmp_certification     REAL    NOT NULL DEFAULT 500,
            cmp_making_charge     REAL    NOT NULL DEFAULT 2500
        )
    """)

    # Add compare-at columns if upgrading from older schema
    for col, default in [
        ("cmp_diamond_i1i2", 100000), ("cmp_diamond_si", 125000),
        ("cmp_colorstone_rate", 1500), ("cmp_huid_per_pc", 100),
        ("cmp_certification", 500), ("cmp_making_charge", 2500),
    ]:
        try:
            c.execute(f"ALTER TABLE rate_config ADD COLUMN {col} REAL NOT NULL DEFAULT {default}")
        except sqlite3.OperationalError:
            pass

    # ── Users ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    UNIQUE NOT NULL,
            password_hash TEXT    NOT NULL,
            role          TEXT    NOT NULL DEFAULT 'viewer'
        )
    """)

    # ── Uploaded Files ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS uploaded_files (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT    NOT NULL,
            filename      TEXT    NOT NULL,
            original_name TEXT    NOT NULL,
            is_active     INTEGER NOT NULL DEFAULT 1
        )
    """)

    conn.commit()

    # Seed default users if not present
    existing = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if existing == 0:
        c.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("admin", generate_password_hash("admin123"), "editor"),
        )
        c.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("viewer", generate_password_hash("viewer123"), "viewer"),
        )
        conn.commit()

    conn.close()


# ── Rate History ─────────────────────────────────────────

def save_rate(rate_14kt, rate_18kt, rate_fine, session, rate_date, rate_9kt=None):
    conn = get_connection()
    conn.execute(
        """INSERT INTO rate_history (timestamp, rate_14kt, rate_18kt, rate_fine, session, rate_date, rate_9kt)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (datetime.now().isoformat(), rate_14kt, rate_18kt, rate_fine, session, rate_date, rate_9kt),
    )
    conn.commit()
    conn.close()


def get_latest_rate():
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM rate_history ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_rate_history(limit=50):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM rate_history ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Update Log ───────────────────────────────────────────

def save_update_log(old_14, old_18, new_14, new_18, input_file, output_file,
                    variants_updated, products_updated, status="success"):
    conn = get_connection()
    conn.execute(
        """INSERT INTO update_log
           (timestamp, old_rate_14kt, old_rate_18kt, new_rate_14kt, new_rate_18kt,
            input_file, output_file, variants_updated, products_updated, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (datetime.now().isoformat(), old_14, old_18, new_14, new_18,
         input_file, output_file, variants_updated, products_updated, status),
    )
    conn.commit()
    conn.close()


def get_update_logs(limit=50):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM update_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Diamond Rate History ─────────────────────────────────

def save_diamond_rates(rate_i1i2, rate_si):
    conn = get_connection()
    conn.execute(
        """INSERT INTO diamond_rate_history (timestamp, rate_i1i2, rate_si)
           VALUES (?, ?, ?)""",
        (datetime.now().isoformat(), float(rate_i1i2), float(rate_si)),
    )
    conn.commit()
    conn.close()


def get_latest_diamond_rates():
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM diamond_rate_history ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_diamond_rate_history(limit=50):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM diamond_rate_history ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Diamond Update Log ───────────────────────────────────

def save_diamond_update_log(old_i1i2, old_si, new_i1i2, new_si,
                             input_file, output_file,
                             variants_updated, products_updated, status="success"):
    conn = get_connection()
    conn.execute(
        """INSERT INTO diamond_update_log
           (timestamp, old_rate_i1i2, old_rate_si, new_rate_i1i2, new_rate_si,
            input_file, output_file, variants_updated, products_updated, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (datetime.now().isoformat(), old_i1i2, old_si, new_i1i2, new_si,
         input_file, output_file, variants_updated, products_updated, status),
    )
    conn.commit()
    conn.close()


def get_diamond_update_logs(limit=50):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM diamond_update_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# Auto-initialize on import
init_db()


# ── Original Base Rates ──────────────────────────────────

def save_base_rates(rate_14kt, rate_18kt, rate_9kt=None):
    """Store the original base rates (the rates the original export was priced at)."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO base_rates (timestamp, rate_14kt, rate_18kt, rate_9kt)
           VALUES (?, ?, ?, ?)""",
        (datetime.now().isoformat(), float(rate_14kt), float(rate_18kt),
         float(rate_9kt) if rate_9kt is not None else None),
    )
    conn.commit()
    conn.close()


def get_base_rates():
    """Return the original base rates, or None if not set."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM base_rates ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── Rate Config ──────────────────────────────────────────

def save_rate_config(diamond_i1i2, diamond_si, colorstone_rate,
                     huid_per_pc, certification, making_charge=2500,
                     cmp_diamond_i1i2=100000, cmp_diamond_si=125000,
                     cmp_colorstone_rate=1500, cmp_huid_per_pc=100,
                     cmp_certification=500, cmp_making_charge=2500):
    conn = get_connection()
    conn.execute(
        """INSERT INTO rate_config
           (timestamp, diamond_i1i2, diamond_si, colorstone_rate,
            huid_per_pc, certification, making_charge,
            cmp_diamond_i1i2, cmp_diamond_si, cmp_colorstone_rate,
            cmp_huid_per_pc, cmp_certification, cmp_making_charge)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (datetime.now().isoformat(), float(diamond_i1i2), float(diamond_si),
         float(colorstone_rate), float(huid_per_pc), float(certification),
         float(making_charge), float(cmp_diamond_i1i2), float(cmp_diamond_si),
         float(cmp_colorstone_rate), float(cmp_huid_per_pc),
         float(cmp_certification), float(cmp_making_charge)),
    )
    conn.commit()
    conn.close()


def get_rate_config():
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM rate_config ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    # Return defaults if none saved
    return {
        "diamond_i1i2": 39500,
        "diamond_si": 49500,
        "colorstone_rate": 1500,
        "huid_per_pc": 100,
        "certification": 500,
        "making_charge": 2500,
        "cmp_diamond_i1i2": 100000,
        "cmp_diamond_si": 125000,
        "cmp_colorstone_rate": 1500,
        "cmp_huid_per_pc": 100,
        "cmp_certification": 500,
        "cmp_making_charge": 2500,
    }


# ── Users ────────────────────────────────────────────────

def authenticate_user(username, password):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    conn.close()
    if row and check_password_hash(row["password_hash"], password):
        return {"id": row["id"], "username": row["username"], "role": row["role"]}
    return None


def get_all_users():
    conn = get_connection()
    rows = conn.execute("SELECT id, username, role FROM users").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Uploaded Files ───────────────────────────────────────

def save_uploaded_file(filename, original_name):
    conn = get_connection()
    # Deactivate all previous uploads
    conn.execute("UPDATE uploaded_files SET is_active = 0")
    conn.execute(
        """INSERT INTO uploaded_files (timestamp, filename, original_name, is_active)
           VALUES (?, ?, ?, 1)""",
        (datetime.now().isoformat(), filename, original_name),
    )
    conn.commit()
    conn.close()


def get_active_upload():
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM uploaded_files WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None
