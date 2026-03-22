"""
fetch_and_push.py — runs on YOUR MAC to fetch real NSE data and push to GitHub.
Fixed: proper NSE session with retry logic and better cookie handling.
"""

import json, time, logging, subprocess, sys, os, requests
from datetime import datetime, date
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

YF_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
YF_HDR  = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


# ── NSE Session ──────────────────────────────────────────────────────────────

def make_nse_session() -> requests.Session:
    s = requests.Session()

    # Retry on failures
    retry = Retry(total=3, backoff_factor=1,
                  status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/122.0.0.0 Safari/537.36",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":  "document",
        "Sec-Fetch-Mode":  "navigate",
        "Sec-Fetch-Site":  "none",
        "Cache-Control":   "max-age=0",
    }
    s.headers.update(headers)
    return s


def warm_nse_session(s: requests.Session):
    """
    NSE requires you to visit the main site and option chain page
    before the API endpoint works. Cookies must be set in order.
    """
    steps = [
        ("https://www.nseindia.com/", "main page"),
        ("https://www.nseindia.com/market-data/equity-derivatives-watch",
         "derivatives watch"),
        ("https://www.nseindia.com/option-chain", "option chain page"),
    ]

    for url, name in steps:
        try:
            log.info(f"  Visiting {name}...")
            r = s.get(url, timeout=20)
            log.info(f"  {name}: HTTP {r.status_code} | "
                     f"Cookies: {list(s.cookies.keys())}")
            time.sleep(2)
        except Exception as e:
            log.warning(f"  {name} warning (non-fatal): {e}")
            time.sleep(1)

    # Update headers for API calls
    s.headers.update({
        "Accept":     "application/json, text/plain, */*",
        "Referer":    "https://www.nseindia.com/option-chain",
        "X-Requested-With": "XMLHttpRequest",
    })


def fetch_option_chain(s: requests.Session) -> dict:
    url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"

    for attempt in range(3):
        try:
            log.info(f"  Fetching option chain (attempt {attempt+1})...")
            r = s.get(url, timeout=20)
            log.info(f"  HTTP {r.status_code} | Length: {len(r.content)} bytes")

            if r.status_code == 401:
                log.warning("  401 — session expired, re-warming...")
                warm_nse_session(s)
                time.sleep(3)
                continue

            if r.status_code != 200:
                log.warning(f"  Non-200 response: {r.status_code}")
                time.sleep(2)
                continue

            if len(r.content) < 100:
                log.warning(f"  Response too short ({len(r.content)} bytes) — likely blocked")
                time.sleep(3)
                continue

            data = r.json()

            if isinstance(data, list):
                log.warning(f"  Got list instead of dict — NSE blocked this request")
                time.sleep(3)
                continue

            if "records" not in data:
                log.warning(f"  Missing 'records'. Keys: {list(data.keys())[:5]}")
                time.sleep(2)
                continue

            log.info(f"  Option chain fetched. "
                     f"Spot: {data['records']['underlyingValue']} | "
                     f"Expiries: {data['records']['expiryDates'][:3]}")
            return data

        except requests.exceptions.JSONDecodeError as e:
            log.warning(f"  JSON decode error: {e}. "
                        f"Raw (first 200 chars): {r.text[:200]}")
            time.sleep(2)
        except Exception as e:
            log.warning(f"  Attempt {attempt+1} error: {e}")
            time.sleep(2)

    raise RuntimeError(
        "Could not fetch NSE option chain after 3 attempts.\n"
        "Try running during market hours (9:15 AM – 3:30 PM IST).\n"
        "If problem persists, NSE may be down or blocking your IP."
    )


# ── Yahoo Finance OHLC ───────────────────────────────────────────────────────

def fetch_ohlc(interval: str, range_: str) -> list:
    for sym in ["^NSEI", "NIFTY50.NS"]:
        for base in [YF_BASE, YF_BASE.replace("query1", "query2")]:
            try:
                r = requests.get(f"{base}/{sym}", headers=YF_HDR,
                                 params={"interval": interval, "range": range_},
                                 timeout=15)
                if r.status_code != 200:
                    continue
                result = r.json()["chart"]["result"][0]
                ts     = result["timestamp"]
                q      = result["indicators"]["quote"][0]
                rows   = []
                for i, t in enumerate(ts):
                    if q["close"][i] is None:
                        continue
                    rows.append({
                        "date":   datetime.fromtimestamp(t).strftime(
                                    "%Y-%m-%d" if interval == "1d" else "%Y-%m-%d %H:%M"),
                        "open":   round(q["open"][i] or 0, 2),
                        "high":   round(q["high"][i] or 0, 2),
                        "low":    round(q["low"][i] or 0, 2),
                        "close":  round(q["close"][i], 2),
                        "volume": int(q["volume"][i] or 0),
                    })
                if rows:
                    log.info(f"  OHLC {interval}: {len(rows)} rows from {sym} ({base})")
                    return rows
            except Exception as e:
                log.warning(f"  OHLC {sym} {interval} {base}: {e}")
    log.error(f"  Could not fetch {interval} OHLC from any source")
    return []


