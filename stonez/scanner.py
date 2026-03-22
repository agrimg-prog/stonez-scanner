"""
scanner.py — reads from DataReader (real NSE data), runs Stonez analysis.
"""

import logging
from datetime import datetime, date
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd
import numpy as np

from stonez.data_reader import DataReader

log = logging.getLogger(__name__)


class SignalStrength(str, Enum):
    STRONG    = "STRONG"
    MODERATE  = "MODERATE"
    NO_TRADE  = "NO_TRADE"


@dataclass
class Trigger:
    timestamp:       str
    side:            str
    symbol:          str
    strike:          float
    expiry:          str
    entry_price:     float
    sl_price:        float
    target_price:    float
    signal_strength: SignalStrength
    rsi_daily:       float
    rsi_hourly:      float
    price_pattern:   str
    spot_level:      float
    risk_per_lot:    float
    dte:             int
    iv:              float
    reasoning:       str


@dataclass
class WatchItem:
    side:         str
    rsi_daily:    float
    rsi_hourly:   float
    message:      str
    spot:         float
    symbol:       str   = ""
    strike:       float = 0.0
    expiry:       str   = ""
    entry_price:  float = 0.0
    sl_price:     float = 0.0
    target_price: float = 0.0
    iv:           float = 0.0


@dataclass
class ScanResult:
    scan_time:      str
    triggers:       list = field(default_factory=list)
    watchlist:      list = field(default_factory=list)
    market_context: dict = field(default_factory=dict)
    summary:        str  = ""


