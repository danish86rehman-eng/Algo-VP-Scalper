"""New, isolated trigger comparison. Production is never imported or edited.

py -3.14 -E -B docs/reviews/trigger_priority_study.py --prepare
py -3.14 -E -B docs/reviews/trigger_priority_study.py --window W1 --arm BASELINE
Orders, logins and network data access are denied during frozen-data replay.
"""
import argparse
from collections import Counter
from contextlib import ExitStack
from copy import copy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/reviews/2026-09-07-trigger-priority-evidence"
SNAP = OUT / "snapshot/apex_ai"
COST_SOURCE = Path("D:/OneDrive - Orient Petroleum/Personal/Obsidian/AI-Trading-Strategies/wiki/concepts/xauusd-execution-costs.json")
ACTIVE = ("HTF_CRT_SWEEP", "FVG_FILL", "SWEEP_REJECTION", "BOS_RETEST", "JUDAS")
OPTIONAL = ("VP_LIQUIDITY_REACTION", "VALUE_AREA_FADE")
WINDOWS = {"W1": ("2026-03-01", "2026-06-01"),
           "W2": ("2026-06-01", "2026-09-01"),
           "RECENT": ("2026-09-01", "2026-09-07"),
           "SMOKE": ("2026-09-04", "2026-09-05")}


