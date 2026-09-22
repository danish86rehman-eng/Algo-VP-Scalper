# 🚀 APEX AI — Luna Code Review Enhancement Recommendations

**Generated:** 2026-09-19
**Status:** Documentation only — no trading-code changes pending approval
**Scope:** Current uncommitted APEX working tree, with emphasis on Luna's market-location and S01 Volume Profile work and their live/backtest integration

This document lists prioritized improvements found during a code audit of the current APEX working tree. Each item explains the current behavior, the failure mode, the proposed enhancement, impact, and implementation complexity.

Verification performed during the audit:

- `py -3.14 -E -m unittest discover -s tests -v`: **641 tests, 12 failures, 4 errors**.
- Focused Luna suites: **21/21 passed** (`test_market_location` and `test_s01_reference_candle_raid_vp`).
- `py -3.14 -E -m compileall -q .`: passed.
- `git diff --check`: failed on trailing whitespace in `apex_ai/get_session_hl.py:275`.

---

## 🟥 CRITICAL (Safety/Correctness)

### C1. S01 never leaves `BUILD_VP`

**Current behavior:** `_advance_m15` expects `_build_frozen_profile` to change the setup state to `VP_FROZEN` before it can proceed to `WAIT_ENTRY_RETEST` (`apex_ai/scalper/s01_reference_candle_raid_vp.py:633`). `_build_frozen_profile` assigns the profile and `profile_frozen_at`, but returns without calling `transition(VP_FROZEN, ...)` (`apex_ai/scalper/s01_reference_candle_raid_vp.py:642`, `apex_ai/scalper/s01_reference_candle_raid_vp.py:675`).

**Problem:** The implemented strategy cannot complete its documented state chain. Once a valid raid and M15 MSS reach `BUILD_VP`, every later M15 update rebuilds the profile while the state remains `BUILD_VP`; entry-model selection and M5 confirmation are unreachable. The focused tests pass because they manually construct a setup already in `VP_FROZEN` rather than exercising the full lifecycle (`apex_ai/tests/test_s01_reference_candle_raid_vp.py:124`).

**Enhancement:** Transition to `VP_FROZEN` exactly once after a valid profile is stored, then add an end-to-end test that advances a setup from reference selection through `READY` without manually assigning an intermediate state.

**Impact:** CRITICAL
**Complexity:** Easy

---

### C2. “Evidence-only” market location is actually a default-on hard veto

**Current behavior:** The configuration says the market-location feature is “observation/evidence only” and “deliberately not a new veto,” while enabling it by default (`apex_ai/scalper/decision_params.py:595`). Live passes `market_location_required=True` whenever that switch is enabled (`apex_ai/scalper_agent.py:954`). The trigger selector then builds a permission and drops every detected trigger whose permission is not executable (`apex_ai/scalper/trigger_engine.py:376`, `apex_ai/scalper/trigger_engine.py:425`).

**Problem:** This is a silent strategy change across every trigger family, not telemetry. It can suppress existing validated entries based on a new, unpromoted model while operators and reviewers are told that it has no decision authority. That mismatch makes production behavior impossible to infer from configuration comments and invalidates baseline comparisons.

**Enhancement:** Split the feature into explicit `OFF`, `SHADOW`, and `ACTIVE` modes. `SHADOW` should always attach the snapshot and permission telemetry but never change the winner. Keep the default at `SHADOW` or `OFF`; require an explicit operator flag plus walk-forward evidence before `ACTIVE` can veto entries.

**Impact:** CRITICAL
**Complexity:** Medium

---

### C3. The market-location switch does not control the backtester

**Current behavior:** Live consults `MARKET_LOCATION_ENABLED` before building a snapshot (`apex_ai/scalper_agent.py:508`, `apex_ai/scalper_agent.py:819`). Replay always constructs a market-location engine, always builds a snapshot, and always passes `market_location_required=True` (`apex_ai/backtest_scalper.py:1080`, `apex_ai/backtest_scalper.py:1263`, `apex_ai/backtest_scalper.py:1373`). The backtester never reads `MARKET_LOCATION_ENABLED`.

