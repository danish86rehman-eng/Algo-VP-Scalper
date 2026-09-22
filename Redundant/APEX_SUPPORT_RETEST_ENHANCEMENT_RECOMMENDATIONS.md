---
type: review
status: implemented-demo-contract
confidence: anecdotal
updated: 2026-09-02
sources: 6
tags: [apex, xauusd, support-resistance, volume-profile, reentry, loss-review]
---

# APEX XAUUSD support-retest incident — Enhancement Recommendations

**Generated:** 2026-09-02; broker snapshot 12:33:02 UTC / 17:33:02 Pakistan time.
**Status:** Displacement-reclaim/FVG correction implemented and validated following the operator's explicit instruction. See the implementation addendum for current status; earlier proposal sections retain the diagnostic history.
**Scope:** Recent losses, especially the profitable buy followed by a losing buy around 4324–4328 on 2 September. This is a focused incident review, not a whole-system audit.
**Account/executor:** MT5 reported `ACCOUNT_TRADE_MODE_DEMO` (0); existing SA bot orders, Guardian management. The initial diagnosis used read-only queries. The subsequent authorized demo correction is documented in the implementation addendum.

The clearest confirmed gap is **re-entry after a reversal exit without fresh confirmation**. The Guardian recognized a bearish M5 engulfing and closed the first buy profitably. Five minutes later the scalper bought again using an older bullish M15 signal. The missing support-role state and missing active M15 VP context are relevant research hypotheses; the exact POC at 4328 is still operator-reported.

## Incident and evidence

The operator reports that 4324 support broke, the attempted recovery failed, and M15-chart POC near 4328 should have supplied context for a short. Preserve this observation as the research question. It does not establish that a short would have filled or won.

### Broker-reconciled sequence

All times below are **broker UTC**, obtained from `history_deals_get`. Pakistan time is UTC+5. Net includes entry commission and all exit profit, commission, swap and fee; attribution follows the opening position, including Guardian exits with a different magic number.

| Position | Side | Entry time | Actual entry | Exit time | Actual exit | Net USD | Broker exit |
|---|---|---|---:|---|---:|---:|---|
| 494866895 | SELL | 00:00:09 | 4324.105 | 01:06:15 | 4308.131 | +47.60 | Trailed SL |
| 494897782 | SELL | 06:30:04 | 4322.241 | 07:10:33 | 4330.867 | -34.95 | SL |
| **494903819** | **BUY** | **08:00:06** | **4324.048** | **08:15:52** | **4327.303** | **+15.73** | **TGA_EARLY_CLOSE** |
| **494906413** | **BUY** | **08:20:54** | **4325.538** | **08:51:53** | **4318.853** | **-27.18** | **SL** |

The highlighted pair lost **$11.45 net**. Four settled positions on 2 September total **+$1.20** at the snapshot. Position 494933044, opened at 12:00:00, was still open when broker state was inspected and is excluded from settled results. These are partial-day observations, not an end-of-day statement.

Other recent closed losses were 494596660 (31 August, -$37.31), 494690521 (1 September, -$33.49), and 494729996 (1 September, -$28.32). The 31 August–2 September snapshot contains 13 closed positions: 8 winners, 5 losers, +$74.91 net. This small, selected period establishes transactions, not strategy expectancy.

### What was available before the second entry

| Closed bar, open time UTC | Relevant facts | Interpretation / limit |
|---|---|---|
| M15 07:30 | Close 4322.847 | Below operator's 4324 level |
| M15 07:45 | Close 4323.265 | Second consecutive M15 close below 4324 |
| M15 08:00 | Open 4323.281; high 4331.607; close 4325.452 | Green aggregate candle, but its close did not hold above reported 4328 POC |
| M5 08:10 | Open 4329.824; high 4331.607; close 4325.452 | Bearish engulfing recognized by the Guardian after this bar closed at 08:15 |
| M5 08:15 | High 4327.755; close 4325.607 | Completed by 08:20; recovery remained below reported POC |

At the second entry, **the M15 08:15 candle was still forming**. Its eventual 08:30 close at 4323.327 supports the retrospective narrative but must never be used to justify a decision at 08:21. The M5 08:20 candle was also incomplete. A first buy had already briefly traded above 4331 before reversing, so “never broke above 4324” would be inaccurate; **failed sustained acceptance** is the hypothesis.

