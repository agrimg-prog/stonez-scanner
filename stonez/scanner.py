"""
Stonez Scanner — complete, self-consistent version.
All classes defined here. No missing attributes.
"""

import logging
from datetime import datetime, date
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd
import numpy as np

from stonez.nse_client import NSEClient

log = logging.getLogger(__name__)


# ── Enums ────────────────────────────────────────────────────────────────────

class SignalStrength(str, Enum):
    STRONG    = "STRONG"
    MODERATE  = "MODERATE"
    WATCHLIST = "WATCHLIST"
    NO_TRADE  = "NO_TRADE"


# ── Data classes ─────────────────────────────────────────────────────────────

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
    above_20sma:     bool
    spot_level:      float
    risk_per_lot:    float
    reasoning:       str
    dte:             int = 0


@dataclass
class WatchItem:
    side:        str
    rsi_daily:   float
    rsi_hourly:  float
    message:     str
    spot:        float
    symbol:      str   = ""
    entry_price: float = 0.0
    sl_price:    float = 0.0
    target_price: float = 0.0
    expiry:      str   = ""
    strike:      float = 0.0


@dataclass
class ScanResult:
    scan_time:      str
    triggers:       list = field(default_factory=list)
    watchlist:      list = field(default_factory=list)
    market_context: dict = field(default_factory=dict)
    summary:        str  = ""


# ── Scanner ──────────────────────────────────────────────────────────────────

