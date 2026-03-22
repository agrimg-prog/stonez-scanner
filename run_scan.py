import os,sys,logging
from dotenv import load_dotenv
load_dotenv()
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log=logging.getLogger(__name__)
def main():
    from stonez.scanner     import StonezScanner,SignalStrength
    from stonez.notifier    import send_telegram,format_trigger,format_no_trigger,format_watchlist
    from stonez.trade_state import load_state,set_watching
    log.info("=== Stonez scan starting (free tier) ===")
    scanner=StonezScanner(); result=scanner.run_full_scan()
    ctx=result.market_context; cur=load_state(); active=cur.status in ("ACTIVE","WATCHING")
    log.info(f"Summary: {result.summary} | Trade: {cur.status}")
    if result.triggers:
        best=sorted(result.triggers,key=lambda t:(t.signal_strength==SignalStrength.STRONG,-t.risk_per_lot),reverse=True)[0]
        for t in result.triggers:
            log.info(f"  [{t.signal_strength.value}] {t.side} {t.symbol} Est=₹{t.estimated_premium} VIX={t.india_vix}%")
            send_telegram(format_trigger(t))
        if not active:
            set_watching(best)
            send_telegram(f"📌 <b>Setup saved</b>\n<code>{best.symbol}</code>\n"
                          f"Est. SL: ₹{best.sl_price} | Est. Target: ₹{best.target_price}\n"
                          f"✅ Open Zerodha → check actual premium → enter if it's in ₹{int(best.estimated_premium*0.85)}–₹{int(best.estimated_premium*1.15)} range.")
    elif result.watchlist:
        log.info(f"  WATCHLIST: {len(result.watchlist)} item(s)")
        send_telegram(format_watchlist(result.watchlist,ctx))
    else:
        log.info(f"  No setup. RSI={ctx.get('rsi_daily')} | {ctx.get('condition')}")
        send_telegram(format_no_trigger(ctx))
    log.info("=== Scan complete ===")
if __name__=="__main__": main()
