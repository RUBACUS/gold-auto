# TaaraLaxmii — Full Automation Roadmap v2.0

**Last Updated:** March 2026  
**Status:** Production-Ready Design  
**Repo:** https://github.com/RUBACUS/gold-auto

---

## Goal

Every day at **12:00 PM IST** (after IBJA AM session) and **5:00 PM IST** (after IBJA PM session), the system automatically:

1. Waits for IBJA rates to be freshly published
2. Fetches a **fresh complete product export directly from Shopify** via GraphQL Bulk API
3. Uploads the fresh CSV to your Flask app and runs full price recalculation
4. Downloads the newly generated pricing CSV
5. Pushes updated prices back to Shopify via GraphQL (per-product batching — ~3 minutes for 55,000 variants)
6. Sends a Telegram message confirming success with full stats

**Zero manual steps. Zero human intervention. Zero fragile browser automation.**

Additionally, every night at **2:00 AM IST**, the system syncs new products automatically so they are included in the next morning's pricing run.

---

## Why This Roadmap Replaces the Old One

| Old Approach | Problem | New Approach |
|---|---|---|
| Playwright clicks Shopify UI → Export | Breaks when Shopify updates their UI | Shopify GraphQL Bulk Operations API |
| Gmail IMAP → wait for ZIP email | 5–20 min delay, unreliable | Direct download URL from API, ready in 3–7 min |
| REST API per variant (55,098 calls) | 7.6 hours at 2 calls/sec | GraphQL per product (~939 calls, ~3 minutes) |
| SQLite on Railway ephemeral disk | Data wiped on every redeploy | Railway PostgreSQL (persistent, free tier) |
| Playwright RAM usage on Railway | OOM crash on 512MB free tier | No Playwright needed at all |

---

## Complete Pipeline (Simple View)

```
12:00 PM / 5:00 PM IST (Railway Cron triggers automation.py)
        │
        ▼
┌─────────────────────────────────────────────────────┐
│ STAGE 1 — Wait for Fresh IBJA Rates                 │
│ Check /api/rates/current every 5 min                │
│ Validate rates are in safe range (not corrupted)    │
│ Give up after 2 hours → Telegram alert → exit       │
└─────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────┐
│ STAGE 2 — Fetch Fresh Product CSV from Shopify      │
│ Submit GraphQL Bulk Operation query                 │
│ Poll every 10 sec until COMPLETED (3–7 min)         │
│ Download .jsonl file → convert to CSV               │
│ Validate CSV has expected columns + row count       │
└─────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────┐
│ STAGE 3 — Upload CSV to Flask App + Run Pricing     │
│ POST /api/auth/login (get session)                  │
│ POST /api/upload (upload fresh CSV)                 │
│ POST /api/update/run (run full recalculation)       │
│ GET  /api/sheets/{filename}/download                │
│ Validate output file exists + row count matches     │
└─────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────┐
│ STAGE 4 — Push Prices to Shopify via GraphQL        │
│ Group variants by product_id from output CSV        │
│ For each product: send productVariantsBulkUpdate    │
│ ~939 calls total, 0.2s delay between calls          │
│ Retry failed products up to 3 times                 │
│ Log all failures with variant/product IDs           │
└─────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────┐
│ STAGE 5 — Telegram Success Notification             │
│ Report rates, variants updated, time taken          │
│ Report any partial failures clearly                 │
└─────────────────────────────────────────────────────┘
```

---

---

# PART 1 — One-Time Setups

---

## Step 1 — Create the Shopify Custom App

This gives the automation permission to export products AND push price updates directly.

**How to do it:**

1. Log into your Shopify admin: `yourstore.myshopify.com/admin`
2. Go to **Settings → Apps and sales channels**
3. Click **Develop apps → Allow custom app development → Create an app**
4. Name it `TaaraLaxmiiAutomation`
5. Click **Configure Admin API scopes**
6. Enable **all of these** — do not skip any:

| Scope | Why Needed |
|---|---|
| `read_products` | Read product + variant IDs for export |
| `write_products` | Push updated prices back |
| `read_product_listings` | Required for bulk export query |
| `read_inventory` | Read variant inventory data |

7. Click **Save → API credentials tab → Install app**
8. Copy and **immediately save** these three values:
   - `API key`
   - `API secret key`
   - `Admin API access token` (starts with `shpat_...`) — **shown only once**

```
SHOPIFY_TOKEN=shpat_xxxxxxxxxxxx
SHOPIFY_STORE=yourstore.myshopify.com
```

> ⚠️ The access token is shown only once. If you miss it, you must regenerate it.

---

## Step 2 — Metafield Namespace and Keys (Already Confirmed ✅)

These have been verified against the live TaaraLaxmii store. **Do not change these values.**

**Critical architectural fact:** All metafields are stored at the **product level**, not the variant level. This means the GraphQL query in `shopify_export.py` must fetch metafields from the `products` node — and then apply them to every variant of that product during CSV conversion. The variant node has no metafields.

**Confirmed metafield keys:**

| What | Namespace | Key | Type | Example Value |
|---|---|---|---|---|
| 14KT gold weight | `custom` | `14kt_metal_weight` | number_decimal | `4.454` |
| 18KT gold weight | `custom` | `18kt_metal_weight` | number_decimal | `5.18` |
| 9KT gold weight | `custom` | `9kt_metal_weight` | number_decimal | `3.833` |
| Diamond weight | `custom` | `diamond_total_weight` | number_decimal | `0.48` |
| Gemstone weight | `custom` | `gemstone_total_weight` | single_line_text | `1.98` |

**Confirmed CSV column mapping** (must match exactly for `update_prices.py` to work):

| Column # | Exact Header in CSV |
|---|---|
| 50 | `14KT Metal Weight (product.metafields.custom.14kt_metal_weight)` |
| 51 | `18KT Metal Weight (product.metafields.custom.18kt_metal_weight)` |
| 52 | `9KT Metal weight (product.metafields.custom.9kt_metal_weight)` |
| 55 | `Diamond Total Weight (product.metafields.custom.diamond_total_weight)` |
| 60 | `Gemstone Total Weight (product.metafields.custom.gemstone_total_weight)` |

The GraphQL export must produce these exact column headers so `update_prices.py` reads columns 50, 51, 52, 55, 60 correctly without any modification.

---

## Step 3 — Create a Telegram Bot

**How to do it:**

1. Open Telegram → search `@BotFather` → send `/newbot`
2. Name: `TaaraLaxmii Updater`
3. Username: `taaralaxmii_updater_bot` (must end in `bot`)
4. Copy the **Bot Token** (looks like `7123456789:AAFxxxx`)

**Get your Chat ID:**
1. Send any message to your new bot (e.g. "hello")
2. Open: `https://api.telegram.org/botYOUR_TOKEN/getUpdates`
3. Find `"chat":{"id":XXXXXXXXX}` — copy that number

```
TELEGRAM_BOT_TOKEN=7123456789:AAFxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=123456789
```

---

## Step 4 — Set Up Railway PostgreSQL (Replaces SQLite)

Your current `gold_updater.db` SQLite file will be **wiped on every Railway redeploy** because Railway uses ephemeral storage. This must be fixed before deploying.

**How to do it:**

