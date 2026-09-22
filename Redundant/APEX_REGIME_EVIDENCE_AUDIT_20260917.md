# APEX XAUUSD Regime / Market-State Evidence Audit

**Generated:** 2026-09-17  
**Scope:** `apex_ai` scalper runtime, regime/state code, backtest artifacts, live telemetry  
**Status:** Documentation only — no code, package, configuration, process, or live-trading changes made.

## 1. Executive verdict

The scalper already has a usable regime/context layer, but it is not one unified classifier:

1. **Active production consultation:** a deterministic ICT-style `RegimeEngine` classifies the M15 trigger frame as `EXPANSION`, `MANIPULATION`, `ROTATION`, or `TRANSITION`. It uses ATR expansion, displacement momentum, structure trend, and H1 manipulation/sweep evidence.
2. **Optional statistical classifier:** `scalper/regime_classifier.py` classifies an H1 completed-bar window as `TRENDING_UP`, `TRENDING_DOWN`, `RANGING`, or `VOLATILE` using lag-1 return autocorrelation, Kaufman efficiency ratio, and a relative volatility ratio. Its consumers are off by default (`RD_GATE_ENABLED=False`, `VP_GATE_ENABLED=False`).
3. **HMM:** an opt-in backend exists but is not wired into the live consultant or entry path. It uses only return and rolling volatility, fits on the same final window it labels, and returns no posterior confidence. It is therefore an experiment backend, not a live evidence engine.

**Verdict: IMPROVE CURRENT / DO NOT USE HMM IN PRODUCTION YET.** There is no evidence that GaussianHMM adds independent information. The one repository measurement cited in code found the deterministic and HMM answers agreed on a 400-bar XAUUSD H1 sample, while HMM cost about 142 ms versus 0.9 ms. Current evidence is also too sparse and incompletely labelled to establish transition or regime-conditional expectancy.

The correct architecture is:

```text
Closed MT5 bars
      |
      +--> ICT/SMC evidence: liquidity -> manipulation -> structure -> displacement
      |                                      |
      |                                      +--> trigger-specific evidence
      |
      +--> context regime: ATR / momentum / structure / manipulation
                                             |
                                             v
                                regime + trigger eligibility
                                             |
                                STB / location / risk / execution
```

ICT/SMC evidence should remain separate from any future statistical regime model. A regime model must never directly open a trade.

## 2. Current regime architecture and runtime trace

### Active path

`scalper_agent.py:651` starts `_scan_symbol()` once per loop; the main loop sleeps for the configured interval at `scalper_agent.py:635`. The default operational interval is 30 seconds.

At `scalper_agent.py:665-666`, the agent fetches M15 trigger data and M5 confirmation data. `_get_ohlcv()` starts at MT5 position 1 by default (`scalper_agent.py:2174-2199`), excluding the forming candle.

After trigger construction, STB and other gates, the live path calls the consultant at `scalper_agent.py:1044-1051`. `SAConsultant._run_consultation()` fetches closed M15 and H1 frames (`sa_consultant.py:242-260`) and caches the result for 30 seconds (`sa_consultant.py:174-175, 209-223`).

`sa_consultant.analyse()` then runs:

* M15 `StructureEngine` and `DisplacementEngine` (`sa_consultant.py:131-146`);
* H1 `LiquidityEngine` and `ManipulationEngine` (`sa_consultant.py:148-155`);
* `RegimeEngine.analyze()` on the M15 frame, passing M15 structure/displacement and H1 manipulation (`sa_consultant.py:157-162`).

The returned ICT regime is binding through Gate 1 at `scalper_agent.py:1080-1119`: `STRESS` is denied, and otherwise the trigger must appear in `TRIGGER_REGIME_WHITELIST` (`decision_params.py:66-82`). Gate 2 uses displacement for triggers that require it (`scalper_agent.py:1121-1129`); Gate 3 can change TP2 using H1 liquidity (`scalper_agent.py:1131-1135`).

### Separate optional path

The H1 deterministic trend/range classifier is used by:

* `RegimeDirectionGate.check()` (`regime_direction_gate.py:92-134`), called only if `RD_GATE_ENABLED` is true (`scalper_agent.py:918-931`);
* `VolumeProfileGate.check()` (`vp_gate.py:170-194`), called only if `VP_GATE_ENABLED` is true (`scalper_agent.py:943-962`).

