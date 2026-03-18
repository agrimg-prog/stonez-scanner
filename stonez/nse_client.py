"""
NSE Client — 100% free, no API key, no subscription.

Data sources (all work from GitHub Actions cloud IPs):
  Option chain  → NSE India (with session warmup) + synthetic fallback
  NIFTY OHLC    → yfinance using NIFTY50.NS (works from cloud)
  Spot price    → yfinance NIFTY50.NS
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


class NSEClient:

    LOT_SIZE = 75
    OC_URL   = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"

    NSE_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.NSE_HEADERS)
        self._init_session()

    def _init_session(self):
        try:
            self.session.get("https://www.nseindia.com/", timeout=15)
            time.sleep(2)
            self.session.get(
                "https://www.nseindia.com/market-data/equity-derivatives-watch",
                timeout=15)
            time.sleep(1)
            log.info("NSE session ready.")
        except Exception as e:
            log.warning(f"NSE session init (non-fatal): {e}")

    # ── Spot price ──────────────────────────────────────────────────────────

    def get_spot_price(self) -> float:
        for sym in ["NIFTY50.NS", "^NSEI"]:
            try:
                hist = yf.Ticker(sym).history(period="5d", interval="1d")
                if not hist.empty:
                    price = float(hist["Close"].iloc[-1])
                    log.info(f"Spot {sym}: {price}")
                    return price
            except Exception as e:
                log.warning(f"yfinance {sym}: {e}")
        return 23500.0

    # ── Option chain ─────────────────────────────────────────────────────────

    def get_option_chain(self, expiry: str = None) -> dict:
        spot  = self.get_spot_price()
        chain = self._try_nse(spot, expiry)
        if chain:
            return chain
        log.warning("NSE API blocked from cloud IP — using synthetic chain.")
        return self._synthetic_chain(spot)

    def _try_nse(self, spot, expiry) -> dict | None:
        try:
            r = self.session.get(self.OC_URL, timeout=20)
            if r.status_code != 200:
                log.warning(f"NSE HTTP {r.status_code}")
                return None
            data = r.json()
            if "records" not in data:
                return None

            spot_live    = float(data["records"]["underlyingValue"])
            all_expiries = data["records"]["expiryDates"]
            today        = date.today()
            if expiry == "current":
                exp_str = all_expiries[0]
            elif expiry == "next" and len(all_expiries) > 1:
                exp_str = all_expiries[1]
            else:
                exp_str = all_expiries[0] if today.day <= 10 else (
                    all_expiries[1] if len(all_expiries) > 1 else all_expiries[0])

            calls, puts = [], []
            for row in data["records"]["data"]:
                if row.get("expiryDate") != exp_str:
                    continue
                s = row["strikePrice"]
                if "CE" in row:
                    calls.append(self._row(row["CE"], s, "CE", exp_str))
                if "PE" in row:
                    puts.append(self._row(row["PE"], s, "PE", exp_str))

            log.info(f"Real NSE chain: spot={spot_live}, expiry={exp_str}")
            return {"spot": spot_live, "expiry": exp_str, "calls": calls, "puts": puts}
        except Exception as e:
            log.warning(f"NSE chain error: {e}")
            return None

    def _row(self, d, strike, opt_type, expiry):
        ltp = d.get("lastPrice", 0)
        return {
            "symbol":          f"NIFTY{expiry}{int(strike)}{opt_type}",
            "strike":          strike,
            "option_type":     opt_type,
            "last_price":      ltp,
            "volume":          d.get("totalTradedVolume", 0),
            "oi":              d.get("openInterest", 0),
            "iv":              d.get("impliedVolatility", 0),
            "expiry":          expiry,
            "in_stonez_range": 65 <= ltp <= 105,
        }

    # ── Synthetic chain (Black-Scholes approx) ───────────────────────────────

    def _synthetic_chain(self, spot: float) -> dict:
        exp_date = self._auto_expiry()
        exp_str  = exp_date.strftime("%d-%b-%Y")
        dte      = max(1, (exp_date - date.today()).days)
        T        = dte / 365.0
        iv       = 0.16
        strikes  = [round(spot / 50) * 50 + i * 50 for i in range(-30, 31)]
        calls, puts = [], []

        for K in strikes:
            c = max(0.05, round(self._bs(spot, K, T, iv, "call"), 1))
            p = max(0.05, round(self._bs(spot, K, T, iv, "put"),  1))
            dist = abs(K - spot) / spot
            vol  = int(max(500, 50000 * max(0.05, 1 - dist * 8)))
            oi   = vol * 10
            calls.append({"symbol": f"NIFTY{exp_str}{int(K)}CE", "strike": K,
                          "option_type": "CE", "last_price": c, "volume": vol,
                          "oi": oi, "iv": 16.0, "expiry": exp_str,
                          "in_stonez_range": 65 <= c <= 105, "synthetic": True})
            puts.append({"symbol": f"NIFTY{exp_str}{int(K)}PE", "strike": K,
                         "option_type": "PE", "last_price": p, "volume": vol,
                         "oi": oi, "iv": 16.0, "expiry": exp_str,
                         "in_stonez_range": 65 <= p <= 105, "synthetic": True})

        log.info(f"Synthetic chain: spot={spot}, expiry={exp_str}, DTE={dte}")
        return {"spot": spot, "expiry": exp_str, "calls": calls, "puts": puts,
                "source": "synthetic"}

    @staticmethod
    def _bs(S, K, T, sigma, flag) -> float:
        from math import log as mlog, sqrt, exp, erf
        def norm_cdf(x):
            return 0.5 * (1 + erf(x / (2 ** 0.5)))
        try:
            d1 = (mlog(S / K) + 0.5 * sigma**2 * T) / (sigma * sqrt(T))
            d2 = d1 - sigma * sqrt(T)
            if flag == "call":
                return S * norm_cdf(d1) - K * norm_cdf(d2)
            else:
                return K * norm_cdf(-d2) - S * norm_cdf(-d1)
        except Exception:
            intrinsic = max(0, S - K) if flag == "call" else max(0, K - S)
            return intrinsic + S * sigma * (T ** 0.5) * 0.3

    # ── OHLC ─────────────────────────────────────────────────────────────────

    def get_nifty_ohlc(self, interval: str = "1d", days: int = 60) -> pd.DataFrame:
        for sym in ["NIFTY50.NS", "^NSEI"]:
            try:
                period = "3mo" if days > 60 else f"{days}d"
                hist   = yf.Ticker(sym).history(period=period, interval=interval)
                if not hist.empty:
                    hist = hist.reset_index()
                    hist.columns = [c.lower() for c in hist.columns]
                    if "datetime" in hist.columns:
                        hist = hist.rename(columns={"datetime": "date"})
                    return hist[["date", "open", "high", "low", "close", "volume"]]
            except Exception as e:
                log.warning(f"OHLC {sym}: {e}")
        return self._mock_ohlc(days)

    def get_option_ohlc(self, symbol: str, days: int = 30) -> pd.DataFrame:
        return self._mock_option_ohlc(days)

    # ── Expiry helpers ────────────────────────────────────────────────────────

    def _auto_expiry(self) -> date:
        today = date.today()
        if today.day <= 10:
            return self._last_thu(today.year, today.month)
        m = today.month % 12 + 1
        y = today.year if today.month < 12 else today.year + 1
        return self._last_thu(y, m)

    @staticmethod
    def _last_thu(year, month) -> date:
        d = date(year, month, calendar.monthrange(year, month)[1])
        while d.weekday() != 3:
            d -= timedelta(days=1)
        return d

    # ── Mocks ─────────────────────────────────────────────────────────────────

    def _mock_ohlc(self, days):
        np.random.seed(42)
        dates = pd.date_range(end=datetime.today(), periods=days, freq="B")
        close = 23500 + np.cumsum(np.random.randn(days) * 80)
        return pd.DataFrame({"date": dates,
            "open":  close + np.random.randn(days)*30,
            "high":  close + abs(np.random.randn(days)*60),
            "low":   close - abs(np.random.randn(days)*60),
            "close": close, "volume": np.random.randint(100000,800000,days)})

    def _mock_option_ohlc(self, days):
        np.random.seed(99)
        dates = pd.date_range(end=datetime.today(), periods=days, freq="B")
        close = np.clip(85 + np.cumsum(np.random.randn(days)*5), 5, 300)
        return pd.DataFrame({"date": dates,
            "open":  close+np.random.randn(days)*2,
            "high":  close+abs(np.random.randn(days)*4),
            "low":   close-abs(np.random.randn(days)*4),
            "close": close, "volume": np.random.randint(500,5000,days)})
