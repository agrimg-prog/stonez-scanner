import os,sys,logging,requests
from datetime import datetime,date
from stonez.trade_state import load_state,set_closed,save_state,clear_state
from stonez.notifier import send_telegram
from stonez.market_data import get_nifty_spot
from dotenv import load_dotenv
load_dotenv()
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log=logging.getLogger(__name__)
def est_option(state,spot)->float:
    e=state.spot_at_entry or spot; mv=(spot-e)/e; ml=3.5 if state.side=="CALL" else -3.5
    try: dy=(date.today()-datetime.fromisoformat(state.entered_at).date()).days
    except: dy=0
    return max(0.5,round(state.entry_price*(1+ml*mv)*max(0.5,1-dy*0.015),1))
def check():
    s=load_state(); now=datetime.now().strftime("%d-%b-%Y %I:%M %p IST")
    log.info(f"SL Monitor | {s.status} | {s.symbol or 'none'}")
    if s.status=="NONE": log.info("No active trade."); return
    if s.status=="WATCHING":
        send_telegram(f"👀 <b>Watchlist Reminder</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                      f"<b>Symbol:</b> <code>{s.symbol}</code>\n"
                      f"<b>Est. entry:</b> ₹{s.entry_price}\n"
                      f"<b>SL:</b> ₹{s.sl_price}  |  <b>Target:</b> ₹{s.target_price}\n"
                      f"━━━━━━━━━━━━━━━━━━━━\n"
                      f"Check actual premium on Zerodha. Not entered yet.\n<i>{now}</i>"); return
    if s.status!="ACTIVE": return
    spot=get_nifty_spot()
    if not spot:
        send_telegram(f"⚠️ Cannot fetch NIFTY spot.\nCheck manually. SL: ₹{s.sl_price} | Target: ₹{s.target_price}"); return
    price=est_option(s,spot); en,sl,tgt=s.entry_price,s.sl_price,s.target_price
    gain=round((price-en)/en*100,1)
    log.info(f"Spot:{spot:.0f} EstOpt:₹{price} Entry:₹{en} SL:₹{sl} Tgt:₹{tgt}")
    if price<=sl:
        set_closed(s,price,"SL_HIT")
        send_telegram(f"🔴 <b>SL HIT (estimated)</b>\n{s.symbol}\nEntry ₹{en} → Est. exit ₹{price}\n"
                      f"Est. loss: ₹{abs((price-en)*75):,.0f}\n"
                      f"⚠️ Check actual price on Zerodha and exit.\n<i>{now}</i>")
        clear_state(); return
    if price>=tgt:
        set_closed(s,price,"TARGET_HIT")
        send_telegram(f"🟢 <b>TARGET HIT (estimated)</b>\n{s.symbol}\nEntry ₹{en} → Est. exit ₹{price}\n"
                      f"Est. profit: ₹{((price-en)*75):,.0f}\nBook 50%, trail rest.\n<i>{now}</i>")
        clear_state(); return
    if price>=en*1.5:
        new_sl=round(en*1.15,1)
        if new_sl>s.sl_price:
            s.sl_price=new_sl; save_state(s)
            send_telegram(f"📈 <b>Trail SL Updated</b>\n{s.symbol}\nEst. price ₹{price} ({gain:+.1f}%) | New SL: ₹{new_sl}\n<i>{now}</i>"); return
    s.last_checked=datetime.now().isoformat(); save_state(s)
    icon="📈" if price>en else "📉"
    send_telegram(f"{icon} <b>Position Update</b>\n<b>{s.symbol}</b>\n"
                  f"Est. option price: ₹{price} ({gain:+.1f}%)\n"
                  f"Entry ₹{en} | SL ₹{sl} | Target ₹{tgt}\n"
                  f"Est. P&L: ₹{round((price-en)*75):,.0f}\n"
                  f"<i>NIFTY {spot:.0f} | {now}</i>\n"
                  f"⚠️ Est. only — check Zerodha for actual price.")
if __name__=="__main__": check()
