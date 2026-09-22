# SWEEP_REJECTION greed audit

**Generated:** 2026-09-18  
**Scope:** Current `apex_ai` branch; live scalper path, replay path, location/context components, logs and observation telemetry.  
**Status:** Documentation only — no code, YAML, configuration, trigger permission, risk, order, or running process was changed.

## 1. Executive verdict

`SWEEP_REJECTION` is a technically coherent closed-bar micro-event detector, but it does not identify the economic identity of the liquidity it swept. It works from a nearest M15 local equal-level/session extreme and immediately assigns `HIGH` confidence. It does not require HTF/session liquidity identity, displacement, MSS/CHoCH, FVG/OB proximity, freshness, or a deterministic meaningful-area object.

The current branch also has a separate selector problem: when `SWEEP_REJECTION` is enabled, `step2_trigger()` evaluates it before HTF CRT, VP reaction, FVG, BOS and Judas, retains the first detected candidate, and later reports the others only as telemetry. This is demonstrably pre-emptive in the rotated live log. However, the current CLI default excludes `SWEEP_REJECTION`, so current-day candidate-funnel data contains no sweep candidates; the historical live telemetry/log evidence is from runs where it was explicitly enabled.

The primary cause of random-location entries is missing location permission. Priority/pre-emption is a material secondary cause when multiple detectors fire on the same bar.

## 2. Current SWEEP_REJECTION state machine

The actual current path is:

1. The state machine permits scanning only in `ACTIVE` or trigger-scoped `VP_ONLY` state (`apex_ai/scalper_agent.py:603-635`). Session windows are evaluated by `SASessionChecker`; the default CLI whitelist is `TOKYO_OPEN,LONDON_NY` (`apex_ai/scalper/session_checker.py:141-165`, `apex_ai/scalper_agent.py:2884-2887`).
2. `_scan_symbol()` fetches the trigger and confirmation frames as closed bars (`apex_ai/scalper_agent.py:706-713`, `2287-2313`). Current decision frames are M15 trigger and M5 confirmation, despite legacy local variable names `df_m5` and `df_m1` (`apex_ai/scalper_agent.py:706-710`).
3. Cooldown and one-open-position-per-symbol dedup run before trigger analysis (`apex_ai/scalper_agent.py:720-744`).
4. `step1_liquidity()` builds `MicroLiquidity` from the last 50 trigger-frame highs/lows and the current calendar day's high/low (`apex_ai/scalper/trigger_engine.py:277-295`).
5. `step2_trigger()` evaluates enabled detectors in fixed order and returns the first detected candidate (`apex_ai/scalper/trigger_engine.py:297-398`).
6. The selected candidate passes reclaim, optional EMA-band, optional previous-day-range, optional regime-direction, optional H4 VP, optional VP-leg, STB, confidence/thin-liquidity, consultant regime/displacement, spread/cost and CRG gates (`apex_ai/scalper_agent.py:911-1293`).
7. Lot sizing occurs, then the live path obtains a fresh executable quote and submits a market order with rounded SL and TP1 (`apex_ai/scalper_agent.py:1295-1333`, `1456-1575`).

## 3. Exact detector rules

The detector is `SATriggerEngine._check_sweep_rejection()` (`apex_ai/scalper/trigger_engine.py:559-615`).

| Rule | Current implementation | Evidence |
|---|---|---|
| Trigger frame | M15 in the live caller; M15 in replay | `scalper_agent.py:706-710`; `backtest_scalper.py:1100-1107` |
| Reference liquidity | `nearest_ssl`/`nearest_bsl` from M15 equal highs/lows plus current-day session extreme | `trigger_engine.py:43-56`, `277-295` |
| Reference identity | Only numeric level; no origin/type is carried in `SATrigger.swept_level` | `trigger_engine.py:71-72`, `588`, `610` |
| Sweep depth | Any recent low below SSL or high above BSL; no ATR/spread minimum in this detector | `trigger_engine.py:571-575`, `593-597` |
| Reclaim | Latest bar closes back across the level and has same-direction body | `trigger_engine.py:574-575`, `595-596` |
| Wick | Measured on the extreme bar; optional filter only | `trigger_engine.py:575-578`, `597-600`; ratio helper `93-111` |
| Direction | SSL breach plus bullish close => BULLISH; BSL breach plus bearish close => BEARISH | `trigger_engine.py:571-590`, `593-612` |
| Displacement/MSS/CHoCH | None required by this detector; `requires_displacement` remains false | `trigger_engine.py:72`, `581-590` |
| Confidence | Always `HIGH` after the local conditions pass | `trigger_engine.py:589`, `611` |
| Stop | Extreme of the last four trigger bars, buffered by `0.2 * ATR` | `trigger_engine.py:566-580`, `595-602` |
| Target | TP1/TP2 are fixed R multiples from entry-to-stop; current defaults 2R/3R | `trigger_engine.py:198-213`, `266-273`, `579-587` |