Both optional gates classify H1 data independently from the active consultant regime. They use the same H1 frame/window defaults (`decision_params.py:282-285`), but they do not share the consultant's `RegimeState`.

### Competing market-state concepts

There are at least five state-like outputs in the scalper:

| State/output | Producer | Active by default? | Meaning |
|---|---|---:|---|
| `EXPANSION`, `MANIPULATION`, `ROTATION`, `TRANSITION` | `intelligence/regime_engine.py:18-113` | Yes, through consultation | ICT/context operating mode |
| `TRENDING_UP`, `TRENDING_DOWN`, `RANGING`, `VOLATILE` | `scalper/regime_classifier.py:94-264` | No, optional gates off | Statistical trend/range/volatility context |
| `BULLISH`, `BEARISH`, `RANGING` | `core/structure_engine.py:112-130` | Yes, as evidence input | M15 structure direction |
| short-term bias/confidence | `scalper/short_term_bias.py:297+` | Yes | M5 structure and recent session-liquidity state |
| session state | `scalper/session_checker.py:95-180` | Yes | Time eligibility, not price regime |

This is not automatically wrong, but it makes the word “regime” ambiguous and makes joint attribution difficult.

## 3. Exact code trace and formulas

### Active `RegimeEngine`

`intelligence/regime_engine.py:57-60` extracts:

* `atr_ratio = mean(TR[-5:]) / mean(TR[-20:])`, where `TR[i] = max(high-low, |high-prev_close|, |low-prev_close|)` (`:115-126`);
* displacement `momentum_score`;
* `manip.detected` and `manip.confidence`;
* structure trend.

The priority rules are first-match:

| Label | Exact condition | Outputs |
|---|---|---|
| `EXPANSION` | `atr_ratio >= 1.5` and `momentum >= 0.65` (`:64-72`) | `CONTINUATION`; confidence `min(1, 0.6 + 0.2*momentum + 0.1*(atr_ratio-1))`; `EXPLOIT` if confidence ≥ 0.70 |
| `MANIPULATION` | no expansion, sweep detected, manipulation confidence ≥ 0.65 (`:74-83`) | `RETURN`; confidence = manipulation confidence; `EXPLOIT` |
| `ROTATION` | no prior match, `atr_ratio < 0.6` and momentum `< 0.4` (`:85-93`) | `MEAN_REVERSION`; confidence 0.60; `OBSERVE` |
| `TRANSITION` | otherwise (`:95-103`) | `WAIT`; confidence 0.40; `ADAPT` |

Daily drawdown overrides behavior at ≥5% (`REDUCE`) and ≥7% (`DISENGAGE`) (`:105-112`), but the consultant passes `daily_dd_pct=0.0` (`sa_consultant.py:157-158`), so these overrides do not affect the scalper consultation path.

### Supporting evidence inputs

* Structure detects centered swings using `swing_lookback=5`, requiring five bars on each side (`core/structure_engine.py:47-60, 79-110`). It calls the last eight swings and labels bullish when at least two HH and one HL exist; bearish when at least two LL and one LH exist (`:112-129`).
* Displacement computes a 14-bar simple mean true range (`core/displacement_engine.py:181-191`), a momentum score from the mean absolute body of the last five bars divided by `1.5*ATR`, capped at 1 (`:173-179`), and marks displacement at score ≥0.6 (`:83-94`). The consultant's Gate 2 raises the acceptance floor to score ≥0.65 and requires an FVG (`sa_consultant.py:142-146`).
* H1 manipulation checks the top three BSL/SSL pools. XAUUSD's sweep piercing threshold is 0.000637 of level (`core/manipulation_engine.py:69-88, 118-163`), with reversal close confirmation and confidence beginning at 0.6 plus pool/depth terms.

### Statistical `RegimeClassifier`

On the final `lookback=100` completed bars (`regime_classifier.py:176-207`):

