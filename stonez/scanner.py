"""
scanner.py — Stonez signal scanner.

What this does accurately (real free data):
  - NIFTY spot, daily + hourly OHLC from Yahoo Finance
  - RSI (daily + hourly), 20 SMA, price action patterns
  - India VIX for market context
  - Stonez trigger detection (RSI extremes + patterns)

What this does NOT do (requires paid API):
  - Option chain lookup with real LTP
  - Actual premium verification

When a trigger fires, the alert tells you exactly which
strike range to check on Zerodha, and what to look for.
You verify the actual premium in 30 seconds on Zerodha.
"""

import logging
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import pandas as pd
import numpy as np
import calendar

from stonez.market_data import (
    get_nifty_spot, get_india_vix, get_nifty_ohlc, get_stonez_expiry
)

log = logging.getLogger(__name__)


class SignalStrength(str, Enum):
    STRONG   = "STRONG"
    MODERATE = "MODERATE"
    NO_TRADE = "NO_TRADE"


@dataclass
class Trigger:
    timestamp:        str
    side:             str
    expiry_date:      date
    expiry_str:       str
    dte:              int
    signal_strength:  SignalStrength
    rsi_daily:        float
    rsi_hourly:       float
    price_pattern:    str
    spot_level:       float
    sma_20:           float
    india_vix:        float
    trend:            str
    condition:        str
    # What to do on Zerodha
    zerodha_action:   str
    zerodha_strikes:  str   # human-readable range


