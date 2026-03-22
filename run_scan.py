"""
run_scan.py — Stonez scanner entry point v3.
Saves trade state on trigger so SL monitor can track it.
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
    scanner = StonezScanner()
    result  = scanner.run_full_scan()
    ctx     = result.market_context

    # Don't overwrite an active/watching trade
    current_state = load_state()
    trade_active  = current_state.status in ("ACTIVE", "WATCHING")

    log.info(f"Summary: {result.summary} | Existing trade: {current_state.status}")

    if result.triggers:
        # Pick the strongest trigger
        best = sorted(result.triggers,
                      key=lambda t: (t.signal_strength == SignalStrength.STRONG, t.risk_per_lot),
                      reverse=True)[0]

        for t in result.triggers:
            log.info(f"  TRIGGER [{t.signal_strength.value}] {t.side} | "
                     f"{t.symbol} | Entry ₹{t.entry_price} | SL ₹{t.sl_price} | "
                     f"Target ₹{t.target_price} | DTE {t.dte}d")
            msg = format_trigger(t)
            send_telegram(msg)

        # Save state for SL monitor (only if no active trade already)
        if not trade_active:
            set_watching(best)
            log.info(f"Trade state set to WATCHING: {best.symbol}")
            send_telegram(
                f"📌 <b>Trade saved to watchlist</b>\n"
                f"SL monitor will alert you every 30 min once you enter.\n"
                f"When you buy on Zerodha, the monitor tracks ₹{best.sl_price} SL "
                f"and ₹{best.target_price} target automatically."
            )
        else:
            log.info(f"Existing trade ({current_state.symbol}) active — not overwriting.")

    elif result.watchlist:
        log.info(f"  WATCHLIST: {len(result.watchlist)} item(s)")
        msg = format_watchlist(result.watchlist, ctx)
        send_telegram(msg)

    else:
        log.info(f"  No triggers. RSI={ctx.get('rsi_daily')} | {ctx.get('condition')}")
        msg = format_no_trigger(ctx)
        send_telegram(msg)

    log.info("=== Stonez scan complete ===")


if __name__ == "__main__":
    main()
