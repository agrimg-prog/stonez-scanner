"""
run_scan.py — GitHub Actions entry point. Reads option_data.json, runs analysis, sends Telegram.
"""

import os, sys, logging
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)s  %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)


def main():
    from stonez.scanner     import StonezScanner, SignalStrength
    from stonez.notifier    import send_telegram, format_trigger, format_no_trigger, format_watchlist
    from stonez.trade_state import load_state, set_watching

    log.info("=== Stonez scan starting ===")

    scanner       = StonezScanner()
    result        = scanner.run_full_scan()
    ctx           = result.market_context
    current_state = load_state()
    trade_active  = current_state.status in ("ACTIVE", "WATCHING")

    log.info(f"Summary: {result.summary} | Trade state: {current_state.status}")

    if result.triggers:
        best = sorted(result.triggers,
                      key=lambda t: (t.signal_strength == SignalStrength.STRONG, -t.risk_per_lot),
                      reverse=True)[0]
        for t in result.triggers:
            log.info(f"  [{t.signal_strength.value}] {t.side} {t.symbol} "
                     f"| Entry ₹{t.entry_price} | SL ₹{t.sl_price} | Target ₹{t.target_price} | IV {t.iv}%")
            send_telegram(format_trigger(t))

        if not trade_active:
            set_watching(best)
            send_telegram(
                f"📌 <b>Trade saved for SL monitoring</b>\n"
                f"Symbol: <code>{best.symbol}</code>\n"
                f"SL monitor tracks ₹{best.sl_price} SL and ₹{best.target_price} target every 30 min.\n"
                f"Buy on Zerodha first, then trigger the SL Monitor workflow."
            )

    elif result.watchlist:
        log.info(f"  WATCHLIST: {len(result.watchlist)} item(s)")
        send_telegram(format_watchlist(result.watchlist, ctx))

    else:
        log.info(f"  No setup. RSI={ctx.get('rsi_daily')} | {ctx.get('condition')}")
        send_telegram(format_no_trigger(ctx))

    log.info("=== Scan complete ===")


if __name__ == "__main__":
    main()
