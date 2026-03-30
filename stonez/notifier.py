"""
notifier.py — sends Telegram messages to all subscribers (or one user).

Fixed:
  ★ format_trigger now includes estimated premium, strike, SL, and target
  ★ send_to_one() added for bot_handler.py to reply to specific /trade requests
  ★ Dead subscriber cleanup retained
"""

import os, json, logging, requests
from pathlib import Path

log      = logging.getLogger(__name__)
API      = "https://api.telegram.org/bot{token}/sendMessage"
SUBS_FILE = Path(__file__).parent.parent / "subscribers.json"


# ── Recipient helpers ─────────────────────────────────────────────────────────

def _get_recipients() -> list:
    """
    Priority order:
      1. subscribers.json  (users who /start'd the bot)
      2. TELEGRAM_CHAT_IDS env var  (comma-separated, legacy)
      3. TELEGRAM_CHAT_ID  env var  (single ID, legacy)
    """
    ids = set()

    if SUBS_FILE.exists():
        try:
            subs = json.loads(SUBS_FILE.read_text())
            ids.update(subs.keys())
            log.info(f"Loaded {len(subs)} subscriber(s) from subscribers.json")
        except Exception as e:
            log.warning(f"Could not read subscribers.json: {e}")

    multi  = os.getenv("TELEGRAM_CHAT_IDS", "").strip()
    single = os.getenv("TELEGRAM_CHAT_ID",  "").strip()
    if multi:
        ids.update(c.strip() for c in multi.split(",") if c.strip())
    if single:
        ids.add(single)

    return list(ids)


