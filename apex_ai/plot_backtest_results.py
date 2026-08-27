import json
import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

def generate_backtest_charts(json_path: str, output_path: str):
    if not os.path.exists(json_path):
        print(f"Error: {json_path} does not exist.")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    trades = data.get("trades", [])
    if not trades:
        print("No trades to plot.")
        return

    df = pd.DataFrame(trades)
    df["close_time"] = pd.to_datetime(df["close_time"])
    df = df.sort_values("close_time").reset_index(drop=True)

    initial_balance = data["config"]["sa_pool"]
    df["cumulative_pnl"] = df["pnl"].cumsum()
    df["balance"] = initial_balance + df["cumulative_pnl"]

    # Add starting point
    start_row = pd.DataFrame([{
        "close_time": pd.to_datetime(data["config"]["from"]),
        "pnl": 0.0,
        "balance": initial_balance,
        "symbol": "START",
        "direction": "NONE"
    }])
    df = pd.concat([start_row, df], ignore_index=True)

    # Calculate Drawdown
    df["peak"] = df["balance"].cummax()
    df["drawdown_usd"] = df["peak"] - df["balance"]
    df["drawdown_pct"] = (df["drawdown_usd"] / df["peak"]) * 100.0

    # Custom sleek theme styling
    plt.style.use("dark_background")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9), sharex=True, gridspec_kw={"height_ratios": [2.1, 0.9]})
    
    # Grid styling
    grid_color = "#2E2E2E"
    grid_style = "--"
    grid_width = 0.5

    # 1. Equity Curve Plot
    ax1.plot(df["close_time"], df["balance"], color="#10B981", linewidth=2.5, label="Account Equity ($)")
    
    # Gradient fill under the equity curve
    ax1.fill_between(df["close_time"], df["balance"], initial_balance, where=(df["balance"] >= initial_balance),
                     interpolate=True, color="#10B981", alpha=0.1)
    ax1.fill_between(df["close_time"], df["balance"], initial_balance, where=(df["balance"] < initial_balance),
                     interpolate=True, color="#EF4444", alpha=0.1)

    # Horizontal line for starting balance
    ax1.axhline(initial_balance, color="#9CA3AF", linestyle=":", linewidth=1.2, label=f"Initial Balance (${initial_balance:.0f})")

    # Highlighting Peak and Ending Balances
    ending_bal = df["balance"].iloc[-1]
    peak_bal = df["balance"].max()
    peak_idx = df["balance"].idxmax()
    peak_time = df["close_time"].iloc[peak_idx]

    ax1.scatter(df["close_time"].iloc[-1], ending_bal, color="#3B82F6", s=80, zorder=5)
    ax1.annotate(f"Ending: ${ending_bal:.2f}", (df["close_time"].iloc[-1], ending_bal),
                 xytext=(-120, 10), textcoords="offset points",
                 arrowprops=dict(arrowstyle="->", color="#3B82F6", lw=1),
                 color="#F3F4F6", fontweight="bold", fontsize=10)

    ax1.scatter(peak_time, peak_bal, color="#F59E0B", s=80, zorder=5)
    ax1.annotate(f"Peak: ${peak_bal:.2f}", (peak_time, peak_bal),
                 xytext=(-40, 15), textcoords="offset points",
                 arrowprops=dict(arrowstyle="->", color="#F59E0B", lw=1),
                 color="#F3F4F6", fontweight="bold", fontsize=10)

    ax1.set_title("SCALPER AGENT - 30D CUMULATIVE EQUITY CURVE", fontsize=14, fontweight="bold", pad=15, color="#F9FAFB")
    ax1.set_ylabel("Account Equity ($)", fontsize=11, color="#D1D5DB")
    ax1.grid(True, color=grid_color, linestyle=grid_style, linewidth=grid_width)
    ax1.legend(loc="upper left", frameon=True, facecolor="#1F2937", edgecolor="#374151")
    ax1.tick_params(axis='y', colors='#9CA3AF')

    # 2. Drawdown Plot
    ax2.fill_between(df["close_time"], -df["drawdown_pct"], 0, color="#EF4444", alpha=0.25)
    ax2.plot(df["close_time"], -df["drawdown_pct"], color="#EF4444", linewidth=1.5, label="Drawdown %")
    
    max_dd_pct = df["drawdown_pct"].max()
    max_dd_idx = df["drawdown_pct"].idxmax()
    max_dd_time = df["close_time"].iloc[max_dd_idx]

    ax2.scatter(max_dd_time, -max_dd_pct, color="#EF4444", s=60, zorder=5)
    ax2.annotate(f"Max DD: -{max_dd_pct:.2f}%", (max_dd_time, -max_dd_pct),
                 xytext=(20, -10), textcoords="offset points",
                 arrowprops=dict(arrowstyle="->", color="#EF4444", lw=1),
                 color="#EF4444", fontweight="bold", fontsize=10)

    ax2.set_ylabel("Drawdown %", fontsize=11, color="#D1D5DB")
    ax2.set_xlabel("Date (UTC)", fontsize=11, color="#D1D5DB")
    ax2.grid(True, color=grid_color, linestyle=grid_style, linewidth=grid_width)
    ax2.legend(loc="lower left", frameon=True, facecolor="#1F2937", edgecolor="#374151")
    ax2.set_ylim(-max_dd_pct - 10, 2)
    ax2.tick_params(axis='both', colors='#9CA3AF')

    # Formatting date axis
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax2.xaxis.set_major_locator(mdates.DayLocator(interval=3))
    plt.xticks(rotation=45)

    # Adjust layout
    plt.tight_layout()
    plt.savefig(output_path, dpi=200, facecolor="#111827")
    plt.close()
    print(f"Equity and Drawdown charts successfully generated and saved to: {output_path}")

if __name__ == "__main__":
    generate_backtest_charts("logs/scalper_30d_backtest_results.json", "logs/backtest_performance.png")
