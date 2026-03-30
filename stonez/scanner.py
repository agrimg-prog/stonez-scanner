"""
scanner.py — Stonez signal scanner (fixed).

Trading logic (faithful to the video transcript):
  Strategy:   COUNTER-TREND at RSI extremes
  ─────────────────────────────────────────────────────
  CALL setup: Market has been falling (spot < 20 SMA).
              RSI is oversold. Wait for bullish price pattern.
              Buy a monthly OTM CE with premium ₹70–100.

  PUT setup:  Market has been rising (spot > 20 SMA).
              RSI is overbought. Wait for bearish price pattern.
              Buy a monthly OTM PE with premium ₹70–100.

  Expiry rule: Before 10th → can use current month.
               After 10th  → must use next month.

  SL:      ~32 pts below entry price (≈ 30–35 from transcript)
  Target:  2× entry price
  Lots:    Max 1 lot (75 shares). Max 30–35% of capital.
  Frequency: 1–2 trades per month MAX. Not daily.
"""

import logging
from datetime import datetime, date
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import pandas as pd

from stonez.market_data import (
    get_nifty_spot, get_india_vix, get_nifty_ohlc,
    get_stonez_expiry, find_stonez_strikes, bs_price,
)

log = logging.getLogger(__name__)

# ── Constants (from transcript) ───────────────────────────────────────────────
PREMIUM_MIN  = 65.0    # ₹65 lower bound (transcript says 70 ± 5)
PREMIUM_MAX  = 105.0   # ₹105 upper bound
SL_POINTS    = 32.0    # ~30–35 pts from transcript
LOT_SIZE     = 75


class SignalStrength(str, Enum):
    STRONG   = "STRONG"
    MODERATE = "MODERATE"
    NO_TRADE = "NO_TRADE"


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class Trigger:
    # Core identity
    timestamp:         str
    side:              str           # "CALL" | "PUT"

    # Expiry
    expiry_date:       date
    expiry_str:        str
    dte:               int

    # Signal quality
    signal_strength:   SignalStrength
    condition:         str
    trend:             str

    # Market context
    spot_level:        float
    rsi_daily:         float
    rsi_hourly:        float
    price_pattern:     str
    sma_20:            float
    india_vix:         float

    # ★ Option details (now baked in)
    best_strike:       int           # best matching strike
    estimated_premium: float         # BS-estimated LTP for that strike
    sl_price:          float         # entry − 32 pts
    target_price:      float         # entry × 2
    symbol:            str           # e.g. NIFTY27MAR202524500PE

    # Zerodha guidance
    zerodha_action:    str
    zerodha_strikes:   str           # backup range if BS estimate is off


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


# ── Scanner ───────────────────────────────────────────────────────────────────