* returns are percent close-to-close returns (`:211-214`);
* lag-1 autocorrelation is the centered covariance numerator divided by centered variance denominator (`:132-149`);
* ER is `abs(close[-1]-close[0]) / sum(abs(diff(close)))` (`:152-164`);
* volatility is rolling 20-return standard deviation; `vol_ratio = latest_vol / mean(rolling_vol)` (`:216-225`);
* `VOLATILE` wins first when `vol_ratio > 1.5`;
* otherwise trending is `abs(rho)>0.2 OR ER>0.35` under default `combine="ANY"` (`:231-257`), with direction from the mean of the latest versus earliest 10 closes;
* otherwise the result is `RANGING` (`:259-264`).

### HMM backend

`scalper/hmm_backend.py:94-184` is not imported by the live agent. It is lazy and optional (`:60-66, 107-126`). Defaults are 400 bars, three states, 20-bar rolling volatility, seed `20260825`, three restarts, and 100 iterations (`:49-57`). Features are only `[return, rolling volatility]` (`:76-91`).

The implementation fits and scores on the same observation matrix it then predicts (`:128-147`), so it is not a valid strictly out-of-sample classifier for the final observation. The module comments say it avoids look-ahead, but the implementation does not: the final bar contributes to both parameter fitting and classification. It also has no state posterior/probability output; `RegimeRead.efficiency_ratio` is hard-coded to 0 (`:162-168`). State labels are heuristically mapped by fitted volatility (`:149-180`), which does not reliably distinguish trend from balance when their volatility overlaps.

## 4. Feature and threshold table

| Layer | Frame | Features | Threshold/lookback | Decision role |
|---|---|---|---|---|
| Active regime | M15 | ATR ratio, displacement momentum, M15 structure trend, H1 manipulation result | ATR 5/20 ≥1.5; momentum ≥0.65; manipulation confidence ≥0.65; rotation ATR <0.6 and momentum <0.4 | Per-trigger whitelist |
| Structure | M15 consultant | centered swing HH/HL/LH/LL | swing lookback 5; recent 8 swings | Evidence and direction context |
| Displacement | M15 consultant | body/ATR momentum, FVG/OB | ATR14 SMA; last 5 bodies; displacement 0.6; Gate 2 0.65 + FVG | BOS_RETEST validation; active regime input |
| Manipulation | H1 consultant | BSL/SSL sweep/reclaim | top 3 pools; XAUUSD 0.000637; reversal bars 3 | Active `MANIPULATION` input |
| Optional regime | H1 | return rho, ER, relative rolling volatility | 100 bars; smoothing 10; rho 0.2; ER 0.35; vol ratio 1.5 | Optional VP/RD gates only |
| Optional HMM | H1 if separately called | return, rolling volatility | 400 bars; 3 states; vol window 20; 3 restarts | Not wired to trading |
| STB state | M5 plus H1/H4 context | range position, recent session sweep, intraday structure | range extremes 25%/75%; recent sweep window in `short_term_bias.py`; H1 is tie-breaker | Direction/admission gate |

## 5. Regime → trading behaviour matrix

| Active regime | Whitelisted triggers (`decision_params.py:68-78`) | Behaviour |
|---|---|---|
| `MANIPULATION` | `SESSION_SWEEP`, `HTF_CRT_SWEEP`, `SWEEP_REJECTION`, `JUDAS`, `FVG_FILL`, `VALUE_AREA_FADE`, `VP_LIQUIDITY_REACTION` as applicable | Reversal/mitigation setups may pass; `BOS_RETEST` is denied |
| `ROTATION` | sweep/Judas/session/CRT/value-area fade/VPLR families; not BOS; FVG is not generally allowed by the whitelist | Mean-reversion/location-dependent admission |
| `EXPANSION` | `HTF_CRT_SWEEP`, `BOS_RETEST`, `FVG_FILL`, VPLR if its separate set permits | Continuation/selected reaction setups |
| `TRANSITION` | No whitelist entry | Gate 1 rejects every trigger |
| `STRESS` | Explicitly rejected at runtime, although not emitted by current `RegimeEngine` | Reject every trigger |
| `UNKNOWN` | Consultant failure is rejected before Gate 1; optional statistical gates abstain/permissively allow when unreadable | Safe skip for consultant failure; abstention for optional gates |

The active regime currently affects trigger permission and model family. It does not directly change direction, SL, risk size, or execution. TP2 can be changed by the separate H1 liquidity override. Risk/execution gates occur downstream.

## 6. Causality, candle use, and leakage

