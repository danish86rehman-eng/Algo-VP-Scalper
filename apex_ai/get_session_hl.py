import MetaTrader5 as mt5
import os
import pandas as pd
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

def get_session_hl(df, start_hour, end_hour):
    if start_hour > end_hour:
        mask = (df.index.hour >= start_hour) | (df.index.hour < end_hour)
    else:
        mask = (df.index.hour >= start_hour) & (df.index.hour < end_hour)
    
    session_df = df[mask]
    if session_df.empty:
        return "N/A", "N/A"
    return session_df['high'].max(), session_df['low'].min()

def main():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(env_path)
    login = int(os.getenv("MT5_LOGIN", "0"))
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")
    
    if not mt5.initialize(login=login, password=password, server=server):
        print("MT5 init failed")
        return

    now = datetime.now(timezone.utc)
    rates = mt5.copy_rates_from('XAUUSD', mt5.TIMEFRAME_M15, now, 100)
    if rates is None or len(rates) == 0:
        print("Could not retrieve rates.")
        mt5.shutdown()
        return

    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df.set_index('time', inplace=True)
    
    last_24h = now - timedelta(hours=24)
    df = df[df.index >= last_24h]

    asia_h, asia_l = get_session_hl(df, 22, 8)
    lon_h, lon_l = get_session_hl(df, 7, 16)
    ny_h, ny_l = get_session_hl(df, 12, 21)
    
    lon_kz_h, lon_kz_l = get_session_hl(df, 7, 10)
    ny_kz_h, ny_kz_l = get_session_hl(df, 12, 15)
    
    print("=== MAJOR SESSIONS (Last 24h UTC) ===")
    print(f"Asian Session  (22:00-08:00) | High: {asia_h} | Low: {asia_l}")
    print(f"London Session (07:00-16:00) | High: {lon_h} | Low: {lon_l}")
    print(f"NY Session     (12:00-21:00) | High: {ny_h} | Low: {ny_l}")
    print("")
    print("=== APEX KILLZONES ===")
    print(f"London KZ      (07:00-10:00) | High: {lon_kz_h} | Low: {lon_kz_l}")
    print(f"NY KZ          (12:00-15:00) | High: {ny_kz_h} | Low: {ny_kz_l}")

    mt5.shutdown()

if __name__ == "__main__":
    main()