The detector therefore validates “a nearby locally defined level was pierced and reclaimed,” not “a known meaningful liquidity pool was raided.” It has no explicit sweep-depth requirement, no level freshness/consumption state, and no location-to-HTF-structure relation.

## 4. Complete live decision path

`market data → trigger → gates → order` is:

```text
MT5 copy_rates_from_pos(shift=1)
  -> M15 trigger / M5 confirmation
  -> MicroLiquidity from last 50 M15 bars + today's M15 high/low
  -> step2_trigger fixed priority
  -> optional reclaim sequence
  -> optional H1 EMA band
  -> optional previous-day range
  -> optional H1 regime-direction
  -> optional H4 rolling VP location
  -> optional H4 leg-confluence location
  -> STB range/session liquidity and intraday bias
  -> STB confidence / thin-liquidity
  -> consultant regime whitelist; displacement only for BOS_RETEST
  -> spread, SL floor, net-R cost gate
  -> news state and SA-CRG
  -> lot floor
  -> fresh bid/ask, rounded SL/TP1, MT5 market order
```

The closed-bar claim is sound for normal decision frames: `_get_ohlcv()` starts at position 1 and explicitly documents why position 0 would repaint (`apex_ai/scalper_agent.py:2287-2308`). Replay slices M15 to bars strictly before the decision M15 bar and M5 to timestamps before `now` (`apex_ai/backtest_scalper.py:1095-1107`). The replay also mirrors the trigger engine and enabled-trigger whitelist (`backtest_scalper.py:757-764`, `1187-1200`).

The final order does not use the detector's close as the actual fill. It uses the current executable tick (`scalper_agent.py:1461-1468`), then sends SL and TP1 in the request (`1542-1564`). For ordinary sweep trades there is no sweep-specific final quote/location revalidation beyond generic spread/geometry checks.

## 5. Trigger priority/pre-emption behaviour

`step2_trigger()` does not let the CLI whitelist define priority. The code evaluates in this effective order:

1. `SESSION_SWEEP` when enabled and allowed
2. `SWEEP_REJECTION`
3. `HTF_CRT_SWEEP`
4. `VP_LIQUIDITY_REACTION`
5. M15 `FVG_FILL` plan or local FVG
6. `BOS_RETEST`
7. `JUDAS`
8. `VALUE_AREA_FADE`

Evidence: `apex_ai/scalper/trigger_engine.py:336-393`. `consider()` records every fired detector but sets `winner` only once, at the first detected candidate (`320-334`); the observer cannot alter selection (`325-331`).

The live log confirms real pre-emption:

- 2026-09-16 07:47:43Z: sweep selected over `BOS_RETEST, JUDAS`, then passed reclaim, STB, consultant and CRG and executed ticket `1120463153` (`apex_ai/logs/scalper_agent.log.1:39915-39921`).
- 2026-09-16 11:00:17Z: sweep selected over `JUDAS`, then executed ticket `1120507562` (`scalper_agent.log.1:41830-41836`).
- 2026-09-17 04:54:18Z: sweep selected over `BOS_RETEST`, then executed ticket `1120811954` (`scalper_agent.log.1:42866-42872`).

The current candidate funnel has 11 recorded pre-emptions, all `JUDAS -> BOS_RETEST`, because the current default excludes sweep. It does not prove that sweep pre-emption is absent; it proves only that current funnel collection has not observed sweeps under the current default configuration. The rotated logs contain repeated sweep-priority messages; the raw count is not a unique-candidate count because the same still-valid bar is revisited every scan cycle.

