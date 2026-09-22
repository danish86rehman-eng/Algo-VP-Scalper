# SWEEP_REJECTION location shadow telemetry

**Date:** 2026-09-18  
**Status:** Implemented as observation-only instrumentation. No strategy permission, selector priority, confidence, geometry, risk, session, gate, order or exit behavior was changed.

## Files changed

- `apex_ai/scalper/sweep_location.py` — immutable causal context and shared builder.
- `apex_ai/scalper/trigger_engine.py` — attaches context to detected sweep candidates and preserves existing winner logic.
- `apex_ai/scalper/candidate_funnel.py` — records overlap/winner/location observation and reuses stable candidate IDs.
- `apex_ai/scalper/execution_telemetry.py` — includes sweep context in entry observations.
- `apex_ai/scalper_agent.py` — live wiring and candidate identity propagation into execution/open-trade telemetry.
- `apex_ai/backtest_scalper.py` — replay wiring using the same builder and equivalent observer hook.
- `apex_ai/analyze_sweep_location.py` — later grouping utility; observation-only.
- `apex_ai/tests/test_sweep_location_shadow.py` — focused deterministic tests.

## Data structure and causality

`SweepLocationContext` is a frozen dataclass. It records swept-level identity, source timeframe, distance in price and ATR units, session/PDH/PDL/equal-liquidity identity, confirmed H1/H4 swing proximity and trend, M15 FVG proximity/freshness when an existing FVG plan is available, VP POC/VAH/VAL proximity from a supplied completed profile, premium/discount context, structure flags, and matched triggers.

Unknown evidence remains `None` or `UNKNOWN_LOCAL`. The builder does not create breaker/IFVG or OB evidence where no deterministic causal object is already available.

Distance convention:

```text
distance_price = abs(swept_level - level)
distance_atr   = distance_price / mean true range of the last 14 trigger bars
```

For a bounded FVG, distance is zero when the swept level lies inside the zone; otherwise it is the distance to the nearest edge. Named-level identity uses exact source equality within a deliberately tiny floating-point tolerance; no optimized proximity threshold is introduced.

Reused components include `SessionLiquidityTracker`, `StructureEngine`, existing M15 FVG plans, completed D1/H1/H4 frames, and existing VP profiles. The new builder is called only for observation and exceptions are swallowed by the trigger engine’s observation hook so telemetry cannot affect a decision.

## Selector overlap telemetry

`step2_trigger()` still evaluates and selects in exactly the existing fixed order. The new `record_selection()` event records:

- every fired trigger;
- each trigger direction;
- the winner and matched-trigger list;
- selected versus non-selected status;
- the sweep location context when applicable.

No candidate is promoted, downgraded, vetoed or reordered.

## Candidate identity and deduplication

The existing `CandidateFunnelRecorder` identity remains authoritative. It excludes wall-clock evaluation time and uses symbol, trigger, direction, originating event timestamp, reference level, setup ID and formation timestamp. Repeated scans of the same closed-bar sweep therefore reuse one logical candidate ID. The live execution context also carries that ID into the ordinary sweep candidate/entry/open-trade records.

The replay path accepts an optional `CandidateFunnelRecorder`, uses the same observer callback and invokes the same selection-recording method. Existing callers that do not request a funnel remain behaviorally unchanged.

## Available and unavailable evidence

Available causally:

- PDH/PDL from the last supplied completed D1 bar;
- session high/low identity and consumed state from `SessionLiquidityTracker`;
- M15 equal high/low identity from `MicroLiquidity`;
- H1/H4 confirmed swing proximity and current structure state;
- M15 FVG proximity when the existing M15 FVG planner returns a zone;
- completed VP profile POC/VAH/VAL proximity when a profile is already supplied;
- trigger-frame premium/discount classification;
- all fired-trigger overlap and winner information.

Not inferred:

- H1 FVGs where no existing causal FVG object is supplied;
- OBs, breakers or IFVGs without a deterministic existing object;
- future-completed profiles or post-decision session ranges;
- optimized thresholds or profitability labels.

## Replay/live parity

Live and replay call `build_sweep_location_context()` with closed decision frames and an injected `now`. Replay uses `_closed_tf()` for D1/H1/H4, while live uses `_get_ohlcv()` with shift 1. Both paths retain the existing M15/M5 frame boundaries and selector call. The new tests cover direct deterministic context construction and the existing replay fidelity suite remains green.

## Outcome linkage

The context is embedded in entry observations and the stable candidate ID is copied into the live open-trade record. Selected candidates now receive observation-only stage markers for structure, STB, regime, cost, and risk, plus a terminal record for rejection, simulated entry, execution failure, or execution success. No exit, MFE, MAE, realized-R, or timeout calculation was modified. The grouping utility is intentionally conservative and never declares a location category profitable.

## Tests

Exact commands:

```powershell
py -3.14 -E -m compileall -q scalper_agent.py backtest_scalper.py scalper tests/test_sweep_location_shadow.py analyze_sweep_location.py
py -3.14 -E -m unittest tests.test_sweep_location_shadow tests.test_candidate_funnel tests.test_entry_telemetry tests.test_trigger_geometry tests.test_backtest_fidelity
```

Result: **70 tests passed, 0 failures, 0 errors**.

The separately run pre-existing trigger-priority suite remains unchanged and reports **2 errors plus 1 failure** because its expectations still include `SWEEP_REJECTION` in the constructor whitelist and expect `VPLR_ENABLED=False`; the current branch intentionally differs. Those failures were not repaired.

## Proof trading behavior is unchanged

- No `ALL_TRIGGERS`, CLI default, session, STB, consultant, CRG, risk, SL/TP or execution code was changed.
- `location_context_builder` is observation-only and wrapped so failures cannot affect the trigger result.
- `candidate_observer` and `record_selection` return no decision value.
- The existing `winner` assignment remains first-match; `matched_triggers` remains the same list.
- Focused geometry, replay-fidelity, funnel and telemetry tests pass.
- No MT5, scalper, watchdog, Guardian or main-agent process was started or restarted.

READY FOR SHADOW DATA COLLECTION
