"""
market_data.py — 100% free, no signup, no API key.

Key fix: Stonez options are always OTM.
  CALL → strikes strictly ABOVE spot
  PUT  → strikes strictly BELOW spot

BS formula uses real India VIX for time value.
"""

import logging
import requests
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from math import log, sqrt, exp, erf
import calendar

log = logging.getLogger(__name__)

YF1 = "https://query1.finance.yahoo.com/v8/finance/chart"
YF2 = "https://query2.finance.yahoo.com/v8/finance/chart"
HDR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def _yf_get(symbol: str, interval: str, range_str: str) -> dict | None:
    for base in [YF1, YF2]:
        try:
            r = requests.get(f"{base}/{symbol}", headers=HDR,
                             params={"interval": interval, "range": range_str},
                             timeout=15)
            if r.status_code == 200:
                result = r.json().get("chart", {}).get("result", [])
                if result:
                    return result[0]
        except Exception as e:
            log.warning(f"Yahoo {symbol} {interval}: {e}")
    return None


def get_nifty_spot() -> float:
    for sym in ["^NSEI", "NIFTY50.NS"]:
        r = _yf_get(sym, "1m", "1d")
        if r:
            p = r.get("meta", {}).get("regularMarketPrice")
            if p and p > 1000:
                log.info(f"NIFTY spot: {p} (from {sym})")
                return float(p)
    log.error("Could not fetch NIFTY spot")
    return 0.0


def get_india_vix() -> float:
    """
    Real India VIX from Yahoo Finance (^INDIAVIX).
    Returned as percentage e.g. 22.81 means 22.81% annualised vol.
    """
    for range_str in ["5d", "30d"]:
        r = _yf_get("^INDIAVIX", "1d", range_str)
        if r:
            # Try live price first
            p = r.get("meta", {}).get("regularMarketPrice")
            if p and 5 < p < 100:
                log.info(f"India VIX: {p:.2f}%")
                return float(p)
            # Fall back to last close in OHLC
            closes = r.get("indicators", {}).get("quote", [{}])[0].get("close", [])
            closes = [c for c in closes if c and 5 < c < 100]
            if closes:
                log.info(f"India VIX (close): {closes[-1]:.2f}%")
                return float(closes[-1])

    log.warning("Could not fetch India VIX — using 18% (elevated default)")
    return 18.0


def get_nifty_ohlc(interval: str = "1d", days: int = 60) -> pd.DataFrame:
    yf_range = "3mo" if days > 60 else f"{days}d"
    for sym in ["^NSEI", "NIFTY50.NS"]:
        r = _yf_get(sym, interval, yf_range)
        if not r:
            continue
        try:
            ts  = r["timestamp"]
            q   = r["indicators"]["quote"][0]
            rows = []
            for i, t in enumerate(ts):
                if not q["close"][i]:
                    continue
                rows.append({
                    "date":   datetime.fromtimestamp(t),
                    "open":   q["open"][i]   or 0,
                    "high":   q["high"][i]   or 0,
                    "low":    q["low"][i]    or 0,
                    "close":  q["close"][i],
                    "volume": q["volume"][i] or 0,
                })
            if rows:
                df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
                log.info(f"OHLC {sym} {interval}: {len(df)} rows, latest={df['close'].iloc[-1]:.1f}")
                return df
        except Exception as e:
            log.warning(f"OHLC parse {sym}: {e}")
    return pd.DataFrame()


# ── Black-Scholes ─────────────────────────────────────────────────────────────

def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2)))


def bs_price(spot: float, strike: float, dte: int,
             iv_pct: float, option_type: str) -> float:
    """
    Black-Scholes price for European option.
    iv_pct: India VIX value e.g. 22.81 (NOT 0.2281)
    Returns estimated option premium in ₹.
    """
    if spot <= 0 or strike <= 0 or dte < 1:
        intrinsic = max(0.0, spot - strike) if option_type == "CE" else max(0.0, strike - spot)
        return max(0.05, round(intrinsic, 1))

    T     = dte / 365.0
    sigma = iv_pct / 100.0      # e.g. 22.81 → 0.2281
    r     = 0.065               # approx Indian risk-free rate

    try:
        d1 = (log(spot / strike) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt(T))
        d2 = d1 - sigma * sqrt(T)

        if option_type == "CE":
            price = (spot * _ncdf(d1) -
                     strike * exp(-r * T) * _ncdf(d2))
        else:
            price = (strike * exp(-r * T) * _ncdf(-d2) -
                     spot * _ncdf(-d1))

        result = max(0.05, round(price, 1))
        log.debug(f"BS: spot={spot:.0f} K={strike:.0f} DTE={dte} "
                  f"IV={iv_pct:.1f}% → {option_type} = ₹{result}")
        return result

    except Exception as e:
        log.warning(f"BS error (spot={spot}, K={strike}, IV={iv_pct}): {e}")
        intrinsic = max(0.0, spot - strike) if option_type == "CE" else max(0.0, strike - spot)
        return max(0.05, round(intrinsic, 1))


