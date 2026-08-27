# Trading Bot Audit Checklist

A module-by-module list of common issues to look for. Not exhaustive — use it as a starting point, then trust your judgment for system-specific patterns.

## Main Loop / Entry Point

- Cycle interval — is it appropriate for the timeframes used? 30s loop checking M1 setups will miss fast moves
- Error handling — do exceptions in one cycle silently kill the loop, or is there a circuit breaker?
- Reconnection logic — what happens when the broker connection drops mid-run?
- Startup checks — does it validate account balance vs. configured pool, MT5 connection, env vars?
- Graceful shutdown — KeyboardInterrupt handling, in-flight trade reconciliation
- Repeated error patterns — is there a max-error count that halts the bot?

## Signal / Trigger Engine

- **Fixed tolerances** — any constant that's a percentage or absolute value should probably be ATR-based
- **ATR calculation** — simple mean vs Wilder's smoothing? Wilder's matches industry tools
- **Pairwise/quadratic algorithms** — equal-high detection comparing all pairs is O(n²)
- **Hardcoded session offsets** — Judas swing using `df[-15:-12]` assumes 5-min bars; breaks if timeframe changes
- **No volatility filter** — same logic in chop and trending regimes
- **TP1/TP2 ratios fixed** — should adapt to regime
- **No volume confirmation** — when volume data exists but isn't used
- **No multi-timeframe agreement** — M5 trigger without M15/H1 alignment check
- **No entry quality score** — all triggers treated equally regardless of context strength

## Risk Governor

- **Pause durations static** — 15-min pause whether after 2 or 5 consecutive losses
- **No correlation check** — open positions on correlated instruments stack risk
- **Direction conflict** — opposing positions on same symbol (BUY + SELL)
- **Hardcoded thresholds** — 3% main DD threshold should be configurable
- **No volatility-adjusted limits** — same daily loss cap regardless of regime

## Execution Layer

- **Market orders only** — no consideration of limit orders for better fills
- **No retry on order send failure** — requote/timeout = lost setup
- **Hard TP/SL only** — TP2 tracked in memory means crash recovery fails
- **No partial close logic** — "TP1 partial + runner" model needs MT5 modification calls
- **Magic number hardcoded** — prevents running multiple instances
- **No slippage tracking** — requested vs. actual fill price not logged
- **Deviation parameter fixed** — should escalate on retry

## State Management

- **In-memory only** — restart loses open trades, daily PnL, pause timer
- **No reconciliation** — if MT5 has open positions with our magic number on startup, are they tracked?
- **Daily counters resetting on restart** — daily loss limit becomes exploitable
- **Pause timer not persisted** — bot restart bypasses cooldown

## Capital / Pool Management

- **Pool adjustment too aggressive** — 50% reduction on 3 losing days punishes statistical normalcy
- **Pool adjustment too slow** — 5-day winning streak for 10% increase = years to compound meaningfully
- **No equity curve tracking** — only point P&L
- **No unrealized P&L tracking** — open positions don't count toward DD
- **Risk percentage static** — doesn't scale with confidence or recent performance
- **No drawdown-based scaling** — full risk during a bad streak

## Session / Timing

- **DST not handled** — fixed UTC times shift relative to local sessions twice a year
- **Static session windows** — no quality scoring (e.g., low-vol Friday vs. high-vol Tuesday)
- **Fallback windows** — "trade always" fallbacks that contradict the documented strict-window rule
- **No EOD enforcement** — positions carry into next day, weekend, holiday

## State Machine

- **Instant transitions** — no grace period, whipsaws between states
- **No hysteresis** — enter PROTECTED at 3% DD, exit at 3.0001% (oscillates)
- **State logged every cycle** — log noise, only changes should be logged
- **Missing states** — no WARMUP for first-run, no MAINTENANCE for scheduled downtime

## External Data (News, Market)

- **Single source of failure** — only one news provider, no fallback
- **No retry logic** — single fetch attempt, fail = stale data for hours
- **Static blackout window** — same window for NFP and minor data points
- **Symbol mapping incomplete** — currency-to-instrument map missing common pairs
- **No success rate monitoring** — silent feed degradation goes unnoticed

## Logging / Telemetry

- **JSON file only** — no querying, file grows unbounded
- **Read-write whole file each trade** — wasteful as file grows
- **No real-time dashboard** — operator must tail logs to see live state
- **Trade duration as integer minutes** — loses precision for sub-minute analysis
- **No metrics export** — can't feed into monitoring systems (Grafana, etc.)

## Architectural / Cross-Cutting

- **Backtest and live diverge** — separate code paths can drift, invalidating backtest predictions
- **No walk-forward** — single-period backtest = high overfitting risk
- **Single-threaded scanning** — sequential symbol scans accumulate latency
- **No paper trading mode** — `--dry-run` doesn't simulate fills realistically
- **No A/B framework** — can't compare two strategy variants on same data
- **Hardcoded broker assumptions** — MT5-specific code that won't port to other brokers

## What NOT to flag

- **Style preferences** — naming, indentation, import ordering. Not your call.
- **Theoretically better algorithms with no measurable benefit** — "use deque instead of list" when list is fine
- **Refactors for refactor's sake** — splitting one 200-line file into three 70-line files
- **Comments and docstrings** — unless materially misleading or contradicting the code
- **Dependency upgrades** — unless tied to a security or correctness issue

The bar is: "would the user agree this is worth engineering time?" If you can't justify it with a *Problem* statement that names a real failure mode or missed opportunity, leave it out.
