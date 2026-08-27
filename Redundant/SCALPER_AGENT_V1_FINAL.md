# ⚡ SCALPER AGENT (SA) — V1 FINAL SPECIFICATION

> **Complete implementation spec for the Scalper Agent**  
> Standalone micro-scalp module with council consultation, institutional context, and autonomous execution.
> Last updated: 2026-05-09

---

## 🔷 AGENT IDENTITY

**Agent Name:** Scalper Agent (SA)  
**Agent Type:** Autonomous Intra-Session Micro-Scalper  
**Scope:** Price delivery on M5/M1, session volatility, rapid reversals, 2–30 min holding periods  
**Capital:** Isolated pool, completely separate from main system account  
**Process:** Standalone (`scalper_agent.py`), no dependency on `maingpt.py`  
**Magic Number:** 88880 (distinct from main system 12345)  

---

## 🔶 ISOLATION GUARANTEE (NON-NEGOTIABLE)

- **Capital:** SA pool is fixed allocation drawn once at startup. Main account balance **never touched** for sizing.
- **Execution:** SA owns trade entry/exit timing. No interference from main system agents.
- **Logging:** All SA activity isolated to `logs/scalper_log.json` and `logs/scalper_agent.log`
- **Integration Level:** Read-only consultation with core engines (LIA, MSA, RDA, Displacement). No write-back. No veto overrides.
- **Safety Fallback:** If council data is stale/unavailable → SA skips trade (safe default).

---

## 🔷 CAPITAL ALLOCATION & RISK RULES

| Parameter | Rule |
|---|---|
| **SA Pool Size** | Fixed USD amount, set at startup (e.g., $100 USD) |
| **Max Risk Per Trade** | 0.25% – 0.5% of SA pool (default 0.3%) |
| **Max Daily Loss** | 2% of SA pool → auto-shutdown |
| **Max Open Positions** | 2 simultaneously |
| **Main Account DD Protection** | SA halts if main account drawdown > 3% |
| **Lot Size Floor** | Trade rejected if mathematically required lots < broker minimum |

**Example:**
- Account balance: $50,000
- SA pool: $100 (isolated)
- Risk per trade: $0.30 (0.3% of $100)
- Max daily loss: $2.00 (2% of pool)
- SL = 50 pips on XAUUSD → lots sized from $0.30 risk budget only

---

## 🔷 SESSION WINDOWS (UTC)

SA is **active ONLY during designated high-volatility windows**:

| Window | UTC Time | Pakistan Time (PKT) | Priority | Duration | Purpose |
|---|---|---|---|---|---|
| **TOKYO_OPEN** | 00:00 – 02:00 | 05:00 – 07:00 AM | 2 | 120 min | Asia Kill Zone |
| **PRE_LONDON** | 06:30 – 07:00 | 11:30 – 12:00 PM | 2 | 30 min | Early liquidity grab |
| **LONDON_OPEN** | 07:00 – 08:30 | 12:00 – 13:30 PM | 1 | 90 min | Primary scalp window |
| **LONDON_NY** | 12:00 – 13:30 | 17:00 – 18:30 | 1 | 90 min | Highest volatility overlap |
| **NY_LUNCH_REV** | 16:30 – 17:30 | 21:30 – 22:30 PM | 3 | 60 min | Mean reversion scalps |
| **Whole_day** | 00:00 – 23:00 | 05:00 AM – 04:00 AM | 4 | ∞ | Fallback window (lower priority) |

**Trading constraints:**
- Outside these windows → state machine returns `IDLE` → no trades allowed
- **No new trades after 23:00 UTC** (last hour of day excluded)
- **All open trades force-closed at 23:00 UTC** → nothing carries to next day

**Total tradeable time per day:** 390 minutes (6.5 hours) across 5 primary windows

---

## 🔷 PREFERRED INSTRUMENTS

**Primary:**
- XAUUSD (Gold — excellent volatility, tight spreads during sessions)

**Secondary:**
- XAGUSD (Silver)
- USOIL (Crude Oil)
- BTCUSD (Bitcoin)
- EURUSD, GBPUSD, USDJPY (major FX pairs)

**Anti-correlation check:** Before opening a scalp on any symbol, SA verifies the main system is not actively trading that same instrument. Prevents conflicting positions.

---

## 🔷 3-STEP SCALP EXECUTION PIPELINE

### STEP 1: Micro Liquidity Identification (M5)

Scan last 50 M5 candles for:
- **Equal Highs** (±0.03%) → Buy-Side Liquidity (BSL) clusters
- **Equal Lows** (±0.03%) → Sell-Side Liquidity (SSL) clusters
- **Session High/Low** → today's institutional extremes

