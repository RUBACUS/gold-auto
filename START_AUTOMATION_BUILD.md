# Copilot Build Prompt — TaaraLaxmii Automation v2.0

## Message to Send (copy everything below this line)

---

Please build the full automation pipeline for my TaaraLaxmii Gold Price Updater project by strictly following the `AUTOMATION_ROADMAP.md` file that already exists in this workspace. Build it stage by stage exactly as described in the roadmap. Do not change any existing pricing formula or logic in `update_prices.py`, `scraper.py`, `database.py`, or `app.py`.

---

### What Files to Create (New Files Only)

Create exactly these four new files — nothing else:

| File | Purpose |
|---|---|
| `shopify_export.py` | Shopify GraphQL Bulk Operations export — fetches fresh product CSV directly from Shopify API |
| `shopify_push.py` | Shopify GraphQL price push — updates all variant prices grouped by product |
| `nightly_sync.py` | Nightly new-product detection — runs at 2AM IST to sync new products |
| `automation.py` | Main pipeline — orchestrates all stages, sends Telegram alerts |

Also update `requirements.txt` to add new dependencies only (do not remove existing ones).

Also create `.env.example` showing all required environment variables with empty values (safe to commit).

---

### Shopify Configuration

```
SHOPIFY_STORE=
# Your store domain — no https://, no trailing slash
# Example: mytaaralaxmiistore.myshopify.com

SHOPIFY_TOKEN=
# Admin API access token from your Shopify Custom App
# Starts with: shpat_
# Scopes required: read_products, write_products, read_product_listings, read_inventory

SHOPIFY_API_VERSION=2025-01
# Use 2025-01 — update once a year when Shopify deprecates old versions
# Shopify supports each version for ~2 years with 12-month deprecation notice
```

---

### Metafield Configuration — CONFIRMED ✅

These have been verified against the live store. Use these exact values — do not change them.

**CRITICAL ARCHITECTURE NOTE:** All metafields are stored at the **product level**, not the variant level. The GraphQL query must fetch metafields from the `products` node. During CSV conversion, product metafield values must be copied to every variant row of that product. The variant node has no metafields — do not query them there.

```
# Confirmed namespace.key for taara-laxmii.myshopify.com
METAFIELD_GOLD_14KT=custom.14kt_metal_weight
# Maps to CSV column 50: "14KT Metal Weight (product.metafields.custom.14kt_metal_weight)"

METAFIELD_GOLD_18KT=custom.18kt_metal_weight
# Maps to CSV column 51: "18KT Metal Weight (product.metafields.custom.18kt_metal_weight)"

METAFIELD_GOLD_9KT=custom.9kt_metal_weight
# Maps to CSV column 52: "9KT Metal weight (product.metafields.custom.9kt_metal_weight)"

METAFIELD_DIAMOND=custom.diamond_total_weight
# Maps to CSV column 55: "Diamond Total Weight (product.metafields.custom.diamond_total_weight)"

METAFIELD_GEMSTONE=custom.gemstone_total_weight
# Maps to CSV column 60: "Gemstone Total Weight (product.metafields.custom.gemstone_total_weight)"
```

The CSV produced by `shopify_export.py` must have these exact column headers at columns 50, 51, 52, 55, 60 so that `update_prices.py` reads them correctly without any modification:

| Column | Exact Header |
|---|---|
| 50 | `14KT Metal Weight (product.metafields.custom.14kt_metal_weight)` |
| 51 | `18KT Metal Weight (product.metafields.custom.18kt_metal_weight)` |
| 52 | `9KT Metal weight (product.metafields.custom.9kt_metal_weight)` |
| 55 | `Diamond Total Weight (product.metafields.custom.diamond_total_weight)` |
| 60 | `Gemstone Total Weight (product.metafields.custom.gemstone_total_weight)` |

The full CSV must have all 82 original columns in the correct order, plus `Variant ID` and `Product ID` appended at the end (columns 83 and 84) for the price push stage.

---

### Telegram Configuration

