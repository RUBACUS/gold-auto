import os
import io
import uuid
import secrets
import time
import logging
import threading
from collections import defaultdict
from datetime import datetime
from functools import wraps

# Load environment variables from .env.local (local dev) then .env before anything else
from dotenv import load_dotenv
_BASE_DIR_EARLY = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE_DIR_EARLY, ".env.local"), override=False)
load_dotenv(os.path.join(_BASE_DIR_EARLY, ".env"), override=False)

from flask import (
    Flask, jsonify, request, send_from_directory, send_file,
    render_template, session, redirect, url_for,
)
from werkzeug.utils import secure_filename

from scraper import scrape_ibja_rates
from database import (
    get_latest_rate, get_rate_history, get_update_logs, save_rate,
    get_diamond_update_logs,
    get_rate_config, save_rate_config,
    authenticate_user,
    save_uploaded_file, get_active_upload, get_upload_file_data,
    deactivate_active_upload, get_all_uploads, delete_uploaded_file_record,
    save_generated_file, get_generated_file,
    get_all_generated_files, delete_generated_file_record,
)
from update_prices import run_update, run_diamond_update, OUTPUT_DIR, get_source_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")

# ── Secret Key (MUST be set via environment for production) ──────────
_secret = os.environ.get("SECRET_KEY")
if not _secret:
    _secret = secrets.token_hex(32)
    logging.warning(
        "SECRET_KEY not set in environment. A random key was generated. "
        "Sessions will NOT persist across restarts. Set SECRET_KEY env var."
    )

app = Flask(__name__)
app.secret_key = _secret

# ── Security Config ──────────────────────────────────────────────────
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1 GB upload limit
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if os.environ.get("FLASK_ENV") == "production":
    app.config["SESSION_COOKIE_SECURE"] = True

ALLOWED_EXTENSIONS = {".xlsx", ".csv"}
_XLSX_MAGIC = b"PK"  # ZIP-based format signature

# Simple lock to prevent concurrent updates (single-worker mode)
_update_lock = threading.Lock()

# ── Rate Limiting ────────────────────────────────────────────────────
_login_attempts = defaultdict(list)
LOGIN_RATE_LIMIT = 5        # max attempts
LOGIN_RATE_WINDOW = 300     # per 5 minutes (seconds)


def _is_rate_limited(ip):
    now = time.time()
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < LOGIN_RATE_WINDOW]
    return len(_login_attempts[ip]) >= LOGIN_RATE_LIMIT


def _record_login_attempt(ip):
    _login_attempts[ip].append(time.time())


# ── Security Headers ────────────────────────────────────────────────
@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com; "
        "img-src 'self' data:;"
    )
    if os.environ.get("FLASK_ENV") == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ── CSRF Protection ─────────────────────────────────────────────────
def _get_csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return session["_csrf_token"]


app.jinja_env.globals["csrf_token"] = _get_csrf_token


@app.errorhandler(413)
def request_entity_too_large(e):
    return jsonify({"ok": False, "error": "File too large. Maximum upload size is 1 GB."}), 413


@app.before_request
def csrf_protect():
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        # Login doesn't have a session yet, skip CSRF
        if request.path == "/api/auth/login":
            return
        token = request.headers.get("X-CSRF-Token") or \
            (request.get_json(silent=True) or {}).get("_csrf_token")
        if not token or token != session.get("_csrf_token"):
            return jsonify({"ok": False, "error": "CSRF validation failed"}), 403


# ── File Content Validation ──────────────────────────────────────────
def _validate_file_content(file_storage, ext):
    """Check that file content matches its extension (not just name)."""
    header = file_storage.read(4096)
    file_storage.seek(0)
    if ext == ".xlsx":
        if not header.startswith(_XLSX_MAGIC):
            return False, "File content does not match .xlsx format"
    elif ext == ".csv":
        # CSV should be mostly text; reject binary files renamed to .csv
        if len(header) > 0:
            non_text = sum(1 for b in header[:512] if b < 0x09 or (0x0E <= b <= 0x1F))
            if non_text / max(len(header[:512]), 1) > 0.1:
                return False, "File content does not appear to be valid CSV"
    return True, ""


def _sanitize_error(e):
    """Return a safe error message without leaking internals."""
    msg = str(e)
    # Strip raw HTML/section content from scraper errors
    if "Section text:" in msg:
        msg = msg.split("Section text:")[0].strip()
    return msg