def find_stonez_strikes(spot: float, vix: float, dte: int,
                         side: str, p_min: float, p_max: float) -> list:
    """
    Scan OTM strikes to find ones where BS premium is in Stonez range.

    CRITICAL FIX:
      CALL → only strikes ABOVE spot (OTM calls)
      PUT  → only strikes BELOW spot (OTM puts)

    ITM options are NEVER valid Stonez trades — their premiums are
    dominated by intrinsic value, not the time value we want to ride.

    Returns list of candidates sorted closest-to-spot first.
    """
    opt_type = "CE" if side == "CALL" else "PE"
    results  = []
    step     = 50

    # Validate inputs
    if spot <= 0 or vix <= 0 or dte < 1:
        log.error(f"Invalid inputs: spot={spot}, vix={vix}, dte={dte}")
        return []

    # Log what we're scanning
    log.info(f"Scanning OTM {side}s | spot={spot:.0f} | VIX={vix:.2f}% | "
             f"DTE={dte} | range ₹{p_min}–₹{p_max}")

    if side == "CALL":
        # OTM calls: strikes ABOVE spot
        # Start 50 pts above spot, scan up to +8000 pts
        start_strike = round(spot / step) * step + step   # first OTM strike above spot
        for strike in range(int(start_strike), int(start_strike) + 8001, step):
            prem = bs_price(spot, float(strike), dte, vix, "CE")

            if prem > p_max:
                # Still too expensive — keep going higher (further OTM = cheaper)
                continue
            if prem < p_min:
                # Gone too far OTM — premiums only get cheaper from here
                break
            # In range
            results.append({
                "strike":             float(strike),
                "estimated_premium":  prem,
                "distance_from_spot": strike - spot,
                "moneyness_pct":      round((strike - spot) / spot * 100, 2),
                "type":               "OTM_CE",
            })

    else:  # PUT
        # OTM puts: strikes BELOW spot
        # Start 50 pts below spot, scan down to -8000 pts
        start_strike = round(spot / step) * step - step   # first OTM strike below spot
        for strike in range(int(start_strike), int(start_strike) - 8001, -step):
            if strike <= 0:
                break
            prem = bs_price(spot, float(strike), dte, vix, "PE")

            if prem > p_max:
                continue
            if prem < p_min:
                break
            results.append({
                "strike":             float(strike),
                "estimated_premium":  prem,
                "distance_from_spot": spot - strike,
                "moneyness_pct":      round((spot - strike) / spot * 100, 2),
                "type":               "OTM_PE",
            })

    if results:
        # Sort closest to spot first (least OTM = highest delta = most responsive)
        results.sort(key=lambda x: x["distance_from_spot"])
        log.info(f"Found {len(results)} OTM {side} strike(s) in range:")
        for r in results[:3]:
            log.info(f"  Strike {r['strike']:.0f} | "
                     f"Est. ₹{r['estimated_premium']} | "
                     f"{r['moneyness_pct']:+.1f}% OTM")
    else:
        log.warning(f"No OTM {side} strikes found in ₹{p_min}–₹{p_max} range. "
                    f"VIX={vix:.1f}%, DTE={dte}. Market may be in extreme condition.")

    return results[:5]


def get_stonez_expiry() -> tuple:
    """
    Returns (expiry_date, dte) per Stonez rule:
    Before 10th → current month last Thursday
    After  10th → next month last Thursday
    """
    today = date.today()

    def last_thu(y, m):
        d = date(y, m, calendar.monthrange(y, m)[1])
        while d.weekday() != 3:
            d -= timedelta(days=1)
        return d

    if today.day <= 10:
        exp = last_thu(today.year, today.month)
    else:
        m = today.month % 12 + 1
        y = today.year if today.month < 12 else today.year + 1
        exp = last_thu(y, m)

    dte = max(1, (exp - today).days)
    log.info(f"Stonez expiry: {exp} (DTE={dte}, "
             f"rule={'current month' if today.day<=10 else 'next month'})")
    return exp, dte
