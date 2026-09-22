# 🚀 APEX AI Trading System — Enhancement Recommendations

**Generated:** 2026-08-23
**Status:** Documentation only — no code changes pending approval
**Scope:** Read-only profitability audit of Pool A, SA-V2 Pool B, the backtester, Trade Guardian, risk controls, logs, and research records

This document lists prioritized improvements found during a full code audit of APEX AI. The central conclusion is that the historical live losses are real, but the latest positive backtest is not yet evidence that the current live system is profitable because the simulated and live decision/exit paths still differ materially.

---

## 🟥 CRITICAL (Safety/Correctness)

### C1. Make live and backtest consume the same market information

**Current behavior:** Live SA fetches MT5 bars from position zero and evaluates the last M15 row (`apex_ai/scalper_agent.py:348-349`, `apex_ai/scalper_agent.py:385-405`, `apex_ai/scalper_agent.py:877-884`). MT5 defines bar zero as the current, still-forming bar. The backtest instead evaluates only completed M15/M5 bars (`apex_ai/backtest_scalper.py:406-423`). It also includes H1/H4 rows whose opening time equals the decision time (`apex_ai/backtest_scalper.py:435-459`), exposing the final high, low, and close of an unfinished higher-timeframe bar. Finally, live consultation classifies regime on M5 (`apex_ai/scalper/sa_consultant.py:172-210`) while the backtest substitute classifies it on M15 (`apex_ai/backtest_scalper.py:161-176`).

**Problem:** The bot trades signals that can appear and disappear intrabar, while the simulator sees settled M15 data plus future information from the current H1/H4 candle. Candidate selection, regime classification, entry price, and win rate therefore cannot be expected to match. The official [MT5 bar-copy documentation](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrompos_py) confirms that index zero is the current bar.

**Enhancement:** Define one timestamped snapshot contract and use it everywhere. Prefer decisions once per newly closed M15 bar, exclude the current H1/H4 bar, and call the same consultant/gate function in live and replay. Add a parity test that feeds a saved snapshot to both paths and requires identical trigger, gate reasons, levels, and order intent.

**Impact:** CRITICAL
**Complexity:** Hard

---

### C2. Include the Trade Guardian in every performance simulation

**Current behavior:** The SA backtest models fixed SL, TP1, six-hour timeout, and EOD close (`apex_ai/backtest_scalper.py:216-261`, `apex_ai/backtest_scalper.py:508-540`). The live Guardian can move stops, close reversals, partially close/extend targets, and kill a trade after 60 minutes when peak profit is at most 0.3R (`apex_ai/trade_guardian_agent.py:55-68`, `apex_ai/trade_guardian_agent.py:638-672`). The latest positive run contains 46 timeout exits, 17.1% of all trades, and the Guardian can change many TP/SL paths before those nominal exits occur (`apex_ai/logs/bt_baseline.json`).

**Problem:** Entry signals are being credited with an exit distribution the live system does not execute. Even if the Guardian improves some trades, that improvement is unknown; trailing and early closure can also cut winners or convert targets into breakevens. The reported +$3,211, PF 1.41, and 46.84% win rate are not live-reproducible metrics until this is reconciled.

**Enhancement:** Express Guardian management as a deterministic, persisted state machine shared by live and backtest. Replay every stop move, partial close, no-progress close, early close, and TP extension using bid/ask data. Report expectancy by both entry model and Guardian exit reason.

**Impact:** CRITICAL
**Complexity:** Hard

---

### C3. Stop executing Pool A retracement prices as immediate market orders

**Current behavior:** The Pool A RETURN model selects an FVG/order-block midpoint below or above current price as a future retracement entry (`apex_ai/core/execution_models.py:91-142`). EA forwards that hypothetical entry unchanged (`apex_ai/agents/ea.py:78-85`), and the connector submits it with `TRADE_ACTION_DEAL`, an immediate market deal (`apex_ai/market/mt5_connector.py:225-253`). The historical log contains 22 `retcode=10016 Invalid stops` rejections and repeated one-minute retries (`apex_ai/logs/apex_ai.log:34283`).

