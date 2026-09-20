# Changelog

All notable changes to the APEX AI trading system. Timestamps are UTC.
Newest first. Every entry states what changed, why, and how it was verified.

## 2026-09-20 04:32 UTC — Reconcile SWEEP_REJECTION code and runtime document

- Reconciled the technical document to the executable ACTIVE policy,
  causal `0.10 ATR` pool match, final ASK/BID geometry checks, frozen
  location/pool revalidation, and the approved non-dry-run DEMO command.
- Updated the preferred XAUUSD DEMO launch profile for future starts to a
  `$900` pool, `3%` risk, and `$100` daily loss limit while retaining FRESH
  pool mode and the mandatory ACTIVE market-location policy.
- Closed one contract gap found during the reconciliation: if an exact frozen
  structural location disappears, a nearby replacement location ID now returns
  `LOCATION_INVALIDATED` instead of preserving executable permission under the
  old ID. Risk, sizing, targets, sessions, cooldown, Guardian, news, trigger
  priority, wick filtering, base detection, and M5 thresholds are unchanged.
- Verification: 90 focused reconciliation tests and all 681 repository tests
  pass; `py -3.14 -E -m compileall -q .` and `git diff --check` pass.

## 2026-09-20 04:20 UTC — Complete SWEEP_REJECTION runtime contract

- Made ACTIVE named-liquidity policy mandatory whenever `SWEEP_REJECTION` is
  enabled. Startup now logs the resolved policy path, allowed families, and
  `ALLOW_UNKNOWN_LOCAL` / `ALLOW_CONSUMED` safety flags; missing or inactive
  configuration fails closed.
- Replaced exact-number pool matching with causal, pre-event ATR-normalized
  matching and added future/self-pool, consumed-pool, stable candidate-ID, and
  named-pool telemetry guarantees. The base detector, wick policy, priority,
  confidence, and 2R/3R targets were not changed.
- Added live/replay final frozen-location checks and executable quote geometry:
  BUY uses ASK, SELL uses BID, with explicit `FINAL_SPREAD_FAIL`,
  `FINAL_SPREAD_TO_STOP_FAIL`, `FINAL_MIN_SL_FAIL`, and `FINAL_NET_R_FAIL`
  blockers. Signal and fill telemetry remain separate.
- Verification: 20 focused runtime-contract tests and 678 full-suite tests
  pass; `py -3.14 -E -m compileall -q .` and `git diff --check` pass. The
  XAUUSD DEMO process is running with `--pool-mode FRESH`, ACTIVE location
  policy, no `--dry-run`, and no manual order; it is waiting for a genuine
  candidate.

## 2026-09-20 02:53 UTC — Correct active-auction Volume Profile anchors

- Replaced score-first W1/H4 ACTIVE selection with current-structure auction
  discovery. Historical score-ranked legs remain separately addressable
  REFERENCE profiles and no longer supply the active location gate.
- Resolved same-candle dual pivots deterministically, separated anchor endpoint
  prices from internal profile extremes, and added frozen ACTIVE/REFERENCE/
  RETIRED lifecycle telemetry with stable profile IDs.
- Reconstructed W1 profiles from complete M15 tick volume (H1/native fallback)
  and H4 profiles from complete M5 tick volume (M15/native fallback), clipped
  to the anchor interval with a configured 48-row policy and volume checksum.
- Broker-dataset validation independently selected W1 3942.204→4697.232 and
  H4 4235.035→4399.826, matching the approved audit's detailed POC/VAH/VAL
  targets. Verification: 658 tests pass, `compileall` passes, and
  `git diff --check` is clean (line-ending warnings only).

## 2026-09-19 14:34 UTC — Activate top-down market location for sweep rejection

- Replaced the ambiguous location switch with the explicit `OFF`/`ACTIVE`
  contract. `ACTIVE` is binding only for `SWEEP_REJECTION`; other trigger
  families retain their own strategy contracts. Live and replay now pass the
  same mode, W1/H4 engine, permission rules, and reaction confirmation path.
- Preserved separate W1 macro and H4 local anchored profiles, added complete
  per-timeframe ATR-distance telemetry, immutable sweep-location association,
  explicit acceptance/expiry rejection reasons, and checksummed atomic profile
  state. Profile lifecycle keys are symbol-scoped.
## 2026-09-13 07:36 UTC — Add default-off completed-session sweep trigger

- Added a feature-gated `SESSION_SWEEP` scalper trigger for completed Asia, London and New York
  liquidity from the vault ledger. BSL raids may qualify only for bearish reversals and SSL raids
  only for bullish reversals, after strict breach, reclaim, displaced M5 market-structure shift,
  frozen-pivot chronology and an unconsumed opposing session-liquidity target. Two closes accepting
  outside the level cancel the reversal. The entry must retain at least 2R gross and 1.5R after costs.
- The live path validates the ledger generation, freshness, UTC timestamps and Exness account/server,
  then revalidates the quote, account, costs and risk immediately before submission. A durable setup
  reservation prevents retrying an uncertain order submission. Guardian keeps the structural target
  fixed. Replay reconstructs the same levels causally from broker M1 bars instead of reading today's
  ledger, and calls the same detector used by live code.
- The trigger remains off unless `--session-sweep` is supplied. No live process was restarted and no
  order was placed. Focused trigger tests pass; the chronological validation windows observed zero
  trades in one fold and one losing trade in another, so evidence does not support live activation.
  The longer training fold was invalid because MT5 returned no symbol metadata late in replay.

## 2026-09-09 11:48 UTC — Restore pre-September 7 scalper confidence behavior

- Restored the demo-era confidence behavior requested by the operator: after the existing STB gate
  admits a setup, trigger/STB confidence labels no longer impose an additional global HIGH/HIGH
  requirement. The older thin-liquidity rule still requires HIGH STB confidence during its configured
  UTC hours.
- Kept the shared live/backtest policy function so both paths remain identical. Updated the startup
  policy message and stamped new incidents with configuration era
  `2026-09-09-pre-high-confidence-restored`. Reclaim/FVG, sessions, risk, targets, cooldown, news,
  consultant, CRG and Guardian behavior are unchanged.
- Focused policy tests: 2/2 pass; compileall is clean. Full suite: 504/507, with the same three
  pre-existing session-window assertion failures documented in the preceding telemetry entry.
  Reloaded only the flat scalper through its watchdog: PID 15712 became PID 6836. Guardian PID 15704
  and watchdog PID 10436 remained running. Startup confirms live account 172783529, balance $1007.48,
  zero open scalper trades, unchanged launch arguments and the restored confidence-policy message.

## 2026-09-09 10:44 UTC — Add immutable scalper execution telemetry

- Added `scalper/execution_telemetry.py`, an independent `execution_telemetry_worker.py`, and watchdog
  supervision. The scalper emits small nonblocking localhost UDP messages after broker submission;
  the worker owns all MT5 history calls, calculations and files. Records include tick-native spread,
  request/deal provenance, latency, adverse implementation shortfall, direction-normalized
  1/5/15/30/60-second markouts, broker activity/impact proxies and a 60-minute spread-recovery curve.
- The trading process performs no tick-history query, horizon polling or telemetry file write. If the
  UDP worker is unavailable, events may be missing while trading continues unchanged. The JSONL and
  pending file expose no permission-shaped fields. DOM is `UNKNOWN`; VWAP and ICT/SMC/ATR/ADX/DI
  context have zero voting power.
- Added 13 execution-telemetry contract tests, including MT5 numpy structured-tick compatibility,
  nonblocking client behavior, watchdog supervision and source checks preventing history/poll work
  from returning to the scalper. Focused execution/close/forensics/CRT suite: 78 tests OK.
  `compileall` and `git diff --check` clean.
  Full suite: 504 of 507 tests pass; the three remaining failures are the pre-existing dirty-worktree
  session mismatch where `session_checker.py` uses Tokyo 00:00-06:00 and Pre-London 06:00-07:00 but
  older VP/session assertions still expect 00:00-02:00 and 06:30-07:00. No process restart or order.

## 2026-09-08 09:44 UTC — Activate operator-selected five-session default

- Updated `apex_ai/tests/test_whitelists.py` to verify all five operator-enabled session boundaries and out-of-window IDLE behavior. Existing explicit whitelist exclusion coverage retained. Full suite: 494 tests OK; compileall clean.
- Account 172783529 (Exness-MT5Real2) was flat before relaunch, balance/equity $1007.90. Stopped only old scalper PID 6404. Guardian PID 9028 attached at 09:44:23Z; scalper PID 26068 attached at 09:44:38Z. Locks 55555/55556 verified.
- Scalper arguments retained: `--pool 1000 --risk 0.03 --symbols XAUUSD --interval 30 --loss-limit 100.0 --pool-mode FRESH`. New process loads the operator's all-five-session default. HIGH trigger AND HIGH STB policy confirmed in startup output; news loaded 10 events. No proxy configured.
- Evidence: `apex_ai/logs/relaunch_20260908_tga_stdout.log` and `relaunch_20260908_sa_stdout.log`; corresponding stderr files empty at startup. Updated existing 15-minute monitor to cover all five sessions and these logs.

## 2026-09-07 18:19 UTC — Operator-requested HIGH-only scalper entries

Both trigger confidence and short-term-bias confidence must now equal HIGH.
Shared `decision_params.entry_confidence_allowed` is enforced after STB in
`scalper_agent._scan_symbol` and `backtest_scalper.run_backtest`, before
consultation and execution. Other/unknown ratings fail closed with CONFIDENCE
rejection telemetry. Trigger precedence and original ratings are preserved:
MEDIUM-rated CRT and M15 FVG candidates therefore cannot enter under this policy.
Configuration era: `2026-09-07-high-confidence-only`. This is an operator policy
change, not a measured profitability improvement.

Verification: 494 tests OK, compileall silent. Added rating-combination and
live/replay gate-order coverage. Three CRT integration tests explicitly isolate
their existing execution/confluence plumbing from the new confidence policy.
Reloaded only scalper PID 13336 as PID 6404 with the existing $1000 FRESH / 3%
command, after confirming the real account was flat. Guardian PID 12332 stayed
running. Startup at 18:19:16 UTC confirms `trigger=HIGH AND STB=HIGH required`,
new era, attach to account 172783529, and IDLE state with Open=0. Ports 55556
and 55555 match. Evidence: `apex_ai/logs/high_confidence_20260907_stdout.log`
lines 1, 11-12; matching stderr is empty. Sessions and other settings unchanged.

---

## 2026-09-07 09:46 UTC — Operator-approved Sweep-first trigger precedence

The operator explicitly approved `SWEEP_REJECTION -> HTF_CRT_SWEEP ->
FVG_FILL -> BOS_RETEST -> JUDAS` after reviewing the separate trigger study.
Moved only the Sweep consideration to the front of the shared
`SATriggerEngine.step2_trigger`; live and `backtest_scalper.py` import that
same engine, so no second decision path or new gate was introduced.
`VPLR_ENABLED=False` and `VA_FADE_ENABLED=False` remain unchanged and are
now explicitly pinned for both entry points by the priority regression test.
Detection, TFs, targets, sessions, risk, cooldowns and Guardian exits unchanged.

