# APEX AI Trading System — Enhancement Log

## Trade Guardian Agent (TGA): Hedge Management Module v2

---

### Overview

This document specifies two complementary hedge exit strategies for the Trade Guardian Agent. Both operate on the same symbol-grouping and net profit calculation foundation, but differ in *how* they unwind the hedge:

| Mode | Strategy | Best For |
|---|---|---|
| `SIMULTANEOUS` | Close all legs at once when basket net profit hits target | Ranging/choppy markets, conservative accounts |
| `SEQUENTIAL` | Close the winning leg first, let the losing leg ride, then close it at profit | Trending markets, maximising extraction from both directions |

The mode is controlled by `hedge_exit_mode` in `TGAConfig`. Both modes share the same detection, locking, verification, and retry logic.

---

### Section 1: Shared Foundation

#### 1.1 Symbol grouping

On each TGA tick cycle, group all open positions by symbol. This runs regardless of which exit mode is active.

#### 1.2 Hedge detection

A symbol qualifies for basket management if it has **at least one BUY and at least one SELL** position open simultaneously. Partial hedges (e.g. 2 BUY vs 1 SELL) are included — net directional exposure is tracked, not ignored.

A symbol must also have a total position count >= `hedge_min_legs` to qualify (prevents single-position false positives during detection lag).

#### 1.3 Net profit calculation

Calculate the **combined floating net profit** of the basket on every tick:

```
basket_net_profit = Σ(floating_pnl) + Σ(swap) + Σ(commission)
```

for all positions in the basket, converted to the account deposit currency (USD). Do not cache this value between ticks — swap values update at broker rollover and must be recalculated fresh.

#### 1.4 Basket lock

When a close condition is triggered (target hit in either mode), the basket is **immediately locked**:

- Suppress all individual trailing stop logic for every position in this basket
- Suppress all individual early-close triggers for this basket
- Block new manual or automated orders on this symbol while the lock is active

The lock is released only when: (a) all positions confirm closed, or (b) all retries are exhausted and the lock is explicitly released with an alert.

#### 1.5 Close verification and retry

After issuing any close command:

1. Confirm all targeted positions show status `CLOSED`
2. If any position remains open (partial fill, requote, timeout), wait `hedge_close_retry_delay_ms` and retry
3. Retry up to `hedge_close_retry_limit` times
4. If retries exhausted: **unlock the basket**, fire an alert via the existing notification channel, and resume standard TGA leg management on all remaining positions

#### 1.6 Logging

Log each basket event with full context:

- Event type: `TARGET_REACHED`, `LOCK_APPLIED`, `LEG_CLOSED`, `BASKET_CLOSED`, `RETRY_ATTEMPT`, `RETRY_EXHAUSTED`, `LOCK_RELEASED`
- Symbol and position ticket IDs involved
- Net profit at the moment of the event
- Timestamp
- Exit mode active at time of event

---

### Section 2: Mode A — Simultaneous Exit

**Use when:** The market is ranging or choppy, or when the priority is a clean, safe exit with no residual exposure.

#### 2.1 Trigger condition

```
basket_net_profit >= hedge_net_profit_target_usd
```

#### 2.2 Execution

1. Re-validate `basket_net_profit >= hedge_net_profit_target_usd` at the exact moment of the close command (price may have moved since detection)
2. If re-validation fails (profit dropped below target), release the lock and continue monitoring
3. If re-validation passes, issue a single market execution command to close **all legs simultaneously**
4. Do not close legs sequentially — sequential closure creates a window of directional exposure between fills

#### 2.3 New positions opened mid-basket

If a new position is opened on a locked symbol:

- If `hedge_include_new_legs = true`: add it to the basket; do not close until it is accounted for
- If `hedge_include_new_legs = false`: flag an alert and exclude it from the basket close; it remains under standard TGA management

---

### Section 3: Mode B — Sequential Unwind ("Lock and Ride")

**Use when:** The market is trending and there is value to be extracted from both directions of a move.

**Core concept:** Close the currently profitable ("dominant") leg to lock in gains, then let the losing ("trailing") leg run until *it* becomes profitable. Net result: profit captured from both directions of the move rather than waiting for the hedge to simultaneously reach a combined target.

#### 3.1 Phase 1 — Dominant leg identification and close

**Trigger condition:**

```
dominant_leg_floating_profit >= dominant_leg_profit_target_usd
AND trend confirmation signal fires (see 3.3)
```

When both conditions are met:

1. Identify the dominant leg: the position (or group of same-direction positions) whose floating profit exceeds `dominant_leg_profit_target_usd`
2. Apply basket lock
3. Close the dominant leg(s) only — leave the trailing leg(s) open
4. Record `phase1_locked_profit` = net realised profit of the closed dominant leg(s) after commission and swap
5. Transition basket state to `PHASE_2_ACTIVE`

#### 3.2 Phase 2 — Trailing leg management and close

With the dominant leg closed, the trailing leg now runs directionally. The TGA monitors it for any of the following exit conditions (whichever triggers first):

