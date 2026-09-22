# APEX Entry-Model Replacement — Phase 0 Audit

**Date:** 2026-09-17  
**Scope:** Read-only audit before telemetry/evaluator implementation  
**Status:** STOP — baseline suite has one pre-existing failure; no production behavior changed

## Repository state

The worktree is already dirty with tracked and untracked changes across the scalper, backtester, session logic, VP logic, tests and research tooling. Those changes were preserved. The only artifact added for this phase is this report. The previously created `APEX_ENTRY_MODEL_REPLACEMENT_PLAN.md` is also documentation only.

`git diff --check` is not clean because of pre-existing trailing whitespace at `apex_ai/get_session_hl.py:275`.

## Current entry authority

### Live path

1. `apex_ai/scalper_agent.py:651-695` fetches closed M15/M5 data, applies cooldown and open-symbol deduplication.
2. `apex_ai/scalper_agent.py:697-820` builds optional context, calls `SATriggerEngine.step2_trigger`, then optionally advances/selects the persistent tracked-pullback book.
3. `apex_ai/scalper/trigger_engine.py:297-390` evaluates all enabled detectors but chooses the first detected candidate. Current fixed precedence is `SESSION_SWEEP` when enabled, then `SWEEP_REJECTION`, `HTF_CRT_SWEEP`, `VP_LIQUIDITY_REACTION`, `FVG_FILL`, `BOS_RETEST`, `JUDAS`, and `VALUE_AREA_FADE`.
4. `apex_ai/scalper_agent.py:842-865` applies the L-017 reclaim veto to legacy candidates; native CRT/FVG/session-sweep candidates follow their own quote guards.
5. `apex_ai/scalper_agent.py:867-1180` applies existing location, bias, regime, consultation, spread, news, CRG and exposure gates.
6. `apex_ai/scalper_agent.py:1182-1219` applies sizing/lot-floor rejection and reaches execution.
7. `apex_ai/scalper_agent.py:1338-1505` performs final quote validation, final cost checks and `mt5.order_send` at line 1456. This is the final order authority.

### Backtest path

1. `apex_ai/backtest_scalper.py:1056-1083` constructs the M15-FVG plan and calls the same trigger engine.
2. `apex_ai/backtest_scalper.py:1085-1100` advances/selects tracked-pullback setups.
3. `apex_ai/backtest_scalper.py:1114-1329` mirrors reclaim, location, bias, regime, consultation, cost, risk, sizing and final quote gates.
4. `apex_ai/backtest_scalper.py:1276-1284` fills from the first M5 bar at/after the decision instant by default. It does not reproduce the live 30-second tick fill.
5. Backtest M15-FVG evaluation uses the M5 opening quote as both bid and ask at `apex_ai/backtest_scalper.py:1058-1063`; live uses the actual MT5 bid/ask at `apex_ai/scalper_agent.py:777-783`.

## Reusable components and gaps

### `reclaim_fvg.py`

`apex_ai/scalper/reclaim_fvg.py:18-45` provides a closed-bar `ReclaimDecision` and final quote guard. `:48-65` restores directional post-close barriers from broker deals, including Guardian exits. `:68-220` provides causal completed-bar reclaim/FVG evaluation for both directions.

Reusable: completed-bar filtering, broken-level buffer, displacement/FVG/return checks, final quote guard and post-close barrier logic.

Gap: it is stateless per evaluation and has no deterministic setup identity or lifecycle persistence.

### `m15_fvg_entry.py`

`apex_ai/scalper/m15_fvg_entry.py:45-127` finds the newest qualifying completed M15 FVG, requires a current quote inside the zone, rejects consumed/stale gaps, selects the nearest structural target and enforces minimum R.

Reusable: M15 formation timing, FVG geometry, structural target clearance, quote-in-zone and no-chase checks.

Gap: it scans for the latest qualifying gap rather than maintaining the requested `LOCATION_CONFIRMED → ... → READY → CONSUMED` setup identity.

### `tracked_pullback.py`

This user-added, opt-in module is the closest existing lifecycle implementation. It persists a checksummed book at `apex_ai/data/tracked_pullback/book.json`, has `FORMING`, `WAIT_PULLBACK`, `READY`, `ATTEMPTED`, `INVALIDATED`, `EXPIRED` and `MISSED_ENTRY` states, and reserves submissions durably.

