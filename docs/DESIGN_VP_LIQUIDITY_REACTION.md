# Design note — `VP_LIQUIDITY_REACTION`

**Status:** design, pre-implementation. Written before code per the workflow.
**Ledger:** L-014 (to be opened).
**Date:** 2026-08-27

---

## 1. What this is, and what it is not

A first-class **entry trigger** that treats H4 volume-profile levels as *locations
where liquidity rests*, and requires an ICT/SMC liquidity event plus lower-timeframe
structure confirmation before it fires.

| | L-005 `VP_GATE` | L-009 `VALUE_AREA_FADE` | **L-014 `VP_LIQUIDITY_REACTION`** |
|---|---|---|---|
| Kind | veto layer | signal | **signal** |
| VP role | permission to trade | the setup itself | **location only** |
| Direction from | the VA side | the VA side (buy VAL / sell VAH) | **the liquidity raid + MSS** |
| POC | dead zone (no trade) | no trade | **bidirectional reaction zone** |
| Confirmation | none | RSI 70/30 + wick | **raid → reclaim/displacement → M5 MSS → confluence** |
| Profile | rolling 42-bar H4 | rolling 42-bar H4 | **anchored to a confirmed H4 leg** |

The three differ in premise, not in degree. L-005's rejection constrains vetoes;
L-009's zero-trade result is a plumbing finding. Neither is evidence about this.

---

## 2. The blocking gate — measured, not assumed

From `logs/scalper_agent.log`, 2026-08-27:

```
00:00:08Z  SA State: IDLE -> ACTIVE   | Session active, all checks clear
02:00:12Z  SA State: ACTIVE -> IDLE   | Outside SA session window
06:30:18Z  SA State: IDLE -> ACTIVE   | Session active, all checks clear
```

**The agent was IDLE from 02:00 to 06:30 UTC — 4.5 hours of late Asia.** The
reference setup falls inside that window.

The gate is **not** STB, **not** the regime whitelist, **not** the session checker
returning a wrong window. It is the state machine:

- `scalper_agent.py:448` calls `state_mach.evaluate(in_session_window=sess.in_window, …)`
- `behavior_state.py:89` → rule 4 → `IDLE`
- `scalper_agent.py:469` → `if new_state == SAState.ACTIVE:` → **`_scan_symbol` is never called**

So no trigger runs at all. `TOKYO_OPEN` ends at 02:00 and `PRE_LONDON` starts at
06:30; the gap between them is structural, not a filter decision.

**Consequence for the design:** a trigger-aware fix cannot live inside `_scan_symbol`,
because during the dead zone that function is never reached. The state machine must
gain a third answer between ACTIVE and IDLE.

Two further gates would block the trigger *after* it fires, and both are documented
contradictions rather than judgements:

- `short_term_bias.py:451` — the NEUTRAL branch admits only
  `("SWEEP_REJECTION","JUDAS","FVG_FILL")`; anything else is "requires direction".
- `short_term_bias.py:473` — the *opposing* branch admits only `SWEEP_REJECTION`.
  A raid-and-reverse reads as opposing by construction: price has just made a new
  high, so intraday structure is bullish at the exact moment the short is valid.
  This is the same rule that produced L-009's 9-of-10 rejections.

Both are fixed by membership, not by weakening: the hard-coded tuples move to named
sets in `decision_params`, and `VP_LIQUIDITY_REACTION` joins them. Existing triggers'
membership is unchanged, so the baseline is bit-identical when VP is off.

---

## 3. Causal anchored H4 profile

**Question the stop-condition asks:** can the leg be anchored without future data?
**Answer: yes**, and the existing swing code already has the property.

`StructureEngine._identify_swings` (`core/structure_engine.py:85`) confirms a pivot at
index `i` only when `hi[i] == max(hi[i-n : i+n+1])`, iterating `i in range(n, len(df)-n)`.
A pivot is therefore never reported until `n` bars have **closed after it**. Combined
with a frame that already excludes the forming bar, anchor selection is causal by
construction — no right-side look-ahead, no forming-H4 OHLC.

