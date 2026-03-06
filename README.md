# TaaraLaxmii – Gold Price Updater

An automated tool that scrapes live gold rates from the official **IBJA (India Bullion and Jewellers Association)** website twice a day, calculates updated prices for every product variant in your Shopify export sheet, and provides a **web-based dashboard** to trigger updates, preview sheets, and track every change — all with one click.

---

## Table of Contents

- [Overview](#overview)
- [How It Works — The Big Picture](#how-it-works--the-big-picture)
- [Architecture Diagram](#architecture-diagram)
- [Data Flow Diagram](#data-flow-diagram)
- [Folder Structure](#folder-structure)
- [File-by-File Explanation](#file-by-file-explanation)
- [The Price Formula Explained](#the-price-formula-explained)
- [Excel Sheet Structure](#excel-sheet-structure)
- [Database Schema](#database-schema)
- [REST API Reference](#rest-api-reference)
- [Web Dashboard Guide](#web-dashboard-guide)
- [Installation Guide](#installation-guide)
- [How to Run](#how-to-run)
- [First Time Setup](#first-time-setup)
- [Day-to-Day Usage](#day-to-day-usage)
- [What Gets Updated and What Doesn't](#what-gets-updated-and-what-doesnt)
- [Troubleshooting](#troubleshooting)
- [Future Roadmap](#future-roadmap)
- [Tech Stack](#tech-stack)

---

## Overview

**TaaraLaxmii** is a jewelry brand that sells on Shopify at [taaralaxmii.com](https://taaralaxmii.com/). Their products are made of gold and each product has multiple variants — different karat (9KT, 14KT, 18KT) and different diamond quality (GH I1-I2, GH SI).

Gold prices change **twice a day** (AM and PM sessions) as published by IBJA. Every time gold rates change, the selling price of every product must be recalculated. Doing this manually for **939 products** and **36,000+ variants** is impossible.

This project **automates that completely**.

---

## How It Works — The Big Picture

```
IBJA website publishes new gold rates (AM / PM)
          │
          ▼
You click "Run Update Now" in the dashboard
          │
          ▼
scraper.py fetches the latest 14KT and 18KT rate per gram from ibja.co
          │
          ▼
update_prices.py compares new rate vs last stored rate → calculates delta
          │
          ▼
For every 14KT and 18KT variant row in the Excel sheet:
    new_price = old_price + (metal_weight × rate_delta)
          │
          ▼
A brand-new timestamped Excel file is saved in updated_sheets/
          │
          ▼
Rate and update details are stored in SQLite database (gold_updater.db)
          │
          ▼
Download the new sheet from dashboard → upload to Shopify
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER (Browser)                           │
│                  http://127.0.0.1:5000                          │
└──────────────────────────┬──────────────────────────────────────┘
                           │ HTTP Requests
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Flask Web Server                            │
│                         app.py                                  │
│                                                                 │
│   GET  /                     → renders dashboard page          │
│   GET  /api/rates/current    → scrapes IBJA live                │
│   GET  /api/rates/stored     → reads last baseline from DB      │
│   GET  /api/rates/history    → past rate records from DB        │
│   POST /api/update/run       → triggers price update engine     │
│   POST /api/update/force     → sets new baseline (no changes)   │
│   GET  /api/sheets           → lists all Excel files            │
│   GET  /api/sheets/<f>/download → downloads a file              │
│   GET  /api/sheets/<f>/preview  → previews first 100 rows       │
│   GET  /api/logs             → update history from DB           │
└──────┬───────────────┬──────────────────────┬───────────────────┘
       │               │                      │
       ▼               ▼                      ▼
┌────────────┐  ┌──────────────┐   ┌─────────────────────┐
│ scraper.py │  │  database.py │   │  update_prices.py   │
│            │  │              │   │                     │
│ Fetches    │  │ SQLite DB    │   │ Reads Excel,        │
│ live rates │  │              │   │ applies delta,      │
│ from       │  │ rate_history │   │ saves new file      │
│ ibja.co    │  │ update_log   │   │ to updated_sheets/  │
└────────────┘  └──────────────┘   └─────────────────────┘
       │               │                      │
       ▼               ▼                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                       File System                               │
│                                                                 │
│  products_export_1.xlsx          ← Original Shopify export      │
│  updated_sheets/                                                │
│    products_20260306_1305_AM.xlsx  ← Generated output files     │
│    products_20260306_1800_PM.xlsx                               │
│  gold_updater.db                 ← SQLite database              │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Flow Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                    FULL UPDATE DATA FLOW                         │
└──────────────────────────────────────────────────────────────────┘

 ibja.co                scraper.py           database.py
    │                       │                    │
    │  HTTP GET ibja.co      │                    │
    │◄──────────────────────│                    │
    │  HTML page             │                    │
    │──────────────────────►│                    │
    │                       │ parse rates         │
    │                       │ 14KT = ₹10,282/g    │
    │                       │ 18KT = ₹12,912/g    │
    │                       │                    │
    │                       │  get_latest_rate() │
    │                       │───────────────────►│
    │                       │  {14kt: 10200,      │
    │                       │   18kt: 12800}      │
    │                       │◄───────────────────│
    │                       │                    │
    │                 delta_14 = 10282 - 10200 = +82
    │                 delta_18 = 12912 - 12800 = +112
    │                       │                    │
    │              update_prices.py              │
    │                       │                    │
    │          Load products_export_1.xlsx        │
    │          (or latest from updated_sheets/)   │
    │                       │                    │
    │          Pass 1: Build weight map           │
    │          {product_handle → {14KT: 3.354g,  │
    │                              18KT: 3.9g}}  │
    │                       │                    │
    │          Pass 2: For each 14KT/18KT row:   │
    │          new_price = old + weight × delta  │
    │          e.g. 73465 + 3.354×82 = 73740     │
    │                       │                    │
    │          9KT rows: SKIPPED (unchanged)      │
    │                       │                    │
    │          Save → products_20260306_1305_AM.xlsx
    │                       │                    │
    │                       │  save_rate()        │
    │                       │───────────────────►│
    │                       │  save_update_log() │
    │                       │───────────────────►│
    │                       │                    │
    │         Return result to Flask → to Browser│
```

---

## Folder Structure

```
gold auto/
│
├── app.py                     # Flask web server – all API routes
├── scraper.py                 # Fetches live gold rates from ibja.co
├── database.py                # SQLite database layer (history + logs)
├── update_prices.py           # Price calculation and Excel update engine
│
├── products_export_1.xlsx     # Original Shopify product export (never modified)
├── gold_updater.db            # SQLite database (auto-created on first run)
├── requirements.txt           # Python package dependencies
│
├── templates/
│   └── index.html             # The web dashboard page (Jinja2 template)
│
├── static/
│   ├── css/
│   │   └── style.css          # Dashboard custom styles
│   └── js/
│       └── app.js             # Dashboard JavaScript (API calls, UI logic)
│
└── updated_sheets/            # All generated output Excel files (auto-created)
    ├── products_20260306_1305_AM.xlsx
    ├── products_20260306_1800_PM.xlsx
    └── ...
```

---

## File-by-File Explanation

### `scraper.py`
**What it does:** Connects to `https://ibja.co/` and extracts the current official gold rates.

**How it works:**
- Sends an HTTP GET request to ibja.co using the `requests` library
- Parses the HTML with `BeautifulSoup` to extract plain text
- Finds the "Retail selling Rates for Gold Jewellery" section
- Extracts 14KT and 18KT rates per gram using regular expressions
- Also extracts Fine Gold (999), 22KT, the session (AM/PM), and the date

**Returns:**
```python
{
    "14kt": 10282,       # ₹ per gram, excl. GST & making charges
    "18kt": 12912,
    "fine_gold": 15941,
    "22kt": 15558,
    "session": "AM",     # or "PM"
    "date": "06/03/2026"
}
```

**Important:** Rates from IBJA are **per gram, excluding GST and making charges**.

---

### `database.py`
**What it does:** Manages all persistent data using SQLite. This is a lightweight database stored as a single file (`gold_updater.db`) in the project folder — no external database server needed.

**Tables:**

| Table | Purpose |
|---|---|
| `rate_history` | Every IBJA rate that was scraped and saved |
| `update_log` | Every time an Excel file was updated — before/after rates, variant counts |

**Key functions:**

| Function | Description |
|---|---|
| `init_db()` | Creates tables if they don't exist yet (runs automatically on import) |
| `save_rate(...)` | Saves a newly scraped rate to `rate_history` |
| `get_latest_rate()` | Returns the most recently stored rate (used as baseline) |
| `get_rate_history(limit)` | Returns last N rates for display in dashboard |
| `save_update_log(...)` | Records an update event in `update_log` |
| `get_update_logs(limit)` | Returns last N update events for display |

---

### `update_prices.py`
**What it does:** This is the core engine. It takes the current and previous IBJA rates, calculates the difference (delta), and applies that delta to every eligible row in the Excel file.

**Step-by-step logic:**

1. **Scrape** current IBJA rates (calls `scraper.py`)
2. **Load baseline** – retrieves the last stored rate from the database
3. **First run check** – if no baseline exists, store current rate as baseline and stop (no Excel changes)
4. **No-change check** – if rates are identical to baseline, stop and inform user
5. **Find input file** – uses the latest file in `updated_sheets/`, or falls back to original `products_export_1.xlsx`
6. **Pass 1 (weight map)** – reads the Excel once to build a dictionary: `{product_handle → {14KT weight, 18KT weight}}`
7. **Pass 2 (price update)** – reads every row, applies the delta formula, writes new price
8. **Skip logic** – 9KT rows are completely skipped. Compare At Price column is not touched.
9. **Save** – writes to new timestamped file in `updated_sheets/`
10. **Persist** – saves new rate and update log to database

**The delta formula:**
```
new_price = old_price + metal_weight_grams × (new_rate_per_gram − old_rate_per_gram)
```

---

### `app.py`
**What it does:** The Flask web server. It serves the dashboard HTML page and provides all the REST API endpoints that the frontend calls.

**Concurrency safety:** Uses a `threading.Lock` to prevent two updates from running simultaneously if two people click the button at the same time.

**Security:** The download and preview endpoints sanitize filenames using `os.path.basename()` to prevent path traversal attacks.

---

### `templates/index.html`
**What it does:** The main (and only) web page of the dashboard. Uses **Bootstrap 5** for layout and styling, **Bootstrap Icons** for icons.

**Sections:**
- Navbar with live clock
- Three rate cards: Live IBJA / Last Applied / Delta
- Action buttons: Run Update / Set Baseline
- Tabs: Sheets / Rate History / Update Logs
- Modal: Sheet preview popup

---

### `static/js/app.js`
**What it does:** All the browser-side JavaScript. Talks to the Flask API using `fetch()` and updates the page without reloading.

**Key functions:**

| Function | What it does |
|---|---|
| `fetchLiveRates()` | Calls `/api/rates/current`, updates Live Rate card |
| `fetchStoredRate()` | Calls `/api/rates/stored`, updates Last Applied card |
| `updateDelta()` | Calculates difference between live and stored, shows delta card |
| `runUpdate()` | POSTs to `/api/update/run`, shows spinner, displays result |
| `forceBaseline()` | POSTs to `/api/update/force` to recalibrate baseline |
| `fetchSheets()` | Lists all Excel files in the Sheets tab |
| `previewSheet(filename)` | Opens modal with first 100 rows of any sheet |
| `fetchHistory()` | Loads Rate History tab data |
| `fetchLogs()` | Loads Update Logs tab data |

---

### `static/css/style.css`
Custom styling for rate cards, delta color coding (red for increase, green for decrease), table typography, and button states.

---

### `products_export_1.xlsx`
**The original Shopify product export file.** This file is **never modified** by the script. It only serves as the starting input on the very first update. After that, each subsequent update uses the most recently generated file from `updated_sheets/` as its input — so each run builds on top of the previous one.

---

### `gold_updater.db`
An SQLite database file. Auto-created when you first run the app. Stores all rate history and update logs. You can open this file with any SQLite viewer (like [DB Browser for SQLite](https://sqlitebrowser.org/)) to inspect data manually.

---

### `requirements.txt`
Lists all Python packages needed. Install everything with one command (see Installation Guide).

---

## The Price Formula Explained

### Why a Delta Approach?

Instead of recalculating prices from scratch every time (which would require knowing the exact making charges and diamond rates per product), this system uses a **delta (difference) approach**.

When IBJA changes the rate, only the **gold cost component** changes. Diamond prices are fixed. Making charges are fixed. So:

```
price_change = metal_weight × rate_change
```

If gold goes up by ₹100/gram and a product has 3.354g of 14KT gold, its price increases by:

```
3.354 × 100 = ₹335.40 → rounded to ₹335
```

This is applied to every variant of that product.

### Why 14KT and 18KT only?

IBJA publishes official 14KT and 18KT rates directly. **9KT** pricing involves different purity calculations and was excluded from scope in Phase 1 of this project.

### Why does color (Yellow/Rose/White) not affect price?

Gold of the same karat but different color (alloyed differently) has the same gold content per gram. The cost difference is negligible and TaaraLaxmii prices all colors identically per karat.

### Why does size not affect price?

Each product has a defined metal weight (grams) that is fixed regardless of size variant in this catalog. The weight column in the sheet holds a single value per product.

---

## Excel Sheet Structure

The Shopify export has **82 columns** and **55,124 data rows** (939 unique products × ~18 variants each). The key columns this project uses:

| Column # | Column Name | Used For |
|---|---|---|
| 1 | Handle | Unique product identifier |
| 13 | Option2 Value | Gold quality variant: `14KT-Yellow`, `18KT-Rose`, `9KT-White`, etc. |
| 16 | Option3 Value | Diamond quality: `GH I1-I2`, `GH SI` |
| 18 | Variant SKU | Unique variant code |
| 24 | **Variant Price** | **The price this script updates** |
| 25 | Variant Compare At Price | Original/crossed-out price (not modified in Phase 1) |
| 50 | 14KT Metal Weight | Grams of 14KT gold in the product (stored only in first row of each product) |
| 51 | 18KT Metal Weight | Grams of 18KT gold in the product |
| 52 | 9KT Metal Weight | Grams of 9KT gold (not used in Phase 1) |
| 55 | Diamond Total Weight | Total diamond carat weight |

**Important detail about data layout:** In the Shopify export, product-level data (like metal weight) is only filled in the **first row** of each product. All subsequent variant rows for that product have `None`/blank in those columns. The script handles this by building a weight map in Pass 1 before updating prices in Pass 2.

---

## Database Schema

### Table: `rate_history`

Stores every IBJA rate that was fetched and saved.

```sql
CREATE TABLE rate_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,   -- ISO format: "2026-03-06T13:05:41"
    rate_14kt   REAL    NOT NULL,   -- ₹ per gram e.g. 10282.0
    rate_18kt   REAL    NOT NULL,   -- ₹ per gram e.g. 12912.0
    rate_fine   REAL,               -- Fine gold 999 rate
    session     TEXT,               -- "AM" or "PM"
    rate_date   TEXT                -- Date from IBJA e.g. "06/03/2026"
);
```

### Table: `update_log`

Records every time an Excel file was generated.

```sql
CREATE TABLE update_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           TEXT    NOT NULL,
    old_rate_14kt       REAL    NOT NULL,   -- Rate before update
    old_rate_18kt       REAL    NOT NULL,
    new_rate_14kt       REAL    NOT NULL,   -- Rate after update
    new_rate_18kt       REAL    NOT NULL,
    input_file          TEXT    NOT NULL,   -- Which file was used as input
    output_file         TEXT    NOT NULL,   -- Name of generated file
    variants_updated    INTEGER NOT NULL,   -- e.g. 36732
    products_updated    INTEGER NOT NULL,   -- e.g. 939
    status              TEXT    NOT NULL DEFAULT 'success'
);
```

---

## REST API Reference

All endpoints return JSON. The `ok` field is `true` on success and `false` on error.

### GET `/api/rates/current`
Fetches live gold rates directly from ibja.co right now.

**Response:**
```json
{
    "ok": true,
    "rates": {
        "14kt": 10282,
        "18kt": 12912,
        "fine_gold": 15941,
        "22kt": 15558,
        "session": "AM",
        "date": "06/03/2026"
    }
}
```

---

### GET `/api/rates/stored`
Returns the last rate that was saved to the database (used as baseline for next update).

---

### GET `/api/rates/history?limit=50`
Returns the last 50 rate records stored in the database.

---

### POST `/api/update/run`
Triggers the full update pipeline: scrape → compare → update Excel → save.

**Possible response statuses:**

| `status` value | Meaning |
|---|---|
| `baseline_set` | First ever run — baseline stored, no Excel changes |
| `no_change` | IBJA rates haven't changed — no update needed |
| `updated` | Prices updated, new Excel file generated |

**Success response (when updated):**
```json
{
    "ok": true,
    "status": "updated",
    "message": "Prices updated for 36732 variants across 939 products.",
    "output_file": "products_20260306_1305_AM.xlsx",
    "variants_updated": 36732,
    "products_updated": 939,
    "delta_14kt": 82,
    "delta_18kt": 112,
    "old_rates": {"14kt": 10200, "18kt": 12800},
    "new_rates": {"14kt": 10282, "18kt": 12912, ...}
}
```

---

### POST `/api/update/force`
Forces the current IBJA rate to become the new baseline **without changing any prices**. Use this when you upload a fresh Shopify export and want to re-sync the baseline to match what that sheet was priced at.

---

### GET `/api/sheets`
Lists all Excel files (original + all generated sheets) with metadata and linked update log info.

---

### GET `/api/sheets/<filename>/download`
Downloads the specified Excel file directly.

---

### GET `/api/sheets/<filename>/preview?limit=100`
Returns the first N rows (max 500) of a sheet as JSON, showing key columns only (Handle, Title, Gold Quality, Diamond Quality, SKU, Price, Compare Price, Weights).

---

### GET `/api/logs?limit=50`
Returns the last 50 update log entries.

---

## Web Dashboard Guide

Open your browser and go to **http://127.0.0.1:5000**

```
┌────────────────────────────────────────────────────────────────┐
│  💎 TaaraLaxmii – Gold Price Updater         [Live Clock]      │
├────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌───────────────┐  │
│  │ Live IBJA Rate  │  │ Last Applied    │  │ Rate Delta    │  │
│  │                 │  │ Rate            │  │               │  │
│  │ 14KT: ₹10,282   │  │ 14KT: ₹10,200  │  │ 14KT: +₹82   │  │
│  │ 18KT: ₹12,912   │  │ 18KT: ₹12,800  │  │ 18KT: +₹112  │  │
│  │ Session: AM     │  │ Applied: Today  │  │ ⚠ Update Now  │  │
│  └─────────────────┘  └─────────────────┘  └───────────────┘  │
│                                                                │
│  [ ⚡ Run Update Now ]   [ 📌 Set Baseline ]                   │
│                                                                │
│ ┌── Sheets ──┬── Rate History ──┬── Update Logs ─────────┐    │
│ │ File Name     │ Type     │ Rate Used  │ Updated │ Actions │   │
│ │ products_...  │ Original │ —          │ —       │ ⬇ 👁  │   │
│ │ products_...  │ Updated  │ ₹10,282    │ 36,732  │ ⬇ 👁  │   │
└────────────────────────────────────────────────────────────────┘
```

### Rate Cards
- **Live IBJA Rate** — Pulls the current rate from ibja.co every time you click the refresh button or load the page
- **Last Applied Rate** — The rate that was used in the most recent Excel update (stored in database)
- **Rate Delta** — The difference between live and stored. Goes **red** when rates went up (prices will increase), **green** when rates went down. Shows "Update Available" badge when a change is detected.

### Buttons
- **Run Update Now** — Runs the complete pipeline. Shows a spinner while processing. After completion, shows a success/info/no-change alert and refreshes all tabs.
- **Set Baseline** — Use this when you've just downloaded a fresh Shopify export and replaced `products_export_1.xlsx`. This tells the system "the current IBJA rate is what that sheet was priced at" — so the next update only adjusts from this point.

### Sheets Tab
- Lists all Excel files in the project
- Shows file size, what rate was used, and how many variants were updated
- **Download** button (↓) saves the file to your computer
- **Preview** button (👁) opens a popup showing the first 100 rows of that file

### Rate History Tab
Shows every rate recorded in the database — timestamp, 14KT/18KT values, AM/PM session, and the IBJA date. Newest first.

### Update Logs Tab
Shows every time an Excel file was generated — old rate, new rate, delta, output filename, number of variants updated. Full audit trail.

---

## Installation Guide

### Prerequisites

Before starting, make sure you have the following installed on your computer:

1. **Python 3.10 or higher**
   - Download from: https://www.python.org/downloads/
   - During installation, check ✅ "Add Python to PATH"
   - Verify: open Command Prompt, type `python --version`

2. **pip** (comes with Python automatically)
   - Verify: `pip --version`

3. **Internet connection** — needed to fetch rates from ibja.co

---

### Step-by-Step Installation

**Step 1 — Get the project files**

The project folder is already set up at:
```
C:\Users\devil\OneDrive\Desktop\gold auto\
```

If you're setting this up on a new machine, copy the entire folder there.

**Step 2 — Open a terminal in the project folder**

Option A — PowerShell:
```powershell
cd "C:\Users\devil\OneDrive\Desktop\gold auto"
```

Option B — Right-click the folder in File Explorer → "Open in Terminal"

**Step 3 — Install required packages**

```bash
pip install -r requirements.txt
```

This installs:

| Package | Version | Purpose |
|---|---|---|
| `flask` | 3.1.0+ | Web server framework |
| `openpyxl` | 3.1.5+ | Read and write Excel .xlsx files |
| `requests` | 2.32.3+ | Make HTTP requests to ibja.co |
| `beautifulsoup4` | 4.13.3+ | Parse HTML from ibja.co |

**Step 4 — Verify installation**

```bash
python -c "import flask, openpyxl, requests, bs4; print('All OK')"
```

Should print: `All OK`

**Step 5 — Confirm your Excel file is in place**

Make sure `products_export_1.xlsx` (your Shopify product export) is in the project folder.

---

## How to Run

Open a terminal in the project folder and run:

```bash
python app.py
```

You should see:
```
 * Serving Flask app 'app'
 * Running on http://127.0.0.1:5000
```

Then open your browser and go to: **http://127.0.0.1:5000**

> To stop the server, press `Ctrl+C` in the terminal.

---

## First Time Setup

The very first time you run this tool after setting it up with a fresh Shopify export sheet, follow these steps:

1. **Start the server:** `python app.py`
2. **Open the dashboard:** http://127.0.0.1:5000
3. **Set the baseline:**
   - Click **"Set Baseline"** button
   - This records the current IBJA rate as the starting point
   - No Excel file will be generated yet — this just calibrates the system
4. **Confirm:** The "Last Applied Rate" card now shows today's current rate

From now on, every time IBJA publishes new rates and you click **"Run Update Now"**, the system will calculate how much prices changed since this baseline and generate an updated sheet.

---

## Day-to-Day Usage

IBJA updates gold rates **twice a day**: once in the morning (AM) and once in the evening (PM).

**Recommended routine:**

**Morning (after AM rates publish):**
1. Open http://127.0.0.1:5000
2. Check if the **Delta card** shows "Update Available"
3. If yes → click **"Run Update Now"**
4. Once complete, download the new `.xlsx` file from the Sheets tab
5. Upload it to Shopify

**Evening (after PM rates publish):**
1. Repeat the same steps

**Note:** If you click "Run Update Now" and rates haven't changed, the system will simply say "No change needed" — no new file is generated and nothing is overwritten.

---

## What Gets Updated and What Doesn't

| Element | Updated? | Notes |
|---|---|---|
| **14KT variant prices** | ✅ Yes | Delta formula applied using 14KT metal weight |
| **18KT variant prices** | ✅ Yes | Delta formula applied using 18KT metal weight |
| **9KT variant prices** | ❌ No | Skipped in Phase 1 — to be added later |
| **Compare At Price** | ❌ No | Skipped in Phase 1 — to be configured later |
| **Color variants** (Yellow/Rose/White) | N/A | Same product, same weight → same price change |
| **Size variants** | N/A | Size doesn't affect metal weight in this catalog |
| **Diamond quality** (GH SI, GH I1-I2) | N/A | Diamond cost is fixed; only gold delta applies |
| **Original file** (`products_export_1.xlsx`) | ❌ Never | Original is never touched |

---

## Troubleshooting

### "Could not find 'Retail selling Rates' section on IBJA page"
- IBJA's website structure may have changed temporarily, or the site is down
- Try refreshing in 15–30 minutes
- Check https://ibja.co/ manually in your browser

### Browser shows "This site can't be reached"
- The Flask server is not running
- Open a terminal, navigate to the project folder, and run `python app.py`

### "No baseline set yet" shown on dashboard
- This is normal for a fresh setup
- Click "Set Baseline" to initialize

### Updated sheet file size is smaller than original
- Normal — openpyxl does not preserve all Excel formatting metadata from Shopify exports
- The data (all 82 columns, all rows) is complete; only formatting differences may exist

### The price didn't change even though IBJA published new rates
- Check the Rate History tab — if the new rate was already stored as baseline, then there's no delta to apply
- This happens if you clicked "Set Baseline" after the rates changed but before running the update

### Error when running `pip install -r requirements.txt`
- Ensure Python and pip are correctly installed
- Try: `python -m pip install -r requirements.txt`

---

## Future Roadmap

The following features are planned for future phases:

- **9KT price calculation** — calculating 9KT prices using a separate formula
- **Compare At Price update** — updating the compare/original price column proportionally
- **Direct Shopify API upload** — pushing the updated sheet directly to Shopify without manual download/upload
- **Scheduled automation** — auto-run twice a day using Windows Task Scheduler (AM and PM)
- **Email/WhatsApp notification** — alert when rates change and update is available
- **Multi-file support** — handling multiple product export files simultaneously

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.12, Flask 3.1 |
| **Web Scraping** | requests, BeautifulSoup4 |
| **Excel Processing** | openpyxl |
| **Database** | SQLite (via Python's built-in `sqlite3`) |
| **Frontend** | HTML5, Bootstrap 5.3, Bootstrap Icons, Vanilla JavaScript (ES6+) |
| **Data Source** | [ibja.co](https://ibja.co/) – India Bullion and Jewellers Association |
| **Target Platform** | Shopify (product export/import via CSV/XLSX) |

---

## Notes

> All IBJA rates are **per gram, excluding 3% GST and making charges**, as stated on the IBJA website.

> The tool currently handles **939 products** and **55,124 rows** in the Excel sheet, updating **36,732 variant prices** (14KT + 18KT across all color variants) per run.

> The original `products_export_1.xlsx` is **never modified**. Every update creates a new file in the `updated_sheets/` folder, so you always have a complete history.
