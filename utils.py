"""Shared utilities for automation.py and nightly_sync.py.

Extracts _ts(), now_ist(), send_telegram(), and _flask_session() so they
are defined once instead of duplicated across modules.
"""

import os
import re
import requests
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

FLASK_APP_URL = os.environ.get("FLASK_APP_URL", "").rstrip("/")
FLASK_EDITOR_USER = os.environ.get("FLASK_EDITOR_USERNAME")
FLASK_EDITOR_PASS = os.environ.get("FLASK_EDITOR_PASSWORD")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Support multiple recipients: comma-separated list
_TELEGRAM_CHAT_IDS = [
    cid.strip()
    for cid in (TELEGRAM_CHAT_ID or "").split(",")
    if cid.strip()
]


def _ts():
    """Formatted timestamp for log lines."""
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")


def now_ist():
    """Human-readable IST datetime string."""
    return datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")


def current_session():
    """Returns 'AM' if before 2 PM IST, else 'PM'."""
    hour = datetime.now(IST).hour
    return "AM" if hour < 14 else "PM"


def send_telegram(message):
    """Send a Telegram message to all configured chat IDs."""
    if not TELEGRAM_BOT_TOKEN or not _TELEGRAM_CHAT_IDS:
        print(f"[{_ts()}] [Telegram] Not configured — skipping.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chat_id in _TELEGRAM_CHAT_IDS:
        try:
            requests.post(url, json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
            }, timeout=15)
            print(f"[{_ts()}] [Telegram] Sent to {chat_id}: {message[:80]}...")
        except Exception as e:
            print(f"[{_ts()}] [Telegram] FAILED to send to {chat_id}: {e}")


def flask_session():
    """Create an authenticated Flask session and return (session, csrf_token).

    CSRF token is extracted from the rendered index page. Falls back to
    the /api/csrf endpoint if the regex approach fails.
    """
    sess = requests.Session()

    login_resp = sess.post(
        f"{FLASK_APP_URL}/api/auth/login",
        json={"username": FLASK_EDITOR_USER, "password": FLASK_EDITOR_PASS},
        timeout=20,
    )
    if login_resp.status_code != 200:
        raise Exception(f"Flask login failed: HTTP {login_resp.status_code} — {login_resp.text[:200]}")

    # Try dedicated CSRF endpoint first (faster, more reliable)
    csrf_token = None
    try:
        csrf_resp = sess.get(f"{FLASK_APP_URL}/api/csrf-token", timeout=10)
        if csrf_resp.status_code == 200:
            csrf_token = csrf_resp.json().get("csrf_token")
    except Exception:
        pass

    # Fallback: extract from rendered page
    if not csrf_token:
        page_resp = sess.get(f"{FLASK_APP_URL}/", timeout=20, allow_redirects=True)
        m = re.search(r'const\s+CSRF_TOKEN\s*=\s*"([a-f0-9]+)"', page_resp.text)
        if m:
            csrf_token = m.group(1)

    return sess, csrf_token