class StonezScanner:

    # RSI thresholds (per transcript: overbought ~65+, oversold ~35-)
    RSI_OS_STRONG   = 28
    RSI_OS_MODERATE = 35
    RSI_OS_WATCH    = 42

    RSI_OB_STRONG   = 72
    RSI_OB_MODERATE = 65
    RSI_OB_WATCH    = 60

    MIN_DTE = 12   # don't enter if fewer days to expiry

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
                msg = (f"Expiry too close ({dte} days). "
                       f"No new Stonez trades — wait for next month's setup.")
                log.warning(msg)
                return ScanResult(
                    scan_time=scan_time,
                    market_context={k: v for k, v in ctx.items() if k != "spot_df"},
                    summary=msg,
                )

            exp_str = exp.strftime("%d%b%Y").upper()

            # ── CALL side: oversold → counter-trend buy ───────────────────────
            if rsi_d <= self.RSI_OS_MODERATE or rsi_h <= self.RSI_OS_MODERATE:
                t = self._make_trigger("CALL", ctx, exp, exp_str, dte)
                if t:
                    triggers.append(t)
            elif rsi_d <= self.RSI_OS_WATCH:
                w = self._make_watch("CALL", ctx, exp_str, dte)
                if w:
                    watchlist.append(w)

            # ── PUT side: overbought → counter-trend buy ──────────────────────
            if rsi_d >= self.RSI_OB_MODERATE or rsi_h >= self.RSI_OB_MODERATE:
                t = self._make_trigger("PUT", ctx, exp, exp_str, dte)
                if t:
                    triggers.append(t)
            elif rsi_d >= self.RSI_OB_WATCH:
                w = self._make_watch("PUT", ctx, exp_str, dte)
                if w:
                    watchlist.append(w)

        except Exception as e:
            log.error(f"Scan error: {e}", exc_info=True)
            return ScanResult(scan_time=scan_time, summary=f"Scan failed: {e}")

        ctx_clean = {k: v for k, v in ctx.items() if k != "spot_df"}
        return ScanResult(
            scan_time=scan_time,
            triggers=triggers,
            watchlist=watchlist,
            market_context=ctx_clean,
            summary=self._summary(triggers, watchlist, ctx),
        )

    # ── Trigger builder ───────────────────────────────────────────────────────

    def _make_trigger(self, side, ctx, exp, exp_str, dte) -> Optional[Trigger]:
        spot    = ctx["spot"]
        vix     = ctx["india_vix"]
        pattern = self._pattern(ctx.get("spot_df"))

        # Counter-trend context check (transcript rule):
        # CALL: spot should be below 20 SMA (market has been falling)
        # PUT:  spot should be above 20 SMA (market has been rising)
        in_counter_trend = (
            (side == "CALL" and spot <= ctx["sma_20"]) or
            (side == "PUT"  and spot >= ctx["sma_20"])
        )

        strength = self._score(
            side, ctx["rsi_daily"], ctx["rsi_hourly"], pattern, in_counter_trend
        )

        if strength == SignalStrength.NO_TRADE:
            log.info(f"{side} scored NO_TRADE — skipping.")
            return None

        # ★ Find actual strike with premium in ₹65–105 range
        opt_type = "CE" if side == "CALL" else "PE"
        strikes  = find_stonez_strikes(spot, vix, dte, side, PREMIUM_MIN, PREMIUM_MAX)

        if not strikes:
            log.warning(f"No {side} strike found in ₹{PREMIUM_MIN}–{PREMIUM_MAX} range. "
                        f"Spot={spot}, VIX={vix}%, DTE={dte}")
            # Fall back to guidance without specific price
            estimated_premium = 85.0   # midpoint estimate
            best_strike       = (
                round((spot + 2500) / 50) * 50 if side == "CALL"
                else round((spot - 2500) / 50) * 50
            )
        else:
            best           = strikes[0]
            best_strike    = int(best["strike"])
            estimated_premium = best["estimated_premium"]

        # SL and target per transcript
        sl_price     = round(estimated_premium - SL_POINTS, 1)
        target_price = round(estimated_premium * 2.0, 1)

        # Symbol string
        symbol = f"NIFTY{exp_str}{best_strike}{opt_type}"

        # Zerodha guidance — now anchored on the estimated strike
        approx_lo = round((spot + 1500) / 50) * 50 if side == "CALL" else round((spot - 3500) / 50) * 50
        approx_hi = round((spot + 3500) / 50) * 50 if side == "CALL" else round((spot - 1500) / 50) * 50

        action = (
            f"Open Zerodha → NIFTY Options → <b>{exp_str}</b> expiry → "
            f"{'Calls' if side=='CALL' else 'Puts'} tab.\n"
            f"▶ Look near strike <b>{best_strike:,}</b> (est. ₹{estimated_premium:.0f}).\n"
            f"▶ Accept if LTP is ₹70–100. Enter 1 lot (75 qty).\n"
            f"▶ Set SL at ₹{sl_price:.0f} | Target ₹{target_price:.0f}."
        )

        return Trigger(
            timestamp         = datetime.now().isoformat(),
            side              = side,
            expiry_date       = exp,
            expiry_str        = exp_str,
            dte               = dte,
            signal_strength   = strength,
            condition         = ctx["condition"],
            trend             = ctx["trend"],
            spot_level        = spot,
            rsi_daily         = round(ctx["rsi_daily"], 1),
            rsi_hourly        = round(ctx["rsi_hourly"], 1),
            price_pattern     = pattern,
            sma_20            = ctx["sma_20"],
            india_vix         = round(vix, 2),
            best_strike       = best_strike,
            estimated_premium = estimated_premium,
            sl_price          = sl_price,
            target_price      = target_price,
            symbol            = symbol,
            zerodha_action    = action,
            zerodha_strikes   = f"{approx_lo:,}–{approx_hi:,} {opt_type} (backup range)",
        )

    # ── Watchlist builder ─────────────────────────────────────────────────────

    def _make_watch(self, side, ctx, exp_str, dte) -> Optional[WatchItem]:
        spot = ctx["spot"]
        vix  = ctx["india_vix"]
        rsi_d, rsi_h = ctx["rsi_daily"], ctx["rsi_hourly"]

        if side == "CALL":
            msg  = (f"Daily RSI {rsi_d:.1f} approaching oversold zone "
                    f"(trigger ≤ {self.RSI_OS_MODERATE}). "
                    f"Watch for hammer / bullish engulfing on daily chart. "
                    f"Stay patient — do NOT enter yet.")
            lo   = round((spot + 1500) / 50) * 50
            hi   = round((spot + 3500) / 50) * 50
            hint = (f"When RSI hits {self.RSI_OS_MODERATE}, check {exp_str} CE "
                    f"strikes {lo:,}–{hi:,} for ₹70–100 premium.")
        else:
            msg  = (f"Daily RSI {rsi_d:.1f} approaching overbought zone "
                    f"(trigger ≥ {self.RSI_OB_MODERATE}). "
                    f"Watch for shooting star / bearish engulfing on daily chart. "
                    f"Stay patient — do NOT enter yet.")
            lo   = round((spot - 3500) / 50) * 50
            hi   = round((spot - 1500) / 50) * 50
            hint = (f"When RSI hits {self.RSI_OB_MODERATE}, check {exp_str} PE "
                    f"strikes {lo:,}–{hi:,} for ₹70–100 premium.")

        return WatchItem(
            side=side,
            rsi_daily=round(rsi_d, 1),
            rsi_hourly=round(rsi_h, 1),
            message=msg,
            spot=spot,
            india_vix=round(vix, 2),
            expiry_str=exp_str,
            dte=dte,
            zerodha_hint=hint,
        )

    # ── Market context ────────────────────────────────────────────────────────

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
            "spot":        round(spot, 1),
            "india_vix":   round(vix, 2),
            "rsi_daily":   round(rsi_d, 1),
            "rsi_hourly":  round(rsi_h, 1),
            "sma_20":      round(sma_d, 1),
            "condition":   cond,
            "trend":       "bullish" if spot > sma_d else "bearish",
            "data_source": "yahoo_finance_real",
            "scan_time":   datetime.now().strftime("%d-%b-%Y %I:%M %p IST"),
            "spot_df":     daily_df,
        }

    # ── Technical indicators ──────────────────────────────────────────────────

    def _rsi(self, df, p=14) -> float:
        if df is None or df.empty or len(df) < p + 1:
            return 50.0
        d = df["close"].diff()
        g = d.clip(lower=0).ewm(com=p - 1, min_periods=p).mean()
        l = (-d.clip(upper=0)).ewm(com=p - 1, min_periods=p).mean()
        rs = g / l.replace(0, float("nan"))
        val = float((100 - 100 / (1 + rs)).iloc[-1])
        return round(val, 2) if not pd.isna(val) else 50.0

    def _sma(self, df, p=20) -> float:
        if df is None or df.empty:
            return 0.0
        if len(df) < p:
            return float(df["close"].mean())
        return float(df["close"].tail(p).mean())

    def _pattern(self, df) -> str:
        if df is None or len(df) < 3:
            return "none"
        r = df.iloc[-1]
        p = df.iloc[-2]
        o, h, l, c = r["open"], r["high"], r["low"], r["close"]
        po, pc = p["open"], p["close"]
        b   = abs(c - o)
        rng = h - l
        if rng == 0:
            return "none"
        if b / rng < 0.10 and (h - max(o, c)) / rng > 0.60:
            return "gravestone_doji"
        if b / rng < 0.10 and (min(o, c) - l) / rng > 0.60:
            return "dragonfly_doji"
        if c < o and o > pc and c < po:
            return "bearish_engulfing"
        if c > o and o < pc and c > po:
            return "bullish_engulfing"
        if c < o and (h - o) / rng > 0.55 and b / rng < 0.30:
            return "shooting_star"
        if c > o and (o - l) / rng > 0.55 and b / rng < 0.30:
            return "hammer"
        if b / rng < 0.12:
            return "doji"
        return "none"

    def _score(self, side: str, rsi_d: float, rsi_h: float,
               pattern: str, in_counter_trend: bool) -> SignalStrength:
        """
        Score the signal. Transcript rules:
          - RSI at extreme (OS for CALL, OB for PUT): 3 pts strong, 2 pts moderate
          - Hourly RSI confirmation: 2 pts strong, 1 pt moderate
          - Price action pattern: 2 pts bullish/bearish, 1 pt doji
          - Counter-trend context (spot on right side of 20 SMA): 1 pt

          STRONG = 5+  |  MODERATE = 3–4  |  NO_TRADE = <3
        """
        s = 0
        if side == "CALL":
            s += 3 if rsi_d <= self.RSI_OS_STRONG   else (2 if rsi_d <= self.RSI_OS_MODERATE else 0)
            s += 2 if rsi_h <= self.RSI_OS_STRONG   else (1 if rsi_h <= self.RSI_OS_MODERATE else 0)
            s += 2 if pattern in self.BULLISH else (1 if pattern == "doji" else 0)
        else:
            s += 3 if rsi_d >= self.RSI_OB_STRONG   else (2 if rsi_d >= self.RSI_OB_MODERATE else 0)
            s += 2 if rsi_h >= self.RSI_OB_STRONG   else (1 if rsi_h >= self.RSI_OB_MODERATE else 0)
            s += 2 if pattern in self.BEARISH else (1 if pattern == "doji" else 0)

        if in_counter_trend:
            s += 1

        return (SignalStrength.STRONG   if s >= 5 else
                SignalStrength.MODERATE if s >= 3 else
                SignalStrength.NO_TRADE)

    def _summary(self, triggers, watchlist, ctx) -> str:
        base = (
            f"NIFTY {ctx.get('spot', 0):,.0f} | "
            f"Daily RSI {ctx.get('rsi_daily', 0)} | "
            f"VIX {ctx.get('india_vix', 0)}% | "
            f"{ctx.get('condition', '').upper().replace('_', ' ')}"
        )
        if triggers:
            strong = sum(1 for t in triggers if t.signal_strength == SignalStrength.STRONG)
            return f"{len(triggers)} TRIGGER(S) | {strong} STRONG | {base}"
        if watchlist:
            return f"WATCHLIST: {len(watchlist)} | {base}"
        return f"No setup | {base}"