The Guardian logged `Trigger 1: Bearish Engulfing`, peak 1.12R, and a profitable early close. The application timestamps trail/lead broker timestamps by roughly 14–15 seconds here; both clock domains are preserved in the evidence bundle. They must not be merged as a single precise execution clock.

### Exact trigger reconstruction

Broker bars, restricted to bars whose closing time was at or before each application decision, reproduced both signal entries, stops, targets and short-term-bias decisions:

| Application decision UTC | Last closed M15 opened | Selected SSL | Sweep extreme bar opened | Signal entry | STB / HTF | Sweep wick |
|---|---|---:|---|---:|---|---:|
| 08:00:20 | 07:45 | 4323.1278 | 07:00 | 4323.265 | BULLISH / BEARISH; MEDIUM | 21.39% |
| 08:21:08 | 08:00 | 4324.25775 | 07:45 | 4325.452 | BULLISH / BEARISH; MEDIUM | 48.61% |

Both trades were `SWEEP_REJECTION`, selected ahead of `JUDAS`. They were not logged as breakout entries. The detector uses any pierce in four M15 bars plus a directional last close; the second signal reused a sweep from before the Guardian exit. `nearest_ssl` is recalculated from recent equal lows, not a durable support zone with a known break/reclaim history.

The existing 45% sweep-wick research filter, with everything else held fixed, rejects the **winner** and retains the **loser**. Its 35% variant does the same; 55% rejects both. This is a two-case predicate check, not a backtest of the resulting trading sequence or a verdict on the whole L-016 campaign.

## 🟥 CRITICAL (Safety/Correctness)

No new critical severity finding is established by this focused review. The independently verified reporting error is M1 below.

## 🟧 HIGH IMPACT (Performance/Profitability)

### H1. A reversal exit does not invalidate a same-direction re-entry

**Current behavior:** `apex_ai/trade_guardian_agent.py:397` detects and logs the bearish reversal. `apex_ai/scalper_agent.py:1246` forwards P&L to cooldown, but does not retain that structural warning; `:600` deduplicates only open positions. A profitable close receives five minutes of cooldown regardless of why it closed.

**Problem:** The 08:21 buy passed after a bearish exit using a signal whose confirming M15 bar had closed before that exit. A profitable exit is treated as permission to reuse a weakening setup.

**Enhancement:** Persist the symbol, direction, exit reason, reversal bar and originating setup ID. For reversal exits, require a **new closed trigger bar after the exit and a new confirmed setup** before another same-direction entry. A timer expiring alone must not re-arm the old setup. Mirror both directions and replay behavior.

**Impact:** High
**Complexity:** Medium

### H2. The sweep detector lacks support-role history and subsequent invalidation

**Current behavior:** `apex_ai/scalper/trigger_engine.py:51`, `:271` and `:511` choose current nearby equal lows and inspect four bars, without tracking a level through support, break, failed reclaim and resistance. `apex_ai/scalper/short_term_bias.py:187` measures 12 bars of displacement; the live caller supplies M15 (`apex_ai/scalper_agent.py:580`, `:837`), so this read spans about three hours. HTF disagreement reduces confidence but permits entry (`short_term_bias.py:440`). The sweep detector does not use its caller's M5 frame to invalidate an old M15 signal.

**Problem:** A rebound can stay short-term bullish while a local recovery fails. The new SSL level can be mistaken for fresh rejection evidence without accounting for intervening bearish price action.

**Enhancement:** Add a shared, closed-bar level-state evaluator and invalidate sweep evidence when a later opposite structural event supersedes it. Require confirmation that price has reclaimed and held the relevant broken zone for a long; evaluate a short separately after a failed upward retest. Define buffers, freshness and reset rules before testing.

**Impact:** High
**Complexity:** Hard

### H3. Existing VP research does not supply the requested M15 context

**Current behavior:** `apex_ai/scalper/decision_params.py:198` specifies H4 profiles; `:256`, `:457` and `:557` disable the VP gate, VP reaction trigger and leg-confluence gate. `apex_ai/scalper_agent.py:623` builds the optional H4 context. No active M15 profile participated in either logged entry. Previously tested VP arms in ledger L-005/L-014/L-015 were not promoted.

**Problem:** The bot cannot check the operator's particular 4328 profile. Enabling an old H4 “no POC” rule would answer a different question and repeat rejected research.

**Enhancement:** Record an intraday profile with explicit instrument/feed, window anchors, source timeframe, binning, volume type and as-of timestamp. First use it as observation data. Test incremental value **after** the role-state rule, including winners removed and trades newly admitted. POC proximity alone must never select SELL.