**Problem:** Setting the feature off reproduces the legacy path live but not in replay. Backtest results therefore describe a different decision path, directly violating the repository's live/simulation parity invariant. Any promotion or rejection decision based on those results is invalid.

**Enhancement:** Add one shared market-location mode parameter to both entry points and route both through the same helper. Add parity tests proving that `OFF` reproduces the legacy trade list, `SHADOW` changes telemetry only, and `ACTIVE` produces the same veto decisions in live-style and replay-style calls.

**Impact:** CRITICAL
**Complexity:** Medium

---

## 🟧 HIGH IMPACT (Performance/Profitability)

### H1. S01 can treat a pre-formation touch as an FVG retest

**Current behavior:** The M5 confirmation loop may discover the first qualifying FVG at an index later than the displacement candle (`apex_ai/scalper/s01_reference_candle_raid_vp.py:793`). After finding it, the code sets `formed_idx = displacement`, not the index at which that FVG was formed, and searches for retests from `formed_idx + 1` (`apex_ai/scalper/s01_reference_candle_raid_vp.py:824`).

**Problem:** If the qualifying FVG forms after the displacement candle, an earlier candle can be counted as a “retest” of a zone that did not yet exist. This is look-ahead in replay and a stale-history false confirmation in live incremental evaluation. It inflates candidate quality and can emit entries that do not satisfy the stated first-fresh-POI contract.

**Enhancement:** Store the exact formation index alongside `zone_data`; begin the retest scan at `fvg_formed_idx + 1`. Keep the OB branch's formation index separate. Add a regression fixture where a future FVG overlaps a candle between displacement and FVG formation and assert that candle is not a retest.

**Impact:** HIGH
**Complexity:** Easy

---

### H2. S01 validates the midpoint but submits an unvalidated executable-side price

**Current behavior:** Live feeds the S01 detector the bid/ask midpoint (`apex_ai/scalper_agent.py:919`). The detector requires that midpoint to be inside the M5 entry zone (`apex_ai/scalper/s01_reference_candle_raid_vp.py:833`). Immediately before submission, `_execute_trade` rechecks cost geometry and sizing for S01 but does not recheck that the actual ask for a buy or bid for a sell remains inside the frozen entry zone (`apex_ai/scalper_agent.py:1725`).

**Problem:** A spread wider than the remaining distance to the zone edge, or ordinary scan-to-submit movement, can place the order outside the POI that authorized it. The final cost gate does not protect entry-location validity.

**Enhancement:** Carry the frozen M5 entry zone onto the trigger and require the executable quote to remain inside it immediately before `order_send`. Reject with a dedicated telemetry reason when the quote has left the zone.

**Impact:** HIGH
**Complexity:** Easy

---

### H3. The VP edge-state guard is unreachable

**Current behavior:** `_vp_candidate` first rejects any candidate farther than `proximity_atr` (`apex_ai/scalper/location_permission.py:211`). The next condition is intended to reject a candidate that is neither at the correct edge state nor showing the correct rejection event, but it also requires `distance_atr > proximity_atr` (`apex_ai/scalper/location_permission.py:213`). That clause cannot be true after the prior return.

**Problem:** Any price within the proximity band can receive a VAL/VAH permission even when `vp_state` is not `AT_VAL`/`AT_VAH` and there is no rejection event. Because market location is currently a live hard gate, this is not only a telemetry defect; it changes which entries receive permission.

**Enhancement:** Remove the contradictory distance predicate from the state/event condition, or explicitly document and test that proximity alone is sufficient. Add boundary tests at 0.29, 0.34, and 0.36 ATR with and without a value-edge event.

**Impact:** HIGH
**Complexity:** Trivial

---

### H4. Experimental defaults have been promoted while their contracts still say “off”

**Current behavior:** `VA_FADE_ENABLED` and `VPLR_ENABLED` are `True` even though their adjacent comments state that they remain off pending fold evidence (`apex_ai/scalper/decision_params.py:312`, `apex_ai/scalper/decision_params.py:460`). Session sweep is default-on in both live CLI and replay (`apex_ai/scalper_agent.py:3132`, `apex_ai/backtest_scalper.py:862`), and `SASessionChecker` defaults to `allow_whole_day=True` despite its docstring saying the opposite (`apex_ai/scalper/session_checker.py:123`, `apex_ai/scalper/session_checker.py:131`).

