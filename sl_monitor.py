"""
sl_monitor.py
Runs every 30 minutes during market hours.
Checks the live option price against SL and target.
Fires Telegram alert immediately on breach.

Also handles:
  - WATCHING state: reminds you of the setup to enter
  - Trailing SL after 1.5× gain
"""

import os
import sys
import logging
import requests
from datetime import datetime

from stonez.trade_state import load_state, set_closed, save_state, TradeState
from stonez.notifier    import send_telegram

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)s  %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

YF_BASE    = "https://query1.finance.yahoo.com/v8/finance/chart"
YF_HEADERS = {"User-Agent": "Mozilla/5.0"}


def get_live_option_price(state: TradeState) -> float | None:
    """
    Try to get live option price.
    NSE option prices via yfinance are partial — falls back to
    estimating from NIFTY spot movement if direct fetch fails.
    """
    # Try direct option fetch first (works for some strikes)
    yf_sym = _to_yf_option_symbol(state)
    if yf_sym:
        price = _yf_fetch(yf_sym)
        if price and price > 0.5:
            return price

    # Fallback: estimate from NIFTY spot move
    return _estimate_from_spot(state)


def _to_yf_option_symbol(state: TradeState) -> str | None:
    """
    Convert NSE symbol to Yahoo Finance format.
    NSE: NIFTY27-Mar-202623000CE
    Yahoo: approximate — limited availability
    """
    try:
        from datetime import datetime
        # Parse expiry from NSE format e.g. "27-Mar-2026"
        exp = datetime.strptime(state.expiry, "%d-%b-%Y")
        flag = "C" if state.side == "CALL" else "P"
        # Yahoo format: ^NSEIyymmddCstrike000
        sym = f"^NSEI{exp.strftime('%y%m%d')}{flag}{int(state.strike * 1000):08d}"
        return sym
    except Exception:
        return None


def _yf_fetch(symbol: str) -> float | None:
    try:
        for base in [YF_BASE, YF_BASE.replace("query1", "query2")]:
            r = requests.get(f"{base}/{symbol}", headers=YF_HEADERS,
                             params={"interval": "1m", "range": "1d"}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                meta = data["chart"]["result"][0]["meta"]
                p    = meta.get("regularMarketPrice")
                if p and p > 0: return float(p)
    except Exception:
        pass
    return None


def _estimate_from_spot(state: TradeState) -> float | None:
    """
    Estimate current option price from NIFTY spot movement.
    Uses simplified delta: OTM options move ~0.3× spot.
    """
    try:
        r = requests.get(f"{YF_BASE}/^NSEI", headers=YF_HEADERS,
                         params={"interval": "1m", "range": "1d"}, timeout=10)
        if r.status_code != 200:
            r = requests.get(f"{YF_BASE.replace('query1','query2')}/^NSEI",
                             headers=YF_HEADERS,
                             params={"interval": "1m", "range": "1d"}, timeout=10)
        if r.status_code != 200:
            return None

        data      = r.json()
        spot_now  = float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])
        spot_entry= state.spot_at_entry or spot_now

        spot_move_pct = (spot_now - spot_entry) / spot_entry
        multiplier    = 3.5 if state.side == "CALL" else -3.5

        # Apply time decay: roughly 1% per day
        from datetime import date
        try:
            entered = datetime.fromisoformat(state.entered_at).date()
            days_held = (date.today() - entered).days
        except Exception:
            days_held = 0

        time_decay  = max(0.5, 1 - days_held * 0.015)
        est_price   = state.entry_price * (1 + multiplier * spot_move_pct) * time_decay
        est_price   = max(0.5, round(est_price, 1))

        log.info(f"Spot now: {spot_now:.0f} | Entry spot: {spot_entry:.0f} | "
                 f"Move: {spot_move_pct*100:.1f}% | Est option: ₹{est_price}")
        return est_price

    except Exception as e:
        log.warning(f"Spot estimation failed: {e}")
        return None


