"""
sl_monitor.py — monitors an active Stonez trade for SL or target hit.
Runs every ~30 min during market hours via GitHub Actions.

Uses Black-Scholes re-pricing with live NIFTY spot + India VIX
to estimate current option LTP (since NSE option chain is blocked from cloud).
This is an estimate — always verify on Zerodha.
"""

import sys, logging
from datetime import datetime, date

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
sys.path.insert(0, ".")


def main():
    from stonez.trade_state import load_state, save_state, set_closed
    from stonez.notifier import send_telegram

    state = load_state()

    if state.status != "WATCHING":
        log.info(f"No active trade to monitor. Status={state.status}")
        return

    log.info(
        f"Active trade: {state.side} {int(state.strike)} @ ₹{state.entry_price} | "
        f"SL=₹{state.sl_price} | Target=₹{state.target_price}"
    )

    try:
        from stonez.market_data import (
            get_nifty_spot, get_india_vix, bs_price, get_stonez_expiry
        )

        spot      = get_nifty_spot()
        vix       = get_india_vix()
        _, dte    = get_stonez_expiry()
        opt_type  = "CE" if state.side == "CALL" else "PE"

        if spot <= 0:
            log.error("Could not fetch spot price. Skipping monitor.")
            return

        current_price = bs_price(spot, state.strike, dte, vix, opt_type)
        log.info(
            f"Re-priced: spot={spot:.1f}, VIX={vix:.1f}%, DTE={dte} → "
            f"option est. ₹{current_price:.1f}"
        )

        # Update last_checked
        state.last_checked = datetime.now().isoformat()

        # ── SL Hit ────────────────────────────────────────────────────────────
        if current_price <= state.sl_price:
            log.warning(f"SL HIT. Price ₹{current_price} ≤ SL ₹{state.sl_price}")
            set_closed(state, current_price, "SL_HIT")
            pnl_rs = round((current_price - state.entry_price) * 75, 0)
            send_telegram(
                f"🔴 <b>STONEZ — SL HIT (estimated)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Side:</b>   {state.side}\n"
                f"<b>Strike:</b> {int(state.strike)}\n"
                f"<b>Expiry:</b> {state.expiry}\n"
                f"<b>Entry:</b>  ₹{state.entry_price}\n"
                f"<b>Est. LTP:</b> ₹{current_price:.1f}\n"
                f"<b>Loss:</b>  {current_price - state.entry_price:+.1f} pts  "
                f"(₹{pnl_rs:+.0f})\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ Verify on Zerodha. Premium is estimated (BS model).\n"
                f"Trade closed. Wait patiently for next setup."
            )
            return

        # ── Target Hit ────────────────────────────────────────────────────────
        if current_price >= state.target_price:
            log.info(f"TARGET HIT. Price ₹{current_price} ≥ Target ₹{state.target_price}")
            set_closed(state, current_price, "TARGET_HIT")
            pnl_rs = round((current_price - state.entry_price) * 75, 0)
            send_telegram(
                f"🎯 <b>STONEZ — 2× TARGET HIT!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Side:</b>   {state.side}\n"
                f"<b>Strike:</b> {int(state.strike)}\n"
                f"<b>Expiry:</b> {state.expiry}\n"
                f"<b>Entry:</b>  ₹{state.entry_price}\n"
                f"<b>Est. LTP:</b> ₹{current_price:.1f}\n"
                f"<b>Profit:</b> +{current_price - state.entry_price:.1f} pts  "
                f"(₹{pnl_rs:+.0f})\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ Verify on Zerodha before booking.\n"
                f"Consider booking at least 50% here. Ride rest with trailing SL\n"
                f"(prev day low OR 1H 20 SMA, whichever breaks first)."
            )
            return

        # ── Still Active ──────────────────────────────────────────────────────
        pct = round((current_price / state.entry_price - 1) * 100, 1)
        log.info(f"Trade still active. P&L: {pct:+.1f}%")
        save_state(state)

        # Send a brief status update every check
        send_telegram(
            f"📍 <b>Stonez Trade Update</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Side:</b> {state.side}  |  <b>Strike:</b> {int(state.strike)}\n"
            f"<b>Entry:</b> ₹{state.entry_price}  |  "
            f"<b>Est. now:</b> ₹{current_price:.1f}\n"
            f"<b>P&L:</b> {pct:+.1f}%  |  "
            f"<b>SL:</b> ₹{state.sl_price}  |  "
            f"<b>Target:</b> ₹{state.target_price}\n"
            f"<b>NIFTY spot:</b> {spot:,.1f}  |  <b>VIX:</b> {vix:.1f}%\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Premium is estimated (BS model). Verify on Zerodha.</i>"
        )

    except Exception as e:
        log.error(f"SL monitor error: {e}", exc_info=True)
        send_telegram(f"⚠️ SL monitor error: <code>{e}</code>")


if __name__ == "__main__":
    main()