Updated the old priority assertions to the operator's new contract, added
both-direction precedence/fallback and shared-engine/default checks, and added
a live startup priority/feature-state log. Configuration era is now
`2026-09-07-sweep-first`. Isolated-copy verification: **492 tests, OK**;
compileall silent. The original study's snapshots/results remain untouched.

Evidence is **not** a validated profitability promotion: Sweep-first's
increment versus current precedence was -$7.39 / +$166.02 in the two windows.
This is an explicit operator policy choice despite that instability.
Study: `docs/reviews/2026-09-07-trigger-priority-results.md`.
Activation evidence: `docs/reviews/2026-09-07-sweep-first-activation/`.
Reload verified at **09:47:16 UTC** on the existing real account: scalper PID
13336 logs the exact new precedence and both disabled flags. Guardian PID
12332 remained running; ports 55556/55555 match those processes. Account flat
before and after, no deals today, pool $1,000, Algo Trading enabled. Command,
risk and sessions unchanged; see the activation README and startup excerpt.

---

## 2026-09-05 12:00 UTC — Production sessions narrowed to Tokyo and London/NY

Operator-directed strategy-policy change: the Scalper now enables only
`TOKYO_OPEN` (00:00–02:00 UTC) and `LONDON_NY` (12:00–13:30 UTC) by default.
`PRE_LONDON`, `LONDON_OPEN`, and `NY_LUNCH_REV` remain valid named windows for
explicit `--sessions` research runs, but ordinary live and backtest launches
are IDLE during them. The default lives in the shared `SASessionChecker`, so
live and simulator use the same gate.

Decision context: broker-net P&L for 2026-08-26 through 2026-09-05 was
`LONDON_OPEN -$24.16` and `NY_LUNCH_REV -$3.33`; `PRE_LONDON` was **+$73.50**.
Its removal is therefore an explicit operator restriction, not a finding that
all three excluded windows lost money, and this short sample does not satisfy
the chronological fold standard in CLAUDE.md §13.5.

Verified: 18 focused session/allowance tests pass; full discovery passes
**490 tests**; `py -3.14 -E -m compileall -q .` is silent.

## 2026-09-03 05:53 UTC - CRT confluence measurement and source review (L-020)

Added shared causal M15 MSS and fresh M5 FVG-return confirmation labels, with explicit OBSERVE/MSS/MSS_RETEST modes. Default OBSERVE records evidence without claiming an unvalidated filter improves returns. Strict final submission checks confirmation expiry; transient first-watch data failure retries next cycle. Replay supports a measured XAUUSD total-cost debit replacing spread/commission, with normal spread entry gates retained. 488 tests and compileall pass. Frozen June-August three-arm replay: identical 139 trades, zero eligible CRT candidates/trades; no incremental verdict possible. Whole-bot net +$91.48 / +6.768R is fold-unstable and turns negative under the higher-cost fixed-trade overlay. Source catalogue includes inspected MQL5 report numbers and vault corrections. See `docs/reviews/2026-09-03-crt-confluence-review.md`. Scalper and Guardian reloaded on verified DEMO at 05:53 UTC, mode OBSERVE; no risk/session loosening.

---

## 2026-09-03 05:07 UTC - Priority daily/weekly/monthly CRT sweep trigger

Added operator-requested `HTF_CRT_SWEEP` ahead of existing signals. Shared live/replay detector uses prior completed native D1/W1/MN1 ranges, closed M15 displacement reclaim, confirmed FVG and later quote return. Both directions, true calendar boundaries, no forming-bar leakage, per-symbol broker-restored setup identity, stop beyond raid and range/structure-capped TP under existing >=2R/cost gates. Guardian preserves the target. First watch runs outside entry sessions; execution still requires existing session/news/risk/cooldown permissions. Default enabled for DEMO; era `2026-09-03-htf-crt-trigger`. Prior CRT research remains negative or inconclusive, not evidence of this rule's profitability. Definition, validation and activation: `docs/reviews/2026-09-03-htf-crt-trigger.md`.

---

## 2026-09-02 (2) - M15 FVG entry and fixed structural TP

Operator-requested M15 demand-zone entry, with target before broken structure. Shared live/backtest evaluator uses completed displacement/FVG formation and an executable in-zone quote; no chasing, full mitigation reuse, or nearest-resistance skipping. Existing stop buffer, 2R and cost requirements remain binding. A valid M15 plan precedes a local sweep; the old M5 FVG detector is replaced while the new mode is enabled. TGA recognizes `SA_FVG_M15` and preserves its structural TP across restarts; the simulator mirrors this.

Default enabled, configuration era `2026-09-02-m15-fvg-target`. 447 tests pass; compileall and full-pipeline smoke pass. Actual tick evidence does not support a 4300 fill at the two marked revisits. No profitability claim; entry sessions unchanged pending operator clarification. Definition and relaunch evidence: `docs/reviews/2026-09-02-m15-fvg-entry.md`.

---

## 2026-09-02 - L-017 displacement reclaim and FVG return entry restriction

Operator-requested correction for repeated buys into broken support. A shared closed-bar evaluator now gates every SA trigger: known M15 level break, displacement reclaim, confirmed FVG, then a later completed M5 return. SELL is symmetric. Re-entry requires fresh evidence after the preceding same-direction close, including Guardian exits restored from broker history. The final quote must remain inside the qualified FVG. Default on; config era `2026-09-02-reclaim-fvg`.

Backtest mirrors the evaluator and final quote check and observes M5 confirmation times. Original trigger priority, SL/TP, risk and Guardian exit rules are preserved. Existing L-016 edits are retained with its wick filter off. 425 tests pass, compileall passes, and a bounded Guardian-enabled pipeline smoke completes. Replays block yesterday's loss and both morning buys today, including the winner. Future efficacy is unproven; this is a demo entry-contract correction. See `docs/reviews/2026-09-02-reclaim-fvg-correction.md` for the exact definition and activation record.

---

## 2026-08-28 (2) — L-012 re-measured on n=23: the fill haircut is half what n=11 showed

**Measurement only. No code change; no runtime behaviour change.**

- Ran the fill reconciliation with both agents stopped (no MT5 contention),
  comparing `logs/scalper_log.json` signal prices against `history_deals_get`
  fills. 23 positions reconciled — 287 of 310 log rows have no deal record
  because the broker archive starts 2026-08-01 while the log reaches to May.

  | | n=11 (08-26) | **n=23 (08-28)** |
  |---|---:|---:|
  | adverse fills | 8/11 (72.7%) | **14/23 (60.9%)** |
  | median realized R:R | 1.852 | **1.945** |
  | reward-leg haircut | 0.9260 | **0.9723** |

  Median slippage +0.347; stops 1.9% wider than intended; p10 1.438R;
  **30.4% below the 1.81 survival line**.

- **Cautions recorded with the number.** Mean/max realized R:R (2.723 / 16.195)
  are meaningless — a fill landing on the stop sends actual risk to zero. Mean
  slippage is *favourable*, a demo-server artefact, so 0.9723 is a floor not an
  estimate. A 2.8% haircut is still the same order as S13.12's 2.9% gross margin.
- **S13.12's -$3,731 / PF 0.9528 projection is annotated, not replaced.** It
  used the 0.9260 haircut; the true drag is ~38% of it. The corrected nine-year
  figure was NOT computed — that needs an L-008 re-run.
- L-012 stays **OPEN, measurement only**. Its stated blocker (exit-side changes
  unmeasurable under L-003) is now gone since L-003 closed, but the re-anchoring
  fix itself remains unmeasured.

**Operational note:** agents were stopped 07:34Z and restarted 07:35:39Z (~90s,
no open positions). Restart verified — scalper ACTIVE on LONDON_OPEN, Guardian
banner `BE@1.0R | Trail@1.5R | AggrTrail@2.5R`, confirming the refactored
`TGAConfig` loads identical values.

**Docs**: `docs/REMEDY_LEDGER.md` L-012, `CLAUDE.md` S13.14 and S13.12.

---

## 2026-08-28 — Guardian exit modelling in the simulator (L-003 closed) + VP_LEG_CONFLUENCE (L-015 rejected)

**Both are RESEARCH ONLY and ship disabled.** The live bot is performing; no
runtime behaviour changes in this entry.

### L-003 — the simulator now models the live Trade Guardian
- **Refactor.** The Guardian's decision surface (`SLEngine`,
  `EarlyCloseEngine`, `TPEngine`, `TGAConfig`, `TGAPositionRecord`) moved out of
  `trade_guardian_agent.py` into a new MT5-free `scalper/tga_engines.py`. Both
  processes import the SAME class objects; parity is asserted with `assertIs`.
  All thirteen thresholds moved to `decision_params` as `TGA_*`.
- **New.** `scalper/exit_manager.py` replays those engines over M5 bars:
  three-stage trailing with the breakeven structure-confirmation, early close,
  the 60-minute no-progress kill, and TP extension including the partial fill.
  Behind `--tga-exits`, default `TGA_EXITS_IN_SIM = False`.
- **Measured.** W1 +$2757.57 -> +$2008.19; W2 **-$650.02 -> +$585.54** (sign
  flip). Exit profile is unrecognisable — early close is the dominant live exit
  (119 W1 / 290 W2) and had no simulator representation. `NO_PROGRESS` explains
  §13.10's 36 losses that closed before price reached the original stop.
- **Caveat.** W2's dollar delta is confounded: `LOT_FLOOR` 872 -> 91 because the
  managed arm compounds to a larger risk unit. The clean figure is the
  shared-bar R change, W1 -0.88 / W2 +35.80.
- **Follow-on, not acted on.** 91 trades exit `TP -> EARLY_CLOSE` for about
  -90R across both windows.

### L-015 — `VP_LEG_CONFLUENCE`
- **New.** `scalper/leg_confluence.py` — two COMPLETED H4 swing legs
  (P1 low -> P2 high -> P3 low) as a location filter on existing triggers. A
  veto only; direction, stop and target stay with the trigger. HVN/LVN from
  MQL5 CodeBase 76264 (±1.0 SD of mean occupied bin volume). Modes
  `CONFLUENCE_ONLY` / `AT_LEVEL` / `LVN_VETO`. Mirrored into both decision
  paths in this change (invariant #2). Default `LEG_CONF_ENABLED = False`.
- **Measured, 8 arms, two disjoint windows.** W1 PF rises monotonically with
  strictness (1.35 -> 1.42 -> 1.49) and avgR with it (+0.1890 -> +0.2055 ->
  +0.2352) — the hypothesis' predicted shape. W2 inverts (PF 0.91 -> 0.87 ->
  0.87; avgR -0.0285 -> -0.0719 -> -0.0851). Sign flip across disjoint windows,
  so **REJECTED** under S13.12.
- **The quality gain never paid for itself even in W1**: avgR +24%, trades
  -38%, net -$835.65, sumR -12.17.
- **A veto buys a lottery ticket on what comes next.** W1 `LVN_VETO` removed 18
  trades worth -$272.86 and the book still fell $799.84 — the 15 NEW trades it
  enabled ran WR 6.67% / -$605.11 / avgR -0.8200.