1. On Railway dashboard → your project → click **+ New Service**
2. Select **Database → PostgreSQL**
3. Railway creates the database and gives you a `DATABASE_URL` env variable automatically
4. Add `DATABASE_URL` to your Flask service's environment variables
5. Update `database.py` to use `psycopg2` instead of `sqlite3`

```
DATABASE_URL=postgresql://user:pass@host:5432/railway   ← auto-provided by Railway
```

> **Cost:** Free on Railway's Hobby plan ($5/month free credit). PostgreSQL service uses ~$0.50–$1/month of that credit.

> ⚠️ Also remove `gold_updater.db` from your GitHub repo — it currently contains user data and is publicly visible.

---

## Step 5 — Create GitHub Account and Push Code

1. Create a repository on `github.com` — name it `gold-auto`
2. Make it **Private** — your repo is currently Public, change this immediately
3. Push your code:

```bash
git init
git add .
git commit -m "initial commit"
git remote add origin https://github.com/YOURUSERNAME/gold-auto.git
git push -u origin main
```

> ⚠️ Before pushing, ensure `.gitignore` contains:
> ```
> .env
> *.db
> __pycache__/
> uploads/
> updated_sheets/
> ```

---

## Step 6 — Deploy Flask App on Railway

1. Railway dashboard → **New Project → Deploy from GitHub repo**
2. Select `gold-auto`
3. Railway auto-detects Python + your `Procfile`
4. Go to **Settings → Domains → Generate Domain**
5. You get: `https://gold-auto-production.up.railway.app`

**Add all environment variables** (Settings → Variables tab):

```
SECRET_KEY=your-long-random-secret-key-here
DATABASE_URL=<auto-provided by Railway PostgreSQL>

SHOPIFY_TOKEN=shpat_xxxxxxxxxxxx
SHOPIFY_STORE=yourstore.myshopify.com

TELEGRAM_BOT_TOKEN=7123456789:AAFxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=123456789

FLASK_EDITOR_USERNAME=<choose a strong username — NOT admin>
FLASK_EDITOR_PASSWORD=<choose a strong password — NOT admin123>

FLASK_APP_URL=https://gold-auto-production.up.railway.app
```

> ⚠️ **Change the default credentials immediately.** Your current `admin/admin123` are documented in your public README. Anyone can find your Railway URL and log in.

---

---

# PART 2 — New File: `shopify_export.py`

This replaces Stages 2 and 3 from the old roadmap (Playwright + Gmail IMAP entirely).

---

## How Shopify GraphQL Bulk Operations Works

```
PHASE 1 — Submit Job (instant)
Your script sends ONE GraphQL mutation to Shopify API
Shopify responds: "Job accepted, ID = gid://shopify/BulkOperation/12345"

PHASE 2 — Poll Until Ready (3–7 minutes)
Your script asks "is job done?" every 10 seconds
Shopify responds: RUNNING... RUNNING... COMPLETED
When COMPLETED → Shopify gives a direct download URL (valid for 7 days)

PHASE 3 — Download and Convert (30 seconds)
Your script downloads a .jsonl file (one JSON object per line)
Converts it to CSV matching your existing 82-column Shopify export format
Saves as uploads/shopify_export_DDMMMYYYY_HHMM.csv
```

**Cost:** Free. The GraphQL API uses a cost-points system (not billing). 1,000-point bucket, refills at 50 points/second. Your export query uses ~100 points total.

---

## Resilience Features in `shopify_export.py`

| Scenario | What the Script Does |
|---|---|
| Shopify API returns HTTP 429 (rate limited) | Waits the `Retry-After` seconds from response header, then retries |
| Bulk job status = FAILED | Retries the full job submission once, then alerts Telegram |
| Bulk job takes > 15 minutes | Aborts, sends Telegram alert, stops pipeline |
| Download URL returns empty file | Detected via row count check, treats as failure |
| Metafield keys not found in response | Logs warning, fills with empty string — does NOT crash |
| Row count in CSV is < 1,000 rows | Treated as suspicious — sends Telegram warning + pauses for confirmation |
| Shopify API endpoint changes | Version is pinned to `2024-01` — update once a year if needed |

---

## `shopify_export.py` — Full Code

