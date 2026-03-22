"""
trade_state.py — persists active trade to trade_state.json in the repo.
"""

import json, logging
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional
from pathlib import Path

log = logging.getLogger(__name__)
STATE_FILE = Path(__file__).parent.parent / "trade_state.json"


@dataclass
class TradeState:
    status:        str   = "NONE"
    side:          str   = ""
    symbol:        str   = ""
    strike:        float = 0.0
    expiry:        str   = ""
    entry_price:   float = 0.0
    sl_price:      float = 0.0
    target_price:  float = 0.0
    spot_at_entry: float = 0.0
    rsi_at_entry:  float = 0.0
    pattern:       str   = ""
    entered_at:    str   = ""
    last_checked:  str   = ""
    exit_price:    float = 0.0
    exit_reason:   str   = ""
    exited_at:     str   = ""
    pnl_pts:       float = 0.0
    pnl_rs:        float = 0.0


def load_state() -> TradeState:
    if not STATE_FILE.exists():
        return TradeState()
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        return TradeState(**{k: v for k, v in data.items() if k in TradeState.__dataclass_fields__})
    except Exception as e:
        log.warning(f"Could not load trade state: {e}")
        return TradeState()


def save_state(state: TradeState):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(asdict(state), f, indent=2)
        log.info(f"Trade state saved: {state.status} | {state.symbol}")
    except Exception as e:
        log.error(f"Could not save trade state: {e}")


def set_watching(trigger) -> TradeState:
    state = TradeState(
        status        = "WATCHING",
        side          = trigger.side,
        symbol        = trigger.symbol,
        strike        = trigger.strike,
        expiry        = trigger.expiry,
        entry_price   = trigger.entry_price,
        sl_price      = trigger.sl_price,
        target_price  = trigger.target_price,
        spot_at_entry = trigger.spot_level,
        rsi_at_entry  = trigger.rsi_daily,
        pattern       = trigger.price_pattern,
        entered_at    = datetime.now().isoformat(),
        last_checked  = datetime.now().isoformat(),
    )
    save_state(state)
    return state


def set_closed(state: TradeState, exit_price: float, reason: str) -> TradeState:
    state.status     = "CLOSED"
    state.exit_price = exit_price
    state.exit_reason= reason
    state.exited_at  = datetime.now().isoformat()
    state.pnl_pts    = round(exit_price - state.entry_price, 1)
    state.pnl_rs     = round(state.pnl_pts * 75, 0)
    save_state(state)
    return state


def clear_state():
    save_state(TradeState())
