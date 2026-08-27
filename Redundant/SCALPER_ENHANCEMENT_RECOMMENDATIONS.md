# 🚀 Scalper Agent — Enhancement Recommendations

**Generated:** 2026-05-09
**Status:** Documentation only — no code changes pending approval
**Scope:** Comprehensive review of all SA modules

This document lists prioritized improvements found during a full code audit of the Scalper Agent. Each item explains the problem, the proposed enhancement, expected benefit, and implementation complexity.

---

## 🟥 CRITICAL (Safety/Correctness)

### C1. TP2 Not Tracked as Hard MT5 Order

**Current behavior:** Only TP1 is sent to MT5 as a take-profit. TP2 lives only in Python memory (`self._open_trades[ticket]["tp2"]`).

**Problem:**
- If SA process crashes or restarts, TP2 target is lost — trade has no upper exit
- No partial close at TP1 (current code closes full position at TP1)
- The "1:1 partial + 1:2 runner" execution model isn't actually implemented in MT5

**Enhancement:**
- After TP1 fills, SA should detect partial close and modify the remaining position to set TP2 as MT5 hard order
- Persist `_open_trades` dict to disk so crashes don't lose state

**Impact:** HIGH — fixes a documented but unimplemented feature
**Complexity:** Medium

---

### C2. No MT5 Reconnection Logic

**Current behavior:** If MT5 disconnects during runtime (network blip, broker restart), trades fail silently. The main loop catches the exception but doesn't reconnect.

**Problem:**
- SA enters zombie state — running but unable to execute
- Open positions can't be monitored or closed
- No alert to user

**Enhancement:**
- Detect connection loss (`mt5.account_info() == None`)
- Auto-reconnect with backoff (5s → 15s → 60s → halt with alert)
- Log every disconnect/reconnect event
- Track reconnect count per session

**Impact:** HIGH — operational reliability
**Complexity:** Easy-Medium

---

### C3. State Not Persisted Across Restarts

**Current behavior:** All SA state (open trades, daily P&L, consecutive losses, pause timer, session open prices) lives in memory only.

**Problem:**
- Restart loses all tracking — open trades become orphaned
- Daily loss limit resets on every restart (could be exploited)
- Consecutive loss pause clears

**Enhancement:**
- Save state to `state/sa_state.json` every cycle (lightweight)
- On startup, load previous state if from same UTC date
- Reconcile open trades with MT5 positions (magic 88880)

**Impact:** HIGH — production reliability
**Complexity:** Medium

---

## 🟧 HIGH IMPACT (Performance/Profitability)

### H1. Fixed Tolerances — Should Be ATR-Based

**Current behavior:** Several constants are fixed values instead of volatility-adjusted:
- Equal H/L tolerance: `0.0003` (0.03%) — same for XAUUSD ($4500) and USOIL ($90)
- BOS_RETEST proximity: fixed `0.001` (0.1%)
- HTF bias buffer: fixed `0.05%`

**Problem:**
- For XAUUSD, 0.03% = ~$1.35 (very tight)
- For USOIL, 0.03% = ~$0.027 (way too tight, missed setups)
- High-volatility regimes need wider tolerances; low-vol regimes need tighter

**Enhancement:**
- Replace fixed tolerances with multiples of ATR
- E.g., equal H/L tolerance = 0.05 × ATR(14)
- HTF bias buffer = 0.10 × ATR(14)
- BOS retest proximity = 0.25 × ATR(14)

**Expected benefit:** Catches more valid setups in trending markets, fewer false signals in choppy markets
**Impact:** HIGH — directly affects trade quality
**Complexity:** Easy

---

### H2. No Volatility Regime Filter

**Current behavior:** SA trades the same way in low-vol (Tokyo lunch) and high-vol (NFP) regimes.

**Problem:**
- Backtest shows USOIL has 28% timeout rate — many setups aren't resolving
- Low-vol periods have lower R:R potential
- High-vol periods need wider stops or smaller size

**Enhancement:**
- Calculate rolling 24-hour ATR vs 30-day ATR
- Define regimes: LOW (< 0.7x), NORMAL (0.7-1.3x), HIGH (> 1.3x)
- LOW volatility → reduce trade frequency or skip
- HIGH volatility → widen SL by 1.2x, reduce position size
- NORMAL → trade as-is

**Expected benefit:** Reduces timeout rate, improves R:R during sweet-spot conditions
**Impact:** HIGH
**Complexity:** Medium

---

### H3. Risk Per Trade Doesn't Scale With Confidence

**Current behavior:** Every trade risks the same fixed % of pool (0.3% default).

