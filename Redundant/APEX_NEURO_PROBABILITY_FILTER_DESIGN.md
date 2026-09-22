# APEX XAUUSD Neuro Probability Filter — Implementation-Ready Design

**Date:** 2026-09-18  
**Scope:** Active online probability gate for the existing XAUUSD SA-V2 deterministic scalper  
**Status:** Design only; no code changes made  
**Decision:** Conditionally GO for implementation; NO-GO for activation until the bootstrap replay and causal-candidate acceptance tests pass.

## Revision delta from the approved design

This revision makes eight explicit changes: (1) production uses a frozen ACTIVE Champion while labels train a separate Learning Challenger; (2) V1 removes `WAIT` and uses only PASS/SKIP; (3) same-bar barrier ordering is resolved with reliable chronological MT5 ticks; (4) labels are side-aware using ASK entry/BID barriers for longs and BID entry/ASK barriers for shorts; (5) the Champion freezes and checksums all preprocessing, calibration, threshold, barrier, family, config, and code/schema metadata; (6) the model domain is explicitly separated into structural event, model-eligible candidate, trade-eligible candidate, and executed trade; (7) Challenger promotion is scheduled, chronological, deterministic, and atomic; and (8) replay determinism is a hard acceptance test.

## Latest mandatory correction addendum

The following sections are authoritative and supersede any conflicting earlier wording in this document.

### 1. Strategy Counterfactual Outcome contract

Every `MODEL-ELIGIBLE CANDIDATE`, whether neuro PASS or SKIP, creates two independent outcome streams:

```text
ML outcome tracker       -> P(+1R before -1R) -> logistic target, Challenger SGD, Brier/calibration/AUC
Strategy outcome tracker -> existing deterministic economics -> threshold calibration and promotion economics
```

The ML label remains the side-aware +1R/-1R fixed-barrier label already defined below. It is not used for PF, expectancy, drawdown, retention, or realized strategy economics.

The strategy tracker must causally replay the existing deterministic strategy: identical entry geometry, SL, TP1/TP2, spread/slippage/commission cost model, timeout, Guardian behavior where reproducible, partial exits, trailing/breakeven, EOD/session exits, and other shipped exit rules. It must emit `gross_R`, `cost_R`, `net_R`, `exit_type`, `MFE_R`, `MAE_R`, and `holding_bars`. A missing or non-reproducible management rule is a parity blocker; no simplified exit model may be substituted silently.

For PASS, record any actual broker trade separately while also recording the deterministic research outcome. For SKIP, place no order but continue the hypothetical deterministic simulation to closure. `SKIP != missing economic outcome`.

### 2. Revised persistence schema

Add a dedicated `strategy_outcomes` table with at least:

```text
candidate_id, snapshot_hash, strategy_version, outcome_version,
counterfactual, entry_time, entry_bid, entry_ask, entry_price,
SL, TP1, TP2, exit_time, exit_price, exit_type,
gross_R, cost_R, net_R, MFE_R, MAE_R, holding_bars,
executed_live, actual_trade_id, created_utc
```

Use `UNIQUE(candidate_id, snapshot_hash, strategy_version, outcome_version)` and an atomic terminal-state update so restart/retry cannot create a second outcome. Keep `actual_trade_id` nullable; broker P/L never replaces the counterfactual outcome.

### 3. Revised Champion/Challenger promotion logic

Calibration and promotion must use the same model-eligible candidate population, the same stored strategy outcomes, the same geometry, and the same costs. Only PASS/SKIP policy decisions may differ. Challenger-only PASS candidates must contribute their stored strategy outcomes even when the Champion skipped them. Never evaluate Challenger economics only on trades executed by the Champion.

For each candidate, replay both policies against the same strategy outcome:

```text
Champion p/policy + stored strategy outcome -> Champion retained economics
Challenger p/policy + stored strategy outcome -> Challenger retained economics
```

Champion/Challenger comparison requires cost-adjusted net-R, expectancy, PF, drawdown, retention, exit-type distribution, and chronological stability. Promotion remains scheduled, prequential, non-inferiority/improvement tested, and atomic; newer data alone is insufficient.

### 4. Execution-drift contract

After Champion PASS and unchanged downstream CRG/exposure/daily-loss checks, obtain a fresh final broker quote and validate it against the frozen neuro snapshot before execution:

```text
entry_price_drift_R = abs(final_executable_entry - snapshot_entry)
                     / frozen_structural_stop_distance
```

Also check final spread-to-stop, final available net-R, entry relation to the frozen FVG/OB/VP location, quote freshness, and broker stop validity. Neuro must not mutate SL, TP, risk, or geometry. Material drift cancels the candidate or requires a genuinely new completed-evidence candidate revision; a quote move alone must not create an endless revision loop.

```text
"execution_drift": {
  "enabled": true,
  "max_entry_drift_r": null,
  "max_spread_to_stop": null,
  "min_final_net_r": null,
  "on_violation": "CANCEL_CANDIDATE"
}
```

Null limits block activation until justified values are preregistered from historical/bootstrap evidence. Untouched test data cannot optimize them. Required events: `EXECUTION_DRIFT_CHECK`, `EXECUTION_DRIFT_PASS`, and `EXECUTION_DRIFT_CANCEL`, including snapshot/final entry, drift R, spreads, net-R, location validity, quote timestamps, and reason.

### 5. Updated candidate lifecycle

