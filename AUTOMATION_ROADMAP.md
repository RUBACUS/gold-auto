# TaaraLaxmii — Full Automation Roadmap

**Goal:** Every day at **12:00 PM IST** (after IBJA AM session) and **5:00 PM IST** (after IBJA PM session), the system automatically:
1. Checks that IBJA rates are freshly updated
2. Triggers a Shopify product export
3. Gets the ZIP from email, unzips the CSV
4. Uploads to your Flask app, runs pricing, downloads the new CSV
5. Pushes updated prices back to Shopify
6. Sends a Telegram message confirming success

Zero manual steps. Zero intervention.

---

## How the Full Pipeline Works (Simple View)

```
12:00 PM / 5:00 PM IST
        │
        ▼
 Wait for IBJA rates to update on the website
 (check every 5 min, give up after 2 hours)
        │
        ▼
 Log into Shopify admin via browser automation
 Click: Products → Export → Export CSV
        │
        ▼
 Wait for Shopify to email the ZIP (5–15 min)
 Check Gmail inbox every 2 minutes
        │
        ▼
 Download ZIP → Unzip → Get CSV
        │
        ▼
 Upload CSV to your Flask app
 Run Pricing → Download the new generated CSV
        │
        ▼
 Push updated prices to Shopify via their API
        │
        ▼
 Send Telegram message: "Done! 1247 variants updated"
```

---

## What You Will Need to Create / Set Up (One-time)

| What | Where | Cost |
|------|-------|------|
| Shopify Custom App | Your Shopify Admin panel | Free |
| Gmail App Password | Your Google Account settings | Free |
| Telegram Bot | Telegram app (takes 2 minutes) | Free |
| Railway account | railway.app | Free tier available |
| GitHub account (for code storage) | github.com | Free |

---

---

# PART 1 — One-Time Setups

---

## Step 1 — Create the Shopify Custom App

This gives the automation permission to push price updates directly into Shopify without re-uploading a CSV manually.

### How to do it:

1. Log into your Shopify admin: `yourstore.myshopify.com/admin`
2. In the left sidebar go to **Settings** (bottom left gear icon)
3. Click **Apps and sales channels**
4. Click **Develop apps** (top right button)
5. If prompted, click **Allow custom app development**
6. Click **Create an app**
7. Give it any name, e.g. `PriceAutomation`
8. Click **Configure Admin API scopes**
9. Enable these exact permissions:
   - `read_products` — to read product and variant IDs
   - `write_products` — to update prices
   - `read_inventory` — optional but useful
10. Click **Save**
11. Go to the **API credentials** tab
12. Click **Install app**
13. Copy and save these three values somewhere safe:
    - **API key**
    - **API secret key**
    - **Admin API access token** (this is the important one — starts with `shpat_...`)

> ⚠️ The access token is shown only once. Copy it immediately and store it.

**You will use the access token in the automation script as:**
```
SHOPIFY_TOKEN=shpat_xxxxxxxxxxxx
SHOPIFY_STORE=yourstore.myshopify.com
```

---

## Step 2 — Set Up Gmail App Password

The automation needs to read your email inbox to find the ZIP file Shopify sends. The safest way is an "App Password" — a special one-time password just for this script.

### How to do it:

1. Go to your Google Account: `myaccount.google.com`
2. Click **Security** in the left sidebar
3. Under "How you sign in to Google", click **2-Step Verification** — make sure it is ON (required for App Passwords)
4. After enabling 2FA, go back to Security
5. Search for **App passwords** in the search bar at the top
6. Click App passwords
7. In the dropdown, select **Mail** as the app and **Other** as the device
8. Type a name like `ShopifyAutomation`
9. Click **Generate**
10. A 16-character password appears (like `abcd efgh ijkl mnop`) — **copy it now**

**You will use this as:**
```
GMAIL_ADDRESS=youremail@gmail.com
GMAIL_APP_PASSWORD=abcdefghijklmnop
```

> Note: Remove spaces when storing the app password. It becomes `abcdefghijklmnop`.

---

## Step 3 — Create a Telegram Bot

This is the notification system. Takes under 2 minutes.

### How to do it:

1. Open Telegram on your phone or desktop
2. Search for `@BotFather` and open the chat
3. Send: `/newbot`
4. It asks for a name — type anything, e.g. `TaaraLaxmii Updater`
5. It asks for a username — must end in `bot`, e.g. `taaralaxmii_updater_bot`
6. BotFather replies with your **Bot Token** — looks like `7123456789:AAFxxxxxxxxxxxxxxxxxxxx`
7. Copy it

