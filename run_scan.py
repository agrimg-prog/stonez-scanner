"""
run_scan.py — runs the full Stonez scan and broadcasts to all subscribers.
Called by the 'Stonez Daily Scan' GitHub Actions workflow.

Broadcast rules (fixes the "constant reminder" spam):
  - Trigger fires          → ALWAYS broadcast to all subscribers
  - Watchlist only         → broadcast ONLY at morning scan (9:20 AM IST)
  - No setup               → broadcast ONLY at morning scan (9:20 AM IST)
  - Afternoon scan (3:25 PM) with no trigger → SILENT (no message)
"""

import sys, logging
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
sys.path.insert(0, ".")

IST = timezone(timedelta(hours=5, minutes=30))


def is_morning_scan() -> bool:
    """Returns True if current IST time is before 10:30 AM (morning scan window)."""
    now_ist = datetime.now(IST)
    return now_ist.hour < 10


def main():
    from stonez.scanner import StonezScanner
    from stonez.notifier import send_telegram, format_trigger, format_watchlist, format_no_trigger

    log.info("=" * 50)
    log.info("Stonez Daily Scan starting")
    log.info("=" * 50)

    try:
        scanner = StonezScanner()
        result  = scanner.run_full_scan()
    except Exception as e:
        log.error(f"Scanner crashed: {e}", exc_info=True)
        # Send error alert so you know something went wrong
        send_telegram(
            f"⚠️ <b>Stonez Scan Error</b>\n"
            f"Scanner crashed: <code>{e}</code>\n"
            f"Check GitHub Actions logs."
        )
        return

    ctx  = result.market_context
    morning = is_morning_scan()

    log.info(f"Summary: {result.summary}")
    log.info(f"Triggers: {len(result.triggers)} | "
             f"Watchlist: {len(result.watchlist)} | "
             f"Morning scan: {morning}")

    scan_time = datetime.now(IST).strftime("%d-%b-%Y %I:%M %p IST")

    # ── Case 1: Real trigger fired → ALWAYS send ──────────────────────────────
    if result.triggers:
        log.info(f"🚨 {len(result.triggers)} trigger(s). Broadcasting to all subscribers...")
        for t in result.triggers:
            send_telegram(format_trigger(t))
        return

    # ── Case 2: Watchlist only ────────────────────────────────────────────────
    if result.watchlist:
        if morning:
            log.info("Watchlist alert (morning). Broadcasting...")
            send_telegram(format_watchlist(result.watchlist, {
                **ctx,
                "scan_time": scan_time,
            }))
        else:
            log.info("Watchlist alert (afternoon). Silent — triggers only in PM.")
        return

    # ── Case 3: No setup ─────────────────────────────────────────────────────
    if morning:
        log.info("No setup. Sending morning daily summary...")
        send_telegram(format_no_trigger({**ctx, "scan_time": scan_time}))
    else:
        log.info("No setup (afternoon). Silent — not spamming subscribers.")


if __name__ == "__main__":
    main()