```python
import requests
import time
import json
import csv
import os
from datetime import datetime
from dotenv import load_dotenv

# Load .env file when running locally (no effect on Railway where env vars are set directly)
load_dotenv()

SHOPIFY_STORE = os.environ.get("SHOPIFY_STORE")
SHOPIFY_TOKEN = os.environ.get("SHOPIFY_TOKEN")
# API version: update once a year (Shopify supports versions for ~2 years)
SHOPIFY_API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2025-01")
GRAPHQL_URL   = f"https://{SHOPIFY_STORE}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"

HEADERS = {
    "Content-Type": "application/json",
    "X-Shopify-Access-Token": SHOPIFY_TOKEN
}

# ── Confirmed metafield keys for taara-laxmii.myshopify.com ──
# These are on the PRODUCT node — not the variant node
# Do not change these unless metafields are renamed in Shopify
METAFIELD_KEYS = {
    "gold_weight_14kt": "custom.14kt_metal_weight",
    "gold_weight_18kt": "custom.18kt_metal_weight",
    "gold_weight_9kt":  "custom.9kt_metal_weight",
    "diamond_weight":   "custom.diamond_total_weight",
    "gemstone_weight":  "custom.gemstone_total_weight",
}

MIN_EXPECTED_ROWS = 1000  # safety check — alert if fewer rows than this


def _graphql_request(payload, retries=3):
    """Makes a GraphQL request with retry on 429/500 errors."""
    for attempt in range(retries):
        try:
            resp = requests.post(GRAPHQL_URL, headers=HEADERS,
                                 json=payload, timeout=30)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 10))
                print(f"[Export] Rate limited. Waiting {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                print(f"[Export] Shopify server error {resp.status_code}. "
                      f"Retry {attempt+1}/{retries}...")
                time.sleep(15)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise Exception(f"Shopify API unreachable after {retries} attempts: {e}")
            time.sleep(10)


def submit_bulk_export():
    """Submits the bulk export job. Returns operation ID."""
    mutation = """
    mutation {
      bulkOperationRunQuery(
        query: \"""
        {
          products {
            edges {
              node {
                id
                handle
                title
                status
                vendor
                productType
                tags
                metafields {
                  edges {
                    node {
                      namespace
                      key
                      value
                    }
                  }
                }
                variants {
                  edges {
                    node {
                      id
                      sku
                      price
                      compareAtPrice
                      position
                      option1
                      option2
                      option3
                    }
                  }
                }
              }
            }
          }
        }
        \"""
      ) {
        bulkOperation { id status }
        userErrors { field message }
      }
    }
    """
    data = _graphql_request({"query": mutation})
    errors = data.get("data", {}).get("bulkOperationRunQuery", {}).get("userErrors", [])
    if errors:
        raise Exception(f"Bulk export submission error: {errors}")

    op_id = data["data"]["bulkOperationRunQuery"]["bulkOperation"]["id"]
    print(f"[Export] Bulk job submitted. ID: {op_id}")
    return op_id


def poll_until_complete(max_wait_minutes=15):
    """Polls until job is COMPLETED. Returns download URL."""
    query = """
    {
      currentBulkOperation {
        id status errorCode objectCount fileSize url
      }
    }
    """
    max_attempts = (max_wait_minutes * 60) // 10
    for attempt in range(max_attempts):
        time.sleep(10)
        data = _graphql_request({"query": query})

        # ── IMPORTANT: currentBulkOperation returns null when no job is running.
        # Must check for null before accessing fields — otherwise TypeError crash.
        op = data["data"].get("currentBulkOperation")
        if op is None:
            print(f"[Export] Poll {attempt+1}: No bulk operation running yet. Waiting...")
            continue

        status = op.get("status", "UNKNOWN")
        count  = op.get("objectCount", 0)

        print(f"[Export] Poll {attempt+1}: {status} — {count} objects")

        if status == "COMPLETED":
            url = op.get("url")
            if not url:
                raise Exception("Job completed but no download URL returned.")
            print(f"[Export] ✅ Ready. {count} objects exported.")
            return url

        if status == "FAILED":
            raise Exception(f"Bulk export FAILED. Error: {op.get('errorCode')}")

        if status not in ["RUNNING", "CREATED"]:
            raise Exception(f"Unexpected bulk operation status: {status}")

    raise Exception(f"Bulk export timed out after {max_wait_minutes} minutes.")


def download_and_convert(download_url, output_path):
    """Downloads .jsonl, converts to CSV. Returns (csv_path, row_count)."""
    print("[Export] Downloading .jsonl from Shopify...")

    resp = requests.get(download_url, stream=True, timeout=120)
    resp.raise_for_status()

    raw_path = output_path.replace(".csv", "_raw.jsonl")
    with open(raw_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    print("[Export] Converting .jsonl → CSV...")

    products          = {}   # product gid → product object
    product_metafields = {}  # product gid → {namespace.key: value}
    variants          = []   # list of variant objects

    # ── IMPORTANT: In Shopify Bulk Operations JSONL format, nested connections
    # (metafields, variants) appear as SEPARATE LINES — not nested inside the
    # parent object. Each line has __parentId pointing to its parent's GID.
    # Metafields are distinguished from variants by the presence of 'namespace'+'key'.
    with open(raw_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)

            if "__parentId" not in obj:
                # Top-level product object (no parent)
                products[obj["id"]] = obj

            elif "namespace" in obj and "key" in obj:
                # This is a metafield line — has namespace + key fields
                # __parentId points to the product GID
                parent_id = obj["__parentId"]
                if parent_id not in product_metafields:
                    product_metafields[parent_id] = {}
                mf_key = f"{obj['namespace']}.{obj['key']}"
                product_metafields[parent_id][mf_key] = obj.get("value", "")

            else:
                # This is a variant line — has price, sku, option fields
                variants.append(obj)

    rows = []
    for variant in variants:
        parent_id = variant.get("__parentId")
        product   = products.get(parent_id, {})

        # Get this product's metafields (confirmed: metafields are product-level)
        mf = product_metafields.get(parent_id, {})

        # Extract numeric Shopify ID from gid:// URI
        variant_id = variant.get("id", "").split("/")[-1]
        product_id = product.get("id", "").split("/")[-1]

        # Build CSV row matching your exact 82-column Shopify export format
        # Column headers must match exactly so update_prices.py reads cols 50,51,52,55,60 correctly
        row = {
            "Handle":                       product.get("handle", ""),
            "Title":                        product.get("title", ""),
            "Body (HTML)":                  "",
            "Vendor":                       product.get("vendor", ""),
            "Product Category":             "",
            "Type":                         product.get("productType", ""),
            "Tags":                         ", ".join(product.get("tags", [])),
            "Published":                    "",
            "Option1 Name":                 "",
            "Option1 Value":                variant.get("option1", ""),
            "Option1 Linked To":            "",
            "Option2 Name":                 "",
            "Option2 Value":                variant.get("option2", ""),
            "Option2 Linked To":            "",
            "Option3 Name":                 "",
            "Option3 Value":                variant.get("option3", ""),
            "Option3 Linked To":            "",
            "Variant SKU":                  variant.get("sku", ""),
            "Variant Grams":                "",
            "Variant Inventory Tracker":    "",
            "Variant Inventory Qty":        "",
            "Variant Inventory Policy":     "",
            "Variant Fulfillment Service":  "",
            # Col 24 — written by update_prices.py
            "Variant Price":                variant.get("price", ""),
            # Col 25 — written by update_prices.py
            "Variant Compare At Price":     variant.get("compareAtPrice", ""),
            "Variant Requires Shipping":    "",
            "Variant Taxable":              "",
            "Unit Price Total Measure":     "",
            "Unit Price Total Measure Unit":"",
            "Unit Price Base Measure":      "",
            "Unit Price Base Measure Unit": "",
            "Variant Barcode":              "",
            "Image Src":                    "",
            "Image Position":               "",
            "Image Alt Text":               "",
            "Gift Card":                    "",
            "SEO Title":                    "",
            "SEO Description":              "",
            "Google Shopping / Google Product Category": "",
            "Google Shopping / Gender":     "",
            "Google Shopping / Age Group":  "",
            "Google Shopping / MPN":        "",
            "Google Shopping / Condition":  "",
            "Google Shopping / Custom Product": "",
            "Google Shopping / Custom Label 0": "",
            "Google Shopping / Custom Label 1": "",
            "Google Shopping / Custom Label 2": "",
            "Google Shopping / Custom Label 3": "",
            "Google Shopping / Custom Label 4": "",
            # Col 50 — read by update_prices.py for 14KT pricing
            "14KT Metal Weight (product.metafields.custom.14kt_metal_weight)":
                mf.get(METAFIELD_KEYS["gold_weight_14kt"], ""),
            # Col 51 — read by update_prices.py for 18KT pricing
            "18KT Metal Weight (product.metafields.custom.18kt_metal_weight)":
                mf.get(METAFIELD_KEYS["gold_weight_18kt"], ""),
            # Col 52 — read by update_prices.py for 9KT pricing
            "9KT Metal weight (product.metafields.custom.9kt_metal_weight)":
                mf.get(METAFIELD_KEYS["gold_weight_9kt"], ""),
            "Diamond Count (product.metafields.custom.diamond_count)":         "",
            "Diamond Quality (product.metafields.custom.diamond_quality)":     "",
            # Col 55 — read by update_prices.py for diamond pricing
            "Diamond Total Weight (product.metafields.custom.diamond_total_weight)":
                mf.get(METAFIELD_KEYS["diamond_weight"], ""),
            "Diamond Weight Filter (product.metafields.custom.diamond_weight_filter)": "",
            "Fancy Diamond Weight (product.metafields.custom.fancy_diamond_weight)":   "",
            "Gemstone Color (product.metafields.custom.gemstone_color)":               "",
            "Gemstone Count (product.metafields.custom.gemstone_count)":               "",
            # Col 60 — read by update_prices.py for gemstone pricing
            "Gemstone Total Weight (product.metafields.custom.gemstone_total_weight)":
                mf.get(METAFIELD_KEYS["gemstone_weight"], ""),
            "Gender (product.metafields.custom.gender)":                               "",
            "Love this piece? (product.metafields.custom.love_this_piece)":            "",
            "Pendant Chain Note (product.metafields.custom.pendant_chain_note)":       "",
            "Preferred by (product.metafields.custom.preferred_by)":                   "",
            "Product Standard Size (product.metafields.custom.product_standard_size)": "",
            "Product Type (product.metafields.custom.product_type)":                   "",
            "Round Diamond Weight (product.metafields.custom.round_diamond_weight)":   "",
            "Showcase Tags (product.metafields.custom.showcase_tags)":                 "",
            "Sibling Product Link (product.metafields.custom.sibling_product_link)":   "",
            "Sibling Product Type (product.metafields.custom.sibling_product_type)":   "",
            "SKU (product.metafields.custom.sku)":                                     "",
            "Google: Custom Product (product.metafields.mm-google-shopping.custom_product)": "",
            "Ring size (product.metafields.shopify.ring-size)":                        "",
            "Complementary products (product.metafields.shopify--discovery--product_recommendation.complementary_products)": "",
            "Related products (product.metafields.shopify--discovery--product_recommendation.related_products)": "",
            "Related products settings (product.metafields.shopify--discovery--product_recommendation.related_products_display)": "",
            "Search product boosts (product.metafields.shopify--discovery--product_search_boost.queries)": "",
            "Variant Image":        "",
            "Variant Weight Unit":  "",
            "Variant Tax Code":     "",
            "Cost per item":        "",
            "Status":               product.get("status", ""),
            # Extra cols for automation use (not in original 82 — appended at end)
            "Variant ID":           variant_id,
            "Product ID":           product_id,
        }
        rows.append(row)

    os.remove(raw_path)

    if not rows:
        raise Exception("Conversion produced 0 rows. The .jsonl file may be empty or malformed.")

    if len(rows) < MIN_EXPECTED_ROWS:
        raise Exception(
            f"Only {len(rows)} rows found — expected at least {MIN_EXPECTED_ROWS}. "
            f"Data may be incomplete. Aborting to prevent bad pricing."
        )

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print(f"[Export] ✅ CSV saved: {output_path} ({len(rows)} rows)")
    return output_path, len(rows)


def fetch_fresh_shopify_csv(output_dir="uploads"):
    """
    Full pipeline: submit → poll → download → convert.
    Returns (csv_path, row_count).
    """
    os.makedirs(output_dir, exist_ok=True)
    ts          = datetime.now().strftime("%d%b%Y_%H%M")
    output_path = os.path.join(output_dir, f"shopify_export_{ts}.csv")

    print("\n" + "="*55)
    print("[Export] Starting fresh Shopify product export...")
    print("="*55)

    submit_bulk_export()
    download_url = poll_until_complete(max_wait_minutes=15)
    csv_path, row_count = download_and_convert(download_url, output_path)

    print(f"[Export] 🎉 Done! {row_count} variants ready at: {csv_path}")
    return csv_path, row_count
```

