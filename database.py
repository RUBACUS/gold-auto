import os
import gzip
import secrets
import logging
from decimal import Decimal
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash

import psycopg2
import psycopg2.extras
import psycopg2.pool

# â”€â”€ Connection â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Reads DATABASE_URL from environment (set by Railway automatically).
# Format: postgres://user:password@host:port/dbname
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Max rows to keep in append-only tables (cleanup threshold)
_MAX_RATE_HISTORY = 500
_MAX_RATE_CONFIG = 100


def _json_safe_value(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _normalize_row(row):
    return {k: _json_safe_value(v) for k, v in dict(row).items()}


# ── Connection Pool ──────────────────────────────────────
_pool = None


def _get_pool():
    """Lazy-initialize a threaded connection pool (1–5 connections)."""
    global _pool
    if _pool is None or _pool.closed:
        if not DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL environment variable is not set. "
                "Add a PostgreSQL service on Railway and link it to your app."
            )
        _pool = psycopg2.pool.ThreadedConnectionPool(
            1, 5, DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor
        )
    return _pool


def get_connection():
    """Get a connection from the pool."""
    return _get_pool().getconn()


def put_connection(conn):
    """Return a connection to the pool."""
    try:
        pool = _get_pool()
        pool.putconn(conn)
    except Exception:
        pass