**Problem:** The setup geometry is calculated around one price but the broker either fills at the current market or rejects SL/TP that are invalid relative to the current bid/ask. Actual risk and reward can therefore differ sharply from the council-approved RR. MT5 distinguishes an immediate deal from a pending order in its [trade-action documentation](https://www.mql5.com/en/docs/constants/tradingconstants/enum_trade_request_actions).

**Enhancement:** Give RETURN setups a lifecycle: pending, filled, expired, or invalidated. Place a correctly typed pending order at the zone, or permit a market order only when the executable quote is inside the entry zone. Immediately before sending, recompute SL, TP, RR, and size from the executable quote; validate stop/freeze distance and call [`order_check`](https://www.mql5.com/en/docs/python_metatrader5/mt5ordercheck_py).

**Impact:** CRITICAL
**Complexity:** Hard

---

### C4. Base risk controls on actual loss-at-stop and equity

**Current behavior:** Pool A open-position records contain no risk percentage (`apex_ai/market/mt5_connector.py:189-207`), so the portfolio engine assigns every position a default 1% risk (`apex_ai/intelligence/portfolio_engine.py:74-75`) even when the risk engine can allocate up to 5%. Lot size is forced to at least 0.01 and rounded to the nearest step (`apex_ai/intelligence/risk_engine.py:117-134`; SA equivalent at `apex_ai/scalper_agent.py:781-802`). Governance measures balance drawdown, not equity drawdown, and resets its peak to current balance after restart (`apex_ai/maingpt.py:191-193`, `apex_ai/maingpt.py:224-232`).

**Problem:** Exposure can be under-reported, minimum/rounded lots can exceed the intended dollar loss, floating losses do not reduce risk, and restarting erases the drawdown reference. This can turn a weak edge into a much larger loss. Pool A also assesses the portfolio once before the symbol loop and does not refresh it after a fill (`apex_ai/maingpt.py:238-254`, `apex_ai/maingpt.py:271-274`).

**Enhancement:** Calculate one-lot loss from executable entry to SL with `order_calc_profit`, floor volume to the broker step, and reject—not force—anything below minimum volume. Persist the high-water mark, govern on equity, derive every open position's current risk from its actual fill/SL/volume, and reserve proposed risk before evaluating the next symbol.

**Impact:** CRITICAL
**Complexity:** Medium

---

### C5. Make Guardian ownership and restart state explicit

**Current behavior:** TGA discovers every account position without filtering magic numbers (`apex_ai/trade_guardian_agent.py:480-520`); the intended `TGA_MANAGES_SCALPER` ownership switch is declared but unused (`apex_ai/core/constants.py:43-49`). The old TGA action log includes 17 actions on `UNKNOWN` positions. Its registry is in memory; after restart, current broker SL/TP are treated as the original contract (`apex_ai/trade_guardian_agent.py:508-518`), while stage, peak R, partial-close status, and original risk are lost.

**Problem:** The Guardian can modify or close manual/unowned trades. A restart after breakeven or trailing can reduce calculated original risk to zero or otherwise reset management decisions. That changes realized expectancy and makes trade attribution unreliable.

**Enhancement:** Use an explicit magic-number ownership allowlist, persist the original entry contract and Guardian state per ticket, and reconcile that state with MT5 on restart. For partial closes, floor to `volume_step` and require both the closed portion and runner to meet `volume_min`; otherwise use a documented full-close policy.

**Impact:** CRITICAL
**Complexity:** Medium

---

## 🟧 HIGH IMPACT (Performance/Profitability)

### H1. Treat SWEEP_REJECTION as unproven rather than as the default edge

**Current behavior:** The May live journal contains 287 records: 173 losses, 67 timeouts, 37 TP1 wins, four EOD closes, and six still marked open. Of those records, 279 are `SWEEP_REJECTION`. The fresh $500/2% replay was also dominated by this trigger—196 of 198 trades—and produced -$91.63 for that model, PF 0.92 (`docs/RESEARCH_NOTES.md:242-317`). The trigger engine evaluates it first and returns immediately (`apex_ai/scalper/trigger_engine.py:206-228`), labels every detected sweep HIGH confidence (`apex_ai/scalper/trigger_engine.py:317-356`), and the short-term bias filter even permits it against the short-term direction (`apex_ai/scalper/short_term_bias.py:422-454`).

**Problem:** The apparent multi-strategy scalper is effectively one sweep-fade strategy. Historical live evidence is poor, the clean 2% replay is negative, and the gates do not meaningfully discriminate confidence within the dominant pattern. The positive 3% run is encouraging but not independent and is affected by C1/C2.

**Enhancement:** Suspend claims of a proven sweep edge. Evaluate every trigger in isolation on the corrected shared path; require an expiry/consumption rule after a rejected or unaffordable setup; and calibrate confidence from out-of-sample outcomes rather than assigning HIGH to the pattern name. Do not re-tune the same May–August sample.

**Impact:** HIGH
**Complexity:** Medium

---

### H2. Separate trade admission from risk percentage

**Current behavior:** On the same XAUUSD window, $1,000 at 1% produced 210 trades, 32.38% wins, and -$91.15, while 3% produced 269 trades, 46.84% wins, and +$3,211.19 (`docs/RESEARCH_NOTES.md:379-441`). The difference is not simple risk scaling: minimum-lot rejection occurs after all gates and consumes neither the setup nor cooldown, so the smaller budget later enters a different, degraded setup. The project correctly calls this the “lot-floor free look.”

**Problem:** Risk percentage changes which signals exist. Drawdown lowers the affordable stop distance and can push the system into the weaker re-entry population precisely while it is losing. A profitable 3% result therefore cannot be safely converted to 1% by changing one number.

**Enhancement:** Decide signal identity and expiry before sizing. An unaffordable approved setup should be recorded and consumed for that symbol/setup window. For research, use enough notional capital, a cent/micro contract, or a normalized synthetic size so every candidate is admitted consistently; apply the desired risk only after the candidate set is frozen.

**Impact:** HIGH
**Complexity:** Medium

---

### H3. Rebuild the council as independent evidence plus separate vetoes

**Current behavior:** Consensus counts a high-confidence `NEUTRAL` vote as aligned with BUY or SELL (`apex_ai/fund/hedge_fund_os.py:101-119`). EA and CRG emit NEUTRAL approvals (`apex_ai/agents/ea.py:61-66`, `apex_ai/agents/crg.py:98-102`), and SEE emits NEUTRAL at exactly 0.55 when it has no history (`apex_ai/agents/see.py:28-35`). Thus three non-directional approvals can count toward “5 of 7.” The Monte Carlo gate draws outcomes from a win probability created from the same heuristic bias and setup confidence, with the same seed on every call (`apex_ai/fund/simulation_engine.py:44-82`).

**Problem:** Feasibility and risk vetoes are being misrepresented as directional evidence, while simulation merely resamples the model's own assumption. Multiple agents transform the same OHLC features, so their votes are correlated rather than independent. The gate can create confidence without adding predictive information.

**Enhancement:** Count only explicitly bullish/bearish predictive agents in directional consensus. Keep EA, CRG, and data-quality checks as independent vetoes. Calibrate each signal's probability against held-out outcomes with reliability curves/Brier score; replace the current Monte Carlo approval with empirical conditional returns or use it only for risk-of-ruin after an edge is established.

**Impact:** HIGH
**Complexity:** Medium

---

### H4. Do not promote the 3% result from the window used to select it

**Current behavior:** `bt_baseline.json` reports 269 trades, +0.2179R expectancy, PF 1.41, and positive 60/20/20 fold signs. But baseline, 1%, no-daily-limit, Asia-only, no-London, control, and BOS variants were all evaluated on the same May–August data on 2026-08-23. The risk level was selected after viewing full-window results, so the labeled test fold is no longer untouched.

**Problem:** Repeated selection inflates apparent performance. A t-statistic of 2.69 before accounting for all attempted variants, serial dependence, C1 look-ahead, and C2 exit mismatch is a research lead, not production proof. The Deflated Sharpe Ratio was specifically designed to correct for multiple testing and non-normal returns ([Bailey & López de Prado](https://doi.org/10.2139/ssrn.2460551)); backtest-overfitting probability is addressed in their related [CSCV/PBO research](https://escholarship.org/uc/item/4hn4t174).

**Enhancement:** Freeze code and parameters, register every trial, and start a genuinely untouched forward paper period. Use rolling/purged walk-forward validation, report all trials, and calculate DSR/PBO where sample size permits. Require the same sign across periods, not just trade-count slices of one tuned window.

**Impact:** HIGH
**Complexity:** Medium

---

### H5. Reduce production risk only after fixing the admission mechanism

**Current behavior:** The positive 3% simulation reached 26.88% maximum drawdown; its validation and test slices show 50.93% and 35.93% drawdown when each is evaluated from the same starting capital. Pool A configuration requests 16%/8% clarity risk and allows 40% total exposure, while the runtime single-trade clamp is 5% (`apex_ai/config.json:14-31`, `apex_ai/intelligence/risk_engine.py:113-125`).

**Problem:** These risk levels are too high for an unverified edge and can mask whether the strategy is structurally profitable. Lowering the current SA risk directly is not sufficient because H2 changes the trade population.

**Enhancement:** After H2, target 0.25%-0.5% per trade during forward paper validation, no more than 1%-2% aggregate open risk, and a hard equity drawdown stop. If broker minimum size prevents this, use more actual funded capital or a genuinely smaller contract/cent-account instrument rather than relabeling the same broker balance or accepting 3%-5% risk. Set promotion criteria before the run, such as net PF at least 1.20, positive expectancy in every untouched period, and max drawdown at most 10%-15%.

**Impact:** HIGH
**Complexity:** Easy

---

### H6. Do not allocate capital to Pool A without a historical harness

**Current behavior:** The repository explicitly states that `maingpt.py` has no historical backtester (`AGENTS.md:464`). Its live logs end on 2026-05-15, before the current August changes, and contain only 14 execution messages against 22 invalid-stop rejections. The adaptive journal currently contains zero trades. Pool A therefore has no current, statistically usable evidence of expectancy.

**Problem:** The five gates, seven-agent council, and 500-run simulation are not substitutes for outcome data. Capital is being allocated based on internal confidence scores that have not been calibrated to realized returns.

**Enhancement:** Keep Pool A in dry-run/paper mode until its exact live path—including pending-entry lifecycle, order fills, Guardian exits, portfolio state, and costs—can be replayed. Require an untouched forward sample after the historical harness passes parity tests.

**Impact:** HIGH
**Complexity:** Hard

---

## 🟨 MEDIUM IMPACT (Robustness/Reliability)

### M1. Use executable bid/ask fills and variable costs in SA research

**Current behavior:** The backtest fills at the signal close and charges a static 2.5-pip spread with zero commission (`apex_ai/backtest_scalper.py:501-539`, `apex_ai/logs/bt_baseline.json`). Live sends at the current ask/bid but sizes and journals from the signal price (`apex_ai/scalper_agent.py:583-602`, `apex_ai/scalper_agent.py:607-643`).

**Problem:** Gap, slippage, spread spikes, stop-level rejection, and actual fill-to-SL risk are absent. Costs did not cause the negative 2% run—gross was still negative (`docs/RESEARCH_NOTES.md:267-279`)—but simplified fills can overstate a marginal positive edge and corrupt per-trade R.

**Enhancement:** Replay timestamped bid/ask or tick data, commission and swap by account type, adverse slippage sensitivity, and stop gaps. Store the broker's actual fill and recompute realized initial risk from it before journaling or evaluating R.

**Impact:** MEDIUM
**Complexity:** Hard

---

### M2. Complete the M15 migration of short-term-bias semantics

**Current behavior:** The live caller passes the M15 trigger frame into parameters still named and calibrated for M5 (`apex_ai/scalper_agent.py:392-409`). The range detector's 20-bar “about 1.5 hours” lookback becomes five hours, and the 12-bar “about 1 hour” structure lookback becomes three hours (`apex_ai/scalper/short_term_bias.py:128-132`, `apex_ai/scalper/short_term_bias.py:186-188`). The backtest mirrors this behavior, so it is consistent but semantically stale.

**Problem:** A filter intended to describe short-term structure now measures a much slower state. Its thresholds and thin-liquidity confidence rules have not been revalidated for M15.

**Enhancement:** Specify every lookback in elapsed time, derive bars from the supplied timeframe, and revalidate thresholds only on training data. Record the chosen timeframe and lookback in each decision log.

**Impact:** MEDIUM
**Complexity:** Easy

---

### M3. Repair the cross-asset and regime-alignment claims

**Current behavior:** `cross_assets` is configured (`apex_ai/config.json:2-4`) but is not read by Python. CAIA receives only `self.symbols`, and every named symbol is analyzed using the current primary symbol's same H1 dataframe (`apex_ai/maingpt.py:382-386`). With the current one-symbol XAUUSD list, there is no cross-asset evidence. Gate 5 allows either model in ROTATION or TRANSITION and only logs—rather than enforces—`preferred_model` (`apex_ai/fund/hedge_fund_os.py:167-175`).

**Problem:** Two advertised intelligence layers do not enforce what their names imply. When several symbols are enabled, mislabeled same-market data can manufacture alignment; in TRANSITION, the regime gate is effectively permissive.

**Enhancement:** Fetch independent completed data for configured read-only cross-assets and abstain when unavailable. Define an explicit regime-to-model allowlist and compare the candidate to `preferred_model`; treat TRANSITION as a block unless separately validated.

**Impact:** MEDIUM
**Complexity:** Medium

---

### M4. Fix adaptive-memory units before enabling learning

**Current behavior:** Both Pool A and SA journal `risk_reward_actual` as dollar PnL divided by raw price distance (`apex_ai/maingpt.py:525-542`, `apex_ai/scalper_agent.py:845-864`). Adaptive memory averages that field as RR (`apex_ai/intelligence/adaptive_memory.py:123-158`). The database currently has zero trade rows, so SEE and meta-fund behavior has not been exercised on the current version.

**Problem:** Dollars divided by price units is not an R-multiple and varies arbitrarily by instrument and lot size. Once the journal fills, reported average RR will be meaningless and may contaminate future allocation decisions.

**Enhancement:** Persist actual entry, exit, volume, fees, and initial dollar loss-at-stop; define realized R as net PnL divided by that dollar risk. Require a minimum sample and a frozen evaluation policy before adaptive weights can affect capital.

**Impact:** MEDIUM
**Complexity:** Easy

---

### M5. Add end-to-end parity and restart tests

**Current behavior:** Existing fidelity tests cover simple SL/TP/timeout/EOD scheduling and injected clocks (`apex_ai/tests/test_backtest_fidelity.py`), but do not assert live-versus-backtest snapshots, H1/H4 completion, Guardian actions, broker fill reconciliation, ownership, or restart persistence.

**Problem:** Local tests can stay green while the profitable simulation and live system remain different strategies.

**Enhancement:** Build golden-event tests from recorded MT5 snapshots and order results. Assert identical decision reason codes, levels, sizes, and Guardian transitions before/after restart; include gap fills, simultaneous SL/TP bars, minimum volume, manual positions, and two simultaneous symbols.

**Impact:** MEDIUM
**Complexity:** Medium

---

### M6. Make session definitions daylight-saving aware

**Current behavior:** London and New York windows are fixed UTC hours (`apex_ai/scalper/session_checker.py:31-36`). Their local market opens move relative to UTC when daylight-saving rules change.

**Problem:** The same named session can represent different market microstructure across the year, causing seasonally shifted entries and invalid session attribution.

**Enhancement:** Define sessions in `Europe/London` and `America/New_York`, convert them to UTC per trading date, and preserve a separate broker-rollover/EOD boundary.

**Impact:** MEDIUM
**Complexity:** Easy

---

## 🟩 LOW IMPACT (Nice-to-Have)

### L1. Version every decision and archive superseded performance claims

**Current behavior:** Old reports still display 62.50% win rate and +82.51% despite being explicitly superseded, while the current live logs predate the M15/M5, 2R, cooldown, and journal changes. Comments still refer to a 120-minute timeout where current SA uses six hours (`apex_ai/scalper_agent.py:651-674`).

**Problem:** It is easy to combine results from different strategies and mistake stale metrics for current evidence.

**Enhancement:** Stamp each signal/trade/report with code version, trigger/confirmation timeframe, enabled gates, Guardian version, account type, risk, and dataset hash. Move superseded results into an archive index with a visible invalidation reason.

**Impact:** LOW
**Complexity:** Easy

---

### L2. Log the complete order contract and rejection funnel

**Current behavior:** Logs record setup levels and basic broker comments but do not consistently preserve requested quote, actual fill, one-lot loss, stop-level/freeze-level, slippage, effective RR, and every gate input. Historical logs include 323 market-closed and 37 no-money SA failures without a consolidated rate denominator.

**Problem:** Execution leakage and operational errors cannot be separated cleanly from signal failure.

**Enhancement:** Emit one structured decision/order record with stable reason codes from candidate through close. Report conversion rates for trigger→gate→order-check→fill→exit and attribute PnL by version, trigger, session, regime, direction, and exit manager.

**Impact:** LOW
**Complexity:** Easy

---

## 🟦 ARCHITECTURAL (Future-Looking)

### A1. Create one deterministic trading kernel for live and replay

**Current behavior:** Live SA, its consultant, the historical simulator, TGA, and Pool A each duplicate parts of the decision or exit path.

**Problem:** Every duplicated gate is a future opportunity for silent divergence. The present C1/C2 defects are consequences of this structure.

**Enhancement:** Separate pure decisions from adapters. Feed immutable market/account events into one engine that returns intents and state transitions; use MT5 and the backtester only as data/execution adapters. Replay captured live events through the same kernel in continuous regression tests.

**Impact:** HIGH
**Complexity:** Hard

---

### A2. Establish a formal experiment registry and promotion ladder

**Current behavior:** Many variants and historical campaigns exist, but trial count, selection history, dataset reuse, and promotion status are distributed across filenames and notes.

**Problem:** Researchers can unknowingly optimize on a former test set or revive a rejected concept under a new label.

**Enhancement:** Register hypothesis, immutable configuration, dataset hash, train/validation/test dates, trial family, result, rejection reason, and owner. Promotion should progress from unit/parity tests to historical walk-forward, untouched paper forward, shadow live, and only then limited capital.

**Impact:** HIGH
**Complexity:** Medium

---

### A3. Use one account-wide risk ledger while preserving pool budgets

**Current behavior:** Pool A and SA maintain separate logical risk views while sharing one MT5 balance, and TGA manages account positions independently.

**Problem:** Logical isolation does not prevent combined margin use, correlated exposure, or one pool's floating loss from constraining the other at the broker.

**Enhancement:** Keep separate strategy budgets but maintain one authoritative ledger of actual fills, stop-loss cash exposure, margin, correlation, and Guardian ownership for the whole account. Require an atomic reservation before any pool sends an order.

**Impact:** HIGH
**Complexity:** Hard

---

## 📊 PRIORITY MATRIX

| Priority | Items | Impact | Effort |
|---|---|---|---|
| **P0 — Do First** | C1, C2, C3, C4, C5 | Make results trustworthy and prevent uncontrolled execution/risk | 3-6 weeks |
| **P1 — High Value** | H1, H2, H3, H4, H5, H6 | Establish whether a real edge exists and size it safely | 4-8 weeks plus forward time |
| **P2 — Quality of Life** | M1, M2, M3, M4, M5, M6, L1, L2 | Improve robustness, attribution, and research quality | 2-4 weeks |
| **P3 — Future** | A1, A2, A3 | Prevent recurring path drift and coordinate the full account | 1-3 months |

---

## 💡 SUGGESTED EXECUTION ORDER

**Phase 1 — Safety & Reliability (3-6 weeks)**
1. C3: Correct Pool A entry/order semantics and preflight validation.
2. C4: Reconcile actual loss-at-stop, equity drawdown, and portfolio reservations.
3. C5: Restrict and persist Guardian ownership/state.
4. C1: Freeze a common bar/snapshot contract.
5. C2: Put Guardian behavior into replay.

**Phase 2 — Profitability Evidence (4-8 weeks plus data collection)**
6. H2: Remove lot-floor free looks and decouple admission from sizing.
7. H1: Measure each trigger independently on the corrected path.
8. H3: Separate predictive votes from vetoes and calibrate probabilities.
9. H4: Start an untouched, registered forward test.
10. H5: Apply low risk only after candidate admission is stable.
11. H6: Keep Pool A out of live allocation until it has a faithful harness.

**Phase 3 — Robustness (2-4 weeks)**
12. M1: Add executable fills and variable costs.
13. M2: Recalibrate elapsed-time lookbacks.
14. M3: Repair cross-asset/regime enforcement.
15. M4: Correct adaptive-memory units.
16. M5: Add parity/restart tests.
17. M6: Make market sessions DST-aware.
18. L1-L2: Version evidence and improve the structured audit trail.

**Phase 4 — Architecture (1-3 months)**
19. A1: Consolidate the decision and exit kernel.
20. A2: Formalize the research/promotion registry.
21. A3: Consolidate account-wide risk state.

---

## 🎯 EXPECTED COMBINED IMPACT

If P0+P1 implemented:
- **Win rate:** No defensible uplift estimate yet; the target is a stable, net-positive rate on an untouched sample, not a fitted delta.
- **Profit factor:** Current PF 1.41 is not live-valid. Use net PF ≥1.20 in each untouched period as a promotion threshold, not a forecast.
- **Max drawdown:** Current 26.88% backtest drawdown is too high. Target ≤10%-15% after admission and sizing are decoupled; direct linear scaling is invalid under the present lot-floor behavior.
- **Operational uptime:** Invalid-stop and unowned-position interventions should approach zero; exact improvement requires new structured logs.

**Net effect:** These recommendations do not promise profit. They convert APEX from a system with incompatible live/simulated strategies and misleading risk measurements into one whose expectancy can be measured honestly and whose losses are bounded.

---

## ⚠️ NOTES

- No source code, configuration, trading state, broker order, or live process was changed during this audit. This file is the only created artifact.
- Historical live logs end on 2026-05-15 and describe older code. They diagnose past failure but cannot validate the August build.
- The old 62.50% win-rate/+82.51% report is superseded and was not used as evidence.
- The latest 3% run is promising, but C1, C2, and H4 must be cleared before it is treated as tradable evidence.
- This audit is technical research, not a guarantee of returns or personalized financial advice.

---

**End of Recommendations Document**

*Awaiting approval before any code changes.*
