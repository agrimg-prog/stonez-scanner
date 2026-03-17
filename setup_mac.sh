#!/bin/bash
# setup_mac.sh — Run once to set everything up on your MacBook
# Usage: bash setup_mac.sh

set -e

echo ""
echo "=== Stonez Setup for macOS ==="
echo ""

# 1. Check Python 3
if ! command -v python3 &>/dev/null; then
  echo "ERROR: Python 3 not found."
  echo "Install it from https://www.python.org/downloads/ then re-run this script."
  exit 1
fi
PYTHON_VERSION=$(python3 --version)
echo "✅ $PYTHON_VERSION found"

# 2. Install pip dependencies
echo ""
echo "Installing Python packages..."
pip3 install -r requirements.txt
echo "✅ Packages installed"

# 3. Create .env if it doesn't exist
if [ ! -f .env ]; then
  cp .env.example .env
  echo "✅ Created .env from template"
else
  echo "✅ .env already exists"
fi

# 4. Prompt for Telegram credentials
echo ""
echo "=== Telegram Setup ==="
echo "You need a Telegram bot to receive alerts on your phone."
echo ""
echo "Step 1: Open Telegram and message @BotFather"
echo "        Send: /newbot"
echo "        Follow instructions — you'll get a token like: 123456789:ABCDEF..."
echo ""
read -p "Paste your Telegram bot token here (or press Enter to skip): " BOT_TOKEN

if [ -n "$BOT_TOKEN" ]; then
  # Save token to .env
  sed -i.bak "s|TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=$BOT_TOKEN|" .env

  echo ""
  echo "Step 2: Message @userinfobot on Telegram"
  echo "        It will reply with your chat ID (a number like 123456789)"
  echo ""
  read -p "Paste your Telegram chat ID here: " CHAT_ID

  if [ -n "$CHAT_ID" ]; then
    sed -i.bak "s|TELEGRAM_CHAT_ID=.*|TELEGRAM_CHAT_ID=$CHAT_ID|" .env
    rm -f .env.bak
    echo "✅ Telegram credentials saved to .env"
  fi
fi

# 5. Test run
echo ""
echo "=== Running a test scan ==="
python3 run_scan.py

echo ""
echo "=== Setup complete! ==="
echo ""
echo "What's next:"
echo ""
echo "  Option A — Run manually any time:"
echo "    python3 run_scan.py"
echo ""
echo "  Option B — Automate locally (runs on your Mac when it's on):"
echo "    bash setup_cron.sh"
echo ""
echo "  Option C — Automate via GitHub (runs in cloud, Mac can be off):"
echo "    See README.md → GitHub Actions setup (5 minutes, free)"
echo ""