## 6. What currently defines “meaningful area”

For ordinary `SWEEP_REJECTION`, no single meaningful-area permission exists. The effective location checks are partial and mostly downstream:

- `MicroLiquidity` uses local equal highs/lows and current-day extremes (`trigger_engine.py:277-295`), but its output loses origin identity before the trigger is created.
- STB has a range-extreme guard for fades and a session-pool “don't chase” rule (`short_term_bias.py:348-422`). This can block internal-range fades or approve a counter-sweep, but it does not require the sweep candidate itself to be at a named HTF/session level.
- Previous-day range, H4 VP, regime-direction and VP-leg gates are optional switches, not a universal causal location object (`scalper_agent.py:936-1049`; defaults in `decision_params.py:287-347`).
- The regime whitelist admits `SWEEP_REJECTION` in `MANIPULATION` and `ROTATION`, but regime is not price-location identity (`decision_params.py:66-78`).

The practical answer today is: “meaningful” means a local level plus whatever optional vetoes happen to be enabled. It does not mean the swept level is PDH/PDL, a session high/low, equal highs/lows with provenance, HTF liquidity, or a fresh POI.

## 7. What location information already exists but is unused

| Evidence family | Exists in repository? | Used by ordinary SWEEP_REJECTION? | Notes |
|---|---:|---:|---|
| M15 local equal highs/lows | Yes | Yes, but only as an anonymous numeric level | `trigger_engine.py:285-287`, `784+` |
| Current-day/session extreme | Yes | Yes, only as anonymous numeric level | `trigger_engine.py:289-293` |
| Session pool identity and consumed state | Yes | Indirectly in STB, not attached to sweep | `short_term_bias.py:220-292` |
| PDH/PDL | Yes | Optional veto only; default OFF | `pdr_gate.py:105-180`; `decision_params.py:318-347` |
| Premium/discount | Yes | Computed by core consultant; not retained as sweep location | `core/liquidity_engine.py:28-55`; `sa_consultant.py:112-162` |
| H1/H4 confirmed structure | Yes | Used for STB/regime context, not sweep identity | `short_term_bias.py:434-491`; `core/structure_engine.py:47-121` |
| M15/H1 FVG | Yes | Used by separate FVG/reclaim paths; not required by sweep | `m15_fvg_entry.py`; `reclaim_fvg.py`; `trigger_engine.py:617-670` |
| Order blocks | Yes in core displacement/execution models | No ordinary sweep permission | `core/displacement_engine.py:32-49`; `core/execution_models.py:85-141` |
| Breaker/IFVG | No deterministic ordinary-sweep object located | No | No evidence found in current decision path |
| Anchored H4 profile / POC / VAH / VAL | Yes | Separate VPLR/VP gates; not ordinary sweep identity | `anchored_vp.py:155-200`; `vp_gate.py:154-248`; `decision_params.py:350-386` |
| Session VP levels | Partial | Not attached to ordinary sweep | Session VP/VPLR components are trigger-specific |
| Round numbers | No evidence in current path | No | Do not infer from price formatting |
| Fresh/untouched/consumed liquidity state | Partial for session/FVG/reclaim | No ordinary sweep state | Session pool and FVG modules track state independently |

The strongest reusable pieces are `SessionPool`, `PDRGate`, `VolumeProfileGate`, anchored VP/leg confluence and the core `LiquidityEngine`. None currently returns a common `MeaningfulArea` object consumed by `SWEEP_REJECTION`.

## 8. Recent candidate examples

These examples distinguish event validity from location validity. The logs confirm downstream decisions, but the current telemetry schema records ordinary sweep candidates as `entry_location=OTHER`, `reference_level=UNKNOWN`, and `liquidity_event_type=NONE`; it cannot prove a named liquidity location retroactively.

### Three sweep candidates that became trades/setups

