"""
data_reader.py — reads option_data.json (fetched by Mac) instead of
calling NSE directly. This is what GitHub Actions uses.
"""

import json
import logging
import pandas as pd
from datetime import datetime, date
from pathlib import Path

log = logging.getLogger(__name__)

DATA_FILE = Path(__file__).parent.parent / "option_data.json"


class DataReader:
    """Reads the pre-fetched option chain data pushed by your Mac."""

    def __init__(self):
        self._data = self._load()

    def _load(self) -> dict:
        if not DATA_FILE.exists():
            raise FileNotFoundError(
                f"option_data.json not found. "
                f"Run fetch_and_push.py on your Mac first."
            )
        with open(DATA_FILE) as f:
            data = json.load(f)

        # Warn if data is stale (older than 8 hours)
        try:
            fetched = datetime.strptime(data["fetched_at"], "%Y-%m-%d %H:%M:%S IST")
            age_hrs = (datetime.now() - fetched).total_seconds() / 3600
            if age_hrs > 8:
                log.warning(f"option_data.json is {age_hrs:.1f} hours old. "
                            f"Run fetch_and_push.py on Mac to refresh.")
            else:
                log.info(f"Data loaded. Age: {age_hrs:.1f}h | "
                         f"Spot: {data['spot']} | Expiry: {data['expiry']} | "
                         f"IV: {data.get('avg_iv', '?')}%")
        except Exception:
            pass

        return data

    @property
    def spot(self) -> float:
        return float(self._data["spot"])

    @property
    def expiry(self) -> str:
        return self._data["expiry"]

    @property
    def avg_iv(self) -> float:
        return float(self._data.get("avg_iv", 16.0))

    @property
    def fetched_at(self) -> str:
        return self._data.get("fetched_at", "unknown")

    def get_calls(self) -> list:
        return self._data.get("calls", [])

    def get_puts(self) -> list:
        return self._data.get("puts", [])

    def get_daily_ohlc(self) -> pd.DataFrame:
        rows = self._data.get("daily_ohlc", [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)

    def get_hourly_ohlc(self) -> pd.DataFrame:
        rows = self._data.get("hourly_ohlc", [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)

    def get_option_chain(self) -> dict:
        """Return in the same format the scanner expects."""
        return {
            "spot":   self.spot,
            "expiry": self.expiry,
            "calls":  [{
                "symbol":          o["symbol"],
                "strike":          o["strike"],
                "option_type":     "CE",
                "last_price":      o["ltp"],
                "iv":              o["iv"],
                "volume":          o["volume"],
                "oi":              o["oi"],
                "expiry":          o["expiry"],
                "in_stonez_range": 60 <= o["ltp"] <= 125,
            } for o in self.get_calls()],
            "puts":   [{
                "symbol":          o["symbol"],
                "strike":          o["strike"],
                "option_type":     "PE",
                "last_price":      o["ltp"],
                "iv":              o["iv"],
                "volume":          o["volume"],
                "oi":              o["oi"],
                "expiry":          o["expiry"],
                "in_stonez_range": 60 <= o["ltp"] <= 125,
            } for o in self.get_puts()],
            "source": "nse_live_via_mac",
            "avg_iv": self.avg_iv,
            "fetched_at": self.fetched_at,
        }