It cannot be adopted unchanged: it starts from legacy trigger candidates, permits an order-block fallback when no FVG exists, uses legacy trigger names as source identity, and `Book.select` allows native CRT/FVG/session candidates outside the persistent book. The requested staged mode must not inherit those bypasses.

### Trigger telemetry

`SATrigger.matched_triggers` already exists at `apex_ai/scalper/trigger_engine.py:73-76` and is explicitly telemetry-only. `apex_ai/scalper/execution_telemetry.py:445-523` records candidate/entry execution, quote, spread, fill and shortfall data. Rejection logging exists through `RejectionLog`, but lifecycle-stage records, deterministic setup IDs and staged-vs-legacy agreement are not yet present.

## Configuration convention

No YAML file or schema file was found in the repository. The scalper convention is Python configuration constants in `apex_ai/scalper/decision_params.py` plus CLI arguments in `scalper_agent.py` and `backtest_scalper.py`. The root `apex_ai/config.json` belongs to the main system and is not the scalper entry-model configuration surface.

Therefore the staged-mode key must follow the existing `decision_params.py` + CLI convention, likely a default-off `ENTRY_MODEL_MODE = "legacy"` with explicit `legacy`, `staged_shadow` and offline-only `staged` validation. This should be confirmed in Phase 1 before adding it.

## Causality, persistence and parity findings

- Live OHLCV fetches read MT5 position 1, excluding the forming bar; `reclaim_fvg._closed` also rejects stale or malformed histories.
- Backtest uses closed-frame helpers and an explicit next-M5-open fill model, but live uses tick bid/ask. The staged evaluator can be shared; fill and cost attribution must remain separate and explicit.
- Reclaim close barriers restore from broker deal history in `scalper_agent.py:2236-2262`.
- Tracked-pullback setup state restores from its checksummed book, but it is currently opt-in and is not the default entry authority.
- Existing parity tests verify shared reclaim/CRT/forensics components. No test currently proves a new staged evaluator produces identical decisions from identical causal inputs in live and replay.

## Baseline verification

Commands run:

```text
py -3.14 -E -m unittest discover -s tests
py -3.14 -E -m unittest tests.test_reclaim_fvg tests.test_m15_fvg_entry tests.test_trigger_priority tests.test_live_sim_parity tests.test_forensics_parity tests.test_tracked_pullback
py -3.14 -E -m compileall -q .
git diff --check
```

Results:

| Check | Result |
|---|---|
| Full suite | **FAIL: 557 run, 556 pass, 1 failure, 0 skipped reported** |
| Failure | `tests.test_vp_liquidity_trigger.SessionAllowanceTests.test_session_windows_are_untouched` |
| Failure cause | Worktree changes `LONDON_NY` from `12:00` to `11:30`; the test still expects `12:00` |
| Focused entry/parity suite | **PASS: 125 tests** |
| Compileall | **PASS** |
| Diff check | **FAIL: pre-existing trailing whitespace at `apex_ai/get_session_hl.py:275`** |
| Live account/processes | Not contacted, restarted or modified |

## Exact Phase 1 test plan

Phase 1 must remain telemetry-only and default to the current baseline behavior.

1. Capture a baseline fixture from the current default configuration: selected trigger, `matched_triggers`, direction, entry/SL/TP, gate reasons and final decision.
2. Add deterministic setup-ID tests using only causal fields: symbol, direction, location identity/time, reclaim time and FVG formation time. Assert scan-time changes do not change the ID.
3. Add lifecycle telemetry tests for every requested transition and terminal reason.
4. Add forming-bar mutation tests proving telemetry does not change when future/forming OHLC values are altered.
5. Add explicit staged-mode tests proving no staged observation can reach `_execute_trade` or `mt5.order_send`.
6. Add live/backtest telemetry-shape parity tests using the same synthetic completed-bar input.
7. Re-run the full suite and `git diff --check`; do not proceed while the pre-existing baseline failure remains unresolved or explicitly waived by the owner.

## Recommendation

**STOP and repair/reconcile the baseline first.** The entry architecture is implementable using the existing closed-bar and persistence components, but Phase 1 should not begin until the session-window test mismatch and existing diff hygiene are resolved or explicitly accepted as unrelated by the owner. No intelligent-model delegation is required for this Phase 0 audit; the repository evidence is sufficient and all conclusions are directly traceable to the current code and tests.

