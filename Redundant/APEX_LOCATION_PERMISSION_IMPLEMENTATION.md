# APEX Location Permission Implementation

**Date:** 2026-09-19  
**Scope:** XAUUSD scalper trigger architecture  
**Status:** Implemented; focused verification passed. The repository contains unrelated pre-existing dirty changes, so the full suite remains red as documented below.

## 1. Files changed

- `apex_ai/scalper/location_permission.py` — normalized directional permission, frozen location identity, reaction state, and M5 confirmation contract.
- `apex_ai/scalper/decision_params.py` — ATR-normalized proximity and M5 confirmation parameters.
- `apex_ai/scalper/trigger_engine.py` — location permission is evaluated before trigger priority; sweep reaction is required.
- `apex_ai/scalper/candidate_funnel.py` — records location, reaction, confirmation, and explicit rejection stages.
- `apex_ai/scalper/execution_telemetry.py` — records permission/reaction/confirmation evidence.
- `apex_ai/scalper_agent.py` — live path passes the authoritative snapshot and records blocked candidates.
- `apex_ai/backtest_scalper.py` — replay mirrors the live permission and funnel path.
- `apex_ai/tests/test_location_permission.py` — 13 focused permission/gate/funnel tests.
- `apex_ai/tests/test_market_location.py` — updated expectation for location-blocked candidates.

The existing `scalper/market_location.py` remains the only VP/SR calculation authority. The new layer consumes its snapshot and does not recalculate profiles or structure.

## 2. Previous SWEEP_REJECTION path

The detector could emit an executable `SWEEP_REJECTION` after:

```text
local SSL/BSL sweep
→ rejecting trigger-frame candle
→ candidate selected by priority
```

The older independent sweep-location policy was optional. With that policy off, a sweep/rejection could reach downstream execution gates without proving VP/SR location.

## 3. New execution path

```text
closed D1/H4/M15 VP/SR snapshot
→ directional location permission
→ trigger/setup detection
→ M15 sweep reaction
→ closed M5 reclaim plus displacement or MSS
→ trigger priority
→ existing execution gates
→ trade
```

For live and replay, missing runtime location fails closed when the location gate is required. Risk sizing, SL/TP geometry, Guardian, spread, news, sessions, cooldown, and broker handling were not redesigned.

## 4. Exact location permission rules

The permission result is one of `ALLOW_LONG`, `ALLOW_SHORT`, `CONTEXT_ONLY`, or `BLOCK`.

- Long reversal permission requires an active structural support zone or VAL proximity.
- Short reversal permission requires an active structural resistance zone or VAH proximity.
- VP/SR confluence is recorded when the structural zone and VP level overlap.
- Flipped zones use their current role; a former resistance flipped to support can authorize long, and vice versa.
- `BOS_RETEST`, `FVG_FILL`, and S01 continuation candidates may also use directional `ABOVE_VALUE`/`BELOW_VALUE` context.
- `VALUE_AREA_FADE` is edge-specific: long at VAL, short at VAH.
- POC by itself returns `CONTEXT_ONLY` and cannot authorize a reversal.
- Opposite directional locations return `WRONG_LOCATION_DIRECTION`.
- Mid-range/unknown locations return `MID_RANGE` or `NO_IMPORTANT_LOCATION`.
- A sweep level must be within the configured ATR proximity of the selected location; otherwise the result is `TOO_FAR_FROM_LOCATION`.

The frozen record contains the location ID, zone boundaries, profile ID, POC/VAH/VAL, direction, and trigger timestamp. It is copied into the candidate and is not replaced by a newly discovered nearby zone during that evaluation.

## 5. Reaction and M5 confirmation rules

For a bullish sweep:

1. The location permission must be long-valid.
2. Sell-side liquidity must be swept at/through the frozen support or VAL location.
3. Closed M5 data must include the raid and a bullish final M5 close back above the frozen location price.
4. M5 confirmation must contain either a body displacement of at least `0.80 ATR` or the existing repository M5 MSS routine used by `VP_LIQUIDITY_REACTION`.

The bearish path is mirrored for BSL/resistance/VAH. The recorded states are `NO_REACTION`, `TOUCH`, `RAID`, `REJECTION`, `RECLAIM`, `DISPLACEMENT_CONFIRMED`, and `ACCEPTANCE`; the executable minimum is `RECLAIM` plus `DISPLACEMENT_CONFIRMED`, `MSS_CONFIRMED`, or both.

Acceptance beyond the frozen zone invalidates the setup after two closed M5 bars, using a `0.10 ATR` acceptance buffer. The rejection reasons include `NO_REACTION`, `NO_RECLAIM`, `NO_DISPLACEMENT`, and `LOCATION_INVALIDATED`.

## 6. ATR thresholds

| Parameter | Default |
|---|---:|
| Location/sweep proximity | `0.35 ATR` |
| Acceptance buffer | `0.10 ATR` |
| M5 displacement body | `0.80 ATR` |
| M5 MSS lookback | `120` bars |
| M5 MSS swing lookback | `2` bars |
| Acceptance confirmation | `2` closed bars |

All are in `scalper/decision_params.py` and are shared by live and replay.

## 7. Priority protection

`trigger_engine.step2_trigger()` evaluates the location permission inside the common candidate-consideration function before adding a trigger to `matched_triggers` or assigning it as the winner. Therefore a higher-priority sweep that is blocked cannot consume the bar or preempt a valid lower-priority candidate.

## 8. Candidate funnel additions

The funnel now records `LOCATION_CHECKED` and `M5_CONFIRMATION_CHECKED`, including the frozen permission record and reaction state. Explicit rejection codes include:

`NO_IMPORTANT_LOCATION`, `WRONG_LOCATION_DIRECTION`, `TOO_FAR_FROM_LOCATION`, `NO_REACTION`, `NO_RECLAIM`, `NO_DISPLACEMENT`, `LOCATION_INVALIDATED`, `SETUP_EXPIRED`, `MID_RANGE`, and `POC_ONLY_NO_STRUCTURE`.

For a blocked sweep it records `SWEEP_REJECTION_BLOCKED_NO_LOCATION`, plus `sweep_type`, `swept_level`, `location_id`, ATR distance, reaction state, M5 confirmation state, and final permission.

## 9. Verification

- Focused location/permission/funnel tests: **28 passed**.
- Touched-module compilation with `py -3.14 -E`: **passed**.
- Live/replay integration slice: new location tests and CRT execution integration passed.
- Full repository suite: **632 tests; 12 failures and 4 errors**. The failures/errors are pre-existing dirty-worktree issues involving VPLR ship defaults/priority, session schedule expectations, CRT test fixtures, and session ledger identity—not the new focused permission tests.

## 10. Examples

Valid long:

```text
H4 support + VAL
→ SSL sweep through support
→ bullish M5 close reclaims support
→ M5 displacement/MSS confirms
→ ALLOW_LONG
→ SWEEP_REJECTION may continue to execution
```

Rejected mid-range example:

```text
SSL sweep + bullish rejection candle
→ snapshot = MID_RANGE_NO_LOCATION
→ BLOCK
→ SWEEP_REJECTION_BLOCKED_NO_LOCATION
```

## 11. Remaining edge cases

- The confirmation is bar-based; it does not claim tick-level sequencing inside an M5 candle.
- The location engine still requires usable real or tick volume for VP profiles; structural zones can remain available when profile data is unavailable.
- The current stateless sweep detector confirms within the same decision cycle. Longer multi-scan pending candidates remain the responsibility of stateful trigger modules such as S01.
- The broader repository should be cleaned and its unrelated baseline failures resolved before a live promotion run.