# ── Auth helpers ─────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Not authenticated"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return wrapper


def editor_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return jsonify({"ok": False, "error": "Not authenticated"}), 401
        if session["user"].get("role") != "editor":
            return jsonify({"ok": False, "error": "Editor access required"}), 403
        return f(*args, **kwargs)
    return wrapper


# ── Pages ────────────────────────────────────────────────

@app.route("/favicon.ico")
def favicon():
    return "", 204

@app.route("/login")
def login_page():
    if "user" in session:
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/")
@login_required
def index():
    # Deactivate any active upload on page load so user must re-upload each session
    try:
        deactivate_active_upload()
    except Exception:
        pass
    return render_template("index.html", user=session["user"])


# ── Auth API ─────────────────────────────────────────────

@app.route("/api/auth/login", methods=["POST"])
def api_login():
    # Rate limiting
    client_ip = request.remote_addr
    if _is_rate_limited(client_ip):
        return jsonify({"ok": False, "error": "Too many login attempts. Try again later."}), 429

    body = request.get_json(force=True, silent=True) or {}
    username = body.get("username", "").strip()
    password = body.get("password", "")

    if not username or not password:
        return jsonify({"ok": False, "error": "Username and password required"}), 400

    user = authenticate_user(username, password)
    if not user:
        _record_login_attempt(client_ip)
        return jsonify({"ok": False, "error": "Invalid credentials"}), 401

    session["user"] = user
    return jsonify({"ok": True, "user": user})


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.pop("user", None)
    return jsonify({"ok": True})


@app.route("/api/auth/me")
def api_me():
    if "user" in session:
        return jsonify({"ok": True, "user": session["user"]})
    return jsonify({"ok": False}), 401


# ── API: Rates ───────────────────────────────────────────

@app.route("/api/rates/current")
@login_required
def api_current_rates():
    try:
        rates = scrape_ibja_rates()
        return jsonify({"ok": True, "rates": rates})
    except Exception as e:
        return jsonify({"ok": False, "error": _sanitize_error(e)}), 502


@app.route("/api/rates/stored")
@login_required
def api_stored_rates():
    stored = get_latest_rate()
    if stored:
        return jsonify({"ok": True, "rate": stored})
    return jsonify({"ok": True, "rate": None, "message": "No baseline rate stored yet."})


@app.route("/api/rates/history")
@login_required
def api_rate_history():
    limit = request.args.get("limit", 50, type=int)
    rows = get_rate_history(limit)
    return jsonify({"ok": True, "history": rows})


# ── API: Rate Config (editable fields) ──────────────────

@app.route("/api/config")
@login_required
def api_get_config():
    cfg = get_rate_config()
    return jsonify({"ok": True, "config": cfg})


@app.route("/api/config", methods=["POST"])
@editor_required
def api_set_config():
    body = request.get_json(force=True, silent=True) or {}

    # Standard price chart fields
    std_fields = ["diamond_i1i2", "diamond_si", "colorstone_rate",
                  "huid_per_pc", "certification"]
    # Compare-at price chart fields
    cmp_fields = ["cmp_diamond_i1i2", "cmp_diamond_si", "cmp_colorstone_rate",
                  "cmp_huid_per_pc", "cmp_certification"]

    all_fields = std_fields + cmp_fields
    for f in all_fields:
        if f not in body:
            return jsonify({"ok": False, "error": f"Missing field: {f}"}), 400

    try:
        vals = {f: float(body[f]) for f in all_fields}
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "All rates must be valid numbers"}), 400

    for f, v in vals.items():
        if v < 0:
            return jsonify({"ok": False, "error": f"{f} cannot be negative"}), 400

    try:
        making = float(body.get("making_charge", 2500))
        cmp_making = float(body.get("cmp_making_charge", 2500))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Making charge must be a valid number"}), 400

    if making < 0:
        return jsonify({"ok": False, "error": "making_charge cannot be negative"}), 400
    if cmp_making < 0:
        return jsonify({"ok": False, "error": "cmp_making_charge cannot be negative"}), 400

    save_rate_config(
        vals["diamond_i1i2"], vals["diamond_si"], vals["colorstone_rate"],
        vals["huid_per_pc"], vals["certification"], making,
        vals["cmp_diamond_i1i2"], vals["cmp_diamond_si"], vals["cmp_colorstone_rate"],
        vals["cmp_huid_per_pc"], vals["cmp_certification"], cmp_making,
    )
    return jsonify({"ok": True, "message": "Rate configuration saved successfully."})