```text
DETERMINISTIC SETUP
  -> MODEL-ELIGIBLE CANDIDATE
  -> FROZEN SNAPSHOT
  -> ACTIVE CHAMPION SCORE
  -> PASS / SKIP
  -> ML OUTCOME TRACKER + STRATEGY OUTCOME TRACKER
```

PASS continues through CRG/exposure/daily-loss, fresh quote, execution-drift validation, final cost/risk/broker checks, and MT5 execution. SKIP sends no broker order; both outcome trackers continue. A materially new completed setup creates a new revision linked by `supersedes_candidate_id`. A stale/cancelled candidate cannot later execute.

### 6. Additional tests

- Every model-eligible SKIP receives one faithful strategy counterfactual outcome.
- PASS and SKIP use identical deterministic strategy simulation rules and costs.
- Champion/Challenger comparison uses the same candidate population and includes Challenger-only PASS candidates.
- Strategy outcomes are exactly-once across restart; actual trade P/L never replaces them.
- Strategy replay cannot use future information unavailable to the live deterministic strategy; irreproducible Guardian behavior fails parity.
- Drift within tolerance passes; entry drift, spread deterioration, final net-R degradation, stale quote, or invalid broker stop cancels correctly.
- Frozen SL/TP/risk cannot mutate; cancelled stale candidates cannot execute; new revisions require completed evidence.
- Live restart, backtester, forensic replay, duplicate events, and drift decisions are identical.

### 7. Updated GO / NO-GO criteria

NO-GO if SKIPped candidates lack faithful deterministic strategy outcomes; Champion and Challenger cannot be compared over the same population; deterministic exits cannot be replayed with live parity; final execution can materially differ from the scored snapshot without a drift guard; drift limits are missing, arbitrary, or optimized on untouched test data; or any strategy outcome/update can duplicate after restart.

## Executive decision

V1 should be a small, online logistic-regression filter over one homogeneous setup family:

`HTF/session raid -> failed acceptance/reclaim -> completed M15 displacement/MSS -> frozen M15 VP/FVG/OB location -> completed M5 return/retest`

The model receives an already-valid deterministic candidate and returns `p_success`. It never supplies direction, geometry, risk, TP, SL, or a trade. The deterministic path remains the authority for all of those decisions. The neuro decision is inserted after the existing deterministic gates and cost validation, immediately before lot sizing/execution.

The production filter is not shadowed: the ACTIVE Champion gates live candidates. The Champion does not mutate after each label. Newly matured labels update a separate Learning Challenger; only a scheduled chronological validation can promote that Challenger atomically into a new immutable Champion artifact. The first Champion is created from chronological bootstrap, calibration, and minimum evidence requirements. Every model-eligible candidate, including SKIPped candidates, receives a counterfactual fixed-barrier label.

This is consistent with the cited MQL5 online-learning design: logistic SGD is cheap and auditable, inputs must be scaled, weights must persist, and updates occur only after the outcome is known. The article's synthetic results are implementation evidence, not evidence of an XAUUSD edge: [Online Machine Learning for Trade Signal Filtering in MQL5, Part 1](https://www.mql5.com/en/articles/23700). The NeuroBook is background material only: [MQL5 NeuroBook](https://www.mql5.com/en/neurobook/).

## 1. Current repository findings

### What can be reused

- `apex_ai/scalper_agent.py:695` contains the live `_scan_symbol` funnel. It fetches the M15/M5 stack, observes closed-bar state, runs trigger detection, then applies the existing gates.
- `apex_ai/scalper_agent.py:846` calls `SATriggerEngine.step2_trigger`; `apex_ai/scalper/trigger_engine.py:297` owns trigger selection and priority. The caller already passes an observation-only candidate callback.
- `apex_ai/scalper/candidate_funnel.py:43` creates stable candidate IDs that exclude wall-clock evaluation time. It supports append-only events, idempotent stages, pre-emption, and terminal states.
- `apex_ai/scalper_agent.py:1012-1242` contains VP, leg-confluence, STB, consultation, regime, and displacement gates. These must remain upstream of neuro.
- `apex_ai/scalper_agent.py:1251-1288` validates spread/cost and CRG. Neuro must not bypass either gate; the proposed boundary is after this validation and before `:1295` lot sizing/execution.
- `apex_ai/backtest_scalper.py` already mirrors the live M15/M5 gate chain, uses closed-frame helpers, per-bar broker spread where available, and realistic execution-cost accounting. This is the correct bootstrap/replay foundation.
- `apex_ai/scalper/regime_evidence.py` records candidate-time snapshots and later outcomes, but it is regime evidence rather than a complete neuro sample store.
- `apex_ai/scalper/trade_logger.py:37` is trade-centric and only logs executed/opened trades. It cannot be the learning population because rejected candidates do not become trade records.
- `docs/RESEARCH_NOTES.md` records the decisive research constraints: the fresh walk-forward was negative and fold signs unstable; earlier results were invalidated by gate-fidelity defects; a live edge must not be claimed from a single window.
- `docs/reviews/2026-09-17-candidate-funnel.md` confirms the existing funnel is observation-only and currently lacks producer-specific evidence adapters for full BOS/STB, M5 confirmation, frozen VP/FVG/OB, rejected-candidate outcomes, and fold reporting.

### Important gaps and risks

