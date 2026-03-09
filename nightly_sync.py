import os
import sys
import csv
import io
import requests
from dotenv import load_dotenv

load_dotenv()

from shopify_export import fetch_fresh_shopify_csv
from utils import _ts, now_ist as _now_ist, send_telegram, flask_session, FLASK_APP_URL





def get_current_active_row_count():
    """
    Determines current variant row count by downloading the most recent
    generated sheet from the Flask app and counting CSV rows.
    Falls back to 0 if unable to determine.
    """
    try:
        session, _ = flask_session()

        # List generated sheets
        resp = session.get(f"{FLASK_APP_URL}/api/sheets", timeout=15)
        if resp.status_code != 200:
            print(f"[{_ts()}] [Nightly Sync] Could not list sheets: HTTP {resp.status_code}")
            return 0

        sheets = resp.json().get("sheets", [])
        if not sheets:
            print(f"[{_ts()}] [Nightly Sync] No generated sheets found.")
            return 0

        # Sheets are sorted by modified desc; pick the most recent
        most_recent = sheets[0]["filename"]
        print(f"[{_ts()}] [Nightly Sync] Checking row count from: {most_recent}")

        dl_resp = session.get(
            f"{FLASK_APP_URL}/api/sheets/{most_recent}/download",
            timeout=120,
        )
        if dl_resp.status_code != 200:
            print(f"[{_ts()}] [Nightly Sync] Could not download sheet: HTTP {dl_resp.status_code}")
            return 0

        # Count CSV data rows (excluding header)
        reader = csv.reader(io.StringIO(dl_resp.text))
        row_count = sum(1 for _ in reader) - 1  # subtract header
        print(f"[{_ts()}] [Nightly Sync] Current row count from latest sheet: {row_count}")
        return max(row_count, 0)

    except Exception as e:
        print(f"[{_ts()}] [Nightly Sync] Error getting row count: {e}")
        return 0


def main():
    now = _now_ist()
    print(f"\n[{_ts()}] [Nightly Sync] Starting at {now}")

    # Fetch fresh CSV from Shopify
    try:
        csv_path, new_count = fetch_fresh_shopify_csv(output_dir="uploads")
    except Exception as e:
        send_telegram(
            f"<b>Nightly Sync Failed</b>\nError: {e}\nTime: {now}"
        )
        print(f"[{_ts()}] [Nightly Sync] Export failed: {e}")
        sys.exit(1)

    # Get current row count from most recent generated sheet
    old_count = get_current_active_row_count()
    print(f"[{_ts()}] [Nightly Sync] Old row count: {old_count}, New row count: {new_count}")

    # If unable to determine old count (0), upload anyway as safe default
    if new_count > old_count:
        try:
            session, csrf_token = flask_session()

            headers = {}
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token

            with open(csv_path, "rb") as f:
                upload_resp = session.post(
                    f"{FLASK_APP_URL}/api/upload",
                    files={"file": (os.path.basename(csv_path), f, "text/csv")},
                    headers=headers,
                    timeout=60,
                )

            if upload_resp.status_code != 200:
                raise Exception(f"Upload failed: HTTP {upload_resp.status_code} — {upload_resp.text[:200]}")

            new_products_est = max((new_count - old_count) // 3, 0)

            if old_count > 0:
                send_telegram(
                    f"<b>TaaraLaxmii — New Products Detected</b>\n"
                    f"Previous variant count: {old_count:,}\n"
                    f"New variant count: {new_count:,}\n"
                    f"Estimated new products: ~{new_products_est}\n"
                    f"Source file updated. Next pricing run will include them.\n"
                    f"Time: {now}"
                )
            else:
                send_telegram(
                    f"<b>TaaraLaxmii — Source File Updated</b>\n"
                    f"Could not determine previous count.\n"
                    f"New variant count: {new_count:,}\n"
                    f"Source file uploaded as safe default.\n"
                    f"Time: {now}"
                )

            print(f"[{_ts()}] [Nightly Sync] New source file uploaded with {new_count} rows.")

        except Exception as e:
            send_telegram(
                f"<b>Nightly Sync — Upload Failed</b>\nError: {e}\nTime: {now}"
            )
            print(f"[{_ts()}] [Nightly Sync] Upload failed: {e}")
            sys.exit(1)
    else:
        print(f"[{_ts()}] [Nightly Sync] No new products. Source file unchanged.")


if __name__ == "__main__":
    main()