The live data boundary is causal: `_get_ohlcv()` uses MT5 position 1 (`scalper_agent.py:2179-2199`), and the consultant does the same (`sa_consultant.py:279-289`). The trigger and regime path therefore excludes the current forming candle. The backtest's `_closed_tf`/closed-frame path is intended to mirror this (`backtest_scalper.py:460-477` and calls at `:1163-1184`).

The active deterministic regime arithmetic itself is causal over the supplied final row. Centered swing detection is safe at decision time because the final supplied bar is already closed and the swing algorithm only confirms points with bars on both sides inside the historical frame (`structure_engine.py:79-90`).

Important caveats:

* `RegimeState.timestamp` uses wall-clock time, but timestamp does not determine the label (`regime_engine.py:49`).
* The consultant's 30-second cache means an M15/H1 state can be reused for up to 30 seconds; that is a freshness choice, not look-ahead (`sa_consultant.py:209-223`).
* The HMM implementation has fitting leakage for a final-bar classification because it fits on the same observations it predicts (`hmm_backend.py:128-147`).
* Historical incidents before the current telemetry era carry `regime="UNKNOWN"`; they cannot be used to estimate performance by regime.

## 7. Recent evidence and what it can support

### Available counts

| Artifact | Observation |
|---|---|
| `apex_ai/logs/sa_execution_telemetry.jsonl` | 9 `CANDIDATE_OBSERVED` and 9 `ENTRY_EXECUTION` records; all 9 labelled `MANIPULATION` with confidence 0.96 in the sampled recent telemetry. This is a tiny, selection-biased sample. |
| `apex_ai/logs/sa_incidents.jsonl` | 332 incidents: 316 `UNKNOWN`, 16 `MANIPULATION`. The 16 labelled trades were 10 wins and 6 losses, net +$211.87; this is too small and era-mixed to claim regime edge. |
| `apex_ai/logs/bt_baseline.json` | 281 trades, 129 wins, net +$2,757.57; `CONSUL_GATE1_REGIME` rejected 74 candidates. Trade records do not include regime labels. |
| `apex_ai/logs/backtest_20260515_20260821.json` | 198 trades, 65 wins, net -$76.97; `CONSUL_GATE1_REGIME` rejected 464 candidates. Trade records do not include regime labels. |
| `apex_ai/logs/bt_forensics.json` | 280 trades, 129 wins, net +$2,742.35; 72 regime-gate rejections. No regime field in trade records. |

### Not measurable from current artifacts

Regime distribution over all scanned bars, dwell/persistence, transition frequency, conditional expectancy by regime, and admission-quality lift are not recoverable from the stored backtest trade schema. A rejection count alone does not reveal which regimes were present or whether rejected candidates would have won.

The recent telemetry's all-`MANIPULATION` sample is also expected from the active XAUUSD trigger mix (predominantly sweep rejection) and is not evidence that the classifier is persistent or predictive.

## 8. Problems and duplication found

### 🟥 Critical

**HMM final-observation leakage.**

* **Current behavior:** `hmm_backend.py:128-147` fits on `obs` and predicts the last element of that same `obs`.
* **Problem:** the classified bar influenced the fitted parameters, so a backtest can report a state that would not have been available at the decision boundary.
* **Enhancement:** for any HMM experiment, fit only on observations strictly before the decision bar, freeze parameters for the decision, and evaluate the next bar or next block walk-forward.
* **Impact:** Critical for research validity.
* **Complexity:** Medium.

### 🟧 High

**Two regime vocabularies with separate classifiers.**

* **Current behavior:** active consultation uses `EXPANSION/MANIPULATION/ROTATION/TRANSITION`; optional gates use `TRENDING/RANGING/VOLATILE` (`sa_consultant.py:157-162`, `regime_classifier.py:94-103`).
* **Problem:** logs and analysis can attribute a trade to one “regime” while a different gate used another; the labels answer different questions but share the same overloaded name.
* **Enhancement:** name them explicitly as `ICT_CONTEXT` and `STATISTICAL_CONTEXT`, log both when evaluated, and centralize the evidence snapshot.
* **Impact:** High for attribution and gate correctness.
* **Complexity:** Medium.

**Active regime is heavily redundant with trigger evidence.**