**Problem:**
- HIGH-confidence triggers (SWEEP_REJECTION, JUDAS) get same size as MEDIUM (FVG_FILL, BOS_RETEST)
- Wastes capital on lower-quality setups
- Caps upside on highest-quality setups

**Enhancement:**
- Tier-based sizing:
  - HIGH confidence → 1.0x base risk
  - MEDIUM confidence → 0.6x base risk
  - LOW confidence → 0.3x or skip
- All council gates passing → +0.2x bonus
- Counter-trend (still allowed) → 0.5x

**Expected benefit:** Better capital allocation, higher Kelly fraction utilization
**Impact:** HIGH
**Complexity:** Easy

---

### H4. No Correlation Check Between Open Positions

**Current behavior:** SA can hold 2 positions simultaneously. No check on whether they're correlated.

**Problem:**
- USOIL + XAUUSD often correlate during risk-off events
- Two correlated longs = effectively double exposure to same theme
- Two opposing trades on correlated symbols = self-cancelling

**Enhancement:**
- Maintain rolling 30-day correlation matrix between traded symbols
- Block second trade if correlation > 0.70 with open trade and same direction
- Allow opposite-direction correlated trades only with reduced risk

**Expected benefit:** True diversification, reduced theme-risk concentration
**Impact:** HIGH
**Complexity:** Medium

---

### H5. Dynamic TP1/TP2 Based on Regime

**Current behavior:** TP1 = 1:1 R:R, TP2 = 1:2 R:R (fixed).

**Problem:**
- In strong EXPANSION regimes, 1:2 leaves money on the table
- In ROTATION/MANIPULATION, 1:1 may not even be reachable

**Enhancement:**
- EXPANSION regime → TP1: 1:1.5, TP2: 1:3
- NORMAL regime → keep current 1:1, 1:2
- ROTATION regime → TP1: 1:0.8, TP2: 1:1.5 (faster exits)
- LIA TP2 override stays in effect on top of regime adjustments

**Expected benefit:** Captures more profit in trending markets, exits faster in choppy
**Impact:** HIGH
**Complexity:** Medium

---

### H6. No Entry Quality Score

**Current behavior:** All triggers fire immediately when their condition is met. No multi-factor confirmation.

**Problem:**
- A SWEEP_REJECTION with no displacement is weaker than one with strong displacement
- A BOS_RETEST with deep retest is weaker than shallow retest
- Currently no scoring system

**Enhancement:**
- Compute entry score (0-100) per trigger:
  - Displacement quality (+15)
  - Volume confirmation (+10)
  - Distance from session VWAP (+10)
  - Time within session (+5 prime, -5 fading)
  - Multi-timeframe agreement (+15)
  - News-free zone (+5)
- Block trades with score < 50
- Adjust risk based on score (50-69: 0.5x, 70-84: 1.0x, 85+: 1.3x)

**Expected benefit:** Significantly improved win rate (likely 5-10% increase)
**Impact:** HIGH
**Complexity:** Medium-Hard

---

## 🟨 MEDIUM IMPACT (Robustness/Reliability)

### M1. Equal H/L Detection Is O(n²)

**Current behavior:** [`trigger_engine.py:367`](apex_ai/scalper/trigger_engine.py:367) compares every pair of bars (50 × 49 / 2 = 1225 comparisons per scan).

**Problem:**
- Inefficient for higher bar counts
- Doesn't cluster nearby levels — returns all matches as separate

**Enhancement:**
- Use sorted-array binning algorithm (O(n log n))
- Cluster close levels into single liquidity zones
- Return weighted center (more bars contributing = stronger level)

**Impact:** MEDIUM (performance)
**Complexity:** Easy-Medium

---

### M2. ATR Calculation Uses Simple Mean

**Current behavior:** [`trigger_engine.py:378`](apex_ai/scalper/trigger_engine.py:378) — ATR is `np.mean(trs[-period:])`.

**Problem:**
- Industry standard is Wilder's smoothing: `ATR_today = ((period-1) × ATR_prev + TR) / period`
- Simple mean is more reactive to outliers (single huge bar skews 14-bar avg)
- Mismatches with TradingView/MT5 ATR indicator → harder to debug visually

