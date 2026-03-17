# Stonez Strategy — Free Automated Setup

Zero cost. No Kite subscription. No server. Works on Mac.  
Alerts land on your phone via Telegram automatically.

---

## How it works

```
NSE Public API ──┐
                 ├──► Stonez Scanner ──► Telegram alert on your phone
yfinance        ──┘
```

- Data: NSE's own public option chain endpoint (free, no login) + yfinance for OHLC
- Scanner: applies all 6 Stonez rules, finds ₹70–100 options, checks 20 SMA + RSI + pattern
- Alerts: Telegram message with full trade details
- Automation: GitHub Actions runs it in the cloud twice daily — your Mac can be completely off

**Total monthly cost: ₹0**

---

## Requirements

- MacBook with Python 3.10+
- Telegram account (for alerts)
- GitHub account (free, for automation)
- ~20 minutes to set up once

---

## Step 1 — Check Python on your Mac

Open Terminal (Cmd+Space → type Terminal → Enter):

```bash
python3 --version
```

If you see `Python 3.x.x` → you're good.  
If not → download from https://www.python.org/downloads/ and install.

---

## Step 2 — Download and extract the project

Download `stonez_v2.zip` and extract it. Then in Terminal:

```bash
cd ~/Downloads/stonez_v2
```

---

## Step 3 — One-command setup

```bash
bash setup_mac.sh
```

This script:
- Installs all Python packages (`yfinance`, `pandas`, etc.)
- Creates your `.env` file
- Walks you through Telegram bot setup
- Runs a test scan immediately so you can see it working

---

## Step 4 — Set up Telegram bot (5 minutes)

### 4a — Create the bot
1. Open Telegram on your phone
2. Search for **@BotFather** and start a chat
3. Send: `/newbot`
4. Follow the prompts — give it any name and username
5. BotFather sends you a token like: `7123456789:AAHxxx...`
6. Copy that token

### 4b — Get your chat ID
1. Search for **@userinfobot** on Telegram and start a chat
2. It instantly replies with your user ID (a number like `987654321`)
3. Copy that number

### 4c — Add to .env
Open `.env` in any text editor (TextEdit, VS Code, etc.):

```
TELEGRAM_BOT_TOKEN=7123456789:AAHxxx...
TELEGRAM_CHAT_ID=987654321
```

Test it:
```bash
python3 run_scan.py
```

You should get a Telegram message on your phone within seconds.

---

## Step 5 — Automate with GitHub Actions (fully free, Mac can be off)

This is the recommended automation method. GitHub runs the script on their servers at 9:20 AM and 3:25 PM IST every weekday.

### 5a — Create a GitHub repository

1. Go to https://github.com → Sign in (or create free account)
2. Click **New repository**
3. Name it `stonez-scanner` (private is fine)
4. Click **Create repository**

### 5b — Upload your code

In Terminal (replace `yourusername` with your GitHub username):

```bash
cd ~/Downloads/stonez_v2
git init
git add .
git commit -m "Initial Stonez scanner"
git branch -M main
git remote add origin https://github.com/yourusername/stonez-scanner.git
git push -u origin main
```

If prompted for GitHub credentials, use your username + a Personal Access Token  
(GitHub Settings → Developer Settings → Personal Access Tokens → Classic → generate with `repo` scope).

### 5c — Add your Telegram credentials as GitHub Secrets

1. Go to your repo on GitHub
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Add these two secrets:

| Name | Value |
|------|-------|
| `TELEGRAM_BOT_TOKEN` | your bot token |
| `TELEGRAM_CHAT_ID` | your chat ID |

### 5d — That's it

The workflow file `.github/workflows/stonez.yml` is already in your repo.  
GitHub will now run the scan automatically:
- **9:20 AM IST** every weekday
- **3:25 PM IST** every weekday

To trigger a manual scan: GitHub repo → **Actions** tab → **Stonez Daily Scan** → **Run workflow**

---

## Option B — Automate locally on Mac (Mac must be awake)

If you prefer not to use GitHub, run this once:

```bash
bash setup_cron.sh
```

This adds cron jobs that run at 9:20 AM and 3:25 PM on weekdays.  
Your Mac must be awake at those times. If it's asleep, the scan is skipped.

---

## Running a manual scan anytime

```bash
cd ~/Downloads/stonez_v2
python3 run_scan.py
```

---

## What a Telegram alert looks like

**When a setup is found:**
```
🟢 STONEZ CALL TRIGGER 🔥
━━━━━━━━━━━━━━━━━━━━
Symbol: NIFTY27APR202525000CE
Strike: 25,000  |  Expiry: 27-Apr-2025
Entry: ₹82.5  |  SL: ₹51.0  |  Target: ₹165.0
Risk/lot: ₹2,363  (lot size 75)
━━━━━━━━━━━━━━━━━━━━
Daily RSI: 24.1  |  Hourly RSI: 18.7
Pattern: Hammer
Option above 20 SMA: ✅
NIFTY spot: 23,455
Signal: STRONG
━━━━━━━━━━━━━━━━━━━━
⚠️ Paper trade first. Max 1-2 trades/month.
```

**When no setup is found (daily update):**
```
📊 Stonez Daily Scan
━━━━━━━━━━━━━━━━━━━━
NIFTY: 23,455
Daily RSI: 48.2  |  Hourly RSI: 52.1
Condition: NEUTRAL
No valid Stonez setup right now. Watching...
```

---

## How the scanner decides

| Check | Logic |
|-------|-------|
| Premium range | Only looks at options priced ₹65–105 |
| Expiry | Before 10th → current month, after 10th → next month |
| Direction | Daily RSI ≤30 → scans CALL side. RSI ≥75 → scans PUT side |
| Option 20 SMA | Option's own price must be above its 20-day SMA |
| Pattern | Detects Doji, Engulfing, Hammer, Shooting Star on daily NIFTY chart |
| Liquidity | Skips strikes with low volume/OI |
| Signal strength | STRONG = all conditions met. MODERATE = 2 of 3. |

---

## Disclaimer

Educational tool based on a publicly available YouTube strategy. Not financial advice.  
Paper trade for 3–4 months before using real money.
