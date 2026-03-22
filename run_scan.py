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

    log.info("=== Stonez scan starting ===")
    scanner = StonezScanner()
    result  = scanner.run_full_scan()
    ctx     = result.market_context
    cur     = load_state()
    active  = cur.status in ("ACTIVE","WATCHING")

    log.info(f"Summary: {result.summary} | Trade state: {cur.status}")

    if result.triggers:
        best = sorted(result.triggers,
                      key=lambda t:(t.signal_strength==SignalStrength.STRONG),
                      reverse=True)[0]
        for t in result.triggers:
            log.info(f"  [{t.signal_strength.value}] {t.side} | RSI={t.rsi_daily} | Pattern={t.price_pattern}")
            send_telegram(format_trigger(t))
        if not active:
            from stonez.trade_state import TradeState,save_state
            s=TradeState(status="WATCHING",side=best.side,
                         expiry=best.expiry_str,rsi_at_entry=best.rsi_daily,
                         pattern=best.price_pattern,spot_at_entry=best.spot_level,
                         entered_at=__import__('datetime').datetime.now().isoformat(),
                         last_checked=__import__('datetime').datetime.now().isoformat())
            save_state(s)
            send_telegram(
                f"📌 <b>Signal saved</b>\n"
                f"When you find your strike on Zerodha and enter the trade, "
                f"trigger the SL Monitor workflow to start tracking it.\n"
                f"Side: {best.side} | Expiry: {best.expiry_str}"
            )
    elif result.watchlist:
        log.info(f"  WATCHLIST: {len(result.watchlist)} item(s)")
        send_telegram(format_watchlist(result.watchlist,ctx))
    else:
        log.info(f"  No setup. RSI={ctx.get('rsi_daily')} | {ctx.get('condition')}")
        send_telegram(format_no_trigger(ctx))

    log.info("=== Scan complete ===")

if __name__=="__main__": main()
