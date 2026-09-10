"""Offline reproduction of two recorded entry predicates; never submits orders.

Run from any directory with: py -3.14 -E path/to/replay_incident.py
This validates the incident reconstruction, not portfolio performance.
"""
from dataclasses import asdict
import json
from pathlib import Path
import sys

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "apex_ai"))

from scalper import decision_params as DP
from scalper.short_term_bias import ShortTermBiasFilter
from scalper.trigger_engine import SATriggerEngine


def main():
    frames = {}
    for label in ("M15", "M5", "H1", "H4"):
        df = pd.read_csv(HERE / f"XAUUSD_{label}.csv")
        df["time"] = pd.to_datetime(df["time"], utc=True)
        frames[label] = df
    reference = json.loads((HERE / "decision_reconstruction.json").read_text())
    minutes = {"M15": 15, "M5": 5, "H1": 60, "H4": 240}
    lengths = {"M15": DP.TRIGGER_BARS, "M5": DP.CONFIRM_BARS, "H1": 120, "H4": 120}
    checks = []
    for expected in reference:
        moment = pd.Timestamp(expected["decision_utc"])
        closed = {
            label: df[df.time + pd.Timedelta(minutes=minutes[label]) <= moment].tail(lengths[label])
            for label, df in frames.items()
        }
        engine = SATriggerEngine(sweep_wick_filter=False)
        liq = engine.step1_liquidity(closed["M15"], "XAUUSD")
        trigger = engine._check_sweep_rejection(closed["M15"], liq, "XAUUSD")
        assert trigger.detected
        for name in ("entry_price", "stop_loss", "tp1", "tp2", "swept_level", "wick_ratio"):
            assert abs(getattr(trigger, name) - expected["trigger"][name]) < 1e-8, name
        bias = ShortTermBiasFilter().check(
            "XAUUSD", trigger.direction, trigger.trigger_type, trigger.entry_price,
            closed["M15"], closed["H1"], closed["H4"], moment.to_pydatetime(),
        )
        assert asdict(bias) == expected["stb"]
        wick_results = {}
        for threshold in (0.35, 0.45, 0.55):
            filtered = SATriggerEngine(sweep_wick_filter=True, sweep_wick_ratio=threshold)
            wick_results[str(threshold)] = bool(
                filtered._check_sweep_rejection(closed["M15"], liq, "XAUUSD").detected
            )
        checks.append({"decision": str(moment), "signal_and_bias_match": True,
                       "wick_filter_retains_candidate": wick_results})
    deals = json.loads((HERE / "broker_deals.json").read_text())["deals"]
    pair_net = []
    for ticket in (494903819, 494906413):
        position = [d for d in deals if d["position_id"] == ticket]
        net = sum(d[k] for d in position for k in ("profit", "commission", "swap", "fee"))
        pair_net.append(round(net, 2))
    assert pair_net == [15.73, -27.18]
    assert checks[0]["wick_filter_retains_candidate"]["0.45"] is False
    assert checks[1]["wick_filter_retains_candidate"]["0.45"] is True
    first_exit = max(pd.Timestamp(d["time_utc"]) for d in deals
                     if d["position_id"] == 494903819 and d["entry"] == 1)
    second_signal_close = pd.Timestamp(reference[1]["last_closed_trigger_bar_open"]) + pd.Timedelta(minutes=15)
    assert second_signal_close <= first_exit
    print(json.dumps({"checks": checks, "pair_net_usd": round(sum(pair_net), 2),
                      "second_candidate_has_fresh_post_exit_m15_close": False,
                      "scope": "Entry predicates and broker P&L only; not a backtest of a new strategy."}, indent=2))


if __name__ == "__main__":
    main()
