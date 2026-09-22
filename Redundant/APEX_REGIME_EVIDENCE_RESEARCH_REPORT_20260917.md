# APEX Regime Evidence Research Report

**Date:** 2026-09-17  
**Status:** Instrumentation corrected; no trading logic changed. Fresh post-instrumentation observations are required before making an edge claim.

## Executive verdict

**Recommendation: IMPROVE_CURRENT_REGIME.** The active ICT context is deterministic and operational, but it partly repeats trigger evidence. The corrected two-stream path records every newly completed M15 bar independently of trigger existence, plus separate candidate evidence. HMM is not implemented or activated.

## What changed

* `apex_ai/scalper/regime_evidence.py` adds separate bar/candidate records, strict numeric sanitization, conflict detection, deterministic offline dwell/transition aggregation, admission matrices, integrity checks, and chronological 60/20/20 replay summaries.
* `apex_ai/intelligence/regime_engine.py` exposes already-computed ATR ratio, momentum, structure, and manipulation values as observation fields only.
* `apex_ai/scalper/sa_consultant.py` computes the existing statistical H1 classifier observationally; it cannot veto or alter a trade.
* `apex_ai/scalper_agent.py` records one market-bar snapshot before trigger detection and a separate candidate snapshot at the existing regime-gate boundary. Both recorders are guarded and have no return value consumed by decisions.
* `apex_ai/analyze_regime_evidence.py` reads `logs/regime_evidence.jsonl` and emits regime frequency, dwell, transitions, admission, ICT-vs-statistical contingency, and fold summaries.

The authoritative stream is `REGIME_BAR_SNAPSHOT`, emitted once per newly
completed M15 bar before trigger detection. `CANDIDATE_EVIDENCE_SNAPSHOT` is a
separate stream at the existing regime-gate boundary. Candidate rows never
contribute to market-bar counts or dwell. Duplicate conflicting bar states emit
`REGIME_SNAPSHOT_CONFLICT`; non-finite numeric values serialize as `null`.

## Existing measured evidence

| Source | Result | Limitation |
|---|---:|---|
| `logs/bt_baseline.json` | 281 trades; 129 wins; +$2,757.57; 74 regime-gate rejections | Trades do not store regime labels |
| `logs/backtest_20260515_20260821.json` | 198 trades; 65 wins; -$76.97; 464 regime-gate rejections | Trades do not store regime labels |
| `logs/sa_incidents.jsonl` | 332 incidents; 316 `UNKNOWN`, 16 `MANIPULATION` | Era-mixed and not sufficient for conditional expectancy |
| `logs/sa_execution_telemetry.jsonl` | 9 candidates/executions; all 9 `MANIPULATION` | Tiny, selected live sample |

The following are therefore **UNMEASURED** from historical artifacts: percentage of all bars in `TRANSITION` or `ROTATION`, median/mean/p90 dwell, transition matrix, accepted-vs-rejected forward outcomes, and incremental information after controlling for trigger type.

## Runtime architecture

```text
Closed MT5 bars
  ├─ M15 structure + displacement ─┐
  ├─ H1 liquidity + manipulation ──┼─> active ICT RegimeEngine
  └─ M5 confirmation / STB ────────┘             │
                                                 v
                                     trigger × regime whitelist
                                                 │
                                      validation → risk → execution

Closed H1 bars ─> statistical RegimeClassifier ─> observation only
```

The active labels are `EXPANSION`, `MANIPULATION`, `ROTATION`, and `TRANSITION`. The statistical labels are `TRENDING_UP`, `TRENDING_DOWN`, `RANGING`, and `VOLATILE`. `RD_GATE` and `VP_GATE` remain disabled.

## How to run the replay report

From `apex_ai`:

```powershell
py -3.14 -E analyze_regime_evidence.py --input logs/regime_evidence.jsonl --output logs/regime_evidence_report.json
```

Run after a sufficiently broad observation period. The report intentionally marks forward outcomes `UNLABELLED` unless a causal future-bar replay source is supplied; it never invents rejected-candidate geometry or outcomes.

## Acceptance questions

1. **What percentage becomes TRANSITION?** New authoritative bar snapshots will answer it; the old candidate-only stream could not.
2. **What percentage of candidates are rejected only because of TRANSITION?** The admission matrix will answer it for candidates reaching the regime gate.
3. **Would rejected candidates differ materially?** Not yet measurable; requires causal future-bar replay.
4. **How often is ROTATION?** Not yet measurable over all observations.
5. **Is MANIPULATION repeating sweep evidence?** Likely partly yes by construction: the active regime consumes H1 manipulation output while triggers also use sweep evidence. The new component fields enable ablation.
6. **Is EXPANSION repeating displacement?** Likely partly yes: `EXPANSION` requires ATR expansion and displacement momentum, while displacement is also a trigger gate/input.
7. **Does the regime gate add information after trigger type?** Unknown until fixed-candidate ablation across chronological folds.
8. **Does statistical H1 context add information?** Unknown; snapshots now record the contingency needed to test this without gating.
9. **Enough evidence to justify HMM?** No. First establish whether the current context and statistical classifier add independent information; HMM remains unjustified.

## Test results

Passed:

```text
py -3.14 -E -m unittest tests.test_regime_evidence -v
Ran 10 tests ... OK
```

The full suite ran 579 tests with 564 passing and 15 failures/errors, all attributable to pre-existing working-tree changes: removal of `SWEEP_REJECTION` from the trigger set and session-window changes. `HEAD` still contains `SWEEP_REJECTION` in `SATriggerEngine.ALL_TRIGGERS`, confirming those failures predate this telemetry patch. No live processes were restarted and no MT5 operation was performed.
