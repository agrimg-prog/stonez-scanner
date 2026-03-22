"""
fetch_and_push.py — runs on YOUR MAC twice a day.
Fetches real NSE option chain + NIFTY OHLC from your Indian IP,
saves to option_data.json, commits and pushes to GitHub.

Schedule on Mac:
  bash setup_mac_cron.sh   (run once)

Then it auto-runs at 9:15 AM and 3:20 PM IST on weekdays.
"""

import json
import time
import logging
import subprocess
import sys
import os
from datetime import datetime, date, timedelta
import calendar
import requests

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}

YF_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
YF_HDR  = {"User-Agent": "Mozilla/5.0"}


def init_nse_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    log.info("Warming up NSE session...")
    session.get("https://www.nseindia.com/", timeout=15)
    time.sleep(2)
    session.get("https://www.nseindia.com/market-data/equity-derivatives-watch", timeout=15)
    time.sleep(1)
    return session


def fetch_option_chain(session: requests.Session) -> dict:
    url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
    r   = session.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    if "records" not in data:
        raise ValueError(f"NSE response missing 'records'. Got: {list(data.keys())}")
    return data


def fetch_nifty_ohlc_daily() -> list:
    """60 days of daily OHLC from Yahoo Finance."""
    for sym in ["^NSEI", "NIFTY50.NS"]:
        try:
            r = requests.get(f"{YF_BASE}/{sym}", headers=YF_HDR,
                             params={"interval": "1d", "range": "3mo"}, timeout=15)
            if r.status_code == 200:
                result = r.json()["chart"]["result"][0]
                ts     = result["timestamp"]
                q      = result["indicators"]["quote"][0]
                rows   = []
                for i, t in enumerate(ts):
                    if q["close"][i] is None: continue
                    rows.append({
                        "date":   datetime.fromtimestamp(t).strftime("%Y-%m-%d"),
                        "open":   round(q["open"][i] or 0, 2),
                        "high":   round(q["high"][i] or 0, 2),
                        "low":    round(q["low"][i] or 0, 2),
                        "close":  round(q["close"][i], 2),
                        "volume": int(q["volume"][i] or 0),
                    })
                log.info(f"Daily OHLC: {len(rows)} rows from {sym}")
                return rows
        except Exception as e:
            log.warning(f"Daily OHLC {sym}: {e}")
    return []


def fetch_nifty_ohlc_hourly() -> list:
    """30 days of hourly OHLC from Yahoo Finance."""
    for sym in ["^NSEI", "NIFTY50.NS"]:
        try:
            r = requests.get(f"{YF_BASE}/{sym}", headers=YF_HDR,
                             params={"interval": "60m", "range": "30d"}, timeout=15)
            if r.status_code == 200:
                result = r.json()["chart"]["result"][0]
                ts     = result["timestamp"]
                q      = result["indicators"]["quote"][0]
                rows   = []
                for i, t in enumerate(ts):
                    if q["close"][i] is None: continue
                    rows.append({
                        "date":  datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M"),
                        "open":  round(q["open"][i] or 0, 2),
                        "high":  round(q["high"][i] or 0, 2),
                        "low":   round(q["low"][i] or 0, 2),
                        "close": round(q["close"][i], 2),
                    })
                log.info(f"Hourly OHLC: {len(rows)} rows from {sym}")
                return rows
        except Exception as e:
            log.warning(f"Hourly OHLC {sym}: {e}")
    return []


def stonez_expiry(all_expiries: list) -> str:
    """Pick expiry per Stonez rule: before 10th = current, after 10th = next."""
    today = date.today()
    if today.day <= 10:
        return all_expiries[0]
    return all_expiries[1] if len(all_expiries) > 1 else all_expiries[0]


def build_data_file(raw: dict, daily: list, hourly: list) -> dict:
    """Build a clean option_data.json from raw NSE response."""
    spot         = float(raw["records"]["underlyingValue"])
    all_expiries = raw["records"]["expiryDates"]
    exp_str      = stonez_expiry(all_expiries)

    calls, puts = [], []
    for row in raw["records"]["data"]:
        if row.get("expiryDate") != exp_str:
            continue
        strike = row["strikePrice"]
        if "CE" in row:
            ce = row["CE"]
            calls.append({
                "symbol":    f"NIFTY{exp_str.replace('-','')}{int(strike)}CE",
                "strike":    strike,
                "type":      "CE",
                "ltp":       ce.get("lastPrice", 0),
                "iv":        ce.get("impliedVolatility", 0),
                "volume":    ce.get("totalTradedVolume", 0),
                "oi":        ce.get("openInterest", 0),
                "bid":       ce.get("bidPrice", 0),
                "ask":       ce.get("askPrice", 0),
                "expiry":    exp_str,
            })
        if "PE" in row:
            pe = row["PE"]
            puts.append({
                "symbol":    f"NIFTY{exp_str.replace('-','')}{int(strike)}PE",
                "strike":    strike,
                "type":      "PE",
                "ltp":       pe.get("lastPrice", 0),
                "iv":        pe.get("impliedVolatility", 0),
                "volume":    pe.get("totalTradedVolume", 0),
                "oi":        pe.get("openInterest", 0),
                "bid":       pe.get("bidPrice", 0),
                "ask":       pe.get("askPrice", 0),
                "expiry":    exp_str,
            })

    # Average IV from ATM strikes (spot ± 500)
    atm_calls = [c for c in calls if abs(c["strike"] - spot) <= 500 and c["iv"] > 0]
    avg_iv    = round(sum(c["iv"] for c in atm_calls) / len(atm_calls), 2) if atm_calls else 16.0

    return {
        "fetched_at":    datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
        "fetched_by":    "mac_local",
        "spot":          spot,
        "expiry":        exp_str,
        "all_expiries":  all_expiries[:4],
        "avg_iv":        avg_iv,
        "calls":         calls,
        "puts":          puts,
        "daily_ohlc":    daily,
        "hourly_ohlc":   hourly,
    }


def push_to_github(data: dict):
    """Save option_data.json and git push."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    filepath   = os.path.join(script_dir, "option_data.json")

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    log.info(f"Saved {len(data['calls'])} calls, {len(data['puts'])} puts to option_data.json")

    try:
        subprocess.run(["git", "-C", script_dir, "add", "option_data.json"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", script_dir, "commit", "-m",
                        f"data: option chain {data['fetched_at']} [skip ci]"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", script_dir, "push"],
                       check=True, capture_output=True)
        log.info("Pushed option_data.json to GitHub.")
    except subprocess.CalledProcessError as e:
        log.error(f"Git push failed: {e.stderr.decode()}")
        sys.exit(1)


def main():
    log.info("=== Fetch and push starting ===")

    session = init_nse_session()

    log.info("Fetching NSE option chain...")
    raw = fetch_option_chain(session)

    log.info("Fetching NIFTY OHLC...")
    daily  = fetch_nifty_ohlc_daily()
    hourly = fetch_nifty_ohlc_hourly()

    data = build_data_file(raw, daily, hourly)
    log.info(f"Spot: {data['spot']} | Expiry: {data['expiry']} | IV: {data['avg_iv']}% | "
             f"Calls: {len(data['calls'])} | Puts: {len(data['puts'])}")

    push_to_github(data)
    log.info("=== Done. GitHub Actions will now run the analysis. ===")


if __name__ == "__main__":
    main()
