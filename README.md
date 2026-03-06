# TaaraLaxmii – Gold Price Updater

An automated tool that scrapes live gold rates from **IBJA (India Bullion and Jewellers Association)** and **ibjarates.com**, recalculates every jewelry variant price using a full component-wise formula, and generates an updated CSV/XLSX ready to import into Shopify — complete with both **Variant Price** and **Compare At Price**.

Features a **web dashboard** with role-based login, dual editable price charts, CSV/XLSX upload, and full rate & update history tracking.

---

## Table of Contents

- [Overview](#overview)
- [How It Works](#how-it-works)
- [Architecture Diagram](#architecture-diagram)
- [Data Flow Diagram](#data-flow-diagram)
- [Folder Structure](#folder-structure)
- [File-by-File Explanation](#file-by-file-explanation)
- [The Price Formula](#the-price-formula)
- [9KT Rate Calculation](#9kt-rate-calculation)
- [Excel/CSV Sheet Structure](#excelcsv-sheet-structure)
- [Database Schema](#database-schema)
- [REST API Reference](#rest-api-reference)
- [Web Dashboard Guide](#web-dashboard-guide)
- [Installation](#installation)
- [How to Run](#how-to-run)
- [First-Time Setup](#first-time-setup)
- [Day-to-Day Usage](#day-to-day-usage)
- [What Gets Updated](#what-gets-updated)
- [Troubleshooting](#troubleshooting)
- [Tech Stack](#tech-stack)

---

## Overview

| Aspect | Detail |
|---|---|
| **Purpose** | Recalculate gold + diamond jewelry prices for Shopify based on live IBJA gold rates |
| **Gold Purities** | 9KT, 14KT, 18KT |
| **Pricing Columns** | Variant Price (col 24) + Compare At Price (col 25) |
| **Data Sources** | [ibja.co](https://ibja.co/) (14KT, 18KT, Fine Gold 999) + [ibjarates.com](https://ibjarates.com/) (750 purity) |
| **File Formats** | CSV and XLSX (input and output) |
| **Authentication** | Session-based login with admin/editor and viewer roles |
| **Products** | ~939 products, ~55,098 variant rows per run |

---

## How It Works

```
1. User logs in to the web dashboard
2. Clicks "Run Pricing"
3. System scrapes live gold rates from ibja.co + ibjarates.com
4. Calculates 9KT rate: round(Fine Gold 999 × 0.375 + (18KT − 750 purity))
5. For each variant (9KT/14KT/18KT):
   a. Reads gold weight, diamond weight, gemstone weight from sheet
   b. Computes Variant Price (standard diamond rates)
   c. Computes Compare At Price (higher diamond rates)
6. Writes updated CSV/XLSX to updated_sheets/ folder
7. User downloads and imports into Shopify
```

Key difference from older versions: this is a **full recalculation** — every variant price is computed from scratch using the formula, not as a delta from previous rates.

---

## Architecture Diagram

```
┌─────────────┐      ┌──────────────────┐      ┌─────────────────┐
│  ibja.co    │      │  ibjarates.com   │      │  Browser (UI)   │
│ (14KT,18KT │◄─────│  (750 purity)    │      │  Bootstrap 5    │
│  Fine Gold) │      └──────────────────┘      └────────┬────────┘
└──────┬──────┘                                         │
       │                                                │ HTTP
       ▼                                                ▼
┌──────────────────────────────────────────────────────────────┐
│                      Flask App (app.py)                      │
│                                                              │
│  /login              → Login page                            │
│  /api/auth/*         → Auth endpoints                        │
│  /api/rates/*        → Live + stored rates                   │
│  /api/config         → Editable rate charts (12 fields)      │
│  /api/update/run     → Run Pricing (full recalculation)      │
│  /api/update/force   → Set Baseline                          │
│  /api/upload         → Upload CSV/XLSX source file           │
│  /api/upload/list    → List all uploaded files               │
│  /api/sheets         → List generated output files           │
│  /api/sheets/*/dl    → Download output file                  │
│  /api/logs           → Update logs                           │
│  /api/diamond/logs   → Diamond update logs                   │
│                                                              │
├──────────┬───────────────┬────────────────┬──────────────────┤
│scraper.py│ update_prices │  database.py   │                  │
│          │    .py        │                │                  │
└──────────┴───────────────┴────────────────┴──────────────────┘
       │            │               │
       ▼            ▼               ▼
   Web Scrape   CSV/XLSX       SQLite DB
   (requests)   (openpyxl/     (gold_updater.db)
                 csv module)
```

---

## Data Flow Diagram

```
 ibja.co ──────┐
               ▼
         ┌───────────┐     ┌──────────────┐
         │ scraper.py │────►│  9KT Formula │
         │            │     │  (calc from  │
         │            │     │  Fine Gold,  │
         └───────────┘     │  18KT, 750)  │
               ▲            └──────┬───────┘
 ibjarates.com─┘                   │
                                   ▼
 ┌──────────────┐         ┌──────────────────┐
 │ Uploaded CSV │────────►│ update_prices.py  │
 │ or default   │         │                  │
 │ XLSX export  │         │ For each variant: │
 └──────────────┘         │ • Variant Price   │
                          │ • Compare At Price│
       ┌─────────┐       └────────┬─────────┘
       │ rate_    │                │
       │ config   │◄───── rates + │
       │ (12 flds)│       config   │
       └─────────┘                ▼
                          ┌──────────────────┐
                          │  Output CSV/XLSX  │
                          │  in updated_sheets│
                          └──────────────────┘
```

---

## Folder Structure

```
gold auto/
├── app.py                  # Flask web server (20 routes)
├── scraper.py              # IBJA + ibjarates.com scraper + 9KT formula
├── database.py             # SQLite DB layer (8 tables)
├── update_prices.py        # Price recalculation engine (CSV + XLSX)
├── requirements.txt        # Python dependencies
├── gold_updater.db         # SQLite database (auto-created)
├── products_export_1.xlsx  # Default Shopify product export (fallback)
├── static/
│   ├── css/
│   │   └── style.css       # Dashboard styling
│   └── js/
│       └── app.js          # Dashboard logic (fetch, config, upload)
├── templates/
│   ├── index.html          # Main dashboard (logged-in view)
│   └── login.html          # Login page
├── uploads/                # Uploaded CSV/XLSX source files
└── updated_sheets/         # Generated output files (date-stamped)
```

---

## File-by-File Explanation

### `scraper.py`

Scrapes gold rates from two sources:

1. **[ibja.co](https://ibja.co/)** — Retail selling rates for Fine Gold 999, 22KT, 20KT, 18KT, 14KT, plus session (AM/PM) and date.
2. **[ibjarates.com](https://ibjarates.com/)** — 750 purity gold rate.

**9KT derivation:**
```
9KT = round(Fine Gold 999 × 0.375 + (18KT − 750 purity))
```

Returns a dictionary with all rates including `9kt` and `purity_750`.

### `database.py`

Manages SQLite database with **8 tables**:

| Table | Purpose |
|---|---|
| `rate_history` | Every scraped IBJA rate (timestamped) |
| `update_log` | Every pricing run result |
| `diamond_rate_history` | Diamond rate change history |
| `diamond_update_log` | Diamond pricing run results |
| `base_rates` | Stored baseline rates |
| `rate_config` | 12 editable fields (6 standard + 6 compare-at) |
| `users` | Login credentials with roles |
| `uploaded_files` | Uploaded source file tracking |

Also handles:
- User authentication with `werkzeug.security` password hashing
- Default user seeding on first run

### `update_prices.py`

The pricing engine. Key functions:

- **`update_excel_prices()`** — Reads source CSV/XLSX, recalculates every 9KT/14KT/18KT variant using the full formula, writes **both** Variant Price (col 24) and Compare At Price (col 25).
- **`run_update()`** — Orchestrates: scrape rates → recalculate → save output → persist to DB.
- **`_compute_variant_price()`** — Helper that applies the formula for one variant.
- **`ceil_safe(x)`** — `math.ceil(round(x, 6))` to avoid floating-point ceiling errors.

Supports both CSV and XLSX input/output (format matches the source file).

### `app.py`

Flask web server with **20 routes**, role-based access control, file upload, and concurrent-update protection via threading lock.

Two roles:
- **editor** — Can run pricing, set baseline, upload files, edit config
- **viewer** — Read-only access to rates, sheets, logs

### `templates/index.html`

Main dashboard with:
- Navbar with logged-in user badge + role indicator
- 3 rate cards: Live IBJA Rates (with 750 purity + 9KT breakdown), Last Applied Rates, Rate Delta
- 2 side-by-side config cards: **Variant Price Chart** (blue, 6 fields) and **Compare At Price Chart** (orange, 6 fields)
- Action bar: Run Pricing, Set Baseline, Upload CSV/XLSX
- 4 tabs: Generated Sheets, Uploaded Files, Rate History, Update Logs

### `templates/login.html`

Gold-themed gradient login page with Bootstrap 5. Redirects to dashboard on success.

### `static/js/app.js`

Frontend logic:
- Dual config fetch/save (12 fields across both charts)
- Live rates display with 9KT formula explanation
- File upload with progress feedback
- Sheet listing, download, and "Latest" tag
- Uploaded files listing

### `static/css/style.css`

Custom styling including:
- `.card-config` — Blue gradient header for Variant Price Chart
- `.card-compare` — Orange gradient header for Compare At Price Chart
- Responsive table and badge styles

---

## The Price Formula

Every variant price is calculated using this formula:

```
Price = ceil(gold_wt × gold_rate)
      + ceil(diamond_wt × diamond_rate)
      + ceil(gold_wt × making_charge)
      + huid_per_pc
      + ceil(diamond_wt × certification)
      + ceil(gem_wt × colorstone_rate)
```

Where:
- **`gold_wt`** — From col 50 (14KT), 51 (18KT), or 52 (9KT) in the sheet
- **`gold_rate`** — Live-scraped from IBJA (14KT, 18KT) or calculated (9KT)
- **`diamond_wt`** — From col 55 in the sheet
- **`diamond_rate`** — From rate config (per carat, quality-specific: GH I1-I2 or GH SI)
- **`making_charge`** — Editable per gram (default ₹2,500)
- **`huid_per_pc`** — Flat charge per variant (default ₹100)
- **`certification`** — Per carat diamond labour (default ₹500)
- **`gem_wt`** — From col 60; **`colorstone_rate`** — Per carat (default ₹1,500)
- **`ceil()`** — `ceil_safe()` = `math.ceil(round(x, 6))` to avoid FP ceiling artifacts

### Dual Pricing

Two prices are calculated per variant:

| Column | Name | Diamond Rates (defaults) | Purpose |
|---|---|---|---|
| **Col 24** | Variant Price | I1-I2: ₹39,500 · SI: ₹49,500 | Selling price (lower) |
| **Col 25** | Compare At Price | I1-I2: ₹1,00,000 · SI: ₹1,25,000 | Strikethrough price (higher) |

Both use the same formula but with different rate charts (all 6 fields are independently editable).

---

## 9KT Rate Calculation

9KT is not published by IBJA. It's derived from:

```
9KT = round(Fine Gold 999 × 0.375 + (18KT − 750 purity))
```

| Component | Source |
|---|---|
| Fine Gold 999 | ibja.co |
| 18KT | ibja.co |
| 750 purity | ibjarates.com |

**Example (PM rates):**
```
Fine Gold 999 = 15,941
18KT          = 12,912
750 purity    = 11,956

9KT = round(15,941 × 0.375 + (12,912 − 11,956))
    = round(5,977.875 + 956)
    = round(6,933.875)
    = 6,934
```

---

## Excel/CSV Sheet Structure

The tool reads and writes Shopify product export files. Key columns (1-based):

| Column | Header | Read/Write | Purpose |
|---|---|---|---|
| 1 | Handle | Read | Product identifier |
| 13 | Option2 Value | Read | Gold quality (e.g. "14KT-Yellow") |
| 16 | Option3 Value | Read | Diamond quality (e.g. "GH I1-I2") |
| **24** | **Variant Price** | **Write** | Calculated selling price |
| **25** | **Variant Compare At Price** | **Write** | Calculated strikethrough price |
| 50 | Metafield (14KT weight) | Read | Gold weight in grams |
| 51 | Metafield (18KT weight) | Read | Gold weight in grams |
| 52 | Metafield (9KT weight) | Read | Gold weight in grams |
| 55 | Metafield (Diamond weight) | Read | Diamond total weight in carats |
| 60 | Metafield (Gemstone weight) | Read | Gemstone total weight in carats |

All 82 columns are preserved in the output — only columns 24 and 25 are modified.

---

## Database Schema

SQLite database (`gold_updater.db`) — auto-created on first run.

### `rate_history`
| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
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
| id | INTEGER PK | Auto-increment |
| timestamp | TEXT | ISO timestamp |
| old_rate_14kt | REAL | Previous 14KT rate |
| old_rate_18kt | REAL | Previous 18KT rate |
| new_rate_14kt | REAL | New 14KT rate |
| new_rate_18kt | REAL | New 18KT rate |
| input_file | TEXT | Source filename |
| output_file | TEXT | Generated filename |
| variants_updated | INTEGER | Count of variants changed |
| products_updated | INTEGER | Count of products changed |
| status | TEXT | "success" or error |

### `diamond_rate_history`
| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| timestamp | TEXT | ISO timestamp |
| rate_i1i2 | REAL | GH I1-I2 rate per carat |
| rate_si | REAL | GH SI rate per carat |

### `diamond_update_log`
Same structure as `update_log` but for diamond rate changes.

### `base_rates`
| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| timestamp | TEXT | ISO timestamp |
| rate_14kt | REAL | Baseline 14KT |
| rate_18kt | REAL | Baseline 18KT |
| rate_9kt | REAL | Baseline 9KT |

### `rate_config`
12 editable fields (6 standard + 6 compare-at):

| Column | Default | Description |
|---|---|---|
| diamond_i1i2 | 39,500 | GH I1-I2 rate/carat (Variant Price) |
| diamond_si | 49,500 | GH SI rate/carat (Variant Price) |
| colorstone_rate | 1,500 | Gemstone rate/carat |
| huid_per_pc | 100 | Flat charge per variant |
| certification | 500 | Diamond labour/carat |
| making_charge | 2,500 | Gold making charge/gram |
| cmp_diamond_i1i2 | 1,00,000 | GH I1-I2 rate/carat (Compare At) |
| cmp_diamond_si | 1,25,000 | GH SI rate/carat (Compare At) |
| cmp_colorstone_rate | 1,500 | Gemstone rate/carat (Compare At) |
| cmp_huid_per_pc | 100 | Flat charge (Compare At) |
| cmp_certification | 500 | Diamond labour/carat (Compare At) |
| cmp_making_charge | 2,500 | Making charge/gram (Compare At) |

### `users`
| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| username | TEXT UNIQUE | Login username |
| password_hash | TEXT | Werkzeug hashed password |
| role | TEXT | "editor" or "viewer" |

### `uploaded_files`
| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| timestamp | TEXT | Upload time |
| filename | TEXT | Stored filename (UUID-prefixed) |
| original_name | TEXT | User's original filename |
| is_active | INTEGER | 1 = current source file |

---

## REST API Reference

All API endpoints require login unless noted. Editor-only endpoints require the `editor` role.

### Authentication

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/login` | — | Login page |
| POST | `/api/auth/login` | — | `{"username", "password"}` → session cookie |
| POST | `/api/auth/logout` | Any | Clear session |
| GET | `/api/auth/me` | Any | Current user info |

### Rates

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/rates/current` | Any | Live-scrape IBJA + ibjarates.com |
| GET | `/api/rates/stored` | Any | Last applied baseline rate |
| GET | `/api/rates/history` | Any | Rate history (`?limit=50`) |

### Config (Rate Charts)

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/config` | Any | All 12 editable rate fields |
| POST | `/api/config` | Editor | Update all 12 fields |

### Update

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/update/run` | Editor | Scrape + recalculate + generate output file |
| POST | `/api/update/force` | Editor | Store current IBJA rate as baseline |

### File Upload

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/upload` | Editor | Upload CSV/XLSX (multipart `file`) |
| GET | `/api/upload/active` | Any | Current active source file info |
| GET | `/api/upload/list` | Any | List all uploaded source files |

### Sheets (Output Files)

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/sheets` | Any | List all generated files with metadata |
| GET | `/api/sheets/<filename>/download` | Any | Download a generated file |

### Logs

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/logs` | Any | Update history (`?limit=50`) |
| GET | `/api/diamond/logs` | Any | Diamond update history |

---

## Web Dashboard Guide

### Login
Navigate to `http://127.0.0.1:5000`. You'll see a gold-themed login page.

**Default credentials:**
| Username | Password | Role |
|---|---|---|
| admin | admin123 | editor |
| viewer | viewer123 | viewer |

### Main Dashboard (after login)

```
┌──────────────────────────────────────────────────────────────┐
│  TaaraLaxmii Gold Updater          [admin] editor  [Logout] │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │ Live IBJA    │  │ Last Applied │  │ Delta        │       │
│  │ Rates        │  │ Rates        │  │              │       │
│  │              │  │              │  │              │       │
│  │ 14KT: 10537 │  │ 14KT: 10450 │  │ 14KT: +87   │       │
│  │ 18KT: 12912 │  │ 18KT: 12800 │  │ 18KT: +112  │       │
│  │ 9KT:  6934  │  │ 9KT:  6900  │  │ 9KT:  +34   │       │
│  │ 750p: 11956 │  │              │  │              │       │
│  │ 9KT = ...   │  │              │  │              │       │
│  └──────────────┘  └──────────────┘  └──────────────┘       │
│                                                              │
│  ┌─── Variant Price Chart ───┐  ┌── Compare At Price Chart ─┐│
│  │ (blue header, "Selling    │  │ (orange header,            ││
│  │  Price" badge)            │  │  "Strikethrough" badge)    ││
│  │                           │  │                            ││
│  │ Diamond GH I1-I2: 39500  │  │ Diamond GH I1-I2: 100000  ││
│  │ Diamond GH SI:    49500  │  │ Diamond GH SI:    125000  ││
│  │ Colorstone Rate:  1500   │  │ Colorstone Rate:  1500    ││
│  │ HUID per pc:      100    │  │ HUID per pc:      100     ││
│  │ Certification:    500    │  │ Certification:    500     ││
│  │ Making Charge:    2500   │  │ Making Charge:    2500    ││
│  └───────────────────────────┘  └────────────────────────────┘│
│                                                              │
│  [Save Both Charts]                                          │
│                                                              │
│  ┌──────────────────────────────────────────────────────────┐│
│  │ [Run Pricing]  [Set Baseline]  [Upload CSV / XLSX]       ││
│  └──────────────────────────────────────────────────────────┘│
│                                                              │
│  ┌──────────────────────────────────────────────────────────┐│
│  │ [Generated Sheets] [Uploaded Files] [Rate Hist] [Logs]   ││
│  │                                                          ││
│  │  products_20250609_1430_PM.csv  [Latest]  [Download]     ││
│  │  products_20250609_1105_AM.xlsx           [Download]     ││
│  │  ...                                                     ││
│  └──────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────┘
```

**Viewer role:** Can see everything but action buttons (Run Pricing, Set Baseline, Upload, Save Charts) are hidden.

---

## Installation

### Prerequisites

- **Python 3.10+** (tested with 3.12)
- **pip** (comes with Python)

### Steps

```bash
# 1. Clone or download the project
cd "gold auto"

# 2. Install dependencies
pip install -r requirements.txt
```

This installs:
- `flask==3.1.0` — Web framework
- `openpyxl==3.1.5` — Excel read/write
- `requests==2.32.3` — HTTP client for scraping
- `beautifulsoup4==4.13.3` — HTML parsing

No additional setup needed — the SQLite database, folders, and default users are auto-created on first run.

---

## How to Run

```bash
python app.py
```

Opens at: **http://127.0.0.1:5000**

---

## First-Time Setup

1. **Start the server:** `python app.py`
2. **Open browser:** http://127.0.0.1:5000
3. **Log in** with `admin` / `admin123`
4. **Upload your Shopify product export** (CSV or XLSX) via the Upload button — or place it as `products_export_1.xlsx` in the project root
5. **Review the rate charts** — adjust diamond rates, making charge, etc. as needed for both Variant Price and Compare At Price charts
6. **Click "Set Baseline"** to store the current IBJA rate
7. **Click "Run Pricing"** to generate your first output file
8. **Download** the generated file and import into Shopify

---

## Day-to-Day Usage

1. Open dashboard, log in
2. Check the Live IBJA Rates card — rates update automatically (AM and PM sessions)
3. Click **Run Pricing** — generates a new output file with recalculated prices
4. Go to **Generated Sheets** tab, download the latest file (marked with **Latest** badge)
5. Import into Shopify

If diamond rates or making charges change, update them in the price chart cards and click **Save Both Charts** before running pricing.

---

## What Gets Updated

| What | Updated? | Details |
|---|---|---|
| **9KT variants** | ✅ Yes | Using derived 9KT rate from ibjarates formula |
| **14KT variants** | ✅ Yes | Using live 14KT from ibja.co |
| **18KT variants** | ✅ Yes | Using live 18KT from ibja.co |
| **Variant Price (col 24)** | ✅ Yes | Standard rate chart |
| **Compare At Price (col 25)** | ✅ Yes | Higher-rate chart (strikethrough) |
| **Color variants** (Yellow/Rose/White) | N/A | Same product, same weight → same price |
| **Diamond quality** (GH SI, GH I1-I2) | ✅ Yes | Quality-specific diamond rates applied |
| **Original file** | ❌ Never | Original upload is never modified |

---

## Troubleshooting

### "Could not find 'Retail selling Rates' section on IBJA page"
- IBJA's website structure may have changed or the site is temporarily down
- Try again in 15–30 minutes
- Check https://ibja.co/ manually

### "Could not find 750 purity rate on ibjarates.com"
- ibjarates.com may be down or have changed its HTML structure
- Check https://ibjarates.com/ manually

### Browser shows "This site can't be reached"
- Flask server is not running — run `python app.py`

### Login fails with default credentials
- On first start, `admin/admin123` and `viewer/viewer123` are auto-seeded
- If the database was deleted or recreated, restart the server

### Output file is smaller than original
- Normal — openpyxl doesn't preserve all Excel formatting metadata
- All 82 columns and all data rows are complete

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.12, Flask 3.1 |
| **Web Scraping** | requests, BeautifulSoup4 |
| **Excel Processing** | openpyxl, csv (stdlib) |
| **Database** | SQLite (Python's built-in `sqlite3`) |
| **Auth** | Flask sessions, werkzeug password hashing |
| **Frontend** | HTML5, Bootstrap 5.3.3, Bootstrap Icons, Vanilla JS (ES6+) |
| **Data Sources** | [ibja.co](https://ibja.co/) + [ibjarates.com](https://ibjarates.com/) |
| **Target Platform** | Shopify (product export/import via CSV/XLSX) |
