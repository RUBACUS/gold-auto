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
# Telegram config imported from utils

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


# ── Helpers (imported from shared utils) ──────────────────────
from utils import _ts, now_ist, current_session, send_telegram, flask_session








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
            rate_fine_gold = float(rates.get("fine_gold", 0))

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
                  f"14KT={rate_14kt:,.0f}, 18KT={rate_18kt:,.0f}, 9KT={rate_9kt:,.0f}, "
                  f"Fine Gold={rate_fine_gold:,.0f}")
            return {
                "session": session_name,
                "rate_date": rate_date,
                "rate_14kt": rate_14kt,
                "rate_18kt": rate_18kt,
                "rate_9kt": rate_9kt,
                "fine_gold": rate_fine_gold,
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
    session, csrf_token = flask_session()

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

    # Run pricing (async — poll until done)
    print(f"[{_ts()}] [Stage 3] Running pricing engine...")
    run_resp = session.post(
        f"{FLASK_APP_URL}/api/update/run",
        headers=headers,
        timeout=60,
    )
    if run_resp.status_code != 200:
        raise Exception(f"Pricing run failed: HTTP {run_resp.status_code} — "
                        f"{run_resp.text[:200]}")

    run_start = run_resp.json()
    task_id = run_start.get("task_id")

    if task_id:
        # Background task — poll /api/update/status/<task_id> until done
        import time as _time
        max_wait = 600   # 10 minutes
        poll_interval = 5
        elapsed = 0
        run_data = None
        while elapsed < max_wait:
            _time.sleep(poll_interval)
            elapsed += poll_interval
            status_resp = session.get(
                f"{FLASK_APP_URL}/api/update/status/{task_id}",
                timeout=30,
            )
            if status_resp.status_code != 200:
                continue
            status_data = status_resp.json()
            if status_data.get("status") == "done":
                run_data = status_data.get("result", {})
                break
            if status_data.get("status") == "error":
                err = (status_data.get("result") or {}).get("error", "Unknown error")
                raise Exception(f"Pricing task failed: {err}")
            print(f"[{_ts()}] [Stage 3] Waiting for pricing engine… ({elapsed}s)")
        if run_data is None:
            raise Exception(f"Pricing task timed out after {max_wait}s (task_id={task_id})")
    else:
        # Synchronous response (fallback)
        run_data = run_start

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
    success, variants, failed, summary = push_prices(output_csv)
    return success, variants, failed, summary


# ── Stage 5 — Telegram Notification ──────────────────────────

def stage5_notify(rates, row_count, variants_done, products_done,
                  push_success, failed_products, push_summary, duration_sec,
                  meta_ok=0, meta_total=6):
    minutes = int(duration_sec // 60)
    seconds = int(duration_sec % 60)
    session_name = rates["session"]
    failures = len(failed_products)
    total_products = (push_summary or {}).get("total_products") or (push_success + failures)
    failure_pct = (push_summary or {}).get("failure_percent")
    if failure_pct is None:
        failure_pct = (failures * 100.0 / total_products) if total_products else 0.0
    threshold_pct = (push_summary or {}).get("failure_threshold_percent", 5.0)
    is_critical = bool((push_summary or {}).get("is_critical", failure_pct > threshold_pct))

    if failures == 0:
        status_icon = "✅"
        status_line = "All products updated successfully."
    elif not is_critical:
        status_icon = "⚠️"
        status_line = (
            f"{failures} products had partial failures "
            f"({failure_pct:.2f}% of {total_products}, threshold {threshold_pct:.2f}%)."
        )
    else:
        status_icon = "❌"
        status_line = (
            f"CRITICAL: {failures} products failed to update "
            f"({failure_pct:.2f}% of {total_products}, threshold {threshold_pct:.2f}%)!"
        )

    if meta_ok == meta_total:
        rate_display_line = f"🔢 Display Rates: {meta_ok}/{meta_total} updated"
    else:
        rate_display_line = f"⚠️ Display Rates: {meta_ok}/{meta_total} updated — see logs"

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


# ── Stage 6 — Update Display Metaobjects ─────────────────────

def stage6_update_display_metaobjects(rates):
    import json as _json
    from shopify_push import _graphql_request

    # ── Metaobject GIDs (confirmed, do not change) ──
    GID_GOLD_RATE        = "gid://shopify/Metaobject/169970499905"
    GID_COLOR_STONE      = "gid://shopify/Metaobject/170368336193"
    GID_MAKING_CHARGE    = "gid://shopify/Metaobject/169319760193"
    GID_HUID             = "gid://shopify/Metaobject/169319792961"
    GID_DIAMOND_DISCOUNT = "gid://shopify/Metaobject/169813016897"
    GID_DIAMOND_CERT     = "gid://shopify/Metaobject/169319825729"

    # ── Fetch rate config from Flask ──
    print(f"\n[{_ts()}] [Stage 6] Fetching rate config from Flask...")
    _s = requests.Session()
    _s.post(
        f"{FLASK_APP_URL}/api/auth/login",
        json={"username": FLASK_EDITOR_USER, "password": FLASK_EDITOR_PASS},
        timeout=20,
    )
    config_resp = _s.get(f"{FLASK_APP_URL}/api/config", timeout=20)
    config = config_resp.json()["config"]

    mutation = """
    mutation($id: ID!, $fields: [MetaobjectFieldInput!]!) {
      metaobjectUpdate(id: $id, metaobject: {fields: $fields}) {
        metaobject { id handle }
        userErrors { field message }
      }
    }
    """

    def _update_metaobject(gid, fields_dict):
        fields_list = [{"key": k, "value": str(v)} for k, v in fields_dict.items()]
        resp = _graphql_request({"query": mutation,
                                 "variables": {"id": gid, "fields": fields_list}})
        errors = resp.get("data", {}).get("metaobjectUpdate", {}).get("userErrors", [])
        if errors:
            raise Exception(f"userErrors for {gid}: {errors}")

    tasks = [
        (
            "Gold Rate",
            GID_GOLD_RATE,
            {
                "rate_24kt_999": rates["fine_gold"],
                "rate_18kt":     rates["rate_18kt"],
                "rate_14kt":     rates["rate_14kt"],
                "rate_9kt":      rates["rate_9kt"],
            },
        ),
        (
            "Color Stone",
            GID_COLOR_STONE,
            {"rate_per_carat": config["colorstone_rate"]},
        ),
        (
            "Making Charge",
            GID_MAKING_CHARGE,
            {"making_charge": config["making_charge"]},
        ),
        (
            "HUID Cost",
            GID_HUID,
            {"huid": config["huid_per_pc"]},
        ),
        (
            "Diamond Certification",
            GID_DIAMOND_CERT,
            {"diamond_certification": config["certification"]},
        ),
        (
            "Diamond Discount",
            GID_DIAMOND_DISCOUNT,
            {
                "gh_si":    _json.dumps({"amount": f"{config['cmp_diamond_si']:.2f}",   "currency_code": "INR"}),
                "gh_i1_i2": _json.dumps({"amount": f"{config['cmp_diamond_i1i2']:.2f}", "currency_code": "INR"}),
            },
        ),
    ]

    results = {}   # name -> True/False
    for name, gid, fields in tasks:
        try:
            _update_metaobject(gid, fields)
            print(f"[{_ts()}] [Stage 6] ✅ {name} metaobject updated.")
            results[name] = True
        except Exception as e:
            print(f"[{_ts()}] [Stage 6] ⚠️  {name} failed: {e}")
            results[name] = False

    ok_count  = sum(1 for v in results.values() if v)
    fail_names = [n for n, v in results.items() if not v]

    if fail_names:
        send_telegram(
            f"⚠️ <b>Stage 6 — Metaobject Partial Failure</b>\n"
            f"Updated {ok_count}/{len(tasks)} metaobjects.\n"
            f"Failed: {', '.join(fail_names)}\n"
            f"Time: {now_ist()}"
        )

    return ok_count, len(tasks)


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
        push_success, variants_pushed, failed_products, push_summary = stage4_push_prices(output_csv)
    except Exception as e:
        msg = (f"❌ <b>Automation FAILED — Stage 4 (Shopify Price Push)</b>\n"
               f"Error: {e}\nTime: {now_ist()}")
        send_telegram(msg)
        print(f"[{_ts()}] [Main] Stage 4 failed: {e}")
        sys.exit(1)

    # ── Stage 6 (non-critical — runs after push, before notify)
    meta_ok, meta_total = stage6_update_display_metaobjects(rates)

    # ── Stage 5
    duration = time.time() - start_time
    try:
        stage5_notify(rates, row_count, variants_done, products_done,
                      push_success, failed_products, push_summary, duration,
                      meta_ok=meta_ok, meta_total=meta_total)
    except Exception as e:
        print(f"[{_ts()}] [Main] Stage 5 notification error (non-fatal): {e}")

    print(f"\n[{_ts()}] [Main] Automation complete in "
          f"{int(duration // 60)}m {int(duration % 60)}s")


if __name__ == "__main__":
    main()