def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str, allow_nan=False), encoding="utf-8")


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def prepare():
    assert not (OUT / "manifest.json").exists(), "Frozen study already prepared; do not overwrite"
    OUT.mkdir(parents=True, exist_ok=True)
    source_hashes = {}
    for src in (ROOT / "apex_ai").rglob("*"):
        rel = src.relative_to(ROOT / "apex_ai")
        if not src.is_file() or any(p in {"logs", "data", "__pycache__", ".venv", "venv"} for p in rel.parts):
            continue
        if src.suffix != ".py" and str(rel) not in {"news_events.json", "config.json"}:
            continue
        dest = SNAP / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        source_hashes[str(rel)] = digest(dest)
    shutil.copy2(COST_SOURCE, OUT / "cost_reference.json")
    # Two existing integration tests use repository-relative frozen candles.
    fixture_rel = Path("docs/reviews/2026-09-02-support-retest-evidence")
    fixture_dir = OUT / "snapshot" / fixture_rel
    fixture_dir.mkdir(parents=True, exist_ok=True)
    for name in ("XAUUSD_M15.csv", "XAUUSD_M5.csv"):
        shutil.copy2(ROOT / fixture_rel / name, fixture_dir / name)
    (SNAP / "logs").mkdir(exist_ok=True)
    sys.path.insert(0, str(SNAP))
    import MetaTrader5 as mt5
    import numpy as np
    import pandas as pd
    import backtest_scalper as sim
    from scalper import decision_params as dp
    assert dp.CRT_ENABLED and dp.M15_FVG_ENTRY_ENABLED and dp.RECLAIM_FVG_ENABLED
    assert not dp.VPLR_ENABLED and not dp.VA_FADE_ENABLED
    validation = subprocess.run([sys.executable, "-E", "-B", "-m", "unittest", "discover", "-s", "tests"],
                                cwd=SNAP, capture_output=True, text=True, encoding="utf-8", errors="replace")
    attempt = len(list(OUT.glob("tests_stderr*.log"))) + 1
    (OUT / f"tests_stdout_{attempt}.log").write_text(validation.stdout, encoding="utf-8")
    (OUT / f"tests_stderr_{attempt}.log").write_text(validation.stderr, encoding="utf-8")
    print(validation.stderr[-1500:], flush=True)
    assert validation.returncode == 0 and "Ran 490 tests" in validation.stderr, "Preflight failed"
    # Syntax verification without producing bytecode in the production tree.
    for path in SNAP.rglob("*.py"):
        compile(path.read_bytes(), str(path), "exec")
    assert mt5.initialize(), mt5.last_error()  # attach only; never credentialed fallback
    try:
        account = mt5.account_info()
        info = mt5.symbol_info("XAUUSD")
        assert account and info and account.currency == "USD" and info.currency_profit == "USD"
        assert info.point == info.trade_tick_size and info.trade_contract_size > 0
        specs = info._asdict()
        # Prove the offline USD contract P&L adapter against the broker calculator.
        pnl_checks = []
        for side in (mt5.ORDER_TYPE_BUY, mt5.ORDER_TYPE_SELL):
            for volume, entry, close in ((.01, 4400., 4390.), (.03, 4400., 4420.), (.12, 3900., 3900.317)):
                broker = mt5.order_calc_profit(side, "XAUUSD", volume, entry, close)
                local = round((close-entry)*volume*info.trade_contract_size*(1 if side == mt5.ORDER_TYPE_BUY else -1), 2)
                assert broker is not None and abs(local-broker) < .001, (broker, local)
                pnl_checks.append(dict(side=side, volume=volume, entry=entry, close=close, broker=broker, offline=local))
        mapping = {"M5": dp.TF_CONFIRM, "M15": dp.TF_TRIGGER, "H1": dp.TF_HTF, "H4": dp.TF_H4, **dp.CRT_TIMEFRAMES}
        manifest_data = {}
        for name, code in mapping.items():
            start = utc(WINDOWS["W1"][0]) - timedelta(days=400 if name in dp.CRT_TIMEFRAMES else sim.WARMUP_LEAD_DAYS)
            end = utc(WINDOWS["RECENT"][1])
            frame = sim._fetch("XAUUSD", code, start, end)
            assert not frame.empty and frame.time.is_monotonic_increasing and frame.time.is_unique, name
            assert frame.time.min() < utc(WINDOWS["W1"][0]), f"{name}: missing warm-up"
            assert frame.time.max() >= utc("2026-09-04") or name in {"W1", "MN1"}, f"{name}: missing tail"
            path = OUT / f"data/XAUUSD_{name}.pkl"
            path.parent.mkdir(exist_ok=True)
            frame.to_pickle(path)
            rows = dict(rows=len(frame), first=frame.time.iloc[0], last=frame.time.iloc[-1], sha256=digest(path))
            if "spread" in frame:
                rows["spread_zero_fraction"] = float((frame.spread == 0).mean())
                rows["spread_points_quantiles"] = np.quantile(frame.spread, [0, .5, .9, 1]).tolist()
            manifest_data[name] = rows
            print("DATA", name, rows, flush=True)
        deals = mt5.history_deals_get(utc("2026-09-01"), datetime.now(timezone.utc))
        deals = [d for d in (deals or []) if d.symbol == "XAUUSD" and d.volume > 0]
        live_cost_sample = dict(deals=len(deals), commission_per_lot_unique=sorted({round(abs(d.commission)/d.volume, 6) for d in deals}),
                               swap_total=sum(d.swap for d in deals), note="Small live sample; not a live slippage calibration")
        news = json.loads((SNAP / "news_events.json").read_text(encoding="utf-8"))
        news_times = sorted(e["time_utc"] for e in news)
        save(OUT / "manifest.json", dict(captured_utc=datetime.now(timezone.utc), server=account.server,
             account_trade_mode=account.trade_mode, currency=account.currency, symbol="XAUUSD", symbol_info=specs,
             windows=WINDOWS, active_triggers=ACTIVE, disabled_triggers=["VP_LIQUIDITY_REACTION", "VALUE_AREA_FADE"],
             source_hashes=source_hashes, data=manifest_data, pnl_adapter_checks=pnl_checks,
             live_cost_sample=live_cost_sample, news=dict(events=len(news), first=news_times[0], last=news_times[-1]),
             cost_reference_sha256=digest(OUT / "cost_reference.json"), tests=490,
             limitations=["Existing M5 bar-fill/Guardian approximation, not the live 30-second/tick sequence",
                          "Current cached calendar lacks historical coverage; news gate kept on but cannot reconstruct past blackouts",
                          "Measured total-cost reference is from DEMO, not a calibrated live execution model",
                          "Existing code applied retrospectively; historical dates are not untouched out-of-sample",
                          "No broker partial/rejected fills, reconnects, restarts, queueing or intrabar bid/ask reconstruction",
                          "watch_inactive_crt=False skips observational watches outside sessions, not entry permissions"] ))
    finally:
        mt5.shutdown()