1. The current funnel observes detected triggers, but the neuro sample must be created only after the full deterministic candidate contract is valid. Every downstream veto must still leave a sample that can mature to a counterfactual label.
2. The funnel ID and `regime_evidence.candidate_id` are different identity schemes. V1 needs one canonical neuro candidate ID and an alias map, not two independently generated IDs.
3. The live path currently uses `CandidateEvidenceSnapshot` for selected/gated candidates and `SATradeRecord` for trades. Neither is sufficient for all rejected candidates or exactly-once label updates.
4. The current funnel's `WAIT` status is unrelated telemetry. Neuro V1 has no WAIT decision; one frozen candidate receives one feature vector and one score.
5. A live quote at decision time and the frozen trigger geometry must be copied into the sample. Reconstructing spread, VP, FVG, OB, or stop later is not acceptable.

### Stop-condition assessment

No architectural stop condition is proven yet. The repository has M15/M5 replay infrastructure and data artifacts, so causal reconstruction appears feasible. Activation remains blocked until a replay proves, for a sample window, that it can reproduce the same candidate ID, direction, entry quote, structural stop, target geometry, and upstream gate results using only information available at candidate time. If it cannot, report NO-GO and do not train or activate neuro.

## 2. Exact integration point

Use this order in both live and replay:

```text
trigger detection / priority
  -> deterministic candidate assembly
  -> VP/FVG/OB/liquidity references frozen
  -> STB/regime/consultation/location/session/cost/risk gates
  -> NeuroFeatureExtractor.freeze(candidate)
  -> NeuroModel.predict(x)
  -> ProbabilityPolicy(PASS/SKIP)
  -> existing lot sizing and execution
```

The model boundary is after the model-eligibility gates described below and before the remaining trade-eligibility gates, lot sizing, and execution. If neuro says SKIP, no order is sent. If neuro says PASS, the candidate must still pass every existing downstream gate. All existing gates remain mirrored by `backtest_scalper.py`.

The learning population is defined at the model-eligibility boundary. A sample is created for every model-eligible candidate, regardless of neuro decision. Candidates rejected before model eligibility are structural-event/funnel telemetry only; they are not V1 training samples. This prevents mixing incomplete or out-of-domain events with candidates that could actually reach execution.

## 3. Candidate contract

Create an immutable `NeuroCandidate` at the boundary with:

- `candidate_id`: structural identity only: family/version, symbol, direction, raid/reference event ID, M15 confirmation bar, M5 confirmation bar, frozen structural reference IDs, and candidate revision. It must not include floating entry price, quote timestamp, spread, SL, or TP.
- `snapshot_hash`: hash of the exact BID/ASK, quote timestamp, spread, executable entry, SL, TP, cost geometry, complete frozen feature vector, feature source timestamps, frozen VP/FVG/OB values, and config/schema hashes.
- `schema_version`, `config_era`, `model_family`, `instrument`, `direction`.
- `decision_time_utc`, `decision_m5_bar_close_utc`, `decision_m15_bar_close_utc`, and all source-bar timestamps.
- `entry_bid`, `entry_ask`, executable side price, spread in points/pips, tick size, point, and quote freshness.
- `stop_loss`, structural stop anchor, `tp1`, `tp2`, stop distance in price/pips/ATR, and target distance in R.
- Setup evidence: raid side/depth, reclaim/failed-acceptance measure, M15 displacement/MSS, M5 return/retest, frozen FVG/OB/VP IDs and prices, session, regime, STB direction/confidence, net-R/cost values.
- `features_raw`, `features_scaled`, feature schema hash, snapshot hash, Champion artifact ID, probability, policy decision, and policy version.
- `upstream_gate_snapshot`: pass/fail and reasons, for audit only; neuro cannot mutate it.

Direction and structural references are copied from the deterministic trigger and are read-only. A candidate with missing required evidence is `INVALID_DATA`, not a low-probability sample. If materially new completed evidence changes the setup, create a new candidate revision linked through `supersedes_candidate_id`; do not mutate the structural identity or silently rescore the old snapshot.

### Model-domain contract

The population is deliberately narrower than the entire trigger funnel:

1. **STRUCTURAL EVENT:** a raid, reclaim, MSS, FVG/OB/VP formation, or M5 return observed by the funnel. It may be incomplete and is never automatically a model sample.
2. **MODEL-ELIGIBLE CANDIDATE:** the complete V1 sequence is present; all required M15/M5 bars are closed; direction, executable quote, structural stop, target, frozen references, and required features are valid; and all deterministic gates that define the V1 model domain have passed.
3. **TRADE-ELIGIBLE CANDIDATE:** a model-eligible candidate whose neuro result is PASS and which passes the existing post-neuro trade gates, lot floor, and final quote checks.
4. **EXECUTED TRADE:** a trade-eligible candidate for which the broker returns a confirmed order/position.

For V1, model eligibility occurs after trigger priority/deduplication, completed M15/M5 sequence validation, frozen VP/FVG/OB/location construction, session eligibility, STB, regime/consultation/displacement gates, and `step3_validate` cost/geometry validation. Neuro then scores. After a PASS, the existing CRG/daily-loss/exposure check, quote freshness/final cost validation, lot sizing/lot floor, broker validation, and execution remain unchanged and downstream. No safety, risk, or execution gate is moved merely to increase model sample size.

Every model-eligible candidate receives a counterfactual label whether it PASSes or SKIPs. A structural event that never reaches model eligibility is not silently converted into a negative label.

## 4. Feature schema V1

Use 12 numeric/context features plus one-hot categorical context. Keep the initial model small. All numeric inputs are clipped to configured bounds after normalization; clipping is logged.