**Leg selection** (`scalper/anchored_vp.py`, new):

1. Take `df_h4_closed` — `_closed_tf(..., 240)` in sim, `copy_rates_from_pos(H4, 1, n)` live.
2. Confirmed swing points from `StructureEngine(swing_lookback=VPLR_H4_SWING_LOOKBACK)`.
3. Anchor `S` = the most recent confirmed pivot.
   - `S` is a swing **low** → leg is low→high (a recovery leg; the reference case).
   - `S` is a swing **high** → leg is high→low.
4. Leg spans `df_h4_closed.iloc[S.index : ]` — from the pivot to the last closed bar.
5. Reject the leg unless it is *meaningful*: `>= VPLR_MIN_LEG_BARS` bars and a range
   `>= VPLR_MIN_LEG_ATR × ATR(H4)`.
6. Profile it with the existing `build_profile_auto` — same arithmetic, same
   tie-break, same bar-based volume attribution. Nothing is re-implemented.

The rolling 42-bar profile is untouched and still feeds `vp_gate` / `va_fade_trigger`.
The anchored profile is a **separate** object with its own params.

**L-007:** every H1/H4 frame this trigger touches uses `_closed_tf`. The legacy
`_closed` reads feeding STB/consultation stay as they are — re-timing those would
invalidate the existing baseline inside a change that adds a trigger, which is the
mistake L-007 was opened to avoid repeating.

---

## 4. Setup contract

Being near POC/VAH/VAL is **not** a trigger. The detector returns `None` until all of
the following hold, so lower-priority triggers still get their turn on the same bar.

For a **SHORT** (long mirrors with SSL / bullish):

| # | Requirement | Source |
|---|---|---|
| 1 | **VP interaction** — a liquidity level sits within `VPLR_ZONE_ATR × ATR` (and `VPLR_ZONE_VA_FRAC × VA width`) of POC, VAH or VAL | anchored profile |
| 2 | **BSL raid** — a closed trigger-frame bar's high exceeded that level by `>= VPLR_MIN_SWEEP_ATR × ATR` | `MicroLiquidity` equal-highs / session high, trigger-frame swing highs, PDH |
| 3 | **Reclaim OR displacement** — last closed bar closes back below the level, **or** a closed bar's body `>= VPLR_DISPLACEMENT_ATR × ATR` in the trade direction | trigger frame |
| 4 | **M5 MSS/CHoCH/BOS** — a closed confirmation-frame bar closed below the last swing low that preceded the raid | confirmation frame |
| 5 | **`>= VPLR_MIN_CONFLUENCE` confluences** from: FVG/IFVG, OB, displacement strength, rejection wick/engulfing, PDH/PDL or session liquidity, H1/H4 structure, ATR momentum | existing detectors |

No RSI gate. No RANGING requirement. POC is bidirectional — direction comes from
which side the raid happened on and which way structure broke, never from the level.

**Geometry:** entry = last closed trigger-bar close (as every other trigger);
SL = raid extreme ± `VPLR_SL_BUFFER_ATR × ATR`; TP1/TP2 via the shared `_targets`
(2R/3R). The stop sits beyond the raid, so `MIN_SL_PIPS` and the net-R cost gate
apply unchanged.

**Early entry (scope §4):** the H4 candle that raids does **not** need to close. The
raid and the MSS are both read on closed M15/M5 bars *inside* the developing H4 bar.
Only the *profile* comes from closed H4 — which is what makes it non-repainting.

---

## 5. Priority

`ALL_TRIGGERS` order becomes `VP_LIQUIDITY_REACTION → SWEEP_REJECTION → FVG_FILL →
BOS_RETEST → JUDAS → VALUE_AREA_FADE`, and `step2_trigger` evaluates VP first.

