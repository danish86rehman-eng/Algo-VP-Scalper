"""
analyze_incidents.py — turn the forensic stream into a ranked diagnosis.

Two modes:

  --backfill   Reconstruct incidents for trades that closed BEFORE the
               forensics layer existed. Reads `logs/scalper_log.json` for the
               decision context, pulls the confirmation-frame bars each trade
               lived through from MT5, and runs the same `postmortem.analyse`
               the live agent runs. This is what makes the existing loss book
               readable instead of only future trades.

  (default)    Aggregate `logs/sa_incidents.jsonl` into `logs/sa_diagnosis.md`
               plus a JSON sibling: failure modes ranked by what they cost,
               with sample sizes attached to every claim.

The report ranks by DOLLARS LOST, not by frequency. The most common failure
mode is rarely the most expensive one, and the cheap-but-frequent mode is the
one a naive reading of a log tempts you to fix first.

Sample sizes are printed next to every number and modes below MIN_ACTIONABLE_N
are labelled as such. A diagnosis is a pointer for research, never a promotion:
CLAUDE.md 13.5 still requires a chronological 60/20/20 walk-forward with a
consistent sign before anything changes in the live configuration.

Usage:
    py -3.14 -E analyze_incidents.py --backfill
    py -3.14 -E analyze_incidents.py
    py -3.14 -E analyze_incidents.py --since 2026-07-01 --symbol XAUUSD
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))

from core.constants import OUTCOME_UNRECONCILED
from scalper import postmortem as PM
from scalper import decision_params as DP

#: Below this many samples a failure mode is reported but explicitly marked
#: as too small to act on. Not a statistical test — a guard against the
#: strong pull to redesign a strategy around three bad trades.
MIN_ACTIONABLE_N = 20


# ── Backfill ─────────────────────────────────────────────────────────────────

def _direction_from_record(rec: Dict[str, Any]) -> str:
    """
    The legacy trade log never stored direction. Recover it from geometry:
    a long's target sits above its entry, a short's below.
    """
    entry = float(rec.get("entry_price", 0.0) or 0.0)
    tp1 = float(rec.get("tp1_target", 0.0) or 0.0)
    sl = float(rec.get("stop_loss", 0.0) or 0.0)
    if tp1 and entry:
        return "BULLISH" if tp1 > entry else "BEARISH"
    if sl and entry:
        return "BULLISH" if sl < entry else "BEARISH"
    return "UNKNOWN"


def _normalise_outcome(result: str) -> str:
    """Map legacy result labels onto the taxonomy's expected vocabulary."""
    result = (result or "").upper()
    if result.startswith("WIN"):
        return result
    if result == "TIMEOUT":
        return "TIMEOUT"
    # EOD_CLOSE is a forced exit, not a stop — it belongs with the timeout
    # profile, which CLAUDE.md 13.3 requires to be measured on its own.
    if result == "EOD_CLOSE":
        return "TIMEOUT"
    return "LOSS"


def _broker_pnl(mt5, ticket: int) -> Optional[float]:
    """
    Settled P&L for a position from the broker's deal history.

    The trade log is not the account of record and has demonstrably failed to
    book closes: on the first backfill of this repository, 140 of 282 records
    carried $0, and spot-checking 60 of them against the broker found a real
    P&L for every single one (median +$9.37, range -$74.00 to +$88.80). Taking
    the log at face value would have mislabelled profitable trades as losses
    and then diagnosed the resulting phantom as an exit defect.

    Commission and swap are included — `deal.profit` alone is the gross figure
    and disagrees with what the pool ledger books for the same event.
    """
    try:
        deals = mt5.history_deals_get(position=ticket)
        if not deals:
            return None
        closing = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
        if not closing:
            return None
        return float(sum(d.profit + d.commission + d.swap for d in closing))
    except Exception:
        return None