| Feature | Source/time | Formula and normalization | Missing rule / causality proof |
|---|---|---|---|
| `raid_depth_atr` | raid timeframe, completed raid bar | `(raid extreme - swept level) / ATR_M15`; signed by direction; clip `[-5,5]` | Required for V1 family; reject sample if absent. Raid bar is closed before candidate creation. |
| `reclaim_strength_atr` | M15, through completed reclaim bar | signed close displacement beyond level / ATR_M15 | Required; only bars closed at `decision_time` are used. |
| `m15_displacement_body_atr` | M15 completed MSS bar | `abs(close-open) / ATR_M15` | Required; ATR uses only prior completed bars. |
| `m15_close_location` | M15 completed MSS bar | `(close-low)/(high-low)`, mapped to `[-1,1]` by direction | Zero-range maps to `0`; no current/forming bar. |
| `m5_return_location` | completed M5 return bar | normalized location within frozen FVG/OB/VP zone | Required for the family; candidate is created only after the M5 bar closes. |
| `distance_to_frozen_poc_atr` | frozen M15/H1 VP | signed `(entry-POC)/ATR_M15` | Frozen profile and anchor timestamp are stored; never recalculate after entry. |
| `distance_to_frozen_value_edge_atr` | frozen VAH/VAL | distance to direction-relevant value edge / ATR_M15 | Use the profile snapshot created at the candidate timestamp. Missing VP means `INVALID_DATA`, not imputation. |
| `distance_to_fresh_fvg_ob_atr` | frozen FVG/OB | distance from executable entry to nearest valid family location / ATR_M15 | Reference must have formation and validity timestamps <= decision time. |
| `stop_distance_atr` | deterministic geometry | `abs(entry-SL)/ATR_M15` | Copied from trigger; never recomputed from later candles. |
| `target_distance_r` | deterministic geometry | `abs(TP1-entry)/abs(entry-SL)` | Copied target; neuro cannot change it. |
| `spread_to_stop` | candidate quote | `spread_price / abs(entry-SL)` | Quote must be fresh; if unavailable, candidate is invalid. |
| `available_net_r` | existing cost gate | `(reward - round_turn_cost)/(risk + round_turn_cost)` | Use the same cost model as `step3_validate` and replay. |
| `session_minute_sin`, `session_minute_cos` | UTC decision time | cyclical minute within configured session | Session label is also one-hot; no local time. |
| `stb_confidence_ord` | STB snapshot | LOW/MEDIUM/HIGH -> `0/0.5/1` | Snapshot is computed before decision and stored. |
| `regime_one_hot` | consultant/regime snapshot | one-hot for allowed V1 regimes | Unknown/STRESS is invalid or upstream-vetoed, never imputed. |

The table contains 15-ish effective inputs depending on categorical encoding. Do not add RSI, MACD, Stochastic, or broad indicator banks in V1. ATR is used only to make geometry comparable across XAUUSD volatility states. Volume/order flow may be added only as a separately preregistered feature experiment with causal timestamp proof.

For every extractor output, store: source timeframe, exact source timestamp, `bar_closed=true`, formula version, normalization version, missing-data rule, and source-bar hash. The extractor must fail closed if a required frame is incomplete or has a timestamp after decision time.

## 5. Label contract

V1 label family is independent of whether an order was executed:

```text
entry = candidate executable side quote at t0
risk  = abs(entry - frozen structural stop)
upper barrier = entry + direction * 1.0 * risk
lower barrier = entry - direction * 1.0 * risk
expiry = t0 + configured M5 bars (default 24 M5 bars)
```

For LONG, entry is executable ASK and both barrier touches are evaluated on BID. For SHORT, entry is executable BID and both barrier touches are evaluated on ASK. `time_bars=24` means 24 subsequent completed broker M5 bars after candidate creation, not `decision_time + 120` wall-clock minutes. Market closures, feed gaps, and missing bars do not advance the count; expiry occurs only after the 24th subsequent completed M5 bar is available. Reliable chronological MT5 ticks resolve intrabar ordering. If both barriers are touched within an M5 bar, inspect the tick path and assign the first executable barrier reached. Missing, incomplete, non-monotonic, or uncovered bid/ask tick history produces `AMBIGUOUS`, excluded from V1 training; never substitute midpoint, candle close, or arbitrary OHLC ordering. Positive first is label `1`; negative first is label `0`; neither by the counted-bar expiry is `CENSORED`. Persist entry/barrier sides and prices, first-tick timestamp, tick coverage, completed-bar sequence/count, and spread/slippage assumptions.

The schema must include `barrier_multiple` so the same sample can later produce `0.5R`, `1R`, `1.5R`, and `2R` labels without rewriting storage. Store `label_family`, `label_version`, `outcome_status`, `mfe_R`, `mae_R`, `expiry_utc`, and `label_matured_utc`.

V1 is binary: `p_success >= frozen_pass_threshold -> PASS`; otherwise `SKIP`. PASS only permits continuation; it never authorizes execution. A candidate is scored once for one immutable feature vector and one Champion artifact. If later completed information materially changes the setup, create a new causal candidate revision with a new canonical ID linked by `supersedes_candidate_id`; never silently rescore the original observation. The existing funnel `WAIT` status is not a neuro policy state.

## 6. Historical bootstrap process