### Get your Chat ID:

1. Open your new bot in Telegram and send it any message (e.g. "hello")
2. Open this URL in your browser (replace YOUR_TOKEN):
   `https://api.telegram.org/botYOUR_TOKEN/getUpdates`
3. In the JSON response, find `"chat":{"id":XXXXXXXXX}` — that number is your Chat ID
4. Copy it

**You will use these as:**
```
TELEGRAM_BOT_TOKEN=7123456789:AAFxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=123456789
```

---

## Step 4 — Prepare Your Flask App URL

The automation script makes API calls to your Flask app. It needs a public URL (not localhost).

- If your app is already on Railway/Render/etc, you already have a URL like `https://gold-auto.railway.app`
- If it's running locally, you need to deploy it to Railway first (see Part 3)
- The URL will be used as:
```
FLASK_APP_URL=https://gold-auto.railway.app
FLASK_EDITOR_USERNAME=admin
FLASK_EDITOR_PASSWORD=admin123
```

---

---

# PART 2 — The Automation Script (What It Does, Step by Step)

---

This is a single Python file (`automation.py`) that runs the entire pipeline. Here's exactly what it does in order:

---

### Stage 1 — Wait for Fresh IBJA Rates

**Why:** IBJA publishes rates at roughly 11:30–11:45 AM (AM session) and 4:00–4:30 PM (PM session). We don't start processing until the rates are actually fresh for today.

**How it works:**
- Script calls your existing `/api/rates/current` endpoint
- It compares the date on the returned rate to today's date AND checks the session (AM or PM) matches the current run
- If the rate is today's fresh rate → move to Stage 2
- If not → wait 5 minutes and check again
- If 2 hours pass with no fresh rate → abort, send Telegram message "IBJA rates not updated. Automation aborted for this session.", and stop
- Next scheduled run will try again at the next time

---

### Stage 2 — Trigger Shopify Product Export

**How it works:**
- A headless browser (Playwright, runs invisibly in the background) opens Chrome
- Logs into `yourstore.myshopify.com/admin` using your Shopify admin email and password
- Navigates to the Products page
- Clicks the **Export** button
- Selects "All products" and "CSV for Excel, Numbers, or other spreadsheet programs"
- Clicks **Export products**
- Shopify shows a confirmation message and sends a ZIP to your store email
- Browser closes

---

### Stage 3 — Wait for Email and Download ZIP

**How it works:**
- Script connects to Gmail using IMAP (your Gmail and App Password)
- Every 2 minutes, it checks for a new unread email from `noreply@shopify.com` with subject containing "Your product export"
- Waits up to 20 minutes total
- When found — downloads the ZIP attachment to a temp folder
- Unzips it — gets the `products_export_1.csv` file inside

---

### Stage 4 — Upload to Flask App and Generate Pricing

**How it works:**
- Script logs into your Flask app using the editor credentials
- Uploads the extracted CSV via `POST /api/upload`
- Calls `POST /api/update/run` to run pricing
- Receives the response containing the output filename
- Downloads the generated CSV via `GET /api/sheets/{filename}/download`
- Saves it locally as `updated_products.csv`

---

### Stage 5 — Push Prices Back to Shopify

**How it works:**
- Script reads the generated CSV row by row
- For each row that has a variant price, it extracts: `variant_id` (from the CSV) and the new `price` + `compare_at_price`
- Makes a direct API call to Shopify:
  `PUT https://yourstore.myshopify.com/admin/api/2024-01/variants/{variant_id}.json`
  with the new prices
- Shopify updates the price instantly — no import queue, no waiting
- This is faster and more reliable than re-importing a CSV

> Note: The variant ID column must be present in your Shopify export CSV (it is — column `Variant ID`). The script reads it directly.

---

### Stage 6 — Send Telegram Notification

After everything succeeds, the bot sends a message to your Telegram:

```
✅ TaaraLaxmii Pricing Updated — 06 Mar 2026, 12:04 PM IST

Session: AM
18KT: ₹6,850/g
14KT: ₹5,920/g
9KT: ₹3,810/g

Variants updated: 1,247
Products updated: 312
Output file: products_06Mar2026_120312_IST_AM.csv

Duration: 18 minutes
```

