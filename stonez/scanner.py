"""
Stonez Scanner — all six strategy rules applied to free NSE data.
"""

import logging
from datetime import datetime, date
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from stonez.nse_client import NSEClient

log = logging.getLogger(__name__)


class SignalStrength(str, Enum):
    STRONG   = "STRONG"
    MODERATE = "MODERATE"
    NO_TRADE = "NO_TRADE"


@dataclass
class Trigger:
    timestamp:        str
    side:             str
    symbol:           str
    strike:           float
    expiry:           str
    entry_price:      float
    sl_price:         float
    target_price:     float
    signal_strength:  SignalStrength
    rsi_daily:        float
    rsi_hourly:       float
    price_pattern:    str
    above_20sma:      bool
    spot_level:       float
    risk_per_lot:     float
    reasoning:        str


@dataclass
class ScanResult:
    scan_time:      str
    triggers:       list = field(default_factory=list)
    market_context: dict = field(default_factory=dict)
    summary:        str  = ""


class StonezScanner:

    PREMIUM_MIN    = 65
    PREMIUM_MAX    = 105
    SMA_PERIOD     = 20
    RSI_PERIOD     = 14
    RSI_OB         = 75
    RSI_OS         = 30
    RSI_EXTREME_OB = 85
    RSI_EXTREME_OS = 20
    MIN_VOLUME     = 2000
    MIN_OI         = 30000

    BEARISH_PATTERNS = {"gravestone_doji", "bearish_engulfing", "shooting_star"}
    BULLISH_PATTERNS = {"dragonfly_doji", "bullish_engulfing", "hammer"}

    def __init__(self):
        self.nse = NSEClient()

    # ── Main entry point ────────────────────────────────────────────────────

    def run_full_scan(self) -> ScanResult:
        scan_time = datetime.now().isoformat()
        triggers  = []

        try:
            ctx   = self.get_market_context()
            chain = self.nse.get_option_chain()

            if ctx["condition"] in ("oversold", "neutral_low"):
                triggers.extend(self._scan_calls(chain, ctx))

            if ctx["condition"] in ("overbought", "neutral_high"):
                triggers.extend(self._scan_puts(chain, ctx))

            # Even in neutral zone — report what's closest
            if not triggers:
                log.info(f"No triggers. RSI daily={ctx['rsi_daily']} | condition={ctx['condition']}")

        except Exception as e:
            log.error(f"Scan error: {e}", exc_info=True)
            return ScanResult(scan_time=scan_time, summary=f"Scan failed: {e}")

        return ScanResult(
            scan_time=scan_time,
            triggers=triggers,
            market_context={k: v for k, v in ctx.items() if k != "spot_df"},
            summary=self._build_summary(triggers, ctx),
        )

    # ── CALL scan ─────────────────────────────────────────────────────────

    def _scan_calls(self, chain: dict, ctx: dict) -> list:
        results = []
        for opt in chain.get("calls", []):
            if not self._passes_premium(opt["last_price"]):
                continue
            if not self._passes_liquidity(opt):
                continue
            opt_df   = self.nse.get_option_ohlc(opt["symbol"])
            sma_ok   = self._above_20sma(opt_df)
            rsi_h    = self._rsi_hourly()
            pattern  = self._detect_pattern(ctx.get("spot_df", pd.DataFrame()))
            strength = self._score("CALL", sma_ok, ctx["rsi_daily"], rsi_h, pattern)
            if strength == SignalStrength.NO_TRADE:
                continue
            sl  = self._calc_sl(opt_df, opt["last_price"])
            results.append(Trigger(
                timestamp=datetime.now().isoformat(), side="CALL",
                symbol=opt["symbol"], strike=opt["strike"], expiry=opt["expiry"],
                entry_price=opt["last_price"], sl_price=sl,
                target_price=round(opt["last_price"] * 2, 1),
                signal_strength=strength,
                rsi_daily=round(ctx["rsi_daily"], 1), rsi_hourly=round(rsi_h, 1),
                price_pattern=pattern, above_20sma=sma_ok,
                spot_level=chain.get("spot", 0),
                risk_per_lot=round((opt["last_price"] - sl) * 75, 0),
                reasoning=self._reason("CALL", ctx, pattern, sma_ok),
            ))
        return results

    # ── PUT scan ──────────────────────────────────────────────────────────

    def _scan_puts(self, chain: dict, ctx: dict) -> list:
        results = []
        for opt in chain.get("puts", []):
            if not self._passes_premium(opt["last_price"]):
                continue
            if not self._passes_liquidity(opt):
                continue
            opt_df   = self.nse.get_option_ohlc(opt["symbol"])
            sma_ok   = self._above_20sma(opt_df)
            rsi_h    = self._rsi_hourly()
            pattern  = self._detect_pattern(ctx.get("spot_df", pd.DataFrame()))
            strength = self._score("PUT", sma_ok, ctx["rsi_daily"], rsi_h, pattern)
            if strength == SignalStrength.NO_TRADE:
                continue
            sl  = self._calc_sl(opt_df, opt["last_price"])
            results.append(Trigger(
                timestamp=datetime.now().isoformat(), side="PUT",
                symbol=opt["symbol"], strike=opt["strike"], expiry=opt["expiry"],
                entry_price=opt["last_price"], sl_price=sl,
                target_price=round(opt["last_price"] * 2, 1),
                signal_strength=strength,
                rsi_daily=round(ctx["rsi_daily"], 1), rsi_hourly=round(rsi_h, 1),
                price_pattern=pattern, above_20sma=sma_ok,
                spot_level=chain.get("spot", 0),
                risk_per_lot=round((opt["last_price"] - sl) * 75, 0),
                reasoning=self._reason("PUT", ctx, pattern, sma_ok),
            ))
        return results

    # ── Market context ────────────────────────────────────────────────────

    def get_market_context(self) -> dict:
        spot      = self.nse.get_spot_price()
        daily_df  = self.nse.get_nifty_ohlc(interval="1d",  days=60)
        hourly_df = self.nse.get_nifty_ohlc(interval="60m", days=20)

        rsi_d = self._calc_rsi(daily_df)  if not daily_df.empty  else 50.0
        rsi_h = self._calc_rsi(hourly_df) if not hourly_df.empty else 50.0
        sma_d = self._calc_sma(daily_df)  if not daily_df.empty  else spot

        if rsi_d >= self.RSI_EXTREME_OB or rsi_h >= self.RSI_EXTREME_OB:
            condition = "overbought"
        elif rsi_d >= self.RSI_OB:
            condition = "neutral_high"
        elif rsi_d <= self.RSI_EXTREME_OS or rsi_h <= self.RSI_EXTREME_OS:
            condition = "oversold"
        elif rsi_d <= self.RSI_OS:
            condition = "neutral_low"
        else:
            condition = "neutral"

        recent_high = float(daily_df["high"].tail(20).max()) if not daily_df.empty else spot
        recent_low  = float(daily_df["low"].tail(20).min())  if not daily_df.empty else spot

        stonez_expiry = "current_month" if date.today().day <= 10 else "next_month"

        return {
            "spot":           round(spot, 1),
            "rsi_daily":      round(rsi_d, 1),
            "rsi_hourly":     round(rsi_h, 1),
            "sma_20_daily":   round(sma_d, 1),
            "condition":      condition,
            "trend":          "bullish" if spot > sma_d else "bearish",
            "recent_high":    round(recent_high, 1),
            "recent_low":     round(recent_low, 1),
            "stonez_expiry":  stonez_expiry,
            "spot_df":        daily_df,
        }

    # ── Indicators ───────────────────────────────────────────────────────

    def _calc_rsi(self, df: pd.DataFrame, period: int = 14) -> float:
        if df.empty or len(df) < period + 1:
            return 50.0
        delta = df["close"].diff()
        gain  = delta.clip(lower=0)
        loss  = -delta.clip(upper=0)
        avg_g = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_l = loss.ewm(com=period - 1, min_periods=period).mean()
        rs    = avg_g / avg_l.replace(0, np.nan)
        rsi   = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1])

    def _calc_sma(self, df: pd.DataFrame) -> float:
        if df.empty or len(df) < self.SMA_PERIOD:
            return float(df["close"].mean()) if not df.empty else 0
        return float(df["close"].tail(self.SMA_PERIOD).mean())

    def _above_20sma(self, df: pd.DataFrame) -> bool:
        if df.empty or len(df) < 2:
            return False
        sma = self._calc_sma(df)
        return float(df["close"].iloc[-1]) > sma

    def _rsi_hourly(self) -> float:
        df = self.nse.get_nifty_ohlc(interval="60m", days=20)
        return self._calc_rsi(df)

    def _calc_sl(self, df: pd.DataFrame, entry: float) -> float:
        sma      = self._calc_sma(df)
        hard_sl  = round(entry - 32, 1)
        sma_sl   = round(sma * 0.90, 1)
        return max(hard_sl, sma_sl, 5.0)

    # ── Pattern detection ─────────────────────────────────────────────────

    def _detect_pattern(self, df: pd.DataFrame) -> str:
        if df is None or len(df) < 3:
            return "none"
        last = df.iloc[-1]
        prev = df.iloc[-2]
        o, h, l, c = last["open"], last["high"], last["low"], last["close"]
        body  = abs(c - o)
        range_= h - l
        if range_ == 0:
            return "none"

        if body / range_ < 0.10 and (h - max(o, c)) / range_ > 0.65:
            return "gravestone_doji"
        if body / range_ < 0.10 and (min(o, c) - l) / range_ > 0.65:
            return "dragonfly_doji"
        if c < o and o > prev["close"] and c < prev["open"]:
            return "bearish_engulfing"
        if c > o and o < prev["close"] and c > prev["open"]:
            return "bullish_engulfing"
        if c < o and (h - o) / range_ > 0.55 and body / range_ < 0.30:
            return "shooting_star"
        if c > o and (o - l) / range_ > 0.55 and body / range_ < 0.30:
            return "hammer"
        if body / range_ < 0.10:
            return "doji"
        return "none"

    # ── Signal scoring ────────────────────────────────────────────────────

    def _score(self, side, sma_ok, rsi_d, rsi_h, pattern) -> SignalStrength:
        score = 0
        if side == "CALL":
            if rsi_d <= self.RSI_OS or rsi_h <= self.RSI_OS:        score += 1
            if rsi_d <= self.RSI_EXTREME_OS or rsi_h <= self.RSI_EXTREME_OS: score += 1
            if pattern in self.BULLISH_PATTERNS:                      score += 1
        else:
            if rsi_d >= self.RSI_OB or rsi_h >= self.RSI_OB:        score += 1
            if rsi_d >= self.RSI_EXTREME_OB or rsi_h >= self.RSI_EXTREME_OB: score += 1
            if pattern in self.BEARISH_PATTERNS:                      score += 1
        if sma_ok: score += 1

        if score >= 3: return SignalStrength.STRONG
        if score == 2: return SignalStrength.MODERATE
        return SignalStrength.NO_TRADE

    # ── Helpers ──────────────────────────────────────────────────────────

    def _passes_premium(self, price: float) -> bool:
        return self.PREMIUM_MIN <= price <= self.PREMIUM_MAX

    def _passes_liquidity(self, opt: dict) -> bool:
        return opt.get("volume", 0) >= self.MIN_VOLUME and opt.get("oi", 0) >= self.MIN_OI

    def _reason(self, side, ctx, pattern, sma_ok) -> str:
        return (
            f"{'Oversold' if side == 'CALL' else 'Overbought'} | "
            f"Daily RSI {ctx['rsi_daily']} | Hourly RSI {ctx.get('rsi_hourly','—')} | "
            f"Pattern: {pattern.replace('_',' ').title()} | "
            f"Option above 20 SMA: {'Yes' if sma_ok else 'No'} | "
            f"Trend: {ctx.get('trend','—').upper()}"
        )

    def _build_summary(self, triggers, ctx) -> str:
        if not triggers:
            return (
                f"No Stonez setups. NIFTY {ctx.get('spot',0):.0f} | "
                f"Daily RSI {ctx.get('rsi_daily',0)} | {ctx.get('condition','').upper()}"
            )
        strong = sum(1 for t in triggers if t.signal_strength == SignalStrength.STRONG)
        mod    = sum(1 for t in triggers if t.signal_strength == SignalStrength.MODERATE)
        return (
            f"{len(triggers)} trigger(s): {strong} STRONG, {mod} MODERATE | "
            f"NIFTY {ctx.get('spot',0):.0f} | RSI {ctx.get('rsi_daily',0)} | "
            f"{ctx.get('condition','').upper()}"
        )
