import os
import random
import MetaTrader5 as mt5
from dotenv import load_dotenv

def main():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(env_path)
    login = int(os.getenv("MT5_LOGIN", "0"))
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")
    
    if not mt5.initialize(login=login, password=password, server=server):
        print(f"MT5 initialize failed, error code = {mt5.last_error()}")
        return

    symbols = ["XAUUSD", "USOIL", "XAGUSD"]
    symbol = random.choice(symbols)
    mt5.symbol_select(symbol, True)
    
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if not tick or not info:
        print(f"Could not get info for {symbol}")
        mt5.shutdown()
        return

    direction = random.choice([mt5.ORDER_TYPE_BUY, mt5.ORDER_TYPE_SELL])
    price = tick.ask if direction == mt5.ORDER_TYPE_BUY else tick.bid
    
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": info.volume_min,
        "type": direction,
        "price": price,
        "deviation": 20,
        "magic": 12345,
        "comment": "Random trade",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        direction_str = "BUY" if direction == mt5.ORDER_TYPE_BUY else "SELL"
        print(f"Success! Random trade placed: {direction_str} {info.volume_min} {symbol} at {price}. Ticket: {result.order}")
    else:
        print(f"Order failed, retcode={result.retcode if result else 'None'}")
        if result:
            print(result.comment)
        
    mt5.shutdown()

if __name__ == "__main__":
    main()
