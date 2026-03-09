import os
import re
import sys
import time
import requests
from datetime import datetime, timezone, timedelta, date
from dotenv import load_dotenv

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))

# ── Local modules ─────────────────────────────────────────────
from shopify_export import fetch_fresh_shopify_csv
from shopify_push import push_prices

# ── Environment variables ─────────────────────────────────────
FLASK_APP_URL = os.environ.get("FLASK_APP_URL", "").rstrip("/")
FLASK_EDITOR_USER = os.environ.get("FLASK_EDITOR_USERNAME")
FLASK_EDITOR_PASS = os.environ.get("FLASK_EDITOR_PASSWORD")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

RATE_WAIT_TIMEOUT_HOURS = int(os.environ.get("RATE_WAIT_TIMEOUT_HOURS", "2"))
RATE_CHECK_INTERVAL_MINUTES = int(os.environ.get("RATE_CHECK_INTERVAL_MINUTES", "5"))

# ── Rate sanity ranges (per gram) ────────────────────────────
RATE_SANITY = {
    "14kt": (
        int(os.environ.get("RATE_SANITY_14KT_MIN", "5000")),
        int(os.environ.get("RATE_SANITY_14KT_MAX", "25000")),
    ),
    "18kt": (
        int(os.environ.get("RATE_SANITY_18KT_MIN", "7000")),
        int(os.environ.get("RATE_SANITY_18KT_MAX", "35000")),
    ),
    "9kt": (
        int(os.environ.get("RATE_SANITY_9KT_MIN", "3000")),
        int(os.environ.get("RATE_SANITY_9KT_MAX", "15000")),
    ),
}


# ── Helpers ───────────────────────────────────────────────────

def _ts():
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")


def now_ist():
    return datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")


def current_session():
    """Returns 'AM' if before 2 PM IST, else 'PM'."""
    hour = datetime.now(IST).hour
    return "AM" if hour < 14 else "PM"


# ── Telegram ──────────────────────────────────────────────────

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=15)
        print(f"[{_ts()}] [Telegram] Sent: {message[:80]}...")
    except Exception as e:
        print(f"[{_ts()}] [Telegram] FAILED to send alert: {e}")


def _flask_session():
    """Create an authenticated Flask session and return (session, csrf_token)."""
    session = requests.Session()

    login_resp = session.post(
        f"{FLASK_APP_URL}/api/auth/login",
        json={"username": FLASK_EDITOR_USER, "password": FLASK_EDITOR_PASS},
        timeout=20,
    )
    if login_resp.status_code != 200:
        raise Exception(f"Flask login failed: HTTP {login_resp.status_code} — {login_resp.text[:200]}")

    # Visit index page to generate CSRF token (also deactivates stale uploads — fine before upload)
    page_resp = session.get(f"{FLASK_APP_URL}/", timeout=20, allow_redirects=True)
    csrf_token = None
    m = re.search(r'const\s+CSRF_TOKEN\s*=\s*"([a-f0-9]+)"', page_resp.text)
    if m:
        csrf_token = m.group(1)

    return session, csrf_token


# ── Stage 1 — Wait for Fresh IBJA Rates ──────────────────────

