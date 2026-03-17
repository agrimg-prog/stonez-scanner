"""
Notifier — sends Stonez alerts via Telegram (free).
"""

import os
import logging
import requests

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram(text: str):
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log.warning("Telegram not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
        return
    try:
        resp = requests.post(
            TELEGRAM_API.format(token=token),
            data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if resp.ok:
            log.info("Telegram alert sent.")
        else:
            log.warning(f"Telegram error: {resp.text}")
    except Exception as e:
        log.error(f"Telegram send failed: {e}")


def format_trigger(t) -> str:
    side_label = "CALL (BUY)" if t.side == "CALL" else "PUT (BUY)"
    icon       = "🟢" if t.side == "CALL" else "🔴"
    strength_icon = {"STRONG": "🔥", "MODERATE": "⚡"}.get(t.signal_strength.value, "")

    return (
        f"{icon} <b>STONEZ {side_label} TRIGGER</b> {strength_icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Symbol:</b> {t.symbol}\n"
        f"<b>Strike:</b> {t.strike:,.0f}  |  <b>Expiry:</b> {t.expiry}\n"
        f"<b>Entry:</b> ₹{t.entry_price}  |  <b>SL:</b> ₹{t.sl_price}  |  <b>Target:</b> ₹{t.target_price}\n"
        f"<b>Risk/lot:</b> ₹{t.risk_per_lot:,.0f}  (lot size 75)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Daily RSI:</b> {t.rsi_daily}  |  <b>Hourly RSI:</b> {t.rsi_hourly}\n"
        f"<b>Pattern:</b> {t.price_pattern.replace('_', ' ').title()}\n"
        f"<b>Option above 20 SMA:</b> {'✅' if t.above_20sma else '❌'}\n"
        f"<b>NIFTY spot:</b> {t.spot_level:,.0f}\n"
        f"<b>Signal:</b> {t.signal_strength.value}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Paper trade first. Max 1-2 trades/month."
    )


def format_no_trigger(ctx: dict) -> str:
    return (
        f"📊 <b>Stonez Daily Scan</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>NIFTY:</b> {ctx.get('spot', 0):,.0f}\n"
        f"<b>Daily RSI:</b> {ctx.get('rsi_daily', 0)}  |  <b>Hourly RSI:</b> {ctx.get('rsi_hourly', 0)}\n"
        f"<b>Condition:</b> {ctx.get('condition', '').upper()}\n"
        f"<b>Trend:</b> {ctx.get('trend', '').upper()}\n"
        f"<b>Expiry logic:</b> {ctx.get('stonez_expiry', '').replace('_', ' ').title()}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"No valid Stonez setup right now. Watching..."
    )
