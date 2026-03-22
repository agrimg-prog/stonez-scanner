"""
notifier.py — all alert formatters.
"""

import os, logging, requests
log = logging.getLogger(__name__)


def send_telegram(text: str):
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log.warning("Telegram not configured.")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10)
        if r.ok: log.info("Telegram sent.")
        else:    log.warning(f"Telegram error: {r.text}")
    except Exception as e:
        log.error(f"Telegram failed: {e}")


def format_trigger(t) -> str:
    icon  = "🟢" if t.side == "CALL" else "🔴"
    sicon = {"STRONG": "🔥", "MODERATE": "⚡"}.get(t.signal_strength.value, "")
    return (
        f"{icon} <b>STONEZ {t.side} TRIGGER</b> {sicon}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Symbol:</b> <code>{t.symbol}</code>\n"
        f"<b>Strike:</b> {t.strike:,.0f}  |  <b>Expiry:</b> {t.expiry}\n"
        f"<b>Current premium (live):</b> ₹{t.entry_price}\n"
        f"<b>SL:</b> ₹{t.sl_price}  |  <b>Target:</b> ₹{t.target_price} (2×)\n"
        f"<b>Risk/lot:</b> ₹{t.risk_per_lot:,.0f}  |  <b>IV:</b> {t.iv}%\n"
        f"<b>Days to expiry:</b> {t.dte}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Daily RSI:</b> {t.rsi_daily}  |  <b>Hourly RSI:</b> {t.rsi_hourly}\n"
        f"<b>Pattern:</b> {t.price_pattern.replace('_',' ').title()}\n"
        f"<b>NIFTY spot:</b> {t.spot_level:,.1f}\n"
        f"<b>Signal:</b> {t.signal_strength.value}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Verify premium on Zerodha before entering. Max 1-2 trades/month."
    )


def format_watchlist(items: list, ctx: dict) -> str:
    lines = [
        "👀 <b>Stonez Watchlist Alert</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"<b>NIFTY:</b> {ctx.get('spot',0):,.1f}",
        f"<b>Daily RSI:</b> {ctx.get('rsi_daily',0)}  |  <b>Hourly RSI:</b> {ctx.get('rsi_hourly',0)}",
        f"<b>Condition:</b> {ctx.get('condition','').upper().replace('_',' ')}",
        f"<b>Data age:</b> {ctx.get('data_age','')}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for item in items:
        lines += [
            f"<b>{item.side} side approaching trigger zone</b>",
            item.message,
        ]
        if item.symbol:
            lines += [
                "",
                f"<b>Option to prepare:</b> <code>{item.symbol}</code>",
                f"<b>Strike:</b> {item.strike:,.0f}  |  <b>Expiry:</b> {item.expiry}",
                f"<b>Live premium now:</b> ₹{item.entry_price}",
                f"<b>SL if entered:</b> ₹{item.sl_price}",
                f"<b>Target:</b> ₹{item.target_price} (2×)",
                f"<b>IV:</b> {item.iv}%",
            ]
    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "Not a trade alert yet. Wait for confirming candle.",
        f"<b>Scanned at:</b> {ctx.get('scan_time','')}",
    ]
    return "\n".join(lines)


def format_no_trigger(ctx: dict) -> str:
    return (
        f"📊 <b>Stonez Daily Scan</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>NIFTY:</b> {ctx.get('spot',0):,.1f}\n"
        f"<b>Daily RSI:</b> {ctx.get('rsi_daily',0)}  |  <b>Hourly RSI:</b> {ctx.get('rsi_hourly',0)}\n"
        f"<b>Condition:</b> {ctx.get('condition','').upper().replace('_',' ')}\n"
        f"<b>Trend:</b> {ctx.get('trend','').upper()}\n"
        f"<b>Data source:</b> {ctx.get('data_source','')}\n"
        f"<b>Data age:</b> {ctx.get('data_age','')}\n"
        f"<b>Scanned at:</b> {ctx.get('scan_time','')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"No Stonez setup right now. Watching..."
    )