```
TELEGRAM_BOT_TOKEN=
# From @BotFather on Telegram
# Example: 7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxx

TELEGRAM_CHAT_ID=
# Your personal Telegram chat ID
# Find it at: https://api.telegram.org/botYOUR_TOKEN/getUpdates
# Example: 123456789
```

---

### Flask App Configuration

```
FLASK_APP_URL=
# Public Railway URL of your deployed Flask app
# Example: https://gold-auto-production.up.railway.app
# No trailing slash

FLASK_EDITOR_USERNAME=
# Editor account username (do NOT use default 'admin')

FLASK_EDITOR_PASSWORD=
# Editor account password (do NOT use default 'admin123')
```

---

### Database Configuration

```
DATABASE_URL=
# PostgreSQL connection string — auto-provided by Railway PostgreSQL service
# Example: postgresql://user:password@host:5432/railway
# This replaces the old SQLite gold_updater.db
```

---

### Schedule and Timing Configuration

```
AM_RUN_TIME_UTC=06:30
# 12:00 PM IST = 06:30 UTC

PM_RUN_TIME_UTC=11:30
# 05:00 PM IST = 11:30 UTC

NIGHTLY_SYNC_UTC=20:30
# 02:00 AM IST = 20:30 UTC (previous day)

RATE_WAIT_TIMEOUT_HOURS=2
# Hours to wait for IBJA rates before aborting the run

RATE_CHECK_INTERVAL_MINUTES=5
# How often to recheck IBJA rates while waiting

BULK_EXPORT_TIMEOUT_MINUTES=15
# Max minutes to wait for Shopify bulk export to complete

BULK_EXPORT_POLL_INTERVAL_SECONDS=10
# How often to poll Shopify for bulk export completion

MIN_EXPECTED_VARIANT_ROWS=1000
# Safety check — abort if CSV has fewer rows than this (prevents bad pricing from corrupt export)
```

---

### Rate Sanity Ranges (Corruption Detection)

```
# If IBJA returns values outside these ranges, treat as scrape corruption
# and abort immediately — do NOT run pricing with bad data
RATE_SANITY_14KT_MIN=5000
RATE_SANITY_14KT_MAX=25000
RATE_SANITY_18KT_MIN=7000
RATE_SANITY_18KT_MAX=35000
RATE_SANITY_9KT_MIN=3000
RATE_SANITY_9KT_MAX=15000
```

---

### Shopify GraphQL Push Configuration

```
PUSH_DELAY_SECONDS=0.2
# Delay between each product update call (prevents rate limiting)
# 939 products × 0.2s = ~3 minutes total push time

PUSH_MAX_RETRIES=3
# How many times to retry a failed product update before giving up

PUSH_FAILURE_THRESHOLD_PERCENT=5
# If more than this % of products fail, treat as critical failure in Telegram
```

---

### Existing Project Files (Do Not Modify)

The workspace already contains these files — do not touch them:

```
app.py              ← Flask web server with 20 routes (do not modify)
database.py         ← Database layer (update only to add PostgreSQL support)
update_prices.py    ← Pricing engine with formula (do not modify)
scraper.py          ← IBJA scraper (do not modify)
requirements.txt    ← Add new dependencies only, do not remove existing ones
Procfile            ← Do not modify
templates/          ← Do not modify
static/             ← Do not modify
AUTOMATION_ROADMAP.md ← Your reference document — follow it exactly
```

---

### Build Instructions for Copilot

1. Read `AUTOMATION_ROADMAP.md` completely before writing any code.

2. Build in this exact order — do not skip ahead:
   - `shopify_export.py` first (GraphQL Bulk Export)
   - `shopify_push.py` second (GraphQL Price Push)
   - `nightly_sync.py` third (Nightly Product Sync)
   - `automation.py` last (Main Pipeline — imports the above three)