def metrics(trades):
    values = [t["pnl"] for t in trades]
    positive = sum(p for p in values if p > 0)
    negative = -sum(p for p in values if p < 0)
    equity, peak, max_dd, max_dd_pct = 1000., 1000., 0., 0.
    for trade in sorted(trades, key=lambda t: t["close_time"]):
        equity += trade["pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak-equity)
        max_dd_pct = max(max_dd_pct, 100*(peak-equity)/peak)
    return dict(trades=len(trades), pnl=round(sum(values), 2), ending_balance=round(equity, 2),
                gross_pnl=round(sum(t["gross_pnl"] for t in trades), 2),
                win_rate_pct=100*sum(p > 0 for p in values)/len(values) if values else None,
                profit_factor=positive/negative if negative else None,
                sum_net_r=sum(t["pnl"]/t["risk_usd"] for t in trades),
                max_closed_balance_drawdown=round(max_dd, 2), max_closed_balance_drawdown_pct=max_dd_pct,
                cost_overlay_fixed_trades={str(c): round(sum(t["gross_pnl"]-c*100*t["volume"] for t in trades), 2) for c in (.17, .35, .75)})


def replay(window, arm, full_watch=False):
    import MetaTrader5 as mt5
    import pandas as pd
    manifest = json.loads((OUT / "manifest.json").read_text())
    # Refuse stale or modified snapshots; production can continue independently.
    for rel, expected in manifest["source_hashes"].items():
        assert digest(SNAP / rel) == expected, f"Snapshot changed: {rel}"
    run_name = arm + ("_FULLWATCH" if full_watch else "")
    result_path = OUT / f"results/{window}__{run_name}.json"
    if result_path.exists():
        assert (OUT / f"summaries/{window}__{run_name}.json").exists(), "Incomplete checkpoint needs inspection"
        print("PRESERVED completed experiment", window, arm, flush=True)
        return
    sys.path.insert(0, str(SNAP))
    os.chdir(SNAP)
    import backtest_scalper as sim
    from scalper import decision_params as dp
    from scalper.session_checker import DEFAULT_ENABLED_SESSIONS
    logging.disable(logging.CRITICAL)
    mapping = {"M5": dp.TF_CONFIRM, "M15": dp.TF_TRIGGER, "H1": dp.TF_HTF, "H4": dp.TF_H4, **dp.CRT_TIMEFRAMES}
    frames = {}
    for name, code in mapping.items():
        path = OUT / f"data/XAUUSD_{name}.pkl"
        assert digest(path) == manifest["data"][name]["sha256"]
        frames[code] = pd.read_pickle(path)  # only the files generated by this study
    start, end = map(utc, WINDOWS[window])
    cost = json.loads((OUT / "cost_reference.json").read_text())["round_trip_model"]["RECOMMENDED_DEFAULT_mean"]
    info = SimpleNamespace(**manifest["symbol_info"])
    started = time.monotonic()
    calls, switches = 0, 0
    overlaps, possible_promotions = Counter(), Counter()
    original_step2 = sim.SATriggerEngine.step2_trigger
    original_watch = sim.watch_crt
    promote = arm.removeprefix("TOP_") if arm.startswith("TOP_") else None

    def selector(engine, *args, **kwargs):
        nonlocal switches
        result = original_step2(engine, *args, **kwargs)
        if not result.detected:
            return result
        assert result.trigger_type == result.matched_triggers[0]
        if len(result.matched_triggers) > 1:
            overlaps[" > ".join(result.matched_triggers)] += 1
        for name in result.matched_triggers[1:]:
            possible_promotions[name] += 1
        if promote and promote in result.matched_triggers[1:]:
            isolated = copy(engine)
            isolated.enabled_triggers = {promote}
            chosen = original_step2(isolated, *args, **kwargs)
            assert chosen.detected and chosen.trigger_type == promote
            chosen.matched_triggers = [promote] + [t for t in result.matched_triggers if t != promote]
            switches += 1
            return chosen
        return result

    def fetch(symbol, code, begin, finish):
        assert symbol == "XAUUSD"
        f = frames[code]
        return f[(f.time >= begin) & (f.time <= finish)].copy()

    def calc_profit(side, symbol, volume, entry, close):
        assert symbol == "XAUUSD" and side in (mt5.ORDER_TYPE_BUY, mt5.ORDER_TYPE_SELL)
        return round((close-entry)*volume*info.trade_contract_size*(1 if side == mt5.ORDER_TYPE_BUY else -1), 2)

    def watch(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls % 250 == 0:
            print(f"PROGRESS {window} {arm} {args[5]} watches={calls} elapsed={time.monotonic()-started:.1f}s", flush=True)
        # The optional VP-only whitelist cannot select HTF_CRT_SWEEP.
        # In this isolated arm the CRT plan is used solely for observation,
        # never in gates or exits. Skip those unused pure-function watches.
        # --full-watch provides a paired replay to verify trade-path equality.
        if arm == "VP_LIQUIDITY_REACTION" and not full_watch:
            return []
        return original_watch(*args, **kwargs)

    with ExitStack() as stack:
        for name in ("initialize", "login", "order_send", "copy_rates_range", "copy_rates_from", "copy_rates_from_pos", "copy_ticks_range", "copy_ticks_from"):
            stack.enter_context(patch.object(mt5, name, side_effect=AssertionError(f"OFFLINE RESEARCH: {name} prohibited")))
        stack.enter_context(patch.object(mt5, "symbol_info", return_value=info))
        stack.enter_context(patch.object(mt5, "symbol_select", return_value=True))
        stack.enter_context(patch.object(mt5, "order_calc_profit", side_effect=calc_profit))
        stack.enter_context(patch.object(sim, "_fetch", side_effect=fetch))
        stack.enter_context(patch.object(sim, "watch_crt", side_effect=watch))
        # BASELINE observes arbitration only. TOP_* changes only the selected
        # first match, using unchanged detector methods, inside this process.
        stack.enter_context(patch.object(sim.SATriggerEngine, "step2_trigger", selector))
        result = sim.run_backtest(["XAUUSD"], start, end-timedelta(microseconds=1),
                 1000., .03, dp.MAX_OPEN_POSITIONS, 6., True, True,
                 daily_loss_limit_usd=100., commission_per_lot=11.,
                 enabled_triggers=None if arm == "BASELINE" or promote else [arm],
                 enabled_sessions=DEFAULT_ENABLED_SESSIONS, tga_exits=True,
                 vplr_enabled=arm == "VP_LIQUIDITY_REACTION",
                 va_fade_enabled=arm == "VALUE_AREA_FADE",
                 round_trip_cost_price=cost, watch_inactive_crt=False)
    result["research"] = dict(optional_activation=arm in OPTIONAL,
        unused_crt_watch_suppressed=arm == "VP_LIQUIDITY_REACTION" and not full_watch,
        note="VP-only native ASIA_ONLY/00:00-06:30 allowance preserved; not the current production session sample" if arm == "VP_LIQUIDITY_REACTION" else "")
    save(result_path, result)
    trades = result["trades"]
    cuts = [start, start+(end-start)*.6, start+(end-start)*.8, end]
    summary = dict(window=window, arm=arm, start=start, end_exclusive=end,
                   **metrics(trades), folds=[metrics([t for t in trades if a <= pd.Timestamp(t["open_time"]) < b]) for a,b in zip(cuts,cuts[1:])],
                   trigger_breakdown={name: metrics([t for t in trades if t["trigger_type"] == name]) for name in ACTIVE},
                   triggers_detected=result["triggers_detected"], rejections=result["rejections"],
                   arbitration_overlaps=dict(overlaps), possible_promotions=dict(possible_promotions),
                   arbitration_switches=switches, elapsed_seconds=time.monotonic()-started)
    save(OUT / f"summaries/{window}__{run_name}.json", summary)
    print("DONE", window, arm, "n", len(trades), "net", summary["pnl"], "PF", summary["profit_factor"],
          "switches", switches, "seconds", round(summary["elapsed_seconds"], 1), flush=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--window", choices=WINDOWS)
    parser.add_argument("--arm", choices=("BASELINE", *ACTIVE, *OPTIONAL, *("TOP_"+n for n in ACTIVE)))
    parser.add_argument("--full-watch", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        prepare()
    else:
        assert args.window and args.arm
        replay(args.window, args.arm, args.full_watch)
