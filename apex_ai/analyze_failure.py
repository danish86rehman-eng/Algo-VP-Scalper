
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
import os
from dotenv import load_dotenv

def analyze_failure(symbol="XAGUSD"):
    load_dotenv(".env")
    mt5.initialize(login=int(os.getenv("MT5_LOGIN")), password=os.getenv("MT5_PASSWORD"), server=os.getenv("MT5_SERVER"))
    
    # Trade Window: 07:00 to 07:40 UTC
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=2)
    
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start_time, end_time)
    if rates is None:
        print("Failed to get rates")
        return
        
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    
    print(f"--- Analysis for {symbol} ---")
    
    # Check for trend on M15
    rates_m15 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 20)
    df15 = pd.DataFrame(rates_m15)
    m15_ema = df15['close'].rolling(10).mean().iloc[-1]
    curr_price = df15['close'].iloc[-1]
    bias = "BULLISH" if curr_price > m15_ema else "BEARISH"
    
    # Check volatility
    ranges = df['high'] - df['low']
    avg_range = ranges.mean()
    
    # Look at specific period 07:05 - 07:35 UTC
    period_df = df[(df['time'] >= '2026-04-28 07:00:00') & (df['time'] <= '2026-04-28 07:40:00')]
    
    print(f"M15 Bias: {bias} (Price: {curr_price:.3f}, EMA: {m15_ema:.3f})")
    print(f"Avg M5 Range: {avg_range:.4f}")
    
    print("\nRecent Price Action (M5):")
    for i, row in period_df.iterrows():
        print(f"{row['time'].strftime('%H:%M')} | O:{row['open']:.3f} H:{row['high']:.3f} L:{row['low']:.3f} C:{row['close']:.3f} | Vol:{row['tick_volume']}")

    # Identify the 'failure' - price drop
    max_high = period_df['high'].max()
    min_low = period_df['low'].min()
    drop = max_high - min_low
    print(f"\nMax High in window: {max_high:.3f}")
    print(f"Min Low in window: {min_low:.3f}")
    print(f"Total Drop: {drop:.3f}")

    mt5.shutdown()

if __name__ == "__main__":
    analyze_failure()
