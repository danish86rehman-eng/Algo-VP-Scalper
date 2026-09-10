# L-017: displacement reclaim and FVG return correction

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
