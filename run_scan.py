"""
run_scan.py — The main entry point.

Run manually:       python3 run_scan.py
Run via cron:       30 3 * * 1-5 cd /path/to/stonez_v2 && python3 run_scan.py
Run via GitHub Actions: triggered by .github/workflows/stonez.yml

Reads credentials from environment variables (set in .env or GitHub Secrets).
"""

import os
import sys
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def main():
    from stonez.scanner  import StonezScanner, SignalStrength
    from stonez.notifier import send_telegram, format_trigger, format_no_trigger

    log.info("=== Stonez scan starting ===")

    scanner = StonezScanner()
    result  = scanner.run_full_scan()

    log.info(f"Summary: {result.summary}")

    if result.triggers:
        for t in result.triggers:
            log.info(
                f"  TRIGGER [{t.signal_strength.value}] {t.side} | "
                f"{t.symbol} | Entry ₹{t.entry_price} | SL ₹{t.sl_price} | "
                f"Target ₹{t.target_price} | Risk/lot ₹{t.risk_per_lot:,.0f}"
            )
            msg = format_trigger(t)
            send_telegram(msg)
            print(msg)
    else:
        ctx = result.market_context
        log.info(f"  No triggers. RSI={ctx.get('rsi_daily')} | {ctx.get('condition')}")
        send_telegram(format_no_trigger(ctx))

    log.info("=== Stonez scan complete ===")


if __name__ == "__main__":
    main()
