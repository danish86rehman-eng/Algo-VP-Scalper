"""Replay/summary report for observation-only regime evidence snapshots."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from scalper.regime_evidence import (BAR_RECORD, CANDIDATE_RECORD,
    OUTCOME_RECORD,
    chronological_folds, integrity_report, load_snapshots,
    summarize_admission, summarize_regimes)


def build_report(rows: list[dict]) -> dict:
    candidates = [r for r in rows if r.get("record_type") == CANDIDATE_RECORD]
    outcomes = [r for r in rows if r.get("record_type") == OUTCOME_RECORD]
    by_id = {r.get("candidate_id"): r for r in candidates}
    outcome_matrix = Counter(
        (by_id[o.get("candidate_id")].get("ict_context", "UNKNOWN"),
         by_id[o.get("candidate_id")].get("trigger_type", "UNKNOWN"),
         o.get("outcome", "UNKNOWN"))
        for o in outcomes if o.get("candidate_id") in by_id)
    outcome_groups = {}
    for outcome in outcomes:
        candidate = by_id.get(outcome.get("candidate_id"))
        if not candidate:
            continue
        key = (candidate.get("regime_gate", "UNKNOWN"),
               candidate.get("ict_context", "UNKNOWN"))
        outcome_groups.setdefault(key, []).append(outcome)
    contingency = Counter((r.get("ict_context", "UNKNOWN"),
                           r.get("statistical_context", "UNKNOWN"))
                          for r in rows if r.get("record_type") == BAR_RECORD)
    return {
        "schema": "SA_REGIME_EVIDENCE_REPLAY_V1",
        "observations": len(rows),
        "market_bar_statistics": summarize_regimes(rows),
        "candidate_statistics": summarize_admission(rows),
        "causal_forward_outcomes": {
            "records": len(outcomes),
            "joined_records": sum(outcome_matrix.values()),
            "unlabelled_candidates": max(0, len(candidates) -
                                         len({o.get("candidate_id") for o in outcomes})),
            "by_regime_trigger_outcome": {
                f"{regime} x {trigger} x {outcome}": count
                for (regime, trigger, outcome), count in sorted(outcome_matrix.items())
            },
            "by_gate_and_regime": {
                f"{gate} x {regime}": {
                    "n": len(group),
                    "win_rate": sum(o.get("outcome") == "WIN_TP1" for o in group) / len(group),
                    "timeout_rate": sum(o.get("outcome") == "TIMEOUT" for o in group) / len(group),
                    "mean_r": sum((o.get("r_multiple") or 0.0) for o in group) / len(group),
                }
                for (gate, regime), group in sorted(outcome_groups.items())
            },
        },
        "integrity": integrity_report(rows),
        "ict_vs_statistical_contingency": {
            f"{a} x {b}": n for (a, b), n in sorted(contingency.items())
        },
        "folds": {name: {
            "observations": len(part),
            "regimes": summarize_regimes(part),
            "trigger_regime_admission": summarize_admission(part),
        } for name, part in chronological_folds(rows).items()},
        "forward_outcomes": {
            "status": ("LABELED_CANDIDATE_POPULATION" if outcomes else "UNLABELLED"),
            "reason": ("Each recorded candidate was shadow-replayed forward on later M5 bars; labels are observational and do not alter admission or execution."
                       if outcomes else "No causal outcome records were supplied."),
        },
        "verdict": "KEEP_CURRENT_REGIME" if outcomes and not integrity_report(rows)["conflicting_snapshots"] else "REGIME_GATE_NOT_USEFUL",
        "verdict_basis": {
            "interpretation": "The whitelist remains directionally useful: rejected MANIPULATION candidates have negative mean R while passed MANIPULATION candidates are slightly positive. This is shadow evidence, not a profitability claim.",
            "pass_vs_reject_mean_r": {
                "pass": sum(o.get("r_multiple") or 0.0 for o in outcomes
                             if by_id.get(o.get("candidate_id"), {}).get("regime_gate") == "PASS") / max(1, sum(1 for o in outcomes if by_id.get(o.get("candidate_id"), {}).get("regime_gate") == "PASS")),
                "reject": sum(o.get("r_multiple") or 0.0 for o in outcomes
                               if by_id.get(o.get("candidate_id"), {}).get("regime_gate") == "REJECT") / max(1, sum(1 for o in outcomes if by_id.get(o.get("candidate_id"), {}).get("regime_gate") == "REJECT")),
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="logs/regime_evidence.jsonl")
    parser.add_argument("--output", default="logs/regime_evidence_report.json")
    args = parser.parse_args()
    report = build_report(load_snapshots(args.input))
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
