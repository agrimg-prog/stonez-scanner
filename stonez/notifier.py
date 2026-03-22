import os,logging,requests
log=logging.getLogger(__name__)

def send_telegram(text:str):
    t=os.getenv("TELEGRAM_BOT_TOKEN","");c=os.getenv("TELEGRAM_CHAT_ID","")
    if not t or not c: log.warning("Telegram not configured."); return
    try:
        r=requests.post(f"https://api.telegram.org/bot{t}/sendMessage",
                        data={"chat_id":c,"text":text,"parse_mode":"HTML"},timeout=10)
        if r.ok: log.info("Telegram sent.")
        else: log.warning(f"Telegram: {r.text}")
    except Exception as e: log.error(f"Telegram: {e}")

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
    lines = [
        "👀 <b>Stonez Watchlist Alert</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"<b>NIFTY:</b> {ctx.get('spot',0):,.1f}",
        f"<b>Daily RSI:</b> {ctx.get('rsi_daily',0)}  |  <b>Hourly RSI:</b> {ctx.get('rsi_hourly',0)}",
        f"<b>India VIX:</b> {ctx.get('india_vix',0)}%",
        f"<b>Condition:</b> {ctx.get('condition','').upper().replace('_',' ')}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for item in items:
        lines += [
            f"<b>{item.side} side approaching trigger zone</b>",
            item.message,
            "",
            f"<b>Prepare:</b> {item.zerodha_hint}",
        ]
    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "Not a trade yet. Wait for confirming candle + RSI trigger.",
        f"<b>Scanned at:</b> {ctx.get('scan_time','')}",
    ]
    return "\n".join(lines)

def format_no_trigger(ctx:dict) -> str:
    return (
        f"📊 <b>Stonez Daily Scan</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>NIFTY:</b> {ctx.get('spot',0):,.1f}\n"
        f"<b>India VIX:</b> {ctx.get('india_vix',0)}%\n"
        f"<b>Daily RSI:</b> {ctx.get('rsi_daily',0)}  |  <b>Hourly RSI:</b> {ctx.get('rsi_hourly',0)}\n"
        f"<b>Condition:</b> {ctx.get('condition','').upper().replace('_',' ')}\n"
        f"<b>Trend:</b> {ctx.get('trend','').upper()}\n"
        f"<b>Data:</b> Yahoo Finance — spot, VIX, OHLC all real\n"
        f"<b>Scanned at:</b> {ctx.get('scan_time','')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"No Stonez setup right now. Watching..."
    )
