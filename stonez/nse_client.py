"""
NSE Client — 100% free, no API key, no subscription.
Uses NSE's public option chain endpoint + yfinance for OHLC.

Data sources:
  Option chain  → NSE public API (free, no auth)
  NIFTY OHLC    → yfinance (free, no auth)
"""

import time
import logging
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, date, timedelta
import calendar

log = logging.getLogger(__name__)

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}

class NSEClient:

    BASE_URL   = "https://www.nseindia.com"
    OC_URL     = BASE_URL + "/api/option-chain-indices?symbol=NIFTY"
    QUOTE_URL  = BASE_URL + "/api/quote-derivative?symbol=NIFTY"
    LOT_SIZE   = 75

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(NSE_HEADERS)
        self._init_session()

    def _init_session(self):
        """NSE needs a cookie from the main page before the API works."""
        try:
            self.session.get(self.BASE_URL, timeout=10)
            time.sleep(1)
        except Exception as e:
            log.warning(f"NSE session init warning: {e}")

    # ── Spot price ──────────────────────────────────────────────────────────

    def get_spot_price(self) -> float:
        try:
            ticker = yf.Ticker("^NSEI")
            hist   = ticker.history(period="1d", interval="1m")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception as e:
            log.warning(f"yfinance spot fallback: {e}")
        return self._get_spot_from_option_chain()

    def _get_spot_from_option_chain(self) -> float:
        try:
            r    = self.session.get(self.OC_URL, timeout=15)
            data = r.json()
            return float(data["records"]["underlyingValue"])
        except Exception:
            return 23500.0  # last known fallback

    # ── Option chain ────────────────────────────────────────────────────────

    def get_option_chain(self, expiry: str = None) -> dict:
        """
        Returns full NIFTY option chain for the Stonez-appropriate expiry.
        expiry: None (auto) | 'current' | 'next' | 'DD-Mon-YYYY'
        """
        try:
            r    = self.session.get(self.OC_URL, timeout=15)
            data = r.json()
        except Exception as e:
            log.error(f"NSE option chain fetch failed: {e}")
            return self._mock_chain(self.get_spot_price(), date.today())

        spot         = float(data["records"]["underlyingValue"])
        all_expiries = data["records"]["expiryDates"]   # list like ['27-Mar-2025', ...]
        exp_date_str = self._resolve_expiry_str(all_expiries, expiry)

        calls, puts = [], []
        for row in data["records"]["data"]:
            if row.get("expiryDate") != exp_date_str:
                continue
            strike = row["strikePrice"]

            if "CE" in row:
                ce = row["CE"]
                calls.append({
                    "symbol":          f"NIFTY{exp_date_str}{int(strike)}CE",
                    "strike":          strike,
                    "option_type":     "CE",
                    "last_price":      ce.get("lastPrice", 0),
                    "volume":          ce.get("totalTradedVolume", 0),
                    "oi":              ce.get("openInterest", 0),
                    "iv":              ce.get("impliedVolatility", 0),
                    "expiry":          exp_date_str,
                    "in_stonez_range": 65 <= ce.get("lastPrice", 0) <= 105,
                })
            if "PE" in row:
                pe = row["PE"]
                puts.append({
                    "symbol":          f"NIFTY{exp_date_str}{int(strike)}PE",
                    "strike":          strike,
                    "option_type":     "PE",
                    "last_price":      pe.get("lastPrice", 0),
                    "volume":          pe.get("totalTradedVolume", 0),
                    "oi":              pe.get("openInterest", 0),
                    "iv":              pe.get("impliedVolatility", 0),
                    "expiry":          exp_date_str,
                    "in_stonez_range": 65 <= pe.get("lastPrice", 0) <= 105,
                })

        return {"spot": spot, "expiry": exp_date_str, "calls": calls, "puts": puts}

    # ── Historical OHLC via yfinance ─────────────────────────────────────────

    def get_nifty_ohlc(self, interval: str = "1d", days: int = 60) -> pd.DataFrame:
        """
        interval: '1d' | '60m' | '15m'
        Returns DataFrame: date, open, high, low, close, volume
        """
        try:
            ticker = yf.Ticker("^NSEI")
            period = f"{days}d" if days <= 60 else "3mo"
            hist   = ticker.history(period=period, interval=interval)
            if hist.empty:
                return self._mock_ohlc(days)
            hist = hist.reset_index()
            hist.columns = [c.lower() for c in hist.columns]
            hist = hist.rename(columns={"datetime": "date", "index": "date"})
            return hist[["date", "open", "high", "low", "close", "volume"]]
        except Exception as e:
            log.warning(f"yfinance OHLC error: {e}")
            return self._mock_ohlc(days)

    def get_option_ohlc(self, symbol: str, days: int = 30) -> pd.DataFrame:
        """
        Fetch daily OHLC for a specific NIFTY option via yfinance.
        yfinance symbol format: NIFTY250327C23000.NS  (approx mapping)
        Falls back to mock if not found.
        """
        try:
            yf_sym = self._to_yf_symbol(symbol)
            ticker = yf.Ticker(yf_sym)
            hist   = ticker.history(period=f"{days}d", interval="1d")
            if not hist.empty:
                hist = hist.reset_index()
                hist.columns = [c.lower() for c in hist.columns]
                return hist[["date", "open", "high", "low", "close", "volume"]]
        except Exception:
            pass
        return self._mock_option_ohlc(days)

    # ── Expiry helpers ────────────────────────────────────────────────────────

    def _resolve_expiry_str(self, available: list, preference: str) -> str:
        """
        Stonez rule: before 10th → current month, after 10th → next month.
        Returns the expiry string as NSE formats it: 'DD-Mon-YYYY'
        """
        today = date.today()

        if preference == "current":
            return available[0]
        if preference == "next" and len(available) > 1:
            return available[1]
        if preference and preference not in (None, "auto", "current", "next"):
            return preference  # direct date string

        # Auto Stonez logic
        if today.day <= 10:
            return available[0]
        elif len(available) > 1:
            return available[1]
        return available[0]

    @staticmethod
    def _last_thursday(year: int, month: int) -> date:
        last_day = calendar.monthrange(year, month)[1]
        d = date(year, month, last_day)
        while d.weekday() != 3:
            d -= timedelta(days=1)
        return d

    @staticmethod
    def _to_yf_symbol(nse_symbol: str) -> str:
        """
        Convert NSE option symbol to yfinance format (approximate).
        NSE: NIFTY27-Mar-202523500CE → yfinance: ^NSEIyyyymmddCstrike
        This is approximate — yfinance coverage of Indian options is partial.
        """
        return nse_symbol + ".NS"

    # ── Mock fallbacks ────────────────────────────────────────────────────────

    def _mock_chain(self, spot: float, exp_date) -> dict:
        import random
        random.seed(int(time.time()) // 3600)
        strikes = [round(spot / 50) * 50 + i * 50 for i in range(-20, 21)]
        calls, puts = [], []
        exp_str = exp_date.strftime("%d-%b-%Y") if isinstance(exp_date, date) else str(exp_date)
        for s in strikes:
            diff  = s - spot
            c_ltp = max(1, round(max(0, spot - s) + 15 * (1 - diff / 3000), 1))
            p_ltp = max(1, round(max(0, s - spot) + 15 * (1 + diff / 3000), 1))
            calls.append({"symbol": f"NIFTY{exp_str}{int(s)}CE", "strike": s,
                          "option_type": "CE", "last_price": c_ltp,
                          "volume": 10000, "oi": 100000, "iv": 15.0,
                          "expiry": exp_str, "in_stonez_range": 65 <= c_ltp <= 105})
            puts.append({"symbol": f"NIFTY{exp_str}{int(s)}PE", "strike": s,
                         "option_type": "PE", "last_price": p_ltp,
                         "volume": 10000, "oi": 100000, "iv": 15.0,
                         "expiry": exp_str, "in_stonez_range": 65 <= p_ltp <= 105})
        return {"spot": spot, "expiry": exp_str, "calls": calls, "puts": puts}

    def _mock_ohlc(self, days: int) -> pd.DataFrame:
        np.random.seed(42)
        dates  = pd.date_range(end=datetime.today(), periods=days, freq="B")
        close  = 23500 + np.cumsum(np.random.randn(days) * 80)
        return pd.DataFrame({
            "date":   dates,
            "open":   close + np.random.randn(days) * 30,
            "high":   close + abs(np.random.randn(days) * 60),
            "low":    close - abs(np.random.randn(days) * 60),
            "close":  close,
            "volume": np.random.randint(100000, 800000, days),
        })

    def _mock_option_ohlc(self, days: int) -> pd.DataFrame:
        np.random.seed(99)
        dates = pd.date_range(end=datetime.today(), periods=days, freq="B")
        close = 85 + np.cumsum(np.random.randn(days) * 5)
        close = np.clip(close, 5, 300)
        return pd.DataFrame({
            "date":   dates,
            "open":   close + np.random.randn(days) * 2,
            "high":   close + abs(np.random.randn(days) * 4),
            "low":    close - abs(np.random.randn(days) * 4),
            "close":  close,
            "volume": np.random.randint(500, 5000, days),
        })