| Time | Direction / geometry | Downstream path | Location conclusion |
|---|---|---|---|
| 2026-09-16 07:47:43Z | BULLISH; entry 4336.4380, SL 4323.9029, TP1 4361.5082 | Reclaim passed as `RECLAIM_NO_BROKEN_LEVEL`; STB passed MEDIUM; regime MANIPULATION; CRG approved; ticket `1120463153` | Sweep/reclaim was accepted. No named swept liquidity or HTF POI was recorded. |
| 2026-09-16 11:00:17Z | BULLISH; entry 4351.5430, SL 4336.2915, TP1 4382.0460 | Reclaim passed; STB HIGH with HTF BULLISH; regime MANIPULATION; ticket `1120507562` | Event and gates passed. CRT watch showed a D1 high at 4317.493 and raid extreme 4351.543, but the ordinary sweep candidate did not carry that identity. |
| 2026-09-17 04:54:18Z | BULLISH; entry 4295.1630, SL 4288.7999, TP1 4307.8893 | Reclaim passed; STB MEDIUM; regime MANIPULATION; CRG approved; ticket `1120811954` | Accepted as a valid micro event, but log evidence still does not establish why 4295.1630 was a meaningful POI. |

The execution telemetry for the 2026-09-14 through 2026-09-16 sweep sample contains 9 `CANDIDATE_OBSERVED` sweep records and 9 matching successful `ENTRY_EXECUTION` records. Every observed record has `entry_location=OTHER`, `reference_level=UNKNOWN`, `liquidity_event_type=NONE`, and null raid depth/MSS fields (`apex_ai/logs/sa_execution_telemetry.jsonl`). This is direct evidence of missing causal location labeling, not proof that every event was economically random.

### Three sweep candidates rejected downstream

| Time | Candidate | Rejection | Event/location conclusion |
|---|---|---|---|
| 2026-09-17 04:43:30Z | XAUUSD BEARISH sweep; selected over `BOS_RETEST, JUDAS` | `RECLAIM_WAIT_LEVEL`, broken level 4276.358, no reclaim/FVG | The local sweep detector fired; the downstream sequence gate rejected it. Location identity was still not attached. |
| 2026-09-17 04:43:30Z | USOIL BULLISH sweep | `RECLAIM_WAIT_FVG`, active broken level 97.377 | Detector event was accepted by selection but not by the optional causal reclaim sequence. This is not evidence that the sweep itself was invalid. |
| 2026-09-17 04:43:30Z | XAGUSD BEARISH sweep; selected over `BOS_RETEST` | Step 3 rejected SL 17.5p below the 120p XAGUSD institutional floor | The event passed reclaim, STB and MANIPULATION regime, but geometry rejected it as noise-grade. This is the clearest example of a valid-looking local event failing risk geometry rather than location permission. |

Evidence: `apex_ai/logs/scalper_agent.log.1:42801-42809`. The current rejection ledger also records sweep `GATE1_REGIME`, STB, lot-floor and Step-3 rejections across 2026-08-28 through 2026-09-17 (`apex_ai/logs/sa_rejections.json`).

## 9. Primary greed mechanism(s)

### High — location permission is absent

**Current behavior:** The detector only compares the last four M15 bars to the nearest anonymous local/session level and emits `HIGH` confidence (`trigger_engine.py:559-613`).  
**Problem:** A local high/low is not necessarily a meaningful resting-liquidity pool. No code asks what was swept, whether it was fresh, whether it was already consumed, or whether the entry is at a structural/auction POI.  
**Enhancement later:** Add a deterministic location/permission layer after event detection and before ordinary downstream admission, using existing location components where causally available. Do not rewrite the sweep detector.  
**Impact:** High; profitability/location quality.  
**Complexity:** Medium.

### High — sweep owns the candidate before stronger evidence can win

**Current behavior:** The first detected candidate is retained, and sweep is before CRT, VP reaction, FVG, BOS and Judas (`trigger_engine.py:320-398`).  
**Problem:** A technically valid local sweep can pre-empt a candidate with a more explicit location or structure contract. The live log repeatedly records sweep selected over BOS/Judas (`scalper_agent.log.1:39915`, `41830`, `42866`).  
**Enhancement later:** Preserve overlap telemetry, but define a measured selection policy or a location-aware permission stage before ownership. Any change must be mirrored in replay.  
**Impact:** High; selector bias and opportunity cannibalisation.  
**Complexity:** Medium.

### Medium — candidate telemetry cannot audit ordinary sweep causality