3. For `shopify_export.py`:
   - Use GraphQL Bulk Operations API (`bulkOperationRunQuery` mutation)
   - **Fetch metafields from the `products` node — NOT the variant node.** Confirmed: all gold weights and diamond weights are product-level metafields. Variant metafields are empty.
   - The GraphQL query structure: `products → metafields { edges { node { namespace key value } } }` AND `products → variants { edges { node { id sku price ... } } }`
   
   - **CRITICAL — JSONL PARSING ARCHITECTURE:** Shopify Bulk Operations outputs a `.jsonl` file where every nested connection becomes a SEPARATE LINE — NOT nested inside the parent. This means:
     - Product lines: objects with no `__parentId` field → these are products
     - Metafield lines: objects WITH `__parentId` (= product GID) AND have `namespace` + `key` fields → these are metafields
     - Variant lines: objects WITH `__parentId` (= product GID) AND have `price` + `sku` fields → these are variants
     
     You MUST use three separate buckets when parsing:
     ```python
     products = {}           # gid → product object
     product_metafields = {} # gid → {namespace.key: value}
     variants = []           # list of variant objects
     
     for each line in jsonl:
         if "__parentId" not in obj:
             products[obj["id"]] = obj
         elif "namespace" in obj and "key" in obj:
             # metafield line
             product_metafields[obj["__parentId"]][f"{obj['namespace']}.{obj['key']}"] = obj["value"]
         else:
             # variant line
             variants.append(obj)
     
     # When building CSV rows, look up metafields like:
     mf = product_metafields.get(variant["__parentId"], {})
     gold_14kt = mf.get("custom.14kt_metal_weight", "")
     ```
     
     DO NOT try to access `obj.get("metafields", {})` on a product object — product objects in the JSONL have NO nested metafields key. All metafields come as separate lines.

   - **CRITICAL — NULL CHECK in `poll_until_complete`:** `currentBulkOperation` returns `null` (Python `None`) when no operation is running. Always check: `op = data["data"].get("currentBulkOperation")` then `if op is None: continue` BEFORE accessing `op["status"]`. Missing this check causes `TypeError: 'NoneType' object is not subscriptable`.
   
   - Add `from dotenv import load_dotenv` and `load_dotenv()` at the top of the file
   - Use `SHOPIFY_API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2025-01")` — do NOT hardcode the version
   - Poll using `currentBulkOperation` query every `BULK_EXPORT_POLL_INTERVAL_SECONDS`
   - Handle HTTP 429 by reading the `Retry-After` response header and waiting that exact duration
   - Handle HTTP 500 with exponential backoff (1s, 2s, 4s)
   - Validate row count against `MIN_EXPECTED_VARIANT_ROWS` — raise exception if fewer rows
   - The output CSV must have all 82 original Shopify export columns in the correct order, with exact header names matching the original file, plus `Variant ID` (col 83) and `Product ID` (col 84) appended at the end
   - Clean up raw `.jsonl` file after conversion even if an exception occurs (use try/finally)
   - Return `(csv_path, row_count)` tuple

4. For `shopify_push.py`:
   - Group all variants by `Product ID` from the output CSV
   - Use `productVariantsBulkUpdate` GraphQL mutation — one call per product
   - Add `PUSH_DELAY_SECONDS` sleep between each product call
   - Retry failed products up to `PUSH_MAX_RETRIES` times with exponential backoff
   - Collect all failed products with their error messages — do not stop the pipeline for partial failures
   - Return `(products_success_count, variants_updated_count, failed_products_list)`

5. For `nightly_sync.py`:
   - Fetch fresh CSV from Shopify using `shopify_export.fetch_fresh_shopify_csv()`
   - To get the current row count, download the currently active source file from `GET /api/sheets` (list files), pick the most recent one, download it, and count its rows — do NOT rely on `/api/upload/active` returning a `row_count` field as this may not exist
   - Only upload new CSV + send Telegram alert if new row count > old row count
   - If unable to determine old count, default to uploading the fresh file anyway (safe default)
   - Log quietly with no Telegram message if no new products detected