Output: `MicroLiquidity` object with `nearest_bsl` and `nearest_ssl` levels.

### STEP 2: Trigger Detection + Consultation Gates

Check for **ONE** of four valid triggers:

#### Trigger 1: SWEEP_REJECTION (Confidence: HIGH)
- Price wicks below SSL/above BSL in last 3 M5 bars
- Current candle closes back above SSL (bullish) or below BSL (bearish)
- Entry: current price | SL: below wick − ATR×0.2 | TP1/TP2: 1:1 / 1:2 R:R

#### Trigger 2: FVG_FILL (Confidence: MEDIUM)
- Scans last 20 M1 candles for Fair Value Gap (bullish: gap between candle[i-2].high and candle[i].low)
- Current price inside FVG zone, closes in reversal direction
- Entry: current price | SL: FVG edge ± ATR×0.3 | TP1/TP2: 1:1 / 1:2 R:R

#### Trigger 3: BOS_RETEST (Confidence: MEDIUM)
- Scans last 20 M5 bars for recent swing high broken
- Previous candle closed above swing high (BOS), current price pulled back within 0.1% of that level (retest)
- **Requires Gate 2 approval** — must have institutional displacement (momentum ≥ 0.65)
- Entry: current price | SL: recent 3-bar low − ATR×0.2 | TP1/TP2: 1:1 / 1:2 R:R

#### Trigger 4: JUDAS_SWING (Confidence: HIGH)
- First 3 M5 candles from session open define initial move range
- Price rallied/dropped initially but now 50%+ faded and closed back through session open
- Entry: current price | SL: session high/low ± ATR×0.3 | TP1/TP2: 1:1 / 1:2 R:R

**If none detected → SKIP symbol, move to next**

### GATE 0: HTF Bias Filter (M15)

**New rule (V1 Final):** HTF trend alignment is **mandatory**, not optional.

Compute M15 10-period EMA with 0.05% buffer zone:
- If price > EMA + buffer → M15 bias is **BULLISH**
- If price < EMA − buffer → M15 bias is **BEARISH**
- If within buffer → M15 bias is **NEUTRAL**

**Decision:**
- NEUTRAL bias → **BLOCK all triggers** (no dominant trend)
- Trigger direction ≠ M15 bias → **BLOCK trade** (counter-trend rejected)
- Trigger direction = M15 bias → **ALLOW** (proceed to next gates)

### GATE 1: RDA Regime Filter (V1 Upgrade — Institutional Context)

Consult `intelligence/regime_engine.py` via SA Consultant (read-only):

| Regime | Decision |
|---|---|
| `EXPANSION` | Allow all triggers |
| `ROTATION` | Allow only SWEEP_REJECTION (mean reversion) |
| `MANIPULATION` | Allow only SWEEP_REJECTION |
| `TRANSITION` | Block all |
| `STRESS` | Block all |

**If blocked → log reason and SKIP**

### GATE 2: Displacement Validation (V1 Upgrade — Trigger Quality)

**For BOS_RETEST only:** Consult `core/displacement_engine.py`
- Check: `displacement.is_displaced == True` AND `momentum_score >= 0.65` AND FVG present
- If not met → **BLOCK** (fakeout rejection, lacks institutional force)
- **If passed → proceed**

### GATE 3: LIA TP2 Override (V1 Upgrade — Target Alignment)

Consult `core/liquidity_engine.py` for H1 macro pools:
- Identify `nearest_bsl` (for BUY trades) or `nearest_ssl` (for SELL trades)
- Proximity rule: if macro pool within 1.5× TP1 distance
- **If found & better than fixed 1:2 R:R TP2 → realign TP2 to macro pool level**
- Else: keep fixed 1:2 R:R TP2

**Consultation Quality Checks:**
- Snapshot freshness: ≤ 60 seconds old
- Latency: ≤ 200 ms
- Data availability: if stale/unavailable → **SKIP trade** (safe fallback)

### STEP 3: Entry Validation

Before execute:
- Spread check: within per-symbol max (XAUUSD ≤ 3.0 pips)
- News blackout: **30 min before + 15 min after** HIGH-impact events
- SA-CRG gate: 5 mandatory risk checks

---

## 🔷 SA RISK GOVERNOR (SA-CRG) — 5 MANDATORY CHECKS

**All 5 must pass. If ANY fails → BLOCK TRADE.**

| # | Check | Block Condition |
|---|---|---|
| 1 | Daily loss limit | SA daily loss ≥ 2% of pool |
| 2 | Main account drawdown | Main account DD > 3% |
| 3 | Max positions | Open SA positions ≥ 2 |
| 4 | Spread acceptable | Current spread > max spread pips |
| 5 | Loss pause elapsed | ≥2 consecutive losses without 15-min pause |