1. Freeze the V1 family definition, feature schema, label version, cost assumptions, and configuration hash in a manifest.
2. Use the existing `backtest_scalper.py` replay path, not a separate simplified signal generator. Refactor conceptually into a producer that emits `NeuroCandidate` objects before neuro policy, while preserving the current baseline output.
3. Replay XAUUSD only over data with known M15/M5 bars and spread source. Use the existing closed-frame helpers and per-bar broker spread. No random split.
4. Verify candidate parity against the live decision path: same setup family, direction, entry quote, structural stop, target, upstream gate result, and candidate ID for a fixture window.
5. Drop `INVALID_DATA`, ambiguous barrier order, and censored observations from the primary fit; report their counts and test that exclusions are not concentrated in one session/regime.
6. Split chronology into bootstrap-train, calibration, validation, and untouched test periods. A practical first requirement is at least 300 mature uncensored candidates, with at least 100 positive and 100 negative labels overall and no fold with fewer than 30 of either class. If this cannot be met, keep neuro disabled.
7. Fit the initial scaler using training data only. Train logistic SGD sequentially in timestamp order; record the pre-update probability for every sample, then update only after its label matures. Do not train on a label before its chronological maturation timestamp.
8. Select the probability policy and calibration method on the calibration period only. Freeze thresholds, scaler, feature order, model version, and policy version before validation/test.
9. Run baseline and neuro replay on identical candidate streams, geometry, costs, and upstream gates. The only difference is the neuro policy.
10. Save the approved artifact manifest and activate only if GO criteria below pass.

## 7. Model specification and Champion/Challenger preprocessing

V1 model: regularized online logistic regression with SGD.

```text
z = bias + dot(weights, x_scaled)
p = sigmoid(clip(z, -30, 30))
error = y - p
weights += learning_rate * (error*x_scaled - l2*weights)
bias += learning_rate * error
```

Recommended initial search ranges, selected chronologically rather than by intuition: learning rate `{0.005, 0.01, 0.02}`, L2 `{1e-5, 1e-4, 1e-3}`, clip bounds `{3, 5}` standard deviations, and optional learning-rate floor/decay. Select one configuration on bootstrap/calibration and freeze it for the untouched test. Use class weighting only if training imbalance is material and the weighting rule is fixed before validation.

The production model is an ACTIVE Champion. Its weights, scaler, feature transforms, clipping, calibration, and PASS threshold are frozen between promotions. The Learning Challenger uses exactly the same scaler, feature transforms, clipping bounds, feature order, and schema as the Champion; only its adaptive logistic weights/intercept may change online. The Challenger must not update normalization or scaler statistics. If a new preprocessing scheme is required, perform full historical retraining with a new training-only scaler, new model, calibration, chronological validation, and Champion comparison before creating a new immutable artifact. Matured labels update only the Challenger; the Champion is never mutated by an individual label. Any failed validation leaves the Champion unchanged.

## 8. Probability policy

The model predicts the meta-label `P(+1R before -1R)`. Do not equate that probability with a break-even probability for the live strategy's actual TP, partial-exit, guardian, spread, slippage, or timeout geometry.

Threshold selection is an empirical calibration-only procedure:

1. Train and calibrate `P(+1R before -1R)` chronologically.
2. On calibration data only, sweep candidate probability thresholds.
3. For every threshold, replay the existing deterministic strategy economics with unchanged geometry, costs, exits, and gates.
4. Measure actual cost-adjusted net-R/expectancy, drawdown, retention, sample count, and concentration by session/regime.
5. Choose the smallest stable probability region meeting preregistered criteria for expectancy, drawdown, retention, calibration/Brier, and chronological stability.

The +1R/-1R barrier remains the ML label. The realized cost-adjusted deterministic strategy outcome determines whether a probability threshold is useful. Untouched validation/test data must not select the threshold. If no stable calibration-only region meets the criteria, the Champion is not valid for activation.

Policy semantics:

- `PASS`: permit continuation through all unchanged downstream gates; it does not authorize execution.
- `SKIP`: do not execute; retain candidate and later label it counterfactually.
- `DISABLED`: neuro is not activated; deterministic scalper behavior is governed by the explicit pre-activation mode in configuration.

## 9. Online update process

For each candidate:

1. Persist the frozen sample before scoring.
2. Score exactly once for the immutable feature vector and persist `p_success`, Champion artifact ID, snapshot hash, and policy version.
3. Persist PASS/SKIP, but do not remove the sample from the outcome tracker.
4. At each cycle, mature pending samples whose expiry/barrier information is now available from completed M5 data.
5. Atomically claim the sample for labeling using `candidate_id + label_version` as a unique key.
6. Write the immutable label event, then apply exactly one SGD update to the Challenger in a transaction/journaled sequence. Never update the Champion here.
7. Persist `update_id`, Challenger version before/after, update timestamp, and checksum.
8. Mark the sample `MODEL_UPDATED` only after the update and durable state write succeed.

Rejected candidates follow the same lifecycle. Executed trade P/L is recorded separately for operational analysis and must not determine the primary label.

### Champion / Challenger promotion

```text
validated bootstrap artifact -> ACTIVE Champion -> live PASS/SKIP gating
                                      ^
new labels -> Learning Challenger -> scheduled chronological validation
                                      |
                         PASS: atomic immutable Champion vN+1
                         FAIL: Champion unchanged
```

The Challenger starts from the current Champion artifact and receives newly matured labels in chronological order. Promotion evaluation starts only after the configured minimum new-label count is reached. It uses prequential score-before-update evaluation and a chronological holdout that was not used to select the PASS threshold. Promotion requires cost-adjusted expectancy, drawdown, calibration/Brier, probability-bucket ordering, acceptable candidate retention, chronological stability, and zero leakage/fidelity failures. It must also meet a non-inferiority or preregistered improvement margin against the current Champion on the same candidate stream. Newer data alone is never a promotion reason.