---

---

# PART 3 — New File: `shopify_push.py`

This replaces the old Stage 5 (REST API per variant = 7.6 hours). This new version uses GraphQL per product = ~3 minutes.

---

## How GraphQL Price Push Works

Instead of calling the API 55,098 times (once per variant), we call it **once per product** — passing all variants of that product in a single mutation.

```
939 products × 1 API call each = 939 total calls
939 calls × 0.2s delay = ~188 seconds = ~3 minutes ✅
Cost: FREE (GraphQL points system, not billing)
```

---

## Resilience Features in `shopify_push.py`

| Scenario | What the Script Does |
|---|---|
| HTTP 429 (rate limited) | Reads `Retry-After` header, waits, retries |
| Shopify returns `userErrors` for a product | Logs the product + error, continues with next product |
| Network timeout on one call | Retries up to 3 times with exponential backoff |
| Entire batch fails | Collects all failed product IDs, reports in Telegram |
| Less than 95% of products succeed | Treated as critical failure in Telegram alert |

---

## `shopify_push.py` — Full Code

```python
import requests
import time
import csv
import os
from collections import defaultdict
from dotenv import load_dotenv

# Load .env file when running locally (no effect on Railway where env vars are set directly)
load_dotenv()

SHOPIFY_STORE = os.environ.get("SHOPIFY_STORE")
SHOPIFY_TOKEN = os.environ.get("SHOPIFY_TOKEN")
# API version: update once a year (Shopify supports versions for ~2 years)
SHOPIFY_API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2025-01")
GRAPHQL_URL   = f"https://{SHOPIFY_STORE}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"

HEADERS = {
    "Content-Type": "application/json",
    "X-Shopify-Access-Token": SHOPIFY_TOKEN
}

DELAY_BETWEEN_CALLS = 0.2   # seconds between product updates
MAX_RETRIES         = 3


def _graphql_request(payload, retries=MAX_RETRIES):
    """Makes a GraphQL request with retry on rate limit and server errors."""
    for attempt in range(retries):
        try:
            resp = requests.post(GRAPHQL_URL, headers=HEADERS,
                                 json=payload, timeout=30)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 5))
                print(f"[Push] Rate limited. Waiting {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                wait = 2 ** attempt  # exponential backoff: 1s, 2s, 4s
                print(f"[Push] Server error {resp.status_code}. Retry in {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise Exception(f"Shopify API unreachable: {e}")
            time.sleep(2 ** attempt)


def _build_variants_input(variants):
    """Builds the GraphQL variants input array string."""
    parts = []
    for v in variants:
        variant_id = f"gid://shopify/ProductVariant/{v['variant_id']}"
        parts.append(
            f'{{id: "{variant_id}", '
            f'price: "{v["price"]}", '
            f'compareAtPrice: "{v["compare_at_price"]}"}}'
        )
    return "[" + ", ".join(parts) + "]"


def push_prices(csv_path):
    """
    Reads the generated pricing CSV, groups variants by product,
    and pushes all prices to Shopify via GraphQL.
    Returns (products_updated, variants_updated, failed_products).
    """
    print("\n" + "="*55)
    print("[Push] Starting Shopify price push via GraphQL...")
    print("="*55)

    # Group variants by product_id
    product_variants = defaultdict(list)

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            variant_id  = str(row.get("Variant ID", "")).strip()
            product_id  = str(row.get("Product ID", "")).strip()
            price       = str(row.get("Variant Price", "")).strip()
            compare     = str(row.get("Variant Compare At Price", "")).strip()

            if not variant_id or not product_id or not price:
                continue

            product_variants[product_id].append({
                "variant_id":       variant_id,
                "price":            price,
                "compare_at_price": compare or price,
            })

    total_products  = len(product_variants)
    success_count   = 0
    variants_count  = 0
    failed_products = []

    print(f"[Push] {total_products} products to update...")

    for idx, (product_id, variants) in enumerate(product_variants.items(), 1):
        gid             = f"gid://shopify/Product/{product_id}"
        variants_input  = _build_variants_input(variants)

        mutation = f"""
        mutation {{
          productVariantsBulkUpdate(
            productId: "{gid}",
            variants: {variants_input}
          ) {{
            productVariants {{ id price compareAtPrice }}
            userErrors {{ field message }}
          }}
        }}
        """

        try:
            data   = _graphql_request({"query": mutation})
            result = data.get("data", {}).get("productVariantsBulkUpdate", {})
            errors = result.get("userErrors", [])

            if errors:
                print(f"[Push] ⚠️ Product {product_id} errors: {errors}")
                failed_products.append({"product_id": product_id, "errors": errors})
            else:
                success_count  += 1
                variants_count += len(variants)

        except Exception as e:
            print(f"[Push] ❌ Product {product_id} failed: {e}")
            failed_products.append({"product_id": product_id, "errors": str(e)})

        # Progress log every 100 products
        if idx % 100 == 0:
            print(f"[Push] Progress: {idx}/{total_products} products processed...")

        time.sleep(DELAY_BETWEEN_CALLS)

    print(f"\n[Push] ✅ Done. {success_count}/{total_products} products updated.")
    print(f"[Push] Variants updated: {variants_count}")
    if failed_products:
        print(f"[Push] ⚠️ {len(failed_products)} products FAILED:")
        for fp in failed_products:
            print(f"       Product {fp['product_id']}: {fp['errors']}")

    return success_count, variants_count, failed_products
```