---

## 🔷 NEWS FILTER — AUTOMATED FOREX FACTORY INTEGRATION (V1 Final)

**Source:** Forex Factory weekly calendar (FairEconomy JSON mirror)  
**URL:** `https://nfs.faireconomy.media/ff_calendar_thisweek.json`  
**Update cadence:** Startup + every 6 hours during runtime  

### Behavior
- Fetches HIGH-impact events automatically
- Maps each event's currency code → list of affected MT5 instruments
- Writes `news_events.json` (sorted by event time)
- **On fetch failure:** preserves existing file (no wipe)

### Blackout Window
- **30 minutes BEFORE** event time
- **15 minutes AFTER** event time
- Only HIGH-impact events trigger blackout
- Symbol-specific: only blocks instruments listed for that event

### Example
```
Event: "USD Non-Farm Payrolls" at 12:30 UTC
Blackout: 12:00 UTC → 12:45 UTC
Symbols blocked: XAUUSD, XAGUSD, USOIL, BTCUSD, EURUSD, GBPUSD, USDJPY...
```

---

## 🔷 BEHAVIORAL STATE MACHINE

**State priority:** `PROTECTED > HALTED > PAUSED > IDLE > ACTIVE`

| State | Condition | Trading Allowed |
|---|---|---|
| **PROTECTED** | Main account drawdown > 3% | ❌ No |
| **HALTED** | Daily loss limit hit (2% of pool) | ❌ No |
| **PAUSED** | 2+ consecutive losses, 15-min cooldown active | ❌ No |
| **IDLE** | Outside session window or no triggers | ❌ No |
| **ACTIVE** | Inside session window, all checks clear, triggers available | ✅ Yes |

**Transitions are deterministic** — logged with timestamp and reason.

---

## 🔷 TRADE EXECUTION

### Entry
- Market order via MT5 (`ORDER_TYPE_BUY` / `ORDER_TYPE_SELL`)
- SL and TP1 set on entry (hard MT5 levels)
- TP2 tracked in memory (manual management)
- Magic number: 88880 (SA-exclusive)
- Deviation: 10 pips
- Filling: IOC (Immediate or Cancel)

### Position Monitoring
- **120-minute hard timeout:** if trade open ≥ 120 min → force close at market
- **TP1 partial:** if TP1 hit → close 50–70% of position, trail remainder to TP2
- **TP2 tail:** hold remainder until TP2 or timeout

### Closing Codes
- `WIN_TP1` — TP1 hit, partial taken
- `WIN_TP2` — TP2 hit, full position closed
- `LOSS` — SL hit
- `TIMEOUT` — 120-minute hard timeout elapsed
- `EOD_CLOSE` — forced close at 23:00 UTC (no position carries to next day)

---

## 🔷 LOT SIZING FORMULA

```
risk_usd = current_pool × risk_pct
sl_pips = abs(entry_price - stop_loss) / point / 10
pip_value = trade_tick_value × 10

lots = risk_usd / (sl_pips × pip_value)

# Validate
if lots < broker_volume_min:
    REJECT TRADE  # Undercapitalized, would risk more than budget
    
# Constrain
lots = min(broker_volume_max, lots)
lots = round(lots / broker_volume_step) × broker_volume_step
```

**Trade rejected if:** mathematically required lots < broker minimum

---

## 🔷 DAILY RESET PROTOCOL

**Triggered at UTC midnight (daily_reset_date rollover):**

1. **Close all open positions** (via MT5, record P&L)
2. **Log daily P&L** to `logs/sa_daily_journal.json`
3. **Reset daily counters** (daily_pnl = 0, trade_count = 0)
4. **Pool adjustment rules:**
   - 3 consecutive losing days → SA pool **reduced by 50%**
   - 5 consecutive winning days → SA pool **increased by 10%**
5. **Reset state machine** to IDLE
6. **Clear SA-CRG pause timer**
7. **Clear session open prices**

---

## 🔷 END-OF-DAY ENFORCEMENT (V1 Final)

**At 23:00 UTC (1 hour before next trading day):**
- **No new trade entries allowed** (Whole_day window shrinks to 00:00–23:00)
- **All open SA trades force-closed** at market price
- P&L registered, logged as `EOD_CLOSE` outcome
- State machine automatically moves to IDLE at 23:00

---

## 🔷 TRADE LOGGING

Every SA trade logged to `logs/scalper_log.json` (JSON format) + `logs/scalper_agent.log` (text):