Promotion creates a new immutable artifact containing the entire Champion contract, not merely newer coefficients. Write and checksum it, validate it from a fresh process, then atomically update one active-artifact pointer. If any check fails, retain the existing Champion and log the Challenger as rejected. The active Champion always identifies the exact artifact used for each live score.

## 10. Persistence and restart design

Use a small SQLite store under `apex_ai/data/neuro/`, with append-only JSONL telemetry as a human-readable audit stream. Suggested tables:

- `candidates(candidate_id PRIMARY KEY, schema_version, frozen_payload_json, feature_hash, created_utc)`
- `predictions(candidate_id, champion_artifact_id, scored_utc, probability, policy, UNIQUE(candidate_id, champion_artifact_id))`
- `labels(candidate_id, label_family, label_version, status, label, barrier_times_json, matured_utc, UNIQUE(candidate_id, label_family, label_version))`
- `updates(update_id PRIMARY KEY, candidate_id, challenger_before, challenger_after, applied_utc, checksum)`
- `models(artifact_id PRIMARY KEY, role, artifact_path, scaler_hash, feature_schema_hash, policy_version, status)`

The Champion artifact must contain and checksum: feature names/order; formulas/schema version; normalization/scaler values; clipping bounds; coefficients/intercept; calibration parameters; PASS threshold; barrier specification; time horizon; candidate-family version; config hash; code/schema hash; training-window metadata; counts; promotion metrics; and artifact SHA-256. The Challenger references the same frozen preprocessing manifest and may change only adaptive weights/intercept. Promotion validates the entire model/preprocessing/policy artifact as one unit. Save a new immutable artifact to a temporary file, flush/fsync, checksum, then atomically promote the complete artifact manifest. Maintain the prior Champion as last known-good.

On restart: load the active Champion; validate every artifact field/hash/config compatibility; rebuild pending outcome work; resume the Challenger; ignore candidates already present in `labels` or `updates`; reconcile duplicate JSONL records by unique keys. If Champion or store integrity fails, disable neuro and fail closed for live entries. A restart must not alter Champion predictions.

### Replay determinism invariant

The hard reproducibility contract is:

```text
candidate_id + snapshot_hash + Champion artifact ID
    = identical p_success and PASS/SKIP result
```

The invariant must hold across a live-process restart, the historical backtester, forensic replay, and duplicate event processing. `snapshot_hash` covers the exact quote, geometry, features, source timestamps, frozen VP/FVG/OB values, and config/schema hashes. The prediction function must read only that serialized snapshot and the immutable Champion artifact; it must not refetch current market data, use Challenger state, consult wall-clock time, or rebuild references. Duplicate processing returns the already-persisted result and cannot create a second score or label.

## 11. Candidate state machine

```text
CREATED
  -> FEATURE_FROZEN
  -> SCORED
  -> PASS | SKIP
  -> OUTCOME_PENDING
  -> LABEL_MATURED | CENSORED | AMBIGUOUS | INVALID_DATA
  -> MODEL_UPDATED (only for eligible 0/1 labels)
  -> CLOSED
```

`PASS` and `SKIP` are decision events, not terminal states. A candidate may not be scored twice for the same feature hash and Champion artifact. A changed observation creates a new candidate revision and canonical ID. A candidate may not receive two labels or two Challenger updates for the same label version.

## 12. Leakage controls

- Use only M15/M5 bars with close time `<= decision_time`; never use the current forming candle.
- Build ATR and all rolling statistics from prior completed bars only.
- Freeze VP profile, FVG, OB, liquidity level, raid, and MSS references at their formation/decision timestamps. Never rebuild them after outcome maturity.
- Do not use future-confirmed swings, post-entry data, realized P/L, later spread, or later regime state in features.
- Use executable-side entry/barrier prices and the existing broker-cost model.
- Use chronological training, calibration, validation, and test splits. Never randomize rows.
- Score each sample with the frozen Champion before that sample's label update. Persist `champion_artifact_id`; keep Challenger state separate.
- Keep rejected-candidate labels in the same population; do not condition training on execution.
- Resolve same-bar barrier ordering with reliable chronological bid/ask ticks; only missing, incomplete, or uncovered tick history is `AMBIGUOUS` and excluded. Report it.
- Record source timestamps and hashes so a replay can prove every field was available at t0.

## 13. Telemetry schema

Emit one event per lifecycle transition, with `event_id`, `candidate_id`, `event_type`, `event_time_utc`, `decision_time_utc`, `champion_artifact_id`, `challenger_version` where applicable, `policy_version`, `schema_version`, and `config_hash`.

Required event payloads:

- `CANDIDATE_FROZEN`: full deterministic contract, source-bar timestamps, geometry, upstream gate snapshot, raw/scaled features, feature hash.
- `PREDICTION`: probability, ACTIVE Champion artifact ID/checksum, score latency, clipped features.
- `POLICY_DECISION`: PASS/SKIP and the frozen threshold.
- `OUTCOME_MATURED`: barrier result, label, expiry, MFE/MAE, ambiguity/censor reason.
- `MODEL_UPDATE`: update ID, label version, Challenger before/after checksum, learning rate, weight norm.
- `CHALLENGER_VALIDATION`: candidate range, Champion/Challenger comparison, cost-adjusted expectancy, drawdown, Brier/calibration, bucket ordering, retention, fold stability, and leakage/fidelity results.
- `CHAMPION_PROMOTION`: old/new immutable artifact IDs, validation manifest, checksum, promotion or rejection reason.
- `MODEL_HEALTH`: update count, recent Brier/log-loss, calibration bins, drift statistics, missing/invalid counts, duplicate-update count, artifact load status.
- `COUNTERFACTUAL`: outcome for rejected candidates, grouped by policy and probability bucket.

