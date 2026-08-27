
import MetaTrader5 as mt5
import os
from dotenv import load_dotenv
from datetime import datetime, time, timedelta

def get_pnl_statement():
    load_dotenv(".env")
    login = int(os.getenv("MT5_LOGIN", 0))
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")

    if not mt5.initialize(login=login, password=password, server=server):
        print(f"Failed to initialize MT5: {mt5.last_error()}")
        return

    acct = mt5.account_info()
    if not acct:
        print("Failed to get account info")
        return

    # Look back periods
    now = datetime.now()
    today_start = datetime.combine(now.date(), time.min)
    yesterday_start = today_start - timedelta(days=1)
    
    # Get all deals from yesterday start to now
    deals = mt5.history_deals_get(yesterday_start, now)
    
    print("====================================================")
    print("           APEX AI TRADING P&L STATEMENT           ")
    print(f"           Report Generated: {now.strftime('%Y-%m-%d %H:%M:%S')}         ")
    print("====================================================")
    print(f"Account Login  : {acct.login}")
    print(f"Current Balance: ${acct.balance:,.2f}")
    print(f"Current Equity : ${acct.equity:,.2f}")
    print(f"Unrealized PnL : ${acct.profit:,.2f}")
    print("----------------------------------------------------")
    
    stats = {
        "TODAY": {"total": 0.0, "trades": 0, "win": 0, "loss": 0},
        "YESTERDAY": {"total": 0.0, "trades": 0, "win": 0, "loss": 0},
        "BY_AGENT": {
            "MAIN": 0.0,
            "SA": 0.0,
            "TGA": 0.0,
            "CHA": 0.0,
            "UNKNOWN": 0.0
        }
    }

    if deals:
        for d in deals:
            if d.entry == 1: # DEAL_ENTRY_OUT
                pnl = d.profit + d.commission + d.swap
                d_time = datetime.fromtimestamp(d.time)
                
                # Assign to period
                period = "TODAY" if d_time >= today_start else "YESTERDAY"
                stats[period]["total"] += pnl
                stats[period]["trades"] += 1
                if pnl > 0: stats[period]["win"] += 1
                else: stats[period]["loss"] += 1
                
                # Assign to agent
                magic = d.magic
                if magic == 12345: agent = "MAIN"
                elif magic == 88880: agent = "SA"
                elif magic == 99990: agent = "TGA"
                elif magic == 77770: agent = "CHA"
                elif magic == 20260426: agent = "MAIN" # Silver logic
                else: agent = "UNKNOWN"
                
                stats["BY_AGENT"][agent] += pnl

    print("PERIOD PERFORMANCE:")
    for period in ["TODAY", "YESTERDAY"]:
        s = stats[period]
        win_rate = (s["win"] / s["trades"] * 100) if s["trades"] > 0 else 0
        print(f"  {period:<10}: ${s['total']:>8.2f} | Trades: {s['trades']} | Win Rate: {win_rate:.0f}%")
    
    print("\nAGENT PERFORMANCE (LAST 48H):")
    for agent, pnl in stats["BY_AGENT"].items():
        if pnl != 0:
            print(f"  {agent:<10}: ${pnl:>8.2f}")
            
    print("====================================================")
    
    mt5.shutdown()

if __name__ == "__main__":
    get_pnl_statement()