```
[SA TRADE LOG]
Instrument     : XAUUSD
Session Window : LONDON_OPEN
Trigger Type   : SWEEP_REJECTION
Entry Price    : 2345.12000
Stop Loss      : 2344.38000
TP1 Target     : 2345.86000
TP2 Target     : 2346.60000
Position Size  : 0.05 lots (SA pool only)
SA Pool Risk % : 0.30%
Main Acct Risk : 0% (isolated)
Trade Duration : 23m
Result         : WIN_TP1
SA Pool P&L    : +$1.47
SA State After : ACTIVE
```

---

## 🔷 COMMAND-LINE STARTUP

```bash
python scalper_agent.py \
    --pool 100                  # SA pool size (USD) \
    --risk 0.003                # Risk per trade (0.3% of pool) \
    --symbols XAUUSD,XAGUSD,USOIL,BTCUSD \
    --interval 30               # Scan interval (seconds) \
    --loss-limit 50.0           # Hard daily loss cap (USD) \
    --dry-run                   # (optional) log signals only, don't execute
```

**Startup checks:**
- MT5 connection validated
- SA pool ≤ account balance
- News calendar fetched from Forex Factory (initial population)
- Trade Guardian Agent auto-launched as subprocess

---

## 🔷 MAIN DECISION FLOW (Every 30 Seconds)

```
CYCLE START
  ├─ Date rollover? → daily reset
  ├─ 23:00 UTC? → force close all open trades (EOD enforcement)
  ├─ News refresh due (6 hours)? → fetch FF calendar
  ├─ Monitor open trades (120-min timeout, TP1 partial)
  ├─ Session state? (in window?)
  ├─ Main account drawdown?
  ├─ Evaluate → state machine → PROTECTED/HALTED/PAUSED/IDLE/ACTIVE
  │
  └─ IF state == ACTIVE:
      └─ FOR each symbol:
          ├─ Fetch M5 + M1 + M15 data
          ├─ Step 1: Map micro liquidity
          ├─ Step 2: Detect trigger
          ├─ Gate 0: HTF bias filter (M15)
          ├─ Gate 1: RDA regime filter
          ├─ Gate 2: Displacement validation (BOS only)
          ├─ Gate 3: LIA TP2 override
          ├─ Step 3: Validate entry (spread, news, CRG)
          ├─ Calculate lots
          ├─ EXECUTE (or DRY RUN)
          └─ Log trade
          
WAIT 30 seconds
```

---

## 🔷 WHAT SA NEVER DOES

- ❌ Never trades against active main system position on same symbol
- ❌ Never uses non-SA capital for sizing
- ❌ Never overrides council gates or SA-CRG blocks
- ❌ Never holds trade > 120 minutes (hard timeout)
- ❌ Never adds to losing position (no pyramiding)
- ❌ Never trades during 30-min pre / 15-min post news blackout
- ❌ Never opens new trades after 23:00 UTC
- ❌ Never carries open position into next day (forced close at 23:00)
- ❌ Never operates in PROTECTED/HALTED/PAUSED states
- ❌ Never widens stop loss under any circumstance

---

## 🔷 INTEGRATION SUMMARY

```
MAIN SYSTEM (APEX v11)        SCALPER AGENT (SA v1)
─────────────────────         ───────────────────
LIA Engine        READ-ONLY →  TP2 macro alignment
RDA Regime        READ-ONLY →  Regime gate
Displacement      READ-ONLY →  BOS quality gate
Structure         READ-ONLY →  Signal context
MT5 Terminal       ISOLATED  →  SA magic 88880
News Filter       AUTO-FETCH →  Forex Factory weekly
CRG (main)        READ ONLY  →  DD protection (3% threshold)

Capital Pool A (main)         Capital Pool B (SA)
      ↓                             ↓
   Main Trades                  Scalp Trades
   (separate P&L)               (separate P&L)
       \_____________________ account-level protection ___/
```

---

## 🔷 PERFORMANCE EXPECTATIONS

**Design target:** 6–12 micro trades per active session, 70%+ win rate on TP1, trailing to TP2 for extended moves.

**Realistic monthly return** on $100 SA pool with 0.3% risk: 8–15% (conservative), 15–25% (strong regime).

**Risk:** Account-level protection at 3% DD, daily reset discipline, and council gates reduce ruin probability to near-zero.

---

## 🔷 FINAL RULE

> SA exists to capture micro inefficiencies during high-volatility institutional windows.  
> It is fast, disciplined, council-governed, and completely isolated.  
> If SA underperforms, it auto-halts before system damage.  
> Account integrity is always preserved.

**Quick entry. Quick exit. No baggage. No interference.**

---

*SCALPER AGENT V1 FINAL SPECIFICATION*  
*Last updated: 2026-05-09*  
*Supersedes all prior SCALPER_AGENT.md and SCALPER_UPGRADE_V1.md documents*