The existing rejection log and trade/execution telemetry remain authoritative for their current purposes. Neuro telemetry must include `executed=false/true` so rejected candidates are queryable without pretending they were trades.

## 14. Walk-forward validation protocol

For each chronological fold:

1. Run the deterministic candidate producer once and persist the candidate stream.
2. Run BASELINE with no neuro policy and NEURO with the frozen Champion policy. Both consume the same candidate IDs, geometry, costs, and upstream gates.
3. For NEURO, score with the Champion before each candidate's label is matured. Update only the Challenger after maturity; the Champion remains unchanged during the fold. The baseline must not receive any neuro-induced changes.
4. Report candidate count, PASS/SKIP counts, expectancy, PF, win rate, net R, drawdown, Brier score, calibration curve, probability deciles, diagnostic AUC, coefficient stability, session/regime breakdown, probability buckets, and rejected-candidate counterfactuals.
5. Report censored, ambiguous, invalid, missing-feature, and duplicate-update counts separately.

Use at least three chronological folds and require the same sign for baseline-vs-neuro incremental net R in development, validation, and untouched test. AUC is diagnostic only; no promotion follows from AUC alone. Compare confidence intervals by bootstrap over contiguous trade/candidate blocks, not iid rows. The final untouched evaluation block is never used to select the PASS threshold or promotion threshold. Challenger promotion is a separate scheduled comparison against the current Champion on new chronological data.

The current research notes already show why this discipline matters: the fresh M15/M5 run was negative with unstable fold signs, and previous results failed when the simulator did not mirror live gates. Neuro V1 must therefore be an incremental filter experiment, not a claim that the underlying deterministic strategy is profitable.

## 15. Configuration additions

Add an externally controlled `neuro_filter` section to the existing config source, with no strategy literals in `scalper_agent.py`:

```json
{
  "neuro_filter": {
    "enabled": false,
    "active": false,
    "family": "RAID_RECLAIM_MSS_M5_RETURN_V1",
    "instrument": "XAUUSD",
    "decision_timeframe": "M5",
    "label": {"barrier_r": 1.0, "time_bars": 24, "exclude_censored": true},
    "model": {"type": "online_logistic_sgd", "learning_rate": 0.01, "l2": 0.0001,
               "clip_z": 5.0, "min_bootstrap_labels": 300},
    "policy": {"pass_threshold": null, "min_pass_labels": 100,
               "calibration_version": null},
    "challenger": {"min_new_labels": 100, "validation_interval_labels": 100},
    "storage": {"db": "data/neuro/neuro.sqlite", "artifact": "data/neuro/model.json"},
    "fail_mode": "HALT_ENTRIES"
  }
}
```

Operating semantics are explicit:

- Before activation: `active=false` means the deterministic scalper operates normally without neuro gating.
- After activation: `active=true` plus a valid Champion, store, schema, checksum, and configuration integrity means the Champion actively gates candidates.
- If an active Champion, store, schema, checksum, or configuration integrity check fails, `HALT_ENTRIES` stops new entries. It must not silently revert to baseline.
- `BYPASS_TO_BASELINE` may be selected only explicitly by the operator; it is never an automatic recovery mode.

`active=true` is accepted only when the artifact manifest says `ACTIVATED`, checksums match, minimum counts pass, and the current config/schema hashes match. A null threshold or missing artifact must not default to an arbitrary threshold.

## 16. Files to add/change

No files were changed in this design phase. Proposed implementation surface:

### Add

- `apex_ai/scalper/neuro_contract.py` — immutable candidate, feature, label, and decision dataclasses.
- `apex_ai/scalper/neuro_features.py` — causal extractor and source timestamp validation.
- `apex_ai/scalper/neuro_model.py` — logistic SGD, scaler, predict/update, checksums.
- `apex_ai/scalper/neuro_policy.py` — frozen calibrated PASS/SKIP threshold.
- `apex_ai/scalper/neuro_outcomes.py` — fixed-barrier tracker and maturity engine.
- `apex_ai/scalper/neuro_store.py` — SQLite schema, transactions, unique keys, atomic artifact management.
- `apex_ai/scalper/neuro_telemetry.py` — lifecycle and health events.
- `apex_ai/tools/bootstrap_neuro.py` — chronological bootstrap/calibration artifact builder.
- `apex_ai/tools/validate_neuro_walkforward.py` — baseline/neuro comparison report.
- `apex_ai/tests/test_neuro_*.py` — focused tests listed below.

### Change surgically

- `apex_ai/scalper_agent.py` — construct/freeze/score/update lifecycle at the exact boundary; do not move existing gates or alter execution geometry.
- `apex_ai/backtest_scalper.py` — emit the same neuro candidate contract and run baseline/neuro as one-variable comparisons.
- `apex_ai/scalper/candidate_funnel.py` — add canonical neuro ID/alias and mature-outcome linkage; preserve observation-only behavior outside the new boundary.
- `apex_ai/config.json` or the project's selected configuration layer — add the externally controlled section.
- `apex_ai/scalper/trade_logger.py` — optional linkage fields only; do not turn the trade log into the learning store.

## 17. Focused tests required

