import os
import uuid
import threading
from functools import wraps

from flask import (
    Flask, jsonify, request, send_from_directory,
    render_template, session, redirect, url_for,
)
from werkzeug.utils import secure_filename

from scraper import scrape_ibja_rates
from database import (
    get_latest_rate, get_rate_history, get_update_logs, save_rate,
    get_diamond_update_logs,
    get_rate_config, save_rate_config,
    authenticate_user,
    save_uploaded_file, get_active_upload,
)
from update_prices import run_update, OUTPUT_DIR, get_source_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "taaralaxmii-gold-auto-2026")

ALLOWED_EXTENSIONS = {".xlsx", ".csv"}

# Simple lock to prevent concurrent updates
_update_lock = threading.Lock()


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

@app.route("/login")
def login_page():
    if "user" in session:
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/")
@login_required
def index():
    return render_template("index.html", user=session["user"])


# ── Auth API ─────────────────────────────────────────────

@app.route("/api/auth/login", methods=["POST"])
def api_login():
    body = request.get_json(force=True, silent=True) or {}
    username = body.get("username", "").strip()
    password = body.get("password", "")

    if not username or not password:
        return jsonify({"ok": False, "error": "Username and password required"}), 400

    user = authenticate_user(username, password)
    if not user:
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
        return jsonify({"ok": False, "error": str(e)}), 502


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

    making = float(body.get("making_charge", 2500))
    cmp_making = float(body.get("cmp_making_charge", 2500))

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
        result = run_update()
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
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
        return jsonify({"ok": False, "error": str(e)}), 500


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

    safe_name = secure_filename(f.filename)
    unique_name = f"{uuid.uuid4().hex[:8]}_{safe_name}"

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    save_path = os.path.join(UPLOAD_DIR, unique_name)
    f.save(save_path)

    save_uploaded_file(unique_name, f.filename)

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
    source = get_source_file()
    return jsonify({
        "ok": True,
        "upload": upload,
        "source_file": os.path.basename(source),
    })


@app.route("/api/upload/list")
@login_required
def api_list_uploads():
    """List all uploaded source files (date-wise)."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    uploads = []
    for fname in sorted(os.listdir(UPLOAD_DIR), reverse=True):
        if fname.startswith("~$"):
            continue
        fpath = os.path.join(UPLOAD_DIR, fname)
        stat = os.stat(fpath)
        uploads.append({
            "filename": fname,
            "size_kb": round(stat.st_size / 1024),
            "modified": os.path.getmtime(fpath),
        })
    return jsonify({"ok": True, "uploads": uploads})


# ── API: Sheets ──────────────────────────────────────────

@app.route("/api/sheets")
@login_required
def api_list_sheets():
    sheets = []
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for fname in os.listdir(OUTPUT_DIR):
        if fname.endswith(".csv") and not fname.startswith("~$"):
            fpath = os.path.join(OUTPUT_DIR, fname)
            stat = os.stat(fpath)
            sheets.append({
                "filename": fname,
                "type": "updated",
                "size_kb": round(stat.st_size / 1024),
                "modified": os.path.getmtime(fpath),
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

    return jsonify({"ok": False, "error": "File not found"}), 404


@app.route("/api/sheets/<filename>/delete", methods=["DELETE"])
@editor_required
def api_delete_sheet(filename):
    safe = os.path.basename(filename)
    if safe != filename:
        return jsonify({"ok": False, "error": "Invalid filename"}), 400

    fpath = os.path.join(OUTPUT_DIR, safe)
    if not os.path.isfile(fpath):
        return jsonify({"ok": False, "error": "File not found"}), 404

    os.remove(fpath)
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
#abc