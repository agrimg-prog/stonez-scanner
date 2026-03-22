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

def format_trigger(t)->str:
    icon="🟢" if t.side=="CALL" else "🔴"
    si={"STRONG":"🔥","MODERATE":"⚡"}.get(t.signal_strength.value,"")
    return(f"{icon} <b>STONEZ {t.side} TRIGGER</b> {si}\n"
           f"━━━━━━━━━━━━━━━━━━━━\n"
           f"<b>Symbol to check:</b> <code>{t.symbol}</code>\n"
           f"<b>Strike:</b> {t.strike:,.0f}  |  <b>Expiry:</b> {t.expiry}\n"
           f"<b>Est. premium (BS+VIX):</b> ₹{t.estimated_premium}\n"
           f"<b>SL:</b> ₹{t.sl_price}  |  <b>Target:</b> ₹{t.target_price} (2×)\n"
           f"<b>Est. risk/lot:</b> ₹{t.risk_per_lot:,.0f}  |  <b>India VIX:</b> {t.india_vix}%\n"
           f"<b>DTE:</b> {t.dte} days\n"
           f"━━━━━━━━━━━━━━━━━━━━\n"
           f"<b>Daily RSI:</b> {t.rsi_daily}  |  <b>Hourly RSI:</b> {t.rsi_hourly}\n"
           f"<b>Pattern:</b> {t.price_pattern.replace('_',' ').title()}\n"
           f"<b>NIFTY spot:</b> {t.spot_level:,.1f}\n"
           f"<b>Signal:</b> {t.signal_strength.value}\n"
           f"━━━━━━━━━━━━━━━━━━━━\n"
           f"⚠️ <b>Premium is estimated.</b> Open Zerodha option chain, find this strike, check actual LTP before entering.\n"
           f"Max 1-2 trades/month. Paper trade first.")

def format_watchlist(items:list,ctx:dict)->str:
    lines=["👀 <b>Stonez Watchlist Alert</b>","━━━━━━━━━━━━━━━━━━━━",
           f"<b>NIFTY:</b> {ctx.get('spot',0):,.1f}",
           f"<b>Daily RSI:</b> {ctx.get('rsi_daily',0)}  |  <b>Hourly RSI:</b> {ctx.get('rsi_hourly',0)}",
           f"<b>India VIX:</b> {ctx.get('india_vix',0)}%",
           f"<b>Condition:</b> {ctx.get('condition','').upper().replace('_',' ')}",
           "━━━━━━━━━━━━━━━━━━━━"]
    for item in items:
        lines+=[f"<b>{item.side} side — approaching trigger zone</b>",item.message]
        if item.symbol:
            lines+=["",
                    f"<b>Strike to watch:</b> <code>{item.symbol}</code>",
                    f"<b>Strike:</b> {item.strike:,.0f}  |  <b>Expiry:</b> {item.expiry}",
                    f"<b>Est. premium now:</b> ₹{item.estimated_premium}  (BS + VIX {item.india_vix}%)",
                    f"<b>SL if entered:</b> ₹{item.sl_price}",
                    f"<b>Target:</b> ₹{item.target_price} (2×)"]
    lines+=["━━━━━━━━━━━━━━━━━━━━",
            "Not a trade yet. Wait for confirming candle.",
            "⚠️ Verify actual premium on Zerodha before entering.",
            f"<b>Scanned at:</b> {ctx.get('scan_time','')}"]
    return "\n".join(lines)

def format_no_trigger(ctx:dict)->str:
    return(f"📊 <b>Stonez Daily Scan</b>\n━━━━━━━━━━━━━━━━━━━━\n"
           f"<b>NIFTY:</b> {ctx.get('spot',0):,.1f}\n"
           f"<b>India VIX:</b> {ctx.get('india_vix',0)}%\n"
           f"<b>Daily RSI:</b> {ctx.get('rsi_daily',0)}  |  <b>Hourly RSI:</b> {ctx.get('rsi_hourly',0)}\n"
           f"<b>Condition:</b> {ctx.get('condition','').upper().replace('_',' ')}\n"
           f"<b>Trend:</b> {ctx.get('trend','').upper()}\n"
           f"<b>Data:</b> Yahoo Finance (spot, VIX, OHLC — all real)\n"
           f"<b>Scanned at:</b> {ctx.get('scan_time','')}\n━━━━━━━━━━━━━━━━━━━━\n"
           f"No Stonez setup right now. Watching...")
