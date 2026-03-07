# TaaraLaxmii — Gold Price Updater & Full Automation System

An end-to-end automated pricing system for a Shopify jewellery store. Every day at **12:00 PM IST** and **5:00 PM IST**, the system automatically scrapes live gold rates from IBJA, exports the complete product catalog directly from Shopify via GraphQL, recalculates every variant price using a full component-wise formula, pushes the updated prices back to Shopify, and sends a Telegram confirmation — with **zero manual steps**.

> **Store:** TaaraLaxmii — `taara-laxmii.myshopify.com`
> **Scale:** ~939 products, ~55,000+ variant rows per run
> **Deployed on:** Railway (Flask web app + two cron services + PostgreSQL)

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Full Automation Pipeline](#3-full-automation-pipeline)
4. [Flask Web App](#4-flask-web-app)
5. [Price Formula](#5-price-formula)
6. [9KT Rate Calculation](#6-9kt-rate-calculation)
7. [Shopify Metafield Mapping](#7-shopify-metafield-mapping)
8. [CSV Column Structure](#8-csv-column-structure)
9. [Database Schema](#9-database-schema)
10. [REST API Reference](#10-rest-api-reference)
11. [File-by-File Explanation](#11-file-by-file-explanation)
12. [Folder Structure](#12-folder-structure)
13. [Web Dashboard Guide](#13-web-dashboard-guide)
14. [Tech Stack](#14-tech-stack)
15. [Railway Deployment](#15-railway-deployment)
16. [Environment Variables](#16-environment-variables)
17. [Local Development Setup](#17-local-development-setup)
18. [Security Notes](#18-security-notes)
19. [Troubleshooting](#19-troubleshooting)

---

## 1. Project Overview

| Aspect | Detail |
|---|---|
| **Purpose** | Fully automated gold + diamond jewellery price updates for Shopify |
| **Gold Purities** | 9KT, 14KT, 18KT |
| **Price Columns** | Variant Price (col 24) + Compare At Price (col 25) |
| **Data Sources** | ibja.co (14KT, 18KT, Fine Gold 999) + ibjarates.com (750 purity) |
| **Shopify Integration** | GraphQL Bulk Operations API (export) + `productVariantsBulkUpdate` (push) |
| **Notifications** | Telegram bot sends success/failure alerts after every run |
| **Database** | PostgreSQL (Railway) — persists across redeploys |
| **File Formats** | CSV and XLSX (input and output) |
| **Authentication** | Session-based login with editor / viewer roles |

### Why This Architecture Exists

| Old Approach | Problem | Current Approach |
|---|---|---|
| Playwright clicks Shopify UI to export | Breaks when Shopify updates their UI | Shopify GraphQL Bulk Operations API |
| Gmail IMAP -> wait for ZIP email | 5-20 min delay, unreliable | Direct download URL from API, ready in 3-7 min |
| REST API call per variant (55,000+ calls) | 7+ hours at 2 calls/sec | GraphQL per product (~939 calls) |
| SQLite on Railway ephemeral disk | Data wiped on every redeploy | Railway PostgreSQL (persistent) |
| Playwright RAM usage on Railway | OOM crash on 512MB free tier | No browser automation at all |

---

## 2. Architecture

### High-Level System Architecture

```
+---------------------------------------------------------------------+
|                         RAILWAY CLOUD                               |
|                                                                     |
|  +---------------------+   +------------------+  +--------------+  |
|  |  Service 1          |   |  Service 2        |  |  Service 3   |  |
|  |  flask-app          |   |  automation-cron  |  |  nightly-sync|  |
|  |  (always running)   |   |  (12PM + 5PM IST) |  |  (2AM IST)   |  |
|  |                     |   |                   |  |              |  |
|  |  app.py             |   |  automation.py    |  | nightly_sync |  |
|  |  Gunicorn server    |   |  -> shopify_export|  |  .py         |  |
|  |  PostgreSQL client  |   |  -> shopify_push  |  |              |  |
|  +----------+----------+   +--------+----------+  +------+-------+  |
|             |                       |                     |          |
|             +----------------+------+-----------+---------+          |
|                              |                                       |
|                     +--------v--------+                              |
|                     |  PostgreSQL DB  |                              |
|                     |  (persistent)   |                              |
|                     +-----------------+                              |
+---------------------------------------------------------------------+
          |                          |                    |
          v                          v                    v
   +-------------+         +------------------+   +--------------+
   |  ibja.co    |         |  Shopify GraphQL |   |  Telegram    |
   |  ibjarates  |         |  Admin API       |   |  Bot API     |
   |  .com       |         |  (export + push) |   |              |
   +-------------+         +------------------+   +--------------+
```

### Component Interaction Diagram

```
automation.py (Main Orchestrator)
|
+-- stage1_wait_for_rates()
|     +-- GET /api/rates/current --> Flask app --> scraper.py --> ibja.co
|                                                             --> ibjarates.com
|
+-- stage2_fetch_shopify_csv()
|     +-- shopify_export.fetch_fresh_shopify_csv()
|           +-- bulkOperationRunQuery mutation --> Shopify GraphQL API
|           +-- poll currentBulkOperation (every 10s)
|           +-- download .jsonl -> parse -> convert to CSV
|
+-- stage3_run_pricing()
|     +-- POST /api/auth/login --> Flask app
|     +-- POST /api/upload     --> Flask app --> database.py (saves to PostgreSQL)
|     +-- POST /api/update/run --> Flask app --> update_prices.py (price formula)
|     +-- GET  /api/sheets/*/download --> Flask app
|
+-- stage4_push_prices()
|     +-- shopify_push.push_prices()
|           +-- productVariantsBulkUpdate mutation (x939 calls) --> Shopify
|
+-- stage5_notify()
      +-- Telegram Bot API --> Your phone
```

---

## 3. Full Automation Pipeline

The automation runs **twice daily** (12PM IST + 5PM IST) and nightly (2AM IST). Each run follows 5 stages.

> **Note on weekends and public holidays:** IBJA does not publish rates on Saturdays, Sundays, or Indian public holidays. On those days, Stage 1 will wait for up to 2 hours, then send a Telegram alert and exit cleanly. This is expected behaviour — no pricing run happens on non-trading days.

### Pipeline Flow (12PM / 5PM Runs)

```
Railway Cron triggers automation.py
              |
              v
+------------------------------------------------+
| STAGE 1 -- Wait for Fresh IBJA Rates           |
|                                                |
| * Logs in to Flask app                         |
| * Calls GET /api/rates/current every 5 min     |
| * Checks: rate_date == today AND session==AM/PM|
| * Validates values against sanity ranges:      |
|   14KT: Rs.5,000-Rs.25,000                     |
|   18KT: Rs.7,000-Rs.35,000                     |
|    9KT: Rs.3,000-Rs.15,000                     |
| * If outside range -> ABORT (prevents corrupt) |
| * Times out after 2 hours -> Telegram alert    |
| * On weekends/holidays: waits full 2 hrs then  |
|   exits cleanly. No action needed from you.    |
+------------------------------------------------+
              |
              v
+------------------------------------------------+
| STAGE 2 -- Fetch Fresh Product CSV from Shopify|
|                                                |
| * Submits GraphQL bulkOperationRunQuery        |
| * Polls currentBulkOperation every 10 seconds  |
| * Takes 3-7 minutes to complete                |
| * Downloads .jsonl file from Shopify CDN       |
| * Parses JSONL:                                |
|   - Lines without __parentId -> products       |
|   - Lines with namespace+key -> metafields     |
|   - All other lines with __parentId -> variants|
| * Builds 84-column CSV                         |
| * Validates row count >= 1,000                 |
| * Deletes raw .jsonl file                      |
| Typical time: 3-7 minutes                      |
+------------------------------------------------+
              |
              v
+------------------------------------------------+
| STAGE 3 -- Upload CSV + Run Pricing via Flask  |
|                                                |
| * Health checks Flask app                      |
| * Authenticates (POST /api/auth/login)         |
| * Extracts CSRF token from page HTML           |
| * Uploads fresh CSV (POST /api/upload)         |
| * Triggers pricing (POST /api/update/run)      |
|   -> scraper.py scrapes live IBJA rates        |
|   -> update_prices.py recalculates all prices  |
|   -> output CSV saved in updated_sheets/       |
| * Downloads the output CSV                     |
| Typical time: 1-2 minutes                      |
+------------------------------------------------+
              |
              v
+------------------------------------------------+
| STAGE 4 -- Push Prices to Shopify via GraphQL  |
|                                                |
| * Reads output CSV, groups variants by Product |
| * For each product (~939 total):               |
|   -> sends productVariantsBulkUpdate mutation  |
|   -> 0.2s minimum delay between calls          |
|   -> each call takes 1-3s (Shopify processing) |
|   -> retry up to 3 times on failure            |
| * Collects all failures without stopping       |
| Typical time: 15-25 minutes for all 939        |
| (actual observed: ~21 minutes on 07 Mar 2026)  |
+------------------------------------------------+
              |
              v
+------------------------------------------------+
| STAGE 5 -- Telegram Notification               |
|                                                |
| Sends message with:                            |
| * IST timestamp + session (AM/PM)              |
| * All three applied gold rates                 |
| * Source row count, products + variants updated|
| * Push success count, failed product count     |
| * Total duration                               |
| * List of any failed product IDs               |
+------------------------------------------------+
```

**Total expected duration per run: 20–35 minutes.** This is normal. The push stage (Stage 4) dominates the time because each GraphQL mutation takes 1–3 seconds on Shopify's side per product, independent of the 0.2s sleep between calls.

### Nightly Sync Flow (2AM IST)

```
Railway Cron triggers nightly_sync.py
              |
              v
+------------------------------------------------+
| Fetch fresh product catalog from Shopify       |
| (full export -- same as Stage 2 above)         |
+------------------------------------------------+
              |
              v
+------------------------------------------------+
| Compare new row count vs most recent sheet     |
|                                                |
| If new_count > old_count:                      |
|   * Upload new CSV to Flask app as active src  |
|   * Send Telegram: "N new products detected"  |
|                                                |
| Else:                                          |
|   * Log quietly, no Telegram message           |
+------------------------------------------------+
```

### Error Handling

| Stage | Failure | What Happens |
|---|---|---|
| 1 | IBJA website down | Retry every 5 min for 2 hours -> Telegram + clean exit |
| 1 | Weekend / public holiday (no rates) | Waits 2 hours -> Telegram alert -> clean exit. Normal behaviour. |
| 1 | Rate values outside sanity range | Hard abort immediately — bad rates would corrupt 55K prices |
| 1 | Flask app unreachable | Retries, then Telegram alert + exit |
| 2 | Shopify API 429 (rate limit) | Reads `Retry-After` header, waits that exact duration |
| 2 | Bulk job fails | Telegram alert + exit |
| 2 | Export times out (>15 min) | Telegram alert + exit |
| 2 | CSV has <1,000 rows | Treated as corrupt export — Telegram + exit |
| 3 | Flask app down at pricing time | Health check fails -> Telegram + exit |
| 3 | Login fails | Telegram with HTTP status + exit |
| 3 | Pricing run returns error | Telegram with error text + exit |
| 4 | GraphQL 429 mid-push | Waits `Retry-After`, continues |
| 4 | Individual product update fails | Logs it, continues with next product |
| 4 | >5% of products fail | Telegram reports as critical failure |
| Any | Unexpected Python exception | Caught by outer try/except -> Telegram + exit |

### Telegram Success Message Example

```
TaaraLaxmii Pricing Updated
---------------------------------
07 Mar 2026, 12:04 PM IST
Session: AM

IBJA Rates Applied
  18KT: Rs.12,912/g
  14KT: Rs.10,537/g
   9KT: Rs.6,934/g

Update Stats
  Source rows:      55,098
  Products updated: 939
  Variants updated: 55,098
  Push success:     939 products
  Push failed:      0 products

Duration: 22m 57s

All products updated successfully.
```

---

## 4. Flask Web App

The Flask web app serves two purposes: a **manual dashboard** for the operator and an **internal API** that the automation scripts call programmatically.

### Routes Overview

```
GET  /              -> Main dashboard (requires login)
GET  /login         -> Login page

POST /api/auth/login    -> Authenticate, start session
POST /api/auth/logout   -> Destroy session
GET  /api/auth/me       -> Current session info

GET  /api/rates/current -> Live-scrape IBJA rates (calls scraper.py)
GET  /api/rates/stored  -> Last applied baseline
GET  /api/rates/history -> Paginated rate history

GET  /api/config        -> All 12 editable rate fields
POST /api/config        -> Save rate chart changes (editor only)

POST /api/update/run    -> Run full pricing recalculation (editor only)
POST /api/update/force  -> Set current rate as baseline (editor only)

POST /api/upload        -> Upload source CSV/XLSX (editor only)
GET  /api/upload/active -> Active source file info
GET  /api/upload/list   -> All uploaded source files

GET  /api/sheets                    -> List generated output files
GET  /api/sheets/<file>/download    -> Download output file
DELETE /api/sheets/<file>/delete    -> Delete output file (editor only)

GET  /api/logs          -> Pricing update history
GET  /api/diamond/logs  -> Diamond update history
```

### Security Features

- **CSRF protection** on all POST/PUT/DELETE routes (token in session + `X-CSRF-Token` header)
- **Rate limiting** on `/api/auth/login` — max 5 attempts per 5 minutes per IP
- **File content validation** — rejects binary files renamed to `.csv`
- **Security headers** on all responses: `X-Frame-Options`, `X-Content-Type-Options`, `CSP`, `Referrer-Policy`
- **Role-based access** — viewer role cannot trigger any writes
- **Session cookies** — `HttpOnly`, `SameSite=Lax`, `Secure` in production (HTTPS only)
- **Concurrent update lock** — threading lock prevents two simultaneous pricing runs

> **Important:** Session cookies have the `Secure` flag set, which means they only transmit over HTTPS. Testing locally over `http://localhost` will show 401 errors after login — this is expected. Always test automation scripts against the Railway HTTPS URL, not localhost.

### Roles

| Capability | Editor | Viewer |
|---|---|---|
| View live rates, history, logs | Yes | Yes |
| Download generated files | Yes | Yes |
| Run Pricing | Yes | No |
| Upload source file | Yes | No |
| Edit rate charts | Yes | No |
| Set baseline | Yes | No |

---

## 5. Price Formula

Every variant price is calculated **from scratch** using the full component formula:

```
Price = ceil(gold_wt  x gold_rate)
      + ceil(diamond_wt x diamond_rate)
      + ceil(gold_wt  x making_charge)
      + huid_per_pc
      + ceil(diamond_wt x certification)
      + ceil(gem_wt   x colorstone_rate)
```

Where:
- **`gold_wt`** — Metal weight in grams from product metafield (col 50/51/52)
- **`gold_rate`** — Live IBJA rate for that purity (14KT, 18KT, or calculated 9KT)
- **`diamond_wt`** — Diamond total weight in carats from metafield (col 55)
- **`diamond_rate`** — From rate config; quality-specific (GH I1-I2 or GH SI)
- **`making_charge`** — Per gram making fee (editable, default Rs.2,500)
- **`huid_per_pc`** — Flat per-variant fee (editable, default Rs.100)
- **`certification`** — Per-carat diamond certification fee (editable, default Rs.500)
- **`gem_wt`** — Gemstone total weight from metafield (col 60)
- **`colorstone_rate`** — Per-carat coloured stone rate (editable, default Rs.1,500)
- **`ceil()`** — Uses `ceil_safe(x) = math.ceil(round(x, 6))` to avoid floating-point artifacts

### Dual Price Calculation

Two separate prices are calculated per variant in a single pass:

| Output Column | Purpose | Diamond Rate Chart |
|---|---|---|
| **Col 24** — Variant Price | Selling price shown to customer | Standard rates (lower) |
| **Col 25** — Compare At Price | Strikethrough "was" price | Higher rates |

Default rate chart values:

| Field | Variant Price | Compare At Price |
|---|---|---|
| Diamond GH I1-I2 | Rs.39,500/ct | Rs.1,00,000/ct |
| Diamond GH SI | Rs.49,500/ct | Rs.1,25,000/ct |
| Coloured stone | Rs.1,500/ct | Rs.1,500/ct |
| HUID per pc | Rs.100 | Rs.100 |
| Certification | Rs.500/ct | Rs.500/ct |
| Making charge | Rs.2,500/g | Rs.2,500/g |

All 12 fields are editable in real time from the dashboard without redeploying.

---

## 6. 9KT Rate Calculation

IBJA does not publish a 9KT rate. It is derived using a cross-source formula:

```
9KT = round( Fine Gold 999 x 0.375  +  (18KT_ibja - 750_purity_ibjarates) )
```

| Component | Source |
|---|---|
| Fine Gold 999 (Rs/gram) | ibja.co — "Retail selling Rates" section |
| 18KT (Rs/gram) | ibja.co — same section |
| 750 purity (Rs/gram) | ibjarates.com — "750 Purity" label |

**Example calculation (PM session, 07 Mar 2026):**

```
Fine Gold 999 = 15,941
18KT          = 12,912
750 purity    = 11,956

Premium = 18KT - 750 purity = 12,912 - 11,956 = 956
Base    = Fine Gold 999 x 0.375 = 15,941 x 0.375 = 5,977.88
9KT     = round(5,977.88 + 956) = 6,934
```

---

## 7. Shopify Metafield Mapping

All gold and diamond weights are stored as **product-level metafields** in Shopify, not variant-level. The GraphQL export queries them from the `products` node and copies them to every variant row of that product during JSONL-to-CSV conversion.

**Confirmed metafield namespace.key values:**

| What | Namespace | Key | Type |
|---|---|---|---|
| 14KT gold weight | `custom` | `14kt_metal_weight` | number_decimal |
| 18KT gold weight | `custom` | `18kt_metal_weight` | number_decimal |
| 9KT gold weight | `custom` | `9kt_metal_weight` | number_decimal |
| Diamond total weight | `custom` | `diamond_total_weight` | number_decimal |
| Gemstone total weight | `custom` | `gemstone_total_weight` | single_line_text |

**Critical architecture note — JSONL parsing:**

Shopify Bulk Operations outputs a `.jsonl` file where every nested connection is a **separate line**, not nested inside the parent. The parser uses three buckets:
- Lines without `__parentId` — product objects
- Lines with `__parentId` + `namespace` + `key` fields — metafield objects
- All other lines with `__parentId` — variant objects

Metafields are looked up by `parent_id` after parsing all lines. Do NOT attempt to access `product["metafields"]` — that key does not exist in the JSONL format.

---

## 8. CSV Column Structure

The generated CSV has 84 columns: 82 matching the original Shopify product export format (so `update_prices.py` can read it without changes), plus two appended columns for automation use.

**Critical columns:**

| Col # | Header | Source | Used By |
|---|---|---|---|
| 13 | `Option2 Value` | `selectedOptions[1].value` | `update_prices.py` — gold quality detection |
| 16 | `Option3 Value` | `selectedOptions[2].value` | `update_prices.py` — diamond quality detection |
| **24** | **`Variant Price`** | Written by pricing engine | Shopify selling price |
| **25** | **`Variant Compare At Price`** | Written by pricing engine | Shopify strikethrough price |
| 50 | `14KT Metal Weight (product.metafields.custom.14kt_metal_weight)` | Product metafield | `update_prices.py` |
| 51 | `18KT Metal Weight (product.metafields.custom.18kt_metal_weight)` | Product metafield | `update_prices.py` |
| 52 | `9KT Metal weight (product.metafields.custom.9kt_metal_weight)` | Product metafield | `update_prices.py` |
| 55 | `Diamond Total Weight (product.metafields.custom.diamond_total_weight)` | Product metafield | `update_prices.py` |
| 60 | `Gemstone Total Weight (product.metafields.custom.gemstone_total_weight)` | Product metafield | `update_prices.py` |
| 83 | `Variant ID` | GraphQL `id` field (numeric) | `shopify_push.py` |
| 84 | `Product ID` | GraphQL `id` field (numeric) | `shopify_push.py` |

> **Note on API 2025-01:** The `option1`, `option2`, `option3` fields were removed from `ProductVariant` in Shopify API 2025-01. The export now uses `selectedOptions { name value }` and maps by array index position.

---

## 9. Database Schema

PostgreSQL database (Railway). Tables are auto-created on first run via `init_db()`.

### `rate_history`

| Column | Type | Description |
|---|---|---|
| id | SERIAL PK | Auto-increment |
| timestamp | TEXT | ISO timestamp |
| rate_14kt | REAL | 14KT rate scraped |
| rate_18kt | REAL | 18KT rate scraped |
| rate_fine | REAL | Fine Gold 999 rate |
| session | TEXT | AM or PM |
| rate_date | TEXT | Date from IBJA (dd/mm/yyyy) |
| rate_9kt | REAL | Calculated 9KT rate |

### `update_log`

| Column | Type | Description |
|---|---|---|
| id | SERIAL PK | Auto-increment |
| timestamp | TEXT | ISO timestamp |
| old_rate_14kt | REAL | Previous 14KT rate |
| old_rate_18kt | REAL | Previous 18KT rate |
| new_rate_14kt | REAL | New 14KT rate |
| new_rate_18kt | REAL | New 18KT rate |
| input_file | TEXT | Source CSV filename |
| output_file | TEXT | Generated output filename |
| variants_updated | INTEGER | Count of variants recalculated |
| products_updated | INTEGER | Count of products recalculated |

### `rate_config`

Stores 12 editable pricing fields (6 for Variant Price + 6 for Compare At Price). Only the latest row is used.

### `uploaded_files`

| Column | Type | Description |
|---|---|---|
| id | SERIAL PK | Auto-increment |
| timestamp | TEXT | Upload time |
| filename | TEXT | UUID-prefixed stored filename |
| original_name | TEXT | User's original filename |
| is_active | INTEGER | 1 = current source file |
| file_data | BYTEA | File bytes stored in DB (survives Railway redeploys) |

### `generated_files`

Stores the bytes of generated output CSVs in PostgreSQL so they survive Railway's ephemeral filesystem.

### `users`

| Column | Type | Description |
|---|---|---|
| id | SERIAL PK | Auto-increment |
| username | TEXT UNIQUE | Login username |
| password_hash | TEXT | Werkzeug pbkdf2 hash |
| role | TEXT | `editor` or `viewer` |

---

## 10. REST API Reference

All endpoints except `/api/auth/login` require a valid session cookie. All POST/DELETE endpoints (except login) require `X-CSRF-Token` header.

### Auth

| Method | Endpoint | Role | Body / Response |
|---|---|---|---|
| `POST` | `/api/auth/login` | — | `{username, password}` -> sets session cookie |
| `POST` | `/api/auth/logout` | Any | Clears session |
| `GET` | `/api/auth/me` | Any | `{ok, user: {username, role}}` or 401 |

### Rates

| Method | Endpoint | Role | Description |
|---|---|---|---|
| `GET` | `/api/rates/current` | Any (authenticated) | Live-scrape ibja.co + ibjarates.com |
| `GET` | `/api/rates/stored` | Any (authenticated) | Last baseline rate from DB |
| `GET` | `/api/rates/history` | Any (authenticated) | Rate history (`?limit=50`) |

### Config

| Method | Endpoint | Role | Description |
|---|---|---|---|
| `GET` | `/api/config` | Any (authenticated) | All 12 editable rate fields |
| `POST` | `/api/config` | Editor | Save all 12 fields |

### Update / Pricing

| Method | Endpoint | Role | Description |
|---|---|---|---|
| `POST` | `/api/update/run` | Editor | Scrape rates -> recalculate all prices -> save CSV |
| `POST` | `/api/update/force` | Editor | Store current IBJA rate as baseline without recalculating |

### File Upload

| Method | Endpoint | Role | Description |
|---|---|---|---|
| `POST` | `/api/upload` | Editor | Upload source CSV/XLSX (multipart `file` field) |
| `GET` | `/api/upload/active` | Any (authenticated) | Currently active source file metadata |
| `GET` | `/api/upload/list` | Any (authenticated) | All uploaded source files |
| `DELETE` | `/api/upload/<filename>/delete` | Editor | Delete an uploaded file |

### Generated Sheets

| Method | Endpoint | Role | Description |
|---|---|---|---|
| `GET` | `/api/sheets` | Any (authenticated) | List all generated output files |
| `GET` | `/api/sheets/<file>/download` | Any (authenticated) | Download a generated CSV/XLSX |
| `DELETE` | `/api/sheets/<file>/delete` | Editor | Delete a generated file |

### Logs

| Method | Endpoint | Role | Description |
|---|---|---|---|
| `GET` | `/api/logs` | Any (authenticated) | Pricing run history (`?limit=50`) |
| `GET` | `/api/diamond/logs` | Any (authenticated) | Diamond update history |

---

## 11. File-by-File Explanation

### `app.py` — Flask Web Server

The main web application with 20+ routes. Handles authentication, CSRF protection, rate limiting, file upload/download, and orchestrates calls to the pricing engine. Never modify unless adding new web features.

### `scraper.py` — IBJA Rate Scraper

Scrapes ibja.co for 14KT, 18KT, Fine Gold 999 rates and ibjarates.com for 750 purity. Calculates 9KT using the cross-source formula. Returns a dict with all rates, session (AM/PM), and date. Never modify the formula here.

### `update_prices.py` — Pricing Engine

Reads the uploaded source CSV/XLSX, detects column headers dynamically, applies the full component formula to every 9KT/14KT/18KT variant, and writes the output file. Handles both CSV and XLSX formats. Supports automatic column header detection with fallback to hardcoded positions.

### `database.py` — PostgreSQL Layer

Manages all database operations using `psycopg2`. Reads `DATABASE_URL` from environment. Provides functions for rate history, update logs, rate config, user authentication, file tracking, and generated file storage.

### `shopify_export.py` — Shopify GraphQL Bulk Export

Fetches the complete product catalog directly from Shopify using the Bulk Operations API. Handles the full flow: submit job -> poll until complete -> download `.jsonl` -> parse -> convert to 84-column CSV. Implements rate limiting, exponential backoff, null-check on `currentBulkOperation`, and automatic JSONL cleanup via `try/finally`.

### `shopify_push.py` — Shopify GraphQL Price Push

Reads the pricing output CSV, groups variants by Product ID, and sends `productVariantsBulkUpdate` mutations — one per product (~939 calls total). Uses 0.2s minimum delay between calls. Each call takes 1–3 seconds on Shopify's side. Total push time is 15–25 minutes for 939 products. Handles 429 rate limiting, retries with exponential backoff, collects all failures without stopping the run.

### `automation.py` — Main Pipeline Orchestrator

The script Railway runs at 12PM and 5PM IST. Calls all 5 stages in sequence with full error handling. Every stage is wrapped in `try/except` — any failure sends a Telegram alert and exits with code 1. Timestamps every log line. Handles CSRF token extraction from Flask's HTML response.

### `nightly_sync.py` — New Product Detector

Runs at 2AM IST. Fetches a fresh Shopify export, compares variant count to the most recent generated sheet, and uploads the new file if count increased. Sends Telegram alert only when new products are detected.

### `AUTOMATION_ROADMAP.md` — Technical Design Document

Complete internal documentation covering the full pipeline design, all code with explanations, error handling decisions, build order, and security checklist. Reference this when making any changes to the automation system.

### `requirements.txt` — Python Dependencies

```
flask==3.1.0
openpyxl==3.1.5
requests==2.32.3
beautifulsoup4==4.13.3
gunicorn==23.0.0
psycopg2-binary==2.9.10
python-dotenv==1.0.1
```

### `Procfile` — Railway/Gunicorn Start Command

```
web: gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4
```

---

## 12. Folder Structure

```
gold auto/
|
+-- app.py                    <- Flask web server (20+ routes)
+-- scraper.py                <- IBJA + ibjarates.com scraper + 9KT formula
+-- database.py               <- PostgreSQL layer (psycopg2)
+-- update_prices.py          <- Pricing engine (full formula, CSV + XLSX)
|
+-- automation.py             <- Main automation pipeline (5 stages)
+-- shopify_export.py         <- GraphQL Bulk Export
+-- shopify_push.py           <- GraphQL price push
+-- nightly_sync.py           <- Nightly new-product detection
|
+-- requirements.txt          <- Python dependencies
+-- Procfile                  <- Railway/Gunicorn start command
+-- AUTOMATION_ROADMAP.md     <- Full technical design document
+-- .env.example              <- Template for all required env vars (safe to commit)
+-- .env                      <- Local secrets -- NEVER commit this
+-- .gitignore                <- Excludes .env, *.db, uploads/, updated_sheets/
|
+-- static/
|   +-- css/
|   |   +-- style.css         <- Dashboard styling (Bootstrap + custom)
|   +-- js/
|       +-- app.js            <- Dashboard logic (rates, config, upload, sheets)
|
+-- templates/
|   +-- index.html            <- Main dashboard (logged-in view)
|   +-- login.html            <- Gold-themed login page
|
+-- uploads/                  <- Uploaded CSV/XLSX source files (gitignored)
+-- updated_sheets/           <- Generated output files (gitignored)
```

---

## 13. Web Dashboard Guide

### Login

Navigate to your Railway URL (or `http://localhost:5000` locally). Credentials are set via `FLASK_EDITOR_USERNAME` / `FLASK_EDITOR_PASSWORD` environment variables.

> **Note:** Local login over `http://` will show 401 after login due to the `Secure` cookie flag. This is not a bug — test against the Railway HTTPS URL instead.

### Main Dashboard Layout

```
+--------------------------------------------------------------+
|  TaaraLaxmii Gold Updater       [username] editor  [Logout] |
+--------------------------------------------------------------+
|                                                              |
|  +--------------+  +--------------+  +------------------+   |
|  | Live IBJA    |  | Last Applied |  | Rate Delta       |   |
|  | Rates        |  | Rates        |  |                  |   |
|  |              |  |              |  | 14KT: +87        |   |
|  | 14KT: 10,537 |  | 14KT: 10,450 |  | 18KT: +112       |   |
|  | 18KT: 12,912 |  | 18KT: 12,800 |  |  9KT: +34        |   |
|  |  9KT:  6,934 |  |  9KT:  6,900 |  |                  |   |
|  |  750: 11,956 |  |              |  |                  |   |
|  +--------------+  +--------------+  +------------------+   |
|                                                              |
|  +-- Variant Price Chart (blue) ---+                        |
|  | Diamond GH I1-I2: 39500         |                        |
|  | Diamond GH SI:    49500         |                        |
|  | Colorstone Rate:  1500          |                        |
|  | HUID per pc:      100           |                        |
|  | Certification:    500           |                        |
|  | Making Charge:    2500          |                        |
|  +---------------------------------+                        |
|                                                              |
|  +-- Compare At Price Chart (orange) ---+                   |
|  | Diamond GH I1-I2: 100000             |                   |
|  | Diamond GH SI:    125000             |                   |
|  | Colorstone Rate:  1500               |                   |
|  | HUID per pc:      100                |                   |
|  | Certification:    500                |                   |
|  | Making Charge:    2500               |                   |
|  +--------------------------------------+                   |
|                                                              |
|  [Save Both Charts]                                          |
|                                                              |
|  [Run Pricing]  [Set Baseline]  [Upload CSV / XLSX]          |
|                                                              |
|  [Generated Sheets] [Uploaded Files] [Rate History] [Logs]  |
|  +----------------------------------------------------------+|
|  | products_07Mar2026_1204_PM.csv  [LATEST] [Download]      ||
|  | products_07Mar2026_0900_AM.csv           [Download]      ||
|  +----------------------------------------------------------+|
+--------------------------------------------------------------+
```

### Day-to-Day Usage (Manual Mode)

Normally the automation handles everything. If you need to run manually:

1. Upload your latest Shopify product export CSV via **Upload CSV / XLSX**
2. Verify live rates look correct in the **Live IBJA Rates** card
3. Click **Run Pricing**
4. Go to **Generated Sheets** tab -> download the **Latest** file
5. Import into Shopify via `Products -> Import`

---

## 14. Tech Stack

| Layer | Technology | Version |
|---|---|---|
| **Backend language** | Python | 3.12 |
| **Web framework** | Flask | 3.1.0 |
| **WSGI server** | Gunicorn | 23.0.0 |
| **Database** | PostgreSQL | (Railway managed) |
| **DB driver** | psycopg2-binary | 2.9.10 |
| **Web scraping** | requests + BeautifulSoup4 | 2.32.3 / 4.13.3 |
| **Excel processing** | openpyxl | 3.1.5 |
| **CSV processing** | csv (Python stdlib) | — |
| **JSON Lines parsing** | json (Python stdlib) | — |
| **Environment vars** | python-dotenv | 1.0.1 |
| **Password hashing** | werkzeug.security (pbkdf2) | — |
| **Frontend** | HTML5, Bootstrap 5.3.3, Vanilla JS ES6+ | — |
| **Icons** | Bootstrap Icons | — |
| **Shopify integration** | GraphQL Admin API | 2025-01 |
| **Notifications** | Telegram Bot API | — |
| **Deployment** | Railway | — |
| **Source control** | Git + GitHub (private repo) | — |
| **Data sources** | ibja.co + ibjarates.com | — |

---

## 15. Railway Deployment

### Project Structure on Railway

```
Railway Project: TaaraLaxmii
|
+-- Service 1: flask-app          <- Flask web app (always running)
|   Start: gunicorn app:app       <- from Procfile
|
+-- Service 2: automation-cron    <- automation.py
|   Start: python automation.py
|   Cron:  30 6,11 * * *          <- 12:00 PM + 5:00 PM IST
|
+-- Service 3: nightly-sync-cron  <- nightly_sync.py
|   Start: python nightly_sync.py
|   Cron:  30 20 * * *            <- 2:00 AM IST
|
+-- Database: PostgreSQL          <- Persistent storage
```

### Cron Schedule Reference

| Service | Cron (UTC) | IST time | Purpose |
|---|---|---|---|
| automation-cron | `30 6,11 * * *` | 12:00 PM + 5:00 PM | Twice-daily pricing |
| nightly-sync-cron | `30 20 * * *` | 2:00 AM | New product detection |

> IST = UTC + 5:30. All Railway cron times are in UTC.

### Build Command (all services)

```
pip install -r requirements.txt
```

### Start Commands

| Service | Command |
|---|---|
| flask-app | `gunicorn app:app` (Procfile) |
| automation-cron | `python automation.py` |
| nightly-sync-cron | `python nightly_sync.py` |

---

## 16. Environment Variables

All three Railway services share the same environment variables. Use `.env.example` as a template.

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | Yes | Long hex string for Flask session signing |
| `DATABASE_URL` | Yes | PostgreSQL connection string (auto-provided by Railway) |
| `FLASK_APP_URL` | Yes | Public Railway URL, no trailing slash |
| `FLASK_EDITOR_USERNAME` | Yes | Editor login username |
| `FLASK_EDITOR_PASSWORD` | Yes | Editor login password |
| `SHOPIFY_STORE` | Yes | `taara-laxmii.myshopify.com` |
| `SHOPIFY_TOKEN` | Yes | Shopify Admin API token (`shpat_...`) |
| `SHOPIFY_API_VERSION` | Yes | `2025-01` |
| `TELEGRAM_BOT_TOKEN` | Yes | From @BotFather |
| `TELEGRAM_CHAT_ID` | Yes | Your personal Telegram chat ID |
| `RATE_WAIT_TIMEOUT_HOURS` | Optional | Default: `2` |
| `RATE_CHECK_INTERVAL_MINUTES` | Optional | Default: `5` |
| `BULK_EXPORT_TIMEOUT_MINUTES` | Optional | Default: `15` |
| `BULK_EXPORT_POLL_INTERVAL_SECONDS` | Optional | Default: `10` |
| `MIN_EXPECTED_VARIANT_ROWS` | Optional | Default: `1000` |
| `PUSH_DELAY_SECONDS` | Optional | Default: `0.2` |
| `PUSH_MAX_RETRIES` | Optional | Default: `3` |
| `PUSH_FAILURE_THRESHOLD_PERCENT` | Optional | Default: `5` |
| `RATE_SANITY_14KT_MIN` / `MAX` | Optional | Default: 5000 / 25000 |
| `RATE_SANITY_18KT_MIN` / `MAX` | Optional | Default: 7000 / 35000 |
| `RATE_SANITY_9KT_MIN` / `MAX` | Optional | Default: 3000 / 15000 |

---

## 17. Local Development Setup

### Prerequisites

- Python 3.10+
- A PostgreSQL database (local or Railway)
- A `.env` file filled in from `.env.example`

### Steps

```bash
# 1. Clone the repo
git clone https://github.com/RUBACUS/gold-auto.git
cd gold-auto

# 2. Create virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and fill in environment variables
copy .env.example .env
# Edit .env with your credentials

# 5. Run the Flask app
python app.py
```

Open: http://localhost:5000

> **Important:** When running locally over http://, login will succeed (200) but subsequent requests will return 401 due to the `Secure` cookie flag. This is expected. To test authenticated endpoints locally, either disable `SESSION_COOKIE_SECURE` in `app.py` temporarily or test against your Railway HTTPS URL directly.

### Test the Automation Scripts Individually

```bash
# Test Shopify export (generates a CSV in uploads/)
python shopify_export.py

# Test full pipeline
# Note: On weekends/holidays IBJA rates are not published.
# Add a test bypass to stage1_wait_for_rates() for local testing.
python automation.py

# Test nightly sync
python nightly_sync.py
```

---

## 18. Security Notes

- `.env` is in `.gitignore` — never commit secrets to Git
- `uploads/` and `updated_sheets/` are gitignored — never commit customer data
- `gold_updater.db` (old SQLite file) must be removed from Git history if present — it contains user data
- The Shopify Custom App uses minimum required scopes: `read_products`, `write_products`
- Credentials are set via Railway environment variables — nothing is hardcoded in any file
- `SECRET_KEY` must be a long random string — generate with:
  `python -c "import secrets; print(secrets.token_hex(32))"`
- All API secrets are loaded via `os.environ.get()` — never hardcoded
- Telegram bot only sends to the specific `TELEGRAM_CHAT_ID` configured
- If the Shopify token is ever exposed (shared in chat, committed to Git, etc.) — regenerate it immediately in Shopify Admin → Settings → Apps → TaaraLaxmiiAutomation → API credentials

---

## 19. Troubleshooting

### Automation waits 2 hours then exits with no pricing run

**Cause:** IBJA did not publish rates. This happens every Saturday, Sunday, and Indian public holiday. This is normal, expected behaviour — no action needed. The system resumes automatically on the next trading day.

### "Field 'option1' doesn't exist on type 'ProductVariant'"

Shopify API 2025-01 removed `option1/2/3` fields. The export now uses `selectedOptions { name value }`. This is already fixed in `shopify_export.py`. If you see this error, check that `SHOPIFY_API_VERSION=2025-01` is set and the correct version of `shopify_export.py` is deployed.

### "currentBulkOperation returns null"

Normal when no bulk job is running yet. The poller checks for `None` before accessing `status`. If this persists beyond 30 seconds, a previous bulk job may still be running — wait a few minutes and retry.

### "Bulk export submission error: another operation already running"

Only one bulk operation can run at a time per Shopify store. Wait for the previous one to complete (check in Shopify Admin → Settings → Bulk operations), then retry.

### 401 errors after login when testing locally

The session cookie has `Secure` flag set — it only transmits over HTTPS. Local `http://` testing will always show 401 after login. Test against your Railway HTTPS URL instead.

### "Could not find 'Retail selling Rates' section on IBJA page"

IBJA's website is down or its HTML structure changed. Stage 1 retries every 5 minutes for 2 hours, then sends a Telegram alert and exits. Fix: update `scraper.py` to match the new IBJA HTML structure.

### "Flask login failed: HTTP 429"

Login rate limit triggered (5 attempts per 5 minutes per IP). Wait 5 minutes. Verify `FLASK_EDITOR_USERNAME` and `FLASK_EDITOR_PASSWORD` in Railway match your actual credentials.

### "CSRF validation failed"

The automation extracts the CSRF token from page HTML after login. If this fails, all POST requests will be rejected with 403. Check that `FLASK_APP_URL` is correct and has no trailing slash, and that the app is running on Railway.

### "Only N rows found — expected at least 1000"

The Shopify export returned fewer rows than the safety threshold. Could be a temporary Shopify API issue. Re-run manually. If your store genuinely has fewer than 1,000 variants, lower `MIN_EXPECTED_VARIANT_ROWS` in Railway env vars.

### Prices not updating in Shopify after push

Check Railway logs for `[Push] Product XXXXX errors:` lines. Most common causes: Shopify token lacks `write_products` scope, or token has expired. Regenerate in Shopify Admin → Settings → Apps → TaaraLaxmiiAutomation → API credentials.

### Automation takes 20-25 minutes — is this normal?

Yes. Stage 4 (price push) takes 15–25 minutes because each GraphQL mutation requires 1–3 seconds of Shopify processing time per product, multiplied by 939 products. The 0.2s sleep between calls is a minimum floor — it does not control Shopify's server response time. Total observed time on 07 Mar 2026: 22 minutes 57 seconds for 939 products and 55,098 variants. This is expected and correct.

### Railway disk is ephemeral — files disappear after redeploy

Expected behaviour. Uploaded files and generated sheets are stored in PostgreSQL (`uploaded_files.file_data`, `generated_files.file_data`) and restored to disk on demand. Do not rely on files persisting on Railway's local filesystem between redeploys.