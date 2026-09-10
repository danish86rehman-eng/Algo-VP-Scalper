import MetaTrader5 as mt5
from datetime import datetime, timezone, timedelta
import pandas as pd

mt5.initialize()
today = datetime.now(timezone.utc)
start = today - timedelta(days=1)
history_deals = mt5.history_deals_get(start, today)

if not history_deals:
    print("No deals found.")
else:
    deals = [d._asdict() for d in history_deals if d.magic == 88880]
    df = pd.DataFrame(deals)
    if not df.empty:
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df = df[['time', 'symbol', 'type', 'volume', 'price', 'profit', 'comment']]
        print(df.tail(20).to_string())
    else:
        print("No SA deals found.")
mt5.shutdown()
