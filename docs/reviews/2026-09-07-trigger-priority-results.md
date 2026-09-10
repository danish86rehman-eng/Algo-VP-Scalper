# Scalper trigger isolation and priority — fresh current-code experiment

Generated 2026-09-07 09:24 UTC. No production code or live strategy settings changed; no live orders submitted by this study.

## Interpretation

SWEEP_REJECTION has the highest standalone net among the five active families: +$295.82, versus JUDAS +$292.58. That $3.24 advantage is small and reverses under the higher-cost fixed-trade overlay. JUDAS has only 18 trades, versus Sweep's 128; its apparent standalone quality does not establish a better combined priority.

Of the tested combined orders, Sweep-first has the highest two-window net: +$418.58, versus +$259.95 for current precedence and +$320.59 for Judas-first. However, Sweep-first changes the baseline by -$7.39 / +$166.02 across W1/W2, while Judas-first changes it by -$126.12 / +$186.76. Neither promotion improves both periods. Sweep-first is the leading further-testing candidate, not a validated live promotion. Leave live precedence unchanged on this evidence.

CRT has no executed sample. BOS has one trade; FVG has seven. Those are insufficient for a robust profitability ranking. The optional VP arm uses different hours and is not a same-session priority alternative.

## Scope and fidelity

Frozen current working copy, including the operator's existing uncommitted changes; 490 tests pass and all snapshot Python compiles. Attached Exness-MT5Real2 XAUUSD history, not the old demo CSVs. Source/data hashes, symbol specification and six broker-vs-offline P&L calculator checks are in `manifest.json`.

Each arm/window starts at $1,000, 3% compounding risk, $100 daily loss limit, current cooldown and position controls. Tokyo 00:00–02:00 and London/NY 12:00–13:30 UTC only. Shared Guardian enabled. M15 triggers, M5 confirmations/decision clock, H1/H4 context and native D1/W1/MN1 CRT ranges. The current reclaim/FVG gates remain on; no legacy M5 FVG substitution.

Net uses the existing measured-total-cost option at $0.35/oz round trip. It replaces, not duplicates, spread and commission debits. Archived spread still drives entry gates; zero spread falls back to 6 pips. The reference was measured on DEMO. Ten current live deals showed commission-per-lot values $0/$11 and zero swap, but do not establish live slippage.

**Not tick-exact or a live-profit forecast:** the shipped simulator uses M5 fills and approximate Guardian timing; news stays enabled but the frozen calendar covers only September 10–11, so historical news blackouts cannot be reconstructed. No broker rejection, partial-fill or restart simulation. Historical dates have been used before; the experiment/configuration/feed comparison is new, not an untouched OOS claim.

The simulator also passes `main_account_dd_pct=0.0` to the shared risk governor (`backtest_scalper.py:1149`) and does not replay other account activity or broker margin rejection. These are isolated virtual-pool results, not a reconstruction of the live account's available margin or account-wide drawdown gate.

## Isolated trigger results

W1 = March 1–May 31; W2 = June 1–August 31, 2026. Dollar totals sum two independent $1,000 starts, not a continuously compounded six-month account. PF is pooled winning net P&L / absolute losing net P&L. Zero trades is unmeasured, not profitable.

| Trigger | W1 trades / net $ | W2 trades / net $ | Total net $ | Pooled PF | Net R |
|---|---:|---:|---:|---:|---:|
| SWEEP_REJECTION | 50 / -119.94 | 78 / +415.76 | +295.82 | 1.15 | +8.51 |
| JUDAS | 4 / +77.18 | 14 / +215.40 | +292.58 | 2.58 | +8.55 |
| FVG_FILL | 2 / +88.05 | 5 / -17.47 | +70.58 | 1.72 | +2.32 |
| HTF_CRT_SWEEP | 0 / +0.00 | 0 / +0.00 | +0.00 | n/a | +0.00 |
| BOS_RETEST | 1 / -31.70 | 0 / +0.00 | -31.70 | 0.00 | -1.05 |

The two dormant families are excluded from this production-configuration ranking and tested separately below; their feature switches remain false in production.