def init_db():
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS rate_history (
            id          SERIAL PRIMARY KEY,
            timestamp   TIMESTAMPTZ   NOT NULL,
            rate_14kt   NUMERIC(12,3) NOT NULL,
            rate_18kt   NUMERIC(12,3) NOT NULL,
            rate_fine   NUMERIC(12,3),
            session     TEXT,
            rate_date   TEXT,
            rate_9kt    NUMERIC(12,3)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS update_log (
            id                  SERIAL PRIMARY KEY,
            timestamp           TIMESTAMPTZ   NOT NULL,
            old_rate_14kt       NUMERIC(12,3) NOT NULL,
            old_rate_18kt       NUMERIC(12,3) NOT NULL,
            new_rate_14kt       NUMERIC(12,3) NOT NULL,
            new_rate_18kt       NUMERIC(12,3) NOT NULL,
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
            timestamp TIMESTAMPTZ   NOT NULL,
            rate_i1i2 NUMERIC(12,3) NOT NULL,
            rate_si   NUMERIC(12,3) NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS diamond_update_log (
            id                  SERIAL PRIMARY KEY,
            timestamp           TIMESTAMPTZ   NOT NULL,
            old_rate_i1i2       NUMERIC(12,3) NOT NULL,
            old_rate_si         NUMERIC(12,3) NOT NULL,
            new_rate_i1i2       NUMERIC(12,3) NOT NULL,
            new_rate_si         NUMERIC(12,3) NOT NULL,
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
            timestamp             TIMESTAMPTZ    NOT NULL,
            diamond_i1i2          NUMERIC(12,3) NOT NULL DEFAULT 39500,
            diamond_si            NUMERIC(12,3) NOT NULL DEFAULT 49500,
            colorstone_rate       NUMERIC(12,3) NOT NULL DEFAULT 1500,
            huid_per_pc           NUMERIC(12,3) NOT NULL DEFAULT 100,
            certification         NUMERIC(12,3) NOT NULL DEFAULT 500,
            making_charge         NUMERIC(12,3) NOT NULL DEFAULT 2500,
            cmp_diamond_i1i2      NUMERIC(12,3) NOT NULL DEFAULT 100000,
            cmp_diamond_si        NUMERIC(12,3) NOT NULL DEFAULT 125000,
            cmp_colorstone_rate   NUMERIC(12,3) NOT NULL DEFAULT 1500,
            cmp_huid_per_pc       NUMERIC(12,3) NOT NULL DEFAULT 100,
            cmp_certification     NUMERIC(12,3) NOT NULL DEFAULT 500,
            cmp_making_charge     NUMERIC(12,3) NOT NULL DEFAULT 2500
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
            timestamp     TIMESTAMPTZ NOT NULL,
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
            timestamp  TIMESTAMPTZ NOT NULL,
            filename   TEXT    UNIQUE NOT NULL,
            file_data  BYTEA   NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS app_locks (
            lock_name   TEXT PRIMARY KEY,
            owner_token TEXT NOT NULL,
            acquired_at TIMESTAMPTZ NOT NULL,
            expires_at  TIMESTAMPTZ NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS login_attempts (
            id          SERIAL PRIMARY KEY,
            ip_address  TEXT NOT NULL,
            attempted_at TIMESTAMPTZ NOT NULL
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_time ON login_attempts (ip_address, attempted_at)")

    c.execute("""
        CREATE TABLE IF NOT EXISTS automation_settings (
            id                   SERIAL PRIMARY KEY,
            automation_enabled   INTEGER NOT NULL DEFAULT 1,
            paused_by            TEXT,
            paused_at            TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id         SERIAL PRIMARY KEY,
            timestamp  TIMESTAMPTZ NOT NULL,
            username   TEXT NOT NULL,
            action     TEXT NOT NULL,
            details    TEXT
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON audit_log (timestamp DESC)")

    c.execute("""
        CREATE TABLE IF NOT EXISTS price_snapshots (
            id             SERIAL PRIMARY KEY,
            timestamp      TIMESTAMPTZ NOT NULL,
            update_log_id  INTEGER,
            snapshot_data  BYTEA NOT NULL,
            snapshot_type  TEXT NOT NULL DEFAULT 'pre_update'
        )
    """)

    conn.commit()

    # Best-effort type upgrades for existing deployments created with TEXT/REAL schema.
    # If conversion fails for legacy bad values, keep old type and continue.
    type_migrations = [
        ("rate_history", "timestamp", "TIMESTAMPTZ", "NULLIF(timestamp::text, '')::timestamptz"),
        ("rate_history", "rate_14kt", "NUMERIC(12,3)", "rate_14kt::numeric"),
        ("rate_history", "rate_18kt", "NUMERIC(12,3)", "rate_18kt::numeric"),
        ("rate_history", "rate_fine", "NUMERIC(12,3)", "rate_fine::numeric"),
        ("rate_history", "rate_9kt", "NUMERIC(12,3)", "rate_9kt::numeric"),
        ("update_log", "timestamp", "TIMESTAMPTZ", "NULLIF(timestamp::text, '')::timestamptz"),
        ("update_log", "old_rate_14kt", "NUMERIC(12,3)", "old_rate_14kt::numeric"),
        ("update_log", "old_rate_18kt", "NUMERIC(12,3)", "old_rate_18kt::numeric"),
        ("update_log", "new_rate_14kt", "NUMERIC(12,3)", "new_rate_14kt::numeric"),
        ("update_log", "new_rate_18kt", "NUMERIC(12,3)", "new_rate_18kt::numeric"),
        ("diamond_rate_history", "timestamp", "TIMESTAMPTZ", "NULLIF(timestamp::text, '')::timestamptz"),
        ("diamond_rate_history", "rate_i1i2", "NUMERIC(12,3)", "rate_i1i2::numeric"),
        ("diamond_rate_history", "rate_si", "NUMERIC(12,3)", "rate_si::numeric"),
        ("diamond_update_log", "timestamp", "TIMESTAMPTZ", "NULLIF(timestamp::text, '')::timestamptz"),
        ("diamond_update_log", "old_rate_i1i2", "NUMERIC(12,3)", "old_rate_i1i2::numeric"),
        ("diamond_update_log", "old_rate_si", "NUMERIC(12,3)", "old_rate_si::numeric"),
        ("diamond_update_log", "new_rate_i1i2", "NUMERIC(12,3)", "new_rate_i1i2::numeric"),
        ("diamond_update_log", "new_rate_si", "NUMERIC(12,3)", "new_rate_si::numeric"),
        ("rate_config", "timestamp", "TIMESTAMPTZ", "NULLIF(timestamp::text, '')::timestamptz"),
        ("rate_config", "diamond_i1i2", "NUMERIC(12,3)", "diamond_i1i2::numeric"),
        ("rate_config", "diamond_si", "NUMERIC(12,3)", "diamond_si::numeric"),
        ("rate_config", "colorstone_rate", "NUMERIC(12,3)", "colorstone_rate::numeric"),
        ("rate_config", "huid_per_pc", "NUMERIC(12,3)", "huid_per_pc::numeric"),
        ("rate_config", "certification", "NUMERIC(12,3)", "certification::numeric"),
        ("rate_config", "making_charge", "NUMERIC(12,3)", "making_charge::numeric"),
        ("rate_config", "cmp_diamond_i1i2", "NUMERIC(12,3)", "cmp_diamond_i1i2::numeric"),
        ("rate_config", "cmp_diamond_si", "NUMERIC(12,3)", "cmp_diamond_si::numeric"),
        ("rate_config", "cmp_colorstone_rate", "NUMERIC(12,3)", "cmp_colorstone_rate::numeric"),
        ("rate_config", "cmp_huid_per_pc", "NUMERIC(12,3)", "cmp_huid_per_pc::numeric"),
        ("rate_config", "cmp_certification", "NUMERIC(12,3)", "cmp_certification::numeric"),
        ("rate_config", "cmp_making_charge", "NUMERIC(12,3)", "cmp_making_charge::numeric"),
        ("uploaded_files", "timestamp", "TIMESTAMPTZ", "NULLIF(timestamp::text, '')::timestamptz"),
        ("generated_files", "timestamp", "TIMESTAMPTZ", "NULLIF(timestamp::text, '')::timestamptz"),
    ]
    def _column_matches_target(table_name, col_name, target_type):
        """Return True when the column already has the desired type shape."""
        c.execute(
            """
            SELECT data_type, numeric_precision, numeric_scale
            FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
            """,
            (table_name, col_name),
        )
        meta = c.fetchone()
        if not meta:
            return False

        data_type = (meta.get("data_type") or "").lower()
        target = target_type.upper()

        if target == "TIMESTAMPTZ":
            return data_type == "timestamp with time zone"

        if target.startswith("NUMERIC"):
            return (
                data_type == "numeric"
                and int(meta.get("numeric_precision") or 0) == 12
                and int(meta.get("numeric_scale") or 0) == 3
            )

        return False

    # Avoid hanging app startup if another transaction holds table locks.
    c.execute("SET lock_timeout TO '2s'")

    for table_name, col_name, target_type, using_expr in type_migrations:
        if _column_matches_target(table_name, col_name, target_type):
            continue
        try:
            c.execute(
                f"ALTER TABLE {table_name} ALTER COLUMN {col_name} TYPE {target_type} USING ({using_expr})"
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logging.warning(
                "Skipped type migration %s.%s -> %s: %s",
                table_name,
                col_name,
                target_type,
                exc,
            )

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
    put_connection(conn)


# Auto-initialize on import (graceful — logs error but does not crash)
_db_initialized = False

def ensure_db():
    """Initialize DB tables if not yet done. Safe to call multiple times."""
    global _db_initialized
    if _db_initialized:
        return
    try:
        init_db()
        _db_initialized = True
    except Exception as exc:
        logging.error(
            "Database initialization failed: %s  "
            "Make sure DATABASE_URL is set correctly.",
            exc,
        )

try:
    ensure_db()
except Exception as _init_exc:
    logging.critical("DB init failed on import (non-fatal): %s", _init_exc)


# â”€â”€ Table Cleanup â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# Whitelist of tables allowed in _cleanup_table to prevent SQL injection
_CLEANABLE_TABLES = frozenset({
    "rate_history", "rate_config", "diamond_rate_history",
    "diamond_update_log", "update_log", "login_attempts", "audit_log",
})


def _cleanup_table(conn, table_name, max_rows):
    """Delete oldest rows beyond max_rows retention limit."""
    if table_name not in _CLEANABLE_TABLES:
        raise ValueError(f"Table '{table_name}' is not in the cleanup whitelist")
    from psycopg2 import sql
    c = conn.cursor()
    count_q = sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table_name))
    c.execute(count_q)
    count = c.fetchone()["count"]
    if count > max_rows:
        delete_q = sql.SQL(
            "DELETE FROM {} WHERE id NOT IN (SELECT id FROM {} ORDER BY id DESC LIMIT %s)"
        ).format(sql.Identifier(table_name), sql.Identifier(table_name))
        c.execute(delete_q, (max_rows,))
    c.close()


# â”€â”€ Rate History â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def save_rate(rate_14kt, rate_18kt, rate_fine, session, rate_date, rate_9kt=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """INSERT INTO rate_history (timestamp, rate_14kt, rate_18kt, rate_fine, session, rate_date, rate_9kt)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (datetime.now(timezone.utc).isoformat(), rate_14kt, rate_18kt, rate_fine, session, rate_date, rate_9kt),
    )
    _cleanup_table(conn, "rate_history", _MAX_RATE_HISTORY)
    conn.commit()
    c.close()
    put_connection(conn)


def get_latest_rate():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM rate_history ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    c.close()
    put_connection(conn)
    return _normalize_row(row) if row else None


def get_rate_history(limit=50):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM rate_history ORDER BY id DESC LIMIT %s", (limit,))
    rows = c.fetchall()
    c.close()
    put_connection(conn)
    return [_normalize_row(r) for r in rows]


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
        (datetime.now(timezone.utc).isoformat(), old_14, old_18, new_14, new_18,
         input_file, output_file, variants_updated, products_updated, status),
    )
    conn.commit()
    c.close()
    put_connection(conn)


def get_update_logs(limit=50):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM update_log ORDER BY id DESC LIMIT %s", (limit,))
    rows = c.fetchall()
    c.close()
    put_connection(conn)
    return [_normalize_row(r) for r in rows]


# â”€â”€ Diamond Rate History â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def save_diamond_rates(rate_i1i2, rate_si):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO diamond_rate_history (timestamp, rate_i1i2, rate_si) VALUES (%s, %s, %s)",
        (datetime.now(timezone.utc).isoformat(), float(rate_i1i2), float(rate_si)),
    )
    conn.commit()
    c.close()
    put_connection(conn)


def get_latest_diamond_rates():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM diamond_rate_history ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    c.close()
    put_connection(conn)
    return _normalize_row(row) if row else None


def get_diamond_rate_history(limit=50):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM diamond_rate_history ORDER BY id DESC LIMIT %s", (limit,))
    rows = c.fetchall()
    c.close()
    put_connection(conn)
    return [_normalize_row(r) for r in rows]


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
        (datetime.now(timezone.utc).isoformat(), old_i1i2, old_si, new_i1i2, new_si,
         input_file, output_file, variants_updated, products_updated, status),
    )
    conn.commit()
    c.close()
    put_connection(conn)


def get_diamond_update_logs(limit=50):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM diamond_update_log ORDER BY id DESC LIMIT %s", (limit,))
    rows = c.fetchall()
    c.close()
    put_connection(conn)
    return [_normalize_row(r) for r in rows]


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
        (datetime.now(timezone.utc).isoformat(), float(diamond_i1i2), float(diamond_si),
         float(colorstone_rate), float(huid_per_pc), float(certification),
         float(making_charge), float(cmp_diamond_i1i2), float(cmp_diamond_si),
         float(cmp_colorstone_rate), float(cmp_huid_per_pc),
         float(cmp_certification), float(cmp_making_charge)),
    )
    _cleanup_table(conn, "rate_config", _MAX_RATE_CONFIG)
    conn.commit()
    c.close()
    put_connection(conn)


def get_rate_config():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM rate_config ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    c.close()
    put_connection(conn)
    if row:
        return _normalize_row(row)
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
    put_connection(conn)
    if row and check_password_hash(row["password_hash"], password):
        return {"id": row["id"], "username": row["username"], "role": row["role"]}
    return None


def get_all_users():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, username, role FROM users ORDER BY id")
    rows = c.fetchall()
    c.close()
    put_connection(conn)
    return [_normalize_row(r) for r in rows]


def create_user(username, password, role):
    """Create a new user. Returns (True, user_dict) or (False, error_str)."""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s) RETURNING id",
            (username, generate_password_hash(password), role),
        )
        new_id = c.fetchone()["id"]
        conn.commit()
        return True, {"id": new_id, "username": username, "role": role}
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return False, "Username already exists"
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        c.close()
        put_connection(conn)


