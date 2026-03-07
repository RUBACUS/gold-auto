# Automation Build — Start Prompt

> **How to use this file:**
> 1. Complete all the one-time setups in `AUTOMATION_ROADMAP.md` (Part 1)
> 2. Fill in every blank field below
> 3. Copy this entire file's contents and paste it as a message to GitHub Copilot
> 4. Copilot will build the complete automation following the roadmap

---

## Message to Send (copy everything below this line)

---

Please build the full automation pipeline for my TaaraLaxmii Gold Price Updater project by strictly following the `AUTOMATION_ROADMAP.md` file that already exists in this workspace. Build it stage by stage — Stage 1 through Stage 6 — exactly as described in the roadmap. Do not change any existing pricing formula or logic in `update_prices.py`. Here are all the credentials and configuration values you will need:

---

### Shopify Configuration

```
SHOPIFY_STORE=
# Example: mytaaralaxmiistore.myshopify.com
# (no https://, no trailing slash)

SHOPIFY_TOKEN=
# The Admin API access token from your Custom App
# Starts with: shpat_

SHOPIFY_ADMIN_EMAIL=
# The email you use to log into Shopify admin

SHOPIFY_ADMIN_PASSWORD=
# The password you use to log into Shopify admin
```

---

### Gmail Configuration

```
GMAIL_ADDRESS=
# The Gmail address that receives the Shopify export email

GMAIL_APP_PASSWORD=
# The 16-character App Password (no spaces)
# Example: abcdefghijklmnop
```

---

### Telegram Bot Configuration

```
TELEGRAM_BOT_TOKEN=
# From @BotFather
# Example: 7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxx

TELEGRAM_CHAT_ID=
# Your personal chat ID
# Example: 123456789
```

---

### Flask App Configuration

```
FLASK_APP_URL=
# The public Railway URL of your deployed Flask app
# Example: https://gold-auto-production.up.railway.app

FLASK_EDITOR_USERNAME=
# The editor account username (default: admin)

FLASK_EDITOR_PASSWORD=
# The editor account password (default: admin123)
```

---

### Schedule Configuration

```
AM_RUN_TIME_IST=12:00
# Time for the first daily automation run (IST)

PM_RUN_TIME_IST=17:00
# Time for the second daily automation run (IST)

RATE_WAIT_TIMEOUT_HOURS=2
# How many hours to wait for IBJA rates before aborting
```

---

### Shopify API Version

```
SHOPIFY_API_VERSION=2024-01
# Leave as-is unless you know it needs changing
```

---

### Additional Notes for Copilot

- The project folder already has: `app.py`, `database.py`, `update_prices.py`, `scraper.py`, `requirements.txt`, `Procfile`
- Do not modify any existing files except `requirements.txt` (only to add new dependencies)
- Create a new file called `automation.py` for the entire automation script
- Create a new file called `.env.example` showing all required environment variables (with empty values, safe to commit)
- All secrets must be read from environment variables using `python-dotenv` — never hardcoded
- The script must send a Telegram message on both success and failure at every stage
- Follow the build order in Part 6 of the roadmap — build and structure each stage as a separate clearly commented function inside `automation.py`
- Add proper logging throughout so every action is printed with a timestamp
- After building, tell me exactly what Railway environment variables I need to set and what the Railway Build Command and Start Command should be

---