def _send(token: str, chat_id: str, text: str) -> bool:
    try:
        r = requests.post(
            API.format(token=token),
            data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if r.ok:
            return True
        err = r.json().get("description", "")
        log.warning(f"Send to {chat_id}: {err}")
        return False
    except Exception as e:
        log.error(f"Send failed {chat_id}: {e}")
        return False


# ── Public send functions ─────────────────────────────────────────────────────

def send_telegram(text: str):
    """Broadcast to ALL subscribers."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN not set.")
        return

    recipients = _get_recipients()
    if not recipients:
        log.warning("No recipients. Add TELEGRAM_CHAT_ID to GitHub Secrets "
                    "or have users /start the bot.")
        return

    dead = []
    for chat_id in recipients:
        ok = _send(token, chat_id, text)
        if not ok:
            # Check for permanently dead chats
            try:
                r = requests.post(
                    API.format(token=token),
                    data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                    timeout=10,
                )
                err = r.json().get("description", "")
                if any(x in err for x in
                       ["blocked", "not found", "deactivated", "kicked", "bot was blocked"]):
                    dead.append(chat_id)
            except Exception:
                pass

    # Prune dead subscribers
    if dead and SUBS_FILE.exists():
        try:
            subs = json.loads(SUBS_FILE.read_text())
            for d in dead:
                subs.pop(d, None)
            SUBS_FILE.write_text(json.dumps(subs, indent=2))
            log.info(f"Removed {len(dead)} dead subscriber(s): {dead}")
        except Exception:
            pass

    log.info(f"Broadcast complete. Sent to {len(recipients)} recipient(s).")


def send_to_one(chat_id: str, text: str):
    """Send to a single chat_id (used by bot_handler for /trade replies)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN not set.")
        return
    _send(token, chat_id, text)


# ── Message formatters ────────────────────────────────────────────────────────

def format_trigger(t) -> str:
    """
    Full trigger alert — now includes estimated premium, strike, SL and target.
    t is a Trigger dataclass from scanner.py.
    """
    icon = "🟢" if t.side == "CALL" else "🔴"
    si   = {"STRONG": "🔥", "MODERATE": "⚡"}.get(t.signal_strength.value, "")
    cond = t.condition.upper().replace("_", " ")

    return (
        f"{icon} <b>STONEZ {t.side} SIGNAL</b> {si}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>NIFTY Spot:</b>   {t.spot_level:,.1f}\n"
        f"<b>Daily RSI:</b>    {t.rsi_daily}  "
        f"|  <b>Hourly RSI:</b> {t.rsi_hourly}\n"
        f"<b>India VIX:</b>   {t.india_vix}%  "
        f"|  <b>20 SMA:</b> {t.sma_20:,.0f}\n"
        f"<b>Pattern:</b>     {t.price_pattern.replace('_', ' ').title()}\n"
        f"<b>Trend:</b>       {t.trend.upper()}  "
        f"|  <b>Condition:</b> {cond}\n"
        f"<b>Expiry:</b>      {t.expiry_str}  "
        f"|  <b>DTE:</b> {t.dte} days\n"
        f"<b>Signal:</b>      {t.signal_strength.value}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"★ <b>OPTION TO BUY (estimated)</b>\n"
        f"<b>Strike:</b>   {t.best_strike:,}  {('CE' if t.side=='CALL' else 'PE')}\n"
        f"<b>Est. LTP:</b> ₹{t.estimated_premium:.0f}  "
        f"<i>(verify on Zerodha before entering)</i>\n"
        f"<b>SL:</b>       ₹{t.sl_price:.0f}  "
        f"(−{t.estimated_premium - t.sl_price:.0f} pts)\n"
        f"<b>Target:</b>   ₹{t.target_price:.0f}  (2×)\n"
        f"<b>Max risk:</b> ₹{round((t.estimated_premium - t.sl_price) * 75):,.0f}  "
        f"(1 lot × {t.estimated_premium - t.sl_price:.0f} pts × 75)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>ACTION — Open Zerodha now:</b>\n"
        f"{t.zerodha_action}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Stonez rules:</b>\n"
        f"• Only enter if LTP is ₹70–100\n"
        f"• SL = 30–35 pts below your entry\n"
        f"• Target = 2× your entry price\n"
        f"• Max 1 lot. Max 1–2 trades/month.\n"
        f"• Paper trade at least 3–4 months first."
    )


def format_watchlist(items: list, ctx: dict) -> str:
    lines = [
        "👀 <b>Stonez Watchlist — Approaching Setup</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"<b>NIFTY:</b>      {ctx.get('spot', 0):,.1f}",
        f"<b>Daily RSI:</b>  {ctx.get('rsi_daily', 0)}"
        f"  |  <b>Hourly RSI:</b> {ctx.get('rsi_hourly', 0)}",
        f"<b>India VIX:</b>  {ctx.get('india_vix', 0)}%",
        f"<b>Condition:</b>  {ctx.get('condition','').upper().replace('_',' ')}",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for item in items:
        lines += [
            f"<b>{item.side} side nearing trigger zone</b>",
            item.message,
            "",
            f"<b>Prepare:</b> {item.zerodha_hint}",
            "",
        ]
    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "⚠️ Not a trade yet. Wait for RSI trigger + confirming candle.",
        f"<b>Scanned at:</b> {ctx.get('scan_time', '')}",
    ]
    return "\n".join(lines)


def format_no_trigger(ctx: dict) -> str:
    return (
        f"📊 <b>Stonez Morning Scan</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>NIFTY:</b>      {ctx.get('spot', 0):,.1f}\n"
        f"<b>India VIX:</b>  {ctx.get('india_vix', 0)}%\n"
        f"<b>Daily RSI:</b>  {ctx.get('rsi_daily', 0)}"
        f"  |  <b>Hourly RSI:</b> {ctx.get('rsi_hourly', 0)}\n"
        f"<b>Condition:</b>  {ctx.get('condition','').upper().replace('_',' ')}\n"
        f"<b>Trend:</b>      {ctx.get('trend','').upper()}\n"
        f"<b>20 SMA:</b>     {ctx.get('sma_20', 0):,.0f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"No Stonez setup today. Watching...\n"
        f"<i>Next automated alert only when RSI hits extreme.</i>\n"
        f"<b>Scanned at:</b> {ctx.get('scan_time', '')}"
    )
