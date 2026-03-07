import os
import secrets
import logging
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

import psycopg2
import psycopg2.extras

# â”€â”€ Connection â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Reads DATABASE_URL from environment (set by Railway automatically).
# Format: postgres://user:password@host:port/dbname
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Max rows to keep in append-only tables (cleanup threshold)
_MAX_RATE_HISTORY = 500
_MAX_RATE_CONFIG = 100


def get_connection():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Add a PostgreSQL service on Railway and link it to your app."
        )
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS rate_history (
            id          SERIAL PRIMARY KEY,
            timestamp   TEXT   NOT NULL,
            rate_14kt   REAL   NOT NULL,
            rate_18kt   REAL   NOT NULL,
            rate_fine   REAL,
            session     TEXT,
            rate_date   TEXT,
            rate_9kt    REAL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS update_log (
            id                  SERIAL PRIMARY KEY,
            timestamp           TEXT   NOT NULL,
            old_rate_14kt       REAL   NOT NULL,
            old_rate_18kt       REAL   NOT NULL,
            new_rate_14kt       REAL   NOT NULL,
            new_rate_18kt       REAL   NOT NULL,
            input_file          TEXT   NOT NULL,
            output_file         TEXT   NOT NULL,
            variants_updated    INTEGER NOT NULL,
            products_updated    INTEGER NOT NULL,
            status              TEXT   NOT NULL DEFAULT 'success'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS diamond_rate_history (
            id        SERIAL PRIMARY KEY,
            timestamp TEXT   NOT NULL,
            rate_i1i2 REAL   NOT NULL,
            rate_si   REAL   NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS diamond_update_log (
            id                  SERIAL PRIMARY KEY,
            timestamp           TEXT   NOT NULL,
            old_rate_i1i2       REAL   NOT NULL,
            old_rate_si         REAL   NOT NULL,
            new_rate_i1i2       REAL   NOT NULL,
            new_rate_si         REAL   NOT NULL,
            input_file          TEXT   NOT NULL,
            output_file         TEXT   NOT NULL,
            variants_updated    INTEGER NOT NULL,
            products_updated    INTEGER NOT NULL,
            status              TEXT   NOT NULL DEFAULT 'success'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS rate_config (
            id                    SERIAL PRIMARY KEY,
            timestamp             TEXT   NOT NULL,
            diamond_i1i2          REAL   NOT NULL DEFAULT 39500,
            diamond_si            REAL   NOT NULL DEFAULT 49500,
            colorstone_rate       REAL   NOT NULL DEFAULT 1500,
            huid_per_pc           REAL   NOT NULL DEFAULT 100,
            certification         REAL   NOT NULL DEFAULT 500,
            making_charge         REAL   NOT NULL DEFAULT 2500,
            cmp_diamond_i1i2      REAL   NOT NULL DEFAULT 100000,
            cmp_diamond_si        REAL   NOT NULL DEFAULT 125000,
            cmp_colorstone_rate   REAL   NOT NULL DEFAULT 1500,
            cmp_huid_per_pc       REAL   NOT NULL DEFAULT 100,
            cmp_certification     REAL   NOT NULL DEFAULT 500,
            cmp_making_charge     REAL   NOT NULL DEFAULT 2500
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            SERIAL PRIMARY KEY,
            username      TEXT   UNIQUE NOT NULL,
            password_hash TEXT   NOT NULL,
            role          TEXT   NOT NULL DEFAULT 'viewer'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS uploaded_files (
            id            SERIAL PRIMARY KEY,
            timestamp     TEXT    NOT NULL,
            filename      TEXT    NOT NULL,
            original_name TEXT    NOT NULL,
            is_active     INTEGER NOT NULL DEFAULT 1,
            file_data     BYTEA
        )
    """)

    # Add file_data column to existing tables that pre-date this migration
    c.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'uploaded_files' AND column_name = 'file_data'
            ) THEN
                ALTER TABLE uploaded_files ADD COLUMN file_data BYTEA;
            END IF;
        END $$;
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS generated_files (
            id         SERIAL PRIMARY KEY,
            timestamp  TEXT    NOT NULL,
            filename   TEXT    UNIQUE NOT NULL,
            file_data  BYTEA   NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.commit()

    # Seed default users if not present
    c.execute("SELECT COUNT(*) FROM users")
    existing = c.fetchone()["count"]
    if existing == 0:
        admin_pw = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(12)
        viewer_pw = os.environ.get("VIEWER_PASSWORD") or secrets.token_urlsafe(12)
        c.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
            ("admin", generate_password_hash(admin_pw), "editor"),
        )
        c.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
            ("viewer", generate_password_hash(viewer_pw), "viewer"),
        )
        conn.commit()
        if not os.environ.get("ADMIN_PASSWORD"):
            logging.warning("========================================")
            logging.warning("  DEFAULT CREDENTIALS GENERATED:")
            logging.warning(f"  admin  / {admin_pw}  (editor)")
            logging.warning(f"  viewer / {viewer_pw}  (viewer)")
            logging.warning("  Set ADMIN_PASSWORD & VIEWER_PASSWORD env vars to control these.")
            logging.warning("========================================")

    c.close()
    conn.close()


# Auto-initialize on import
try:
    init_db()
except Exception as _init_exc:
    logging.critical(
        "Database initialization failed: %s\n"
        "Make sure DATABASE_URL is set correctly in .env.local\n"
        "NOTE: 'postgres.railway.internal' only resolves inside Railway's network.\n"
        "For local development, get the PUBLIC URL from Railway dashboard:\n"
        "  Project → PostgreSQL → Connect → Public Network → DATABASE_URL",
        _init_exc,
    )
    raise


# â”€â”€ Table Cleanup â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _cleanup_table(conn, table_name, max_rows):
    """Delete oldest rows beyond max_rows retention limit."""
    c = conn.cursor()
    c.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = c.fetchone()["count"]
    if count > max_rows:
        c.execute(f"""
            DELETE FROM {table_name} WHERE id NOT IN (
                SELECT id FROM {table_name} ORDER BY id DESC LIMIT {max_rows}
            )
        """)
    c.close()


# â”€â”€ Rate History â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def save_rate(rate_14kt, rate_18kt, rate_fine, session, rate_date, rate_9kt=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """INSERT INTO rate_history (timestamp, rate_14kt, rate_18kt, rate_fine, session, rate_date, rate_9kt)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (datetime.now().isoformat(), rate_14kt, rate_18kt, rate_fine, session, rate_date, rate_9kt),
    )
    _cleanup_table(conn, "rate_history", _MAX_RATE_HISTORY)
    conn.commit()
    c.close()
    conn.close()


def get_latest_rate():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM rate_history ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    c.close()
    conn.close()
    return dict(row) if row else None


def get_rate_history(limit=50):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM rate_history ORDER BY id DESC LIMIT %s", (limit,))
    rows = c.fetchall()
    c.close()
    conn.close()
    return [dict(r) for r in rows]


# â”€â”€ Update Log â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def save_update_log(old_14, old_18, new_14, new_18, input_file, output_file,
                    variants_updated, products_updated, status="success"):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """INSERT INTO update_log
           (timestamp, old_rate_14kt, old_rate_18kt, new_rate_14kt, new_rate_18kt,
            input_file, output_file, variants_updated, products_updated, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (datetime.now().isoformat(), old_14, old_18, new_14, new_18,
         input_file, output_file, variants_updated, products_updated, status),
    )
    conn.commit()
    c.close()
    conn.close()


def get_update_logs(limit=50):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM update_log ORDER BY id DESC LIMIT %s", (limit,))
    rows = c.fetchall()
    c.close()
    conn.close()
    return [dict(r) for r in rows]


# â”€â”€ Diamond Rate History â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def save_diamond_rates(rate_i1i2, rate_si):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO diamond_rate_history (timestamp, rate_i1i2, rate_si) VALUES (%s, %s, %s)",
        (datetime.now().isoformat(), float(rate_i1i2), float(rate_si)),
    )
    conn.commit()
    c.close()
    conn.close()


def get_latest_diamond_rates():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM diamond_rate_history ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    c.close()
    conn.close()
    return dict(row) if row else None


def get_diamond_rate_history(limit=50):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM diamond_rate_history ORDER BY id DESC LIMIT %s", (limit,))
    rows = c.fetchall()
    c.close()
    conn.close()
    return [dict(r) for r in rows]


# â”€â”€ Diamond Update Log â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def save_diamond_update_log(old_i1i2, old_si, new_i1i2, new_si,
                             input_file, output_file,
                             variants_updated, products_updated, status="success"):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """INSERT INTO diamond_update_log
           (timestamp, old_rate_i1i2, old_rate_si, new_rate_i1i2, new_rate_si,
            input_file, output_file, variants_updated, products_updated, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (datetime.now().isoformat(), old_i1i2, old_si, new_i1i2, new_si,
         input_file, output_file, variants_updated, products_updated, status),
    )
    conn.commit()
    c.close()
    conn.close()


def get_diamond_update_logs(limit=50):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM diamond_update_log ORDER BY id DESC LIMIT %s", (limit,))
    rows = c.fetchall()
    c.close()
    conn.close()
    return [dict(r) for r in rows]


# â”€â”€ Rate Config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def save_rate_config(diamond_i1i2, diamond_si, colorstone_rate,
                     huid_per_pc, certification, making_charge=2500,
                     cmp_diamond_i1i2=100000, cmp_diamond_si=125000,
                     cmp_colorstone_rate=1500, cmp_huid_per_pc=100,
                     cmp_certification=500, cmp_making_charge=2500):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """INSERT INTO rate_config
           (timestamp, diamond_i1i2, diamond_si, colorstone_rate,
            huid_per_pc, certification, making_charge,
            cmp_diamond_i1i2, cmp_diamond_si, cmp_colorstone_rate,
            cmp_huid_per_pc, cmp_certification, cmp_making_charge)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (datetime.now().isoformat(), float(diamond_i1i2), float(diamond_si),
         float(colorstone_rate), float(huid_per_pc), float(certification),
         float(making_charge), float(cmp_diamond_i1i2), float(cmp_diamond_si),
         float(cmp_colorstone_rate), float(cmp_huid_per_pc),
         float(cmp_certification), float(cmp_making_charge)),
    )
    _cleanup_table(conn, "rate_config", _MAX_RATE_CONFIG)
    conn.commit()
    c.close()
    conn.close()


def get_rate_config():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM rate_config ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    c.close()
    conn.close()
    if row:
        return dict(row)
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


# â”€â”€ Users â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def authenticate_user(username, password):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = %s", (username,))
    row = c.fetchone()
    c.close()
    conn.close()
    if row and check_password_hash(row["password_hash"], password):
        return {"id": row["id"], "username": row["username"], "role": row["role"]}
    return None


def get_all_users():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, username, role FROM users")
    rows = c.fetchall()
    c.close()
    conn.close()
    return [dict(r) for r in rows]


# â”€â”€ Uploaded Files â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def save_uploaded_file(filename, original_name, file_data=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE uploaded_files SET is_active = 0")
    c.execute(
        "INSERT INTO uploaded_files (timestamp, filename, original_name, is_active, file_data) VALUES (%s, %s, %s, 1, %s)",
        (datetime.now().isoformat(), filename, original_name,
         psycopg2.Binary(file_data) if file_data is not None else None),
    )
    conn.commit()
    c.close()
    conn.close()


def get_active_upload():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, timestamp, filename, original_name, is_active FROM uploaded_files WHERE is_active = 1 ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    c.close()
    conn.close()
    return dict(row) if row else None


def deactivate_active_upload():
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE uploaded_files SET is_active = 0 WHERE is_active = 1")
    conn.commit()
    c.close()
    conn.close()


def get_all_uploads():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, timestamp, filename, original_name, is_active FROM uploaded_files ORDER BY id DESC")
    rows = c.fetchall()
    c.close()
    conn.close()
    return [dict(r) for r in rows]


def delete_uploaded_file_record(filename):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM uploaded_files WHERE filename = %s", (filename,))
    rowcount = c.rowcount
    conn.commit()
    c.close()
    conn.close()
    return rowcount > 0


def get_upload_file_data(filename):
    """Return raw bytes of an uploaded file stored in the database, or None."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT file_data FROM uploaded_files WHERE filename = %s", (filename,))
    row = c.fetchone()
    c.close()
    conn.close()
    if row and row["file_data"] is not None:
        return bytes(row["file_data"])
    return None


# ── Generated Files ──────────────────────────────────────────────────────────
# Store generated CSV outputs in PostgreSQL so they survive Railway redeploys.

def save_generated_file(filename, file_data: bytes):
    """Upsert a generated output file into the database."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM generated_files WHERE filename = %s", (filename,))
    c.execute(
        "INSERT INTO generated_files (timestamp, filename, file_data, size_bytes) VALUES (%s, %s, %s, %s)",
        (datetime.now().isoformat(), filename, psycopg2.Binary(file_data), len(file_data)),
    )
    conn.commit()
    c.close()
    conn.close()


def get_generated_file(filename):
    """Return raw bytes of a generated file stored in the database, or None."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT file_data FROM generated_files WHERE filename = %s", (filename,))
    row = c.fetchone()
    c.close()
    conn.close()
    if row and row["file_data"] is not None:
        return bytes(row["file_data"])
    return None


def get_all_generated_files():
    """List all generated files (metadata only, no binary data)."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, timestamp, filename, size_bytes FROM generated_files ORDER BY id DESC")
    rows = c.fetchall()
    c.close()
    conn.close()
    return [dict(r) for r in rows]


def delete_generated_file_record(filename):
    """Delete a generated file record from the database. Returns True if found."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM generated_files WHERE filename = %s", (filename,))
    rowcount = c.rowcount
    conn.commit()
    c.close()
    conn.close()
    return rowcount > 0