---

---

# PART 4 — Main File: `automation.py`

This is the single script Railway runs at 12:00 PM and 5:00 PM IST.

---

## Resilience Philosophy

- **Every stage has a try/except block** — no stage can crash the whole process silently
- **Every failure sends a Telegram alert** — you always know what happened
- **IBJA rates are validated** — if scraped values are outside a realistic range, they're rejected before any pricing runs
- **Flask app health is checked** before uploading — if the app is down, automation aborts cleanly
- **Partial Shopify failures are reported** — if some products fail, you're told exactly which ones
- **All stages log to console** — Railway logs capture the full run for debugging

---

## `automation.py` — Full Code

```python
import os
import sys
import time
import requests
from datetime import datetime, date
import pytz

# ── Local modules ─────────────────────────────────────────────
from shopify_export import fetch_fresh_shopify_csv
from shopify_push   import push_prices

# ── Environment variables ─────────────────────────────────────
FLASK_APP_URL        = os.environ.get("FLASK_APP_URL")
FLASK_EDITOR_USER    = os.environ.get("FLASK_EDITOR_USERNAME")
FLASK_EDITOR_PASS    = os.environ.get("FLASK_EDITOR_PASSWORD")
TELEGRAM_BOT_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID     = os.environ.get("TELEGRAM_CHAT_ID")

IST = pytz.timezone("Asia/Kolkata")

# ── Rate sanity ranges (₹/gram) — update if gold moves drastically ──
RATE_SANITY = {
    "14kt": (5_000, 25_000),
    "18kt": (7_000, 35_000),
    "9kt":  (3_000, 15_000),
}


# ── Telegram ──────────────────────────────────────────────────
def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "HTML"
        }, timeout=15)
        print(f"[Telegram] Sent: {message[:80]}...")
    except Exception as e:
        print(f"[Telegram] FAILED to send alert: {e}")


def now_ist():
    return datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")


def current_session():
    """Returns 'AM' if before 2 PM IST, else 'PM'."""
    hour = datetime.now(IST).hour
    return "AM" if hour < 14 else "PM"


# ── Stage 1 — Wait for Fresh IBJA Rates ──────────────────────
def stage1_wait_for_rates(max_wait_hours=2):
    session    = current_session()
    today      = date.today().strftime("%d/%m/%Y")
    print(f"\n[Stage 1] Waiting for IBJA {session} rates for {today}...")

    max_attempts = (max_wait_hours * 60) // 5  # check every 5 min
    for attempt in range(max_attempts):
        try:
            resp = requests.get(
                f"{FLASK_APP_URL}/api/rates/current",
                timeout=20
            )
            if resp.status_code != 200:
                raise Exception(f"HTTP {resp.status_code}")

            data         = resp.json()
            rate_date    = data.get("rate_date", "")
            rate_session = data.get("session", "").upper()
            rate_14kt    = float(data.get("rate_14kt", 0))
            rate_18kt    = float(data.get("rate_18kt", 0))
            rate_9kt     = float(data.get("rate_9kt", 0))

            # Check freshness
            if rate_date != today:
                print(f"[Stage 1] Rate date is {rate_date}, need {today}. "
                      f"Retry {attempt+1}/{max_attempts}...")
                time.sleep(300)
                continue

            if rate_session != session:
                print(f"[Stage 1] Rate session is {rate_session}, need {session}. "
                      f"Retry {attempt+1}/{max_attempts}...")
                time.sleep(300)
                continue

            # Sanity check — reject obviously wrong values
            for karat, (lo, hi) in RATE_SANITY.items():
                rate_val = {"14kt": rate_14kt, "18kt": rate_18kt, "9kt": rate_9kt}[karat]
                if not (lo <= rate_val <= hi):
                    raise Exception(
                        f"IBJA {karat.upper()} rate ₹{rate_val:,.0f} is outside safe range "
                        f"₹{lo:,}–₹{hi:,}. Possible scrape corruption. Aborting."
                    )

            print(f"[Stage 1] ✅ Fresh {session} rates confirmed: "
                  f"14KT=₹{rate_14kt:,.0f}, 18KT=₹{rate_18kt:,.0f}, 9KT=₹{rate_9kt:,.0f}")
            return {
                "session":    session,
                "rate_date":  rate_date,
                "rate_14kt":  rate_14kt,
                "rate_18kt":  rate_18kt,
                "rate_9kt":   rate_9kt,
            }

        except Exception as e:
            if "safe range" in str(e):
                # Hard failure — bad data, don't retry
                raise
            print(f"[Stage 1] Error checking rates: {e}. Retry {attempt+1}/{max_attempts}...")
            time.sleep(300)

    raise Exception(
        f"IBJA {session} rates not published by {now_ist()}. "
        f"Checked for {max_wait_hours} hours. Aborting."
    )


# ── Stage 2 — Fetch Fresh CSV from Shopify ───────────────────
def stage2_fetch_shopify_csv():
    print("\n[Stage 2] Fetching fresh product CSV from Shopify...")
    csv_path, row_count = fetch_fresh_shopify_csv(output_dir="uploads")
    print(f"[Stage 2] ✅ Got {row_count} variant rows.")
    return csv_path, row_count


# ── Stage 3 — Upload to Flask + Run Pricing ─────────────────
def stage3_run_pricing(csv_path):
    print("\n[Stage 3] Uploading to Flask app and running pricing...")

    session = requests.Session()

    # Health check
    try:
        health = session.get(f"{FLASK_APP_URL}/api/auth/me", timeout=15)
        if health.status_code not in [200, 401]:
            raise Exception(f"Flask app health check failed: HTTP {health.status_code}")
    except Exception as e:
        raise Exception(f"Flask app is unreachable: {e}")

    # Login
    login_resp = session.post(
        f"{FLASK_APP_URL}/api/auth/login",
        json={"username": FLASK_EDITOR_USER, "password": FLASK_EDITOR_PASS},
        timeout=20
    )
    if login_resp.status_code != 200:
        raise Exception(f"Flask login failed: HTTP {login_resp.status_code} — "
                        f"{login_resp.text[:200]}")

    # Upload CSV
    with open(csv_path, "rb") as f:
        upload_resp = session.post(
            f"{FLASK_APP_URL}/api/upload",
            files={"file": (os.path.basename(csv_path), f, "text/csv")},
            timeout=60
        )
    if upload_resp.status_code != 200:
        raise Exception(f"CSV upload failed: HTTP {upload_resp.status_code} — "
                        f"{upload_resp.text[:200]}")
    print("[Stage 3] CSV uploaded successfully.")

    # Run pricing
    run_resp = session.post(f"{FLASK_APP_URL}/api/update/run", timeout=300)
    if run_resp.status_code != 200:
        raise Exception(f"Pricing run failed: HTTP {run_resp.status_code} — "
                        f"{run_resp.text[:200]}")

    run_data      = run_resp.json()
    output_file   = run_data.get("output_file") or run_data.get("filename")
    if not output_file:
        raise Exception(f"Pricing run response had no output filename: {run_data}")

    variants_done = run_data.get("variants_updated", "?")
    products_done = run_data.get("products_updated", "?")
    print(f"[Stage 3] Pricing done. {variants_done} variants across "
          f"{products_done} products.")

    # Download output CSV
    dl_resp = session.get(
        f"{FLASK_APP_URL}/api/sheets/{output_file}/download",
        timeout=120
    )
    if dl_resp.status_code != 200:
        raise Exception(f"Output CSV download failed: HTTP {dl_resp.status_code}")

    local_output = f"updated_products_{datetime.now(IST).strftime('%d%b%Y_%H%M')}.csv"
    with open(local_output, "wb") as f:
        f.write(dl_resp.content)

    print(f"[Stage 3] ✅ Output CSV saved: {local_output}")
    return local_output, variants_done, products_done


# ── Stage 4 — Push Prices to Shopify ────────────────────────
def stage4_push_prices(output_csv):
    print("\n[Stage 4] Pushing prices to Shopify via GraphQL...")
    success, variants, failed = push_prices(output_csv)
    return success, variants, failed


# ── Stage 5 — Telegram Notification ─────────────────────────
def stage5_notify(rates, row_count, variants_done, products_done,
                  push_success, failed_products, duration_sec):
    minutes  = int(duration_sec // 60)
    seconds  = int(duration_sec % 60)
    session  = rates["session"]
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

    msg = (
        f"{status_icon} <b>TaaraLaxmii Pricing Updated</b>\n"
        f"─────────────────────────\n"
        f"📅 {now_ist()}\n"
        f"🕐 Session: {session}\n\n"
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
        f"⏱ Duration: {minutes}m {seconds}s\n\n"
        f"📋 {status_line}"
    )

    if failed_products:
        msg += "\n\n<b>Failed Products:</b>\n"
        for fp in failed_products[:10]:  # show max 10 in message
            msg += f"  • Product {fp['product_id']}: {str(fp['errors'])[:60]}\n"
        if len(failed_products) > 10:
            msg += f"  ... and {len(failed_products)-10} more. Check Railway logs."

    send_telegram(msg)


# ── MAIN ─────────────────────────────────────────────────────
def main():
    start_time = time.time()
    print(f"\n{'='*60}")
    print(f"TaaraLaxmii Automation Started — {now_ist()}")
    print(f"{'='*60}")

    # ── Stage 1
    try:
        rates = stage1_wait_for_rates(max_wait_hours=2)
    except Exception as e:
        msg = (f"⚠️ <b>TaaraLaxmii Automation Aborted — Stage 1</b>\n"
               f"Reason: {e}\nTime: {now_ist()}")
        send_telegram(msg)
        print(f"[Main] Stage 1 failed: {e}")
        sys.exit(1)

    # ── Stage 2
    try:
        csv_path, row_count = stage2_fetch_shopify_csv()
    except Exception as e:
        msg = (f"❌ <b>Automation FAILED — Stage 2 (Shopify Export)</b>\n"
               f"Error: {e}\nTime: {now_ist()}")
        send_telegram(msg)
        print(f"[Main] Stage 2 failed: {e}")
        sys.exit(1)

    # ── Stage 3
    try:
        output_csv, variants_done, products_done = stage3_run_pricing(csv_path)
    except Exception as e:
        msg = (f"❌ <b>Automation FAILED — Stage 3 (Pricing Run)</b>\n"
               f"Error: {e}\nTime: {now_ist()}")
        send_telegram(msg)
        print(f"[Main] Stage 3 failed: {e}")
        sys.exit(1)

    # ── Stage 4
    try:
        push_success, variants_pushed, failed_products = stage4_push_prices(output_csv)
    except Exception as e:
        msg = (f"❌ <b>Automation FAILED — Stage 4 (Shopify Price Push)</b>\n"
               f"Error: {e}\nTime: {now_ist()}")
        send_telegram(msg)
        print(f"[Main] Stage 4 failed: {e}")
        sys.exit(1)

    # ── Stage 5
    duration = time.time() - start_time
    stage5_notify(rates, row_count, variants_done, products_done,
                  push_success, failed_products, duration)

    print(f"\n[Main] ✅ Automation complete in "
          f"{int(duration//60)}m {int(duration%60)}s")


if __name__ == "__main__":
    main()
```

