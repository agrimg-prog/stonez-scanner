"""
notifier.py — broadcasts to all subscribers from subscribers.json.
Drop-in replacement for the existing stonez/notifier.py.
"""

import os
import json
import logging
import requests
from pathlib import Path

log = logging.getLogger(__name__)
API      = "https://api.telegram.org/bot{token}/sendMessage"
SUBS_FILE= Path(__file__).parent.parent / "subscribers.json"


def _get_recipients() -> list:
    """
    Priority:
    1. subscribers.json (anyone who /start'd the bot)
    2. TELEGRAM_CHAT_IDS env var (comma-separated, legacy)
    3. TELEGRAM_CHAT_ID env var (single ID, legacy)
    """
    ids = set()

    # subscribers.json
    if SUBS_FILE.exists():
        try:
            with open(SUBS_FILE) as f:
                subs = json.load(f)
            ids.update(subs.keys())
        except Exception as e:
            log.warning(f"Could not read subscribers.json: {e}")

    # Legacy env vars
    multi  = os.getenv("TELEGRAM_CHAT_IDS", "").strip()
    single = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if multi:
        ids.update(cid.strip() for cid in multi.split(",") if cid.strip())
    if single:
        ids.add(single)

    return list(ids)


def send_telegram(text: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN not set.")
        return

    recipients = _get_recipients()
    if not recipients:
        log.warning("No recipients. Add TELEGRAM_CHAT_ID to GitHub Secrets or have users /start the bot.")
        return

    url  = API.format(token=token)
    dead = []
    for chat_id in recipients:
        try:
            r = requests.post(url,
                              data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                              timeout=10)
            if r.ok:
                log.info(f"Sent to {chat_id}")
            else:
                err = r.json().get("description", "")
                if any(x in err for x in ["blocked","not found","deactivated","kicked"]):
                    log.warning(f"Dead: {chat_id} — {err}")
                    dead.append(chat_id)
                else:
                    log.warning(f"Error {chat_id}: {err}")
        except Exception as e:
            log.error(f"Send failed {chat_id}: {e}")

    # Clean dead subscribers
    if dead and SUBS_FILE.exists():
        try:
            with open(SUBS_FILE) as f: subs = json.load(f)
            for d in dead: subs.pop(d, None)
            with open(SUBS_FILE, "w") as f: json.dump(subs, f, indent=2)
            log.info(f"Removed {len(dead)} dead subscriber(s)")
        except Exception: pass


def format_trigger(t) -> str:
    icon = "🟢" if t.side=="CALL" else "🔴"
    si   = {"STRONG":"🔥","MODERATE":"⚡"}.get(t.signal_strength.value,"")
    cond = t.condition.upper().replace("_"," ")
    return (
        f"{icon} <b>STONEZ {t.side} SIGNAL</b> {si}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>NIFTY spot:</b> {t.spot_level:,.1f}\n"
        f"<b>Daily RSI:</b> {t.rsi_daily}  |  <b>Hourly RSI:</b> {t.rsi_hourly}\n"
        f"<b>Pattern:</b> {t.price_pattern.replace('_',' ').title()}\n"
        f"<b>Trend:</b> {t.trend.upper()}  |  <b>Condition:</b> {cond}\n"
        f"<b>India VIX:</b> {t.india_vix}%  |  <b>20 SMA:</b> {t.sma_20:,.0f}\n"
        f"<b>Expiry:</b> {t.expiry_str}  |  <b>DTE:</b> {t.dte} days\n"
        f"<b>Signal:</b> {t.signal_strength.value}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>ACTION — Open Zerodha now:</b>\n"
        f"{t.zerodha_action}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Stonez rules once you find the strike:</b>\n"
        f"• Only enter if LTP is ₹70–100\n"
        f"• SL = 30–35 pts below your entry price\n"
        f"• Target = 2× your entry price\n"
        f"• Max 1 lot. Max 1–2 trades/month.\n"
        f"• Paper trade first."
    )


def format_watchlist(items:list, ctx:dict) -> str:
    lines=["👀 <b>Stonez Watchlist Alert</b>","━━━━━━━━━━━━━━━━━━━━",
           f"<b>NIFTY:</b> {ctx.get('spot',0):,.1f}",
           f"<b>Daily RSI:</b> {ctx.get('rsi_daily',0)}  |  <b>Hourly RSI:</b> {ctx.get('rsi_hourly',0)}",
           f"<b>India VIX:</b> {ctx.get('india_vix',0)}%",
           f"<b>Condition:</b> {ctx.get('condition','').upper().replace('_',' ')}",
           "━━━━━━━━━━━━━━━━━━━━"]
    for item in items:
        lines+=[f"<b>{item.side} side approaching trigger zone</b>",item.message,"",
                f"<b>Prepare:</b> {item.zerodha_hint}"]
    lines+=["━━━━━━━━━━━━━━━━━━━━",
            "Not a trade yet. Wait for confirming candle + RSI trigger.",
            f"<b>Scanned at:</b> {ctx.get('scan_time','')}"]
    return "\n".join(lines)


def format_no_trigger(ctx:dict) -> str:
    return (
        f"📊 <b>Stonez Daily Scan</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>NIFTY:</b> {ctx.get('spot',0):,.1f}\n"
        f"<b>India VIX:</b> {ctx.get('india_vix',0)}%\n"
        f"<b>Daily RSI:</b> {ctx.get('rsi_daily',0)}  |  <b>Hourly RSI:</b> {ctx.get('rsi_hourly',0)}\n"
        f"<b>Condition:</b> {ctx.get('condition','').upper().replace('_',' ')}\n"
        f"<b>Trend:</b> {ctx.get('trend','').upper()}\n"
        f"<b>Data:</b> Yahoo Finance — spot, VIX, OHLC all real\n"
        f"<b>Scanned at:</b> {ctx.get('scan_time','')}\n━━━━━━━━━━━━━━━━━━━━\n"
        f"No Stonez setup right now. Watching..."
    )
