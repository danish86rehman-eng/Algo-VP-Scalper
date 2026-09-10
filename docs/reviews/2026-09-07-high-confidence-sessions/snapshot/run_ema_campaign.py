"""
Walk-forward campaign for the H1 EMA(18) high/low band.

Runs the simulator with the band on and off over a chronological 60/20/20
split, which is the promotion bar recorded in docs/RESEARCH_NOTES.md: a rule
is only evidence if its sign is the same in every fold. A single favourable
window is not a result.

Both arms run the same code, the same gates and the same per-bar broker
spread; the only difference is the band. That is the whole point — the
comparison is only meaningful because the two arms are otherwise identical.

Usage:
    python run_ema_campaign.py --from 2026-05-15 --to 2026-08-23 --symbols XAUUSD
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

from backtest_scalper import _load_env_and_connect, run_backtest


def folds(dt_from: datetime, dt_to: datetime) -> List[Tuple[str, datetime, datetime]]:
    """Chronological 60/20/20 split. No shuffling: order is the whole point."""
    span = dt_to - dt_from
    a = dt_from + timedelta(seconds=span.total_seconds() * 0.60)
    b = dt_from + timedelta(seconds=span.total_seconds() * 0.80)
    return [
        ("FULL", dt_from, dt_to),
        ("F1-train(60%)", dt_from, a),
        ("F2-val(20%)", a, b),
        ("F3-test(20%)", b, dt_to),
    ]


def summarise(res: Dict, starting_pool: float) -> Dict:
    """
    Profit factor and peak-to-trough drawdown are not in the simulator's
    summary block, so they are derived here from the trade list — net of costs,
    since a gross profit factor would flatter every arm equally and hide the
    fact that spread is the dominant term on this instrument.
    """
    s = res["summary"]
    pnls = [t["pnl"] for t in res.get("trades", [])]
    gains = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    pf = (gains / losses) if losses > 0 else (float("inf") if gains > 0 else 0.0)

    equity, peak, max_dd = starting_pool, starting_pool, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100.0)

    return {
        "trades": s["trades"],
        "win_rate": s.get("win_rate_pct", 0.0),
        "net": s.get("net_pnl", 0.0),
        "pf": pf,
        "max_dd": max_dd,
        "costs": s.get("total_costs", 0.0),
        "ema_rejects": res.get("rejections", {}).get("EMA_BAND", 0),
        "exits": _exit_mix(res),
    }


def _exit_mix(res: Dict) -> Dict[str, int]:
    """
    Trades bucketed by exit profile. MQL5 art. 19211's finding is that a
    negative-expectancy exit profile has to be eliminated rather than diluted,
    so the mix is reported per arm rather than folded into one win rate.
    """
    mix: Dict[str, int] = {}
    for t in res.get("trades", []):
        mix[t["exit_reason"]] = mix.get(t["exit_reason"], 0) + 1
    return mix


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="date_from", default="2026-05-15")
    p.add_argument("--to", dest="date_to", default="2026-08-23")
    p.add_argument("--symbols", default="XAUUSD")
    p.add_argument("--pool", type=float, default=1000.0)
    p.add_argument("--risk", type=float, default=0.03)
    p.add_argument("--loss-limit", type=float, default=100.0)
    p.add_argument("--triggers", default=None,
                   help="Comma-separated trigger whitelist. step2_trigger "
                        "returns the FIRST detector that fires and "
                        "SWEEP_REJECTION heads the priority order, so a "
                        "trigger behind it can only be measured by removing "
                        "the ones in front.")
    p.add_argument("--out", default="logs/ema_campaign.json")
    a = p.parse_args()

    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    dt_from = datetime.fromisoformat(a.date_from).replace(tzinfo=timezone.utc)
    dt_to = datetime.fromisoformat(a.date_to).replace(tzinfo=timezone.utc)

    triggers = ([t.strip().upper() for t in a.triggers.split(",") if t.strip()]
                if a.triggers else None)

    _load_env_and_connect()
    print(f"Triggers: {triggers or 'all four (default priority order)'}")

    out: Dict[str, Dict] = {}
    rows = []
    for name, f0, f1 in folds(dt_from, dt_to):
        for arm, enabled, mode in (("EMA OFF", False, "TREND"),
                                   ("TREND", True, "TREND"),
                                   ("FADE", True, "FADE")):
            res = run_backtest(
                symbols=symbols, dt_from=f0, dt_to=f1,
                sa_pool=a.pool, risk_pct=a.risk,
                max_open_positions=2, spread_pips=2.5,
                enforce_session_windows=True, enforce_news_blackout=True,
                daily_loss_limit_usd=a.loss_limit,
                enabled_triggers=triggers,
                ema_band_enabled=enabled, ema_band_mode=mode,
            )
            key = f"{name} | {arm}"
            out[key] = res
            m = summarise(res, a.pool)
            rows.append((name, arm, f0.date().isoformat(), f1.date().isoformat(), m))

    hdr = (f"{'Fold':<15}{'Arm':<9}{'Window':<24}{'Trades':>7}{'Win%':>8}"
           f"{'Net$':>10}{'PF':>7}{'DD%':>8}{'Costs$':>9}")
    print("\n" + "=" * len(hdr))
    print("H1 EMA(18) HIGH/LOW BAND — WALK-FORWARD")
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    last_fold = None
    for name, arm, w0, w1, m in rows:
        if last_fold and name != last_fold:
            print("-" * len(hdr))
        last_fold = name
        print(f"{name:<15}{arm:<9}{w0}->{w1:<11}{m['trades']:>7}"
              f"{m['win_rate']:>8.1f}{m['net']:>10.2f}{m['pf']:>7.2f}"
              f"{m['max_dd']:>8.1f}{m['costs']:>9.2f}")
    print("=" * len(hdr))

    # The promotion test: same sign in every fold, for each arm separately.
    print("\nSign consistency across F1/F2/F3 (the promotion bar):")
    for arm in ("EMA OFF", "TREND", "FADE"):
        signs = [(name, m["net"]) for name, a_, _, _, m in rows
                 if a_ == arm and name.startswith("F")]
        pos = all(v > 0 for _, v in signs)
        neg = all(v < 0 for _, v in signs)
        verdict = "PASS (all positive)" if pos else (
            "consistent, all negative" if neg else "FAIL — sign flips")
        detail = "  ".join(f"{n.split('-')[0]}={v:+.2f}" for n, v in signs)
        print(f"  {arm:<9} {detail}    -> {verdict}")

    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\nFull results: {a.out}")


if __name__ == "__main__":
    main()
