"""
Walk-forward and exit-profile analysis for a `backtest_scalper.py` result file.

Answers the three questions `docs/RESEARCH_NOTES.md` §8 says must be answered
before profitability can be claimed:

  1. Chronological 60/20/20 split — development / validation / test. The
     standing rejection criterion is sign instability: the net result must
     carry the same sign in all three folds.
  2. Expectancy per exit type (TP / SL / TIMEOUT / EOD). Per MQL5 art. 19211
     a negative-expectancy exit profile is eliminated, not tuned around, so
     each bucket is measured on its own and a leave-one-out counterfactual
     shows what the book looks like without it.
  3. Whether any of it survives costs — every figure here is net of the
     round-turn spread and commission the simulator charged.

Usage:
    python analyze_walkforward.py [results.json] [--md out.md]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

EXIT_BUCKETS = ["TP", "SL", "TIMEOUT", "EOD"]


# ── statistics ───────────────────────────────────────────────────────────────

def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _t_stat(xs: Sequence[float]) -> float:
    """
    Mean vs zero. |t| < 2 means the sample cannot distinguish it from noise.

    Returns 0.0 for a near-constant sample. The TP and SL buckets are exactly
    that — every TP banks ~+2R and every SL gives back ~-1R — so their standard
    error collapses and t explodes into a four-figure number that reads as
    overwhelming significance while actually measuring nothing but the target
    geometry. Only mixed buckets carry an interpretable t.
    """
    if len(xs) < 2:
        return 0.0
    sd = _stdev(xs)
    if sd <= abs(_mean(xs)) * 1e-3:
        return 0.0
    return _mean(xs) / (sd / math.sqrt(len(xs)))


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    gains = sum(p for p in pnls if p > 0)
    pains = -sum(p for p in pnls if p < 0)
    if pains == 0:
        return None if gains == 0 else float("inf")
    return gains / pains


def _max_drawdown(pnls: Sequence[float], start: float) -> Dict[str, float]:
    equity, peak, worst, worst_pct = start, start, 0.0, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = peak - equity
        if dd > worst:
            worst, worst_pct = dd, (dd / peak * 100.0 if peak else 0.0)
    return {"abs": round(worst, 2), "pct": round(worst_pct, 2)}


def _stats(trades: List[dict], start_balance: float) -> Dict:
    pnls = [t["pnl"] for t in trades]
    rs = [t.get("r_multiple", 0.0) for t in trades]
    wins = [p for p in pnls if p > 0]
    pf = _profit_factor(pnls)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "win_rate_pct": round(len(wins) / len(trades) * 100.0, 2) if trades else 0.0,
        "net_pnl": round(sum(pnls), 2),
        "mean_pnl": round(_mean(pnls), 4),
        "expectancy_R": round(_mean(rs), 4),
        "stdev_R": round(_stdev(rs), 4),
        "t_stat": round(_t_stat(rs), 2),
        "profit_factor": (round(pf, 3) if pf not in (None, float("inf")) else pf),
        "max_drawdown": _max_drawdown(pnls, start_balance),
        "first": trades[0]["open_time"][:10] if trades else "-",
        "last": trades[-1]["open_time"][:10] if trades else "-",
    }


# ── report sections ──────────────────────────────────────────────────────────

def survival(trades: List[dict]) -> Optional[Dict[str, float]]:
    """
    The break-even condition `RRR > (1 - WR) / WR` (MQL5 art. 18991).

    It must be evaluated against the *realised* payoff, not against the nominal
    TP1 multiple. Not every loser gives back a full 1R — timeout and EOD exits
    close somewhere inside the stop — and not every winner banks the full 2R.
    Comparing the requirement to a nominal 2.0 therefore mis-scores books that
    have non-stop exits, which is exactly the shape this system has.
    """
    wins = [t["r_multiple"] for t in trades if t["pnl"] > 0]
    losses = [abs(t["r_multiple"]) for t in trades if t["pnl"] <= 0]
    if not trades or not wins or not losses:
        return None
    wr = len(wins) / len(trades)
    avg_win, avg_loss = _mean(wins), _mean(losses)
    if wr <= 0 or avg_loss <= 0:
        return None
    return {
        "win_rate": wr * 100.0,
        "required_rrr": (1 - wr) / wr,
        "realised_rrr": avg_win / avg_loss,
        "avg_win_R": avg_win,
        "avg_loss_R": avg_loss,
    }


def folds(trades: List[dict], start_balance: float) -> Dict:
    """Chronological 60/20/20 over the trade sequence."""
    n = len(trades)
    if n < 3:
        return {}
    a, b = int(n * 0.60), int(n * 0.80)
    return {
        "development": _stats(trades[:a], start_balance),
        "validation": _stats(trades[a:b], start_balance),
        "test": _stats(trades[b:], start_balance),
    }


def exit_profiles(trades: List[dict], start_balance: float) -> Dict:
    out: Dict[str, Dict] = {}
    total_pnl = sum(t["pnl"] for t in trades)
    for bucket in EXIT_BUCKETS:
        subset = [t for t in trades if t.get("exit_reason") == bucket]
        if not subset:
            out[bucket] = {"trades": 0}
            continue
        s = _stats(subset, start_balance)
        s["share_pct"] = round(len(subset) / len(trades) * 100.0, 2)
        # Leave-one-out: the book with this exit profile removed entirely.
        rest = [t for t in trades if t.get("exit_reason") != bucket]
        s["book_without_this_bucket"] = {
            "trades": len(rest),
            "net_pnl": round(sum(t["pnl"] for t in rest), 2),
            "expectancy_R": round(_mean([t.get("r_multiple", 0.0) for t in rest]), 4),
            "delta_pnl": round(sum(t["pnl"] for t in rest) - total_pnl, 2),
        }
        out[bucket] = s
    return out


def group_by(trades: List[dict], key: str, start_balance: float) -> Dict:
    groups: Dict[str, List[dict]] = {}
    for t in trades:
        groups.setdefault(str(t.get(key, "?")), []).append(t)
    return {k: _stats(v, start_balance) for k, v in sorted(groups.items())}


# ── rendering ────────────────────────────────────────────────────────────────

def _row(label: str, s: Dict) -> str:
    pf = s.get("profit_factor")
    pf_s = "n/a" if pf is None else ("inf" if pf == float("inf") else f"{pf:.2f}")
    return (f"{label:<16} {s['trades']:>6} {s['win_rate_pct']:>8.2f}% "
            f"{s['net_pnl']:>11.2f} {s['expectancy_R']:>10.4f} {s['t_stat']:>7.2f} "
            f"{pf_s:>7} {s['max_drawdown']['pct']:>8.2f}%  {s['first']}..{s['last']}")


HEADER = (f"{'':<16} {'trades':>6} {'win rate':>9} {'net $':>11} "
          f"{'exp (R)':>10} {'t':>7} {'PF':>7} {'maxDD':>9}  period")


def render(results: Dict) -> str:
    trades = results["trades"]
    cfg = results["config"]
    start = float(cfg["sa_pool"])
    L: List[str] = []
    w = L.append

    w("=" * 108)
    w("SA-V2 WALK-FORWARD REPORT")
    w("=" * 108)
    w(f"Window        : {cfg['from'][:10]} .. {cfg['to'][:10]}")
    w(f"Symbols       : {', '.join(cfg['symbols'])}")
    w(f"Pool / risk   : ${start:,.2f} @ {cfg['risk_pct'] * 100:.1f}%   "
      f"max open {cfg['max_open_positions']}")
    # `spread_pips` was split into an observed value and a fallback when the
    # simulator moved to the broker's per-bar spread (§13.4). This reader was
    # not updated with it and raised KeyError on every result file written
    # since; prefer the observed figure, fall back to the configured floor.
    spread_desc = cfg.get("spread_pips_observed",
                          cfg.get("spread_pips_fallback",
                                  cfg.get("spread_pips", "?")))
    w(f"Costs charged : {spread_desc} pip round-turn spread"
      f" ({cfg.get('spread_source', 'unknown')} source)"
      f" + ${cfg.get('commission_per_lot', 0.0):.2f}/lot commission")
    if cfg.get("vp_gate_enabled"):
        w(f"VP gate       : ON  mode={cfg.get('vp_gate_mode')} "
          f"poc_band={cfg.get('vp_poc_band_frac')} "
          f"profile={cfg.get('vp_profile_bars')}x H4 / "
          f"{cfg.get('vp_target_bins')} rows")
    else:
        w("VP gate       : off")
    w(f"Cooldown      : {'on' if cfg.get('cooldown_enabled') else 'OFF'}"
      f" ({cfg.get('loss_cooldown_policy')}, win {cfg.get('win_cooldown_minutes')}m)"
      f"   whole-day window: {'on' if cfg.get('allow_whole_day') else 'off'}")
    w("")

    if not trades:
        w("NO TRADES. The gate chain rejected every candidate — see the")
        w("rejection ledger below. A zero-trade result is not a negative")
        w("result; it means this window produced no measurable sample.")
        w("")
        w("REJECTION LEDGER")
        w("-" * 108)
        for gate, n in results.get("rejections", {}).items():
            w(f"  {gate:<28} {n:>10,}")
        return "\n".join(L)

    overall = _stats(trades, start)
    w("OVERALL")
    w("-" * 108)
    w(HEADER)
    w(_row("full period", overall))
    sv = survival(trades)
    if sv:
        met = sv["realised_rrr"] > sv["required_rrr"]
        w("")
        w(f"Survival condition (RRR > (1-WR)/WR): a {sv['win_rate']:.2f}% win rate "
          f"requires RRR > {sv['required_rrr']:.2f}")
        w(f"  realised RRR  : {sv['realised_rrr']:.2f}  "
          f"(avg win {sv['avg_win_R']:+.2f}R vs avg loss -{sv['avg_loss_R']:.2f}R; "
          f"TP1 is nominally 2.0R)")
        w(f"  {'MET' if met else 'NOT MET'} — "
          + ("realised payoff clears the break-even requirement."
             if met else
             "realised payoff is below break-even for this win rate."))
    w("")

    w("WALK-FORWARD FOLDS (chronological 60/20/20)")
    w("-" * 108)
    w(HEADER)
    f = folds(trades, start)
    for name in ("development", "validation", "test"):
        if name in f:
            w(_row(name, f[name]))
    if f:
        signs = {name: (1 if f[name]["net_pnl"] > 0 else -1 if f[name]["net_pnl"] < 0 else 0)
                 for name in f}
        consistent = len(set(signs.values())) == 1 and 0 not in signs.values()
        w("")
        w(f"Sign by fold  : " + "  ".join(
            f"{k}={'+' if v > 0 else '-' if v < 0 else '0'}" for k, v in signs.items()))
        w(f"VERDICT       : {'CONSISTENT' if consistent else 'UNSTABLE'} — "
          + ("all three folds carry the same sign."
             if consistent else
             "sign flips across folds. Standing rejection criterion; do not promote."))
    w("")

    w("EXPECTANCY PER EXIT TYPE")
    w("-" * 108)
    w(f"{'':<16} {'trades':>6} {'share':>8} {'net $':>11} {'mean $':>10} "
      f"{'exp (R)':>10} {'t':>7}   book without it (net $ / exp R)")
    ep = exit_profiles(trades, start)
    for bucket in EXIT_BUCKETS:
        s = ep[bucket]
        if not s["trades"]:
            w(f"{bucket:<16} {0:>6}       —           —          —          —       —   —")
            continue
        wo = s["book_without_this_bucket"]
        w(f"{bucket:<16} {s['trades']:>6} {s['share_pct']:>7.2f}% "
          f"{s['net_pnl']:>11.2f} {s['mean_pnl']:>10.2f} {s['expectancy_R']:>10.4f} "
          f"{s['t_stat']:>7.2f}   {wo['net_pnl']:>10.2f} / {wo['expectancy_R']:>7.4f}")
    w("")
    negatives = [b for b in EXIT_BUCKETS
                 if ep[b]["trades"] and ep[b]["expectancy_R"] < 0 and b != "SL"]
    if negatives:
        w(f"Negative-expectancy exit profiles (excluding SL, which is negative by "
          f"construction): {', '.join(negatives)}")
        w("Per art. 19211 these are eliminated, not tuned around.")
    else:
        w("No non-structural exit profile carries negative expectancy.")
    w("")

    w("BY SYMBOL")
    w("-" * 108)
    w(HEADER)
    for k, s in group_by(trades, "symbol", start).items():
        w(_row(k, s))
    w("")

    w("BY TRIGGER")
    w("-" * 108)
    w(HEADER)
    for k, s in group_by(trades, "trigger_type", start).items():
        w(_row(k, s))
    w("")

    w("BY DIRECTION")
    w("-" * 108)
    w(HEADER)
    for k, s in group_by(trades, "direction", start).items():
        w(_row(k, s))
    w("")

    w("REJECTION LEDGER (why candidates did not become trades)")
    w("-" * 108)
    for gate, n in results.get("rejections", {}).items():
        w(f"  {gate:<28} {n:>10,}")
    w("")
    w("=" * 108)
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", nargs="?", default="logs/scalper_backtest_results.json")
    ap.add_argument("--md", type=str, default=None, help="also write the report to this file")
    args = ap.parse_args()

    # The Windows console defaults to cp1252 and will not encode box drawing
    # or arrows; the report is plain ASCII but be explicit anyway.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    path = Path(args.results)
    if not path.exists():
        sys.exit(f"Result file not found: {path}")
    results = json.loads(path.read_text(encoding="utf-8"))

    report = render(results)
    print(report)
    if args.md:
        out = Path(args.md)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("```\n" + report + "\n```\n", encoding="utf-8")
        print(f"\nWritten: {out}")


if __name__ == "__main__":
    main()