class StonezScanner:

    # RSI thresholds
    RSI_OS_STRONG   = 28
    RSI_OS_MODERATE = 35
    RSI_OS_WATCH    = 42

    RSI_OB_STRONG   = 72
    RSI_OB_MODERATE = 65
    RSI_OB_WATCH    = 60

    SMA_PERIOD = 20
    MIN_VOLUME = 2000
    MIN_OI     = 30000
    MIN_DTE    = 15

    PREMIUM_MIN        = 60
    PREMIUM_MAX        = 125
    PREMIUM_MIN_LOW_IV = 65
    PREMIUM_MAX_LOW_IV = 105

    BULLISH_PATTERNS = {"dragonfly_doji", "bullish_engulfing", "hammer"}
    BEARISH_PATTERNS = {"gravestone_doji", "bearish_engulfing", "shooting_star"}

    def __init__(self):
        self.nse = NSEClient()

    # ── Main scan ────────────────────────────────────────────────────────────

    def run_full_scan(self) -> ScanResult:
        scan_time = datetime.now().isoformat()
        triggers  = []
        watchlist = []

        try:
            ctx   = self.get_market_context()
            chain = self.nse.get_option_chain()
            rsi_d = ctx["rsi_daily"]
            rsi_h = ctx["rsi_hourly"]

            # CALL side
            if rsi_d <= self.RSI_OS_MODERATE or rsi_h <= self.RSI_OS_MODERATE:
                triggers.extend(self._scan_side(chain, ctx, "CALL"))

            if not triggers and rsi_d <= self.RSI_OS_WATCH:
                item = self._build_watchitem(chain, ctx, "CALL")
                if item:
                    watchlist.append(item)

            # PUT side
            if rsi_d >= self.RSI_OB_MODERATE or rsi_h >= self.RSI_OB_MODERATE:
                triggers.extend(self._scan_side(chain, ctx, "PUT"))

            if not triggers and rsi_d >= self.RSI_OB_WATCH:
                item = self._build_watchitem(chain, ctx, "PUT")
                if item:
                    watchlist.append(item)

        except Exception as e:
            log.error(f"Scan error: {e}", exc_info=True)
            return ScanResult(
                scan_time=scan_time,
                triggers=[],
                watchlist=[],
                market_context={},
                summary=f"Scan failed: {e}"
            )

        ctx_clean = {k: v for k, v in ctx.items() if k != "spot_df"}
        summary   = self._build_summary(triggers, watchlist, ctx)

        return ScanResult(
            scan_time=scan_time,
            triggers=triggers,
            watchlist=watchlist,
            market_context=ctx_clean,
            summary=summary,
        )

    # ── Side scanner ─────────────────────────────────────────────────────────

    def _scan_side(self, chain, ctx, side) -> list:
        results  = []
        flag     = "calls" if side == "CALL" else "puts"
        iv       = self._avg_iv(chain, flag)
        p_min, p_max = (self.PREMIUM_MIN, self.PREMIUM_MAX) if iv > 18 else (self.PREMIUM_MIN_LOW_IV, self.PREMIUM_MAX_LOW_IV)

        for opt in chain.get(flag, []):
            ltp = opt.get("last_price", 0)
            if not (p_min <= ltp <= p_max):
                continue
            if not self._passes_liquidity(opt):
                continue

            dte = self._calc_dte(opt.get("expiry", ""))
            if dte < self.MIN_DTE:
                continue

            rsi_h    = ctx.get("rsi_hourly", 50)
            pattern  = self._detect_pattern(ctx.get("spot_df"))
            above_sma = (ctx["spot"] < ctx["sma_20_daily"]) if side == "CALL" else (ctx["spot"] > ctx["sma_20_daily"])
            strength  = self._score(side, ctx["rsi_daily"], rsi_h, pattern, above_sma)

            if strength == SignalStrength.NO_TRADE:
                continue

            sl  = max(round(ltp - 32, 1), round(ltp * 0.60, 1))
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
                rsi_hourly      = round(rsi_h, 1),
                price_pattern   = pattern,
                above_20sma     = above_sma,
                spot_level      = chain.get("spot", 0),
                risk_per_lot    = round((ltp - sl) * 75, 0),
                reasoning       = self._reason(side, ctx, pattern, above_sma, dte),
                dte             = dte,
            ))
        return results

    def _build_watchitem(self, chain, ctx, side) -> Optional[WatchItem]:
        """Find the best option in range and build a watchlist item."""
        flag     = "calls" if side == "CALL" else "puts"
        iv       = self._avg_iv(chain, flag)
        p_min, p_max = (self.PREMIUM_MIN, self.PREMIUM_MAX) if iv > 18 else (self.PREMIUM_MIN_LOW_IV, self.PREMIUM_MAX_LOW_IV)

        best = None
        for opt in chain.get(flag, []):
            ltp = opt.get("last_price", 0)
            if not (p_min <= ltp <= p_max):
                continue
            if self._calc_dte(opt.get("expiry", "")) < self.MIN_DTE:
                continue
            if opt.get("volume", 0) < 500:
                continue
            best = opt
            break

        rsi_d = ctx["rsi_daily"]
        rsi_h = ctx.get("rsi_hourly", 50)

        if side == "CALL":
            msg = (f"Daily RSI {rsi_d} approaching oversold zone (trigger ≤{self.RSI_OS_MODERATE}). "
                   f"Watch for hammer or bullish engulfing candle on daily chart to confirm entry.")
        else:
            msg = (f"Daily RSI {rsi_d} approaching overbought zone (trigger ≥{self.RSI_OB_MODERATE}). "
                   f"Watch for shooting star or bearish engulfing candle on daily chart to confirm entry.")

        item = WatchItem(
            side       = side,
            rsi_daily  = round(rsi_d, 1),
            rsi_hourly = round(rsi_h, 1),
            message    = msg,
            spot       = ctx["spot"],
        )

        if best:
            sl  = max(round(best["last_price"] - 32, 1), round(best["last_price"] * 0.60, 1))
            item.symbol      = best["symbol"]
            item.entry_price = best["last_price"]
            item.sl_price    = sl
            item.target_price= round(best["last_price"] * 2.0, 1)
            item.expiry      = best.get("expiry", "")
            item.strike      = best["strike"]

        return item

    # ── Market context ────────────────────────────────────────────────────────

    def get_market_context(self) -> dict:
        spot      = self.nse.get_spot_price()
        daily_df  = self.nse.get_nifty_ohlc(interval="1d",  days=60)
        hourly_df = self.nse.get_nifty_ohlc(interval="60m", days=20)

        rsi_d = self._calc_rsi(daily_df)  if not daily_df.empty else 50.0
        rsi_h = self._calc_rsi(hourly_df) if not hourly_df.empty else 50.0
        sma_d = self._calc_sma(daily_df)  if not daily_df.empty else spot

        if   rsi_d <= self.RSI_OS_STRONG:   condition = "oversold_extreme"
        elif rsi_d <= self.RSI_OS_MODERATE: condition = "oversold"
        elif rsi_d <= self.RSI_OS_WATCH:    condition = "near_oversold"
        elif rsi_d >= self.RSI_OB_STRONG:   condition = "overbought_extreme"
        elif rsi_d >= self.RSI_OB_MODERATE: condition = "overbought"
        elif rsi_d >= self.RSI_OB_WATCH:    condition = "near_overbought"
        else:                                condition = "neutral"

        recent_high = float(daily_df["high"].tail(20).max()) if not daily_df.empty else spot
        recent_low  = float(daily_df["low"].tail(20).min())  if not daily_df.empty else spot

        return {
            "spot":          round(spot, 1),
            "rsi_daily":     round(rsi_d, 1),
            "rsi_hourly":    round(rsi_h, 1),
            "sma_20_daily":  round(sma_d, 1),
            "condition":     condition,
            "trend":         "bullish" if spot > sma_d else "bearish",
            "recent_high":   round(recent_high, 1),
            "recent_low":    round(recent_low, 1),
            "stonez_expiry": "current_month" if date.today().day <= 10 else "next_month",
            "data_source":   "yahoo_finance_live",
            "scan_time":     datetime.now().strftime("%d-%b-%Y %I:%M %p IST"),
            "spot_df":       daily_df,
        }

    # ── Indicators ───────────────────────────────────────────────────────────

    def _calc_rsi(self, df, period=14):
        if df is None or df.empty or len(df) < period + 1:
            return 50.0
        delta = df["close"].diff()
        gain  = delta.clip(lower=0)
        loss  = -delta.clip(upper=0)
        avg_g = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_l = loss.ewm(com=period - 1, min_periods=period).mean()
        rs    = avg_g / avg_l.replace(0, np.nan)
        rsi   = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1])

    def _calc_sma(self, df, period=20):
        if df is None or df.empty or len(df) < period:
            return float(df["close"].mean()) if (df is not None and not df.empty) else 0.0
        return float(df["close"].tail(period).mean())

    def _detect_pattern(self, df) -> str:
        if df is None or len(df) < 3:
            return "none"
        last = df.iloc[-1]
        prev = df.iloc[-2]
        o, h, l, c = last["open"], last["high"], last["low"], last["close"]
        po, pc     = prev["open"], prev["close"]
        body = abs(c - o)
        rng  = h - l
        if rng == 0:
            return "none"
        if body / rng < 0.10 and (h - max(o, c)) / rng > 0.60: return "gravestone_doji"
        if body / rng < 0.10 and (min(o, c) - l) / rng > 0.60: return "dragonfly_doji"
        if c < o and o > pc and c < po:                          return "bearish_engulfing"
        if c > o and o < pc and c > po:                          return "bullish_engulfing"
        if c < o and (h - o) / rng > 0.55 and body / rng < 0.30: return "shooting_star"
        if c > o and (o - l) / rng > 0.55 and body / rng < 0.30: return "hammer"
        if body / rng < 0.12:                                     return "doji"
        return "none"

    def _score(self, side, rsi_d, rsi_h, pattern, above_sma) -> SignalStrength:
        score = 0
        if side == "CALL":
            if   rsi_d <= self.RSI_OS_STRONG:   score += 3
            elif rsi_d <= self.RSI_OS_MODERATE: score += 2
            if   rsi_h <= self.RSI_OS_STRONG:   score += 2
            elif rsi_h <= self.RSI_OS_MODERATE: score += 1
            if pattern in self.BULLISH_PATTERNS: score += 2
            elif pattern == "doji":              score += 1
        else:
            if   rsi_d >= self.RSI_OB_STRONG:   score += 3
            elif rsi_d >= self.RSI_OB_MODERATE: score += 2
            if   rsi_h >= self.RSI_OB_STRONG:   score += 2
            elif rsi_h >= self.RSI_OB_MODERATE: score += 1
            if pattern in self.BEARISH_PATTERNS: score += 2
            elif pattern == "doji":              score += 1
        if above_sma:
            score += 1

        if score >= 5: return SignalStrength.STRONG
        if score >= 3: return SignalStrength.MODERATE
        return SignalStrength.NO_TRADE

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _passes_liquidity(self, opt) -> bool:
        return opt.get("volume", 0) >= self.MIN_VOLUME and opt.get("oi", 0) >= self.MIN_OI

    def _avg_iv(self, chain, side_key) -> float:
        ivs = [o["iv"] for o in chain.get(side_key, []) if o.get("iv", 0) > 0]
        return (sum(ivs) / len(ivs)) if ivs else 16.0

    def _calc_dte(self, expiry_str: str) -> int:
        try:
            exp = datetime.strptime(expiry_str, "%d-%b-%Y").date()
            return max(0, (exp - date.today()).days)
        except Exception:
            return 30

    def _reason(self, side, ctx, pattern, above_sma, dte) -> str:
        return (
            f"{'Oversold' if side == 'CALL' else 'Overbought'} | "
            f"Daily RSI {ctx['rsi_daily']} | Hourly RSI {ctx.get('rsi_hourly', '—')} | "
            f"Pattern: {pattern.replace('_', ' ').title()} | "
            f"DTE: {dte} days | Trend: {ctx.get('trend', '—').upper()}"
        )

    def _build_summary(self, triggers, watchlist, ctx) -> str:
        if triggers:
            strong = sum(1 for t in triggers if t.signal_strength == SignalStrength.STRONG)
            mod    = sum(1 for t in triggers if t.signal_strength == SignalStrength.MODERATE)
            return (f"{len(triggers)} trigger(s): {strong} STRONG, {mod} MODERATE | "
                    f"NIFTY {ctx.get('spot', 0):.0f} | RSI {ctx.get('rsi_daily', 0)} | "
                    f"{ctx.get('condition', '').upper()}")
        if watchlist:
            return (f"No triggers — {len(watchlist)} WATCHLIST item(s). "
                    f"NIFTY {ctx.get('spot', 0):.0f} | RSI {ctx.get('rsi_daily', 0)} | "
                    f"{ctx.get('condition', '').upper().replace('_', ' ')}")
        return (f"No Stonez setups. NIFTY {ctx.get('spot', 0):.0f} | "
                f"Daily RSI {ctx.get('rsi_daily', 0)} | "
                f"{ctx.get('condition', '').upper().replace('_', ' ')}")

    # kept for trade_scorer compatibility
    def _above_20sma(self, df) -> bool:
        if df is None or df.empty or len(df) < 2:
            return False
        return float(df["close"].iloc[-1]) > self._calc_sma(df)