**Problem:** These defaults change trigger membership and trading hours, and they are the direct cause of much of the current 12-failure/4-error suite. More importantly, they make an ordinary launch activate strategies or windows that the repository's research notes and tests treat as opt-in or rejected. That is uncontrolled production configuration drift.

**Enhancement:** Define one reviewed production preset and make code, CLI defaults, documentation, and tests agree with it. Keep research candidates default-off until a dated promotion record identifies the exact walk-forward evidence and configuration era.

**Impact:** HIGH
**Complexity:** Medium

---

### H5. Stateful Luna engines do not recover their decision state after restart

**Current behavior:** `ProfileLedger` opens an append-only history path but never loads it; every process starts with empty active-profile and observation maps (`apex_ai/scalper/market_location.py:1099`). `S01Engine` also holds its setup, timestamps, entry-emitted flag, and history only in memory (`apex_ai/scalper/s01_reference_candle_raid_vp.py:451`).

**Problem:** Restarting the agent can select a different active profile on identical market data, which changes a default-on entry veto. An enabled S01 setup in the middle of raid/MSS/VP/M5 confirmation is discarded. Restart-dependent decisions cannot be faithfully replayed and can miss or reclassify setups.

**Enhancement:** Persist versioned, checksummed per-symbol snapshots atomically; restore only after reconciling timestamps and current broker/account identity. At minimum, fail the active location gate closed into `SHADOW` after an unclean restart until the profile state has been reconstructed deterministically.

**Impact:** HIGH
**Complexity:** Medium

---

## 🟨 MEDIUM IMPACT (Robustness/Reliability)

### M1. The profile ledger retires other symbols' profiles

**Current behavior:** One live `MarketLocationEngine` instance serves all symbols (`apex_ai/scalper_agent.py:509`). `ProfileLedger.update` compares the current symbol's profile IDs against every profile in its global `_latest` dictionary and retires every absent ID (`apex_ai/scalper/market_location.py:1116`). The profile record itself does not carry a symbol field, although the hashed ID includes it.

**Problem:** In a multi-symbol scan, processing XAUUSD retires XAGUSD/USOIL history, then the next symbol retires XAUUSD. The in-memory active map survives, so execution may continue, but lifecycle telemetry becomes false and the JSONL evidence stream churns between active and retired states. That corrupts later research and profile-replacement analysis.

**Enhancement:** Scope `_latest` and retirement checks by `(symbol, timeframe)` or pass `symbol` to `update`. Add a two-symbol test proving that updating one symbol never retires the other's active/reference profiles.

**Impact:** MEDIUM
**Complexity:** Easy

---

### M2. Focused tests validate components but not the integrated contracts

**Current behavior:** Luna's 21 focused tests pass, but the S01 suite manually injects `VP_FROZEN` and never exercises `BUILD_VP → VP_FROZEN` (`apex_ai/tests/test_s01_reference_candle_raid_vp.py:124`). Market-location tests do not cover the `OFF/SHADOW/ACTIVE` live/backtest matrix, cross-symbol ledger behavior, or restart recovery. The full suite currently reports 16 unsuccessful tests.

**Problem:** Component-level green tests gave a false sense of completion while the principal state transition is unreachable and live/replay behavior diverges. For capital-sensitive code, the relevant unit is the entire decision contract, not an isolated helper.

**Enhancement:** Add end-to-end deterministic fixtures for S01 lifecycle, exact disabled-mode trade-list parity, active-mode veto parity, multi-symbol ledger isolation, and restart reconstruction. Treat a red full suite as a release blocker.

**Impact:** MEDIUM
**Complexity:** Medium

---

## 🟩 LOW IMPACT (Nice-to-Have)

### L1. Working-tree hygiene obscures meaningful review failures

**Current behavior:** `git diff --check` fails because of trailing whitespace in `apex_ai/get_session_hl.py:275`, and the working tree contains a large mixture of strategy code, research scripts, generated reports, cached news data, and runtime logs.

**Problem:** Noise makes it harder to identify which changes belong to Luna, which are generated artifacts, and which failures are true regressions. It also makes a safe rollback or focused commit difficult.

