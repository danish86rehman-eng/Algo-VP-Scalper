
import MetaTrader5 as mt5
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta

def check_history():
    load_dotenv(".env")
    login = int(os.getenv("MT5_LOGIN", 0))
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")

    if not mt5.initialize(login=login, password=password, server=server):
        print(f"Failed to initialize MT5: {mt5.last_error()}")
        return

    # Look back 48 hours
    from_date = datetime.now() - timedelta(days=2)
    to_date = datetime.now()
    
    deals = mt5.history_deals_get(from_date, to_date)
    print(f"--- Trade History (Last 48 Hours) ---")
    if deals:
        total_pnl = 0
        for d in deals:
            if d.entry == 1: # DEAL_ENTRY_OUT
                magic = d.magic
                agent = "SA" if magic == 88880 else ("Main" if magic == 12345 else "Unknown")
                print(f"Ticket: {d.ticket} | Symbol: {d.symbol} | Agent: {agent} | PnL: ${d.profit:.2f} | Magic: {magic}")
                total_pnl += d.profit
        print(f"\nTotal PnL in History: ${total_pnl:.2f}")
    else:
        print("No deals found in history.")

    mt5.shutdown()

if __name__ == "__main__":
    check_history()
