"""
bot_handler.py — polls Telegram for user commands and handles them.
Runs every 5 min (market hours) via GitHub Actions.

Commands handled:
  /start       → subscribe to signals
  /stop        → unsubscribe
  /trade       → live scan right now, reply to THIS user only
  /status      → show current active trade state
  /scan        → alias for /trade
  /help        → command list

Also handles plain text: "trade", "scan", "status"
"""

import os, json, logging, sys, requests
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
sys.path.insert(0, ".")

BOT_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API_BASE    = f"https://api.telegram.org/bot{BOT_TOKEN}"
SUBS_FILE   = Path("subscribers.json")
OFFSET_FILE = Path("bot_offset.json")


# ── Persistence ───────────────────────────────────────────────────────────────

def load_subscribers() -> dict:
    if SUBS_FILE.exists():
        try:
            return json.loads(SUBS_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_subscribers(subs: dict):
    SUBS_FILE.write_text(json.dumps(subs, indent=2))


def load_offset() -> int:
    if OFFSET_FILE.exists():
        try:
            return int(json.loads(OFFSET_FILE.read_text()).get("offset", 0))
        except Exception:
            return 0
    return 0


def save_offset(offset: int):
    OFFSET_FILE.write_text(json.dumps({"offset": offset, "updated": datetime.now().isoformat()}))


# ── Telegram API ──────────────────────────────────────────────────────────────

def get_updates(offset: int) -> list:
    try:
        r = requests.get(
            f"{API_BASE}/getUpdates",
            params={"offset": offset, "timeout": 5, "limit": 100},
            timeout=20,
        )
        if r.ok:
            return r.json().get("result", [])
        log.warning(f"getUpdates HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.error(f"getUpdates error: {e}")
    return []


def send_one(chat_id: str, text: str):
    """Send a message to one specific chat_id."""
    try:
        r = requests.post(
            f"{API_BASE}/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        if r.ok:
            log.info(f"Replied to {chat_id}")
        else:
            log.warning(f"Reply to {chat_id} failed: {r.text[:150]}")
    except Exception as e:
        log.error(f"send_one {chat_id}: {e}")


# ── Command handlers ──────────────────────────────────────────────────────────

def handle_start(chat_id: str, user: str, subs: dict):
    subs[chat_id] = {
        "name":   user,
        "joined": datetime.now().isoformat(),
    }
    send_one(chat_id,
        f"👋 <b>Welcome to Stonez Bot, {user}!</b>\n\n"
        f"You'll receive NIFTY monthly options signals based on the Stonez method.\n\n"
        f"<b>Commands:</b>\n"
        f"  /trade — Live market scan right now\n"
        f"  /status — Current active trade\n"
        f"  /stop — Unsubscribe from alerts\n"
        f"  /help — This list\n\n"
        f"📊 Stonez rule: Premium ₹70–100 | SL ~32 pts | Target 2×\n"
        f"🔔 You are now subscribed to automated alerts."
    )
    log.info(f"New subscriber: {chat_id} ({user})")


def handle_stop(chat_id: str, subs: dict):
    subs.pop(chat_id, None)
    send_one(chat_id,
        "👋 You've been unsubscribed.\n"
        "Send /start any time to resubscribe."
    )
    log.info(f"Unsubscribed: {chat_id}")


def handle_trade(chat_id: str):
    """Run a live scan and reply to this user only."""
    send_one(chat_id, "🔍 Running live Stonez scan… please wait a moment.")
    try:
        from stonez.scanner import StonezScanner
        from stonez.notifier import format_trigger, format_watchlist, format_no_trigger

        scanner = StonezScanner()
        result  = scanner.run_full_scan()
        ctx     = result.market_context

        if result.triggers:
            for t in result.triggers:
                send_one(chat_id, format_trigger(t))
        elif result.watchlist:
            send_one(chat_id, format_watchlist(result.watchlist, {
                **ctx,
                "scan_time": datetime.now().strftime("%d-%b-%Y %I:%M %p IST"),
            }))
        else:
            send_one(chat_id, format_no_trigger({
                **ctx,
                "scan_time": datetime.now().strftime("%d-%b-%Y %I:%M %p IST"),
            }))

    except Exception as e:
        log.error(f"handle_trade error: {e}", exc_info=True)
        send_one(chat_id, f"⚠️ Scan failed: {e}\nCheck GitHub Actions logs.")


def handle_status(chat_id: str):
    """Reply with current trade_state."""
    try:
        from stonez.trade_state import load_state
        s = load_state()

        if s.status == "NONE":
            send_one(chat_id,
                "📭 <b>No active trade.</b>\n"
                "Use /trade to run a live scan."
            )
            return

        icon = "🟢" if s.side == "CALL" else "🔴"
        msg = (
            f"{icon} <b>Trade Status: {s.status}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Side:</b>   {s.side}\n"
            f"<b>Strike:</b> {int(s.strike)}\n"
            f"<b>Expiry:</b> {s.expiry}\n"
            f"<b>Entry:</b>  ₹{s.entry_price}\n"
            f"<b>SL:</b>     ₹{s.sl_price} "
            f"({s.entry_price - s.sl_price:.0f} pts below)\n"
            f"<b>Target:</b> ₹{s.target_price} (2×)\n"
            f"<b>Opened:</b> {s.entered_at[:16].replace('T',' ')}\n"
        )
        if s.status in ("SL_HIT", "TARGET_HIT", "EXITED"):
            msg += (
                f"\n<b>Exit:</b>  ₹{s.exit_price} ({s.exit_reason})\n"
                f"<b>P&L:</b>  {s.pnl_pts:+.1f} pts  |  ₹{s.pnl_rs:+.0f}\n"
            )
        send_one(chat_id, msg)

    except Exception as e:
        log.error(f"handle_status: {e}")
        send_one(chat_id, f"⚠️ Error loading trade state: {e}")


def handle_help(chat_id: str):
    send_one(chat_id,
        "<b>Stonez Bot Commands</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/trade  — Live NIFTY scan right now\n"
        "/status — Active trade info\n"
        "/start  — Subscribe to alerts\n"
        "/stop   — Unsubscribe\n"
        "/help   — This message\n\n"
        "You can also just type: <code>trade</code> or <code>status</code>"
    )


# ── Update dispatcher ─────────────────────────────────────────────────────────

def process_update(update: dict, subs: dict):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return  # callback_query etc — ignore

    chat_id = str(msg["chat"]["id"])
    raw     = msg.get("text", "").strip()
    user    = msg.get("from", {}).get("first_name", "Trader")
    cmd     = raw.lower().split()[0] if raw else ""

    log.info(f"Update from {chat_id} ({user}): {raw!r}")

    if cmd in ("/start", "start"):
        handle_start(chat_id, user, subs)

    elif cmd in ("/stop", "stop"):
        handle_stop(chat_id, subs)

    elif cmd in ("/trade", "trade", "/scan", "scan"):
        handle_trade(chat_id)

    elif cmd in ("/status", "status"):
        handle_status(chat_id)

    elif cmd in ("/help", "help"):
        handle_help(chat_id)

    else:
        # Unknown message — give a hint
        send_one(chat_id,
            "Type /trade for a live scan, /status for trade info, or /help for commands."
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set in environment.")
        sys.exit(1)

    subs    = load_subscribers()
    offset  = load_offset()
    updates = get_updates(offset)

    log.info(f"Fetched {len(updates)} update(s). Current offset={offset}. "
             f"Subscribers={len(subs)}")

    for update in updates:
        try:
            process_update(update, subs)
        except Exception as e:
            log.error(f"process_update error: {e}", exc_info=True)
        finally:
            offset = update["update_id"] + 1  # always advance offset

    save_subscribers(subs)
    save_offset(offset)
    log.info(f"Done. New offset={offset}")


if __name__ == "__main__":
    main()