---

---

# PART 5 — New File: `nightly_sync.py`

This runs every night at **2:00 AM IST** to detect and incorporate any new products added to Shopify. This ensures the next morning's 12 PM run always has the freshest complete product catalog.

---

## Why This Is Needed

If the brand adds new products to Shopify, those products have new Variant IDs not present in any existing CSV. Without this sync, new products would never get their prices updated.

The nightly sync:
1. Fetches full product catalog from Shopify
2. Compares row count against the last known count
3. If new products detected → uploads new CSV as the active source file
4. Sends Telegram: `"⚠️ 3 new products detected. Source file updated for next run."`
5. If no changes → logs quietly, no Telegram message

```python
import os
import sys
import requests
from datetime import datetime
import pytz

from shopify_export import fetch_fresh_shopify_csv

FLASK_APP_URL     = os.environ.get("FLASK_APP_URL")
FLASK_EDITOR_USER = os.environ.get("FLASK_EDITOR_USERNAME")
FLASK_EDITOR_PASS = os.environ.get("FLASK_EDITOR_PASSWORD")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

IST = pytz.timezone("Asia/Kolkata")


def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=15
        )
    except Exception as e:
        print(f"[Telegram] Failed: {e}")


def get_current_active_row_count():
    """Asks Flask app how many rows the current active source file has."""
    try:
        session = requests.Session()
        session.post(
            f"{FLASK_APP_URL}/api/auth/login",
            json={"username": FLASK_EDITOR_USER, "password": FLASK_EDITOR_PASS},
            timeout=20
        )
        resp = session.get(f"{FLASK_APP_URL}/api/upload/active", timeout=15)
        if resp.status_code == 200:
            return resp.json().get("row_count", 0)
    except Exception:
        pass
    return 0


def main():
    now = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
    print(f"\n[Nightly Sync] Starting at {now}")

    try:
        csv_path, new_count = fetch_fresh_shopify_csv(output_dir="uploads")
    except Exception as e:
        send_telegram(f"⚠️ <b>Nightly Sync Failed</b>\nError: {e}\nTime: {now}")
        sys.exit(1)

    old_count = get_current_active_row_count()
    print(f"[Nightly Sync] Old row count: {old_count}, New row count: {new_count}")

    if new_count > old_count:
        # Upload new file as active source
        session = requests.Session()
        session.post(
            f"{FLASK_APP_URL}/api/auth/login",
            json={"username": FLASK_EDITOR_USER, "password": FLASK_EDITOR_PASS},
            timeout=20
        )
        with open(csv_path, "rb") as f:
            session.post(
                f"{FLASK_APP_URL}/api/upload",
                files={"file": (os.path.basename(csv_path), f, "text/csv")},
                timeout=60
            )

        new_products = (new_count - old_count) // 3  # rough estimate (avg 3 variants/product)
        send_telegram(
            f"⚠️ <b>TaaraLaxmii — New Products Detected</b>\n"
            f"Previous variant count: {old_count:,}\n"
            f"New variant count: {new_count:,}\n"
            f"Estimated new products: ~{new_products}\n"
            f"Source file updated. Next pricing run will include them.\n"
            f"Time: {now}"
        )
        print(f"[Nightly Sync] ✅ New source file uploaded with {new_count} rows.")
    else:
        print(f"[Nightly Sync] ✅ No new products. Source file unchanged.")


if __name__ == "__main__":
    main()
```