def update_user(user_id, username=None, password=None, role=None):
    """Update username, password, and/or role for a user. Returns (True, None) or (False, error_str)."""
    conn = get_connection()
    c = conn.cursor()
    try:
        if username:
            c.execute("UPDATE users SET username=%s WHERE id=%s", (username, user_id))
        if password:
            c.execute("UPDATE users SET password_hash=%s WHERE id=%s",
                      (generate_password_hash(password), user_id))
        if role:
            c.execute("UPDATE users SET role=%s WHERE id=%s", (role, user_id))
        conn.commit()
        return True, None
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return False, "Username already exists"
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        c.close()
        put_connection(conn)


def delete_user(user_id):
    """Delete a user by id. Returns True if found and deleted."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE id=%s", (user_id,))
    rowcount = c.rowcount
    conn.commit()
    c.close()
    put_connection(conn)
    return rowcount > 0


def get_user_by_id(user_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, username, role FROM users WHERE id=%s", (user_id,))
    row = c.fetchone()
    c.close()
    put_connection(conn)
    return _normalize_row(row) if row else None


# â”€â”€ Uploaded Files â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def save_uploaded_file(filename, original_name, file_data=None):
    conn = get_connection()
    try:
        c = conn.cursor()
        # Atomic: deactivate all + insert new in single transaction
        c.execute("UPDATE uploaded_files SET is_active = 0 WHERE is_active = 1")
        compressed = psycopg2.Binary(gzip.compress(file_data)) if file_data is not None else None
        c.execute(
            "INSERT INTO uploaded_files (timestamp, filename, original_name, is_active, file_data) VALUES (%s, %s, %s, 1, %s)",
            (datetime.now(timezone.utc).isoformat(), filename, original_name, compressed),
        )
        conn.commit()
        c.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def get_active_upload():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, timestamp, filename, original_name, is_active FROM uploaded_files WHERE is_active = 1 ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    c.close()
    put_connection(conn)
    return _normalize_row(row) if row else None


def deactivate_active_upload():
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE uploaded_files SET is_active = 0 WHERE is_active = 1")
    conn.commit()
    c.close()
    put_connection(conn)


def get_all_uploads():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, timestamp, filename, original_name, is_active FROM uploaded_files ORDER BY id DESC")
    rows = c.fetchall()
    c.close()
    put_connection(conn)
    return [_normalize_row(r) for r in rows]


def delete_uploaded_file_record(filename):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM uploaded_files WHERE filename = %s", (filename,))
    rowcount = c.rowcount
    conn.commit()
    c.close()
    put_connection(conn)
    return rowcount > 0


def get_upload_file_data(filename):
    """Return raw bytes of an uploaded file stored in the database, or None."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT file_data FROM uploaded_files WHERE filename = %s", (filename,))
    row = c.fetchone()
    c.close()
    put_connection(conn)
    if row and row["file_data"] is not None:
        raw = bytes(row["file_data"])
        # Decompress if gzip-compressed (gzip magic bytes: 1f 8b)
        if raw[:2] == b'\x1f\x8b':
            return gzip.decompress(raw)
        return raw
    return None


