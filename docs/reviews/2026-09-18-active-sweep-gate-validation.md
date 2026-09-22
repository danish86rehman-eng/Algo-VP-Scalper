# Active DEMO SWEEP_REJECTION gate validation

Date: 2026-09-18  
Audit window: activation at `2026-09-18 07:48:33Z` through the latest runtime check  
Scope: observation only; no strategy logic or orders modified

## 1. Runtime proof

- MT5 initialized successfully.
- Account: `40280210` on `Exness-MT5Trial2`.
- `trade_mode=0` (DEMO).
- Balance/equity: `$1293.11 / $1293.11`.
- XAUUSD quote was fresh at audit time: bid `4386.468`, ask `4386.518`.
- Positions: `0`; pending orders: `0`.
- Exactly one scalper process was found: PID `36328`.
- Exact launch includes `--sweep-location-config config.json` and explicitly lists `SWEEP_REJECTION`.
- Effective priority in the startup log: `SESSION_SWEEP > SWEEP_REJECTION > HTF_CRT_SWEEP > VP_LIQUIDITY_REACTION > FVG_FILL > BOS_RETEST > JUDAS > VALUE_AREA_FADE`.
- Active startup configuration: `enabled=True`, `mode=active`, `allow_unknown_local=False`, `allow_consumed=False`.
- TGA PID `29124` and execution telemetry worker PID `28228` were present. No duplicate scalper was found. No separate watchdog process was found.

## 2. Live candidate population

The post-activation log contains 105 analysis cycles and 105 `SWEEP_REJECTION` location permission failures. Every recorded failure has the reason `no independent pre-existing pool`.

| Metric | Observed |
|---|---:|
| Sweep detections visible in production log | 105 |
| Stable candidate IDs | Not emitted |
| Unique candidates | Not measurable from current log |
| Permission PASS | 0 visible |
| Permission FAIL | 105 |
| UNKNOWN_LOCAL | 105 reason-equivalent failures |
| Named-liquidity candidates | 0 visible |
| Consumed candidates | 0 visible |
| Executions | 0 |

The 105 count is a log-observation count, not a deduplicated candidate count. Repeated scans can therefore not be distinguished from new sweep events using the current production telemetry.

## 3. UNKNOWN_LOCAL validation

The gate is producing genuine unknown-location failures: each failure states `no independent pre-existing pool`. This is consistent with `allow_unknown_local=False` and demonstrates that the previous self-authorizing local identity is no longer automatically accepted.

The audit cannot prove the percentage of unique unknown candidates because the live path does not log a stable candidate ID, pool ID, all identities, source swing IDs, or the decision timestamp. No desired percentage has been imposed.

## 4. True M15 equal-liquidity provenance

The implementation requires at least two distinct confirmed M15 swing events clustered within `0.10 ATR`, with source timestamps and source IDs. The sweep bar is excluded from the pre-confirmation map, and repeated scans do not create new source swings.

No admitted M15 equal-liquidity sweep occurred in the observed live window. Therefore the following live distributions are not estimable yet:

- touch count;
- source-level separation in ATR;
- pool age;
- time since last touch;
- consumed-before-sweep state.

Reporting zero for these metrics would incorrectly mean “no observations” rather than “observed zero.”

## 5. PASS/FAIL and downstream funnel

No permitted named-liquidity sweep was visible, so there are no honest live counts for PWH/PWL, PDH/PDL, ASIA, LONDON, NY, H4, H1, or true M15 equal liquidity.

For the blocked-sweep continuation check, the selector did continue into the existing scan. The post-activation log shows 105 session-sweep evaluations and 105 STB evaluations. It also contains downstream CRT/VP/FVG/value-area observations and 83 consultant gate-1 blocks. No later trigger was logged as a winner and no order was executed.

The current log does not identify which later detector was evaluated specifically after each failed sweep, so exact continuation-release counts by `HTF_CRT_SWEEP`, `VP_LIQUIDITY_REACTION`, `FVG_FILL`, `BOS_RETEST`, `JUDAS`, and `VALUE_AREA_FADE` are unavailable.

## 6. Independent outcomes

There were no executed or admitted named/unknown sweeps in this live observation window. Consequently, independent research-only outcome statistics are:

| Cohort | N | Mean R | Median R | MFE_R | MAE_R | TP | SL | Timeout |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Permitted named liquidity | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| UNKNOWN_LOCAL / consumed | 0 outcome evaluations | N/A | N/A | N/A | N/A | N/A | N/A | N/A |

The independent evaluator exists in `apex_ai/scalper/independent_outcomes.py`; no portfolio-profitability claim is made.

## 7. Selector overlap and pre-emption

No admitted sweep reached a winner, so the observed overlap counts are all zero/unknown. The priority order was unchanged. The log confirms that the sweep remains above BOS/JUDAS/FVG in the configured priority, while a failed location permission does not terminate the scan.

## 8. Implementation defect

The gate decision itself is behaving conservatively and is producing real `UNKNOWN_LOCAL` failures. The auditability defect is the production telemetry contract: the live log records only the generic permission reason. It does not persist one immutable row per sweep candidate containing candidate ID, direction, swept level, primary/all identities, pool ID, source timeframe, touch count, freshness, consumed state, ATR separation, matched triggers, final winner, terminal reason, or execution status.

Because of that missing ledger, deduplication, equal-liquidity provenance, overlap, exact continuation, named-vs-unknown outcomes, and downstream gate distributions cannot be proven from live trading records yet.

## 9. Exactly one recommended change

Add one research-only immutable sweep-candidate audit ledger at the selector boundary. Emit one stable-ID row for every sweep detection and update that same row with permission, continuation, winner, terminal reason, execution, and independent-outcome fields. Do not change any detector, hierarchy, permission, priority, confidence, reclaim, FVG, VP, STB, consultant, CRG, risk, SL/TP, exit, or session behavior.

## Conclusion

The active gate is rejecting anonymous live sweeps as intended, but the current production telemetry is insufficient for a complete provenance and outcome audit. Continue DEMO collection only after adding the single research-only ledger correction above.

ACTIVE GATE WORKING — ONE CORRECTION REQUIRED: emit an immutable per-candidate research-only sweep audit ledger