- Closed-bar integrity: forming M5/M15/H1/H4 bars are rejected; source timestamps never exceed decision time.
- Feature formula, scaling, clipping, missing-data, zero-range, and source-hash tests.
- Frozen VP/FVG/OB references do not change when later bars are appended.
- Candidate ID stability across rescans/restarts and distinction for changed geometry.
- Direction/SL/TP/risk invariants: neuro cannot mutate them.
- Logistic prediction bounds, numerical stability, L2 update, learning-rate behavior, and deterministic replay.
- Threshold selection uses only calibration data and never reads test labels.
- Barrier label tests for side-aware executable pricing, tick-resolved same-bar ordering, missing-tick ambiguity, expiry/censored, and cost/slippage handling.
- Exactly-once label and exactly-once SGD update under retries, restart, duplicate JSONL, and transaction failure.
- Model artifact atomic save/load, checksum rejection, last-known-good recovery, and schema mismatch disablement.
- No repeated scoring for unchanged features; candidate revision creates a new canonical ID.
- Champion/Challenger isolation, scheduled promotion, atomic artifact promotion, and failed-promotion rollback.
- Replay determinism: `candidate_id + frozen feature snapshot + Champion artifact ID` produces identical `p_success` and PASS/SKIP across live restart, backtester, forensic replay, and duplicate event processing.
- Rejected-candidate counterfactual maturation and separation from executed P/L.
- Live/replay parity fixture for the chosen homogeneous family.
- Baseline/neuro identical-candidate and identical-cost assertions.
- Activation guard tests for insufficient labels, missing calibration, invalid thresholds, drift alarm, and corrupt artifacts.

## 18. Failure modes and safeguards

| Failure | Safeguard |
|---|---|
| Untrained/cold-start model takes trades | `active` requires a validated bootstrap artifact; otherwise neuro is disabled. |
| Training only on executed trades | Create/persist the sample at the neuro boundary before policy; rejected samples remain pending. |
| Future VP/FVG/OB or current-bar leakage | Freeze source snapshots and assert timestamps/bar-closed flags. |
| Label duplicated after restart | SQLite unique constraints and idempotent update IDs. |
| Model artifact corruption | Atomic replace, checksum, last-known-good artifact, fail closed. |
| Threshold drift silently changes behavior | Thresholds are frozen/versioned; changes require a new calibration artifact. |
| Same-bar barrier ordering | Resolve with reliable chronological executable-side ticks; mark AMBIGUOUS only when tick history is missing, incomplete, non-monotonic, or uncovered. |
| Censoring biases the fit | Exclude from primary fit and report by regime/session; evaluate survival treatment only as a later labeled experiment. |
| Candidate replay differs from live | Parity fixture is a hard activation gate. |
| Neuro mutates deterministic geometry | Immutable contract plus invariant tests immediately before execution. |
| Model learns regime-specific noise | Fold, session, regime, coefficient-stability, and drift reports; no promotion on aggregate metrics alone. |
| Active Champion/store/schema/checksum/config unavailable | Apply `HALT_ENTRIES`; never silently revert to baseline. `BYPASS_TO_BASELINE` is operator-selected only. |

## 19. Implementation sequence

1. **Contract and parity first:** define the homogeneous family, canonical ID, frozen geometry, source timestamps, and replay fixture. Do not add a model yet.
2. **Historical candidate reconstruction:** adapt the existing replay to emit all boundary candidates and prove live/replay parity.
3. **Outcome/store layer:** implement fixed barriers, maturity, SQLite uniqueness, restart recovery, and counterfactual rejected-candidate labels.
4. **Feature extractor:** add the compact causal schema and leakage tests.
5. **Offline chronological bootstrap:** train SGD sequentially, select calibration/thresholds, generate an immutable artifact, and produce baseline/neuro reports.
6. **Live lifecycle integration:** wire scoring and policy at the exact boundary, leaving existing gates and execution untouched.
7. **Online updates and health telemetry:** activate updates only after labels mature; add artifact/version/drift monitoring.
8. **Demo activation review:** require all GO criteria, then enable for XAUUSD V1 only. Any failed integrity check disables neuro.

## 20. GO / NO-GO criteria

### GO for active filtering only when all are true

- At least the configured bootstrap minimum of mature, uncensored, unambiguous V1 labels is available, with both classes represented in every calibration/validation fold.
- Historical replay reproduces the deterministic candidate contract and geometry causally.
- No incomplete/current bars, future-confirmed swings, recalculated profiles, post-entry data, or later spread enters a feature.
- Baseline and neuro differ only by the neuro policy.
- Calibration and threshold selection are frozen before untouched test evaluation.
- Neuro shows stable incremental improvement or risk reduction across chronological folds with realistic costs; no single fold carries the whole result.
- Brier/calibration checks pass the preregistered limits, and probability buckets are monotonic enough to justify a probability interpretation.
- Rejected candidates receive counterfactual labels and appear in telemetry.
- Restart, duplicate event, model corruption, and store failure tests pass.
- Existing STB, regime, spread, news, CRG, broker, SL, TP, and risk invariants remain unchanged.

### NO-GO / disable neuro when any are true

- The candidate family cannot be reconstructed deterministically from historical data.
- Required timestamps/features are contaminated or cannot be proven known at t0.
- Rejected candidates cannot be matured and labeled.
- The minimum bootstrap population or class balance is not met.
- Thresholds are selected from test data or by arbitrary intuition.
- Baseline/neuro candidate streams or geometry differ.
- Labels or SGD updates can duplicate after restart.
- Artifact/schema/config hashes do not match.
- Incremental performance has unstable fold signs or worsens cost-adjusted drawdown without a compelling safety benefit.

The correct NO-GO response is “neuro filtering remains disabled and data collection/replay repair continues,” not a shadow deployment and not an untrained active model.