**Current behavior:** Entry telemetry only derives a causal location identity for reclaim, FVG, CRT or session-sweep objects; ordinary sweep falls back to `OTHER`/unknown (`execution_telemetry.py:241-297`, `298-333`).  
**Problem:** The system cannot quantify distance to named liquidity, FVG/OB, VP levels, freshness, or pre-emption for ordinary sweep candidates after the fact.  
**Enhancement later:** Extend observation-only fields first, before making a permission rule.  
**Impact:** Medium; evidence quality.  
**Complexity:** Easy to Medium.

### Medium — trigger and test defaults have drifted

**Current behavior:** `SATriggerEngine.ALL_TRIGGERS` excludes `SWEEP_REJECTION` (`trigger_engine.py:219-226`), while `step2_trigger()` still contains the detector (`297-350`); the CLI default also excludes it (`scalper_agent.py:2879-2883`).  
**Problem:** Focused tests still expect sweep to be a valid enabled trigger. Running the relevant suite produced 65 tests with 1 failure and 2 errors: both priority-direction cases reject `SWEEP_REJECTION` as unknown, and the volume-profile default assertion expects `VPLR_ENABLED=False` while the current branch is true.  
**Enhancement later:** Reconcile tests and current defaults deliberately; do not silently re-enable sweep.  
**Impact:** Medium; verification and reproducibility.  
**Complexity:** Easy.

## 10. Replay/live parity risks

The main closed-bar parity repair is present and good: live uses shift 1, replay excludes the forming M15/M5 bars, and both call the same trigger engine (`scalper_agent.py:2287-2308`; `backtest_scalper.py:1095-1107`, `1187-1200`).

Remaining risks are narrower:

- The live ordinary sweep path uses a fresh real tick for execution while replay uses a bar fill model (`scalper_agent.py:1461-1564`; `backtest_scalper.py:1396-1419`). This is expected execution modeling, but not identical entry price evidence.
- Live calls `candidate_observer=funnel_seen.append`; replay currently does not pass the observer in its `step2_trigger()` call (`backtest_scalper.py:1187-1197`). Thus overlap/pre-emption funnel telemetry is not symmetric even though selection is shared.
- `MicroLiquidity.step1_liquidity()` derives “today” from the last frame date, while STB session tracking derives pools using injected `now` (`trigger_engine.py:289-293`; `short_term_bias.py:236-292`). Cross-midnight/replayed historical data must keep those clocks aligned.
- Optional gates must be enabled identically for a fair comparison. The branch explicitly mirrors many of them, but ordinary sweep location identity is absent in both paths, so replay cannot answer the missing-location question without additional observation fields.

## 11. Reusable existing components

The smallest reusable building blocks are:

1. `SessionLiquidityTracker` for named session high/low, taken time and taker session (`short_term_bias.py:220-292`).
2. `PDRGate` for causal previous-day high/low and premium/discount classification (`pdr_gate.py:105-180`).
3. `LiquidityEngine` for typed swing/equal/session/previous-day pools and premium/discount (`core/liquidity_engine.py:14-121`). It is not currently wired into the ordinary scalper trigger path as a typed location result.
4. `StructureEngine` for right-side-confirmed swings and BOS/MSS context (`core/structure_engine.py:47-121`).
5. `m15_fvg_entry.py` and `reclaim_fvg.py` for fresh/consumed FVG and broken-level sequence state.
6. `anchored_vp.py`, `vp_gate.py` and `leg_confluence.py` for completed-leg POC/VAH/VAL context (`anchored_vp.py:155-200`; `leg_confluence.py:269-384`).
7. `CandidateFunnelRecorder` and execution telemetry for observation-only measurement (`candidate_funnel.py:80-220`; `execution_telemetry.py:690-860`).

No existing common `MeaningfulArea` object was found. The repository has the ingredients, but they are represented by separate trigger/gate-specific dataclasses and are not attached to ordinary `SATrigger` instances.

## 12. Smallest safe architectural insertion point

The safest future insertion point is immediately after `_check_sweep_rejection()` produces a detected `SATrigger`, before `consider()` assigns it as the winner, with the same pure function called from live and replay:

```text
closed M15/M5 frames
  -> existing local sweep/reclaim event
  -> deterministic location_context / meaningful_area permission
  -> existing selector and matched-trigger telemetry
  -> existing STB, consultant, cost and CRG gates
  -> existing entry geometry and order proposal
```

The permission result should be observation-rich even if the first experiment is veto-only, for example: area type, source timeframe, bounds, distance in ATR, freshness/consumption state, and liquidity identity. It should not alter entry, SL, TP or risk geometry in the first experiment.

Do not insert it inside the wick/depth detector, because that would conflate “did a sweep/rejection happen?” with “is this sweep worth trading?” and would make the proven event detector harder to compare against its own baseline.

## 13. Recommended next experiment — NO implementation

Run a shadow-only, no-order, two-arm replay on the same chronological windows:

- Arm A: current sweep detector and current selector.
- Arm B: same detector and selector, but compute and record a `location_context` without vetoing.

For every sweep candidate, record only facts available at decision time: typed swept level, source timeframe, session/PDH/PDL/equal-level identity where present, distance to candidate POIs in ATR, fresh/consumed state, STB/range location, matched triggers, winner, and downstream terminal reason. Compare accepted/rejected candidates and pre-empted overlaps by fold, direction, session and symbol. Do not promote a permission rule from the existing 9-candidate telemetry sample; it lacks ordinary sweep location identity and is too small for a strategy claim.

The next decision should be based on whether a location family separates outcomes consistently across all chronological folds, not on whether a single named level looks visually persuasive.

| Stage | Current behaviour | Problem? | Evidence | Change needed later? |
| ----- | ----------------- | -------- | -------- | ------------------- |
| Market data | Live and replay use closed M15/M5 decision frames | Low residual parity risk | `scalper_agent.py:2287-2308`; `backtest_scalper.py:1095-1107` | Keep; add parity telemetry |
| Micro liquidity | Last 50 local highs/lows plus current-day extreme | Yes: anonymous/local, not economic liquidity identity | `trigger_engine.py:277-295` | Add typed context outside detector |
| Sweep event | Any breach plus same-bar reclaim/body direction; optional wick ratio | Yes: no depth, freshness, MSS or displacement | `trigger_engine.py:559-615` | Preserve; measure then permission |
| Confidence | Every detected sweep is `HIGH` | Yes: confidence overstates contextual evidence | `trigger_engine.py:589`, `611` | Recalibrate only after location study |
| Selector | First detected wins; sweep is near the head | Yes: pre-emption/cannibalisation | `trigger_engine.py:320-398`; live log lines cited above | Measure overlap, then redesign selection if needed |
| Session/cooldown/dedup | State, session, cooldown and one-position dedup gate scans | Mostly no; these are operational gates, not location permission | `scalper_agent.py:579-635`, `720-744` | Keep |
| STB | Range extreme and named session-sweep awareness | Partial: can veto/chase-protect, but does not annotate ordinary sweep | `short_term_bias.py:348-422` | Reuse session context |
| HTF/auction location | Separate optional PDR/VP/leg components | Yes: not a common permission and defaults are mostly off | `scalper_agent.py:961-1049`; `decision_params.py:287-347` | Build a common observation object |
| Reclaim/structure | Reclaim is optional/sequence-specific; sweep itself has no MSS/CHoCH | Yes for contextual quality | `scalper_agent.py:911-935`, `1229-1242` | Test as evidence family, not automatic mandatory gate |
| Entry geometry | 0.2 ATR stop buffer, TP1 2R/TP2 3R, spread/SL/net-R checks | No primary greed defect; geometry rejects some noise | `trigger_engine.py:503-555`, `579-587` | Leave unchanged in this audit |
| Risk/order | CRG, lot floor, fresh quote, MT5 SL/TP1 order | No location fix here | `scalper_agent.py:1277-1333`, `1456-1575` | Leave unchanged |
| Telemetry | Ordinary sweep location falls back to OTHER/UNKNOWN | Yes: cannot quantify the suspected defect | `execution_telemetry.py:241-333`; 9 live sweep observations | Enrich shadow telemetry first |

## Verdict

**D — MULTIPLE CAUSES; LOCATION + SELECTOR BOTH REQUIRE WORK**