**Exit condition A — Trailing leg profit target:**
```
trailing_leg_floating_profit >= trailing_leg_profit_target_usd
```

**Exit condition B — Combined net minimum:**
```
phase1_locked_profit + trailing_leg_floating_profit >= combined_net_minimum_usd
```
This ensures even a partial Phase 2 recovery still exits in overall profit.

**Exit condition C — Maximum drawdown safeguard:**
```
trailing_leg_floating_profit <= -trailing_leg_max_drawdown_usd
```
If the trailing leg keeps losing beyond this threshold, cut the loss. The account still profits overall if `phase1_locked_profit > trailing_leg_max_drawdown_usd`.

**Exit condition D — Time limit:**
```
time_since_phase1_close >= trailing_leg_time_limit_hours
```
If Phase 2 has not resolved within the time limit, evaluate the current combined net:
- If `phase1_locked_profit + trailing_leg_floating_profit >= 0`: close the trailing leg (exit at breakeven or better)
- If combined net is negative: fire an alert and let the operator decide, or close automatically if `trailing_leg_force_close_on_timeout = true`

**Exit condition E — Reversal signal (re-entry prevention):**

If `reversal_confirmation_signal` detects that the market is likely to reverse back against the trailing leg's direction, close the trailing leg immediately regardless of current profit/loss, provided combined net >= 0.

When any exit condition triggers:

1. Apply basket lock to the trailing leg
2. Close the trailing leg at market
3. Log final combined net: `phase1_locked_profit + trailing_leg_net_realised`
4. Release basket lock and mark symbol as fully unwound

#### 3.3 Reversal confirmation signals

The `reversal_confirmation_signal` parameter accepts one or more of the following. When multiple are specified, **all must fire** before Phase 1 triggers (AND logic), unless `reversal_signal_mode = ANY` is set.

| Signal | Description |
|---|---|
| `MA_CROSS` | Fast MA crosses slow MA in the direction of the dominant leg |
| `RSI_LEVEL` | RSI crosses `rsi_overbought_level` (for SELL dominant) or `rsi_oversold_level` (for BUY dominant) |
| `FIXED_PIPS` | Dominant leg profit exceeds `dominant_leg_profit_target_usd` by at least `reversal_pip_buffer` pips |
| `PRICE_ACTION` | Price closes beyond a recent swing high/low (lookback = `pa_lookback_bars` bars) |
| `COMBINED` | All of the above must fire |

A confirmation delay of `reversal_confirmation_bars` bars can be set to prevent false triggers on wicks.

---

### Section 4: Configuration Reference

Add all of the following parameters to `TGAConfig`:

#### 4.1 Master switches

| Parameter | Type | Default | Description |
|---|---|---|---|
| `enable_hedge_management` | `bool` | `false` | Master switch for the entire Hedge Management Module |
| `hedge_exit_mode` | `enum` | `SIMULTANEOUS` | `SIMULTANEOUS` or `SEQUENTIAL` |

#### 4.2 Shared parameters (both modes)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `hedge_min_legs` | `int` | `2` | Minimum total positions to qualify as a basket |
| `hedge_close_retry_limit` | `int` | `3` | Max retry attempts on failed close |
| `hedge_close_retry_delay_ms` | `int` | `500` | Delay between retry attempts (ms) |
| `hedge_alert_on_failure` | `bool` | `true` | Fire alert if basket close fails after all retries |
| `hedge_include_new_legs` | `bool` | `false` | Include positions opened after basket lock in the close |

#### 4.3 Simultaneous exit parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `hedge_net_profit_target_usd` | `float` | `10.00` | Combined basket net profit (USD) required to trigger simultaneous close |

#### 4.4 Sequential unwind parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `dominant_leg_profit_target_usd` | `float` | `15.00` | Floating profit on the dominant leg to trigger Phase 1 close |
| `trailing_leg_profit_target_usd` | `float` | `10.00` | Floating profit on the trailing leg to trigger Phase 2 close |
| `combined_net_minimum_usd` | `float` | `5.00` | Close Phase 2 if Phase 1 locked profit + Phase 2 floating >= this value |
| `trailing_leg_max_drawdown_usd` | `float` | `20.00` | Force-close Phase 2 if its floating loss exceeds this value |
| `trailing_leg_time_limit_hours` | `int` | `24` | Max hours to hold Phase 2 before forced evaluation |
| `trailing_leg_force_close_on_timeout` | `bool` | `false` | If true, auto-close Phase 2 at timeout regardless of P&L |
| `reversal_confirmation_signal` | `enum[]` | `[MA_CROSS]` | Signal(s) required to confirm Phase 1 trigger |
| `reversal_signal_mode` | `enum` | `ALL` | `ALL` (AND logic) or `ANY` (OR logic) across selected signals |
| `reversal_confirmation_bars` | `int` | `1` | Number of bars signal must persist before acting |
| `reversal_pip_buffer` | `float` | `0.0` | Additional pip buffer beyond target before Phase 1 fires (for `FIXED_PIPS` signal) |
| `rsi_overbought_level` | `int` | `70` | RSI threshold for SELL dominant Phase 1 trigger |
| `rsi_oversold_level` | `int` | `30` | RSI threshold for BUY dominant Phase 1 trigger |
| `pa_lookback_bars` | `int` | `20` | Lookback bars for `PRICE_ACTION` swing high/low detection |