* **Current behavior:** `EXPANSION` requires M15 displacement momentum and uses M15 structure; `MANIPULATION` is directly a detected H1 sweep (`regime_engine.py:57-83`). The same displacement/sweep facts also drive trigger detection and consultation gates.
* **Problem:** the regime gate may be restating the trigger's own evidence rather than adding an independent market-context variable. This can over-filter or double-count evidence without adding information.
* **Enhancement:** measure incremental information: compare admission and forward outcome with each regime component ablated, using chronological folds and fixed trigger candidates.
* **Impact:** High research priority.
* **Complexity:** Medium.

### 🟨 Medium

**Regime confidence is not a calibrated probability.**

* **Current behavior:** confidence is hand-built from thresholds (`regime_engine.py:69-70, 79-80, 90, 100`).
* **Problem:** `0.96` means “formula output,” not a validated 96% probability; this can mislead monitoring and model comparisons.
* **Enhancement:** rename it to score unless calibrated on held-out data; log component margins and conflict flags.
* **Impact:** Medium.
* **Complexity:** Easy.

**Rotation/transition semantics are asymmetric.**

* **Current behavior:** `ROTATION` requires both low ATR ratio and low momentum; all other non-expansion/non-manipulation states become `TRANSITION` (`regime_engine.py:85-103`).
* **Problem:** a quiet trend, high-volatility balance, and ordinary mixed state are collapsed into `TRANSITION`; the engine has no explicit uncertainty/conflict measurement.
* **Enhancement:** expose component votes/margins and retain an explicit `CONFLICT`/`UNCERTAIN` diagnostic, without automatically loosening gates.
* **Impact:** Medium.
* **Complexity:** Medium.

**Telemetry is insufficient for the requested evidence questions.**

* **Current behavior:** backtest trades omit regime and gate component snapshots; current incidents are mostly `UNKNOWN`.
* **Problem:** regime performance and transition persistence cannot be audited or reproduced.
* **Enhancement:** append immutable decision-time fields: context regime, statistical regime if evaluated, component values, allowed set, veto reason, snapshot bar times, and regime duration.
* **Impact:** Medium.
* **Complexity:** Medium.

### 🟩 Low

**Dead/dormant HMM path can be mistaken for production support.**

* **Current behavior:** `VP_REGIME_BACKEND` is `DETERMINISTIC` (`decision_params.py:265-271`) and no code imports `hmm_backend` outside its own module.
* **Problem:** documentation can imply HMM is being compared live when it is not.
* **Enhancement:** label it `RESEARCH_ONLY` and require an explicit experiment command/path.
* **Impact:** Low.
* **Complexity:** Trivial.

### 🟦 Architectural

**No single immutable evidence snapshot crosses live and backtest boundaries.**

* **Current behavior:** live and backtest call shared pure gates in places, but the overall pipeline still assembles multiple frames and state producers separately (`scalper_agent.py:651-1135`, `backtest_scalper.py:518-1238`).
* **Problem:** parity is fragile; a new regime feature can silently be added to one path only, invalidating research.
* **Enhancement:** define a versioned, closed-bar `MarketEvidenceSnapshot` and make both live and replay consume the same classifier/gate orchestration.
* **Impact:** Architectural.
* **Complexity:** Hard.

## 9. Existing method vs alternatives

| Method | Independent information likely? | Advantages | Risks/costs | Verdict |
|---|---|---|---|---|
| Current rule-based ICT context | Low-to-medium; partly repeats trigger evidence | Causal, deterministic, fast, interpretable, already integrated | Threshold brittleness; no persistence model; confidence uncalibrated | Keep, instrument, improve attribution |
| H1 rho/ER/relative-vol classifier | Potentially medium for trend-vs-balance, but not currently active | Simple, cheap, causal, directly tests balance/trend | Separate vocabulary; optional gates off; thresholds not fully validated | Test only as a named context feature |
| GaussianHMM | Unproven; current 2-feature design mostly repackages return/volatility | Can model persistence and probabilistic state occupancy | Leakage risk, local optima, label switching, non-stationarity, fit parity, latency; no current posterior | Do not deploy; only controlled experiment |
| Rolling-volatility + trend-strength state machine | Similar information with less fragility | Transparent, easy to walk forward, easy to log | Still threshold-based; may duplicate ATR/ER | Preferred alternative if a new layer is justified |
| Clustering/change-point detection | Unknown and likely unstable on this sample | Could find structural breaks without fixed labels | Unsupervised labels are not trading semantics; difficult attribution | Lower priority than a state machine |

