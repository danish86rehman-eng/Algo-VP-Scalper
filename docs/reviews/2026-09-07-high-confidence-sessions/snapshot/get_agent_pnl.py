import MetaTrader5 as mt5
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import pandas as pd

def fetch_agent_pnl():
    load_dotenv(".env")
    login = int(os.getenv("MT5_LOGIN", 0))
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")

    if not mt5.initialize(login=login, password=password, server=server):
        print(f"Failed to initialize MT5: {mt5.last_error()}")
        return

    # Fetch history from 30 days ago to now
    from_date = datetime.now(timezone.utc) - timedelta(days=30)
    to_date = datetime.now(timezone.utc) + timedelta(days=1)

    deals = mt5.history_deals_get(from_date, to_date)
    if deals is None:
        print("No deals found in history.")
        mt5.shutdown()
        return

    print(f"Fetched {len(deals)} historical deals.")

    # Categorize deals
    records = []
    for d in deals:
        # We only care about deals that closed positions or represent profits/losses (entry/out deals)
        # Entry deal (In) has profit=0 usually. Exit deal (Out) contains the actual profit/loss.
        # But to be precise, any deal with entry == mt5.DEAL_ENTRY_OUT ormt5.DEAL_ENTRY_OUT_BY has PnL.
        if d.entry in [mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY, mt5.DEAL_ENTRY_INOUT]:
            # Map magic numbers to Agents
            magic = d.magic
            if magic == 12345 or magic == 20260426:
                agent = "MAIN APEX AI"
            elif magic == 88880:
                agent = "SCALPER AGENT (SA)"
            elif magic == 77770:
                agent = "SWING TRADER AGENT"
            elif magic == 99990:
                agent = "TRADE GUARDIAN (TGA)"
            else:
                agent = f"OTHER (Magic: {magic})"

            records.append({
                "ticket": d.position_id,
                "symbol": d.symbol,
                "type": "BUY" if d.type == mt5.DEAL_TYPE_SELL else "SELL", # Close deal type is opposite of entry
                "volume": d.volume,
                "profit": d.profit + d.commission + d.swap,
                "agent": agent,
                "time": datetime.fromtimestamp(d.time, tz=timezone.utc).isoformat()
            })

    if not records:
        print("No closed position deals found in history.")
        mt5.shutdown()
        return

    df = pd.DataFrame(records)
    
    # Calculate stats per Agent
    pnl_summary = []
    grouped = df.groupby("agent")
    for name, group in grouped:
        total_pnl = group["profit"].sum()
        total_trades = len(group)
        winning_trades = len(group[group["profit"] > 0])
        losing_trades = len(group[group["profit"] <= 0])
        win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0.0
        
        gross_profit = group[group["profit"] > 0]["profit"].sum()
        gross_loss = abs(group[group["profit"] < 0]["profit"].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        pnl_summary.append({
            "Agent": name,
            "Total Trades": total_trades,
            "Wins": winning_trades,
            "Losses": losing_trades,
            "Win Rate (%)": f"{win_rate:.1f}%",
            "Profit Factor": f"{profit_factor:.2f}" if profit_factor != float('inf') else "N/A",
            "Total PnL (USD)": f"${total_pnl:,.2f}"
        })

    summary_df = pd.DataFrame(pnl_summary)
    
    print("\n=============================================================")
    print("                AGENT-WISE PNL PERFORMANCE STATEMENT          ")
    print("=============================================================\n")
    print(summary_df.to_string(index=False))
    print("\n=============================================================")
    
    print("\n--- Recent Closed Deals Detail ---")
    detail_df = df.sort_values(by="time", ascending=False).head(15)
    print(detail_df[["time", "agent", "symbol", "profit"]].to_string(index=False))

    mt5.shutdown()

if __name__ == "__main__":
    fetch_agent_pnl()
