import MetaTrader5 as mt5
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta

def export_history():
    load_dotenv(".env")
    login = int(os.getenv("MT5_LOGIN", 0))
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")

    if not mt5.initialize(login=login, password=password, server=server):
        print(f"Failed to initialize MT5: {mt5.last_error()}")
        return

    now = datetime.now()
    start_time = now - timedelta(days=7) # Last 7 days
    
    deals = mt5.history_deals_get(start_time, now)
    
    if not deals:
        print("No deals found.")
        mt5.shutdown()
        return

    print("### Trade History (Last 7 Days)")
    print("| Ticket | Time | Symbol | Type | Volume | Price | Profit | Agent |")
    print("|--------|------|--------|------|--------|-------|--------|-------|")
    
    # Sort deals by time descending
    sorted_deals = sorted([d for d in deals if d.entry == 1], key=lambda x: x.time, reverse=True)
    
    for d in sorted_deals: # DEAL_ENTRY_OUT
        d_time = datetime.fromtimestamp(d.time).strftime('%Y-%m-%d %H:%M:%S')
        magic = d.magic
        if magic == 12345: agent = "MAIN"
        elif magic == 88880: agent = "SA"
        elif magic == 99990: agent = "TGA"
        elif magic == 77770: agent = "CHA"
        elif magic == 20260426: agent = "MAIN"
        else: agent = "UNKNOWN"
        
        deal_type = "SELL" if d.type == 1 else ("BUY" if d.type == 0 else "OTHER")
        
        profit = d.profit + d.commission + d.swap
        
        print(f"| {d.ticket} | {d_time} | {d.symbol} | {deal_type} | {d.volume} | {d.price} | ${profit:.2f} | {agent} |")
        
    mt5.shutdown()

if __name__ == "__main__":
    export_history()
