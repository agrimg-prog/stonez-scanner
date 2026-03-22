"""
market_data.py — 100% free, no signup, no API key.
Uses Yahoo Finance direct HTTP for:
  - NIFTY 50 spot price
  - NIFTY daily + hourly OHLC (for RSI, SMA, patterns)
  - India VIX (real volatility index — makes BS estimates accurate)
  - Option premium estimates via Black-Scholes with real VIX as IV

No option chain API exists for free from cloud IPs.
Premium shown is estimated — always verify on Zerodha before entering.
India VIX currently reflects actual market volatility, so estimates
are meaningful (not hardcoded guesses).
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
    """Fetch from Yahoo Finance, try both endpoints."""
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
    """Real NIFTY 50 spot price."""
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
    India VIX is NSE's official volatility index — represents
    expected 30-day annualised volatility of NIFTY 50.
    This is what makes our Black-Scholes estimates meaningful.
    """
    r = _yf_get("^INDIAVIX", "1d", "5d")
    if r:
        p = r.get("meta", {}).get("regularMarketPrice")
        if p and p > 0:
            vix = float(p)
            log.info(f"India VIX: {vix:.2f}%")
            return vix
    # Fallback: use recent historical VIX from OHLC
    r = _yf_get("^INDIAVIX", "1d", "30d")
    if r:
        closes = r.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        closes = [c for c in closes if c]
        if closes:
            vix = float(closes[-1])
            log.info(f"India VIX (historical): {vix:.2f}%")
            return vix
    log.warning("Could not fetch India VIX, using 16% default")
    return 16.0


def get_nifty_ohlc(interval: str = "1d", days: int = 60) -> pd.DataFrame:
    """
    NIFTY OHLC data from Yahoo Finance.
    interval: '1d' | '60m'
    """
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
                log.info(f"OHLC {sym} {interval}: {len(df)} rows, "
                         f"latest={df['close'].iloc[-1]:.1f}")
                return df
        except Exception as e:
            log.warning(f"OHLC parse {sym}: {e}")
    return pd.DataFrame()


# ── Black-Scholes option pricing ──────────────────────────────────────────────

def _ncdf(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + erf(x / sqrt(2)))


def bs_price(spot: float, strike: float, dte: int,
             iv_pct: float, option_type: str) -> float:
    """
    Black-Scholes option price.
    spot:        NIFTY spot
    strike:      option strike
    dte:         days to expiry
    iv_pct:      implied volatility in % (e.g. 15.5 for 15.5%)
    option_type: 'CE' or 'PE'
    Returns:     estimated option premium in ₹
    """
    T     = max(dte, 1) / 365.0
    sigma = iv_pct / 100.0
    r     = 0.065  # approximate Indian risk-free rate

    if spot <= 0 or strike <= 0 or sigma <= 0:
        return max(0.0, spot - strike) if option_type == "CE" else max(0.0, strike - spot)

    try:
        d1 = (log(spot / strike) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt(T))
        d2 = d1 - sigma * sqrt(T)

        if option_type == "CE":
            price = (spot * _ncdf(d1) -
                     strike * exp(-r * T) * _ncdf(d2))
        else:
            price = (strike * exp(-r * T) * _ncdf(-d2) -
                     spot * _ncdf(-d1))

        return max(0.05, round(price, 1))
    except Exception as e:
        log.warning(f"BS price error: {e}")
        intrinsic = max(0, spot - strike) if option_type == "CE" else max(0, strike - spot)
        return max(0.05, intrinsic)


def find_stonez_strikes(spot: float, vix: float, dte: int,
                         side: str, p_min: float, p_max: float) -> list:
    """
    Scan strikes to find ones where BS-estimated premium is in Stonez range.
    Returns list of {strike, estimated_premium} sorted by how close to midpoint.
    """
    opt_type = "CE" if side == "CALL" else "PE"
    results  = []

    # Search strikes in steps of 50 from -5000 to +5000 of spot
    step   = 50
    spread = 6000

    for delta in range(0, spread + 1, step):
        for direction in ([1, -1] if side == "CALL" else [-1, 1]):
            strike = round((spot + direction * delta) / step) * step
            if strike <= 0:
                continue

            prem = bs_price(spot, strike, dte, vix, opt_type)

            if p_min <= prem <= p_max:
                results.append({
                    "strike":             strike,
                    "estimated_premium":  prem,
                    "distance_from_spot": abs(strike - spot),
                    "moneyness":          round((strike - spot) / spot * 100, 2),
                })

    # Remove duplicates, sort by distance from spot midpoint of range
    seen = set()
    unique = []
    for r in results:
        if r["strike"] not in seen:
            seen.add(r["strike"])
            unique.append(r)

    unique.sort(key=lambda x: x["distance_from_spot"])
    return unique[:5]   # top 5 candidates


def get_stonez_expiry() -> tuple[date, int]:
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
    return exp, dte