6. For `automation.py`:
   - Each stage must be its own clearly named function: `stage1_wait_for_rates()`, `stage2_fetch_shopify_csv()`, `stage3_run_pricing()`, `stage4_push_prices()`, `stage5_notify()`
   - Every stage must be wrapped in try/except
   - Every failure must call `send_telegram()` with the stage name, error message, and current IST timestamp before calling `sys.exit(1)`
   - Add timestamp to every print statement: `[2026-03-07 12:04:31] [Stage 1] Checking rates...`
   - Stage 1 must validate IBJA rate values against sanity ranges before proceeding
   - Stage 3 must do a Flask health check before attempting login
   - Stage 5 Telegram message must include: session (AM/PM), all three gold rates, source row count, products updated, variants updated, push success count, failed product count, total duration

7. All environment variables must be loaded via `python-dotenv`. Add `from dotenv import load_dotenv` and `load_dotenv()` at the top of **every new file** (`shopify_export.py`, `shopify_push.py`, `nightly_sync.py`, `automation.py`). This loads `.env` when running locally and has no effect on Railway where env vars are set as service variables. Use `os.environ.get()` for every single secret — never hardcode any value.

8. Add proper timestamped logging throughout every file. Every action, every retry, every success, every failure must be printed.

9. After writing all files, provide:
   - The exact `requirements.txt` additions needed
   - The exact Railway Build Command for each service
   - The exact Railway Start Command for each service
   - The exact Railway Cron Schedule for each cron service
   - A checklist of all Railway environment variables that must be set
   - Any one-time manual steps needed before the first run (e.g. verifying metafield keys)

---

### What NOT to Do

- Do NOT use Playwright or any browser automation
- Do NOT use Gmail, IMAP, or any email-based approach
- Do NOT read ZIP files
- Do NOT make REST API calls per variant (only per product or use GraphQL)
- Do NOT modify `update_prices.py`, `scraper.py`, `app.py`, or `templates/`
- Do NOT hardcode any credentials, tokens, URLs, or passwords in code
- Do NOT use SQLite — use `DATABASE_URL` (PostgreSQL) for any new database needs
- Do NOT install or use Playwright — it is not needed and will crash Railway free tier
- Do NOT access `obj.get("metafields")` on product objects from the JSONL — metafields are separate lines
- Do NOT access `currentBulkOperation` fields without first checking it is not None

---

### Manual Tasks You Must Do BEFORE Sending This Prompt

These are things that cannot be automated and must be done by hand first:

**URGENT — Security (Do immediately):**
- [ ] **Regenerate Shopify token** — Go to Shopify Admin → Settings → Apps → TaaraLaxmiiAutomation → API credentials tab → Regenerate token. The old token `shpat_b4fa...` was exposed and is compromised.
- [ ] **Make GitHub repo Private** — github.com/RUBACUS/gold-auto → Settings → Danger Zone → Change visibility → Private
- [ ] **Remove gold_updater.db from GitHub** — This file contains your user data and is publicly visible. Run locally: `git rm gold_updater.db && git commit -m "remove db file" && git push`

**Setup (Do before sending prompt):**
- [ ] Create Telegram Bot via @BotFather, get Bot Token and Chat ID
- [ ] Set up Railway PostgreSQL service in your Railway project
- [ ] Deploy Flask app to Railway and verify it's running at your Railway URL
- [ ] Change Flask app credentials from `admin/admin123` to strong values in Railway env vars
- [ ] Add `.env` file to `.gitignore` if not already there
- [ ] Create a `.env` file locally with all the values filled in (use `.env.example` as template after Copilot creates it)

**After Copilot builds the files:**
- [ ] Run `shopify_export.py` ONCE manually on your computer first — verify the CSV has the correct columns and correct row count (~55,000+ rows)
- [ ] Open the generated CSV, check row 2, verify columns 50–52 have gold weights (not empty), column 55 has diamond weight, column 60 has gemstone weight
- [ ] Test `shopify_push.py` on ONE product only first — change `product_variants` loop to only process 1 product, run it, check that product's price updated in Shopify
- [ ] Only after both tests pass — deploy to Railway and set cron schedules
- [ ] Monitor the FIRST automated run manually (watch Railway logs in real time)
- [ ] Verify Telegram messages arrive for both the 12PM and 5PM runs on Day 1

---