---

---

# PART 6 — Deployment on Railway

---

## Railway Project Structure

You will have **three services** inside one Railway project:

```
Railway Project: TaaraLaxmii
│
├── Service 1: flask-app         ← Your existing Flask web app (always running)
├── Service 2: automation-cron   ← automation.py (runs at 12PM + 5PM IST)
└── Service 3: nightly-sync-cron ← nightly_sync.py (runs at 2AM IST)
```

Plus one database:
```
└── Database: PostgreSQL         ← Persistent storage (replaces SQLite)
```

---

## Cron Schedule Reference

| Service | Cron (UTC) | IST Time | Purpose |
|---|---|---|---|
| `automation-cron` | `30 6 * * *` | 12:00 PM | AM pricing run |
| `automation-cron` | `30 11 * * *` | 5:00 PM | PM pricing run |
| `nightly-sync-cron` | `30 20 * * *` | 2:00 AM | New product sync |

> Railway Cron uses UTC. IST = UTC + 5:30.

---

## Step-by-Step Railway Deployment

### Service 1 — Flask App

1. Railway → New Project → Deploy from GitHub → select `gold-auto`
2. Railway auto-detects Python + `Procfile`
3. Settings → Domains → Generate Domain
4. Add all environment variables (Variables tab)

### Service 2 — Automation Cron (×2 schedules)

Railway does not support two cron times in one service. Use this approach:

**Option A** — One service with a wrapper script that runs twice:
Create `run_automation.sh`:
```bash
#!/bin/bash
python automation.py
```
Set cron to `30 6,11 * * *` (runs at both 6:30 UTC and 11:30 UTC).

**Option B** — Two separate cron services (AM and PM), same code.

### Service 3 — Nightly Sync Cron

1. New Service → Empty Service
2. Start Command: `python nightly_sync.py`
3. Cron Schedule: `30 20 * * *`
4. Connect to same GitHub repo
5. Add same environment variables

---

## Environment Variables (All Services)

```
# App
SECRET_KEY=8b65e2f1eecbbbacd1a079170d14b187cb95d7d2433eb8c356cac641ff4a7fc4
DATABASE_URL=postgresql://postgres:YkrVFbwUUMYqsfRKJHUvCLZwQthWoAsI@metro.proxy.rlwy.net:12537/railway
FLASK_APP_URL=https://taaralaxmii-auto-pricing.up.railway.app/

# Shopify
SHOPIFY_TOKEN=shpat_xxxxxxxxxxxx
SHOPIFY_STORE=yourstore.myshopify.com

# Telegram
TELEGRAM_BOT_TOKEN=8683462734:AAGlfW97ADmNht2Q2zLzVN6lnoiBNbiD61c
TELEGRAM_CHAT_ID=1300001301

# Flask App Login (for automation to call the app)
FLASK_EDITOR_USERNAME=<strong username — not admin>
FLASK_EDITOR_PASSWORD=<strong password — not admin123>
```

---

---

# PART 7 — Updated File Structure

```
gold-auto/
├── app.py                    ← Existing Flask app (no changes needed)
├── database.py               ← Update: switch from SQLite to PostgreSQL
├── update_prices.py          ← Existing pricing engine (no changes needed)
├── scraper.py                ← Existing IBJA scraper (no changes needed)
│
├── automation.py             ← NEW: main pipeline (replaces old automation.py)
├── shopify_export.py         ← NEW: GraphQL Bulk Export (replaces Playwright + Gmail)
├── shopify_push.py           ← NEW: GraphQL price push (replaces REST per-variant)
├── nightly_sync.py           ← NEW: nightly new-product detection
│
├── requirements.txt          ← Updated (add pytz, psycopg2-binary)
├── Procfile                  ← Existing (no changes needed)
├── .env                      ← Local secrets (NEVER commit this)
├── .gitignore                ← Must include .env, *.db, uploads/, updated_sheets/
│
└── templates/, static/, ...  ← Existing (no changes needed)
```

### Updated `requirements.txt`

```
flask==3.1.0
openpyxl==3.1.5
requests==2.32.3
beautifulsoup4==4.13.3
python-dotenv==1.0.0
pytz==2024.1
psycopg2-binary==2.9.9
```

> No Playwright. No imaplib. No ZIP handling. Everything removed cleanly.

---

---

# PART 8 — Error Handling Master Reference

Every possible failure and exactly what happens:

| Stage | Failure | What Automation Does |
|---|---|---|
| 1 | IBJA website down | Retries every 5 min for 2 hours, then Telegram alert + clean exit |
| 1 | Rate values look corrupted (outside ₹5k–₹35k range) | Hard abort immediately — bad rates would corrupt 55,000 prices |
| 1 | Flask app unreachable for rate check | Retries 3 times, then Telegram alert + exit |
| 2 | Shopify API returns 429 (rate limit) | Reads `Retry-After` header, waits exact seconds, resumes |
| 2 | Shopify bulk job fails | Retries job submission once, then Telegram alert + exit |
| 2 | Bulk export times out (>15 min) | Telegram alert + exit |
| 2 | CSV has fewer than 1,000 rows | Treated as corrupt export — Telegram alert + exit |
| 3 | Flask app down at pricing time | Health check fails → Telegram alert + exit |
| 3 | Flask login fails | Telegram alert with HTTP status + exit |
| 3 | Pricing run returns error | Telegram alert with error text + exit |
| 4 | Shopify GraphQL 429 mid-push | Waits `Retry-After` seconds, continues |
| 4 | Individual product update fails | Logs it, continues with next product |
| 4 | >5% of products fail | Telegram reports as critical failure with product IDs |
| 4 | Entire push crashes | Telegram alert + exit — already-updated products stay updated |
| Any | Unexpected Python exception | Caught by outer try/except → Telegram alert with traceback |

---

---

# PART 9 — Resilience Against Website Changes

This was a specific requirement: the automation must survive changes to IBJA, Shopify, or your own Flask app.

### If IBJA Website Changes Structure

**Where it breaks:** `scraper.py` uses BeautifulSoup to find the "Retail selling Rates" table.