def backfill(trade_log: Path, out: Path, lookahead_bars: int,
             verbose: bool = True) -> int:
    """Replay every closed trade in the legacy log. Returns incidents written."""
    import MetaTrader5 as mt5
    import pandas as pd
    from dotenv import load_dotenv
    import os

    load_dotenv(Path(__file__).parent / ".env")
    login = os.getenv("MT5_LOGIN")
    if not mt5.initialize(login=int(login) if login else None,
                          password=os.getenv("MT5_PASSWORD"),
                          server=os.getenv("MT5_SERVER")):
        print(f"MT5 initialize failed: {mt5.last_error()}", file=sys.stderr)
        return 0

    try:
        records = json.loads(trade_log.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {trade_log}: {exc}", file=sys.stderr)
        mt5.shutdown()
        return 0

    closed = [r for r in records
              if r.get("result") not in (None, "", "OPEN")
              and r.get("open_time") and r.get("close_time")]
    if not closed:
        print("no closed trades to backfill")
        mt5.shutdown()
        return 0

    # One bar pull per symbol covering the whole span, sliced per trade.
    # Per-trade fetches would be hundreds of round trips for the same data.
    by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in closed:
        by_symbol[r.get("instrument", "UNKNOWN")].append(r)

    journal = PM.IncidentJournal(str(out))
    written = 0
    reconciled = 0      # positions the broker could price
    repriced = 0        # ...where the broker disagreed with the trade log
    unpriced = 0        # marked UNRECONCILED and still unpriceable

    for symbol, recs in by_symbol.items():
        times = [datetime.fromisoformat(r["open_time"]) for r in recs]
        ends = [datetime.fromisoformat(r["close_time"]) for r in recs]
        start = min(times) - timedelta(hours=6)
        end = max(ends) + timedelta(minutes=lookahead_bars * DP.CONFIRM_TF_MINUTES + 60)

        rates = mt5.copy_rates_range(symbol, DP.TF_CONFIRM, start, end)
        if rates is None or len(rates) == 0:
            print(f"  {symbol}: no bars returned — skipping {len(recs)} trades",
                  file=sys.stderr)
            continue
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        if verbose:
            print(f"  {symbol}: {len(df)} M5 bars, {len(recs)} closed trades")

        for r in recs:
            opened = datetime.fromisoformat(r["open_time"])
            closed_at = datetime.fromisoformat(r["close_time"])
            entry = float(r.get("entry_price", 0.0) or 0.0)
            sl = float(r.get("stop_loss", 0.0) or 0.0)

            # Broker first, trade log only as a fallback. See _broker_pnl.
            logged_pnl = float(r.get("sa_pool_pnl", 0.0) or 0.0)
            settled = _broker_pnl(mt5, int(r.get("ticket") or 0))

            # A row the live agent itself marked UNRECONCILED carries a $0.00
            # placeholder, not a measurement. If the broker still cannot price
            # it there is nothing to analyse, and falling through would relabel
            # it LOSS — reinstating in the analysis path exactly the "loss by
            # construction" defect the label was introduced to end.
            if (settled is None
                    and (r.get("result") or "").upper() == OUTCOME_UNRECONCILED):
                unpriced += 1
                continue

            pnl = settled if settled is not None else logged_pnl
            if settled is not None:
                reconciled += 1
                if abs(settled - logged_pnl) > 0.01:
                    repriced += 1

            # The exit LABEL must follow the money too. `_on_trade_closed`
            # derives WIN/LOSS from the P&L it was handed, so a trade the log
            # booked at $0 was recorded as a LOSS regardless of what it
            # actually made. Left uncorrected, a profitable trade that peaked
            # at 1.2R gets classified GAVE_BACK_WINNER and invents an exit
            # defect that never happened.
            outcome = _normalise_outcome(r.get("result", ""))
            if settled is not None and outcome != "TIMEOUT":
                outcome = "WIN_TP1" if settled > 0 else "LOSS"

            # Risk in dollars was never logged. Recover it from the geometry
            # actually traded: (entry-SL) in price x lots x contract value.
            # Where that is unavailable, leave it zero — exit_r then reads 0
            # and the R-based measures still work, since MFE/MAE come from
            # price rather than from P&L.
            risk_usd = _infer_risk_usd(mt5, symbol, entry, sl,
                                       float(r.get("position_size", 0.0) or 0.0))

            ctx = PM.TradeContext(
                ticket=int(r.get("ticket") or 0),
                symbol=symbol,
                direction=_direction_from_record(r),
                entry=entry,
                stop_loss=sl,
                tp1=float(r.get("tp1_target", 0.0) or 0.0),
                outcome=outcome,
                pnl_usd=pnl,
                open_time=opened,
                close_time=closed_at,
                trigger_type=r.get("trigger_type", "UNKNOWN"),
                session=r.get("session_window", "UNKNOWN"),
                regime="UNKNOWN",          # not recorded by the legacy logger
                confidence="UNKNOWN",
                lots=float(r.get("position_size", 0.0) or 0.0),
                risk_usd=risk_usd,
                spread_pips=0.0,           # not recorded at entry historically
                sl_pips=0.0,
            )
            pm = PM.analyse(ctx, df, bar_minutes=DP.CONFIRM_TF_MINUTES,
                            lookahead_bars=lookahead_bars)
            journal.record(pm)
            written += 1

    if verbose and reconciled:
        print(f"  reconciled {reconciled} positions against broker deal "
              f"history; {repriced} disagreed with the trade log and were "
              f"repriced")
    if verbose and unpriced:
        print(f"  skipped {unpriced} {OUTCOME_UNRECONCILED} trades the broker "
              f"still cannot price — they carry a placeholder, not a result")
    mt5.shutdown()
    return written


def _infer_risk_usd(mt5, symbol: str, entry: float, sl: float,
                    lots: float) -> float:
    """Dollar risk implied by the stop distance and the traded volume."""
    try:
        info = mt5.symbol_info(symbol)
        if not info or lots <= 0 or entry <= 0 or sl <= 0:
            return 0.0
        sl_points = abs(entry - sl) / info.point
        return float(sl_points * info.trade_tick_value * lots)
    except Exception:
        return 0.0


# ── Aggregation ──────────────────────────────────────────────────────────────

def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _median(xs: List[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Group incidents by failure mode and by the dimensions worth slicing."""
    by_mode: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_mode[r.get("failure_mode", "UNCLASSIFIED")].append(r)

    modes = []
    for mode, group in by_mode.items():
        pnls = [float(r.get("pnl_usd", 0.0)) for r in group]
        # A trade whose closing deal never settled is booked at $0. Those
        # records are real trades with real excursions, so they belong in the
        # count, but averaging $0 into a dollar figure silently dilutes it —
        # 59 of 61 GAVE_BACK_WINNER incidents in the first backfill were
        # unpriced, which made a 61-trade defect read as a $22 problem.
        priced = [p for p in pnls if p != 0.0]
        modes.append({
            "mode": mode,
            "n": len(group),
            "n_priced": len(priced),
            "share_pct": 0.0,                # filled once the total is known
            "total_pnl": round(sum(priced), 2),
            "mean_pnl": round(_mean(priced), 2),
            "mean_mfe_r": round(_mean([float(r.get("mfe_r", 0)) for r in group]), 2),
            "mean_mae_r": round(_mean([float(r.get("mae_r", 0)) for r in group]), 2),
            "median_bars_held": round(_median(
                [float(r.get("bars_held", 0)) for r in group]), 1),
            "cost_heavy_n": sum(1 for r in group if r.get("cost_heavy")),
            "actionable": len(group) >= MIN_ACTIONABLE_N,
            "description": PM.FAILURE_MODES.get(mode, ""),
            "by_session": _count_pnl(group, "session"),
            "by_trigger": _count_pnl(group, "trigger_type"),
            "by_symbol": _count_pnl(group, "symbol"),
        })

    total_n = len(rows) or 1
    for m in modes:
        m["share_pct"] = round(100.0 * m["n"] / total_n, 1)

    # Rank by damage: most negative total P&L first. A frequent-but-cheap mode
    # must not outrank a rare-but-expensive one.
    modes.sort(key=lambda m: m["total_pnl"])

    all_pnls = [float(r.get("pnl_usd", 0.0)) for r in rows]
    priced = [p for p in all_pnls if p != 0.0]
    wins = [p for p in priced if p > 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(p for p in priced if p < 0))
    stamps = sorted(str(r.get("open_time", "")) for r in rows if r.get("open_time"))

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_incidents": len(rows),
        "n_priced": len(priced),
        "unpriced_n": len(all_pnls) - len(priced),
        "unpriced_pct": round(100.0 * (len(all_pnls) - len(priced))
                              / len(all_pnls), 1) if all_pnls else 0.0,
        "first_open": stamps[0][:19] if stamps else "",
        "last_open": stamps[-1][:19] if stamps else "",
        "symbols": sorted({str(r.get("symbol", "?")) for r in rows}),
        "net_pnl": round(sum(priced), 2),
        "win_rate_pct": round(100.0 * len(wins) / len(priced), 2) if priced else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else 0.0,
        "unclassified_n": sum(1 for r in rows
                              if r.get("failure_mode") == "UNCLASSIFIED"),
        "modes": modes,
        "min_actionable_n": MIN_ACTIONABLE_N,
    }


def _count_pnl(group: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    """n and total P&L per distinct value of `key`, worst first."""
    acc: Dict[str, List[float]] = defaultdict(list)
    for r in group:
        acc[str(r.get(key, "UNKNOWN"))].append(float(r.get("pnl_usd", 0.0)))
    out = {k: {"n": len(v), "pnl": round(sum(v), 2)} for k, v in acc.items()}
    return dict(sorted(out.items(), key=lambda kv: kv[1]["pnl"]))


# ── Report ───────────────────────────────────────────────────────────────────

def render(summary: Dict[str, Any]) -> str:
    L: List[str] = []
    L.append("# Scalper self-diagnosis")
    L.append("")
    L.append(f"Generated {summary['generated_utc']} · "
             f"{summary['n_incidents']} incidents · "
             f"{summary['first_open'][:10]} → {summary['last_open'][:10]} · "
             f"{', '.join(summary['symbols'])}")
    L.append("")
    L.append(f"Priced trades {summary['n_priced']} · "
             f"net ${summary['net_pnl']:+.2f} · "
             f"win rate {summary['win_rate_pct']:.2f}% · "
             f"PF {summary['profit_factor']:.2f}")
    L.append("")
    L.append("> Ranked by dollars lost, not by frequency. A diagnosis points "
             "research at a defect; it does not authorise a change. "
             "CLAUDE.md 13.5 still governs promotion: chronological 60/20/20, "
             "same sign in all three folds.")
    L.append("")

    # Data quality comes before the findings, because it decides which of
    # them can be read as dollars and which only as counts.
    if summary["unpriced_n"]:
        L.append("## Data quality — read this before the table")
        L.append("")
        L.append(f"**{summary['unpriced_n']} of {summary['n_incidents']} "
                 f"incidents ({summary['unpriced_pct']:.1f}%) are booked at "
                 f"$0** — the closing deal never settled into the trade log, "
                 f"so their P&L was never captured.")
        L.append("")
        L.append("Consequences, both of which bite:")
        L.append("")
        L.append("- Every dollar figure below rests on the **priced** subset "
                 "only (`n priced` column). A mode can be frequent and still "
                 "show a small dollar total purely because most of its trades "
                 "are unpriced. Read `n` for how often it happens and "
                 "`n priced` for how much the money column is worth.")
        L.append("- Excursion measures (MFE, MAE, mode assignment) are "
                 "derived from **price**, not from P&L, so they are unaffected "
                 "and every incident counts toward them.")
        L.append("")
        if summary["unpriced_pct"] >= 20:
            L.append("Reconcile against the broker with `get_agent_pnl.py` "
                     "before quoting any dollar figure from this report "
                     "externally.")
            L.append("")
    if len(summary["symbols"]) > 1:
        L.append(f"*Mixed book: {len(summary['symbols'])} instruments across "
                 f"{summary['first_open'][:10]} → {summary['last_open'][:10]}, "
                 f"a span that crosses configuration changes (M15/M5 migration "
                 f"and the 2R geometry landed 2026-08-22). Aggregate win rate "
                 f"and PF describe the mixture, not the shipping strategy — "
                 f"use `--since` and `--symbol` to isolate one era. The "
                 f"failure-mode distribution is the durable part.*")
        L.append("")

    # The survival condition, restated against what was actually measured.
    wr = summary["win_rate_pct"] / 100.0
    if 0 < wr < 1:
        required = (1 - wr) / wr
        L.append(f"**Survival condition** — at the measured {wr*100:.2f}% win "
                 f"rate, RRR must exceed **{required:.2f}**. Shipping targets: "
                 f"TP1 {DP_TP1():.1f}R, TP2 {DP_TP2():.1f}R.")
        L.append("")

    L.append("## Failure modes")
    L.append("")
    L.append("| Mode | n | n priced | share | total $ | mean $ | mean MFE | mean MAE | actionable |")
    L.append("|---|--:|--:|--:|--:|--:|--:|--:|:--|")
    floor = summary["min_actionable_n"]
    for m in summary["modes"]:
        verdict = "yes" if m["actionable"] else f"no — n < {floor}"
        L.append(
            f"| `{m['mode']}` | {m['n']} | {m['n_priced']} | "
            f"{m['share_pct']:.1f}% | "
            f"{m['total_pnl']:+.2f} | {m['mean_pnl']:+.2f} | "
            f"{m['mean_mfe_r']:.2f}R | {m['mean_mae_r']:.2f}R | {verdict} |")
    L.append("")

    if summary["unclassified_n"]:
        L.append(f"*{summary['unclassified_n']} incident(s) could not be "
                 f"classified — usually missing bars for the holding window.*")
        L.append("")

    L.append("## Where each mode concentrates")
    L.append("")
    for m in summary["modes"]:
        if m["mode"] not in PM.ACTIONABLE_MODES:
            continue
        # Costs money, OR happens often enough to matter while being too
        # sparsely priced for the dollar column to show it. The second case is
        # the one a dollar-only filter would hide.
        thin_pricing = m["n"] >= MIN_ACTIONABLE_N and m["n_priced"] * 2 < m["n"]
        if m["total_pnl"] >= 0 and not thin_pricing:
            continue
        L.append(f"### `{m['mode']}` — {m['total_pnl']:+.2f} over "
                 f"{m['n']} trades ({m['n_priced']} priced)")
        L.append("")
        L.append(f"{m['description']}")
        L.append("")
        if thin_pricing:
            L.append(f"**Surfaced on frequency, not on cost.** Only "
                     f"{m['n_priced']} of {m['n']} occurrences carry a booked "
                     f"P&L, so the dollar total understates this by an unknown "
                     f"factor. Treat `n` as the finding.")
            L.append("")
        if not m["actionable"]:
            L.append(f"**Sample too small to act on** "
                     f"(n={m['n']} < {summary['min_actionable_n']}). "
                     f"Recorded for accumulation, not for a change.")
            L.append("")
        for label, key in (("session", "by_session"), ("trigger", "by_trigger"),
                           ("symbol", "by_symbol")):
            parts = [f"{k} (n={v['n']}, ${v['pnl']:+.2f})"
                     for k, v in list(m[key].items())[:5]]
            if parts:
                L.append(f"- Worst by {label}: " + "; ".join(parts))
        L.append("")

    L.append("## Next step")
    L.append("")
    L.append("Take the top actionable mode into `docs/REMEDY_KB.md`, pick or "
             "research a candidate remedy, and open a row in "
             "`docs/REMEDY_LEDGER.md`. The remedy is measured by a backtest "
             "arm before it is enabled anywhere.")
    L.append("")
    return "\n".join(L)


def DP_TP1() -> float:
    from scalper.trigger_engine import SATriggerEngine
    return SATriggerEngine.TP1_R


def DP_TP2() -> float:
    from scalper.trigger_engine import SATriggerEngine
    return SATriggerEngine.TP2_R


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--incidents", default="logs/sa_incidents.jsonl")
    ap.add_argument("--trade-log", default="logs/scalper_log.json")
    ap.add_argument("--out", default="logs/sa_diagnosis.md")
    ap.add_argument("--backfill", action="store_true",
                    help="reconstruct incidents from the legacy trade log via MT5")
    ap.add_argument("--lookahead-bars", type=int, default=24,
                    help="confirmation bars to inspect past each exit")
    ap.add_argument("--since", default=None, help="ISO date lower bound")
    ap.add_argument("--symbol", default=None, help="restrict to one symbol")
    args = ap.parse_args()

    inc_path = Path(args.incidents)

    if args.backfill:
        if inc_path.exists():
            backup = inc_path.with_suffix(".jsonl.bak")
            inc_path.replace(backup)
            print(f"existing incidents moved to {backup}")
        n = backfill(Path(args.trade_log), inc_path, args.lookahead_bars)
        print(f"backfilled {n} incidents -> {inc_path}")

    rows = PM.IncidentJournal(str(inc_path)).load()
    if args.since:
        rows = [r for r in rows if str(r.get("open_time", "")) >= args.since]
    if args.symbol:
        rows = [r for r in rows if r.get("symbol") == args.symbol]

    if not rows:
        print("no incidents to analyse — run with --backfill, or let the "
              "agent accumulate closed trades first")
        return 1

    summary = aggregate(rows)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(summary), encoding="utf-8")
    out.with_suffix(".json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n{summary['n_incidents']} incidents "
          f"({summary['n_priced']} priced, {summary['unpriced_n']} booked at $0) "
          f"· net ${summary['net_pnl']:+.2f} "
          f"· WR {summary['win_rate_pct']:.2f}% · PF {summary['profit_factor']:.2f}")
    print(f"{'mode':<20}{'n':>5}{'priced':>8}{'total $':>12}"
          f"{'mean MFE':>10}{'mean MAE':>10}")
    for m in summary["modes"]:
        print(f"{m['mode']:<20}{m['n']:>5}{m['n_priced']:>8}"
              f"{m['total_pnl']:>12.2f}"
              f"{m['mean_mfe_r']:>9.2f}R{m['mean_mae_r']:>9.2f}R")
    print(f"\nreport -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