`step2_trigger` currently returns on first match, discarding what else would have
fired. It will now collect `matched_triggers` for telemetry while still *selecting*
by priority — so "VP and SWEEP_REJECTION both qualified, VP won" is recorded rather
than inferred.

---

## 6. VP-only Asia access

`SESSION_WINDOWS` is **not** modified. Existing triggers keep their exact windows.

A separate, trigger-scoped allowance is added:

```
VPLR_SESSION_OVERRIDE_ENABLED = True
VPLR_SESSION_WINDOW_UTC       = (time(0,0), time(6,30))   # configurable
```

The state machine gains `vp_window_open`, returning a new `VP_ONLY` state when the
normal session is closed but the VP window is open. In `VP_ONLY`, `_scan_symbol` runs
with `enabled_triggers` narrowed to `{VP_LIQUIDITY_REACTION}`. Every other trigger is
not merely rejected — it is not evaluated, so it cannot consume the bar.

**Untouched in `VP_ONLY`:** daily/max loss (`HALTED`), consecutive-loss pause
(`PAUSED`), main-account drawdown (`PROTECTED`), max positions/exposure, symbol dedup,
cooldown, stale data, spread and net-R (`step3_validate`), news blackout, margin,
execution and reconciliation. State priority stays `PROTECTED > HALTED > PAUSED >
VP_ONLY > IDLE`, so every hard protection is evaluated *before* the VP window is
consulted.

The simulator mirrors this in the same change: its `enforce_session_windows` check
gains the identical branch.

---

## 7. Telemetry

A `VPLRDetail` record on the trigger carries: `selected_trigger`, `matched_triggers`,
profile type (`ANCHORED`/`ROLLING`), anchor start/end timestamps, POC/VAH/VAL,
interacted level, level source (`EQUAL_HIGHS`/`SESSION_HIGH`/`SWING`/`PDH`/…), sweep
depth in ATR, reclaim flag, MSS level and time, confluence list, session name, and the
outcome of every gate. Written to the reject log and the trade journal. Observation
only — §13.10: no counter feeds back into gating.

---

## 8. Files

**New:** `scalper/anchored_vp.py`, `scalper/vp_liquidity_trigger.py`,
`tests/test_vp_liquidity_trigger.py`
**Edited:** `scalper/decision_params.py`, `scalper/trigger_engine.py`,
`scalper/short_term_bias.py`, `scalper/session_checker.py`, `scalper/behavior_state.py`,
`scalper_agent.py`, `backtest_scalper.py`, `tests/test_live_sim_parity.py`
**Untouched:** `scalper/volume_profile.py`, `scalper/vp_gate.py`,
`scalper/va_fade_trigger.py`, `scalper/regime_classifier.py`, `scalper/hmm_backend.py`,
`scalper/regime_direction_gate.py`

**Default:** `VPLR_ENABLED = False`. Disabled mode must reproduce the baseline exactly,
and that is asserted by test, not by inspection.

---

## 9. Validation plan

1. Unit: anchor selection, zone maths, each contract clause in isolation, POC/VAH/VAL
   all reachable, both directions.
2. Causality: a look-ahead test that feeds the detector a frame including a future bar
   and asserts the decision is unchanged; an anchor-stability test asserting a leg
   chosen at time T is still the leg at T when re-derived from a longer frame.
3. Parity: every `VPLR_*` constant identical across both processes; the trigger present
   in both decision paths; `VP_ONLY` state reachable in both.
4. Baseline: VP off → byte-identical trade list against the current baseline run.
5. Replay of 2026-08-27 from broker data.
6. Campaign: `VP_ONLY` and `VP_TOP` arms; POC/VAH/VAL split; Asia vs non-Asia;
   incremental-vs-overlapping attribution; disjoint multi-month windows; spread
   sensitivity at 2.5 and 5.0 pips.

Promotion requires the same sign across **disjoint** windows (§13.12 — within-window
folds carry no cross-regime information). If the evidence is poor, `VPLR_ENABLED`
stays `False` and the result is reported as such.