---

### Section 5: Edge Cases

| Scenario | Handling |
|---|---|
| Price moves against dominant leg between detection and close | Re-validate at execution time; abort and re-enter monitoring if target no longer met |
| Swap update at rollover recalculates basket below target | Normal — recalculation is per-tick; no special handling needed |
| Multiple symbols in hedge simultaneously | Each symbol's basket is evaluated fully independently |
| Phase 1 closes but broker rejects Phase 2 position (margin call, restriction) | Log error, release lock, fire alert; do not retry Phase 2 automatically |
| Sequential mode: dominant leg is tied (equal profit on BUY and SELL) | Close the leg with the longer open time first; log tie-break reason |
| Account equity drops below broker margin requirement mid-Phase 2 | `trailing_leg_max_drawdown_usd` should prevent this; add an equity floor check if required |
| `hedge_exit_mode` changed while a basket is already in `PHASE_2_ACTIVE` | Do not interrupt — complete the active Phase 2 under the original mode; apply new mode to subsequent baskets only |

---

### Section 6: Rationale

**Why simultaneous mode exists:** Safest exit for accounts in a fully locked hedge. Eliminates directional risk entirely at the cost of waiting for both legs to reach a combined profit threshold.

**Why sequential mode adds value:** In a trending market, the losing leg will recover if the trend continues. Rather than waiting indefinitely for the combined net to reach a target, Phase 1 locks in the winning leg's profit immediately, then Phase 2 extracts further profit from the trend continuation. The combined net minimum and max drawdown safeguards ensure the account cannot give back Phase 1 gains entirely.

**Why both modes share the same lock and retry logic:** Execution risk (partial fills, requotes, broker-side delays) is identical regardless of exit strategy. Centralising this logic prevents divergence between modes.

---

*Document version: 2.2 — added DXY Inter-market Analysis*

---

## Session Engine: London/NY Overlap Optimization

### Overview
The current `SessionEngine` defines trading windows in discrete blocks (London Kill Zone, NY Kill Zone, etc.). However, it does not explicitly account for the **London/NY Overlap** (typically 12:00 – 15:00 UTC), which is the most liquid and volatile period of the trading day.

### Proposed Feature
Introduce an explicit `OVERLAP` session type with dedicated weighting and risk parameters.

### Implementation Details
1. **Overlap Definition**: Add a new entry to `SESSIONS` in `session_engine.py` covering the 12:00 – 14:00 UTC (or 15:00 UTC) window.
2. **Dynamic Weighting**: Assign a "Prime Plus" weight (e.g., 1.3x) to this period to reflect the combined institutional liquidity of both major hubs.
3. **Consensus Sensitivity**: Allow the `FundOS` to require higher agent consensus or allow slightly wider spreads during this high-velocity window.
4. **Agent Logic**: Update agents (like LIA and MSA) to be "Overlap Aware," potentially adjusting their swing lookbacks or volume thresholds during this period.

### Rationale
By explicitly defining the overlap, the system can prioritize high-probability setups that occur when global liquidity is at its peak, while treating the pre-NY open and post-London close periods with more appropriate individual session logic.

---

## Intelligence: DXY Inter-market Analysis & SMT Divergence

### Overview
The system currently analyzes XAUUSD, XAGUSD, and USOIL as standalone instruments. While it uses internal correlation between Gold and Silver, it lacks a direct feed of the **US Dollar Index (DXY)**, which is the primary driver of institutional liquidity sweeps in the metals market.

### Proposed Feature
Integrate DXY as a non-trading "Reference Symbol" to enable Inter-market Analysis (IMA) and SMT (Smart Money Tool) Divergence detection.

### Implementation Details
1. **DXY Ingestion**: Add `DXY` (or a broker-specific equivalent like `USDX`) to the configuration as a reference-only symbol.
2. **SMT Divergence Engine**: Implement a module within the `LiquidityEngine` to detect crack-and-hold patterns between DXY and XAUUSD.
    - *Example*: If DXY makes a **Higher High** while XAUUSD makes a **Higher Low** (failing to make a Lower Low), this signals high-probability institutional accumulation for a Buy.
3. **Bias Filter**: Update the `RegimeDetectionAgent` (RDA) to use DXY trend direction as a master filter for all USD-paired assets.

### Rationale
Direct DXY integration allows the bot to identify "True" vs "Fake" price movements. It provides a definitive way to identify institutional footprints before they appear on the target instrument's chart, significantly increasing the win-rate of the Judas Swing and Silver Bullet strategies.