# ── Generated Files ──────────────────────────────────────────────────────────
# Store generated CSV outputs in PostgreSQL so they survive Railway redeploys.

def save_generated_file(filename, file_data: bytes):
    """Upsert a generated output file into the database (gzip-compressed)."""
    conn = get_connection()
    c = conn.cursor()
    compressed = gzip.compress(file_data)
    c.execute("DELETE FROM generated_files WHERE filename = %s", (filename,))
    c.execute(
        "INSERT INTO generated_files (timestamp, filename, file_data, size_bytes) VALUES (%s, %s, %s, %s)",
        (datetime.now(timezone.utc).isoformat(), filename, psycopg2.Binary(compressed), len(file_data)),
    )
    conn.commit()
    c.close()
    put_connection(conn)


def get_generated_file(filename):
    """Return raw bytes of a generated file stored in the database, or None."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT file_data FROM generated_files WHERE filename = %s", (filename,))
    row = c.fetchone()
    c.close()
    put_connection(conn)
    if row and row["file_data"] is not None:
        raw = bytes(row["file_data"])
        # Decompress if gzip-compressed (gzip magic bytes: 1f 8b)
        if raw[:2] == b'\x1f\x8b':
            return gzip.decompress(raw)
        return raw
    return None


def get_all_generated_files():
    """List all generated files (metadata only, no binary data)."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, timestamp, filename, size_bytes FROM generated_files ORDER BY id DESC")
    rows = c.fetchall()
    c.close()
    put_connection(conn)
    return [_normalize_row(r) for r in rows]


