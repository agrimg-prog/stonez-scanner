"""
bot_handler.py
Public Telegram bot — anyone who sends /start gets subscribed.
Polls Telegram getUpdates every 5 minutes via GitHub Actions.

Commands:
  /start   — subscribe to alerts
  /stop    — unsubscribe
  /trade   — run a live scan right now and reply
  /status  — get current NIFTY market snapshot
  /help    — show all commands

Subscribers stored in subscribers.json (committed back to repo).
"""

import os
import sys
import json
import logging
import requests
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN", "")
API         = f"https://api.telegram.org/bot{TOKEN}"
SUBS_FILE   = Path(__file__).parent / "subscribers.json"
OFFSET_FILE = Path(__file__).parent / "bot_offset.json"


# ── Subscriber store ──────────────────────────────────────────────────────────

def load_subscribers() -> dict:
    """
    Returns dict: {chat_id_str: {name, username, joined_at}}
    """
    if not SUBS_FILE.exists():
        return {}
    try:
        with open(SUBS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_subscribers(subs: dict):
    with open(SUBS_FILE, "w") as f:
        json.dump(subs, f, indent=2)
    log.info(f"Subscribers saved: {len(subs)} total")


def load_offset() -> int:
    if not OFFSET_FILE.exists():
        return 0
    try:
        with open(OFFSET_FILE) as f:
            return json.load(f).get("offset", 0)
    except Exception:
        return 0


def save_offset(offset: int):
    with open(OFFSET_FILE, "w") as f:
        json.dump({"offset": offset}, f)


# ── Telegram API helpers ──────────────────────────────────────────────────────

def send_message(chat_id, text: str, parse_mode: str = "HTML"):
    try:
        r = requests.post(f"{API}/sendMessage",
                          data={"chat_id": chat_id, "text": text,
                                "parse_mode": parse_mode},
                          timeout=10)
        if not r.ok:
            log.warning(f"Send to {chat_id} failed: {r.text[:100]}")
    except Exception as e:
        log.error(f"Send error to {chat_id}: {e}")


def broadcast(text: str, subs: dict):
    """Send message to all subscribers."""
    dead = []
    for chat_id in subs:
        try:
            r = requests.post(f"{API}/sendMessage",
                              data={"chat_id": chat_id, "text": text,
                                    "parse_mode": "HTML"},
                              timeout=10)
            if not r.ok:
                err = r.json().get("description", "")
                if any(x in err for x in ["blocked", "not found", "deactivated", "kicked"]):
                    log.warning(f"Dead subscriber {chat_id}: {err}")
                    dead.append(chat_id)
        except Exception as e:
            log.error(f"Broadcast error to {chat_id}: {e}")

    # Auto-remove dead subscribers
    for chat_id in dead:
        subs.pop(chat_id, None)
    if dead:
        save_subscribers(subs)
        log.info(f"Removed {len(dead)} dead subscriber(s)")


def get_updates(offset: int) -> list:
    try:
        r = requests.get(f"{API}/getUpdates",
                         params={"offset": offset, "timeout": 5, "limit": 100},
                         timeout=15)
        if r.ok:
            return r.json().get("result", [])
    except Exception as e:
        log.error(f"getUpdates error: {e}")
    return []


# ── Command handlers ──────────────────────────────────────────────────────────

def handle_start(chat_id: str, user: dict, subs: dict):
    name = user.get("first_name", "there")
    if chat_id in subs:
        send_message(chat_id,
            f"👋 Hey {name}! You're already subscribed.\n\n"
            f"You'll receive Stonez NIFTY signals automatically.\n"
            f"Type /help to see all commands.")
        return

    subs[chat_id] = {
        "name":      name,
        "username":  user.get("username", ""),
        "joined_at": datetime.now().isoformat(),
    }
    save_subscribers(subs)
    log.info(f"New subscriber: {name} ({chat_id})")

    send_message(chat_id,
        f"✅ <b>Welcome {name}! You're now subscribed to Stonez signals.</b>\n\n"
        f"<b>What you'll receive:</b>\n"
        f"• 🟢 CALL or 🔴 PUT signal when RSI hits extremes\n"
        f"• 👀 Watchlist alerts when RSI is approaching\n"
        f"• 📊 Daily market scan at 9:20 AM and 3:25 PM IST\n\n"
        f"<b>Commands you can use anytime:</b>\n"
        f"/trade — run a live scan right now\n"
        f"/status — current NIFTY snapshot\n"
        f"/stop — unsubscribe\n"
        f"/help — show this message\n\n"
        f"⚠️ This is for educational purposes. Always verify signals on Zerodha before trading."
    )


def handle_stop(chat_id: str, subs: dict):
    if chat_id in subs:
        name = subs[chat_id].get("name", "")
        subs.pop(chat_id)
        save_subscribers(subs)
        send_message(chat_id,
            f"👋 You've been unsubscribed, {name}.\n"
            f"Send /start anytime to subscribe again.")
        log.info(f"Unsubscribed: {chat_id}")
    else:
        send_message(chat_id, "You're not subscribed. Send /start to subscribe.")


def handle_help(chat_id: str):
    send_message(chat_id,
        f"<b>Stonez Signal Bot — Commands</b>\n\n"
        f"/start — Subscribe to NIFTY signals\n"
        f"/stop — Unsubscribe\n"
        f"/trade — Run a live scan right now\n"
        f"/status — Current NIFTY snapshot (RSI, VIX, trend)\n"
        f"/help — Show this message\n\n"
        f"<b>About this bot:</b>\n"
        f"Scans NIFTY for Stonez strategy setups:\n"
        f"• RSI daily + hourly extremes\n"
        f"• Price action patterns (Doji, Engulfing, Hammer)\n"
        f"• India VIX for volatility context\n"
        f"• 20 SMA trend filter\n\n"
        f"Automatic scans run at 9:20 AM and 3:25 PM IST on weekdays.\n\n"
        f"⚠️ Educational only. Not financial advice."
    )


def handle_trade(chat_id: str):
    """Run a live scan and send the result directly to this user."""
    send_message(chat_id, "🔄 Running live scan... please wait.")

    try:
        from stonez.scanner  import StonezScanner, SignalStrength
        from stonez.notifier import format_trigger, format_watchlist, format_no_trigger

        scanner = StonezScanner()
        result  = scanner.run_full_scan()
        ctx     = result.market_context

        if result.triggers:
            for t in result.triggers:
                from stonez.notifier import format_trigger
                send_message(chat_id, format_trigger(t))
        elif result.watchlist:
            send_message(chat_id, format_watchlist(result.watchlist, ctx))
        else:
            send_message(chat_id, format_no_trigger(ctx))

    except Exception as e:
        log.error(f"/trade error: {e}", exc_info=True)
        send_message(chat_id,
            f"❌ Scan failed: {str(e)[:200]}\n"
            f"Try again in a few minutes.")


def handle_status(chat_id: str):
    """Send current market snapshot."""
    try:
        from stonez.scanner import StonezScanner
        scanner = StonezScanner()
        ctx     = scanner.get_market_context()

        cond  = ctx.get("condition","").upper().replace("_"," ")
        trend = ctx.get("trend","").upper()

        # Condition emoji
        cond_icon = {
            "OVERSOLD EXTREME": "🔥",
            "OVERSOLD":         "🟢",
            "NEAR OVERSOLD":    "🟡",
            "OVERBOUGHT EXTREME":"🔥",
            "OVERBOUGHT":       "🔴",
            "NEAR OVERBOUGHT":  "🟡",
        }.get(cond, "⚪")

        send_message(chat_id,
            f"📊 <b>NIFTY Live Snapshot</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Spot:</b> {ctx.get('spot',0):,.1f}\n"
            f"<b>India VIX:</b> {ctx.get('india_vix',0)}%\n"
            f"<b>Daily RSI:</b> {ctx.get('rsi_daily',0)}\n"
            f"<b>Hourly RSI:</b> {ctx.get('rsi_hourly',0)}\n"
            f"<b>20 SMA:</b> {ctx.get('sma_20',0):,.0f}\n"
            f"<b>Trend:</b> {trend}\n"
            f"<b>Condition:</b> {cond_icon} {cond}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Data: Yahoo Finance — real time</i>\n"
            f"<b>As of:</b> {ctx.get('scan_time','')}"
        )
    except Exception as e:
        log.error(f"/status error: {e}")
        send_message(chat_id, f"❌ Could not fetch market data: {str(e)[:200]}")


# ── Main polling loop ─────────────────────────────────────────────────────────

def process_updates():
    if not TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set.")
        return

    subs   = load_subscribers()
    offset = load_offset()

    log.info(f"Polling for updates. Offset: {offset} | Subscribers: {len(subs)}")

    updates = get_updates(offset)

    if not updates:
        log.info("No new updates.")
        return

    new_offset = offset
    for update in updates:
        new_offset = max(new_offset, update["update_id"] + 1)

        msg = update.get("message") or update.get("edited_message")
        if not msg:
            continue

        chat_id = str(msg["chat"]["id"])
        user    = msg.get("from", {})
        text    = msg.get("text", "").strip().lower()

        log.info(f"Message from {chat_id} ({user.get('first_name','')}): {text[:50]}")

        # Route commands
        if text.startswith("/start"):
            handle_start(chat_id, user, subs)
        elif text.startswith("/stop"):
            handle_stop(chat_id, subs)
        elif text.startswith("/trade") or text == "trade":
            handle_trade(chat_id)
        elif text.startswith("/status") or text == "status":
            handle_status(chat_id)
        elif text.startswith("/help"):
            handle_help(chat_id)
        else:
            # Unknown message — if not subscribed, nudge them
            if chat_id not in subs:
                send_message(chat_id,
                    "👋 Send /start to subscribe to NIFTY Stonez signals.\n"
                    "Or /help to see all commands.")

    save_offset(new_offset)
    log.info(f"Processed {len(updates)} update(s). New offset: {new_offset}")


if __name__ == "__main__":
    process_updates()