def stage1_wait_for_rates():
    session_name = current_session()
    today = date.today().strftime("%d/%m/%Y")
    print(f"\n[{_ts()}] [Stage 1] Waiting for IBJA {session_name} rates for {today}...")

    max_attempts = (RATE_WAIT_TIMEOUT_HOURS * 60) // RATE_CHECK_INTERVAL_MINUTES

    # Create authenticated session for rate checks
    flask_session = requests.Session()
    login_resp = flask_session.post(
        f"{FLASK_APP_URL}/api/auth/login",
        json={"username": FLASK_EDITOR_USER, "password": FLASK_EDITOR_PASS},
        timeout=20,
    )
    if login_resp.status_code != 200:
        raise Exception(f"Flask login failed: HTTP {login_resp.status_code}")

    for attempt in range(max_attempts):
        try:
            resp = flask_session.get(
                f"{FLASK_APP_URL}/api/rates/current",
                timeout=20,
            )
            if resp.status_code != 200:
                raise Exception(f"HTTP {resp.status_code}")

            data = resp.json()
            rates = data.get("rates", {})
            rate_date = rates.get("date", "")
            rate_session = rates.get("session", "").upper()
            rate_14kt = float(rates.get("14kt", 0))
            rate_18kt = float(rates.get("18kt", 0))
            rate_9kt = float(rates.get("9kt", 0))

            # Check freshness
            if rate_date != today:
                print(f"[{_ts()}] [Stage 1] Rate date is {rate_date}, need {today}. "
                      f"Retry {attempt+1}/{max_attempts}...")
                time.sleep(RATE_CHECK_INTERVAL_MINUTES * 60)
                continue

            if rate_session != session_name:
                print(f"[{_ts()}] [Stage 1] Rate session is {rate_session}, need {session_name}. "
                      f"Retry {attempt+1}/{max_attempts}...")
                time.sleep(RATE_CHECK_INTERVAL_MINUTES * 60)
                continue

            # Sanity check — reject obviously wrong values
            rate_values = {"14kt": rate_14kt, "18kt": rate_18kt, "9kt": rate_9kt}
            for karat, (lo, hi) in RATE_SANITY.items():
                val = rate_values[karat]
                if not (lo <= val <= hi):
                    raise Exception(
                        f"IBJA {karat.upper()} rate {val:,.0f} is outside safe range "
                        f"{lo:,}-{hi:,}. Possible scrape corruption. Aborting."
                    )

            print(f"[{_ts()}] [Stage 1] Fresh {session_name} rates confirmed: "
                  f"14KT={rate_14kt:,.0f}, 18KT={rate_18kt:,.0f}, 9KT={rate_9kt:,.0f}")
            return {
                "session": session_name,
                "rate_date": rate_date,
                "rate_14kt": rate_14kt,
                "rate_18kt": rate_18kt,
                "rate_9kt": rate_9kt,
            }

        except Exception as e:
            if "safe range" in str(e) or "Aborting" in str(e):
                raise  # Hard failure — bad data, don't retry
            print(f"[{_ts()}] [Stage 1] Error checking rates: {e}. "
                  f"Retry {attempt+1}/{max_attempts}...")
            time.sleep(RATE_CHECK_INTERVAL_MINUTES * 60)

    raise Exception(
        f"IBJA {session_name} rates not published by {now_ist()}. "
        f"Checked for {RATE_WAIT_TIMEOUT_HOURS} hours. Aborting."
    )


# ── Stage 2 — Fetch Fresh CSV from Shopify ───────────────────

def stage2_fetch_shopify_csv():
    print(f"\n[{_ts()}] [Stage 2] Fetching fresh product CSV from Shopify...")
    csv_path, row_count = fetch_fresh_shopify_csv(output_dir="uploads")
    print(f"[{_ts()}] [Stage 2] Got {row_count} variant rows.")
    return csv_path, row_count


# ── Stage 3 — Upload to Flask + Run Pricing ──────────────────

def stage3_run_pricing(csv_path):
    print(f"\n[{_ts()}] [Stage 3] Uploading to Flask app and running pricing...")

    # Health check (unauthenticated endpoint)
    try:
        health = requests.get(f"{FLASK_APP_URL}/api/auth/me", timeout=15)
        if health.status_code not in (200, 401):
            raise Exception(f"Flask app health check failed: HTTP {health.status_code}")
        print(f"[{_ts()}] [Stage 3] Flask app health check passed.")
    except requests.RequestException as e:
        raise Exception(f"Flask app is unreachable: {e}")

    # Authenticate and get CSRF token
    session, csrf_token = _flask_session()

    headers = {}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
    else:
        print(f"[{_ts()}] [Stage 3] WARNING: Could not extract CSRF token. "
              f"POST requests may fail.")

    # Upload CSV
    print(f"[{_ts()}] [Stage 3] Uploading CSV...")
    with open(csv_path, "rb") as f:
        upload_resp = session.post(
            f"{FLASK_APP_URL}/api/upload",
            files={"file": (os.path.basename(csv_path), f, "text/csv")},
            headers=headers,
            timeout=60,
        )
    if upload_resp.status_code != 200:
        raise Exception(f"CSV upload failed: HTTP {upload_resp.status_code} — "
                        f"{upload_resp.text[:200]}")
    print(f"[{_ts()}] [Stage 3] CSV uploaded successfully.")

    # Run pricing
    print(f"[{_ts()}] [Stage 3] Running pricing engine...")
    run_resp = session.post(
        f"{FLASK_APP_URL}/api/update/run",
        headers=headers,
        timeout=300,
    )
    if run_resp.status_code != 200:
        raise Exception(f"Pricing run failed: HTTP {run_resp.status_code} — "
                        f"{run_resp.text[:200]}")

    run_data = run_resp.json()
    output_file = run_data.get("output_file") or run_data.get("filename")
    if not output_file:
        raise Exception(f"Pricing run response had no output filename: {run_data}")

    variants_done = run_data.get("variants_updated", "?")
    products_done = run_data.get("products_updated", "?")
    print(f"[{_ts()}] [Stage 3] Pricing done. {variants_done} variants across "
          f"{products_done} products.")

    # Download output CSV
    print(f"[{_ts()}] [Stage 3] Downloading output CSV...")
    dl_resp = session.get(
        f"{FLASK_APP_URL}/api/sheets/{output_file}/download",
        timeout=120,
    )
    if dl_resp.status_code != 200:
        raise Exception(f"Output CSV download failed: HTTP {dl_resp.status_code}")

    local_output = f"updated_products_{datetime.now(IST).strftime('%d%b%Y_%H%M')}.csv"
    with open(local_output, "wb") as f:
        f.write(dl_resp.content)

    print(f"[{_ts()}] [Stage 3] Output CSV saved: {local_output}")
    return local_output, variants_done, products_done