def acquire_app_lock(lock_name: str, owner_token: str, ttl_seconds: int = 1800) -> bool:
    """Acquire distributed lock in PostgreSQL. Returns True when lock is acquired."""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            INSERT INTO app_locks (lock_name, owner_token, acquired_at, expires_at)
            VALUES (%s, %s, NOW(), NOW() + (%s || ' seconds')::interval)
            ON CONFLICT (lock_name) DO UPDATE
              SET owner_token = EXCLUDED.owner_token,
                  acquired_at = NOW(),
                  expires_at = NOW() + (%s || ' seconds')::interval
              WHERE app_locks.expires_at < NOW() OR app_locks.owner_token = EXCLUDED.owner_token
            RETURNING lock_name
            """,
            (lock_name, owner_token, ttl_seconds, ttl_seconds),
        )
        row = c.fetchone()
        conn.commit()
        return bool(row)
    finally:
        c.close()
        put_connection(conn)


def release_app_lock(lock_name: str, owner_token: str) -> None:
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute("DELETE FROM app_locks WHERE lock_name = %s AND owner_token = %s", (lock_name, owner_token))
        conn.commit()
    finally:
        c.close()
        put_connection(conn)


def is_ip_rate_limited(ip_address: str, limit: int, window_seconds: int) -> bool:
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE ip_address = %s AND attempted_at >= NOW() - (%s || ' seconds')::interval",
            (ip_address, window_seconds),
        )
        count = c.fetchone()["count"]
        return int(count) >= int(limit)
    finally:
        c.close()
        put_connection(conn)


def record_login_attempt(ip_address: str) -> None:
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO login_attempts (ip_address, attempted_at) VALUES (%s, NOW())", (ip_address,))
        c.execute("DELETE FROM login_attempts WHERE attempted_at < NOW() - INTERVAL '24 hours'")
        conn.commit()
    finally:
        c.close()
        put_connection(conn)


def delete_generated_file_record(filename):
    """Delete a generated file record from the database. Returns True if found."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM generated_files WHERE filename = %s", (filename,))
    rowcount = c.rowcount
    conn.commit()
    c.close()
    put_connection(conn)
    return rowcount > 0


