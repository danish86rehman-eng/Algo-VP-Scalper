import json
import os
import pandas as pd
import numpy as np
from datetime import datetime

def analyze_json_results(json_path: str):
    if not os.path.exists(json_path):
        print(f"Error: {json_path} does not exist.")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    config = data.get("config", {})
    summary = data.get("summary", {})
    trades_list = data.get("trades", [])

    print("================================================================================")
    print("                      SCALPER AGENT DETAILED BACKTEST REPORT                     ")
    print("================================================================================")
    print(f"Period:         {config.get('from')} to {config.get('to')}")
    print(f"Starting Pool:  ${config.get('sa_pool'):.2f}")
    print(f"Risk per Trade: {config.get('risk_pct')*100:.1f}%")
    print(f"Max Open Pos:   {config.get('max_open_positions')}")
    print(f"Spread Pips:    {config.get('spread_pips')} pips")
    print(f"Session Filter: {'ON' if config.get('enforce_session_windows') else 'OFF'}")
    print(f"News Blackout:  {'ON' if config.get('enforce_news_blackout') else 'OFF'}")
    print("--------------------------------------------------------------------------------")

    if not trades_list:
        print("No trades were taken during this period.")
        return

    df = pd.DataFrame(trades_list)
    df["open_time"] = pd.to_datetime(df["open_time"])
    df["close_time"] = pd.to_datetime(df["close_time"])

    # Basic stats
    total_trades = len(df)
    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]
    
    num_wins = len(wins)
    num_losses = len(losses)
    win_rate = (num_wins / total_trades) * 100.0 if total_trades > 0 else 0.0

    gross_profit = wins["pnl"].sum()
    gross_loss = abs(losses["pnl"].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    net_pnl = df["pnl"].sum()
    ending_balance = config.get("sa_pool") + net_pnl

    # Drawdown calculation
    df = df.sort_values(by="close_time").reset_index(drop=True)
    balances = [config.get("sa_pool")]
    current_balance = config.get("sa_pool")
    for pnl in df["pnl"]:
        current_balance += pnl
        balances.append(current_balance)
    
    balances = np.array(balances)
    peak = np.maximum.accumulate(balances)
    drawdowns = (peak - balances) / peak * 100.0
    max_dd_pct = drawdowns.max()
    max_dd_usd = (peak - balances).max()

    # Results breakdown
    result_counts = df["result"].value_counts()

    print(f"Total Trades:     {total_trades}")
    print(f"Wins / Losses:    {num_wins} / {num_losses}")
    print(f"Win Rate:         {win_rate:.2f}%")
    print(f"Gross Profit:     ${gross_profit:.2f}")
    print(f"Gross Loss:       ${gross_loss:.2f}")
    print(f"Profit Factor:    {profit_factor:.2f}")
    print(f"Net PnL:          ${net_pnl:.2f} ({net_pnl / config.get('sa_pool') * 100:.2f}%)")
    print(f"Ending Balance:   ${ending_balance:.2f}")
    print(f"Max Drawdown:     ${max_dd_usd:.2f} ({max_dd_pct:.2f}%)")
    print(f"Avg Duration:     {df['duration_min'].mean():.1f} mins")
    print("--------------------------------------------------------------------------------")
    print("TRADE OUTCOME BREAKDOWN:")
    for res, cnt in result_counts.items():
        pct = (cnt / total_trades) * 100.0
        print(f"  - {res:12}: {cnt:3} trades ({pct:.2f}%)")
    print("--------------------------------------------------------------------------------")

    # Symbol breakdown
    print("SYMBOL PERFORMANCE BREAKDOWN:")
    sym_groups = df.groupby("symbol")
    print(f"{'Symbol':10} | {'Trades':6} | {'Win Rate':8} | {'Net PnL':10} | {'Profit Factor':13} | {'Avg PnL':8}")
    print("-" * 68)
    for sym, group in sym_groups:
        s_trades = len(group)
        s_wins = len(group[group["pnl"] > 0])
        s_wr = (s_wins / s_trades) * 100.0 if s_trades > 0 else 0.0
        s_pnl = group["pnl"].sum()
        s_gross_p = group[group["pnl"] > 0]["pnl"].sum()
        s_gross_l = abs(group[group["pnl"] <= 0]["pnl"].sum())
        s_pf = s_gross_p / s_gross_l if s_gross_l > 0 else float("inf")
        s_avg_pnl = group["pnl"].mean()
        print(f"{sym:10} | {s_trades:6} | {s_wr:7.2f}% | ${s_pnl:8.2f} | {s_pf:13.2f} | ${s_avg_pnl:7.2f}")
    print("--------------------------------------------------------------------------------")

    # Direction breakdown
    print("DIRECTION PERFORMANCE BREAKDOWN:")
    dir_groups = df.groupby("direction")
    print(f"{'Direction':10} | {'Trades':6} | {'Win Rate':8} | {'Net PnL':10} | {'Avg PnL':8}")
    print("-" * 50)
    for d_name, group in dir_groups:
        d_trades = len(group)
        d_wins = len(group[group["pnl"] > 0])
        d_wr = (d_wins / d_trades) * 100.0 if d_trades > 0 else 0.0
        d_pnl = group["pnl"].sum()
        d_avg_pnl = group["pnl"].mean()
        print(f"{d_name:10} | {d_trades:6} | {d_wr:7.2f}% | ${d_pnl:8.2f} | ${d_avg_pnl:7.2f}")
    print("--------------------------------------------------------------------------------")

    # Trigger type breakdown
    print("TRIGGER TYPE BREAKDOWN:")
    trig_groups = df.groupby("trigger_type")
    print(f"{'Trigger Type':20} | {'Trades':6} | {'Win Rate':8} | {'Net PnL':10} | {'Avg PnL':8}")
    print("-" * 60)
    for t_name, group in trig_groups:
        t_trades = len(group)
        t_wins = len(group[group["pnl"] > 0])
        t_wr = (t_wins / t_trades) * 100.0 if d_trades > 0 else 0.0
        t_pnl = group["pnl"].sum()
        t_avg_pnl = group["pnl"].mean()
        print(f"{t_name:20} | {t_trades:6} | {t_wr:7.2f}% | ${t_pnl:8.2f} | ${t_avg_pnl:7.2f}")
    print("================================================================================")

    # Save a markdown report
    md_report_path = "logs/scalper_30d_performance_summary.md"
    with open(md_report_path, "w", encoding="utf-8") as mr:
        mr.write(f"# Scalper Agent 30-Day Backtest Report\n\n")
        mr.write(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        mr.write(f"## Executive Summary\n")
        mr.write(f"| Parameter | Value |\n")
        mr.write(f"|---|---|\n")
        mr.write(f"| **Backtest Period** | {config.get('from')} to {config.get('to')} |\n")
        mr.write(f"| **Starting Capital** | ${config.get('sa_pool'):.2f} |\n")
        mr.write(f"| **Ending Balance** | ${ending_balance:.2f} |\n")
        mr.write(f"| **Net Return** | **${net_pnl:.2f} ({net_pnl / config.get('sa_pool') * 100:.2f}%)** |\n")
        mr.write(f"| **Total Trades** | {total_trades} |\n")
        mr.write(f"| **Win Rate** | {win_rate:.2f}% ({num_wins} wins, {num_losses} losses) |\n")
        mr.write(f"| **Profit Factor** | {profit_factor:.2f} |\n")
        mr.write(f"| **Max Drawdown** | ${max_dd_usd:.2f} ({max_dd_pct:.2f}%) |\n")
        mr.write(f"| **Avg Trade Duration** | {df['duration_min'].mean():.1f} minutes |\n\n")

        mr.write(f"## Trade Outcome Breakdown\n")
        mr.write(f"| Outcome | Trades | Percentage |\n")
        mr.write(f"|---|---|---|\n")
        for res, cnt in result_counts.items():
            pct = (cnt / total_trades) * 100.0
            mr.write(f"| **{res}** | {cnt} | {pct:.2f}% |\n")
        mr.write(f"\n")

        mr.write(f"## Symbol Performance Breakdown\n")
        mr.write(f"| Symbol | Trades | Win Rate | Net PnL | Profit Factor | Avg PnL per Trade |\n")
        mr.write(f"|---|---|---|---|---|---|\n")
        for sym, group in sym_groups:
            s_trades = len(group)
            s_wins = len(group[group["pnl"] > 0])
            s_wr = (s_wins / s_trades) * 100.0 if s_trades > 0 else 0.0
            s_pnl = group["pnl"].sum()
            s_gross_p = group[group["pnl"] > 0]["pnl"].sum()
            s_gross_l = abs(group[group["pnl"] <= 0]["pnl"].sum())
            s_pf = s_gross_p / s_gross_l if s_gross_l > 0 else float("inf")
            s_avg_pnl = group["pnl"].mean()
            mr.write(f"| **{sym}** | {s_trades} | {s_wr:.2f}% | ${s_pnl:.2f} | {s_pf:.2f} | ${s_avg_pnl:.2f} |\n")
        mr.write(f"\n")

        mr.write(f"## Direction Performance Breakdown\n")
        mr.write(f"| Direction | Trades | Win Rate | Net PnL | Avg PnL per Trade |\n")
        mr.write(f"|---|---|---|---|---|\n")
        for d_name, group in dir_groups:
            d_trades = len(group)
            d_wins = len(group[group["pnl"] > 0])
            d_wr = (d_wins / d_trades) * 100.0 if d_trades > 0 else 0.0
            d_pnl = group["pnl"].sum()
            d_avg_pnl = group["pnl"].mean()
            mr.write(f"| **{d_name}** | {d_trades} | {d_wr:.2f}% | ${d_pnl:.2f} | ${d_avg_pnl:.2f} |\n")
        mr.write(f"\n")

        mr.write(f"## Trigger Type Breakdown\n")
        mr.write(f"| Trigger Type | Trades | Win Rate | Net PnL | Avg PnL per Trade |\n")
        mr.write(f"|---|---|---|---|---|\n")
        for t_name, group in trig_groups:
            t_trades = len(group)
            t_wins = len(group[group["pnl"] > 0])
            t_wr = (t_wins / t_trades) * 100.0 if t_trades > 0 else 0.0
            t_pnl = group["pnl"].sum()
            t_avg_pnl = group["pnl"].mean()
            mr.write(f"| **{t_name}** | {t_trades} | {t_wr:.2f}% | ${t_pnl:.2f} | ${t_avg_pnl:.2f} |\n")
        mr.write(f"\n")

    print(f"Saved Markdown report to: {md_report_path}")

if __name__ == "__main__":
    analyze_json_results("logs/scalper_30d_backtest_results.json")
