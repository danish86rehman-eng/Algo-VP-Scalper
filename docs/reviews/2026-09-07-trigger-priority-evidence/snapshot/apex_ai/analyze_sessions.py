"""
Per-session attribution for a backtest result file.

Answers one question: which kill-zone window is losing money, and is that
answer stable across the chronological 60/20/20 folds. A session that is
negative in the full period but flips sign between folds is noise, not a
session to switch off — the same bar the strategy itself has to clear.

Usage:
    python analyze_sessions.py logs/backtest_20260515_20260821.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from typing import Dict, List

from scalper.session_checker import SESSION_WINDOWS

# Grouping used for the "drop a session" question. The two London windows are
# reported separately and also as one block, because PRE_LONDON exists only to
# catch the liquidity grab ahead of LONDON_OPEN — switching one off without the
# other is not a coherent choice.
BLOCKS = {
    "LONDON_OPEN": "LONDON",
    "PRE_LONDON": "LONDON",
    "LONDON_NY": "LONDON_NY",
    "TOKYO_OPEN": "ASIA",
    "NY_LUNCH_REV": "NY",
}


def window_of(ts: str) -> str:
    t = datetime.fromisoformat(ts).timetz()
    for w in SESSION_WINDOWS:
        if w.start <= t.replace(tzinfo=None) < w.end:
            return w.name
    return "OUTSIDE"


def stats(trades: List[dict]) -> Dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "net": 0.0, "wr": 0.0, "pf": None, "expR": 0.0}
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p <= 0]
    gross_loss = sum(losses)
    rs = [t.get("r_multiple", 0.0) for t in trades]
    return {
        "trades": n,
        "net": round(sum(pnls), 2),
        "wr": round(len(wins) / n * 100, 2),
        "pf": round(sum(wins) / gross_loss, 2) if gross_loss > 0 else None,
        "expR": round(sum(rs) / n, 4),
    }


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "logs/backtest_results.json"
    data = json.loads(open(path, encoding="utf-8").read())
    trades = sorted(data["trades"], key=lambda t: t["open_time"])
    n = len(trades)
    a, b = int(n * 0.60), int(n * 0.80)
    fold_of = {}
    for idx, t in enumerate(trades):
        fold_of[id(t)] = "dev" if idx < a else ("val" if idx < b else "test")

    for t in trades:
        t["_win"] = window_of(t["open_time"])
        t["_block"] = BLOCKS.get(t["_win"], t["_win"])

    print(f"File   : {path}")
    print(f"Trades : {n}   net {data['summary']['net_pnl']}   "
          f"WR {data['summary']['win_rate_pct']}%")
    print()

    for level in ("_win", "_block"):
        title = "BY SESSION WINDOW" if level == "_win" else "BY SESSION BLOCK"
        print(title)
        print(f"{'session':<14}{'trades':>7}{'net $':>10}{'WR%':>8}{'PF':>7}"
              f"{'exp R':>9}   {'dev':>9} {'val':>9} {'test':>9}  signs")
        groups: Dict[str, List[dict]] = defaultdict(list)
        for t in trades:
            groups[t[level]].append(t)
        for name, grp in sorted(groups.items(), key=lambda kv: stats(kv[1])["net"]):
            s = stats(grp)
            fs = {}
            for f in ("dev", "val", "test"):
                sub = [t for t in grp if fold_of[id(t)] == f]
                fs[f] = stats(sub)
            signs = "".join(
                "0" if fs[f]["trades"] == 0 else ("+" if fs[f]["net"] > 0 else "-")
                for f in ("dev", "val", "test"))
            pf = "n/a" if s["pf"] is None else f"{s['pf']:.2f}"
            print(f"{name:<14}{s['trades']:>7}{s['net']:>10.2f}{s['wr']:>8.2f}{pf:>7}"
                  f"{s['expR']:>9.4f}   "
                  f"{fs['dev']['net']:>9.2f} {fs['val']['net']:>9.2f} "
                  f"{fs['test']['net']:>9.2f}  {signs}")
        print()

    # Counterfactual: what the book looks like with one block removed. Naive —
    # it ignores the cooldown and position-cap interactions a real re-run would
    # capture, so it is a screening number, not a result.
    print("COUNTERFACTUAL — drop one block (naive, ignores cooldown/cap coupling)")
    blocks = sorted({t["_block"] for t in trades})
    for blk in blocks:
        kept = [t for t in trades if t["_block"] != blk]
        s = stats(kept)
        fs = {f: stats([t for t in kept if fold_of[id(t)] == f])
              for f in ("dev", "val", "test")}
        signs = "".join(
            "0" if fs[f]["trades"] == 0 else ("+" if fs[f]["net"] > 0 else "-")
            for f in ("dev", "val", "test"))
        pf = "n/a" if s["pf"] is None else f"{s['pf']:.2f}"
        print(f"  without {blk:<10} {s['trades']:>5} trades  net {s['net']:>9.2f}  "
              f"WR {s['wr']:>6.2f}%  PF {pf:>5}  folds {signs}")


if __name__ == "__main__":
    main()