# ── Automation Settings ───────────────────────────────────────────────

def get_automation_enabled() -> bool:
    """Returns True if automation is enabled (default: True when no row exists)."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT automation_enabled FROM automation_settings ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    c.close()
    put_connection(conn)
    if row is None:
        return True
    return bool(row["automation_enabled"])


def set_automation_enabled(enabled: bool, username: str):
    """Persist automation enabled/disabled state with who changed it and when."""
    from datetime import timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    now_str = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM automation_settings ORDER BY id DESC LIMIT 1")
    existing = c.fetchone()
    if existing:
        c.execute(
            "UPDATE automation_settings SET automation_enabled=%s, paused_by=%s, paused_at=%s WHERE id=%s",
            (1 if enabled else 0, username if not enabled else None, now_str if not enabled else None, existing["id"])
        )
    else:
        c.execute(
            "INSERT INTO automation_settings (automation_enabled, paused_by, paused_at) VALUES (%s, %s, %s)",
            (1 if enabled else 0, username if not enabled else None, now_str if not enabled else None)
        )
    conn.commit()
    c.close()
    put_connection(conn)


# ── Audit Log ─────────────────────────────────────────────────────
_MAX_AUDIT_LOG = 1000


def save_audit_log(username, action, details=None):
    """Record an action in the audit trail."""
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO audit_log (timestamp, username, action, details) VALUES (%s, %s, %s, %s)",
        (datetime.now(timezone.utc).isoformat(), username, action, details),
    )
    _cleanup_table(conn, "audit_log", _MAX_AUDIT_LOG)
    conn.commit()
    c.close()
    put_connection(conn)


def get_audit_logs(limit=100):
    """Return most recent audit entries."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT %s", (limit,))
    rows = c.fetchall()
    c.close()
    put_connection(conn)
    return [_normalize_row(r) for r in rows]


# ── Price Snapshots (for rollback) ────────────────────────────────
_MAX_SNAPSHOTS = 20


def save_price_snapshot(snapshot_data: bytes, update_log_id=None, snapshot_type="pre_update"):
    """Save a snapshot of the generated CSV before a pricing update."""
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO price_snapshots (timestamp, update_log_id, snapshot_data, snapshot_type) VALUES (%s, %s, %s, %s) RETURNING id",
        (datetime.now(timezone.utc).isoformat(), update_log_id, psycopg2.Binary(snapshot_data), snapshot_type),
    )
    snap_id = c.fetchone()["id"]
    # Keep only recent snapshots
    c.execute(
        "DELETE FROM price_snapshots WHERE id NOT IN (SELECT id FROM price_snapshots ORDER BY id DESC LIMIT %s)",
        (_MAX_SNAPSHOTS,),
    )
    conn.commit()
    c.close()
    put_connection(conn)
    return snap_id


def get_latest_snapshot():
    """Return the most recent pre-update snapshot, or None."""
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT id, timestamp, update_log_id, snapshot_type FROM price_snapshots ORDER BY id DESC LIMIT 1"
    )
    row = c.fetchone()
    c.close()
    put_connection(conn)
    return _normalize_row(row) if row else None


def get_snapshot_data(snapshot_id):
    """Return the raw snapshot bytes for a given snapshot ID."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT snapshot_data FROM price_snapshots WHERE id = %s", (snapshot_id,))
    row = c.fetchone()
    c.close()
    put_connection(conn)
    if row and row["snapshot_data"] is not None:
        return bytes(row["snapshot_data"])
    return None

