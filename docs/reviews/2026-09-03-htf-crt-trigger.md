# D1 / W1 / MN1 liquidity sweep trigger — L-019

Date: 2026-09-03. Scope: operator-requested scalper implementation and DEMO activation. Efficacy remains unverified. Monthly means MN1, not one-minute M1.

## Evidence and diagnosis

Broker snapshot confirms 1 September D1 low **4322.777**; 2 September low **4282.332**, close **4385.502**, inside the prior daily range [4322.777, 4461.724]. This supports the operator's daily SSL-raid observation. The screenshot labels about 4281.16; feed differences are not silently substituted for broker prices. The weekly bar beginning 30 August was still forming at observation, and September's monthly bar had not swept August's low. These are separate timeframes, not three confirmed signals.

The actual trigger engine previously had no daily/weekly/monthly CRT entry source despite CRT appearing in the architecture description. A local M15 sweep and a prior-day location veto cannot represent this chain. L-017 and L-018 remain active: this addition does not permit buying through unreclaimed broken support or inventing a fill at the low.

## Programmed contract

1. **First watch:** native previous completed D1, W1 and MN1 highs/lows, checked on each new completed M15 observation, including outside entry sessions. Missing/stale source data is explicit. Weekly periods retain the broker's actual origin; monthly closure uses calendar boundaries. Historical decisions select only anchors already closed when the raid began, including across period rollover.
2. **Raid:** M15 crosses from a prior close inside the range to a wick beyond its edge by more than 0.1 preceding M15 ATR(14). SSL gives a bullish candidate; BSL gives a bearish candidate. Track the breach extreme from closed candles. An anchor's colour does not restrict direction: a bearish prior day can have its low swept and reversed.
3. **Displacement reclaim:** closed M15 candle returns inside the HTF range beyond the frozen 0.1 ATR buffer. Directional body >=0.8 preceding ATR, body >=60% of candle range, close in outer 25%. The candle can begin inside the range after an initial close-back-inside. These thresholds reuse the prior contract; they were not optimized for this screenshot.
4. **FVG:** the reclaim displacement must form a same-direction three-candle M15 gap >=0.05 preceding ATR. Only the third candle's close establishes it. If no gap forms, a later qualifying displacement may establish one. Wait for a later M5 observation and an executable quote returning inside the gap, on the reclaimed side of the HTF level. No candle extreme is used as a fill.
5. **Invalidation:** missing continuous return history, full gap mitigation even by wick, loss of reclaimed level, opposite HTF edge reached, or 24 M15 bars since gap formation cancels the entry. The initial raid must be observable in the 150-M15-bar history; an old breach outside that window is not reconstructed from a future weekly/monthly low.
6. **Geometry:** SL beyond the raid extreme by 0.3 displacement ATR. Target before the range midpoint if still ahead, otherwise before the opposite edge; a nearer broken M15 structural level caps that target. Never skip an obstacle or tighten the stop to manufacture >=2R. Existing spread, minimum-stop, >=1.5 net-R, lot-size, risk, news, cooldown and session gates apply.
7. **Selection and reuse:** actual `HTF_CRT_SWEEP` is first in selection, ahead of VP and local models. Concurrent ready directions abstain for the CRT family; aligned candidates prefer MN1, then W1, then D1. One executed setup per symbol/timeframe/anchor/direction. Broker entry comments restore used identifiers; same-direction close barriers also prevent another timeframe from recycling a pre-close raid. Other models retain their own contracts.
8. **Management:** durable `SA_CRT_<id>` order comment identifies the structural target to TGA, including after restart. No TP extension through the range target. Protective trailing/early exits remain active. The simulator uses the same target cap and pure evaluator.

Default `CRT_ENABLED=True`; both CLIs expose `--htf-crt` / `--no-htf-crt`. An explicit trigger whitelist remains binding. Config era `2026-09-03-htf-crt-trigger`. No session expansion: watching outside a session does not allow execution there, and VP_ONLY continues to admit only its named VP trigger.

## Sources and prior research

