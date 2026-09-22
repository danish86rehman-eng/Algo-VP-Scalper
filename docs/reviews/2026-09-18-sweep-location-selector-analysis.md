# Sweep Location and Selector Analysis

**Generated:** 2026-09-18 UTC  
**Scope:** XAUUSD `SWEEP_REJECTION` location telemetry and first-match selector  
**Mode:** Analysis only; no live strategy, execution, risk, session, or order behavior changed.

## 1. Executive verdict

The replay is not sufficient to support a location permission, location grade, or selector change.

The current replay produced 18,031 raw sweep detections and 6,011 stable-ID sweep candidates, but **zero executed sweep trades**. Every candidate was rejected before order submission by the existing downstream chain, predominantly reclaim waits and geometry/cost gates. Therefore no realized-R, MFE, MAE, win-rate, profit-factor, SL/TP/timeout, or fold-stability claim can be made from this run.

The location telemetry also produced **zero `UNKNOWN_LOCAL` candidates**. The current exact-level identity logic classified 5,967 candidates as M15 equal highs/lows and 44 as Asia session highs/lows. Because the required anonymous control population is absent, named-vs-anonymous effect size is not identifiable.

## 2. Baseline fingerprint

Combined SHA-256 fingerprint of the frozen replay inputs and telemetry implementation:

```text
335ce8e90c554caf2362dc59b2dc1b403ee0473ccae08bb108a449596e6e90ee
```

The fingerprint covers `config.json`, `scalper/decision_params.py`, `scalper/trigger_engine.py`, `scalper_agent.py`, `backtest_scalper.py`, `scalper/sweep_location.py`, and `scalper/candidate_funnel.py`.

Frozen baseline facts:

- Current production `ALL_TRIGGERS` omits `SWEEP_REJECTION`, although the detector remains implemented and historical evidence contains sweep results.
- The replay therefore used a compatibility shim inside the analysis process only to expose the existing detector; the production source and runtime configuration were not changed.
- First-match selection, sweep confidence, reclaim, STB, consultant, cost, CRG, sizing, sessions, and exits were left unchanged.
- Replay trigger set: `SWEEP_REJECTION`, `BOS_RETEST`, `JUDAS`, `FVG_FILL`, and `VALUE_AREA_FADE`.

## 3. Dataset and fold integrity

Source bars were the archived XAUUSD MT5 data under `docs/reviews/2026-09-07-trigger-priority-evidence/data/`.

| Frame | Available range | Total bars | Bars in replay window |
|---|---:|---:|---:|
| M5 | 2026-01-15 → 2026-09-07 | 45,634 | 36,877 |
| M15 | 2026-01-15 → 2026-09-07 | 15,214 | 12,293 |
| H1 | 2026-01-15 → 2026-09-07 | 3,806 | 3,074 |
| H4 | 2026-01-15 → 2026-09-07 | 1,029 | 830 |
| D1 | 2025-01-26 → 2026-09-07 | 504 | 162 |

Replay scoring window: **2026-03-01 00:00 UTC through 2026-09-07 00:00 UTC**. The March start supplies the configured 45-day warm-up from the earliest H1/H4 data.

Chronological folds:

- Discovery: 2026-03-01 → 2026-06-23 — 3,562 unique sweep candidates
- Validation: 2026-06-23 → 2026-07-31 — 1,270 candidates
- Confirmation: 2026-07-31 → 2026-09-07 — 1,179 candidates

Weekend/market-closure gaps were present in all intraday frames; no frame was silently substituted or randomly split. Session-sweep replay was disabled because the archived bundle contains no M1 frame. The spread model used the broker `spread` column per M15 bar, with fallback 2.5 pips where zero; observed XAUUSD median spread was 5.0 pips and p90 was 6.0 pips.

## 4. Candidate population

| Measure | Count |
|---|---:|
| Raw `SWEEP_REJECTION` detections | 18,031 |
| Repeated selection observations | 18,031 |
| Stable unique sweep candidates | 6,011 |
| Executed sweep candidates | 0 |
| Current replay trades across all enabled triggers | 0 |

The stable candidate IDs correctly collapsed repeated scans. No downstream outcome was attached because the replay did not reach an order attempt.

## 5. Named vs anonymous liquidity

| Primary identity | Unique candidates | Share |
|---|---:|---:|
| M15 equal high | 3,018 | 50.2% |
| M15 equal low | 2,949 | 49.1% |
| Asia low | 26 | 0.4% |
| Asia high | 18 | 0.3% |
| `UNKNOWN_LOCAL` | 0 | 0.0% |

This is a telemetry-identifiability failure, not evidence that anonymous sweeps do not exist. Exact level matching and the available liquidity inputs leave no untreated control group. Named-vs-anonymous effect sizes, confidence intervals, and stability cannot be computed.

## 6. Location-family results

Observed context coverage among the 6,011 candidates:

- Premium/discount: 3,133 discount and 2,878 premium.
- Confirmed H4 swing context: H4_LH 2,210; H4_HL 1,870; H4_LL 1,105; H4_HH 826.
- VP reference: VAL 2,149; VAH 1,996; POC 1,866.
- M15 FVG context: 231 candidates; 5,780 had no M15 FVG context.
- Equal-liquidity context: 5,967 candidates.

These are population counts only. There is no outcome column with a non-null realized result, so no family is designated higher quality and no descriptive ATR bucket is promoted to a rule.

## 7. Reaction-quality results

The context records MSS/BOS, displacement, structure trend, freshness, and ATR-normalized distances where available. These fields are suitable for future analysis, but the current replay has no executed outcome population against which to test whether they discriminate quality.

The sweep detector's `HIGH` confidence was not altered or reinterpreted.

## 8. Trigger-overlap results

Among unique sweep candidates:

| Overlap group | Count |
|---|---:|
| Sweep + BOS_RETEST | 3,227 |
| Sweep + multiple triggers | 1,197 |
| Sweep only | 914 |
| Sweep + JUDAS | 669 |
| Sweep + VALUE_AREA_FADE | 3 |
| Sweep + FVG_FILL | 1 |

The detector overlap is substantial: 5,097 of 6,011 unique sweep candidates had at least one other detector fire on the same decision event. Under the frozen selector, sweep remained the production winner for these observations. This establishes opportunity for a counterfactual study, but not selector opportunity cost, because no candidate reached execution.

## 9. Counterfactual selector results

The exported counterfactual table contains 6,298 unique sweep/alternate pairs:

- BOS_RETEST alternatives: 4,420
- JUDAS alternatives: 1,840
- FVG_FILL alternatives: 30
- VALUE_AREA_FADE alternatives: 8

No alternate was simulated through a clean portfolio-independent terminal path. Running alternate candidates through the live downstream state would contaminate position count, cooldown, daily loss, and risk state; the current replay produced no executable production candidate from which a paired shadow trade could be anchored. Accordingly, all counterfactual outcome fields are explicitly `NOT_AVAILABLE`.

## 10. Fold stability

Population volume persisted across all three chronological folds, but outcome sign cannot be assessed because executed sweep N is zero in every fold. There is therefore no evidence for or against a location hierarchy, location veto, or selector change.

## 11. Statistical uncertainty

No Wilson interval or bootstrap interval is estimable for sweep outcomes because there are zero executed observations. Any apparent location preference from the population counts would be selection/context coverage, not performance evidence.

The earlier archived trigger-priority campaign reported executed sweep trades, but it predates this location-context telemetry and cannot be joined to these candidate IDs or location fields. It was not reused as a fabricated location result.

## 12. Evidence-supported meaningful-area definition

No evidence-supported meaningful-area definition can be established yet. The next valid dataset must contain both:

1. a non-empty `UNKNOWN_LOCAL` control population, and
2. executed or independently simulated candidate outcomes under an explicitly frozen cost and exit model.

The current data supports only a telemetry-quality observation: equal-liquidity and Asia-session labels are being captured, while anonymous-local identity is not.

## 13. Evidence-supported selector change

None. The sample contains no executed outcomes and no clean counterfactual outcomes. The smallest justified action is therefore **no selector change** and no location veto.

## 14. What must NOT be changed

Until a valid outcome-bearing replay exists, do not:

- make PDH/PDL, session levels, FVG, VP, MSS, BOS, or equal liquidity mandatory;
- lower or raise sweep confidence;
- change first-match priority;
- alter `ALL_TRIGGERS`, CLI defaults, reclaim, STB, consultant/regime rules, cost gates, CRG, risk, sessions, SL/TP, timeout, or exits;
- call population coverage a profitability result;
- use the compatibility shim as a production fix.

## 15. Recommended next implementation experiment

First repair the replayability/data contract without changing trading behavior: run the existing sweep detector through a replay configuration that can actually admit the intended sweep arm, capture a genuine `UNKNOWN_LOCAL` population, and attach deterministic signal geometry plus independent future-bar outcome simulation. Then repeat the same three chronological folds and test only a small pre-registered set of single-family comparisons. A selector shadow evaluator may be added only if it isolates alternate trade outcomes from portfolio state; otherwise report deterministic per-candidate outcomes without claiming portfolio performance.

Machine-readable exports:

- [Sweep location analysis CSV](D:/Hermes%20Quant/Quant%20GPT%20Test%20Claude%20-%20Final/apex_ai/logs/sweep_location_analysis.csv)
- [Sweep selector counterfactual CSV](D:/Hermes%20Quant/Quant%20GPT%20Test%20Claude%20-%20Final/apex_ai/logs/sweep_selector_counterfactual.csv)

F — INSUFFICIENT / UNSTABLE EVIDENCE