# ── Build and push ───────────────────────────────────────────────────────────

def stonez_expiry(expiries: list) -> str:
    return expiries[0] if date.today().day <= 10 else (
        expiries[1] if len(expiries) > 1 else expiries[0])


def build_payload(raw: dict, daily: list, hourly: list) -> dict:
    spot     = float(raw["records"]["underlyingValue"])
    expiries = raw["records"]["expiryDates"]
    exp_str  = stonez_expiry(expiries)

    calls, puts = [], []
    for row in raw["records"]["data"]:
        if row.get("expiryDate") != exp_str:
            continue
        strike = row["strikePrice"]
        if "CE" in row:
            ce = row["CE"]
            calls.append({
                "symbol":  f"NIFTY{exp_str}{int(strike)}CE",
                "strike":  strike, "type": "CE",
                "ltp":     ce.get("lastPrice", 0),
                "iv":      ce.get("impliedVolatility", 0),
                "volume":  ce.get("totalTradedVolume", 0),
                "oi":      ce.get("openInterest", 0),
                "bid":     ce.get("bidPrice", 0),
                "ask":     ce.get("askPrice", 0),
                "expiry":  exp_str,
            })
        if "PE" in row:
            pe = row["PE"]
            puts.append({
                "symbol":  f"NIFTY{exp_str}{int(strike)}PE",
                "strike":  strike, "type": "PE",
                "ltp":     pe.get("lastPrice", 0),
                "iv":      pe.get("impliedVolatility", 0),
                "volume":  pe.get("totalTradedVolume", 0),
                "oi":      pe.get("openInterest", 0),
                "bid":     pe.get("bidPrice", 0),
                "ask":     pe.get("askPrice", 0),
                "expiry":  exp_str,
            })

    atm = [c for c in calls if abs(c["strike"] - spot) <= 500 and c["iv"] > 0]
    avg_iv = round(sum(c["iv"] for c in atm) / len(atm), 2) if atm else 16.0

    return {
        "fetched_at":   datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
        "fetched_by":   "mac_local",
        "spot":         spot,
        "expiry":       exp_str,
        "all_expiries": expiries[:4],
        "avg_iv":       avg_iv,
        "calls":        calls,
        "puts":         puts,
        "daily_ohlc":   daily,
        "hourly_ohlc":  hourly,
    }


def push_to_github(data: dict):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    filepath   = os.path.join(script_dir, "option_data.json")

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    log.info(f"Saved option_data.json — "
             f"{len(data['calls'])} calls, {len(data['puts'])} puts | "
             f"Spot: {data['spot']} | IV: {data['avg_iv']}%")

    cmds = [
        ["git", "-C", script_dir, "add", "option_data.json"],
        ["git", "-C", script_dir, "commit", "-m",
         f"data: NSE option chain {data['fetched_at']} [skip ci]"],
        ["git", "-C", script_dir, "push"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # "nothing to commit" is fine
            if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
                log.info("Nothing new to commit.")
                return
            log.error(f"Git error: {result.stderr}")
            sys.exit(1)
    log.info("Pushed to GitHub successfully.")


def main():
    log.info("=== Stonez fetch_and_push starting ===")

    session = make_nse_session()

    log.info("Warming NSE session (takes ~8 seconds)...")
    warm_nse_session(session)

    log.info("Fetching option chain...")
    raw = fetch_option_chain(session)

    log.info("Fetching NIFTY OHLC...")
    daily  = fetch_ohlc("1d", "3mo")
    hourly = fetch_ohlc("60m", "30d")

    data = build_payload(raw, daily, hourly)
    push_to_github(data)

    log.info(f"=== Done. Spot: {data['spot']} | "
             f"Expiry: {data['expiry']} | IV: {data['avg_iv']}% ===")


if __name__ == "__main__":
    main()
