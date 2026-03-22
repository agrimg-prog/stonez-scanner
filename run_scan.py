"""
run_scan.py — entry point. Handles triggers, watchlist, and no-trade.
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

    log.info(f"Summary: {result.summary} | Existing trade: {current_state.status}")

    if result.triggers:
        best = sorted(result.triggers,
                      key=lambda t: (t.signal_strength == SignalStrength.STRONG, -t.risk_per_lot),
                      reverse=True)[0]

        for t in result.triggers:
            log.info(f"  TRIGGER [{t.signal_strength.value}] {t.side} | "
                     f"{t.symbol} | Entry Rs{t.entry_price} | SL Rs{t.sl_price} | "
                     f"Target Rs{t.target_price} | DTE {t.dte}d")
            send_telegram(format_trigger(t))

        if not trade_active:
            set_watching(best)
            log.info(f"Trade state WATCHING: {best.symbol}")
            send_telegram(
                f"Saved for SL monitoring.\n"
                f"Symbol: {best.symbol}\n"
                f"SL monitor tracks Rs{best.sl_price} SL and Rs{best.target_price} target "
                f"every 30 min once you enter on Zerodha."
            )
        else:
            log.info(f"Existing trade {current_state.symbol} active - not overwriting.")

    elif result.watchlist:
        log.info(f"  WATCHLIST: {len(result.watchlist)} item(s)")
        send_telegram(format_watchlist(result.watchlist, ctx))

    else:
        log.info(f"  No triggers. RSI={ctx.get('rsi_daily')} | {ctx.get('condition')}")
        send_telegram(format_no_trigger(ctx))

    log.info("=== Stonez scan complete ===")


if __name__ == "__main__":
    main()