@dataclass
class WatchItem:
    side:          str
    rsi_daily:     float
    rsi_hourly:    float
    message:       str
    spot:          float
    india_vix:     float
    expiry_str:    str
    dte:           int
    zerodha_hint:  str


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

    MIN_DTE = 15

    BULLISH = {"dragonfly_doji", "bullish_engulfing", "hammer"}
    BEARISH = {"gravestone_doji", "bearish_engulfing", "shooting_star"}

    def run_full_scan(self) -> ScanResult:
        scan_time = datetime.now().isoformat()
        triggers  = []
        watchlist = []

        try:
            ctx      = self.get_market_context()
            rsi_d    = ctx["rsi_daily"]
            rsi_h    = ctx["rsi_hourly"]
            exp, dte = get_stonez_expiry()

            if dte < self.MIN_DTE:
                log.warning(f"Expiry too close ({dte}d). No new trades.")
                return ScanResult(scan_time=scan_time,
                                  market_context={k:v for k,v in ctx.items() if k!="spot_df"},
                                  summary=f"Expiry too close ({dte}d). Wait for next month.")

            exp_str = exp.strftime("%d%b%Y").upper()

            # CALL side
            if rsi_d <= self.RSI_OS_MODERATE or rsi_h <= self.RSI_OS_MODERATE:
                t = self._make_trigger("CALL", ctx, exp, exp_str, dte)
                if t: triggers.append(t)
            elif rsi_d <= self.RSI_OS_WATCH:
                w = self._make_watch("CALL", ctx, exp_str, dte)
                if w: watchlist.append(w)

            # PUT side
            if rsi_d >= self.RSI_OB_MODERATE or rsi_h >= self.RSI_OB_MODERATE:
                t = self._make_trigger("PUT", ctx, exp, exp_str, dte)
                if t: triggers.append(t)
            elif rsi_d >= self.RSI_OB_WATCH:
                w = self._make_watch("PUT", ctx, exp_str, dte)
                if w: watchlist.append(w)

        except Exception as e:
            log.error(f"Scan error: {e}", exc_info=True)
            return ScanResult(scan_time=scan_time, summary=f"Scan failed: {e}")

        ctx_clean = {k: v for k, v in ctx.items() if k != "spot_df"}
        return ScanResult(
            scan_time=scan_time, triggers=triggers,
            watchlist=watchlist, market_context=ctx_clean,
            summary=self._summary(triggers, watchlist, ctx),
        )

    def _make_trigger(self, side, ctx, exp, exp_str, dte) -> Optional[Trigger]:
        spot    = ctx["spot"]
        vix     = ctx["india_vix"]
        pattern = self._pattern(ctx.get("spot_df"))
        above_sma = (spot < ctx["sma_20"]) if side=="CALL" else (spot > ctx["sma_20"])
        strength  = self._score(side, ctx["rsi_daily"], ctx["rsi_hourly"], pattern, above_sma)

        if strength == SignalStrength.NO_TRADE:
            return None

        # Tell user what to look for on Zerodha — real guidance, not fake prices
        if side == "CALL":
            # OTM calls: strikes above spot
            # With VIX 22%+ and DTE ~39, the ₹70-100 range is typically 2000-3000 pts OTM
            # But this varies — we tell user to check a range
            approx_low  = round((spot + 1500) / 50) * 50
            approx_high = round((spot + 3500) / 50) * 50
            action = (
                f"Open Zerodha → NIFTY Options → {exp_str} expiry → "
                f"Calls tab → Find a CALL strike between "
                f"{int(approx_low):,} and {int(approx_high):,} "
                f"priced between ₹70 and ₹100. That is your Stonez entry."
            )
            strikes_hint = f"{int(approx_low):,}–{int(approx_high):,} CE (approx)"
        else:
            # OTM puts: strikes below spot
            approx_low  = round((spot - 3500) / 50) * 50
            approx_high = round((spot - 1500) / 50) * 50
            action = (
                f"Open Zerodha → NIFTY Options → {exp_str} expiry → "
                f"Puts tab → Find a PUT strike between "
                f"{int(approx_low):,} and {int(approx_high):,} "
                f"priced between ₹70 and ₹100. That is your Stonez entry."
            )
            strikes_hint = f"{int(approx_low):,}–{int(approx_high):,} PE (approx)"

        return Trigger(
            timestamp       = datetime.now().isoformat(),
            side            = side,
            expiry_date     = exp,
            expiry_str      = exp_str,
            dte             = dte,
            signal_strength = strength,
            rsi_daily       = round(ctx["rsi_daily"], 1),
            rsi_hourly      = round(ctx["rsi_hourly"], 1),
            price_pattern   = pattern,
            spot_level      = spot,
            sma_20          = ctx["sma_20"],
            india_vix       = round(vix, 2),
            trend           = ctx["trend"],
            condition       = ctx["condition"],
            zerodha_action  = action,
            zerodha_strikes = strikes_hint,
        )

    def _make_watch(self, side, ctx, exp_str, dte) -> Optional[WatchItem]:
        spot = ctx["spot"]
        vix  = ctx["india_vix"]
        rsi_d, rsi_h = ctx["rsi_daily"], ctx["rsi_hourly"]

        if side == "CALL":
            msg = (f"Daily RSI {rsi_d:.1f} approaching oversold (trigger ≤{self.RSI_OS_MODERATE}). "
                   f"Watch for hammer or bullish engulfing on daily chart. "
                   f"Prepare to check OTM calls on Zerodha when it triggers.")
            approx_low  = round((spot + 1500) / 50) * 50
            approx_high = round((spot + 3500) / 50) * 50
            hint = f"When triggered, check {exp_str} CALL strikes {int(approx_low):,}–{int(approx_high):,} for ₹70–100 premium"
        else:
            msg = (f"Daily RSI {rsi_d:.1f} approaching overbought (trigger ≥{self.RSI_OB_MODERATE}). "
                   f"Watch for shooting star or bearish engulfing on daily chart.")
            approx_low  = round((spot - 3500) / 50) * 50
            approx_high = round((spot - 1500) / 50) * 50
            hint = f"When triggered, check {exp_str} PUT strikes {int(approx_low):,}–{int(approx_high):,} for ₹70–100 premium"

        return WatchItem(side=side, rsi_daily=round(rsi_d,1), rsi_hourly=round(rsi_h,1),
                         message=msg, spot=spot, india_vix=round(vix,2),
                         expiry_str=exp_str, dte=dte, zerodha_hint=hint)

    def get_market_context(self) -> dict:
        spot      = get_nifty_spot()
        vix       = get_india_vix()
        daily_df  = get_nifty_ohlc("1d",  60)
        hourly_df = get_nifty_ohlc("60m", 20)

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
            "india_vix":  round(vix, 2),
            "rsi_daily":  round(rsi_d, 1),
            "rsi_hourly": round(rsi_h, 1),
            "sma_20":     round(sma_d, 1),
            "condition":  cond,
            "trend":      "bullish" if spot > sma_d else "bearish",
            "data_source": "yahoo_finance_real",
            "scan_time":  datetime.now().strftime("%d-%b-%Y %I:%M %p IST"),
            "spot_df":    daily_df,
        }

    def _rsi(self, df, p=14) -> float:
        if df is None or df.empty or len(df)<p+1: return 50.0
        d=df["close"].diff()
        g=d.clip(lower=0).ewm(com=p-1,min_periods=p).mean()
        l=(-d.clip(upper=0)).ewm(com=p-1,min_periods=p).mean()
        rs=g/l.replace(0,float("nan"))
        return float((100-100/(1+rs)).iloc[-1])

    def _sma(self, df, p=20) -> float:
        if df is None or df.empty or len(df)<p:
            return float(df["close"].mean()) if (df is not None and not df.empty) else 0.0
        return float(df["close"].tail(p).mean())

    def _pattern(self, df) -> str:
        if df is None or len(df)<3: return "none"
        r=df.iloc[-1]; p=df.iloc[-2]
        o,h,l,c=r["open"],r["high"],r["low"],r["close"]
        po,pc=p["open"],p["close"]
        b=abs(c-o); rng=h-l
        if rng==0: return "none"
        if b/rng<.10 and (h-max(o,c))/rng>.60: return "gravestone_doji"
        if b/rng<.10 and (min(o,c)-l)/rng>.60:  return "dragonfly_doji"
        if c<o and o>pc and c<po:                return "bearish_engulfing"
        if c>o and o<pc and c>po:                return "bullish_engulfing"
        if c<o and (h-o)/rng>.55 and b/rng<.30: return "shooting_star"
        if c>o and (o-l)/rng>.55 and b/rng<.30: return "hammer"
        if b/rng<.12:                             return "doji"
        return "none"

    def _score(self, side, rsi_d, rsi_h, pattern, above_sma) -> SignalStrength:
        s=0
        if side=="CALL":
            s+=3 if rsi_d<=self.RSI_OS_STRONG else (2 if rsi_d<=self.RSI_OS_MODERATE else 0)
            s+=2 if rsi_h<=self.RSI_OS_STRONG else (1 if rsi_h<=self.RSI_OS_MODERATE else 0)
            s+=2 if pattern in self.BULLISH else (1 if pattern=="doji" else 0)
        else:
            s+=3 if rsi_d>=self.RSI_OB_STRONG else (2 if rsi_d>=self.RSI_OB_MODERATE else 0)
            s+=2 if rsi_h>=self.RSI_OB_STRONG else (1 if rsi_h>=self.RSI_OB_MODERATE else 0)
            s+=2 if pattern in self.BEARISH else (1 if pattern=="doji" else 0)
        if above_sma: s+=1
        return SignalStrength.STRONG if s>=5 else (SignalStrength.MODERATE if s>=3 else SignalStrength.NO_TRADE)

    def _summary(self, triggers, watchlist, ctx) -> str:
        base=(f"NIFTY {ctx.get('spot',0):.0f} | RSI {ctx.get('rsi_daily',0)} | "
              f"VIX {ctx.get('india_vix',0)}% | {ctx.get('condition','').upper().replace('_',' ')}")
        if triggers:
            s=sum(1 for t in triggers if t.signal_strength==SignalStrength.STRONG)
            return f"{len(triggers)} trigger(s): {s} STRONG | {base}"
        if watchlist: return f"WATCHLIST: {len(watchlist)} | {base}"
        return f"No setup | {base}"

    def _above_20sma(self, df) -> bool:
        if df is None or df.empty or len(df)<2: return False
        return float(df["close"].iloc[-1])>self._sma(df)