**Impact:** High
**Complexity:** Medium

## 🟨 MEDIUM IMPACT (Robustness/Reliability)

### M1. A profitable early exit is recorded as a target win

**Current behavior:** `apex_ai/scalper_agent.py:1219` maps an otherwise unspecified profitable close to `WIN_TP1`. `apex_ai/scalper/postmortem.py:378` then classifies wins without verifying target execution. For 494903819, incidents claim target success and gross +$16.28, while broker records show early close at 4327.303, net +$15.73, well below TP1 4335.133. The incident also uses signal entry 4323.265 instead of fill 4324.048 and stores the confidence/HTF fields as UNKNOWN despite text logs containing them.

**Problem:** This hides the reversal warning and distorts exit attribution, R, MFE and the labels used to study remedies. A “clean target win then false signal” is materially different from “reversal exit then re-entry.”

**Enhancement:** Keep exit reason separate from profit sign, and reconcile all deals by position ID including entry costs. Preserve actual fill, original stop, trigger time, confidence and HTF snapshot. Only label TP1 reached when broker/Guardian evidence confirms it; retain the original application record with a correction audit trail.

**Impact:** Medium
**Complexity:** Medium

## 🟩 LOW IMPACT (Nice-to-Have)

No separate cosmetic changes proposed.

## 🟦 ARCHITECTURAL (Future-Looking)

Reuse the shared Guardian engines and existing `--tga-exits` simulation path. L-003 was closed as a fidelity implementation on 28 August; older vault statements that no Guardian replay exists are stale. Its simulation default remains off. Adding another trading agent is unnecessary for this remedy.

## 📊 PRIORITY MATRIX

| Priority | Items | Impact | Effort |
|---|---|---|---|
| P0 — Establish reliable evidence | M1 | Correct exit/fill attribution before experiments | Medium |
| P1 — Test the incident mechanism | H1, H2, H3 | Re-entry control, level state, incremental VP context | Medium to Hard |
| P2 — Review reproducibility | Profile anchors and decision snapshots | Makes future incidents independently checkable | Included in H3/M1 |
| P3 — Future | None | Existing shared simulation can be extended | No new architecture proposed |

## 💡 SUGGESTED EXECUTION ORDER

**Phase 1 — Preserve and reconcile.** Completed for this incident: broker deals, M15/M5/H1/H4 bars, exact signal reconstruction, costs, and relevant log excerpts saved. Fix M1 in implementation before treating application outcome labels as research evidence.

**Phase 2 — Fresh-entry guard (L-017 arm A).** Persist a reversal invalidation event keyed by symbol/direction/setup. At minimum the trigger's confirmation close must be strictly later than the prior exit. Additionally require a fresh valid sweep/reclaim event after the invalidation; changing the nearest equal-low number must not reset it. Define deterministic event IDs, crash recovery and expiry. Check the symmetric short case. This would reject the observed 08:21 candidate because its confirmation closed at 08:15, before the prior exit. Recheck after a new M15 close; do not automatically enter then.

**Phase 3 — Role-state rule (arm B, separate from A).** Freeze an established support/resistance zone before testing a break. Track `SUPPORT -> BREAK_PENDING -> BROKEN_SUPPORT -> RETEST -> RECLAIMED_SUPPORT or CONFIRMED_RESISTANCE`, symmetrically for resistance. Distinguish a wick breach from sustained closed-bar acceptance. Require an unsuccessful reclaim and a later M5 bearish confirmation for a short candidate; require an above-zone reclaim and a successful hold for a long candidate. Pre-register the exact close count, ATR/spread buffer, touch tolerance, lifetime and confirmation predicate before the arm. Never hardcode 4324 or retrospectively choose swing pivots.

**Phase 4 — Incremental profile context (arm C).** Once the operator's profile is specified, freeze its window and binning. Compare B against B plus VP, not against an unrelated H4 gate. If 4328 was available before entry, record its role as an overhead interaction zone and whether price accepted or rejected it. Use data available at decision time; never calculate a full-session POC and apply it to an earlier entry. A short needs its own confirmed trigger, broker fill and cost-valid geometry; otherwise WAIT. Keep TP1 >=2R and the existing cost gate.