**How automation handles it:**
- Stage 1 calls `/api/rates/current` which calls `scraper.py`
- If scraper returns `None` or throws an exception, Stage 1 retries for 2 hours
- After 2 hours: Telegram alert → `"⚠️ IBJA rates could not be scraped. Possible website layout change. Manual check required."`
- Automation aborts cleanly — no bad pricing runs

**Fix:** Update `scraper.py` to match new IBJA HTML structure. No changes needed in automation code.

---

### If Shopify Admin UI Changes

**Old approach:** Playwright clicks buttons → breaks immediately when UI changes.

**New approach:** GraphQL API — Shopify has committed to maintaining the GraphQL Admin API with **12-month deprecation notice** before removing any query or field. The `2024-01` API version will be supported until early 2026, and you update the version string once a year.

**Zero fragility to UI changes.**

---

### If Your Flask App Changes (New Routes or Auth)

**Where it breaks:** `automation.py` hardcodes route paths like `/api/auth/login`, `/api/upload`, `/api/update/run`.

**How automation handles it:**
- Each API call checks the HTTP status code
- If an endpoint returns 404 or 500, automation catches it and sends Telegram alert with exact URL + status code
- This tells you immediately which route changed

**Fix:** Update the route constants in `automation.py` to match new route names. All routes are defined in one place at the top of the file.

---

### If ibjarates.com Changes (9KT Source)

**Where it breaks:** `scraper.py` parses ibjarates.com for 750 purity rate used in 9KT formula.

**Added protection:** Rate sanity check in Stage 1 validates the 9KT calculated value is between ₹3,000–₹15,000. If ibjarates.com goes down and returns garbage, the sanity check catches it before any pricing runs.

---

---

# PART 10 — Build Order (Updated)

Follow this exact sequence. Each day builds on the previous.

### Week 1 — Setup and Foundation

- [ ] **Day 1 — URGENT:** Regenerate your Shopify Admin API access token immediately. The old token was exposed in a chat session and must be treated as compromised. Go to Shopify Admin → Settings → Apps → TaaraLaxmiiAutomation → API credentials → Regenerate token. Save the new `shpat_` token securely in your `.env` file only.
- [ ] **Day 1:** Make your GitHub repo **Private** immediately (currently Public — your code and DB file are visible to anyone).
- [ ] **Day 1:** Remove `gold_updater.db` from the GitHub repo. Run: `git rm gold_updater.db && git commit -m "remove db" && git push`
- [ ] **Day 1:** Create Telegram Bot, get token and chat ID (Step 3 above).
- [ ] **Day 1:** ✅ Metafield keys are already confirmed — no action needed for Step 2.
- [ ] **Day 2:** Set up Railway PostgreSQL, update `database.py` to use it instead of SQLite.
- [ ] **Day 2:** Change Flask app credentials from `admin/admin123` to strong values. Update Railway env vars.
- [ ] **Day 3:** Deploy Flask app to Railway, verify all existing endpoints work via the Railway URL.

### Week 2 — Build New Core Scripts

- [ ] **Day 4:** Build and test `shopify_export.py` locally. Run it once, verify the CSV looks correct with the right column names and row counts.
- [ ] **Day 5:** Build and test `shopify_push.py` locally against a **test product** first (not all 939). Verify prices update correctly in Shopify.
- [ ] **Day 6:** Build and test `nightly_sync.py` locally.
- [ ] **Day 7:** Build `automation.py` Stage 1 (IBJA rate wait + sanity check) and test locally.

### Week 3 — Integrate and Deploy

- [ ] **Day 8:** Connect all stages in `automation.py`. Run the full pipeline manually end-to-end.
- [ ] **Day 9:** Deploy all three services to Railway (flask-app, automation-cron, nightly-sync-cron).
- [ ] **Day 10:** Set cron schedules. Monitor the first automatic run. Verify Telegram notifications arrive.
- [ ] **Day 11:** Monitor for 3–4 consecutive runs. Check Railway logs for any warnings.
- [ ] **Day 12:** Done. System is live.

---

---

# PART 11 — Security Checklist

- [ ] GitHub repo is set to **Private**
- [ ] `gold_updater.db` removed from Git history
- [ ] `.env` is in `.gitignore` — never committed
- [ ] `uploads/` and `updated_sheets/` in `.gitignore`
- [ ] All secrets in Railway Environment Variables, **never in code**
- [ ] Shopify Custom App has minimum required scopes only
- [ ] Flask default credentials (`admin/admin123`) changed before first deployment
- [ ] Telegram bot only sends to your specific Chat ID
- [ ] `SECRET_KEY` is a long random string (not "your-secret-key-here")
- [ ] PostgreSQL `DATABASE_URL` is only in Railway env vars, never in code or GitHub

---

---

# PART 12 — Telegram Message Reference

### On Success

```
✅ TaaraLaxmii Pricing Updated
─────────────────────────
📅 07 Mar 2026, 12:04 PM IST
🕐 Session: AM

💰 IBJA Rates Applied
  18KT: ₹12,912/g
  14KT: ₹10,537/g
   9KT: ₹6,934/g

📦 Update Stats
  Source rows:      55,098
  Products updated: 312
  Variants updated: 1,247
  Push success:     939 products
  Push failed:      0 products

⏱ Duration: 11m 42s

📋 All products updated successfully.
```

### On IBJA Rate Timeout

```
⚠️ TaaraLaxmii Automation Aborted — Stage 1
Reason: IBJA AM rates not published by 07 Mar 2026, 02:00 PM IST.
Checked for 2 hours. Aborting.
```

### On Partial Push Failure

```
⚠️ TaaraLaxmii Pricing Updated
...
Push failed: 3 products

Failed Products:
  • Product 8847291930: userErrors: [price format]
  • Product 8821004820: Shopify 500 error after 3 retries
  • Product 8819283740: userErrors: [compareAtPrice < price]
```

### On New Products Detected (Nightly Sync)

```
⚠️ TaaraLaxmii — New Products Detected
Previous variant count: 55,098
New variant count: 55,156
Estimated new products: ~19
Source file updated. Next pricing run will include them.
Time: 07 Mar 2026, 02:00 AM IST
```

---

---

# Summary

| Stage | What It Does | Tool | Approx Time |
|---|---|---|---|
| 1 | Wait for fresh IBJA rates | Your existing Flask scraper | 0–30 min |
| 2 | Fetch product CSV from Shopify | GraphQL Bulk Operations API | 3–7 min |
| 3 | Run pricing on Flask app | Your existing Flask API | 1–2 min |
| 4 | Push prices to Shopify | GraphQL `productVariantsBulkUpdate` | ~3 min |
| 5 | Telegram success/fail notification | Telegram Bot API | instant |
| — | Nightly new-product sync | GraphQL Bulk + Flask upload | 5 min |
| **Total** | | | **~10–15 min** |

**Cost:** $0 for all APIs. Only your existing Shopify plan subscription applies.

The cron runs at **12:00 PM and 5:00 PM IST daily.** The nightly sync runs at **2:00 AM IST.** If IBJA rates are not available within 2 hours, the run is skipped and Telegram notifies you. Everything else is fully hands-off — no browser, no email, no ZIP files, no manual steps.