## Combined priority tests

Changing `--triggers` list order does not change precedence: it becomes a set. Actual active precedence is HTF_CRT_SWEEP → M15 FVG_FILL → SWEEP_REJECTION → BOS_RETEST → JUDAS. The research wrapper calls the unchanged detectors, changing only which detected candidate wins in its own process.

| Combined configuration | W1 net $ | W2 net $ | Delta vs baseline W1 / W2 $ |
|---|---:|---:|---:|
| Current precedence | +10.21 | +249.74 | — |
| SWEEP_REJECTION first | +2.82 | +415.76 | -7.39 / +166.02 |
| JUDAS first | -115.91 | +436.50 | -126.12 / +186.76 |

ARBITRATION_EQUIVALENT in comparison.json means the promoted family never lost a first-match decision on the baseline path. Under this deterministic replay, no state changes and its promotion is equivalent. It is not labelled as a separately executed replay.

## Stability, drawdown and recent check

60/20/20 splits below are chronological splits of each long window; they do not reset the arm's balance. Drawdown is realised closed-balance drawdown within the individual window, not intratrade equity drawdown.

| Arm | W1 fold net $ | W2 fold net $ | W1 / W2 max DD % | Sep 1–6 trades / net $ |
|---|---:|---:|---:|---:|
| SWEEP_REJECTION | -135.13 / -42.16 / +57.35 | +413.06 / -23.52 / +26.22 | 23.18 / 17.72 | 9 / +232.50 |
| JUDAS | -23.16 / +24.10 / +76.24 | +156.36 / +15.96 / +43.08 | 2.83 / 7.81 | 0 / +0.00 |
| FVG_FILL | +78.81 / +9.24 / +0.00 | +46.60 / +0.00 / -64.07 | 0.00 / 6.25 | 0 / +0.00 |
| HTF_CRT_SWEEP | +0.00 / +0.00 / +0.00 | +0.00 / +0.00 / +0.00 | 0.00 / 0.00 | 0 / +0.00 |
| BOS_RETEST | +0.00 / +0.00 / -31.70 | +0.00 / +0.00 / +0.00 | 3.17 / 0.00 | 0 / +0.00 |
| BASELINE | -93.02 / +79.38 / +23.85 | +330.97 / -31.43 / -49.80 | 20.08 / 17.72 | 9 / +232.50 |

## What stopped the other triggers?

Counts below are selected detection scans, not unique setups. Gate counts show the actual first post-detection rejection and can recur on successive M5 decisions.

- HTF_CRT_SWEEP: 1 detections, 0 trades. Top rejections: STEP3:HTF_CRT_SWEEP=1.

- FVG_FILL: 19 detections, 7 trades. Top rejections: RECLAIM_FVG_INVALIDATED:FVG_FILL=4; STB/opposes_short_term:FVG_FILL=4; RECLAIM_RETURN_STALE:FVG_FILL=2; RECLAIM_RETURN_UNCONFIRMED:FVG_FILL=1; STB/range_guard:FVG_FILL=1.

- SWEEP_REJECTION: 4054 detections, 128 trades. Top rejections: RECLAIM_WAIT_LEVEL:SWEEP_REJECTION=2834; RECLAIM_WAIT_DISPLACEMENT:SWEEP_REJECTION=326; RECLAIM_WAIT_FVG:SWEEP_REJECTION=182; CONSUL_GATE1_REGIME:SWEEP_REJECTION=99; LOT_FLOOR:SWEEP_REJECTION=86.

- BOS_RETEST: 3679 detections, 1 trades. Top rejections: RECLAIM_WAIT_LEVEL:BOS_RETEST=1921; CONSUL_GATE1_REGIME:BOS_RETEST=531; RECLAIM_INVALID_GEOMETRY:BOS_RETEST=404; STB/opposes_short_term:BOS_RETEST=271; STB/neutral_needs_direction:BOS_RETEST=259.

- JUDAS: 1828 detections, 18 trades. Top rejections: RECLAIM_WAIT_LEVEL:JUDAS=1099; RECLAIM_WAIT_DISPLACEMENT:JUDAS=236; RECLAIM_WAIT_RETURN:JUDAS=98; LOT_FLOOR:JUDAS=93; RECLAIM_WAIT_FVG:JUDAS=73.