**Phase 5 — Validation.** Both live and backtest must call the same state evaluator. Run chronological portfolio replay **with `--tga-exits`**, equivalent costs, cooldown, position limits and session/news gates. Test LONG/SHORT symmetry, stale setup reuse, valid fresh reclaim, failed retest, missing profile, restart and future-data invariance. Report net $, sumR, average R, drawdown, all rejections, winners removed and newly admitted trades. Do not relax existing gates just to make the hypothetical short fire.

This incident and previously examined windows are development data. The vault's current seven-part evidence gate additionally requires untouched OOS data, pre-established power, pooled bootstrap uncertainty, a null comparison, regime stability, measured costs and a variant count. Same-sign folds alone are insufficient. Three candidate arms are described; **zero performance arms have been run for L-017**. Exact B/C settings remain draft, not a completed preregistration.

## 🎯 EXPECTED COMBINED IMPACT

Suppressing only position 494906413 while holding every other trade fixed changes the pair from -$11.45 to +$15.73: **$27.18 avoided in this single historical counterfactual**. The new-bar condition alone rejects that candidate. This is not a portfolio backtest: removing a trade changes position availability, cooldown and later entries.

Win-rate improvement, profit-factor improvement and drawdown reduction are **UNKNOWN**. No profitable short from 4328 has been demonstrated. The supported outcome is a concrete, testable way to stop this particular reuse of an invalidated signal; broader loss reduction must be measured.

## ⚠️ NOTES

