
import MetaTrader5 as mt5
import os
from dotenv import load_dotenv

def check_status():
    load_dotenv(".env")
    login = int(os.getenv("MT5_LOGIN", 0))
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")

    if not mt5.initialize(login=login, password=password, server=server):
        print(f"Failed to initialize MT5: {mt5.last_error()}")
        return

    acct = mt5.account_info()
    if acct:
        print(f"--- Account Status ---")
        print(f"Balance: ${acct.balance:.2f} | Equity: ${acct.equity:.2f} | Profit: ${acct.profit:.2f}")
    
    positions = mt5.positions_get()
    print(f"\n--- All Open Positions ---")
    if positions:
        for p in positions:
            agent = "MAIN" if p.magic == 12345 or p.magic == 20260426 else ("SA" if p.magic == 88880 else ("TGA" if p.magic == 99990 else "OTHER"))
            print(f"Ticket: {p.ticket} | {p.symbol} | {p.type} | Magic: {p.magic} ({agent}) | Profit: ${p.profit:.2f}")
    else:
        print("No open positions.")

    mt5.shutdown()

if __name__ == "__main__":
    check_status()
