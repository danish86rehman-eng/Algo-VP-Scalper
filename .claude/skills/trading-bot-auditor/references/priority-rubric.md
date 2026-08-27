# Priority Rubric

Use this when assigning impact tier to a finding. Inflated criticality dulls the signal of your real Critical findings — when in doubt, pick the lower tier.

## 🟥 Critical

A finding is Critical if it meets *any* of these:

- A documented feature isn't actually implemented (e.g., spec says "TP2 partial close" but code closes full position at TP1)
- Capital is at risk from silent failure (no reconnection logic, exceptions swallowed)
- Crash recovery is missing for state that matters (open trades, daily limits)
- A safety check exists but can be trivially bypassed (e.g., daily loss limit resets on restart)

**Example:** "TP2 stored only in memory; if process crashes mid-trade, TP2 target is lost and trade has no upper exit."

**Not Critical:** Style issues, missing comments, naming inconsistencies, or anything that doesn't materially affect capital or correctness.

## 🟧 High

A finding is High if it materially affects profitability or drawdown, *and* the fix is concrete:

- Fixed parameters that should be regime/volatility-adaptive (with evidence they're hurting performance)
- Missing filters or quality gates that would meaningfully change win rate
- Sizing rules that don't scale with confidence or recent performance
- Hardcoded values that work for one instrument but break others (e.g., 0.03% tolerance fine for Gold, useless for Oil)

**Example:** "Risk per trade is fixed at 0.3% regardless of trigger confidence. HIGH-confidence SWEEP_REJECTION setups (60%+ historical win rate) get the same size as MEDIUM-confidence FVG_FILL setups (45%)."

**Not High:** Speculative improvements without a clear failure mode. "Maybe we could add ML" is not a High-priority finding.

## 🟨 Medium

A finding is Medium if it improves robustness or efficiency without a direct profit case:

- Single point of failure with a workaround (e.g., one news source — bot keeps stale data on failure, but doesn't crash)
- Inefficient algorithms that work but waste compute
- Loss prevention rules that are too lenient (15-min pause for any number of consecutive losses)
- State machine details (grace periods, hysteresis)

**Example:** "Equal-high detection uses O(n²) pairwise comparison. Works fine for 50 bars but doesn't scale, and doesn't cluster nearby levels into single liquidity zones."

## 🟩 Low

A finding is Low if it's a quality-of-life improvement:

- Logging/observability (dashboard, metrics export, slippage tracking)
- Code organization (extracting magic numbers, splitting files)
- Documentation/clarity (resolving ambiguity in spec vs. code)
- Optional features (paper trading mode, A/B testing)

**Example:** "Trade durations stored as integer minutes. Sub-minute precision lost — can't analyze fills that resolve in 30-90 seconds."

**Not Low:** Anything you'd actually be embarrassed to ship without. Push that to Medium or higher.

## 🟦 Architectural

A finding is Architectural if it requires weeks of work and significantly changes how the system is structured:

- Unifying backtest and live code paths
- Walk-forward optimization framework
- Migrating from single-threaded to parallel scanning
- Decoupling from broker-specific APIs (MT5 → broker-agnostic abstraction)

**Example:** "`backtest_scalper.py` reimplements parts of `scalper_agent.py`. Risk of drift — bug fixes need to be applied in two places, and subtle differences invalidate backtest predictions."

These items go last in the report because they require their own design phase, not just an implementation push.

## Tiebreakers

When you can't decide between two tiers:

| Question | If yes → use higher tier |
|---|---|
| Does this affect capital? | Yes → Critical or High |
| Does this affect win rate or PnL? | Yes → High |
| Is there a concrete failure mode? | Yes → at least Medium |
| Is the fix a one-line change? | Yes → consider lower tier |
| Could the user reasonably ship without this? | Yes → Low |

The honest test: would a senior trader/quant glancing at your priority assignment nod, or roll their eyes? Optimize for the nod.
