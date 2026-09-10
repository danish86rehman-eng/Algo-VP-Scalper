import MetaTrader5 as mt5
import os
from dotenv import load_dotenv

def main():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(env_path)
    login = int(os.getenv("MT5_LOGIN", "0"))
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")
    
    if not mt5.initialize(login=login, password=password, server=server):
        print("MT5 init failed")
        return

    rates = mt5.copy_rates_from_pos('XAUUSD', mt5.TIMEFRAME_D1, 0, 1)
    if rates is not None and len(rates) > 0:
        print(f"High: {rates[0]['high']}")
        print(f"Low: {rates[0]['low']}")
    else:
        print("Could not retrieve rates.")
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