- **Attribution trap, sixth occurrence.** The kept set read +0.2767 avgR; the
  arm delivered +0.2352, and the removed trades were profitable in aggregate
  (138 trades, +$393.68, PF 1.10).

**Verified**
- 378 tests pass (`py -3.14 -E -m unittest discover -s tests`); compileall clean.
- Both flags off reproduce `logs/l014b_w1_baseline.json` exactly — 281 trades,
  +$2757.57, identical trade list and rejection counts.

**Docs**: `docs/DESIGN_VP_LEG_CONFLUENCE.md`, `docs/RESEARCH_NOTES.md` S19-S20,
`docs/REMEDY_LEDGER.md` L-003 (closed) and L-015 (rejected).

---

## 2026-08-27 (2) — `VP_LIQUIDITY_REACTION` re-run against the fixed detector: REJECTED, ships disabled

**Researched** (no behaviour change — the shipped default was already off)
- The L-014 campaign recorded on 2026-08-27 (1) measured a detector carrying
  two correctness bugs and was marked VOID. Those bugs were fixed in the same
  session (raid recency bound `pierced[-1]` + `VPLR_RAID_MAX_AGE_BARS = 8`;
  candidates ranked by raid recency then depth rather than a fixed
  POC->VAH->VAL first-match with pools sorted by distance). This entry records
  the re-run.
- Eight arms, XAUUSD, $1000 @ 3%, `--spread-pips 2.5`, gate chain as shipped,
  two disjoint windows. W1 2026-05-15..08-23, W2 2025-08-01..2026-05-14.
  Raw runs: `logs/l014b_{w1,w2}_{baseline,asia,all,vponly}.json`.

  | arm | W1 net $ | W1 PF | W1 sumR | W2 net $ | W2 PF | W2 sumR |
  |---|---:|---:|---:|---:|---:|---:|
  | baseline           | +2757.57 | 1.35 | +53.10 | -650.02 | 0.91 | -17.99 |
  | VP ASIA_ONLY       |  +823.97 | 1.15 | +29.47 | -781.09 | 0.89 | -29.54 |
  | VP ALL_SESSIONS    |  -774.18 | 0.62 | -44.90 | -771.37 | 0.92 | -26.95 |
  | VP-only (triggers) |  -155.32 | 0.89 |  -5.40 | +323.26 | 1.07 | +13.85 |

- **Verdict: REJECTED.** Delta vs baseline is negative in BOTH disjoint windows
  for both scopes (ASIA_ONLY -$1933.60 / -$131.07; ALL_SESSIONS -$3531.75 /
  -$121.35). The fix made the rejection stronger — the void campaign's
  ASIA_ONLY delta flipped sign, so the old verdict rested on instability.
  `VPLR_ENABLED` stays `False`.
- The VP-only W2 delta of +$973.28 is not a promotion case: the W2 baseline is
  itself losing, and the same arm is -$2912.89 in W1. Standalone expectancy
  flips sign, -0.0551R (W1) vs +0.0539R (W2).
- Survival condition (S13.1) fails in 4 of 6 cells. Realised average wins are
  1.30-1.82R against a shipped TP1 of 2.0R because 10-26% of VP trades exit on
  the timeout — S13.3 reproduced by a new trigger.

**Methodology corrections carried into the invariants**
- **Dollar deltas double-count via compounding.** On the 205 bars shared
  between the W1 baseline and the W1 ASIA_ONLY arm, entry/stop/exit prices are
  identical and the trigger changed on zero of them, yet dollars differ by
  $1153. Sum R is 39.587 vs 39.583. The arm sizes 0.01 lots where the baseline
  sized 0.02 because it lost money earlier. All arms above are therefore
  reported in R as well, and the verdict holds in R. Future campaigns should
  report `sumR` next to net $. Recorded as CLAUDE.md S13.15.
- **The attribution trap fired a fourth and fifth time.** W2 ALL_SESSIONS
  attributes `VP_ASIA` at +$179.40 PF 1.11 while the ASIA_ONLY arm isolating
  those hours books -$157.77 PF 0.89; the same arm attributes `POC` at
  +$1434.24 PF 1.68 in W2 against -$356.33 PF 0.50 in W1.
- **S13.9 sample drift is material here.** `LOT_FLOOR` 12 -> 48 / 976 / 54 in
  W1 and 872 -> 1782 / 1950 / 75 in W2. W1 ALL_SESSIONS at 976 vs 12 is not an
  A/B comparison; the verdict rests on ASIA_ONLY and VP-only.

**Verified**
- 328 tests pass (`py -3.14 -E -m unittest discover -s tests`); compileall clean.
- The W1 baseline reproduces `logs/bt_baseline.json` on every decision field —
  281 trades, +$2757.57, identical trade list, identical rejection counts. The
  only diff is the six telemetry columns (`session`, `matched_triggers`,
  `vp_level`, `vp_level_source`, `vp_sweep_depth_atr`, `vp_confluences`) that
  did not exist when that file was written.

**Docs**
- `docs/RESEARCH_NOTES.md` S18.3-S18.4b rewritten; the void figures are deleted
  rather than annotated, so nothing quotes the buggy detector any more.
- `docs/REMEDY_LEDGER.md` L-014 closed (was OPEN pending this re-run).
- `CLAUDE.md` S13.15 added.

**No code changed.** The arms are measurements; the shipped default was already
`VPLR_ENABLED = False`.

---

## 2026-08-26 (3) — `Whole_day` window measured and rejected; Asia rationale corrected

**Researched** (no behaviour change — the shipped default is confirmed)
- Question: would enabling the `Whole_day` 00:00-23:00 catch-all help or hurt?
  It had been off since the S13.6 fix on a donor-campaign rationale, but had
  never been measured on the current M15/M5 + 2R stack.
- Six paired backtest arms with `--allow-whole-day` as the only variable
  (`logs/wd_*.json`), across two disjoint multi-month windows, two risk levels
  and two symbol sets. **Kill-zone gating is fold-CONSISTENT in 6 of 6;
  whole-day is fold-UNSTABLE in 6 of 6** — S13.5's standing rejection
  criterion. On XAUUSD 2026-05-15..08-23 it cuts net P&L 78% (+$2742 -> +$609)
  and takes drawdown 25.8% -> 41.2%.
- Mechanism established: **cannibalisation, not toxic hours.** The extra trades
  are ~breakeven standalone (224 trades, +$27.78, PF 1.01); the kill-zone book
  is what collapses, +$2742 -> +$581. `SYMBOL_DEDUP` 856 -> 3656 is the
  structural channel. Hour 00 serves as a natural control (nothing precedes it:
  77 -> 77 trades, P&L flat) while hour 06 goes +$330 -> -$263 and hour 12
  +$394 -> -$354. Disabling the cooldown and daily loss limit *widens* the gap
  to -$2963, so those controls were masking damage, not causing it.

**Corrected**
- `scalper/session_checker.py` and `CLAUDE.md` S13.6 both justified the default
  with the donor finding that "the Asia block carried 68-73% of trade volume
  and the largest absolute loss". **It does not reproduce on this stack.** Asia
  is 43.0% / 40.2% of volume in the two windows and carries the largest loss in
  neither; it is the only profitable block in the recent window, and
  `TOKYO_OPEN` is the best single session in the book (109 trades, 51.38% WR,
  +$1820, PF 1.60). The donor figure described unfiltered `SWEEP_REJECTION` on
  1R geometry. The conclusion survives; the rationale did not — and acting on
  the old rationale would point at cutting `TOKYO_OPEN`, the most expensive
  session to remove.

**Noted, not proposed**
- The $100 daily loss limit costs the kill-zone baseline ~$366 on the
  favourable window (+$3108 without vs +$2742 with). Single window, tail-risk
  parameter, inherits L-008 — a separate change needing its own fold test.

**Documented**
- `docs/RESEARCH_NOTES.md` S15 (full tables); `docs/REMEDY_LEDGER.md` L-010
  (hypothesis stated before the arms were run, then refuted).

**Verified**
- `py -3.14 -E -m unittest discover -s tests` — 241 tests, OK.
- `py -3.14 -E -m compileall -q .` — clean.
- Comment-only source edit; `allow_whole_day` default asserted still `False`
  and the default window set unchanged.

---

## 2026-08-27 — VP_LIQUIDITY_REACTION trigger (built, measured, ships off)

**Added**
- `scalper/anchored_vp.py` — causal swing-anchored H4 profile. Reuses
  `volume_profile.build_profile_auto` unchanged; only the WINDOW differs. The
  anchor walks confirmed pivots newest-first and takes the first leg clearing
  `VPLR_MIN_LEG_BARS` / `VPLR_MIN_LEG_ATR`, so a balancing top does not produce
  a four-bar "leg".
- `scalper/vp_liquidity_trigger.py` — the setup contract: VP/liquidity
  coincidence -> BSL/SSL raid -> reclaim or displacement -> M5 MSS/CHoCH ->
  confluence. Direction comes from the raid and the structure break, never from
  the level. POC bidirectional. No RSI, no RANGING requirement.
- `SAState.VP_ONLY` + `SASessionChecker.vp_window_open` — a trigger-scoped Asia
  allowance (00:00-06:30 UTC default). `SESSION_WINDOWS` is NOT modified and no
  existing trigger gains an hour.
- `tests/test_vp_liquidity_trigger.py` — 41 tests. Suite now **326, all passing**.

**Changed**
- `scalper/trigger_engine.py` — `VP_LIQUIDITY_REACTION` at the head of
  `ALL_TRIGGERS`; the relative order of the pre-existing five is unchanged so a
  disabled trigger reproduces the baseline. `step2_trigger` now records
  `matched_triggers` instead of discarding what the winner out-ranked.
  `resolve_enabled_triggers` decides membership for both processes.
- `scalper/short_term_bias.py` — three hard-coded trigger tuples replaced by
  named sets in `decision_params`. Existing membership is unchanged; the new
  trigger joins the two fade-shaped sets, which is what L-009 identified as the
  gate that silently forbids mean-reversion entries.
- `scalper/decision_params.py` — 24 `VPLR_*` constants, a Gate 1 whitelist
  registration, and `STB_RANGE_GUARD_TRIGGERS` / `STB_NEUTRAL_OK_TRIGGERS` /
  `STB_COUNTER_TREND_TRIGGERS`.