**Enhancement:**
- Switch to Wilder's smoothing
- Add EMA-ATR option for more responsive readings
- Cache ATR per timeframe per cycle (don't recompute multiple times)

**Impact:** MEDIUM (accuracy)
**Complexity:** Easy

---

### M3. News Fetcher — Single Source of Failure

**Current behavior:** Only fetches from `nfs.faireconomy.media`. No fallback.

**Problem:**
- If FairEconomy is down or changes URL, news filter goes dark
- File preservation logic helps but doesn't guarantee fresh data

**Enhancement:**
- Add fallback sources:
  - Primary: FairEconomy JSON
  - Secondary: investing.com calendar (HTML scrape)
  - Tertiary: forexfactory.com XML feed
- Try sources in order, use first successful
- Optional: Cross-validate event lists (catch missing events)

**Impact:** MEDIUM (reliability)
**Complexity:** Medium

---

### M4. News Fetcher — No Retry Logic

**Current behavior:** Single attempt, fail = preserve old file.

**Problem:**
- Transient network errors fail unnecessarily
- 6-hour refresh interval means a failed fetch leaves stale data for 6 hours

**Enhancement:**
- Retry 3x with exponential backoff (1s, 5s, 15s)
- After fetch failure, retry on next cycle (not wait 6 hours)
- Track success rate, alert if < 80% over 24h

**Impact:** MEDIUM
**Complexity:** Easy

---

### M5. Pool Adjustment Rules Too Aggressive

**Current behavior:** [`daily_reset.py`](apex_ai/scalper/daily_reset.py) — 3 losing days = 50% pool reduction; 5 winning days = 10% increase.

**Problem:**
- 3 random losing days in a 30-day month is statistically expected
- 50% reduction is steep and slow to recover
- 5-win streak required for tiny 10% increase = capital growth too slow

**Enhancement:**
- Use rolling 5-day P&L instead of consecutive day rule
- Smooth adjustments:
  - 5-day P&L < -10% pool → reduce by 25% (was 50%)
  - 5-day P&L < -20% pool → reduce by 50%
  - 5-day P&L > +10% pool → increase by 5%
  - 5-day P&L > +20% pool → increase by 10%
- Add monthly cap on increases (max +30%/month)

**Impact:** MEDIUM (capital efficiency)
**Complexity:** Medium

---

### M6. State Machine Lacks Grace Periods

**Current behavior:** State transitions are instantaneous on every cycle evaluation.

**Problem:**
- Brief drawdown spike → PROTECTED → recovery → ACTIVE → another spike → PROTECTED (whipsaw)
- Each transition logged as separate event, polluting logs

**Enhancement:**
- Add minimum dwell time per state (e.g., PROTECTED minimum 10 minutes)
- Hysteresis: enter PROTECTED at 3.0% DD, exit at 2.5% DD
- Log only state changes, not every evaluation

**Impact:** MEDIUM (operational)
**Complexity:** Easy-Medium

---

### M7. SA-CRG Loss Pause Is Too Brief

**Current behavior:** [`sa_crg.py`](apex_ai/scalper/sa_crg.py) — 15-minute pause after 2 consecutive losses.

**Problem:**
- 15 min often shorter than the volatility cluster that caused both losses
- Sequential losses suggest market regime mismatch — needs longer cooldown

**Enhancement:**
- Scaling pause based on consecutive losses:
  - 2 losses → 15 min
  - 3 losses → 30 min
  - 4 losses → 60 min
  - 5 losses → halt for the session
- Reset to 0 only on a winning trade

**Impact:** MEDIUM (loss prevention)
**Complexity:** Easy

---

### M8. No Order Send Retry

**Current behavior:** [`scalper_agent.py:_execute_trade`](apex_ai/scalper_agent.py:400) — single `mt5.order_send()` call. Failure = trade skipped.

**Problem:**
- Requote, slippage rejections, momentary disconnects → missed setup
- No differentiation between recoverable errors and permanent failures

**Enhancement:**
- Retry up to 3x on these retcodes:
  - `TRADE_RETCODE_REQUOTE`
  - `TRADE_RETCODE_PRICE_OFF`
  - `TRADE_RETCODE_TIMEOUT`
- Increase deviation parameter on retry (10 → 20 → 30)
- Log retry attempts and outcomes

**Impact:** MEDIUM (fill rate)
**Complexity:** Easy

---

## 🟩 LOW IMPACT (Nice-to-Have)

### L1. Trade Logger — JSON-Only

**Current behavior:** [`trade_logger.py`](apex_ai/scalper/trade_logger.py) writes to JSON file. Reads/writes whole file every trade.

**Problem:**
- File grows over time, full read/write is wasteful
- Can't query (no SQL)
- No real-time analytics

**Enhancement:**
- Migrate to SQLite (already common in Python projects)
- Append-only writes
- Schema: trades, daily_summaries, state_transitions
- Easy queries for analytics dashboards

**Impact:** LOW (operational)
**Complexity:** Medium

---

### L2. No Real-Time Metrics Dashboard

**Current behavior:** Logs in `scalper_agent.log`. No visualization.

**Problem:**
- Hard to see live P&L trajectory
- Can't visualize state transitions over time
- No alerts on anomalies

**Enhancement:**
- Build minimal Streamlit/Flask dashboard:
  - Current state, pool balance, daily P&L
  - Open positions list
  - Recent trades table
  - Equity curve
  - State distribution pie chart
- Optional: Telegram/Discord alert on state changes

**Impact:** LOW (user experience)
**Complexity:** Medium-Hard

---

### L3. No Paper Trading Mode (Different from Dry-Run)

**Current behavior:** `--dry-run` flag logs signals but doesn't execute.

**Problem:**
- Dry-run doesn't simulate fills/slippage realistically
- No virtual P&L tracking
- Can't compare paper vs. live performance

**Enhancement:**
- Add `--paper-trade` mode:
  - Simulate fills using bid/ask + realistic slippage
  - Track virtual P&L in separate journal (`logs/sa_paper_journal.json`)
  - Identical state machine, identical risk rules
- Allow paper-trade and live to run side-by-side for A/B comparison

**Impact:** LOW (testing)
**Complexity:** Medium

---

### L4. Magic Number Hardcoded

**Current behavior:** `magic = 88880` hardcoded in two places.

**Problem:**
- Can't run multiple SA instances with different magic numbers
- Hard to test parallel strategies

**Enhancement:**
- Make magic number configurable via CLI (`--magic 88880`)
- Default stays 88880 for backward compatibility

**Impact:** LOW (flexibility)
**Complexity:** Trivial

---

### L5. Session Windows Don't Adjust for DST

**Current behavior:** [`session_checker.py`](apex_ai/scalper/session_checker.py) — fixed UTC times.

**Problem:**
- US enters/exits DST at different times than UK/EU
- During transition periods, NY session is offset by 1 hour
- Can miss the actual NY open or trade an hour after the close

**Enhancement:**
- Detect DST status for London (Europe/London tz) and New York (America/New_York tz)
- Adjust session windows dynamically:
  - London Open is always 08:00 local London → varies UTC
  - NY Open is always 09:30 local NY → varies UTC
- Tokyo doesn't observe DST (always 00:00 UTC)

**Impact:** LOW-MEDIUM (~6 weeks per year affected)
**Complexity:** Easy

---

### L6. No Slippage Tracking

**Current behavior:** Logs entry price as the requested price, not actual fill price.

**Problem:**
- Can't measure slippage vs. spread predictions
- Can't tune `max_spread_pips` based on real fills
- Real broker performance is opaque

**Enhancement:**
- After order_send success, fetch actual deal price from history
- Log: requested_price, actual_fill_price, slippage_pips
- Track rolling avg slippage per symbol
- Auto-adjust `max_spread_pips` based on observed slippage

**Impact:** LOW (analytics)
**Complexity:** Easy

---

### L7. Whole_day Fallback Is Confusing

**Current behavior:** [`session_checker.py`](apex_ai/scalper/session_checker.py) — `Whole_day` window 00:00-23:00 catches all hours.

**Problem:**
- Documentation says SA only trades 5 windows
- But Whole_day silently allows 24h trading
- Inconsistency between docs and behavior

**Enhancement (pick one):**
- **Option A:** Remove Whole_day entirely → strict 5-window operation
- **Option B:** Make Whole_day flag-controlled (`--enable-fallback`) with explicit logging
- **Option C:** Rename Whole_day to FALLBACK_TRADING and reduce its priority impact

**Impact:** LOW (clarity)
**Complexity:** Trivial

---

### L8. No Performance Attribution by Session

**Current behavior:** Trade log captures session window but no aggregated session stats.

**Problem:**
- Can't answer "Which session is most profitable?"
- Can't identify weak windows to remove
- Useful insights buried in raw trade log

**Enhancement:**
- Daily journal aggregation by session:
  - Trades per window
  - Win rate per window
  - Net P&L per window
  - Avg duration per window
- Weekly summary report

**Impact:** LOW (analytics)
**Complexity:** Easy

---

## 🟦 ARCHITECTURAL (Future-Looking)

### A1. Single-Threaded Symbol Scanning

**Current behavior:** `for symbol in self.symbols: self._scan_symbol(symbol, ...)`

**Problem:**
- Scanning 4 symbols sequentially takes ~4x time
- During fast moves, last symbol scanned has stale data
- 30-second loop interval limits real-time responsiveness

**Enhancement:**
- ThreadPoolExecutor for parallel symbol scans
- Each thread fetches its own MT5 data (MT5 client is thread-safe)
- Total scan time = max(symbol_scan_times) instead of sum

**Impact:** MEDIUM (latency)
**Complexity:** Medium

---

### A2. No Backtest-Live Code Sharing

**Current behavior:** [`backtest_scalper.py`](apex_ai/backtest_scalper.py) reimplements parts of `scalper_agent.py`.

**Problem:**
- Risk of drift — backtest logic different from live logic
- Bug fixes need to be applied in two places
- Subtle differences invalidate backtest predictions

**Enhancement:**
- Extract pure decision logic into shared module (`scalper/decision_engine.py`)
- Both `scalper_agent.py` (live) and `backtest_scalper.py` import from it
- Same code path → backtest accurately predicts live behavior

**Impact:** HIGH (correctness)
**Complexity:** Hard

---

### A3. No Walk-Forward Optimization

**Current behavior:** Backtest is single-period (30 days flat).

**Problem:**
- Parameters optimized on one period may not work on next
- No way to detect parameter decay
- Single-period backtest = high overfitting risk

**Enhancement:**
- Walk-forward framework:
  - Train period (90 days) → optimize parameters
  - Validate period (30 days) → measure out-of-sample performance
  - Roll forward → repeat
- Identifies parameter stability over time
- Warns when latest validation underperforms training

**Impact:** HIGH (robustness)
**Complexity:** Hard

---

### A4. No Position Reduction at Drawdown

**Current behavior:** Position size = fixed 0.3% of pool, regardless of recent performance.

**Problem:**
- After a series of losses, positions are still full-size
- "Doubling down" mentality — losses accumulate fast

**Enhancement:**
- Dynamic position sizing based on recent equity:
  - Pool down 0-3% → 1.0x risk
  - Pool down 3-5% → 0.7x risk
  - Pool down 5-7% → 0.5x risk
  - Pool down > 7% → halt for the day
- Pool up 0-10% → 1.0x risk
- Pool up 10-20% → 1.1x risk
- Pool up > 20% → consider compounding

**Impact:** HIGH (drawdown protection)
**Complexity:** Easy

---

## 📊 PRIORITY MATRIX

| Priority | Items | Impact | Effort |
|---|---|---|---|
| **P0 — Do First** | C1, C2, C3, H1 | Critical bugs/safety | Medium |
| **P1 — High Value** | H2, H3, H6, A4 | Profitability boost | Medium |
| **P2 — Quality of Life** | M1, M2, M5, M6, M7 | Robustness | Easy |
| **P3 — Future** | A1, A2, A3 | Architectural | Hard |

---

## 💡 SUGGESTED EXECUTION ORDER

**Phase 1 — Safety & Reliability (1-2 weeks)**
1. C1: Implement TP2 as hard MT5 order
2. C2: MT5 reconnection logic
3. C3: State persistence
4. M8: Order send retry

**Phase 2 — Profitability (2-3 weeks)**
5. H1: ATR-based tolerances
6. H3: Confidence-tiered risk
7. A4: Drawdown-based position reduction
8. H2: Volatility regime filter
9. H6: Entry quality score

**Phase 3 — Robustness (1-2 weeks)**
10. M2: Wilder's ATR
11. M5: Smoother pool adjustment
12. M6: State machine grace periods
13. M7: Scaling loss pause

**Phase 4 — Quality of Life (1-2 weeks)**
14. M3, M4: News fetcher fallbacks + retry
15. L5: DST adjustment
16. L7: Resolve Whole_day ambiguity
17. L8: Session attribution

**Phase 5 — Architectural (1-2 months)**
18. A2: Shared backtest/live code
19. A1: Parallel symbol scanning
20. A3: Walk-forward framework

---

## 🎯 EXPECTED COMBINED IMPACT

If P0+P1 implemented:
- **Win rate:** +5-10% (entry quality + better filters)
- **Profit factor:** +0.3-0.5 (regime-aware sizing + correlation check)
- **Max drawdown:** -2-4% (DD-based scaling + state grace periods)
- **Operational uptime:** 95% → 99% (reconnection + state persistence)

**Net effect:** Backtest's 78% return profile likely improves to 95-110% range with reduced drawdown.

---

## ⚠️ NOTES

- All enhancements preserve existing isolation guarantees (capital, agents, magic number)
- No changes break the V1 council gate model
- All changes are testable via existing backtest framework
- Recommend implementing one item at a time with backtest validation between each

---

**End of Recommendations Document**

*Awaiting approval before any code changes.*