If anything fails at any stage, the bot sends:
```
❌ Automation FAILED — Stage 3 (Email Download)
Error: No email from Shopify received within 20 minutes.
Time: 06 Mar 2026, 12:22 PM IST
```

---

---

# PART 3 — Deployment on Railway

---

Railway is used to host both your Flask app and the automation worker. Each runs as a separate "service" inside one Railway project.

---

## Step 1 — Set Up Railway Account

1. Go to `railway.app`
2. Click **Login with GitHub** (create a GitHub account first if you don't have one)
3. Authorize Railway
4. You're in. Free tier gives $5 credit/month which covers small apps easily.

---

## Step 2 — Push Your Code to GitHub

1. Create a new repository on `github.com` — name it `gold-auto`
2. Make it **Private**
3. On your PC, open command prompt in your project folder:
   ```
   git init
   git add .
   git commit -m "initial commit"
   git remote add origin https://github.com/YOURUSERNAME/gold-auto.git
   git push -u origin main
   ```
4. Your code is now on GitHub

---

## Step 3 — Deploy Flask App on Railway

1. On Railway dashboard, click **New Project**
2. Click **Deploy from GitHub repo**
3. Select your `gold-auto` repository
4. Railway auto-detects Python and your `Procfile`
5. Click **Deploy**
6. Once deployed, go to **Settings → Domains → Generate Domain**
7. You get a URL like `https://gold-auto-production.up.railway.app`
8. This is your `FLASK_APP_URL`

---

## Step 4 — Add Environment Variables to Railway

In Railway, click your Flask app service → **Variables** tab → Add these one by one:

```
SECRET_KEY=your-secret-key-here
SHOPIFY_TOKEN=shpat_xxxxxxxxxxxx
SHOPIFY_STORE=yourstore.myshopify.com
SHOPIFY_ADMIN_EMAIL=youremail@gmail.com
SHOPIFY_ADMIN_PASSWORD=your-shopify-admin-password
GMAIL_ADDRESS=youremail@gmail.com
GMAIL_APP_PASSWORD=abcdefghijklmnop
TELEGRAM_BOT_TOKEN=7123456789:AAFxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=123456789
FLASK_EDITOR_USERNAME=admin
FLASK_EDITOR_PASSWORD=admin123
```

> ⚠️ Never put passwords directly in code files. Always use environment variables like this.

---

## Step 5 — Add the Automation Worker as a Cron Service on Railway

Railway has a dedicated **Cron** service type — it runs a command on a schedule.

1. In your Railway project, click **+ New Service**
2. Select **Empty Service**
3. In service settings, change **Start Command** to: `python automation.py`
4. Go to **Settings → Deploy → Cron Schedule**
5. Enable Cron and set it to: `30 6,11 * * *`
   - This means: run at 6:30 UTC and 11:30 UTC daily
   - 6:30 UTC = 12:00 PM IST
   - 11:30 UTC = 5:00 PM IST
6. Connect it to the same GitHub repo
7. Add all the same environment variables to this service too

> ✅ Railway will now automatically run `automation.py` at exactly 12:00 PM and 5:00 PM IST every day.

---

---

# PART 4 — Project File Structure After Adding Automation

```
gold-auto/
├── app.py                  ← Your existing Flask app
├── database.py             ← Your existing DB code
├── update_prices.py        ← Your existing pricing logic
├── scraper.py              ← Your existing IBJA scraper
├── automation.py           ← NEW: the full automation script
├── requirements.txt        ← Updated with new dependencies
├── Procfile                ← Your existing Procfile
├── .env                    ← Local secrets (never commit this)
├── .gitignore              ← Must include .env and .db files
└── templates/, static/, ...
```

---

## New Python libraries needed (add to requirements.txt)

```
playwright==1.44.0          ← Browser automation (Shopify login + export)
requests==2.31.0            ← Already likely there, for API calls
python-dotenv==1.0.0        ← Load .env file locally
```

> `playwright` needs one extra setup command after install:
> `playwright install chromium --with-deps`
> On Railway, add this to your service's **Build Command** field.

---

---

# PART 5 — The Logic for Rate-Waiting (Important Detail)

---

This is how the script handles the "wait for fresh rates" requirement:

```
Script starts at 12:00 PM IST
│
├─ Call /api/rates/current
├─ Is today's date in the response AND session = "AM"?
│     YES → Proceed to Stage 2
│     NO  → Wait 5 minutes, try again
│
├─ If still no fresh rate after 2 hours (i.e., 2:00 PM IST):
│     → Send Telegram: "⚠️ IBJA AM rates not published by 2:00 PM.
│                        Automation aborted. Will retry at 5:00 PM."
│     → Exit script
│
Script ends. Railway waits until 5:00 PM to try again.
```

Same logic applies to the 5:00 PM run, checking for PM session rates.

---

---

# PART 6 — Step-by-Step Build Order (How to Build This)

Follow this exact order. Don't skip ahead.

---

### Week 1 — Foundation

- [ ] **Day 1:** Create Shopify Custom App, copy the access token
- [ ] **Day 1:** Create Gmail App Password, copy it
- [ ] **Day 1:** Create Telegram Bot, get token and chat ID
- [ ] **Day 2:** Create GitHub account and push your Flask app code
- [ ] **Day 2:** Deploy Flask app on Railway, confirm it works at the Railway URL
- [ ] **Day 3:** Test all your existing Flask API endpoints via the Railway URL (login, upload, run, download)

---

### Week 2 — Build Automation Script

- [ ] **Day 4:** Build and test Stage 1 (IBJA rate check + wait loop) locally
- [ ] **Day 5:** Build and test Stage 2 (Playwright Shopify export trigger) locally
- [ ] **Day 6:** Build and test Stage 3 (Gmail IMAP email + ZIP download) locally
- [ ] **Day 7:** Build and test Stage 4 (Flask app upload + pricing + download) locally

---

### Week 3 — Complete and Deploy

- [ ] **Day 8:** Build and test Stage 5 (Shopify API price push) locally
- [ ] **Day 9:** Build and test Stage 6 (Telegram notification) locally
- [ ] **Day 10:** Run the full pipeline end-to-end manually once to confirm everything works
- [ ] **Day 11:** Deploy `automation.py` to Railway as a Cron service
- [ ] **Day 12:** Set cron schedule, monitor first two automatic runs

---

---

# PART 7 — What Happens When Something Goes Wrong

The script has safety checks at every stage:

| Failure | What the script does |
|---------|---------------------|
| IBJA rates not updated in 2 hours | Sends Telegram alert, stops cleanly, waits for next scheduled time |
| Shopify login fails | Sends Telegram alert with error, stops |
| Email/ZIP not received in 20 min | Sends Telegram alert, stops |
| Flask app upload fails | Sends Telegram alert with error, stops |
| Shopify API price push fails | Sends Telegram alert, logs which variants failed |
| Any unexpected crash | Sends Telegram alert with full error message |

No silent failures — you always know what happened via Telegram.

---

---

# PART 8 — Security Checklist

- [ ] `.env` file is in `.gitignore` — never committed to GitHub
- [ ] All secrets stored as Railway Environment Variables, not in code
- [ ] Shopify Custom App has minimum required permissions only (`read_products`, `write_products`)
- [ ] Gmail App Password used instead of your real Gmail password
- [ ] Telegram bot is private (only your chat ID gets messages)
- [ ] Railway project is private

---

---

# Quick Reference — All Credentials You Need

Collect these before starting to build:

```
# Shopify
SHOPIFY_STORE=yourstore.myshopify.com
SHOPIFY_TOKEN=shpat_...              ← from Custom App
SHOPIFY_ADMIN_EMAIL=...              ← Shopify admin login email
SHOPIFY_ADMIN_PASSWORD=...           ← Shopify admin login password

# Gmail
GMAIL_ADDRESS=...
GMAIL_APP_PASSWORD=...               ← 16-char app password

# Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Your Flask App
FLASK_APP_URL=https://your-app.railway.app
FLASK_EDITOR_USERNAME=admin
FLASK_EDITOR_PASSWORD=admin123
```

---

# Summary

| Stage | Tool Used | Time Taken |
|-------|-----------|------------|
| Wait for IBJA rates | Your existing scraper | 0–30 min |
| Trigger Shopify export | Playwright (headless browser) | ~1 min |
| Wait for ZIP email | Gmail IMAP | 5–15 min |
| Upload + generate CSV | Your Flask app APIs | ~1 min |
| Push prices to Shopify | Shopify Admin REST API | ~2 min |
| Send notification | Telegram Bot API | instant |
| **Total** | | **~10–25 minutes** |

The cron runs at **12:00 PM and 5:00 PM IST daily.** If rates aren't available within 2 hours, the run is skipped and Telegram notifies you. Everything else is fully hands-off.