[MQL5 CRT/AMD EA, Part 41](https://www.mql5.com/en/articles/20323) provides a design reference for range breaches, close-back-inside confirmation and one-trade-per-setup state. Its anchor-colour filter, H4 default and 1.3R default are not adopted. It is not performance evidence for this implementation.

[MQL5 Python book: reading quotes](https://www.mql5.com/en/book/advanced/python/python_copyrates) documents W1/MN1 native frames. [copy_rates_from_pos reference](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrompos_py) establishes that position zero is the current bar; live source fetches use position one, and replay independently enforces each bar's calendar close.

Vault FS-4 must remain visible: H4 base 1,649 trades, net -0.1923R/trade; daily base 355, -0.0868R; ICT-confirmed H4/H1 variant only 16, insufficient sample. Those frozen rules differ from this D/W/M -> M15 FVG trigger. Their rejection is not erased, and this code has no established profitable edge. The operator's explicit implementation request authorizes a demo experiment, not an evidence-qualified live promotion.

## Verification and artifacts

Shared source: `apex_ai/scalper/htf_crt.py`; selection: `scalper/trigger_engine.py`; matching integration: `scalper_agent.py`, `backtest_scalper.py`; target recovery: `trade_guardian_agent.py`.

Tests cover both sides on all three frames, real selection priority, mocked order submission through the scan path, backtest trade creation, Guardian recovery, reused setup rejection, costs, lost level at final quote, forming/future bars, calendar months/leap year/year rollover, broker Sunday weeks, stale/missing data, expiry and gap invalidation. Existing VP baseline tests now explicitly disable CRT when asserting the pre-CRT baseline. No live broker orders are sent by tests or replay.

Reproducible broker-read-only development replay: `docs/reviews/crt_pipeline_smoke.py`. Evidence folder: `docs/reviews/2026-09-03-htf-crt-evidence/`. M5 opening bid observations approximate scans; they cannot establish every executable intrabar ask or prove that a chart-marked opportunity was fillable. This is integration evidence, not a backtest promotion or a loss-avoidance estimate.

Final validation and activation follow.


## Final validation and demo activation — 2026-09-03

**473 unit/integration tests pass (26 CRT tests); compileall and diff whitespace checks pass.** Synthetic buy/sell setups on every native frame reach mocked broker order submission through the live scan path, with reclaim, cost and risk checks active; separate backtest integration emits a CRT trade. Target-preservation and broker-comment recovery are tested. Legacy tests remain in the suite. Test broker calls were mocked; replay forbids order submission.

Final bounded diagnostic replay: **2 September 11:00–15:00 UTC**, including prior-bar warmup. Zero simulated trades, zero CRT entries. Existing session gate rejects 31 observations; L-017 blocks 15 at WAIT_LEVEL and 3 at WAIT_FVG. The CRT watch recognizes the daily raid, displacement, no-gap rejection, later confirmed gap and WAIT_FVG_RETURN. The later gap is 4344.743–4357.146, formed at 13:45 UTC from the 13:15 displacement. No executable qualifying return occurred in the checked observations. These are funnel counts on M5 opening observations, not independent trades or proof of missed profits. Initial broader development replays were diagnostic; `pipeline-replay.json` and `crt-watch-replay.json` are the final bounded artifacts.

**Both services reloaded at 05:11 UTC / 10:11 Pakistan.** Existing watchdog PID 7704 restarted scalper 22532 -> **25460**, Guardian 19324 -> **192**. Broker account confirmed DEMO and flat before and after. Startup logs show HTF CRT ON, M15 FVG ON, L-017 ON and five restored close barriers. Existing launch arguments remain pool $1,000, risk 3%, XAUUSD, 30-second scans, $100 daily limit and FRESH mode. Scalper completed its first idle scan with all six D1/W1/MN1 directional watch records; TGA entered its main loop.

The new runtime recognizes yesterday's D1 long setup as EXPIRED and today's raid above previous daily high **4397.816** as **SWEPT_WAIT_DISPLACEMENT**, with observed high 4438.616. That status is a watch, not a sell order. Current entry sessions remain binding.

Evidence: `full-tests.txt`, `focused-tests.txt`, `compileall.txt`, native broker OHLC CSVs, `pipeline-replay.json`, `crt-watch-replay.json`, `pre-reload.json`, `post-reload.json` (source hashes), process snapshots and activation logs, all under `docs/reviews/2026-09-03-htf-crt-evidence/`. Existing import-time logging emits a ResourceWarning in standalone focused tests; assertions pass. Earlier synthetic test log entries are not broker trades; activation evidence starts at 05:11 UTC.