class StonezScanner:

    RSI_OS_STRONG   = 28
    RSI_OS_MODERATE = 35
    RSI_OS_WATCH    = 42

    RSI_OB_STRONG   = 72
    RSI_OB_MODERATE = 65
    RSI_OB_WATCH    = 60

    MIN_VOLUME = 2000
    MIN_OI     = 30000
    MIN_DTE    = 15

    BULLISH_PATTERNS = {"dragonfly_doji", "bullish_engulfing", "hammer"}
    BEARISH_PATTERNS = {"gravestone_doji", "bearish_engulfing", "shooting_star"}

    def __init__(self):
        self.reader = DataReader()

    def _premium_range(self, iv: float):
        """Premium range scales with IV — higher IV = wider range."""
        if iv >= 22:   return 80, 150
        if iv >= 18:   return 65, 125
        return 60, 105

    def run_full_scan(self) -> ScanResult:
        scan_time = datetime.now().isoformat()
        triggers  = []
        watchlist = []

        try:
            ctx   = self.get_market_context()
            chain = self.reader.get_option_chain()
            rsi_d = ctx["rsi_daily"]
            rsi_h = ctx["rsi_hourly"]
            iv    = chain.get("avg_iv", 16.0)

            if rsi_d <= self.RSI_OS_MODERATE or rsi_h <= self.RSI_OS_MODERATE:
                triggers.extend(self._scan("CALL", chain, ctx, iv))
            elif rsi_d <= self.RSI_OS_WATCH:
                item = self._build_watch("CALL", chain, ctx, iv)
                if item: watchlist.append(item)

            if rsi_d >= self.RSI_OB_MODERATE or rsi_h >= self.RSI_OB_MODERATE:
                triggers.extend(self._scan("PUT", chain, ctx, iv))
            elif rsi_d >= self.RSI_OB_WATCH:
                item = self._build_watch("PUT", chain, ctx, iv)
                if item: watchlist.append(item)

        except Exception as e:
            log.error(f"Scan error: {e}", exc_info=True)
            return ScanResult(scan_time=scan_time,
                              summary=f"Scan failed: {e}")

        ctx_clean = {k: v for k, v in ctx.items() if k != "spot_df"}
        return ScanResult(
            scan_time=scan_time,
            triggers=triggers,
            watchlist=watchlist,
            market_context=ctx_clean,
            summary=self._summary(triggers, watchlist, ctx),
        )

    def _scan(self, side, chain, ctx, iv) -> list:
        key    = "calls" if side == "CALL" else "puts"
        p_min, p_max = self._premium_range(iv)
        results = []

        for opt in chain.get(key, []):
            ltp = opt.get("last_price", 0)
            if ltp <= 0:
                continue
            if not (p_min <= ltp <= p_max):
                continue
            if opt.get("volume", 0) < self.MIN_VOLUME:
                continue
            if opt.get("oi", 0) < self.MIN_OI:
                continue

            dte = self._dte(opt.get("expiry", ""))
            if dte < self.MIN_DTE:
                continue

            pattern  = self._pattern(ctx.get("spot_df"))
            above_sma= (ctx["spot"] < ctx["sma_20"]) if side == "CALL" else (ctx["spot"] > ctx["sma_20"])
            strength = self._score(side, ctx["rsi_daily"], ctx["rsi_hourly"], pattern, above_sma)

            if strength == SignalStrength.NO_TRADE:
                continue

            sl = max(round(ltp - 32, 1), round(ltp * 0.62, 1))
            results.append(Trigger(
                timestamp       = datetime.now().isoformat(),
                side            = side,
                symbol          = opt["symbol"],
                strike          = opt["strike"],
                expiry          = opt.get("expiry", ""),
                entry_price     = ltp,
                sl_price        = sl,
                target_price    = round(ltp * 2.0, 1),
                signal_strength = strength,
                rsi_daily       = round(ctx["rsi_daily"], 1),
                rsi_hourly      = round(ctx["rsi_hourly"], 1),
                price_pattern   = pattern,
                spot_level      = ctx["spot"],
                risk_per_lot    = round((ltp - sl) * 75, 0),
                dte             = dte,
                iv              = round(opt.get("iv", iv), 1),
                reasoning       = (
                    f"{'Oversold' if side=='CALL' else 'Overbought'} | "
                    f"Daily RSI {ctx['rsi_daily']} | Hourly RSI {ctx['rsi_hourly']} | "
                    f"Pattern: {pattern.replace('_',' ').title()} | "
                    f"IV: {opt.get('iv', iv):.1f}% | DTE: {dte}d"
                ),
            ))
        return results

    def _build_watch(self, side, chain, ctx, iv) -> Optional[WatchItem]:
        key    = "calls" if side == "CALL" else "puts"
        p_min, p_max = self._premium_range(iv)

        best = None
        for opt in chain.get(key, []):
            ltp = opt.get("last_price", 0)
            if ltp <= 0: continue
            if not (p_min <= ltp <= p_max): continue
            if self._dte(opt.get("expiry", "")) < self.MIN_DTE: continue
            if opt.get("volume", 0) < 500: continue
            best = opt
            break

        rsi_d = ctx["rsi_daily"]
        rsi_h = ctx["rsi_hourly"]
        msg   = (
            f"Daily RSI {rsi_d} approaching oversold (trigger ≤{self.RSI_OS_MODERATE}). "
            f"Watch for hammer/bullish engulfing on daily chart."
            if side == "CALL" else
            f"Daily RSI {rsi_d} approaching overbought (trigger ≥{self.RSI_OB_MODERATE}). "
            f"Watch for shooting star/bearish engulfing on daily chart."
        )
        item = WatchItem(side=side, rsi_daily=round(rsi_d,1),
                         rsi_hourly=round(rsi_h,1), message=msg, spot=ctx["spot"])
        if best:
            ltp = best["last_price"]
            sl  = max(round(ltp - 32, 1), round(ltp * 0.62, 1))
            item.symbol      = best["symbol"]
            item.strike      = best["strike"]
            item.expiry      = best.get("expiry", "")
            item.entry_price = ltp
            item.sl_price    = sl
            item.target_price= round(ltp * 2.0, 1)
            item.iv          = round(best.get("iv", iv), 1)
        return item

    def get_market_context(self) -> dict:
        spot      = self.reader.spot
        daily_df  = self.reader.get_daily_ohlc()
        hourly_df = self.reader.get_hourly_ohlc()

        rsi_d = self._rsi(daily_df)
        rsi_h = self._rsi(hourly_df)
        sma_d = self._sma(daily_df)

        if   rsi_d <= self.RSI_OS_STRONG:   cond = "oversold_extreme"
        elif rsi_d <= self.RSI_OS_MODERATE: cond = "oversold"
        elif rsi_d <= self.RSI_OS_WATCH:    cond = "near_oversold"
        elif rsi_d >= self.RSI_OB_STRONG:   cond = "overbought_extreme"
        elif rsi_d >= self.RSI_OB_MODERATE: cond = "overbought"
        elif rsi_d >= self.RSI_OB_WATCH:    cond = "near_overbought"
        else:                                cond = "neutral"

        return {
            "spot":       round(spot, 1),
            "rsi_daily":  round(rsi_d, 1),
            "rsi_hourly": round(rsi_h, 1),
            "sma_20":     round(sma_d, 1),
            "condition":  cond,
            "trend":      "bullish" if spot > sma_d else "bearish",
            "data_source": "nse_live_via_mac",
            "data_age":   self.reader.fetched_at,
            "scan_time":  datetime.now().strftime("%d-%b-%Y %I:%M %p IST"),
            "spot_df":    daily_df,
        }

    def _rsi(self, df, period=14) -> float:
        if df is None or df.empty or len(df) < period + 1: return 50.0
        d = df["close"].diff()
        g = d.clip(lower=0).ewm(com=period-1, min_periods=period).mean()
        l = (-d.clip(upper=0)).ewm(com=period-1, min_periods=period).mean()
        return float(100 - 100 / (1 + (g / l.replace(0, float("nan"))).iloc[-1]))

    def _sma(self, df, period=20) -> float:
        if df is None or df.empty or len(df) < period:
            return float(df["close"].mean()) if (df is not None and not df.empty) else 0.0
        return float(df["close"].tail(period).mean())

    def _pattern(self, df) -> str:
        if df is None or len(df) < 3: return "none"
        r  = df.iloc[-1]; p = df.iloc[-2]
        o, h, l, c = r["open"], r["high"], r["low"], r["close"]
        po, pc = p["open"], p["close"]
        b = abs(c-o); rng = h-l
        if rng == 0: return "none"
        if b/rng < .10 and (h-max(o,c))/rng > .60: return "gravestone_doji"
        if b/rng < .10 and (min(o,c)-l)/rng > .60:  return "dragonfly_doji"
        if c<o and o>pc and c<po:                     return "bearish_engulfing"
        if c>o and o<pc and c>po:                     return "bullish_engulfing"
        if c<o and (h-o)/rng>.55 and b/rng<.30:      return "shooting_star"
        if c>o and (o-l)/rng>.55 and b/rng<.30:      return "hammer"
        if b/rng < .12:                               return "doji"
        return "none"

    def _score(self, side, rsi_d, rsi_h, pattern, above_sma) -> SignalStrength:
        s = 0
        if side == "CALL":
            s += 3 if rsi_d <= self.RSI_OS_STRONG   else (2 if rsi_d <= self.RSI_OS_MODERATE else 0)
            s += 2 if rsi_h <= self.RSI_OS_STRONG   else (1 if rsi_h <= self.RSI_OS_MODERATE else 0)
            s += 2 if pattern in self.BULLISH_PATTERNS else (1 if pattern == "doji" else 0)
        else:
            s += 3 if rsi_d >= self.RSI_OB_STRONG   else (2 if rsi_d >= self.RSI_OB_MODERATE else 0)
            s += 2 if rsi_h >= self.RSI_OB_STRONG   else (1 if rsi_h >= self.RSI_OB_MODERATE else 0)
            s += 2 if pattern in self.BEARISH_PATTERNS else (1 if pattern == "doji" else 0)
        if above_sma: s += 1
        return SignalStrength.STRONG if s >= 5 else (SignalStrength.MODERATE if s >= 3 else SignalStrength.NO_TRADE)

    def _dte(self, expiry_str: str) -> int:
        try:
            return max(0, (datetime.strptime(expiry_str, "%d-%b-%Y").date() - date.today()).days)
        except Exception:
            return 30

    def _summary(self, triggers, watchlist, ctx) -> str:
        base = f"NIFTY {ctx.get('spot',0):.0f} | RSI {ctx.get('rsi_daily',0)} | {ctx.get('condition','').upper().replace('_',' ')}"
        if triggers:
            strong = sum(1 for t in triggers if t.signal_strength == SignalStrength.STRONG)
            return f"{len(triggers)} trigger(s): {strong} STRONG | {base}"
        if watchlist:
            return f"WATCHLIST: {len(watchlist)} item(s) | {base}"
        return f"No setup | {base}"