# ── Stage 4 — Push Prices to Shopify ─────────────────────────

def stage4_push_prices(output_csv):
    print(f"\n[{_ts()}] [Stage 4] Pushing prices to Shopify via GraphQL...")
    success, variants, failed = push_prices(output_csv)
    return success, variants, failed


# ── Stage 5 — Telegram Notification ──────────────────────────

def stage5_notify(rates, row_count, variants_done, products_done,
                  push_success, failed_products, duration_sec,
                  metaobject_ok=True):
    minutes = int(duration_sec // 60)
    seconds = int(duration_sec % 60)
    session_name = rates["session"]
    failures = len(failed_products)

    if failures == 0:
        status_icon = "✅"
        status_line = "All products updated successfully."
    elif failures < push_success * 0.05:
        status_icon = "⚠️"
        status_line = f"{failures} products had partial failures (see below)."
    else:
        status_icon = "❌"
        status_line = f"CRITICAL: {failures} products failed to update!"

    rate_display_line = "🔢 Rate Display: Updated" if metaobject_ok else "🔢 Rate Display: Failed — check logs"

    msg = (
        f"{status_icon} <b>TaaraLaxmii Pricing Updated</b>\n"
        f"─────────────────────────\n"
        f"📅 {now_ist()}\n"
        f"🕐 Session: {session_name}\n\n"
        f"💰 <b>IBJA Rates Applied</b>\n"
        f"  18KT: ₹{rates['rate_18kt']:,.0f}/g\n"
        f"  14KT: ₹{rates['rate_14kt']:,.0f}/g\n"
        f"   9KT: ₹{rates['rate_9kt']:,.0f}/g\n\n"
        f"📦 <b>Update Stats</b>\n"
        f"  Source rows:      {row_count:,}\n"
        f"  Products updated: {products_done}\n"
        f"  Variants updated: {variants_done}\n"
        f"  Push success:     {push_success} products\n"
        f"  Push failed:      {failures} products\n\n"
        f"⏱ Duration: {minutes}m {seconds}s\n"
        f"{rate_display_line}\n\n"
        f"📋 {status_line}"
    )

    if failed_products:
        msg += "\n\n<b>Failed Products:</b>\n"
        for fp in failed_products[:10]:  # show max 10 in message
            msg += f"  • Product {fp['product_id']}: {str(fp['errors'])[:60]}\n"
        if len(failed_products) > 10:
            msg += f"  ... and {len(failed_products) - 10} more. Check Railway logs."

    send_telegram(msg)


# ── Stage 6 — Update Gold Rate Metaobject ────────────────────

def stage6_update_rate_metaobject(rates):
    from shopify_push import GRAPHQL_URL, HEADERS

    print(f"\n[{_ts()}] [Stage 6] Updating Gold Rate metaobject in Shopify...")

    mutation = """
    mutation UpdateGoldRateMetaobject($id: ID!, $fields: [MetaobjectFieldInput!]!) {
      metaobjectUpdate(id: $id, metaobject: { fields: $fields }) {
        metaobject { id }
        userErrors { field message }
      }
    }
    """

    variables = {
        "id": "gid://shopify/Metaobject/169970499905",
        "fields": [
            {"key": "rate_24kt_999", "value": str(rates["fine_gold"])},
            {"key": "rate_18kt",     "value": str(rates["rate_18kt"])},
            {"key": "rate_14kt",     "value": str(rates["rate_14kt"])},
            {"key": "rate_9kt",      "value": str(rates["rate_9kt"])},
        ],
    }

    try:
        resp = requests.post(
            GRAPHQL_URL,
            headers=HEADERS,
            json={"query": mutation, "variables": variables},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        user_errors = (data.get("data") or {}).get("metaobjectUpdate", {}).get("userErrors", [])
        if user_errors:
            err_str = "; ".join(f"{e['field']}: {e['message']}" for e in user_errors)
            print(f"[{_ts()}] [Stage 6] ⚠️  userErrors: {err_str}")
            send_telegram(
                f"⚠️ <b>Stage 6 — Rate Metaobject userErrors</b>\n"
                f"Prices were pushed successfully, but the rate display metaobject could not be updated.\n"
                f"Errors: {err_str}\nTime: {now_ist()}"
            )
            return False
        print(
            f"[{_ts()}] [Stage 6] ✅ Gold Rate metaobject updated — "
            f"24KT: {rates['fine_gold']}, 18KT: {rates['rate_18kt']}, "
            f"14KT: {rates['rate_14kt']}, 9KT: {rates['rate_9kt']}"
        )
        return True
    except Exception as e:
        print(f"[{_ts()}] [Stage 6] ⚠️  Exception: {e}")
        send_telegram(
            f"⚠️ <b>Stage 6 — Rate Metaobject Update Failed</b>\n"
            f"Prices were pushed successfully, but the rate display metaobject could not be updated.\n"
            f"Error: {e}\nTime: {now_ist()}"
        )
        return False


# ── MAIN ─────────────────────────────────────────────────────

def main():
    # ── Kill-switch: check if automation is paused ────────────────
    try:
        from database import get_automation_enabled
        if not get_automation_enabled():
            msg = (
                "⏸ <b>TaaraLaxmii Automation Paused</b>\n"
                f"Run skipped at {now_ist()}.\n"
                "Re-enable from the dashboard to resume."
            )
            send_telegram(msg)
            print(f"[{_ts()}] [Main] Automation is paused. Exiting.")
            sys.exit(0)
    except Exception as _ks_exc:
        print(f"[{_ts()}] [Main] Kill-switch check failed (continuing): {_ks_exc}")

    start_time = time.time()
    print(f"\n{'=' * 60}")
    print(f"[{_ts()}] TaaraLaxmii Automation Started — {now_ist()}")
    print(f"{'=' * 60}")

    # ── Stage 1
    try:
        rates = stage1_wait_for_rates()
    except Exception as e:
        msg = (f"⚠️ <b>TaaraLaxmii Automation Aborted — Stage 1</b>\n"
               f"Reason: {e}\nTime: {now_ist()}")
        send_telegram(msg)
        print(f"[{_ts()}] [Main] Stage 1 failed: {e}")
        sys.exit(1)

    # ── Stage 2
    try:
        csv_path, row_count = stage2_fetch_shopify_csv()
    except Exception as e:
        msg = (f"❌ <b>Automation FAILED — Stage 2 (Shopify Export)</b>\n"
               f"Error: {e}\nTime: {now_ist()}")
        send_telegram(msg)
        print(f"[{_ts()}] [Main] Stage 2 failed: {e}")
        sys.exit(1)

    # ── Stage 3
    try:
        output_csv, variants_done, products_done = stage3_run_pricing(csv_path)
    except Exception as e:
        msg = (f"❌ <b>Automation FAILED — Stage 3 (Pricing Run)</b>\n"
               f"Error: {e}\nTime: {now_ist()}")
        send_telegram(msg)
        print(f"[{_ts()}] [Main] Stage 3 failed: {e}")
        sys.exit(1)

    # ── Stage 4
    try:
        push_success, variants_pushed, failed_products = stage4_push_prices(output_csv)
    except Exception as e:
        msg = (f"❌ <b>Automation FAILED — Stage 4 (Shopify Price Push)</b>\n"
               f"Error: {e}\nTime: {now_ist()}")
        send_telegram(msg)
        print(f"[{_ts()}] [Main] Stage 4 failed: {e}")
        sys.exit(1)

    # ── Stage 6 (non-critical — runs after push, before notify)
    metaobject_ok = stage6_update_rate_metaobject(rates)

    # ── Stage 5
    duration = time.time() - start_time
    try:
        stage5_notify(rates, row_count, variants_done, products_done,
                      push_success, failed_products, duration,
                      metaobject_ok=metaobject_ok)
    except Exception as e:
        print(f"[{_ts()}] [Main] Stage 5 notification error (non-fatal): {e}")

    print(f"\n[{_ts()}] [Main] Automation complete in "
          f"{int(duration // 60)}m {int(duration % 60)}s")


if __name__ == "__main__":
    main()