# ── API: Update ──────────────────────────────────────────

@app.route("/api/update/run", methods=["POST"])
@editor_required
def api_run_update():
    acquired = _update_lock.acquire(blocking=False)
    if not acquired:
        return jsonify({"ok": False, "error": "An update is already in progress."}), 409

    try:
        # Require a freshly uploaded file every time
        upload = get_active_upload()
        if not upload:
            return jsonify({
                "ok": False,
                "error": "No file uploaded. Please upload a CSV/XLSX file before running pricing."
            }), 400

        result = run_update()

        # Deactivate the upload after CSV generation — user must re-upload for next run
        if result.get("status") == "updated":
            deactivate_active_upload()

        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": _sanitize_error(e)}), 500
    finally:
        _update_lock.release()


@app.route("/api/update/force", methods=["POST"])
@editor_required
def api_force_baseline():
    try:
        ibja = scrape_ibja_rates()
        save_rate(
            ibja["14kt"], ibja["18kt"],
            ibja.get("fine_gold"), ibja["session"], ibja["date"],
            rate_9kt=ibja.get("9kt"),
        )
        return jsonify({
            "ok": True,
            "message": "Baseline rate updated to current IBJA rate.",
            "rates": ibja,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": _sanitize_error(e)}), 500


# ── API: Diamond Update ──────────────────────────────────

@app.route("/api/diamond/update", methods=["POST"])
@editor_required
def api_diamond_update():
    acquired = _update_lock.acquire(blocking=False)
    if not acquired:
        return jsonify({"ok": False, "error": "An update is already in progress."}), 409

    try:
        body = request.get_json(force=True, silent=True) or {}
        i1i2 = body.get("rate_i1i2")
        si = body.get("rate_si")

        if i1i2 is None or si is None:
            return jsonify({"ok": False, "error": "Both rate_i1i2 and rate_si are required"}), 400

        upload = get_active_upload()
        if not upload:
            return jsonify({
                "ok": False,
                "error": "No file uploaded. Please upload a CSV/XLSX file before running diamond update."
            }), 400

        result = run_diamond_update(i1i2, si)

        if result.get("status") == "updated":
            deactivate_active_upload()

        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": _sanitize_error(e)}), 500
    finally:
        _update_lock.release()


# ── API: File Upload ─────────────────────────────────────

@app.route("/api/upload", methods=["POST"])
@editor_required
def api_upload_file():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file provided"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"ok": False, "error": "No file selected"}), 400

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"ok": False, "error": "Only .xlsx and .csv files allowed"}), 400

    # Validate file content matches extension
    valid, reason = _validate_file_content(f, ext)
    if not valid:
        return jsonify({"ok": False, "error": reason}), 400

    safe_name = secure_filename(f.filename)
    unique_name = f"{uuid.uuid4().hex[:8]}_{safe_name}"

    # Read bytes once so we can store in both DB and disk
    file_bytes = f.read()

    # Persist to disk (best-effort; Railway filesystem is ephemeral)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    save_path = os.path.join(UPLOAD_DIR, unique_name)
    with open(save_path, "wb") as fp:
        fp.write(file_bytes)

    # Always store in PostgreSQL so the file survives redeploys
    save_uploaded_file(unique_name, f.filename, file_bytes)

    return jsonify({
        "ok": True,
        "message": f"File '{f.filename}' uploaded and set as active source.",
        "filename": unique_name,
        "original_name": f.filename,
    })


@app.route("/api/upload/active")
@login_required
def api_active_upload():
    upload = get_active_upload()
    return jsonify({
        "ok": True,
        "upload": upload,
    })


@app.route("/api/upload/list")
@login_required
def api_list_uploads():
    """List all uploaded source files from the database."""
    uploads = get_all_uploads()
    return jsonify({"ok": True, "uploads": uploads})