- VP_LIQUIDITY_REACTION: 3068 detections, 69 trades. Top rejections: RECLAIM_WAIT_LEVEL:VP_LIQUIDITY_REACTION=2257; RECLAIM_WAIT_DISPLACEMENT:VP_LIQUIDITY_REACTION=278; RECLAIM_WAIT_FVG:VP_LIQUIDITY_REACTION=188; RECLAIM_WAIT_RETURN:VP_LIQUIDITY_REACTION=84; RECLAIM_RETURN_STALE:VP_LIQUIDITY_REACTION=77.

- VALUE_AREA_FADE: 12 detections, 0 trades. Top rejections: RECLAIM_WAIT_LEVEL:VALUE_AREA_FADE=12.

## Cost sensitivity (fixed-trade overlay only)

| Trigger | $0.17/oz net $ | $0.35/oz net $ | $0.75/oz net $ |
|---|---:|---:|---:|
| SWEEP_REJECTION | +368.54 | +295.82 | +134.22 |
| JUDAS | +298.88 | +292.58 | +278.58 |
| FVG_FILL | +76.70 | +70.58 | +56.98 |
| HTF_CRT_SWEEP | +0.00 | +0.00 | +0.00 |
| BOS_RETEST | -30.98 | -31.70 | -33.30 |

These overlays hold entries and volumes fixed. They do not re-run compounding, lot floors, cooldowns or loss limits under the changed cost, and are not alternative strategy backtests.

## Remaining dormant triggers — research-only activations

| Optional trigger | W1 trades / net $ | W2 trades / net $ | Total net $ | PF | Sep 1–6 trades / net $ |
|---|---:|---:|---:|---:|---:|
| VP_LIQUIDITY_REACTION | 35 / -186.34 | 34 / +90.72 | -95.62 | 0.90 | 2 / -47.64 |
| VALUE_AREA_FADE | 0 / +0.00 | 0 / +0.00 | +0.00 | n/a | 0 / +0.00 |

VALUE_AREA_FADE enables only its implemented profile trigger under the existing two sessions. VP_LIQUIDITY_REACTION preserves the shipped ASIA_ONLY scope and trigger-specific 00:00–06:30 allowance: in this configuration VP detections are evaluated on VP-only bars outside the normal sessions (02:00–06:30). Therefore its profit is not an apples-to-apples current-session priority comparison. No risk, gate, target or detector retuning was performed.

The VP-only arm omits unused pure CRT observations, whose plans cannot be selected by its whitelist. A paired September 4 replay with the full watcher produced the identical trade, P&L, equity, detections and rejection counts. All decision TFs and data fetches remain unchanged. This skips non-decision work, not an entry or exit filter.

## Reproduction and evidence

- `docs/reviews/trigger_priority_study.py`: prepare and isolated/offline replay commands.
- `docs/reviews/2026-09-07-trigger-priority-protocol.md`: prespecified windows, settings and selection rule.
- Evidence folder: `manifest.json`, `comparison.json`, `results/` (every trade), `summaries/` (metrics/arbitration), `data/` (frozen server candles), `snapshot/` (unchanged production source), and test logs.
- Initial snapshot test attempt omitted two repository-relative historical fixtures: 490 ran, two FileNotFound errors. Copying those existing fixtures into the snapshot produced 490/OK. No tests or production code were edited to obtain a pass.

Source locations: `apex_ai/scalper/trigger_engine.py:299` (actual selection); `:266` (whitelist is stored as a set); `apex_ai/scalper/decision_params.py:34` (TFs); `apex_ai/scalper/session_checker.py:54` (default sessions); `apex_ai/backtest_scalper.py:600` (replay); `:781` (M5 decision clock); `apex_ai/scalper/exit_manager.py:27` (bar/tick limitations).

No automatic promotion. Sparse samples, sign changes, multiple comparisons, retrospective windows and missing historical news/live execution calibration must qualify any ranking.