- **POC 4328 is unverified:** chart timeframe M15 is known, but profile type, start/end, lower-timeframe input, bins and feed are UNKNOWN. A screenshot/settings request was sent. Stored Exness bars have `real_volume=0`; any profile built from their `tick_volume` is an activity proxy.
- Role reversal is a recognized technical-analysis hypothesis, not a guarantee that every broken level will reject a retest. [Fidelity's support/resistance explanation](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/support-and-resistance?print=true-0).
- A volume profile depends on its window, rows and underlying lower-timeframe data; displaying it on M15 does not uniquely specify the calculation. POC describes the highest-volume price bin and does not alone determine direction. [TradingView's volume-profile documentation](https://www.tradingview.com/support/solutions/43000502040-volume-profile-indicators-basic-concepts/).
- Prior vault sources reviewed: `volume-profile`, `attribution-trap`, `mql5-invalidated-orderblocks-mitigation`, `stop-clusters-liquidity-cascades-sweep-vs-acceptance`, `apex-ai-remedy-ledger` and `evidence-grading`. The mitigation-block source supports a rule shape, not performance of this support/POC setup. Unequal historical POC buckets alone do not establish predictive power.
- Production files already contained uncommitted edits when the review began. They were preserved. The replay explicitly disabled the optional wick filter and matched the recorded signals; it reconstructs trigger/STB decisions, not every historical in-memory runtime state.
- Evidence directory: `D:/Hermes Quant/Quant GPT Test Claude - Final/docs/reviews/2026-09-02-support-retest-evidence/`. `manifest.json` records provenance and code hashes. Application incidents are retained as fallible source records, separately from broker truth.

**End of Recommendations Document**


## Screenshot addendum — 2 September 2026

The operator supplied `XAUUSD_2026-09-02_17-33-04.png` and clarified: **a long should not be permitted until the previous broken support is reclaimed.** This refines the intended rule; it does not itself demonstrate a profitable parameterization.

![Operator chart, captured 12:32 UTC](<D:/OneDrive - Orient Petroleum/Personal/Obsidian/AI-Trading-Strategies/journal/reviews/assets/xauusd-support-retest-2026-09-02-1232-utc.png>)

**What is visible:** XAUUSD, chart header 2 September 12:32 UTC; a marked purple level **4324.76**; a volume concentration/white horizontal line around the operator-identified **4328** area; earlier decline, a bounce that failed, and a later recovery to a displayed **4334.14**. That later recovery is not evidence the earlier entries were valid. The profile settings, exact POC label, feed and computation window are not displayed. The screenshot supports the operator's chart interpretation but cannot independently prove the profile's value at 08:00 or 08:21.

### Why the current code permits the long

`apex_ai/scalper/trigger_engine.py:523` tests whether a recent low pierced the dynamically selected SSL and the latest M15 candle is green and closes back above that SSL. It does not require reclaim of the operator's prior broken support zone. `apex_ai/scalper/short_term_bias.py:440` permits a matching short-term direction despite BEARISH HTF. Thus it is trading a presumed sweep reversal, with no mandatory prior-support-reclaim contract.

| Entry | Evidence available before entry | Consequence for the proposed rule |
|---|---|---|
| First buy, broker 08:00:06 | Latest closed M15 close 4323.265; actual fill 4324.048; both below marked 4324.76 | A prior-support-reclaim requirement would also exclude this eventual winner. |
| Second buy, broker 08:20:54 | M15 08:00–08:15 closed 4325.452, above 4324.76; actual fill 4325.538 | “Close above the level” alone does not reject it. Confirmation must account for a held reclaim and the intervening bearish invalidation. |

The most recent completed M5 candle (08:15–08:20) had low **4324.770**, just **0.010** above the marked level, and close 4325.607. Even an unbuffered “M5 low and close stayed above 4324.76” check can pass. Do not claim that a bare retest condition solves this case. Price/feed tolerance, the actual zone boundaries and the meaning of a successful hold must be fixed before a performance test.

### Refined remedy contract for L-017

1. **Broken support is ineligible for long entry until reclaim is confirmed.** Preserve a zone known before the break; a newly calculated nearby equal low cannot replace its history.
2. **Reclaim requires acceptance, not only a traded price above the line:** a completed M15 reclaim above the zone, followed by a separately completed hold/retest under a pre-registered buffer and timing rule. Keep status `RECLAIM_PENDING` until those requirements finish. Whether the reported POC belongs to the required zone remains a separate VP hypothesis; do not silently merge two different levels.
3. **A subsequent bearish invalidation cancels prior long permission.** In this incident, the Guardian's bearish-engulfing exit occurred after the 08:15 M15 confirmation. Any new long needs post-invalidation closed-bar evidence. Waiting five minutes does not restore the previous permission. This is the exact, independently reproducible reason the second candidate fails arm A.
4. **WAIT while reclaim remains unconfirmed.** A sell requires a separate failed-retest/rejection setup and the existing execution/risk/cost gates. Mirrored rules apply after broken resistance.

A stronger requirement using two M15 confirmations or a particular ATR/spread buffer is a **candidate parameter choice**, not a proven correction. No threshold was optimized on this screenshot. A broad reclaim filter could remove both the +$15.73 winner and the -$27.18 loser; it must not inherit the earlier **$27.18 avoided** counterfactual, which refers only to skipping the second entry while holding other trades fixed.

**Recorded outcome:** screenshot preserved; rule intent and the simple-threshold counterexample added to the vault and L-017. Production trading logic is unchanged. Source SHA-256 and image limitations are in `screenshot-provenance.json` in the evidence directory.


## Implementation addendum - operator-requested correction


**2 September 2026 — implemented, validated and active on DEMO.**

The operator explicitly requested correction after yesterday's loss and today's repeated buy. This is an operator-defined demo entry restriction. Its effect on future expectancy is unproven; it is not a live-money promotion.

## Finding

The existing SWEEP_REJECTION detector could treat a local low sweep and green M15 close as a bullish candidate while an older support level remained broken. The separate consultation displacement requirement applied to BOS_RETEST, not every entry type. A five-minute cooldown also allowed reuse of pre-exit evidence after the Guardian's bearish reversal exit.

| Broker position | Actual entry | Net outcome including costs | New gate on information available at entry |
|---|---:|---:|---|
| 494729996, 1 September 07:15:22 UTC | BUY 4433.071 | -$28.32 | WAIT_LEVEL; broker swing 4433.762 not reclaimed |
| 494903819, 2 September 08:00:06 UTC | BUY 4324.048 | +$15.73, Guardian early close | WAIT_LEVEL; broker swing 4326.124 not reclaimed |
| 494906413, 2 September 08:20:54 UTC | BUY 4325.538 | -$27.18 | WAIT_LEVEL; same broker swing not reclaimed |

These are causal, closed-bar reconstructions using the recorded application decision times. They are not a portfolio counterfactual. The correction removes the earlier winner too. Broker-derived swings are distinct from the screenshot's marked 4324.76 and the operator-reported POC near 4328. No chart price is hardcoded.

## Implemented entry contract

One evaluator, `apex_ai/scalper/reclaim_fvg.py`, is called after detection for **every SA entry type** in both `scalper_agent.py` and `backtest_scalper.py`:

1. Reconstruct confirmed M15 swing levels from the latest 150 completed bars. Three bars on each side confirm a pivot, before any eligible break. Consider levels inside the original stop-to-TP1 corridor; choose the nearest broken obstacle ahead of the quote, otherwise the nearest broken level behind it. A wick breach alone does not break a level.
2. A close through the level by more than **0.10 × preceding ATR(14)** breaks it. Freeze this buffer; a subsequent failed reclaim restarts the sequence.
3. Require a later directional M15 displacement reclaim: body **>=0.80 ATR**, body **>=60%** of candle range, close in the outer **25%**, touching/crossing the level and closing beyond its buffered boundary.
4. That displacement must create a same-direction three-candle M15 FVG at least **0.05 ATR** wide. All three candles must be consecutive and completed. The third candle's close confirms formation; it cannot count as the return.
5. Wait for the first **subsequent completed M5 return**. It must close directionally inside the FVG and on the reclaimed side of the level. Missing return history, invalidated gaps, stale returns and gaps aged **24 M15 bars** are rejected. The executable quote must still lie inside the qualified area; it is checked again immediately before submission.
6. After a same-direction position closes, an applicable broken-level setup needs a new displacement reclaim whose candle closes after that exit. Restore the last close by originating position ID, including Guardian-written exits, from broker history on restart. If history cannot be restored, entry is blocked.

SELL uses the symmetric rule after broken resistance. With no applicable broken level, the existing candidate continues through the other gates. A complete reclaim sequence alone does not generate a trade: the existing trigger, bias, regime, cost, sizing, news, session and position checks still apply. Guardian management and candidate stop/target geometry are preserved.

The backtest now observes M5 decision times while retaining M15 triggers, so a return confirmed at :05 or :10 is available when it occurs. The prior M15-only research clock remains available with `--no-reclaim-fvg`; comparisons must account for the clock difference. Accepted simulation trades and live decision logs include reclaim evidence. Bar event timestamps in that evidence denote bar opens; FVG formation is explicitly the third candle's close.

Default: `RECLAIM_FVG_ENABLED=True`; configuration era: `2026-09-02-reclaim-fvg`. `--no-reclaim-fvg` is an explicit research/rollback override. The optional L-016 wick filter remains off. Thresholds were declared before validation and were not tuned to these three outcomes.

## Validation and limits

- **425 unit tests pass**, including 27 new tests covering both directions, valid entry, weak/wick reclaim, missing/invalid/forming candles, FVG formation/return ordering, gap invalidation, expiry, prior-close freshness, future-data invariance, shared evaluator, broker incidents and final-quote rejection without an order.
- `py -3.14 -E -m compileall -q .` passes from `apex_ai`.
- Bounded full-pipeline smoke, 1 September 00:00 through 2 September 12:00 UTC, with Guardian exits, existing session/news/cost logic and order submission prohibited: completed; four simulated trades remained, all with no applicable broken level. Valid reclaim-to-entry sequences were verified by synthetic tests in both directions. Reclaim rejections: WAIT_LEVEL 60, WAIT_RETURN 9, WAIT_DISPLACEMENT 7, WAIT_FVG 3. This exercises integration, not profitability. It uses development data, bar-level fills and the existing news cache; no untouched OOS or baseline comparison was run.
- Saved evidence: `reclaim-full-tests.txt`, `reclaim-focused-tests.txt`, `reclaim-pipeline-smoke.json`, reproducible `reclaim_pipeline_smoke.py`, and the original immutable incident snapshot under `2026-09-02-support-retest-evidence/`.

The screenshot does not establish profile anchors, bins, feed or entry-time POC. The VP enhancement remains an open research item; the correction does not turn a reported overhead POC into an automatic short. The 150-bar swing detector also does not claim to reproduce every manually marked chart level.

## Runtime verification

Activated at **13:11:41 UTC / 18:11:41 Pakistan time** on the broker-verified DEMO account. Targeted scalper reload: PID 5720 was replaced by watchdog child 9656; Guardian PID 5740 and watchdog PID 7704 continued running. Startup logged **RECLAIM gate=ON**, restored five symbol/direction close barriers, and adopted existing position **494933044**. Three completed scan cycles were observed. At 13:13:04 UTC the broker still reported the same position, volume 0.01, SL 4303.498 and TP 4367.404. Only the scalper process was reloaded; no discretionary order or position modification was submitted. Runtime parameters remain pool $1,000, risk 3%, XAUUSD, 30-second scans, $100 daily limit, FRESH pool mode.

Artifacts: `reclaim-demo-before.json`, `reclaim-demo-after.json` (code hashes), `reclaim-processes-before.json`, `reclaim-processes-after.json`, and `reclaim-demo-activation.log`. The account has an existing position, so activation verification establishes loaded enforcement and position adoption, not a new forward trading result.
