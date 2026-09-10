import os
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timezone
from dotenv import load_dotenv
from core.structure_engine import StructureEngine
from core.liquidity_engine import LiquidityEngine

# Load credentials
env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(env_path)

login = int(os.getenv("MT5_LOGIN", "0"))
password = os.getenv("MT5_PASSWORD", "")
server = os.getenv("MT5_SERVER", "")

if not mt5.initialize(login=login, password=password, server=server):
    print(f"Failed to initialize MT5: {mt5.last_error()}")
    quit()

# 1. Get open positions
positions = mt5.positions_get()
if not positions:
    print("No open positions found.")
    mt5.shutdown()
    quit()

# 2. Engines setup
struct_eng = StructureEngine(swing_lookback=5)
liq_eng = LiquidityEngine(swing_lookback=5)

for pos in positions:
    symbol = pos.symbol
    print(f"\nAnalyzing structure for {symbol} (Position #{pos.ticket})...")
    
    # Fetch data
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 200)
    if rates is None or len(rates) == 0:
        print(f"  Failed to fetch data for {symbol}")
        continue
    
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    
    # Analyze Structure & Liquidity
    struct = struct_eng.analyze(df, symbol, "H1")
    lmap = liq_eng.analyze(df, symbol, "H1")
    
    # Calculate SL/TP based on structure
    # For BUY: SL below recent HL or LL, TP at nearest BSL
    # For SELL: SL above recent LH or HH, TP at nearest SSL
    
    sl = pos.sl
    tp = pos.tp
    
    if pos.type == mt5.ORDER_TYPE_BUY:
        # SL below nearest SSL or HL
        potential_sl = []
        if struct.recent_hl: potential_sl.append(struct.recent_hl)
        if struct.recent_ll: potential_sl.append(struct.recent_ll)
        if lmap.nearest_ssl: potential_sl.append(lmap.nearest_ssl.level)
        
        if potential_sl:
            # Pick the most conservative (lowest) but close to current price
            # Usually ICT style SL is below the 'swing' that started the move
            sl = min(potential_sl)
            # Add a small buffer (ATR-like or 0.1%)
            sl -= (pos.price_open * 0.001)
            
        # TP at nearest BSL
        if lmap.nearest_bsl:
            tp = lmap.nearest_bsl.level
        elif struct.recent_hh:
            tp = struct.recent_hh
            
    elif pos.type == mt5.ORDER_TYPE_SELL:
        # SL above nearest BSL or LH
        potential_sl = []
        if struct.recent_lh: potential_sl.append(struct.recent_lh)
        if struct.recent_hh: potential_sl.append(struct.recent_hh)
        if lmap.nearest_bsl: potential_sl.append(lmap.nearest_bsl.level)
        
        if potential_sl:
            sl = max(potential_sl)
            sl += (pos.price_open * 0.001)
            
        # TP at nearest SSL
        if lmap.nearest_ssl:
            tp = lmap.nearest_ssl.level
        elif struct.recent_ll:
            tp = struct.recent_ll

    # Ensure RR is at least 1.0 if we found levels
    if sl != 0 and tp != 0:
        dist_sl = abs(pos.price_open - sl)
        dist_tp = abs(tp - pos.price_open)
        if dist_tp < dist_sl:
            print(f"  Warning: Calculated RR is less than 1.0 ({dist_tp/dist_sl:.2f}). Adjusting TP.")
            tp = pos.price_open + (pos.price_open - sl) * 2.0 if pos.type == mt5.ORDER_TYPE_BUY else pos.price_open - (sl - pos.price_open) * 2.0

    print(f"  Suggested SL: {sl:.2f}")
    print(f"  Suggested TP: {tp:.2f}")
    
    # Modify position
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "position": pos.ticket,
        "sl": float(sl),
        "tp": float(tp),
    }
    
    result = mt5.order_send(request)
    if result is None:
        print(f"  Failed to update {symbol}: {mt5.last_error()}")
    elif result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"  Update failed: {result.retcode} ({result.comment})")
    else:
        print(f"  --- SUCCESS: SL/TP applied to {symbol} ---")

mt5.shutdown()