**Enhancement:** Remove the whitespace defect, keep runtime/generated output ignored, and split the current work into reviewable commits: market-location shadow telemetry, location permission activation, S01, defaults/promotion, and research tooling.

**Impact:** LOW
**Complexity:** Easy

---

## 🟦 ARCHITECTURAL (Future-Looking)

### A1. Live and replay duplicate orchestration instead of sharing one decision pipeline

**Current behavior:** Market-location fetching, S01 advancement, trigger arguments, candidate observation, and permission wiring are independently assembled in live (`apex_ai/scalper_agent.py:803`) and replay (`apex_ai/backtest_scalper.py:1234`). The current switch divergence is one concrete result.

**Problem:** Every new gate must be implemented and defaulted twice. Even when detector helpers are shared, differences in orchestration, data slicing, switches, quotes, and failure handling can invalidate research without producing a syntax error.

**Enhancement:** Extract a broker-neutral `DecisionContext` builder and one shared candidate-evaluation function. Live should supply MT5 adapters and executable quotes; replay should supply chronological bars and simulated quotes. Assert serialized decision traces are identical for the same fixture.

**Impact:** ARCHITECTURAL
**Complexity:** Hard

---

## 📊 PRIORITY MATRIX

| Priority | Items | Impact | Effort |
|---|---|---|---|
| **P0 — Do First** | C1, C2, C3 | Restore reachable behavior, truthful modes, and valid replay evidence | 2–4 focused days |
| **P1 — High Value** | H1, H2, H3, H4, H5 | Remove false confirmations, unsafe quote drift, configuration drift, and restart variance | 4–7 focused days |
| **P2 — Quality of Life** | M1, M2, L1 | Reliable telemetry, meaningful tests, reviewable history | 2–4 focused days |
| **P3 — Future** | A1 | Structural live/replay drift prevention | 1–3 weeks |

---

## 💡 SUGGESTED EXECUTION ORDER

**Phase 1 — Safety & Reachability (1–2 days)**

1. C2: Move market location to explicit shadow/off behavior.
2. C3: Make the mode identical in live and replay.
3. C1: Repair and fully test the S01 frozen-profile transition.

**Phase 2 — Causal Entry Integrity (2–3 days)**

1. H1: Track the actual FVG formation index.
2. H2: Revalidate the executable quote against the frozen entry zone.
3. H3: Correct the unreachable value-edge predicate.

**Phase 3 — Defaults and Recovery (2–4 days)**

1. H4: Reconcile production defaults, promotion records, docs, and tests.
2. H5: Add restart-safe state restoration.
3. M1: Isolate profile lifecycle by symbol.

**Phase 4 — Verification and Architecture**

1. M2: Add integrated contract tests and return the full suite to green.
2. L1: Split the working tree into reviewable commits.
3. A1: Design and implement shared decision orchestration.

---

## 🎯 EXPECTED COMBINED IMPACT

If P0+P1 are implemented:

- **Win rate:** No responsible uplift estimate is possible until the repaired causal replay is rerun; the result may rise or fall.
- **Profit factor:** No responsible estimate until live/replay parity and defaults are restored.
- **Max drawdown:** No responsible estimate; the immediate benefit is preventing unvalidated gates and out-of-zone S01 fills from contaminating the measurement.
- **Operational correctness:** Eliminate the identified unreachable S01 lifecycle, restore a functioning market-location off/shadow mode, remove the known FVG pre-formation retest path, and target **641/641** passing tests instead of 625/641.

**Net effect:** P0+P1 make the new strategy measurable and the resulting evidence trustworthy; they do not justify a profitability claim by themselves.

---

## ⚠️ NOTES

- No trading code, configuration, live process, order, or position was changed during this audit.
- “Luna work” was identified from the recent Codex tasks **Add XAUUSD location engine** and **Add S01 raid VP strategy**, then verified against the current filesystem rather than relying on task summaries.
- Existing focused tests are useful and generally well structured, but their coverage boundary misses the most important integration failures above.
- The current working tree mixes work from multiple sessions; findings H4 and L1 are repository-wide and are not attributed solely to Luna.

---

**End of Recommendations Document**

*Awaiting approval before any trading-code changes.*
