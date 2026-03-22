"""
NSE Client — fixed to use Yahoo Finance direct HTTP API
which reliably works from GitHub Actions cloud IPs.
"""

import time
import logging
import requests
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
import calendar

log = logging.getLogger(__name__)

# Direct Yahoo Finance HTTP — more reliable than yfinance library from cloud
YF_BASE    = "https://query1.finance.yahoo.com/v8/finance/chart"
YF_BASE_V2 = "https://query2.finance.yahoo.com/v8/finance/chart"

YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
}


class NSEClient:

    LOT_SIZE = 75
    OC_URL   = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(NSE_HEADERS)
        self._nse_ready = False
        self._init_nse_session()

    def _init_nse_session(self):
        try:
            self.session.get("https://www.nseindia.com/", timeout=15)
            time.sleep(2)
            self.session.get(
                "https://www.nseindia.com/market-data/equity-derivatives-watch",
                timeout=15)
            time.sleep(1)
            self._nse_ready = True
            log.info("NSE session ready.")
        except Exception as e:
            log.warning(f"NSE session init (non-fatal): {e}")

    # ── Spot price ──────────────────────────────────────────────────────────

    def get_spot_price(self) -> float:
        """Try multiple sources in order until one works."""

        # Source 1: Yahoo Finance direct HTTP (most reliable from cloud)
        price = self._yf_spot("^NSEI")
        if price: return price

        price = self._yf_spot("NIFTY50.NS")
        if price: return price

        # Source 2: NSE option chain underlying value
        price = self._nse_spot()
        if price: return price

        log.error("ALL spot price sources failed — check internet connectivity")
        raise RuntimeError("Cannot fetch NIFTY spot price from any source")

    def _yf_spot(self, symbol: str) -> float | None:
        """Fetch spot from Yahoo Finance direct HTTP API."""
        for base in [YF_BASE, YF_BASE_V2]:
            try:
                url  = f"{base}/{symbol}"
                r    = requests.get(url, headers=YF_HEADERS,
                                    params={"interval": "1m", "range": "1d"},
                                    timeout=15)
                if r.status_code != 200:
                    continue
                data = r.json()
                meta = data["chart"]["result"][0]["meta"]
                # Use regularMarketPrice (most current)
                price = meta.get("regularMarketPrice") or meta.get("previousClose")
                if price and price > 1000:
                    log.info(f"Spot from Yahoo ({symbol}): {price}")
                    return float(price)
            except Exception as e:
                log.warning(f"Yahoo spot {symbol} @ {base}: {e}")
        return None

    def _nse_spot(self) -> float | None:
        """Get spot price from NSE option chain response."""
        try:
            r = self.session.get(self.OC_URL, timeout=20)
            if r.status_code == 200:
                data = r.json()
                if "records" in data:
                    price = float(data["records"]["underlyingValue"])
                    log.info(f"Spot from NSE option chain: {price}")
                    return price
        except Exception as e:
            log.warning(f"NSE spot fallback: {e}")
        return None

    # ── OHLC data ─────────────────────────────────────────────────────────

    def get_nifty_ohlc(self, interval: str = "1d", days: int = 60) -> pd.DataFrame:
        """
        Fetch NIFTY OHLC via Yahoo Finance direct HTTP.
        interval: '1d' | '60m' | '15m'
        """
        # Map our intervals to Yahoo format
        yf_interval = interval
        if interval == "60m":
            yf_interval = "60m"
            range_str   = "30d" if days <= 30 else "60d"
        elif interval == "15m":
            yf_interval = "15m"
            range_str   = "5d"
        else:
            yf_interval = "1d"
            range_str   = "3mo" if days > 60 else f"{days}d"

        for symbol in ["^NSEI", "NIFTY50.NS"]:
            df = self._yf_ohlc(symbol, yf_interval, range_str)
            if df is not None and not df.empty:
                return df

        log.error("Could not fetch NIFTY OHLC from Yahoo Finance")
        raise RuntimeError("Cannot fetch NIFTY OHLC — check connectivity")

    def _yf_ohlc(self, symbol: str, interval: str, range_str: str) -> pd.DataFrame | None:
        """Raw Yahoo Finance OHLC fetch."""
        for base in [YF_BASE, YF_BASE_V2]:
            try:
                url = f"{base}/{symbol}"
                r   = requests.get(url, headers=YF_HEADERS,
                                   params={"interval": interval, "range": range_str},
                                   timeout=20)
                if r.status_code != 200:
                    log.warning(f"Yahoo OHLC {symbol} HTTP {r.status_code}")
                    continue

                data   = r.json()
                result = data["chart"]["result"][0]
                ts     = result["timestamp"]
                ohlcv  = result["indicators"]["quote"][0]

                df = pd.DataFrame({
                    "date":   pd.to_datetime(ts, unit="s", utc=True)
                                .tz_convert("Asia/Kolkata")
                                .tz_localize(None),
                    "open":   ohlcv["open"],
                    "high":   ohlcv["high"],
                    "low":    ohlcv["low"],
                    "close":  ohlcv["close"],
                    "volume": ohlcv["volume"],
                })
                df = df.dropna(subset=["close"])
                if not df.empty:
                    log.info(f"OHLC from Yahoo ({symbol}, {interval}): {len(df)} rows, "
                             f"latest close={df['close'].iloc[-1]:.1f}")
                    return df
            except Exception as e:
                log.warning(f"Yahoo OHLC {symbol} {interval} @ {base}: {e}")
        return None

    def get_option_ohlc(self, symbol: str, days: int = 30) -> pd.DataFrame:
        """Option-specific OHLC — use mock (NSE doesn't provide this free)."""
        return self._mock_option_ohlc(days)

    # ── Option chain ─────────────────────────────────────────────────────

    def get_option_chain(self, expiry: str = None) -> dict:
        spot  = self.get_spot_price()
        chain = self._try_nse_chain(spot, expiry)
        if chain:
            return chain
        log.warning("NSE chain blocked — using synthetic chain with real spot.")
        return self._synthetic_chain(spot)

    def _try_nse_chain(self, spot: float, expiry: str) -> dict | None:
        try:
            r = self.session.get(self.OC_URL, timeout=20)
            if r.status_code != 200:
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
                exp_str = (all_expiries[0] if today.day <= 10
                           else (all_expiries[1] if len(all_expiries) > 1
                                 else all_expiries[0]))

            calls, puts = [], []
            for row in data["records"]["data"]:
                if row.get("expiryDate") != exp_str:
                    continue
                s = row["strikePrice"]
                if "CE" in row:
                    calls.append(self._row(row["CE"], s, "CE", exp_str))
                if "PE" in row:
                    puts.append(self._row(row["PE"],  s, "PE", exp_str))

            log.info(f"Real NSE chain: spot={spot_live}, expiry={exp_str}, "
                     f"{len(calls)} calls, {len(puts)} puts")
            return {"spot": spot_live, "expiry": exp_str,
                    "calls": calls, "puts": puts, "source": "nse_live"}
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

    # ── Synthetic chain (when NSE blocked) ───────────────────────────────

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
            calls.append({"symbol": f"NIFTY{exp_str}{int(K)}CE", "strike": K,
                          "option_type": "CE", "last_price": c, "volume": vol,
                          "oi": vol * 10, "iv": 16.0, "expiry": exp_str,
                          "in_stonez_range": 65 <= c <= 105, "synthetic": True})
            puts.append({"symbol": f"NIFTY{exp_str}{int(K)}PE", "strike": K,
                         "option_type": "PE", "last_price": p, "volume": vol,
                         "oi": vol * 10, "iv": 16.0, "expiry": exp_str,
                         "in_stonez_range": 65 <= p <= 105, "synthetic": True})

        log.info(f"Synthetic chain: spot={spot:.0f}, expiry={exp_str}, DTE={dte}")
        return {"spot": spot, "expiry": exp_str, "calls": calls,
                "puts": puts, "source": "synthetic"}

    @staticmethod
    def _bs(S, K, T, sigma, flag) -> float:
        from math import log as mlog, sqrt, erf
        def ncdf(x): return 0.5 * (1 + erf(x / (2 ** 0.5)))
        try:
            d1 = (mlog(S / K) + 0.5 * sigma**2 * T) / (sigma * sqrt(T))
            d2 = d1 - sigma * sqrt(T)
            if flag == "call":
                return S * ncdf(d1) - K * ncdf(d2)
            else:
                return K * ncdf(-d2) - S * ncdf(-d1)
        except Exception:
            intrinsic = max(0, S - K) if flag == "call" else max(0, K - S)
            return intrinsic + S * sigma * (T ** 0.5) * 0.3

    # ── Expiry helpers ────────────────────────────────────────────────────

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

    # ── Mock option OHLC only ────────────────────────────────────────────

    def _mock_option_ohlc(self, days: int) -> pd.DataFrame:
        """Only used for option-specific OHLC — everything else is real."""
        np.random.seed(int(time.time()) // 86400)  # changes daily
        dates = pd.date_range(end=datetime.today(), periods=days, freq="B")
        close = np.clip(85 + np.cumsum(np.random.randn(days) * 5), 5, 300)
        return pd.DataFrame({
            "date":   dates,
            "open":   close + np.random.randn(days) * 2,
            "high":   close + abs(np.random.randn(days) * 4),
            "low":    close - abs(np.random.randn(days) * 4),
            "close":  close,
            "volume": np.random.randint(500, 5000, days),
        })