def check_and_alert():
    """Main monitor logic — called every 30 minutes."""
    state = load_state()
    now   = datetime.now().strftime("%d-%b-%Y %I:%M %p IST")

    log.info(f"SL Monitor — Status: {state.status} | Symbol: {state.symbol or 'none'}")

    # ── No active trade ──────────────────────────────────────────────────
    if state.status == "NONE":
        log.info("No active trade. Nothing to monitor.")
        return

    # ── WATCHING: remind about setup ────────────────────────────────────
    if state.status == "WATCHING":
        msg = (
            f"👀 <b>Stonez Watchlist Reminder</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Setup identified. Waiting for entry confirmation.\n\n"
            f"<b>Symbol to watch:</b> {state.symbol}\n"
            f"<b>Side:</b> {state.side}\n"
            f"<b>Strike:</b> {state.strike:,.0f}  |  <b>Expiry:</b> {state.expiry}\n"
            f"<b>Enter around:</b> ₹{state.entry_price}\n"
            f"<b>SL once entered:</b> ₹{state.sl_price}\n"
            f"<b>Target:</b> ₹{state.target_price} (2×)\n"
            f"<b>Pattern triggered:</b> {state.pattern.replace('_',' ').title()}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Once you enter → reply /active to start SL monitoring.\n"
            f"<i>Checked at {now}</i>"
        )
        send_telegram(msg)
        log.info("Watching reminder sent.")
        return

    # ── ACTIVE: check live price ─────────────────────────────────────────
    if state.status == "ACTIVE":
        price = get_live_option_price(state)

        if price is None:
            log.warning("Could not fetch option price. Skipping check.")
            send_telegram(
                f"⚠️ <b>SL Monitor Warning</b>\n"
                f"Could not fetch live price for {state.symbol}.\n"
                f"Please check manually on Zerodha.\n"
                f"<b>Your SL:</b> ₹{state.sl_price} | <b>Target:</b> ₹{state.target_price}\n"
                f"<i>{now}</i>"
            )
            return

        entry  = state.entry_price
        sl     = state.sl_price
        target = state.target_price
        gain   = round(((price - entry) / entry) * 100, 1)

        log.info(f"Live price: ₹{price} | Entry: ₹{entry} | SL: ₹{sl} | Target: ₹{target}")

        # ── SL HIT ──────────────────────────────────────────────────────
        if price <= sl:
            state = set_closed(state, price, "SL_HIT")
            msg = (
                f"🔴 <b>STONEZ SL HIT</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Symbol:</b> {state.symbol}\n"
                f"<b>Side:</b> {state.side}\n"
                f"<b>Entry:</b> ₹{entry}  →  <b>Exit:</b> ₹{price}\n"
                f"<b>Loss:</b> ₹{abs(state.pnl_rs):,.0f}  ({state.pnl_pts:+.1f} pts)\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"SL has been hit. Exit the trade immediately on Zerodha.\n"
                f"Trade closed. Scanner will watch for next setup.\n"
                f"<i>{now}</i>"
            )
            send_telegram(msg)
            log.info(f"SL HIT. Loss: ₹{state.pnl_rs:,.0f}")
            # Reset state after exit
            from stonez.trade_state import clear_state
            clear_state()
            return

        # ── TARGET HIT ──────────────────────────────────────────────────
        if price >= target:
            state = set_closed(state, price, "TARGET_HIT")
            msg = (
                f"🟢 <b>STONEZ TARGET HIT</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Symbol:</b> {state.symbol}\n"
                f"<b>Side:</b> {state.side}\n"
                f"<b>Entry:</b> ₹{entry}  →  <b>Exit:</b> ₹{price}\n"
                f"<b>Profit:</b> ₹{state.pnl_rs:,.0f}  (+{state.pnl_pts:.1f} pts)\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Book 50% profit now. Trail the rest with prev day low as SL.\n"
                f"<i>{now}</i>"
            )
            send_telegram(msg)
            log.info(f"TARGET HIT. Profit: ₹{state.pnl_rs:,.0f}")
            from stonez.trade_state import clear_state
            clear_state()
            return

        # ── TRAILING SL: update after 1.5× gain ─────────────────────────
        if price >= entry * 1.5:
            new_sl = round(entry * 1.15, 1)   # trail SL to 15% above entry
            if new_sl > state.sl_price:
                state.sl_price    = new_sl
                state.last_checked= datetime.now().isoformat()
                save_state(state)
                msg = (
                    f"📈 <b>Stonez Trailing SL Updated</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"<b>Symbol:</b> {state.symbol}\n"
                    f"<b>Current price:</b> ₹{price}  ({gain:+.1f}%)\n"
                    f"<b>New trailing SL:</b> ₹{new_sl} (was ₹{sl})\n"
                    f"<b>Target still:</b> ₹{target}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"Update your SL on Zerodha to ₹{new_sl}.\n"
                    f"<i>{now}</i>"
                )
                send_telegram(msg)
                log.info(f"Trailing SL updated to ₹{new_sl}")
                return

        # ── NORMAL UPDATE ────────────────────────────────────────────────
        state.last_checked = datetime.now().isoformat()
        save_state(state)

        icon = "📈" if price > entry else "📉"
        msg = (
            f"{icon} <b>Stonez Position Update</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Symbol:</b> {state.symbol}\n"
            f"<b>Live price:</b> ₹{price}  ({gain:+.1f}%)\n"
            f"<b>Entry:</b> ₹{entry}  |  <b>SL:</b> ₹{sl}  |  <b>Target:</b> ₹{target}\n"
            f"<b>P&L estimate:</b> ₹{round((price-entry)*75):,.0f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Distance to SL:</b> ₹{round(price-sl,1)}\n"
            f"<b>Distance to target:</b> ₹{round(target-price,1)}\n"
            f"<i>Checked at {now}</i>"
        )
        send_telegram(msg)
        log.info(f"Position update sent. Price ₹{price} | P&L est ₹{round((price-entry)*75):,.0f}")


if __name__ == "__main__":
    check_and_alert()