@app.route("/api/upload/<filename>/download")
@login_required
def api_download_upload(filename):
    safe = os.path.basename(filename)
    if safe != filename:
        return jsonify({"ok": False, "error": "Invalid filename"}), 400
    fpath = os.path.join(UPLOAD_DIR, safe)
    if os.path.isfile(fpath):
        return send_from_directory(UPLOAD_DIR, safe, as_attachment=True)
    # Fallback: serve from PostgreSQL (needed when disk is ephemeral on Railway)
    data = get_upload_file_data(safe)
    if data:
        return send_file(io.BytesIO(data), as_attachment=True, download_name=safe)
    return jsonify({"ok": False, "error": "File not found"}), 404


@app.route("/api/upload/<filename>/delete", methods=["DELETE"])
@editor_required
def api_delete_upload(filename):
    safe = os.path.basename(filename)
    if safe != filename:
        return jsonify({"ok": False, "error": "Invalid filename"}), 400
    fpath = os.path.join(UPLOAD_DIR, safe)
    if os.path.isfile(fpath):
        os.remove(fpath)
    delete_uploaded_file_record(safe)  # removes from DB too
    return jsonify({"ok": True})


# ── API: Sheets ──────────────────────────────────────────

@app.route("/api/sheets")
@login_required
def api_list_sheets():
    sheets = []
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Sheets stored in PostgreSQL (source of truth on Railway)
    db_files = {f["filename"]: f for f in get_all_generated_files()}

    # Also pick up any disk files not yet recorded in DB
    disk_seen = set()
    for fname in os.listdir(OUTPUT_DIR):
        if fname.endswith(".csv") and not fname.startswith("~$"):
            disk_seen.add(fname)
            fpath = os.path.join(OUTPUT_DIR, fname)
            stat = os.stat(fpath)
            sheets.append({
                "filename": fname,
                "type": "updated",
                "size_kb": round(stat.st_size / 1024),
                "modified": os.path.getmtime(fpath),
            })

    # Add DB-only files (disk is ephemeral on Railway)
    for fname, f in db_files.items():
        if fname not in disk_seen:
            try:
                mtime = datetime.fromisoformat(f["timestamp"]).timestamp()
            except Exception:
                mtime = 0.0
            sheets.append({
                "filename": fname,
                "type": "updated",
                "size_kb": round(f.get("size_bytes", 0) / 1024),
                "modified": mtime,
            })

    sheets.sort(key=lambda x: x["modified"], reverse=True)

    # Attach update-log info
    logs = {log["output_file"]: log for log in get_update_logs(200)}
    for s in sheets:
        log = logs.get(s["filename"])
        if log:
            s["log"] = log

    return jsonify({"ok": True, "sheets": sheets})


@app.route("/api/sheets/<filename>/download")
@login_required
def api_download_sheet(filename):
    safe = os.path.basename(filename)
    if safe != filename:
        return jsonify({"ok": False, "error": "Invalid filename"}), 400

    if os.path.isfile(os.path.join(OUTPUT_DIR, safe)):
        return send_from_directory(OUTPUT_DIR, safe, as_attachment=True)

    # Fallback: serve from PostgreSQL (needed when disk is ephemeral on Railway)
    data = get_generated_file(safe)
    if data:
        return send_file(io.BytesIO(data), as_attachment=True, download_name=safe)

    return jsonify({"ok": False, "error": "File not found"}), 404


@app.route("/api/sheets/<filename>/delete", methods=["DELETE"])
@editor_required
def api_delete_sheet(filename):
    safe = os.path.basename(filename)
    if safe != filename:
        return jsonify({"ok": False, "error": "Invalid filename"}), 400

    found = False
    fpath = os.path.join(OUTPUT_DIR, safe)
    if os.path.isfile(fpath):
        os.remove(fpath)
        found = True
    if delete_generated_file_record(safe):
        found = True

    if not found:
        return jsonify({"ok": False, "error": "File not found"}), 404
    return jsonify({"ok": True})



# ── API: Logs ────────────────────────────────────────────

@app.route("/api/logs")
@login_required
def api_logs():
    limit = request.args.get("limit", 50, type=int)
    logs = get_update_logs(limit)
    return jsonify({"ok": True, "logs": logs})


@app.route("/api/diamond/logs")
@login_required
def api_diamond_logs():
    limit = request.args.get("limit", 50, type=int)
    logs = get_diamond_update_logs(limit)
    return jsonify({"ok": True, "logs": logs})


# ── Run ──────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)