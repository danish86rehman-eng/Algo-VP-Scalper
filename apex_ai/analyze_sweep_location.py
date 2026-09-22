"""Group observation-only sweep outcomes; never used by the trading path."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def rows(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def summarize(path: Path) -> dict:
    records = [r for r in rows(path)
               if r.get("event") in {"CANDIDATE_OBSERVED", "ENTRY_EXECUTION", "EXIT_SETTLED"}
               or r.get("kind") == "selection"]
    groups = defaultdict(Counter)
    for row in records:
        ctx = row.get("sweep_location_context") or row.get("location_context") or {}
        if not ctx and row.get("trigger_type") != "SWEEP_REJECTION":
            continue
        key = (
            ctx.get("liquidity_type", "UNKNOWN"),
            ctx.get("session_level_type"),
            ctx.get("htf_swing_type"),
            ctx.get("fvg_timeframe"),
            ctx.get("ob_timeframe"),
            ctx.get("vp_reference_type"),
            ctx.get("premium_discount"),
            "+".join(ctx.get("matched_triggers", ())),
            row.get("entry_session"), row.get("direction"),
            row.get("chronological_fold", "UNKNOWN"),
        )
        groups[key][row.get("event", row.get("kind", "UNKNOWN"))] += 1
    return {"observation_only": True, "groups": [
        {"liquidity_type": k[0], "session_liquidity": k[1],
         "htf_swing": k[2], "fvg": k[3], "ob": k[4], "vp": k[5],
         "premium_discount": k[6], "matched_triggers": k[7],
         "session": k[8], "direction": k[9], "chronological_fold": k[10],
         "counts": dict(v)}
        for k, v in sorted(groups.items(), key=lambda item: str(item[0]))
    ]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path, default=Path("logs/sa_execution_telemetry.jsonl"), nargs="?")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = summarize(args.path)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