HMM is a context classifier, not a BUY/SELL predictor. It should not replace liquidity raids, MSS/CHoCH, displacement, FVG, order-block, or volume-profile evidence.

## 10. Recommended architecture

Use two explicitly separated products:

1. **ICT evidence engine:** liquidity pools, sweeps, structure, displacement, FVG/OB, session and profile location. It supplies trigger evidence and direction.
2. **Market-context engine:** volatility/trend/balance/transition state, with component values and uncertainty. It supplies eligibility and risk-context metadata only.

The context engine should return a versioned record such as:

```text
context_label
context_score (not probability unless calibrated)
component_values
bar_close_time / frame
bars_used
uncertain_or_conflicting
duration_bars
backend_version
```

The decision order should remain `context eligibility -> ICT trigger evidence -> entry validation -> risk -> execution`. No HMM output should directly select direction or place orders.

## 11. HMM suitability test (only if explicitly approved)

Do not install or activate it as part of this audit. If tested later:

* Use completed **H1** bars and evaluate only the next H1/M15 decision block.
* Compare 3 and 4 states, but pre-register the choice; 3 is the minimum baseline, 4 only if “quiet balance / trend / expansion / stress” is separable.
* Start with standardized log return, realized volatility, and ATR/price; do not add ADX/EMA spread until ablation shows independent value. The current two-feature backend is too weak to support the requested state semantics.
* Train on a rolling historical window strictly before each decision block; use at least several hundred observations, with a fixed seed plus 5–10 restarts and deterministic fitted-state mapping from emission diagnostics.
* Report posterior state probability, entropy/uncertainty, state dwell distribution, transition matrix, and missing/invalid fit rate.
* Refit on a predeclared schedule (for example weekly or every 100 completed H1 bars), never after seeing the evaluation block.
* Use chronological 60/20/20 or rolling walk-forward folds, with all trigger/gate logic identical between live replay and the control arm.
* Define success as incremental admission quality or calibrated context separation versus the deterministic control—not in-sample profitability. Require stable sign across all folds and a meaningful improvement after spread/costs.
* The decisive ablation is: current active regime; HMM alone; current + HMM; current with HMM shuffled/lagged; and current with each feature group removed. If current + HMM does not improve out-of-sample context metrics and trade admission quality, reject it.

## 12. Required next evidence before changing logic

The next safe research increment is telemetry only: record both regime vocabularies whenever evaluated, the exact component values, the selected whitelist, and the rejection reason. Then run a frozen historical replay that reports regime counts, dwell lengths, transitions, candidate admission, and forward outcomes by regime. Do not promote a gate or HMM from the existing 9 labelled recent candidates or 16 labelled incidents.

## 13. Priority matrix

| Priority | Bucket | Finding |
|---|---|---|
| P0 | Critical | Fix HMM experiment leakage before treating any HMM result as evidence |
| P1 | High | Separate ICT context from statistical context; measure incremental information and redundancy |
| P2 | Medium | Calibrate/rename confidence, expose conflict/uncertainty, and add regime-complete telemetry |
| P3 | Architectural/Low | Build a single versioned evidence snapshot and keep HMM research-only |

## 14. Suggested execution order and expected impact

**Phase 1:** telemetry-only schema and replay reports; no gate changes.  
**Phase 2:** quantify redundancy by ablation and establish regime-conditioned sample sizes.  
**Phase 3:** improve the deterministic context state machine only where folds support it.  
**Phase 4:** run the pre-registered HMM experiment against the deterministic control.  
**Phase 5:** deploy only a stable, out-of-sample improvement with an explicit rollback switch.

Expected combined impact cannot honestly be expressed as a profit percentage from current evidence. The measurable expected benefit of P0/P1 is research validity and prevention of false HMM conclusions: leakage-free comparisons, identifiable gate attribution, and a direct answer to whether the regime layer adds independent information. Any numerical P&L estimate would violate the available sample and the repository's own no-single-window promotion rule.

