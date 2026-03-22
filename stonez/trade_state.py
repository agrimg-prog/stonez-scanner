import json,logging
from datetime import datetime
from dataclasses import dataclass,asdict
from pathlib import Path
log=logging.getLogger(__name__)
STATE_FILE=Path(__file__).parent.parent/"trade_state.json"
FIELDS=["status","side","symbol","strike","expiry","entry_price","sl_price","target_price",
        "spot_at_entry","rsi_at_entry","pattern","entered_at","last_checked",
        "exit_price","exit_reason","exited_at","pnl_pts","pnl_rs"]
@dataclass
class TradeState:
    status:str="NONE";side:str="";symbol:str="";strike:float=0.0;expiry:str=""
    entry_price:float=0.0;sl_price:float=0.0;target_price:float=0.0
    spot_at_entry:float=0.0;rsi_at_entry:float=0.0;pattern:str=""
    entered_at:str="";last_checked:str=""
    exit_price:float=0.0;exit_reason:str="";exited_at:str=""
    pnl_pts:float=0.0;pnl_rs:float=0.0
def load_state()->TradeState:
    if not STATE_FILE.exists(): return TradeState()
    try:
        with open(STATE_FILE) as f: d=json.load(f)
        return TradeState(**{k:v for k,v in d.items() if k in FIELDS})
    except Exception as e: log.warning(f"Load: {e}"); return TradeState()
def save_state(s:TradeState):
    try:
        with open(STATE_FILE,"w") as f: json.dump(asdict(s),f,indent=2)
    except Exception as e: log.error(f"Save: {e}")
def set_watching(t)->TradeState:
    s=TradeState(status="WATCHING",side=t.side,symbol=t.symbol,strike=t.strike,
                 expiry=t.expiry,entry_price=t.estimated_premium,sl_price=t.sl_price,
                 target_price=t.target_price,spot_at_entry=t.spot_level,
                 rsi_at_entry=t.rsi_daily,pattern=t.price_pattern,
                 entered_at=datetime.now().isoformat(),last_checked=datetime.now().isoformat())
    save_state(s); return s
def set_closed(s:TradeState,ep:float,r:str)->TradeState:
    s.status=r;s.exit_price=ep;s.exit_reason=r;s.exited_at=datetime.now().isoformat()
    s.pnl_pts=round(ep-s.entry_price,1);s.pnl_rs=round(s.pnl_pts*75,0)
    save_state(s); return s
def clear_state(): save_state(TradeState())