- `scalper_agent.py` / `backtest_scalper.py` — trigger mirrored into both
  decision paths in this change (invariant #2). New flags on both: `--vplr`,
  `--vplr-scope`, `--vplr-session-override`. Simulator trade records now carry
  session, matched triggers and the VP evidence chain.
- `tests/test_live_sim_parity.py` — seven new tests: constant identity, both
  paths evaluating the trigger, the Asia allowance mirrored, `_closed_tf` on the
  anchored H4 frame, and the disabled-set guarantee.

**Why it ships disabled**
Two disjoint windows, XAUUSD $1000 @ 3%. ALL_SESSIONS: -$2337 / +$4489 — sign
flips, and it displaces SWEEP_REJECTION 277 -> 23 taken trades. ASIA_ONLY:
+$252 / -$133 — sign flips, and the VP trades themselves lose in both windows
(PF 0.94 and 0.90). Full tables: docs/RESEARCH_NOTES.md §18, ledger L-014.

**Verified**
`py -3.14 -E -m unittest discover -s tests` — 326 tests, OK.
`py -3.14 -E -m compileall -q .` — clean.
Disabled mode reproduces the baseline trade list byte-for-byte: 281 trades,
+$2757.57, identical to `logs/bt_baseline.json`.

---

## 2026-08-26 (2) — VALUE_AREA_FADE entry model (built, inert, ships off)

**Added**
- `scalper/va_fade_trigger.py` — the operator's rule as a SIGNAL: sell a
  rejection at VAH, buy one at VAL, nothing at the POC. Confluence = 33%
  rejection wick + RSI(14) beyond 70/30 (art. 17781). `RangeLevels` carries the
  drawn range high/low and the nearest external liquidity, all derived from
  swing structure — no hard-coded price.
- `SATriggerEngine._check_value_area_fade`, `VALUE_AREA_FADE` in
  `ALL_TRIGGERS`, last in the first-match order so it cannot pre-empt a
  liquidity-anchored setup on the same bar.
- `VA_FADE_*` constants and a `VALUE_AREA_FADE` regime whitelist entry
  (ROTATION / MANIPULATION — EXPANSION excluded, that is where the profile goes
  bimodal). `--va-fade` on both processes, mirrored in the same change.
- Suite now **241 tests, all passing**.

**Measured — zero trades**
3 detections / 0 trades on 2026-05-15..08-23; 10 / 0 on 2025-08-01..2026-05-14.
`STB/opposes_short_term` rejected 3 of 3 and 9 of 10. That rule vetoes any
mean-reversion entry by construction: pushing into the VAH *is* a bullish
short-term read, and selling it is the trade. Detection is also rare — ~13
signals in 13 months.

**Consequence**
The operator's rule is neither confirmed nor refuted; it has never been allowed
to trade. §12's rejection covered the rule as a *filter* and does not transfer.
See RESEARCH_NOTES §14 and ledger L-009 for the two changes needed to make it
measurable.

**Verified**
`py -3.14 -E -m unittest discover -s tests` — 241 tests, OK.
`py -3.14 -E -m compileall -q .` — clean. All new switches default off.

---

## 2026-08-26 — TGA partial close was dead code since the 2R migration

**Reported:** "TGA is not working as intended, not closing partial positions."

**Root cause.** `_partial_close_mt5` has exactly one caller — the TP-extension
block in `_process_position` — and that block became unreachable when TP1 moved
1.0R → 2.0R (§13.1). `early_close_armed` flips at peak 1.0R and the early-close
branch `return`s before the TP-proximity check is evaluated. Under the old
geometry the two never raced: every `TP_EXTEND` in `logs/tga_action_log.json`
fired at peak **0.71R–0.94R**, i.e. the 0.5×ATR band around a 1R target sat
*below* the arming threshold. At 2R the band sits near 1.8R and early close
always wins. Action-log counts: **13 TP_EXTEND in May 2026, 0 in August**, with
4 EARLY_CLOSE and 1 NO_PROGRESS_CLOSE in their place. Nothing logged a skip, so
the feature disappeared silently.

**Fixed**
- Early close now yields to the TP-extension stage, and only there: the
  suppression requires price inside the 0.5×ATR band **and** confirmed
  momentum. A stalling trade inside the band still gets closed. The band/
  momentum answer is computed once per tick and shared by both stages.
- `_partial_close_mt5` floors the close volume to a whole broker step and
  rejects `close_vol >= rec.volume`. `round(0.01 * 0.50, 2)` is `0.01`, and
  volume_min/step are both 0.01 — so a "partial" close took **100%** of a
  minimum-lot position. 2 of 10 August trades were 0.01 lots.
- `_partial_close_mt5` returns `bool` and logs broker rejections. It returned
  `None` on every path and logged nothing on a bad retcode, while the caller
  latched `tp_extended` and wrote a `TP_EXTEND` action row regardless — a
  journal entry for a close that never happened, same class as L-004.
- The extension is now conditional on the partial filling. Extending TP with
  nothing banked is strictly more risk than leaving it. An unsplittable
  position sets `tp_extend_blocked` so the 2s poll stops retrying.
- TP extension writes the SL trailed **this tick**, not the pre-trail `p.sl`
  snapshot. A stage-2/3 trail followed by an extension reverted the stop to
  its looser value.

**Added**
- `tests/test_tga_partial_close.py` — 9 tests. Reachability under 2R geometry,
  narrowness of the suppression (outside the band; inside without momentum),
  the 100%-close guard, the split arithmetic, rejection handling, and SL
  preservation. 7 of the 9 failed against the pre-fix code. Suite now
  **241, all passing**.
- First unit coverage of `trade_guardian_agent.py`. There was none, which is
  why a whole stage could die unnoticed. The simulator models no trailing at
  all (§13.10), so unit tests are the only check on Guardian behaviour.

**Log hygiene.** Writing those tests exposed that importing
`trade_guardian_agent` runs `logging.basicConfig` on the ROOT logger, so a test
run emits ticket-1 records into `logs/tga_log.txt` and `logs/scalper_agent.log`.
34 + 129 fabricated lines from this session's runs were removed after verifying
`SA Cycle` numbering stayed contiguous (#2342 → #2343 → #2346) across every
excision. The new test module contains its own logger; historical residue from
earlier sessions and the other test modules' writes are tracked separately.

**Not done.** Whether suppressing early close in the TP band is *profitable* is
unmeasured, and per §13.10 it cannot be measured on the current simulator,
which models no trailing. This change restores documented intent and fixes
three defects; it is not a fold-tested promotion under §13.5.

**Verified:** `py -3.14 -E -m unittest discover -s tests` → 241 passing.
`py -3.14 -E -m compileall -q .` clean. Scalper + TGA relaunched.

---

## 2026-08-26 — regime-direction gate (REJECTED); baseline window audit opened

**Added**
- `scalper/regime_direction_gate.py` — veto a trigger that fades a classified
  H1 trend. Modes SYMMETRIC / COUNTER_TREND_LONGS. Ships **off**.
- `tests/test_regime_direction_gate.py` — 14 tests incl. both directions, both
  modes, abstentions, and parity. Suite now **232, all passing**.
- `RD_*` constants in `scalper/decision_params.py`, imported by both processes.
- `--rd-gate` / `--rd-gate-mode` on `scalper_agent.py` and
  `backtest_scalper.py`, mirrored in the same change (invariant #2).

**Measured**
Ledger L-006 **rejected**. In-sample the two modes disagree in sign
(+$294 / -$274 on n=8-derived hypothesis); out of sample on 622 trades the
effect is +$32.29 total, and on USOIL the gate never fires ($0.00 delta).

**Opened — L-008, and it is the important one**
Running the earlier window to test L-006 produced: **XAUUSD 2025-08-01 ..
2026-05-14, 622 trades, 35.53% WR, -$650.66, negative in all three folds** —
against +$2742.35 / 46.07% on 2026-05-15..08-23. The nine months before the
baseline window lose money consistently on a 2.2x larger sample. §13.5 was
being applied *within* one 100-day window, so every gate conclusion in
RESEARCH_NOTES §§9-13 is conditional on that regime. See §13.2 for the audit
that has to happen before further gate work is worthwhile.

**Verified**
`py -3.14 -E -m unittest discover -s tests` — 232 tests, OK.
`py -3.14 -E -m compileall -q .` — clean.
Live behaviour unchanged: both new gates default off.

---

## 2026-08-25 19:05 UTC — H4 volume profile, regime classifier, VP location gate (REJECTED, ships off)

**Added**
- `scalper/volume_profile.py` — POC / VAH / VAL. Value-area expansion
  transcribed from MQL5 art. 23169, including its `vol_above >= vol_below`
  upward tie-break. Bar-based volume attribution (the article walks ticks); the
  divergence is documented in the module.
- `scalper/regime_classifier.py` — deterministic trending/ranging read from
  lag-1 return autocorrelation (art. 17737 defaults: lookback 100, smoothing
  10, trend 0.2, volatility 1.5) plus the Kaufman efficiency ratio. `combine`
  selects ANY/BOTH.
- `scalper/hmm_backend.py` — optional `hmmlearn` backend, off by default.
  Seeded, restart-best, and mapped to regimes through fitted emission
  parameters so label switching cannot flip the meaning between fits.
- `scalper/vp_gate.py` — the location gate. Modes RANGING_ONLY / ALWAYS /
  POC_ONLY / POC_REQUIRE.
- `tests/test_volume_profile.py` — 41 tests. Suite is now **218, all passing**.
- `backtest_scalper._closed_tf` — duration-aware closed-bar slice.

**Changed**
- `scalper/decision_params.py` — 20 `VP_*` constants, imported by both
  processes (invariant #2). `VP_GATE_ENABLED = False`.
- `scalper_agent.py` / `backtest_scalper.py` — gate mirrored into both decision
  paths in this change, per invariant #2. New flags on both: `--vp-gate`,
  `--vp-gate-mode`, `--vp-poc-band`.
- `tests/test_live_sim_parity.py` — five new tests asserting *identity* of
  every VP constant across both processes, that the gate appears in both
  decision paths, and that the profile frame uses `_closed_tf`.
- `analyze_walkforward.py` — **bug fix.** It read `cfg['spread_pips']`, which
  the simulator stopped writing when per-bar spread landed (§13.4); every
  result file written since raised `KeyError` and the fold analysis could not
  run at all. Now prefers `spread_pips_observed`.

**Why it ships disabled**
Walk-forward XAUUSD 2026-05-15 -> 08-23, $1000 @ 3%, eight arms. Baseline
+$2742.35 / 280 trades / 46.07% WR, positive in all three folds. Every gated
arm is worse; the two applying the full buy-low/sell-high rule flip sign across
folds. Attribution shows the rule is inverted — AT_POC is the *best* location
bucket (PF 2.10) and AT_VAL the only negative one. Full tables:
docs/RESEARCH_NOTES.md §12. Ledger L-005 (rejected), L-006 (open lead:
longs into H1 downtrends, n=8), L-007 (partial: legacy H1/H4 sim slices).

**Verified**
`py -3.14 -E -m unittest discover -s tests` — 218 tests, OK.
`py -3.14 -E -m compileall -q .` — clean.
Live behaviour unchanged: the gate defaults off, so the running agent's
decision path is byte-for-byte what it was before this change.

---

## 2026-08-25 10:32 UTC — A close the broker has not priced is no longer booked

No strategy default changed. No trigger, gate, target, session window or risk
percentage moved. This is accounting plumbing: it changes when the books are
written, never which trades are taken.

### The problem

`_on_trade_closed` is the single funnel through which a closed trade updates
the pool ledger, the trade log, the cooldown, the AdaptiveMemory journal and
the forensic queue — and it requires a concrete P&L. So all three call sites,
when `_settled_pnl` returned `None` because the closing deal had not landed in
deal history yet, **fabricated one** (`0.0`, or the floating `pos.profit`) and
then derived the WIN/LOSS label from the fabrication.

One fabricated `$0.00` was never neutral. Every consequence below is in the
code it calls, not a hypothesis:

- `pool.register_close(0.0)` leaves the pool balance wrong — which sizes every
  later trade — and **resets** the consecutive-loss counter, because `0.0 < 0`
  is False.
- `cooldown.record_trade_result(0.0)` **arms the LOSS cooldown**, because
  `0.0 > 0` is False. Up to an hour of held trading on a trade that may have
  won.
- The trade log recorded `LOSS` at $0.00 — and `SATradeLogger.log_close` only
  wrote into a record whose `result` was still `OPEN`, so that verdict was
  **permanent**. No later reconciliation could land.
- The phantom then entered the forensics and the journal SEE reads.

A second, quieter leak in the same function: a close with no matching open
record was dropped with nothing but a warning. Every position adopted after a
restart is in that state — `_adopt_open_positions_from_mt5` repopulates
`_open_trades` but never calls `log_open` — so the pool booked trades the log
had no row for.

### What the evidence actually showed

Worth recording, because the brief and the data disagreed. The 140-of-282 $0
bookings are **all from May 2026**; the single August close booked correctly.
The `booking $0.00` warning appears **zero times** in either `scalper_agent.log`
or its rotated predecessor. So the May epidemic was not produced by the current
settlement path — it came from the earlier `history_deals_get(ticket=N)` defect
already documented in `_position_deals`, which filters by *deal* ticket and
matched nothing. That is fixed.

What remains is the same defect in latent form: the fabrication path is live,
has simply not been hit since the August rewrite, and would fire on the first
settlement lag. It was reproduced directly against the fake broker before any
code changed.

### The fix

The rule `_close_at_market` already enforces on the way out — *what the broker
has not confirmed is not booked* — now governs the P&L as well.

- **`_book_settled_close()`** is the one place a vanished position is booked.
  Settled P&L books immediately at its true value. Unsettled does **not** book:
  the ticket stays in `_open_trades` and the next 30s cycle retries it. All
  three former fabrication sites route through it.
- **`PNL_RECONCILE_GRACE_MIN = 10`** bounds the wait, so a ticket that never
  settles cannot hold a position slot for the life of the process. Past the
  grace the trade books as **`OUTCOME_UNRECONCILED`** — deliberately outside
  the `WIN*`/`LOSS`/`TIMEOUT` vocabulary that `postmortem.classify` and
  `analyze_incidents` switch on, so "we do not know" can no longer be spelled
  "LOSS at $0.00".
- **`_on_trade_closed(..., settled=False)`** quarantines that case. The ledger
  and trade log still see it — the position slot has to be released and an
  operator has to be able to find the trade — but the journal and the forensic
  queue are skipped, so a placeholder cannot become a false finding. The
  cooldown *is* armed: an unpriced close is the one case where pausing is the
  conservative reading.
- **`_force_close_all(..., force_settle=True)`** at the midnight reset, where
  there is no next cycle to carry an unbooked trade into. The 23:00 EOD sweep
  defers normally.
- **`SATradeLogger.log_close` is now correctable.** It prefers the `OPEN`
  record, falls back to the most recent row for that ticket, and writes a
  close-only row when there is none at all. A correction logs
  `SA Trade CORRECTED | was X -> now Y` rather than appending a duplicate.

### Verification

`py -3.14 -E -m unittest discover -s tests` — **172 tests, clean** (159 before;
13 added, each confirmed failing against the old code first).
`py -3.14 -E -m compileall -q .` — clean.

New coverage in `tests/test_close_paths.py`: an unsettled close books nothing
and stays tracked; the retry books the real P&L with a `WIN` label once history
lands; give-up is labelled `UNRECONCILED` and releases the slot; `EOD_CLOSE`
defers where `EOD_RESET` forces; an unreconciled close reaches the ledger and
log but not the journal or forensics; and four cases on the logger — correcting
an already-booked record in place, recording a close with no open record, and
not touching a neighbouring ticket. Two more pin that a deferred `TIMEOUT`
or `EOD_CLOSE` keeps its label when the retry books it — without that, the
retry path passes no outcome and would relabel the trade WIN/LOSS, merging a
forced-exit profile into the stop profile that §13.3 requires to be measured
separately.

### Residual, stated plainly

A trade booked `UNRECONCILED` leaves the pool balance understated by its true
P&L, and nothing re-reads deal history for it afterwards. `log_close` can now
accept the correction, but no live sweep issues one — that remains the analysis
path (`analyze_incidents.py --backfill`). Closing it properly needs a
correction-delta API on `SACapitalPool`, which is a change to the capital
ledger and wants its own fold-tested entry. Ledger L-004 updated.

---

## 2026-08-25 10:15 UTC — The scalper can now say what it got wrong

No strategy default changed. No trigger, gate, target, session window or risk
percentage moved. Every component added here is observation-only by
construction, and the test suite now enforces that property.

### The problem

Every exit was recorded as `WIN_TP1`, `LOSS` or `TIMEOUT` plus a dollar figure.
That is not enough information to fix anything. Three trades that all book
`LOSS -$30` can be three unrelated defects — a stop clipped by a wick before
price ran to target, a signal that never went onside, a winner given back from
+1.8R — with three different remedies. Averaged together, all three are hidden.
"Why didn't it trade?" was likewise a grep of a rotating log.

### What was added

- **`scalper/postmortem.py`** — replays each closed trade against its
  confirmation bars, measures MFE/MAE in R, looks a **bounded** 24 M5 bars past
  the exit to test whether the target printed anyway, and assigns one label
  from a fixed taxonomy. The look-ahead is why the pass is **deferred** two
  hours: at the instant of close those bars do not exist, so running it
  immediately would report `tp1_after_exit=False` every time and silently
  under-count `STOP_TOO_TIGHT`. `evidence.lookahead_available` records how much
  of the budget actually existed, so a pass forced early at shutdown is
  identifiable rather than quietly wrong.
- **`scalper/reject_log.py`** — per-day, per-symbol, per-stage veto counts
  across all 17 rejection sites. Makes §13.9's standing requirement to report
  `LOT_FLOOR` counts a field rather than a grep.
- **`analyze_incidents.py`** — ranks failure modes by cost with sample sizes
  attached; `--backfill` reconstructs history from the broker.
- **`backtest_scalper.py --incidents`** — the simulator emits incidents through
  the *same* `postmortem.analyse` the live agent calls. This matters because
  the shipping config has one live trade; the simulator is the only source of
  closed trades at volume under current rules.
- **`docs/REMEDY_KB.md`**, **`docs/REMEDY_LEDGER.md`**, and the
  **`apex-postmortem`** skill — researched remedies with graded evidence, and
  an append-only promotion gate.

### The measurement chain was lying, and it inverted the first finding

`logs/scalper_log.json` booked **140 of 282** closed trades at **$0**.
Reconciling against `history_deals_get` priced 187 of them and **131 disagreed
with the log**. Corrected, the live book is **+$947.93 / 52.78% WR / PF 1.48**,
not the +$61.80 / 27.66% / 1.09 the log reports.

This was not a cosmetic error. `_on_trade_closed` derives the win/loss label
from the P&L it is handed, so a $0 booking becomes a `LOSS` by construction —
and a profitable trade that peaked at 1.2R then classifies as
`GAVE_BACK_WINNER`. The first diagnosis run produced a confident headline of
"61 given-back winners, 34.8% of trades destroyed by the exit". After
reconciliation that mode is **15 trades**. The pipeline had invented an exit
defect that did not exist. `--backfill` now takes P&L *and* the label from the
broker. Ledger L-004.

**The underlying booking defect in the live agent is not fixed** — only the
analysis path routes around it.

### What the corrected data says

Shipping config, XAUUSD 2026-05-15 → 08-23, 280 simulated trades, reproducing
the documented baseline exactly (+$2,742.35, PF 1.35):

| Mode | n | Total $ | Mean MFE | Mean MAE |
|---|--:|--:|--:|--:|
| `LOSS_ORDINARY` | 58 | −3,261.36 | 0.58R | 1.32R |
| `SIGNAL_FALSE` | 43 | −2,209.84 | **0.17R** | 1.33R |
| `GAVE_BACK_WINNER` | 29 | −1,571.92 | **1.43R** | 1.27R |
| `STOP_TOO_TIGHT` | 6 | −359.27 | 0.28R | 1.09R |
| `TIMEOUT_CHOPPED` | 46 | **+451.23** | 0.88R | 0.54R |
| `TIMEOUT_NEAR_MISS` | 11 | **+482.49** | 1.77R | 0.59R |

Three things follow. `LOSS_ORDINARY` is the largest line and is **not a
defect** — a 46% win rate at 2R is supposed to produce those. `SIGNAL_FALSE`
at mean MFE 0.17R is the largest real defect, and
`SATriggerEngine._check_sweep_rejection` currently has **no wick-ratio
requirement**, so a shallow tag with a marginally-directional close is admitted
on the same terms as a violent rejection. And **the timeout profile is now net
positive** — the measurement §13.3 demanded, come back clean.

### Live/simulator exit divergence — open

**36 of 115 reconciled live losses (31.3%) closed without price ever reaching
the original stop**, median MAE 0.54R, cost −$89.29. `tga_action_log.json`
shows 20 of the 36 carry a Guardian modification, dominated by
`Trailing SL Stage 1` (111 of 175 actions — the breakeven move at peak ≥0.5R).
`EARLY_CLOSE` fired 11 times, so this is the breakeven trail, not the
early-close engine. The remaining 16 are unexplained.

The dollar cost is small; the structural point is not. The simulator holds
SL/TP1 only and models no trailing at all, so the two processes exit by
different rules — which means **no exit-side remedy is measurable yet**.
Ledger L-003.

### Verification

```
py -3.14 -E -m unittest discover -s tests   # 159 tests (was 113), all pass
py -3.14 -E -m compileall -q .              # clean
```

46 new tests. `tests/test_forensics_parity.py` pins the two properties that
keep this safe: the simulator and the agent call the *same* `analyse()` rather
than two copies of one idea, and the telemetry cannot acquire decision
authority — no permission-shaped field on the record, no branch on a counter
inside `_scan_symbol`.

---

## 2026-08-24 04:20 UTC — Forced closes were fire-and-forget; the ledger believed them

Findings from the 2026-08-23 → 24 live observation run (scalper PID 19944,
Guardian PID 24892, demo 40280210). No strategy defaults were changed: no
trigger, gate, target, session window or risk percentage moved. Everything
here is a correctness or observability repair on paths the simulator cannot
reach.

**P1 — a rejected close was booked as a completed trade.**
`_close_at_market` discarded the `order_send` result entirely, and both callers
— the 6h timeout sweep and the 23:00 EOD sweep — then called
`_on_trade_closed` and popped the ticket from `_open_trades` regardless.
`_on_trade_closed` is the single point that writes the pool ledger, the trade
log, the journal and the cooldown, so a broker rejection wrote a fabricated
P&L, armed a cooldown, and deleted the only in-process record of a position
that was still open. Once dropped it was invisible for the rest of the process
lifetime: no timeout, no EOD, and `pool._open_positions` decremented so
`can_open_position` would permit a third real position.

The realistic trigger is Friday EOD. The sweep fires on `now.hour >= 23`, but
gold stops trading before that on a Friday, so the close returns
`TRADE_RETCODE_MARKET_CLOSED` and the position carries the weekend untracked.

`_close_at_market` now returns a bool, checks the retcode, and re-reads
`positions_get` to confirm the position is actually gone (a DONE retcode does
not rule out a partial IOC fill). Failure leaves the ticket tracked; the next
cycle retries, because the condition that triggered the close still holds.

**P1 — P&L was read back before the closing deal existed.**
`self._get_closed_pnl(t) or pos.profit` ran the instant the close was fired.
Deal history lags `order_send`, so only the ENTRY deal was visible: either it
summed to 0.0 and the `or` booked the *floating* P&L of a possibly-still-open
position, or the entry commission made it non-zero, the fallback was bypassed,
and a commission-sized "loss" was booked — arming a loss cooldown and
advancing the streak toward the SA-CRG pause. Added `_settled_pnl`, which
polls for the `DEAL_ENTRY_OUT` leg and returns `None` rather than a
plausible-looking zero. `_get_closed_pnl` now returns `Optional[float]` so no
caller can write `or` over a genuinely flat trade again.

**P1 — `mt5.Close(ticket)` has never been a valid call.**
`scalper/daily_reset.py` line 57. The installed MetaTrader5 (5.0.5735)
signature is `Close(symbol, *, comment=None, ticket=None)`, so the ticket
landed in `symbol`; verified directly, `mt5.Close(12345)` raises `TypeError`.
`Close` also returns a bool, so the following `result.retcode` would have
raised `AttributeError` even with the arguments the right way round. The bare
`except Exception` swallowed both and `_perform_daily_reset` cleared
`_open_trades` regardless — the designated last-resort midnight net could
never catch anything, and never had. `execute()` now takes the agent's own
verified closer so there is one close implementation in the system, and
`_perform_daily_reset` closes-and-books through it *before* the day's ledger is
snapshotted (the reset module never routed its closes through
`_on_trade_closed` at all) and forgets only broker-confirmed tickets.

**P2 — the news fetcher was hammered once per cycle.** The failure path never
stamped `_last_news_fetch`, so the 6-hour throttle never engaged: 643 download
attempts and ~1900 log lines in the observed session. Added a 15-minute retry
backoff on `_last_news_attempt`, and a loud warning when the calendar on disk
is older than 24h — the blackout gate is blind at that point. It warns rather
than blocks: a refuse-to-trade rule would be a new gate in the live decision
path and would have to be mirrored into the simulator (invariant #2), which is
a separate measurable change, not a bug fix. The observed `WinError 10061` was
environmental, not repo code — the feed fetches fine from a normal shell
(66 events, no proxy configured).

**P2 — logging.** Both agents attach `StreamHandler(sys.stdout)`; redirected
stdout on Windows is cp1252, so the Guardian's shield emoji raised
`UnicodeEncodeError` inside every `emit`. Logging swallows handler errors so
the daemon survived, but stderr filled with tracebacks. Both now reconfigure
stdout to UTF-8 with `errors="replace"`. Both also stamp **UTC** now: every
gate in this system is UTC and the 00:00 UTC daily reset was being logged as
`05:00`, putting the reader five hours from the event they were reading about.

**P2 — `--pool` was silently overridden.** `_restore_pool_from_mt5` set
`current_pool = initial_pool + cumulative_pnl` over all magic-88880 deals since
the inception stamp in `data/sa_pool_state.json`. The audit run asked for
`--pool 1000 --risk 0.03` and sized at **$423.77 / $12.71 per trade — 42% of
the requested risk unit**. Per §13.9 that is not a smaller version of the same
run: a smaller budget takes more `LOT_FLOOR` rejections and therefore samples a
different, degraded set of signals. Note also that `backtest_scalper.py` starts
at `balance = sa_pool` with no restore, so RESUME-live vs sim was a live/sim
divergence on the sizing input itself.

Added `--pool-mode {RESUME,FRESH}`, default `RESUME` (unchanged behaviour), and
a loud warning in both modes whenever the live pool differs from the requested
allocation by more than 1%. `FRESH` sizes at exactly `--pool` and matches what
the simulator does. Daily counters are restored in both modes — they feed the
daily-loss shutdown, and dropping them would let a restart clear a breached
limit.

**P2 — a stale `.env` password logs the terminal out.** Both agents called
`mt5.initialize(login=..., password=...)` unconditionally. A failed
credentialed initialize does not merely fail; it signs the running terminal
out, and nothing in this codebase can sign it back in — which is how one bad
password ended an audit session and needed the MT5 GUI to recover. Both now
attach to an already-authenticated session first and fall back to credentials
only when the terminal is signed out or on the wrong account.

**P3 — no single-instance lock on the scalper.** The Guardian holds
127.0.0.1:55555; the scalper held nothing. Two instances would both trade magic
88880 and both restore their pool from the same shared deal history,
double-counting every close. The duplicate same-date rows in
`logs/sa_daily_journal.json` (2026-05-07, 05-10, 05-13) are consistent with
that having already happened. The scalper now holds 127.0.0.1:55556 and exits
if it is taken.

Verified: `py -3.14 -E -m unittest discover -s tests` — **113 tests, OK** (99
existing plus 14 new in `tests/test_close_paths.py`, which had no coverage of
any close path at all). `py -3.14 -E -m compileall -q .` clean. The new tests
redirect `DAILY_JOURNAL_PATH` to a temp file; an earlier unisolated run
appended two rows to the real journal and they were removed.

---

## 2026-08-24 02:50 UTC — The interpreter path in the docs was dead

No code changed. A handoff run stopped at the preflight gate with
`ModuleNotFoundError: No module named 'dotenv'` across `test_backtest_fidelity`
and `test_live_sim_parity`, and reported the environment as dependency-
incomplete. It was not. The docs were.

`CLAUDE.md` §14 and `AGENTS.md` both named
`D:\Hermes Quant\GPTMain\.venv\Scripts\python.exe` as the interpreter with
the trading dependencies. The entire GPTMain tree was absent from disk at the
time — the owner had removed it, and restored it the same day; its venv runs
this repo's suite clean (3.12.13, 99 tests OK). With that path gone, the
runner fell back to bare `python`, which on
this machine resolves to the Hermes agent venv
(`...\AppData\Local\hermes\hermes-agentenv`) — dotenv present, pandas and
MetaTrader5 absent. The import failure was an interpreter mistake wearing the
costume of repo breakage.

The correct interpreter is the system CPython 3.14,
`...\AppData\Local\Python\pythoncore-3.14-64\python.exe`, reachable as
`py -3.14`. It carries pandas, MetaTrader5, dotenv, numpy, rich, scipy and
colorama. Confirmed on this machine:

- `py -3.14 -m unittest discover -s tests` -> **99 tests, OK**
- `py -3.14 -m compileall -q .` -> clean

Nothing was installed, no venv was created, `requirements.txt` is untouched.

**Fixed:** the interpreter paragraph in `CLAUDE.md` and `AGENTS.md` now names
`py -3.14` as the default, records the GPTMain venv as a verified alternate,
and warns that bare `python` resolves to an incomplete environment.
`.claude/skills/apex-ops/SKILL.md` names the interpreter in its verification
commands, and its donor-repo entry records the removal and restore with an
instruction to confirm the tree is present before citing it. The stale test
count in `CLAUDE.md` §14 (96) is now 99.

The lesson survives the restore: a load-bearing doc must not make a required
step depend on an absolute path into a second repository that nothing in this
one controls. `py -3.14` is now the default for that reason, not because the
donor venv is deficient — it is not.


### Addendum, 03:05 UTC — the second stop was `PYTHONPATH`, and `-E` is the fix

The handoff run stopped again, one gate later. `py -3.14` resolved correctly,
but the agent's own process environment exports:

```
PYTHONPATH=...\AppData\Local\hermes\hermes-agent;
           ...\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages
```

That prepends a CPython **3.11** site-packages to a CPython **3.14** run.
`pandas` resolved from 3.14, `numpy` from 3.11, and the cp311 extension module
could not load: `No module named 'numpy._core._multiarray_umath'`. The runner
read the mission's "report rather than work around it" rule as covering this
and stopped rather than clearing it.

Reproduced here by setting the same `PYTHONPATH`, then fixed with `-E`, which
makes the interpreter ignore `PYTHON*` variables:

| | without `-E` | with `-E` |
|---|---|---|
| `import numpy` | cp311 ImportError | numpy 2.4.4, pandas 3.0.2 |
| `unittest discover -s tests` | not reached | **99 tests, OK** |
| `compileall -q .` | not reached | clean |
| `scalper_agent.py --help` | not reached | OK |

Every command in `HERMES_LIVE_RUN_PROMPT.md` now carries `-E`, the preflight
opens with an explicit `import numpy, pandas, MetaTrader5, dotenv` line so a
dropped `-E` fails loudly and early, and the stop rule is scoped: it governs
the repository and the strategy, never the launcher environment. Choosing an
interpreter or sanitising `PYTHONPATH` is the runner's call to make and note,
not a reason to abort.

No repository code changed.

---

## 2026-08-23 21:40 UTC — Live/simulator parity, and the H1 EMA(18) band

Two changes. The parity work is the load-bearing one: the EMA result is only
trustworthy because the two processes now run the same code.

### Live and simulator were running different strategies

Invariant #2 was being enforced by comments asking each editor to keep two
copies of every constant in step. Four had already drifted, and every
comparison between a live result and a simulated one was invalid as a result:

| | live | simulator |
|---|---|---|
| consultation timeframe | **M5**, 100 bars | **M15**, unbounded |
| trigger window | 150 bars | entire history to date |
| confirmation window | 150 bars | 200 bars |
| forming bar | included | excluded |
| spread | measured per tick | fixed 2.5 pips |

The first is the worst. `sa_consultant._run_consultation` still hard-coded
`TIMEFRAME_M5` after the stack moved to M15/M5, while `backtest_scalper`
carried its own re-implementation on M15. Gate 1 is a regime whitelist, so
both processes applied the same whitelist to regimes classified on different
timeframes.

The spread default was the most directly quantifiable: XAUUSD averaged **4.78
pips** over 2026-05-15 -> 08-23 (median 5.0, p90 6.0) against an assumed 2.5.
Spread enters twice — as cost, and inside `step3_validate`'s net-R test — so
the simulator was both under-charging trades and admitting setups the live
agent would reject.

**Fixed:**
- `scalper/decision_params.py` — new; one definition of every shared decision
  value. Both processes import it; neither restates it.
- `scalper/sa_consultant.py` — `analyse()` and `apply_lia_override()` extracted
  as pure module functions. The simulator imports them instead of carrying a
  parallel implementation; `_apply_v1_council_gates` now delegates.
- Consultation runs on `DP.TF_TRIGGER`, not a hard-coded M5.
- Every decision frame excludes the forming bar in both processes —
  `copy_rates_from_pos(..., 1, bars)` live, `time < now` in the simulator.
  This is MQL5's shift=1 convention and it also makes live entries
  reproducible: a trigger detected on a forming bar can vanish before that bar
  closes.
- The simulator reads the broker's own per-bar spread from the MT5 rates feed
  rather than a CLI constant. `--spread-pips` is now only a fallback for bars
  where the field is zero.
- Backtest windows sliced to `TRIGGER_BARS` / `CONFIRM_BARS`, and a
  45-calendar-day lead-in added so the long-lookback gates are seeded on the
  first tradable bar instead of failing through the opening weeks.

### H1 EMA(18) high/low band — implemented, measured, defaulted off

`scalper/ema_filter.py`. EMA(18) on the H1 high series and EMA(18) on the H1
low series; longs only above both, shorts only below both, nothing inside.
The EMA matches MetaTrader's `iMA` (SMA seed, alpha 2/(n+1)) so printed values
agree with the chart; verified seed-independent to 1e-6 over 200 bars.

Walk-forward on XAUUSD 2026-05-15 -> 08-23, 60/20/20, band the only difference
between arms:

| Risk | off | TREND (requested) | FADE (inverted) |
|---|---|---|---|
| 3% | +2742, PF 1.35, **all folds +** | +230, PF 1.06, F3 − | −339, PF 0.87, all − |
| 2% | +1617, PF 1.36, **all folds +** | +281, PF 1.11, F2/F3 − | −244, PF 0.87 |

The band removes 197 trades with PF **1.52** and keeps 83 with PF **0.94** —
it strips out the better half, and drawdown rises from 25.8% to 32.4%. The
cause is structural: 276 of 280 trades are `SWEEP_REJECTION`, a counter-trend
fade, and a trend band vetoes a fade by construction. Inverting the mapping is
worse in every fold, which rules out a sign error.

Default **off** (`EMA_BAND_ENABLED = False`); `--ema-band` on either process
enables it, `--ema-band-mode {TREND,FADE}` selects the mapping. Kept rather
than deleted because the failure is specific to a sweep-fade mix — it would
need re-measuring against a continuation-heavy one. Full table and citations:
`docs/RESEARCH_NOTES.md` §11.

### Verification

96 tests (was 58), `python -m compileall -q .` clean. New:
`tests/test_ema_band.py` (17) pins the band rule and the EMA arithmetic;
`tests/test_live_sim_parity.py` (21) asserts *identity* with
`decision_params` rather than equality with literals, so a future divergence
fails the suite instead of quietly changing what the backtest measures.

## 2026-08-22 22:23 UTC — The lot floor is not a filter, it is a free look

The headline is a defect, not a strategy. Every negative walk-forward this
repository has recorded was run at a risk budget too small to afford the stop
the signal actually asked for, and the resulting `LOT_FLOOR` rejection costs
the agent nothing — no position slot, no cooldown — so it immediately re-scans
and takes a degraded re-entry into the same move. Those re-entries win 24.8%
of the time.

### The measurement

Same window (2026-05-15 -> 08-23), same instrument (XAUUSD), same gate chain,
only the risk budget changed:

| risk | trades | win rate | net $ | folds |
|---|---:|---:|---:|---|
| 1% of $1,000 ($10/trade) | 210 | 32.38% | -91.15 | `- - +` UNSTABLE |
| 3% of $1,000 ($30/trade) | 269 | 46.84% | +3,211.19 | `+ + +` CONSISTENT |

Partitioning the two trade sets by open timestamp isolates the cause:

| set | trades | win rate | exp R | median SL |
|---|---:|---:|---:|---:|
| taken by **both** runs | 69 | 47.8% | +0.413 | 750p |
| taken **only** at $10 risk | 141 | **24.8%** | **-0.249** | 713p |
| taken **only** at $30 risk | 200 | 46.5% | +0.151 | 1,573p |

The trades both runs agree on are healthy. The small budget adds 141 trades of
its own at a 24.8% win rate — and their median stop is 713 pips, the *same
width* as the shared set, so this is not a stop-width story. They are extra
entries taken while the correctly-sized run was holding a position or in
cooldown.

Ruled out on the way, each by its own run: stop-loss width (matched-bucket win
rates differ by 13 points at every width), the daily loss limit (removing it
entirely leaves 45.51% and +$3,108.15, all folds positive), the symbol list
(XAUUSD-only at $10 reproduces the three-symbol result), trade clustering
(median inter-trade gap 405min vs 375min), and this session's own code changes
(a control run at the prior configuration returns 197 trades / 32.99% /
-$65.30 against the recorded 198 / 32.83% / -$76.97).

### What this retracts

- **`CLAUDE.md` §12.4, "The Lot Floor Filter Discovery", is wrong.** The lot
  floor was documented as a beneficial volatility filter that "dramatically
  improves win rate". It does the opposite: it removes the affordable trade
  and leaves the agent free to take a worse one.
- **"London is bleeding" is a symptom of the same defect.** Session
  attribution at $10 risk shows LONDON at -$202.68 over 69 trades, 23.19% win
  rate, negative in all three folds. At $30 risk the same window shows LONDON
  at +$233.61. 74% of the $10 run's London trades were free-look re-entries
  the correctly-sized run never took, and they won 22.2%. London is the
  highest-volatility window, so it is where the budget most often could not
  afford the setup — the block bled *because* it was the most contaminated.
- **Asia was never specially good.** It had the *lowest* free-look share (46%
  against London's 74%), so it was simply the least contaminated block. It is
  positive in all three folds under both sizings, which still makes it the
  most robust block on the data, but the reason is now known.

### Changes in this release

- **`SATriggerEngine(enabled_triggers=...)`** — `step2_trigger` returns the
  first detector that fires, so SWEEP_REJECTION at the head of the priority
  order masked every trigger behind it. A whitelist makes one concept
  measurable in isolation. CLI `--triggers` on both the agent and the
  backtester (invariant #2).
- **`SASessionChecker(enabled_sessions=...)`** — same, for kill-zone windows.
  CLI `--sessions` on both.
- **Per-trigger rejection instrumentation** in `backtest_scalper.py`:
  `triggers_detected` counts what step 2 emitted before any post-detection
  gate, `rejections_by_trigger` attributes each downstream rejection to the
  trigger that produced it. A zero-trade result is now distinguishable from a
  never-fired detector.
- **`analyze_sessions.py`** — per-window and per-block P&L with fold signs and
  a drop-one-block counterfactual.
- **11 new tests** (58 total) pinning both whitelists: default-all, case
  handling, unknown-name and empty-set rejection, and the invalidation case
  that a disabled detector cannot fire.

### BOS_RETEST — re-tested, and the answer is "cannot be evaluated"

`docs/RESEARCH_NOTES.md` §7 records BOS_RETEST as already rejected. That
rejection is **not binding here**: it tested a detector that only ever
implemented the bullish branch (the bearish half was added 2026-08-22) on a
different timeframe stack. The re-test is legitimate, and the record should
show that.

Isolated via `--triggers BOS_RETEST` over the same window: **1,278 detections,
3 trades.** It fires constantly; the gate chain consumes it. 676 killed by the
short-term bias gate, 578 by the CONSUL Gate 1 regime whitelist (BOS_RETEST is
EXPANSION-only), 21 by thin liquidity. Three trades is not a sample, so no
verdict on its edge is available in either direction — the earlier "zero
trades" was a statement about the priority order and the whitelist, never
about the signal.

Verified: `python -m unittest discover -s tests` 58 passed; `compileall`
clean; control run reproduces the recorded pre-change result.

---

## 2026-08-22 22:05 UTC — Single-instrument config, pool and risk raised

Owner decision following the fresh-window walk-forward.

- **`config.json` `symbols` is now `["XAUUSD"]`.** XAGUSD and USOIL produced
  0 of 198 trades in the 2026-05-15 -> 08-21 run; at $10 of risk their pip
  values put every candidate lot under the 0.01 broker floor. `cross_assets`
  deliberately keeps all three — CAIA reads them for correlation and
  divergence, it does not trade them.
- **Scalper defaults: pool $1,000, risk 3%, daily loss limit $100, XAUUSD.**
  Previously $100 / 2% / $50 / three symbols. `backtest_scalper.py` defaults
  moved in step so the simulator and the agent stay on the same parameters.
- `.claude/skills/apex-ops/SKILL.md` launch and backtest commands updated;
  the analysis step now points at `analyze_walkforward.py`.

Note for the record: at $30 of risk per trade the lot floor no longer excludes
XAGUSD or USOIL, so dropping them is now a deliberate concentration choice
rather than a constraint. MQL5 art. 18991 puts the recommended risk fraction
at 1-2% and reserves anything above 2% for "a robust and proven system"; the
walk-forward immediately preceding this change was negative with unstable fold
signs. Recorded, not contested — the account is a demo and the sizing is the
owner's call.

Verified: `python -m unittest discover -s tests` 47 passed; `compileall` clean.

---

## 2026-08-22 21:45 UTC — Fresh-window walk-forward: SWEEP_REJECTION rejected again

First run of the M15/M5 stack against a window that had never been inspected
for this strategy shape: **2026-05-15 -> 2026-08-21** (99 days, XAUUSD /
XAGUSD / USOIL, $500 pool at 2% risk). Result is **negative and the folds are
sign-unstable**. Nothing is promoted.

### Result

| | trades | win rate | net $ | exp (R) | PF | maxDD |
|---|---:|---:|---:|---:|---:|---:|
| full period | 198 | 32.83% | **-76.97** | -0.0180 | 0.94 | 36.36% |
| development | 118 | 33.05% | -40.87 | -0.0104 | 0.95 | 32.52% |
| validation | 40 | 27.50% | -61.42 | -0.1793 | 0.74 | 17.80% |
| test | 40 | 37.50% | +25.32 | +0.1208 | 1.13 | 8.27% |

Sign by fold: `-` `-` `+` -> **UNSTABLE**, the standing rejection criterion.

Survival condition: a 32.83% win rate requires RRR > 2.05; realised RRR is
**1.99** (avg win +1.98R, avg loss -0.99R). Short by 0.06 — much closer than
the pre-migration configuration, but still the wrong side of break-even.

Costs are **not** the cause: gross -$70.07, costs $7.36. The shortfall is
signal quality, not friction.

### Expectancy per exit type

| exit | trades | share | exp (R) | book without it |
|---|---:|---:|---:|---:|
| TP | 64 | 32.32% | +1.9961 | -$1,175.20 |
| SL | 131 | 66.16% | -1.0039 | +$1,099.69 |
| TIMEOUT | 1 | 0.51% | -0.0881 | -$76.11 |
| EOD | 2 | 1.01% | +0.1374 | -$79.29 |

**The timeout exit profile has been designed out.** It was 23% of live trades
(67 of 287) and flagged as an unmeasured negative-expectancy bucket; on the
M15 stack with `TIMEOUT_BARS = 24` it is 0.5% of trades. 98.5% of trades now
resolve at the target or the stop. There is no longer a third exit profile
diluting the book — which also means there is nothing left to eliminate, and
the remaining loss is the entry signal itself.

### Two structural findings

- **This is not a three-symbol portfolio.** All 198 trades are XAUUSD;
  XAGUSD and USOIL produced **zero**. At $10 of risk their pip values (50x and
  10x XAUUSD's) put every candidate lot under the 0.01 broker floor — 676
  `LOT_FLOOR` rejections. The config advertises diversification the pool size
  cannot buy.
- **`SWEEP_REJECTION` is 196 of 198 trades and nets -$91.63 at PF 0.92.** The
  donor campaign already rejected it as negative in every fold across 7,709
  candidates. This reproduces that independently on a fresh window, a
  different timeframe stack, and full cost and gate fidelity. It is now the
  eighth rejection of the concept.

### Changed — backtester now runs the live decision path

Running the window first would have repeated the defect that invalidated the
donor repository's entire campaign, so the gate chain was reconciled against
`ScalperAgent._scan_symbol` before any result was taken as evidence:

- **Cooldown was entirely absent.** The flagship change of the 2026-08-22
  release was never mirrored; it now accounts for 462 rejections.
- **Per-symbol dedup was a no-op** — every entry appended `closed: True`, so
  the guard never fired and the simulator opened overlapping trades on one
  symbol. 279 rejections once fixed.
- **No EOD exit existed**, so the `EOD` bucket was unmeasurable by
  construction.
- **STB gate, thin-liquidity gate, SA-CRG and the daily loss limit** were all
  missing (676 / 233 / 51 rejections).
- **News blackout was 15/15**; live is 30/15.
- **Look-ahead**: decision frames were sliced to `i + 1`, handing the
  simulator the still-forming M15 bar's final high/low/close. Now `i`.
- **The exit scan skipped the entry bar**, deferring every stop by 5 minutes.
- **Rewritten as one chronological event loop** over the merged M15 timeline.
  Cooldown, balance, daily loss and the position cap are global in the live
  agent — a loss on XAUUSD pauses USOIL — and a per-symbol loop cannot
  express that.
- **P&L is now net of round-turn spread and commission.** Each trade records
  `exit_reason`, `gross_pnl`, `cost_usd`, `volume`, `risk_usd`, `r_multiple`.
- Rejections are counted per gate so the simulator and the live logs can be
  reconciled.

### Added

- **`analyze_walkforward.py`** — 60/20/20 chronological folds with a sign
  verdict, expectancy per exit type with leave-one-out counterfactuals, the
  survival condition against *realised* payoff, per-symbol / trigger /
  direction breakdowns, and the rejection ledger.
- **`tests/test_backtest_fidelity.py`** — 15 tests pinning the four exit
  profiles in both directions, stop-wins-ties, the entry bar resolving a
  trade, the 23:00 UTC trading-day boundary, and the injected clocks.

### Fixed

- **`MT5Connector.connect()` and `backtest_scalper` now attach to an
  already-authorised terminal before attempting a credentialed login**, and
  accept the attach only when the terminal is on the expected account number.
  Passing credentials unconditionally re-logged the terminal on every connect;
  with a stale password in `.env` that did not merely fail, it dropped a
  working session and left the terminal logged out. Observed directly: the
  terminal authorised account 40280210 at 02:12:20, and a Python login five
  seconds later took it offline until it was signed in by hand.
- **`ShortTermBiasFilter.check()` and `SACRG.check()` take an injected `now`.**
  Both read the wall clock internally, so replaying May bars against today's
  date silently disabled the session-liquidity tracker and the consecutive-loss
  pause in any simulation.

### Verification

```
python -m unittest discover -s tests     # 47 passed (32 + 15 new)
python -m compileall -q .                # clean
```

### What this rules out, and what it does not

Ruled out: that the M15/M5 migration and the 2R/3R geometry are on their own
sufficient to make `SWEEP_REJECTION` profitable. They moved realised RRR to
1.99 against a 2.05 requirement — a large improvement over the pre-migration
configuration, and still a loss. Also ruled out: the superseded +82.51% figure
in `CLAUDE.md` §9, which came from a harness that ran M15 bars through the M5
code path.

Not ruled out: the +$25.32 test fold. One positive fold in three is what noise
looks like at n=40; it is not evidence of a regime change and must not be read
as one.

---

## 2026-08-22 17:47 UTC — Production hardening, M15/M5 migration, cooldown switch

Large corrective release. Closes the twelve findings raised in the
[technical reference](TECHNICAL_REFERENCE.md) audit, migrates the scalper's
timeframe stack, and adds the post-trade cooldown.

### Added

- **`scalper/cooldown.py` — post-trade cooldown switch.**
  - Loss (or breakeven) blocks new entries until the top of the **next UTC
    hour**: a loss at 11:15 holds until 12:00, a loss at 11:55 also holds
    until 12:00.
  - Win blocks new entries for **5 minutes** (`--win-cooldown-min`).
  - Master switch `--no-cooldown`; alternative `FIXED_MINUTES` loss policy.
  - `restore()` re-arms from the last closed deal on startup, so restarting
    the process is not an escape hatch from an active pause.
  - Checked at the top of `_scan_symbol`, before any analysis work.
- **`core/constants.py`** — single source of truth for magic numbers, the
  read-side `MAGIC_TO_AGENT` map, `agent_for_magic()`, position-ownership
  flag, and log-rotation limits.
- **`tests/`** — 32 tests covering cooldown semantics, target geometry, the
  net-R cost gate, and equal-level clustering. Run with
  `python -m unittest discover -s tests`.
- **News blackout for Pool A.** `maingpt.py` now loads `NewsGuard`, refreshes
  the calendar every 6 hours, and blocks execution inside a HIGH-impact
  window. Previously only the scalper respected the blackout even though the
  documentation described it as system-wide.
- **Trade journalling on both pools.** `AdaptiveMemory.log_trade()` had no
  callers and `data/trade_journal.db` held zero rows; SEE therefore always
  voted "no history" and the meta-fund never rebalanced. Pool A now diffs its
  open-position set per cycle and books whatever closed; the scalper writes
  from its unified close handler.
- **Bearish `BOS_RETEST`.** The detector only ever implemented the
  break-above-and-retest case, which is why it produced 0 of 287 logged live
  trades. The mirrored short branch now exists.
- **Net-R cost gate** (`step3_validate` layer 4). Rejects any setup whose
  target cannot clear round-turn spread by `MIN_NET_R` (1.5).

### Changed

- **Scalper timeframe stack is now M15/M5** (was M5/M1).
  - `TF_TRIGGER = M15`, `TF_CONFIRM = M5`, 150 bars each; H1/H4 remain the
    bias tiebreakers.
  - Rationale: the M5/M1 stack fired ~11 trades/day/symbol on the dominant
    trigger and was negative in every walk-forward fold; round-turn spread is
    a fixed per-trade cost, so cutting trade frequency cuts cost drag, and
    M15 stop distances clear the institutional SL floor instead of fighting
    it. This also makes the live agent and the backtester measure the same
    strategy for the first time.
  - Timeout is now expressed in trigger-frame bars (`TIMEOUT_BARS = 24`), so
    6h on M15 — previously a fixed 120 minutes, which on M15 would have been
    8 bars and forced a random exit on most setups.
- **Target geometry moved from 1R to 2R (TP1) and 3R (TP2).** This is the
  single highest-impact change. A 1R target needs >50% win rate to break even
  before costs; the live sample was 37 wins against 173 losses and 67
  timeouts. Published minimum-risk work gives the survival condition as
  `RRR > (1 - win_rate) / win_rate`; both MQL5 SMC reference EAs ship a
  2.0–2.14 default RR. Verified: the cost gate now rejects the old 1R
  geometry at net R 0.95 and accepts 2R at net R 1.91.
- **`Whole_day` session window is opt-in.** It sat last in an ordered
  first-match list spanning 00:00–23:00, so it matched every hour outside a
  kill zone, made `IDLE` unreachable, and had the agent scanning 23 hours a
  day — live logs showed `Session=Whole_day` during normal trading. Now
  behind `SASessionChecker(allow_whole_day=True)` / `--allow-whole-day`.
- **Magic numbers unified.** `MT5Connector` writes `MAGIC_MAIN` (20260426);
  the Guardian resolves origin through `agent_for_magic()`, which recognises
  it. Previously the Guardian's hard-coded ladder omitted that value, so every
  Pool A position was registered as `origin_agent="UNKNOWN"`.
- **Gate 4 uses the configured portfolio limits.** It hard-coded `< 4`
  positions and `< 6.0%` exposure while config declared `max_positions: 2`
  and `max_total_exposure_pct: 40.0`, so CRG and Gate 4 enforced different
  books. `HedgeFundOS` now takes both from config.
- **Gate 2 receives every agent that voted.** `maingpt` passed only the five
  non-execution agents while the threshold stayed at 5, silently turning
  "5 of 7" into "5 of 5" — unanimity plus zero abstentions. EA and CRG now
  count toward consensus; CRG keeps its independent Gate 3 veto.
- **Unified scalper close handler** (`_on_trade_closed`). Pool ledger, trade
  log, cooldown arming, and journalling now happen in one place. The timeout
  and EOD paths previously booked `pos.profit` (excludes commission and swap)
  while the normal path booked the reconciled deal sum — two P&L definitions
  for the same event.
- **`_find_equals` rewritten.** Was an O(n²) pairwise scan that ignored both
  its `current_price` and `side` arguments and emitted one level per matching
  *pair*, so three equal highs became three near-identical pools. Now a single
  sorted cluster walk that returns only levels on the usable side of price.
- **Backtester aligned to the live decision path.** Replaced the blanket
  `MANIPULATION` veto with the same per-trigger regime whitelist the agent
  uses (the veto rejected precisely the regime the live agent trades),
  restored the `sl_pips` argument to `step3_validate` so the SL floor and
  spread-ratio guards actually run, and matched the timeout to 6h.
- **Log rotation** on both daemons: 10 MB × 5 backups. The two logs had
  reached 6.7 MB and 13.1 MB with no ceiling.
- **`requirements.txt`** — added the undeclared `matplotlib`; removed
  `pandas-ta`, `scipy` and `colorama`, none of which are imported anywhere.

### Removed

- `HTFBiasFilter` import from the scalper. It was constructed on every start
  but superseded by `ShortTermBiasFilter`; the module remains on disk for
  reference.

### Fixed

- Gate 3's rejection log printed `?` for every CRG veto because it read
  `crg_vote.reason` while `AgentVote` defines `reasoning`. The actual veto
  reason — governance cap, drawdown, correlated position — is now visible.
- Order-flow state was computed every cycle and discarded; it is now surfaced
  in the per-symbol summary.

### Security

- Added a repository `.gitignore` that excludes `.env`, logs, and the SQLite
  journal. A populated `.env` is present in the tree — **rotate the MT5
  credentials before initialising git or sharing this directory.**

### Verification

```
python -m unittest discover -s tests     # 32 passed
python -m compileall -q apex_ai          # clean
```

Geometry and gating were also exercised directly: 2R/3R targets confirmed for
both directions, session gating confirmed IDLE outside kill zones, and the
net-R gate confirmed to reject 1R and accept 2R at realistic spreads.

### Known limitations

- Profitability is **not** demonstrated by these changes. They remove
  defects that made positive expectancy arithmetically impossible; they do
  not by themselves prove an edge. See
  [docs/RESEARCH_NOTES.md](docs/RESEARCH_NOTES.md) for the evidence base and
  the walk-forward protocol that must be run before any live sizing change.
- The main system still has no backtester.
