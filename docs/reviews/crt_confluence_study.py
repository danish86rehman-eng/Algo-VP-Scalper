"""Fixed L-020 three-arm development replay; orders are prohibited.

Run from apex_ai: py -3.14 -E ../docs/reviews/crt_confluence_study.py
Definitions and window are frozen in the adjacent preregistration.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import argparse
import logging
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"apex_ai"))
import MetaTrader5 as mt5
import numpy as np
import pandas as pd
import backtest_scalper as sim
from scalper import decision_params as DP

OUT = ROOT/"docs/reviews/2026-09-03-crt-confluence-evidence"
START = datetime(2026, 6, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 1, tzinfo=timezone.utc)
VAULT = Path("D:/OneDrive - Orient Petroleum/Personal/Obsidian/AI-Trading-Strategies")
COST_PATH = VAULT/"wiki/concepts/xauusd-execution-costs.json"


def save(name, data):
    (OUT/name).write_text(json.dumps(data, indent=2, default=str, allow_nan=False), encoding="utf-8")


def metrics(trades):
    n = len(trades)
    profits = np.array([t["pnl"] for t in trades], float)
    risks = np.array([t["risk_usd"] for t in trades], float)
    gross = np.array([t["gross_pnl"] for t in trades], float)
    assert np.all(risks > 0), "R requires positive initial risk"
    return dict(n=n, pnl=round(float(profits.sum()), 2),
                gross_pnl=round(float(gross.sum()), 2),
                mean_net_r=float(np.mean(profits/risks)) if n else None,
                mean_gross_r=float(np.mean(gross/risks)) if n else None,
                sum_net_r=float(np.sum(profits/risks)),
                profit_factor=float(profits[profits > 0].sum()/abs(profits[profits < 0].sum())) if (profits < 0).any() else None,
                mean_inverse_stop=float(np.mean([1/abs(t["entry"]-t["sl"]) for t in trades])) if n else None,
                cost_sensitivity_fixed_trades={str(cost): round(sum(t["gross_pnl"]-cost*100*t["volume"] for t in trades), 2)
                                              for cost in (.17, .35, .75)})


def selection(trades, mode):
    labels = np.array([t["htf_crt"]["confluence"]["mss"] and
                       (mode == "MSS" or t["htf_crt"]["confluence"]["retest"]) for t in trades], bool)
    accepted = [t for t, yes in zip(trades, labels) if yes]
    rejected = [t for t, yes in zip(trades, labels) if not yes]
    result = dict(accepted=metrics(accepted), rejected=metrics(rejected), status="INSUFFICIENT_SELECTION_EVIDENCE")
    if min(len(accepted), len(rejected)) < 30:
        return result
    n = len(trades)//5*5
    outcomes = np.array([t["pnl"]/t["risk_usd"] for t in trades[:n]])
    labels = labels[:n]
    if not labels.any() or labels.all():
        return result
    def delta(mask):
        return float(outcomes[mask].mean()-outcomes[~mask].mean())
    observed = delta(labels)
    rng = np.random.default_rng(20260903)
    null = {"circular_shift": [], "block_permutation": []}
    for _ in range(2000):
        null["circular_shift"].append(delta(np.roll(labels, rng.integers(1, n))))
        null["block_permutation"].append(delta(labels.reshape(-1, 5)[rng.permutation(n//5)].reshape(-1)))
    p_values = {k: (1+sum(v >= observed for v in values))/2001 for k, values in null.items()}
    result.update(status="DEVELOPMENT_DIAGNOSTIC_ONLY", analyzed=n, tail_dropped=len(trades)-n,
                  accepted_minus_rejected_net_r=observed, p_values=p_values,
                  passes_two_test_threshold=observed > 0 and all(p <= .025 for p in p_values.values()))
    return result


def main(arm=None):
    logging.disable(logging.CRITICAL)
    OUT.mkdir(exist_ok=True)
    assert mt5.initialize(), mt5.last_error()
    try:
        account = mt5.account_info()
        assert account is not None
        cost = json.loads(COST_PATH.read_text(encoding="utf-8"))
        price_cost = cost["round_trip_model"]["RECOMMENDED_DEFAULT_mean"]
        mapping = {"M5": DP.TF_CONFIRM, "M15": DP.TF_TRIGGER, "H1": DP.TF_HTF,
                   "H4": DP.TF_H4, **DP.CRT_TIMEFRAMES}
        frames, manifest = {}, {}
        for tf, code in mapping.items():
            path = OUT/f"XAUUSD_{tf}.csv"
            if path.exists():
                frame = pd.read_csv(path, parse_dates=["time"])
            else:
                frame = sim._fetch("XAUUSD", code,
                    START-timedelta(days=400 if tf in DP.CRT_TIMEFRAMES else sim.WARMUP_LEAD_DAYS), END)
                assert not frame.empty, f"No {tf} data"
                frame.to_csv(path, index=False)
            frame.time = pd.to_datetime(frame.time, utc=True)
            frames[tf] = frame
            manifest[tf] = dict(rows=len(frame), first=frame.time.iloc[0], last=frame.time.iloc[-1],
                                sha256=sha256(path.read_bytes()).hexdigest())
            print(f"DATA {tf}: {len(frame)} {frame.time.iloc[0]} -> {frame.time.iloc[-1]}", flush=True)
        assert frames["M5"].time.min() < START-timedelta(days=10), "Warm-up incomplete"
        assert frames["M5"].time.max() >= END-timedelta(days=1), "Window incomplete"
        source_hashes = {str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest()
                         for p in (ROOT/"apex_ai").rglob("*.py") if "__pycache__" not in str(p)}
        save(f"manifest_{arm or 'all'}.json", dict(captured_utc=datetime.now(timezone.utc), broker_demo=account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO,
                                  start=START, end_exclusive=END, data=manifest, source_hashes=source_hashes,
                                  cost_source=str(COST_PATH), cost_sha256=sha256(COST_PATH.read_bytes()).hexdigest(),
                                  round_trip_cost_price=price_cost,
                                  limitations=["Used historical data, not untouched OOS", "M5 bar fills and conservative same-bar SL/TP ordering",
                                               "Aggregate measured-cost debit, not tick slippage", "Current cached news, not archived release-time calendar",
                                               "Synthetic entry bid/ask share M5 open; separate cost debit and observed-bar spread gate",
                                               "One M5 decision, not every live 30-second scan"] ))
        def fetch_cached(symbol, code, begin, end):
            tf = next(k for k, v in mapping.items() if v == code)
            f = frames[tf]
            return f[(f.time >= begin) & (f.time <= end)].copy()
        summaries, results = {}, {}
        original_watch = sim.watch_crt
        for mode in ([arm] if arm else DP.CRT_CONFLUENCE_MODES):
            started, calls = time.monotonic(), 0
            def watch(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls % 500 == 0:
                    print(f"PROGRESS {mode} {calls} watches, {args[5]}, {time.monotonic()-started:.1f}s", flush=True)
                return original_watch(*args, **kwargs)
            with patch.object(mt5, "order_send", side_effect=AssertionError("Research cannot submit orders")), \
                 patch.object(sim, "_fetch", side_effect=fetch_cached), patch.object(sim, "watch_crt", side_effect=watch):
                result = sim.run_backtest(["XAUUSD"], START, END-timedelta(microseconds=1),
                    1000., .03, 1, 6., True, True, daily_loss_limit_usd=100.,
                    commission_per_lot=11., tga_exits=True, crt_confluence_mode=mode,
                    round_trip_cost_price=price_cost, watch_inactive_crt=False)
            save(f"{mode}.json", result)
            trades = result["trades"]
            crt = [t for t in trades if t["trigger_type"] == "HTF_CRT_SWEEP"]
            cuts = [START, START+(END-START)*.6, START+(END-START)*.8, END]
            folds = [dict(begin=a, end_exclusive=b, **metrics([t for t in trades if a <= pd.Timestamp(t["open_time"]) < b]))
                     for a, b in zip(cuts, cuts[1:])]
            summaries[mode] = dict(whole_bot=metrics(trades), crt=metrics(crt), folds=folds,
                                  candidates=len(result["config"]["crt_candidate_observations"]),
                                  distinct_candidate_setups=len({p["setup_id"] for p in result["config"]["crt_candidate_observations"]}),
                                  detected=result["triggers_detected"], rejections=result["rejections"],
                                  elapsed_seconds=time.monotonic()-started)
            results[mode] = result
            print(f"DONE {mode}: {json.dumps(summaries[mode], default=str)}", flush=True)
            save(f"summary_{mode}.json", summaries[mode])
        if not arm:
            summarize()
    finally:
        mt5.shutdown()


def summarize():
    summaries = {mode: json.loads((OUT/f"summary_{mode}.json").read_text()) for mode in DP.CRT_CONFLUENCE_MODES}
    baseline = json.loads((OUT/"OBSERVE.json").read_text())
    baseline_crt = [t for t in baseline["trades"] if t["trigger_type"] == "HTF_CRT_SWEEP"]
    save("summary.json", summaries)
    save("selection.json", {mode: selection(baseline_crt, mode) for mode in ("MSS", "MSS_RETEST")})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=DP.CRT_CONFLUENCE_MODES)
    parser.add_argument("--summarize", action="store_true")
    args = parser.parse_args()
    summarize() if args.summarize else main(args.arm)
