# APEX AI Trading System — Complete System Blueprint

> **Version:** v12 (M15/M5 migration + cooldown + expectancy fixes)
> **Runtime:** Python 3.14 + MetaTrader 5
> **Broker:** Exness MT5 Trial (Demo)
> **Last Updated:** 2026-08-22 17:47 UTC

---

## 1. System Architecture Overview

APEX AI is a fully autonomous, multi-agent institutional trading system built on ICT/SMC (Smart Money Concepts) methodology. It operates two independent capital pools concurrently:

```
┌─────────────────────────────────────────────────────────────────┐
│                    APEX AI TRADING SYSTEM                        │
│                                                                 │
│  ┌──────────────────────┐    ┌───────────────────────────────┐  │
│  │  CAPITAL POOL A      │    │  CAPITAL POOL B (ISOLATED)    │  │
│  │  maingpt.py          │    │  scalper_agent.py (SA-V2)     │  │
│  │  Swing/Intraday      │    │  Micro-Scalping M1/M5        │  │
│  │  7-Agent AI Council  │    │  ReadOnly Consultation        │  │
│  │  5-Gate Fund OS      │    │  Isolated $500 Pool           │  │
│  │  M15 Execution TF    │    │  2% Risk Per Trade            │  │
│  │  Magic: 12345        │    │  Magic: 88880                 │  │
│  └──────────┬───────────┘    └───────────────────────────────┘  │
│             │                                                    │
│  ┌──────────▼───────────────────────────────────────────────┐   │
│  │  TRADE GUARDIAN AGENT (TGA) — trade_guardian_agent.py     │   │
│  │  Universal Position Manager (NEVER opens trades)          │   │
│  │  4-Stage Trailing SL | Dynamic TP | Early Close Engine    │   │
│  │  Auto-spawned by maingpt.py | Magic: 99990                │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Directory Structure

```
apex_ai/
├── maingpt.py                  # Main system entry point (Capital Pool A)
├── scalper_agent.py            # SA-V2 Scalper Agent (Capital Pool B)
├── trade_guardian_agent.py     # TGA — universal position manager
├── config.json                 # Main system configuration
├── .env                        # MT5 credentials (MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_MODE)
├── requirements.txt            # Python dependencies
├── backtest_scalper.py         # Historical backtester for SA-V2
├── analyze_backtest_results.py # Parses backtest JSON → detailed report
├── plot_backtest_results.py    # Generates equity curve charts
├── check_history.py            # Quick MT5 trade history viewer
├── check_trade_status.py       # Check open position status
├── get_agent_pnl.py            # PnL breakdown by agent magic number
├── pnl_statement.py            # Full PnL statement generator
├── export_history_md.py        # Export trade history to markdown
├── news_events.json            # Cached Forex Factory HIGH-impact events
│
├── agents/                     # v9 Multi-Agent AI Council (used by maingpt.py)
│   ├── agent_base.py           #   BaseAgent + AgentVote dataclass
│   ├── lia.py                  #   Liquidity Intelligence Agent
│   ├── msa.py                  #   Market Structure Agent
│   ├── rda.py                  #   Regime Detection Agent
│   ├── caia.py                 #   Cross-Asset Intelligence Agent
│   ├── see.py                  #   Strategy Evolution Engine
│   ├── ea.py                   #   Execution Agent
│   └── crg.py                  #   Capital Risk Governor
│
├── core/                       # v1 Core Analysis Engines (shared by both pools)
│   ├── liquidity_engine.py     #   BSL/SSL pool detection, premium/discount zones
│   ├── manipulation_engine.py  #   Institutional sweep detection with per-symbol calibration
│   ├── structure_engine.py     #   Swing HH/HL/LH/LL trend detection, BOS/CHoCH
│   ├── displacement_engine.py  #   Impulsive move detection, FVG mapping, momentum scoring
│   ├── execution_models.py     #   Trade setup generation (RETURN/CONTINUATION models)
│   ├── news_fetcher.py         #   Forex Factory scraper for HIGH-impact events
│   └── news_guard.py           #   News blackout enforcement (30min before / 15min after)
│
├── intelligence/               # v4-v5 Intelligence Layer
│   ├── probabilistic_engine.py #   Bayesian bias calculation (bullish/bearish/ranging %)
│   ├── regime_engine.py        #   Market regime classifier (EXPANSION/MANIPULATION/ROTATION/TRANSITION)
│   ├── order_flow_engine.py    #   Volume delta and order flow analysis
│   ├── portfolio_engine.py     #   Portfolio exposure and correlation tracking
│   ├── risk_engine.py          #   Position sizing by clarity level and governance mode
│   └── adaptive_memory.py      #   Strategy performance tracking for SEE feedback loop
│
├── market/                     # Market Data Layer
│   ├── mt5_connector.py        #   MT5 connection, OHLCV fetching, order execution
│   ├── multi_timeframe.py      #   D1/H4/H1/M15/M5 confluence scoring
│   └── session_engine.py       #   ICT Kill Zone session detection (London/NY/Asia)
│
├── fund/                       # v10-v11 Hedge Fund Operating System
│   ├── hedge_fund_os.py        #   5-Gate trade approval pipeline
│   ├── simulation_engine.py    #   Monte Carlo simulation (500 runs per setup)
│   ├── compounding_engine.py   #   Equity tracking, drawdown, growth metrics
│   ├── capital_governance.py   #   Dynamic governance modes (AGGRESSIVE→PRESERVATION)
│   └── meta_fund.py            #   Sub-fund rebalancing based on adaptive memory
│
├── scalper/                    # SA-V2 Scalper-Specific Modules
│   ├── trigger_engine.py       #   M1/M5 trigger models (Sweep, FVG, BOS, Judas, CRT)
│   ├── sa_consultant.py        #   ReadOnly consultation layer (reads core engines, never trades)
│   ├── sa_crg.py               #   Scalper-specific Capital Risk Governor
│   ├── capital_pool.py         #   Isolated capital pool with daily loss limit
│   ├── session_checker.py      #   Scalper session windows (London/NY/Asia kill zones)
│   ├── behavior_state.py       #   Deterministic state machine (IDLE/ACTIVE/PAUSED/COOLDOWN)
│   ├── trade_logger.py         #   JSON trade log writer
│   └── daily_reset.py          #   End-of-day forced close + daily PnL reset
│
├── dashboard/
│   └── terminal_ui.py          #   Rich-powered live terminal dashboard
│
├── logs/                       # Runtime logs directory
└── data/                       # Runtime data directory
```

---

## 3. Entry Points & Launch Commands

### 3.1 Main System (Capital Pool A)
```powershell
cd apex_ai
python maingpt.py                # Live mode (executes real trades)
python maingpt.py --dry-run      # Paper trading (no real orders)
python maingpt.py --config alt.json  # Use alternate config
```
- Reads `config.json` for symbols, risk, sessions, governance thresholds
- Auto-spawns `trade_guardian_agent.py` as a subprocess on startup
- Scans every 60 seconds (configurable via `analysis_interval_seconds`)
- Uses Magic Number `12345` for all orders

### 3.2 Scalper Agent (Capital Pool B) — SA-V2
```powershell
cd apex_ai
python scalper_agent.py --pool 500 --risk 0.02 --symbols USOIL,XAUUSD,XAGUSD --interval 30 --loss-limit 50.0
python scalper_agent.py --pool 500 --risk 0.02 --symbols USOIL,XAUUSD,XAGUSD --interval 30 --loss-limit 50.0 --dry-run

# Cooldown switches (default: on)
#   --no-cooldown              disable entirely
#   --win-cooldown-min 5       break after a win, minutes
#   --loss-cooldown-policy NEXT_UTC_HOUR | FIXED_MINUTES
#   --allow-whole-day          re-enable the 00:00-23:00 catch-all window
#
# Capital allocation
#   --pool-mode RESUME (default)  --pool is the ORIGINAL allocation; the live
#                                 pool carries all realized P&L since the
#                                 inception stamp in data/sa_pool_state.json.
#                                 A losing history shrinks the risk unit.
#   --pool-mode FRESH             size at exactly --pool. This is what
#                                 backtest_scalper.py does (balance = sa_pool,
#                                 no restore), so FRESH is the mode that makes
#                                 a live run comparable to a simulated one.
# Either way the agent logs a warning when the live pool differs from --pool.
# See §13.9 — sizing decides which signals the run is even allowed to admit.
```
Only one scalper may run at a time: the process holds `127.0.0.1:55556` and a
duplicate exits (the Guardian's equivalent lock is on 55555).

Operator-approved active trigger precedence (2026-09-07):
`SWEEP_REJECTION -> HTF_CRT_SWEEP -> FVG_FILL -> BOS_RETEST -> JUDAS`.
`VPLR_ENABLED` and `VA_FADE_ENABLED` remain false. The shared trigger engine
implements this order for both live and replay; `--triggers` is a whitelist,
not a way to reorder it. This is an operator policy choice, not a claim of
fold-consistent profitability; see the September 7 trigger-priority study.

- Completely independent of maingpt.py (can start/stop separately)
- Isolated capital pool (never touches main account balance beyond its pool)
- Uses Magic Number `88880` for all orders
- Enforces 120-minute hard timeout on all trades
- Forced close at 23:00 UTC daily

### 3.3 Trade Guardian Agent (TGA)
```powershell
cd apex_ai
python trade_guardian_agent.py   # Standalone launch (usually auto-spawned)
```
- Auto-spawned by `maingpt.py` — rarely needs manual launch
- Has a single-instance lock on port `55555` (prevents duplicates)
- Uses Magic Number `99990` for all SL/TP modifications
- Polls every 2 seconds, caches data for 15 seconds
- NEVER opens positions — only manages existing ones

### 3.4 Backtesting (SA-V2 Only)
```powershell
cd apex_ai
python backtest_scalper.py --from "2026-04-12T00:00:00" --to "2026-05-12T00:00:00" --pool 500 --risk 0.02 --symbols XAUUSD,XAGUSD,USOIL --out logs/backtest_results.json
python analyze_backtest_results.py   # Parse JSON → detailed report
python plot_backtest_results.py      # Generate equity curve chart
```
Note: `maingpt.py` does NOT have a historical backtester yet.

---

## 4. Configuration Reference (config.json)

### Symbols
```json
"symbols": ["XAUUSD", "XAGUSD", "USOIL"]
```
Currently active instruments. BTCUSD was removed from production.

### Risk Parameters
| Key | Value | Description |
|-----|-------|-------------|
| `high_clarity_risk_pct` | 16.0 | Risk % when probabilistic clarity is HIGH |
| `medium_clarity_risk_pct` | 8.0 | Risk % when clarity is MEDIUM |
| `low_clarity_risk_pct` | 0.0 | Risk % when clarity is LOW (no trade) |
| `max_total_exposure_pct` | 40.0 | Max portfolio exposure across all positions |
| `max_positions` | 2 | Maximum concurrent open positions |
| `max_drawdown_pct` | 40.0 | Max acceptable drawdown before PRESERVATION mode |
| `min_rr_ratio` | 2.0 | Minimum reward-to-risk ratio for any setup |

### Governance Modes
Dynamic mode transitions based on account drawdown:
- **AGGRESSIVE** (DD < 2%): Full risk allocation
- **BALANCED** (DD < 5%): Standard risk
- **DEFENSIVE** (DD < 7%): Reduced risk
- **PRESERVATION** (DD < 10%): Minimal risk, protective mode

### Manipulation Engine Calibration
Per-symbol sweep detection thresholds calibrated to real price levels:
- XAUUSD: 0.000637 (0.064% — ~$3 sweep on ~$4708)
- XAGUSD: 0.002000 (0.20% — ~$0.15 sweep on ~$75.68)
- USOIL: 0.002677 (0.27% — ~$0.25 sweep on ~$93.39)
- BTCUSD: 0.001022 (0.10% — ~$80 sweep on ~$78256)

---

## 5. Environment Setup

### Requirements
```
MetaTrader5>=5.0.45
pandas>=2.0.0
numpy>=1.24.0
pandas-ta>=0.3.14b0
rich>=13.7.0
scipy>=1.10.0
python-dotenv>=1.0.0
colorama>=0.4.6
```

### .env File
```
MT5_LOGIN=40280210
MT5_PASSWORD=your_password
MT5_SERVER=Exness-MT5Trial2
MT5_MODE=DEMO
```

---

## 6. Main System Pipeline (maingpt.py)

Each 60-second cycle runs this pipeline per symbol:

```
OHLCV Fetch (D1/H4/H1/M15/M5)
    │
    ▼
v1 Core Engines ──────────────────────────────────────────
    ├── LiquidityEngine    → BSL/SSL pools, premium/discount zones
    ├── ManipulationEngine → Sweep detection with per-symbol calibration
    ├── StructureEngine    → HH/HL/LH/LL trend, BOS/CHoCH detection
    └── DisplacementEngine → Impulsive moves, FVG mapping, momentum
    │
    ▼
v3 Multi-Timeframe Confluence (MTF scoring across 5 timeframes)
    │
    ▼
v4-v5 Intelligence Layer ─────────────────────────────────
    ├── ProbabilisticEngine → Bayesian bias (BULLISH/BEARISH/RANGING %)
    ├── RegimeEngine        → EXPANSION/MANIPULATION/ROTATION/TRANSITION
    └── OrderFlowEngine     → Volume delta analysis
    │
    ▼
v9 Multi-Agent Council (7 agents vote) ───────────────────
    ├── LIA  → Liquidity pool quality & proximity
    ├── MSA  → Structure trend alignment across timeframes
    ├── RDA  → Regime suitability for trading
    ├── CAIA → Cross-asset correlation & divergence
    ├── SEE  → Strategy model trust (adaptive memory feedback)
    ├── EA   → Execution feasibility & spread check
    └── CRG  → Capital risk governance & portfolio limits
    │
    ▼
v10 Hedge Fund OS (5-Gate Approval) ──────────────────────
    Gate 1: Agent Consensus (≥5 of 7 agents must align)
    Gate 2: Monte Carlo Simulation (500 runs, ≥52% win prob)
    Gate 3: CRG Risk Approval (within governance limits)
    Gate 4: Portfolio Exposure Check (≤40% total)
    Gate 5: Regime-Model Alignment (setup matches preferred model)
    │
    ▼
EXECUTE or WAIT
```

---

## 7. Scalper Agent Pipeline (scalper_agent.py)

SA-V2 runs independently on **M15 (trigger) / M5 (confirmation)** with a 30-second scan loop:

```
State Machine Check (IDLE/ACTIVE/PAUSED/COOLDOWN)
    │
    ▼
Cooldown Check (loss -> next UTC hour | win -> 5 min)
    │
    ▼
Session Window Check (London KZ / NY KZ / Asia; Whole_day is opt-in)
    │
    ▼
News Blackout Check (30min before / 15min after HIGH events)
    │
    ▼
Per-Symbol Trigger Scan (M15 trigger / M5 confirm) ───────
    Trigger Models:
    ├── A: Sweep Rejection (fading BSL/SSL sweeps)
    ├── B: FVG Fill (M1 Fair Value Gap mitigation)
    ├── C: BOS Retest (break of structure pullback)
    ├── D: Judas Swing (session fakeout fade)
    ├── F: Candle Range Theory (CRT pattern)
    └── H: NY Open Liquidity Hunt
    │
    ▼
HTF Bias Filter (M15 trend must align with trigger direction)
    │
    ▼
ReadOnly Consultation (sa_consultant.py) ─────────────────
    Gate 1: Regime Gate (RDA engine — blocks TRANSITION/ROTATION)
    Gate 2: Displacement Confirmation (momentum ≥ 0.65 + FVG present)
    Gate 3: LIA TP2 Override (macro H1 liquidity pool as better target)
    │
    ▼
Volume Sizing (risk_usd / (SL_pips × pip_value))
    └── Lot Floor Filter: if volume < broker_min (0.01), trade is SKIPPED
        (This naturally filters high-noise/wide-SL environments)
    │
    ▼
EXECUTE on MT5 (Magic 88880) with SL + TP1
    │
    ▼
Position Management:
    ├── Bar-count timeout: TIMEOUT_BARS(24) x 15m = 6h (auto-close at market)
    ├── Cooldown: loss -> next UTC hour, win -> 5 minutes
    ├── 15-minute loss pause after 2 consecutive losses (SA-CRG)
    └── 23:00 UTC forced daily close
```

### SA-V2 Key Design Decisions
- **Bypasses the AI Council entirely** — uses ReadOnly consultation for speed (<200ms latency)
- **Lot Floor Filter edge**: At 2% risk on $500 pool, wide-SL trades are auto-rejected (boosted win rate from 50.94% → 62.50% in backtesting)
- **Optimized parameters**: 2% risk, $500 pool, $50 daily loss limit

---

## 8. Trade Guardian Agent (TGA)

TGA monitors ALL open positions (from any agent) and manages them through a 4-stage trailing system:

### SL Trailing Stages
| Stage | Trigger | SL Position | Description |
|-------|---------|-------------|-------------|
| 0 | Entry | Original SL | Initial stop loss from entry |
| 1 | Peak ≥ 1.0R | Entry Price | Breakeven lock (+ 2-candle structure confirm) |
| 2 | Peak ≥ 1.5R | Trail at 1.0×ATR | Hybrid trail with structure lock |
| 3 | Peak ≥ 2.5R | Trail at 0.6×ATR | Aggressive trail with structure lock |

Corrected 2026-08-26 — this table read 0.5R/1.0R/2.0R long after `TGAConfig`
moved to 1.0/1.5/2.5, and §13.10 was carrying a note that it was stale. The
code is the source of truth: `trade_guardian_agent.TGAConfig`.

### Early Close Triggers (armed after 1R reached)
- Trigger 1: Reversal candle pattern (engulfing or rejection wick)
- Trigger 2: Momentum loss at key level (range contraction + no new HH/LL)
- Trigger 3: Market structure shift (swing low/high broken)

**Early close yields to the TP-extension stage.** When price is inside the
TP band *and* momentum confirms, the trade is running into its target, not
stalling, and early close is suppressed for that tick. Everywhere else it
keeps priority. Without this the two race, and at the current geometry early
close always wins — see below.

### TP Extension
When price is within 0.5×ATR of original TP and strong momentum exists (3 consecutive candles in direction), TGA:
1. Partially closes `tp_extension_partial_pct` (50%) of the position at TP1
2. Extends TP to next M15 structure target

**The extension only proceeds if the partial actually filled.** Pushing TP
further out with nothing banked is strictly more risk than leaving it. A
position too small to split (50% of 0.01 lots is below the broker minimum)
sets `tp_extend_blocked` and keeps its original TP.

**This stage was dead from the M15/M5 + 2R migration until 2026-08-26.** Every
`TP_EXTEND` ever logged fired at peak 0.71R–0.94R against the old TP1 = 1.0R,
where the proximity band sat *below* the 1.0R early-close arming threshold. At
TP1 = 2.0R the band sits near 1.8R, so early close armed first and returned
before the extension check ran: 13 TP_EXTENDs in May 2026, **zero** in August.
Changing TP1 changes which of these two stages fires first — re-check this
interaction whenever the geometry moves.

### Agent Identification (Magic Numbers)
| Magic | Agent |
|-------|-------|
| 20260426 | Main System (maingpt.py) — the value the connector actually writes |
| 12345 | Legacy Main; recognised on read, never emitted |
| 88880 | Scalper Agent (SA-V2) |
| 77770 | CHA (reserved, unimplemented) |
| 99990 | TGA modifications |
| 0 | Manual/Unknown trades |

**All magic numbers live in `core/constants.py`. Never write a literal** — a
writer/reader drift previously made every Pool A position show as UNKNOWN.

---

## 9. Backtesting Results (SA-V2, 30-Day) — SUPERSEDED, DO NOT QUOTE

> These figures came from a harness that ran M15 bars through the M5 code path
> and blanket-vetoed the MANIPULATION regime the live agent actually trades, so
> they describe a different strategy. A parallel walk-forward campaign with
> broker-cost and gate fidelity rejected all seven strategy concepts on the
> same data. Treat as historical record only — see `docs/RESEARCH_NOTES.md`.

**Period:** April 12 – May 12, 2026 | **Starting Capital:** $500

| Metric | 10% Risk (Baseline) | 2% Risk (Optimized) |
|--------|:---:|:---:|
| Trades | 267 | 128 |
| Win Rate | 50.94% | **62.50%** |
| Net PnL | +$74.69 (+14.94%) | **+$412.55 (+82.51%)** |
| Max Drawdown | $447.02 (53.11%) | **$53.10 (6.93%)** |
| Profit Factor | 1.02 | **1.81** |
| Ending Balance | $574.69 | **$912.55** |

**Asset breakdown (2% risk):**
- USOIL: 100 trades, 61% WR, +$301.28, PF 1.70
- XAGUSD: 14 trades, 71.43% WR, +$76.06, PF 3.04
- XAUUSD: 14 trades, 64.29% WR, +$35.21, PF 1.81

---

## 10. Process Management

### Clean Start Procedure
```powershell
# 1. Kill all
taskkill /F /IM python.exe /T; taskkill /F /IM terminal64.exe /T

# 2. Clean memory
powershell -Command "[System.GC]::Collect()"

# 3. Launch main system (auto-spawns TGA)
cd apex_ai
python maingpt.py

# 4. Launch scalper (separate terminal)
python scalper_agent.py --pool 500 --risk 0.02 --symbols USOIL,XAUUSD,XAGUSD --interval 30 --loss-limit 50.0

# 5. Verify all 3 processes are running
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Format-List ProcessId, CommandLine
```

### Expected Process Tree
```
maingpt.py (WindowsApps alias → real interpreter)
  └── trade_guardian_agent.py (auto-spawned child)
scalper_agent.py (WindowsApps alias → real interpreter)
```
Note: Windows Execution Aliases create parent-child pairs (alias → real interpreter). Each script shows as 2 PIDs but is 1 instance. TGA has a single-instance lock on port 55555.

### Log Files
| Log | Location |
|-----|----------|
| Main system | `logs/apex_ai.log` |
| Scalper agent | `logs/scalper_agent.log` |
| TGA actions | `logs/tga_log.txt` + `logs/tga_action_log.json` |
| SA trade log | `logs/scalper_log.json` |

---

## 11. News Guard System

- Scrapes Forex Factory for HIGH-impact events via `core/news_fetcher.py`
- Cached in `news_events.json`, refreshed every 6 hours
- Blackout window: **30 minutes before** to **15 minutes after** each event
- Affects all symbols listed in the event's `symbols` array
- Both maingpt.py and scalper_agent.py respect the blackout

---

## 12. Key Operational Notes

1. **Capital Isolation:** SA-V2's $500 pool is a logical construct tracked in `scalper/capital_pool.py`. The broker account holds one unified balance; isolation is enforced by the SA's internal accounting and lot sizing.

2. **Regime Classifications:**
   - EXPANSION: Strong trend — exploit with trend-following
   - MANIPULATION: Institutional sweep activity — allow sweep rejections only
   - ROTATION: Ranging/mean-reversion — block most entries
   - TRANSITION: No clear regime — block all entries

3. **Session Kill Zones (UTC) — two different layers; do not conflate them.**
   Corrected 2026-08-27: this section used to give one merged list whose NY KZ
   and both Silver Bullet times matched no code path in the repo.

   **Scalper SA-V2** — `scalper/session_checker.py::SESSION_WINDOWS`. A hard
   gate, first match wins, and the only thing that decides whether the scalper
   scans at all:

   | Window | UTC | Priority | Default |
   |---|---|---|---|
   | `TOKYO_OPEN` | 00:00–02:00 | 2 | enabled |
   | `PRE_LONDON` | 06:30–07:00 | 2 | disabled |
   | `LONDON_OPEN` | 07:00–08:30 | 1 | disabled |
   | `LONDON_NY` | 12:00–13:30 | 1 | enabled |
   | `NY_LUNCH_REV` | 16:30–17:30 | 3 | disabled |

   The production default is defined once as
   `DEFAULT_ENABLED_SESSIONS = ("TOKYO_OPEN", "LONDON_NY")`. Disabled named
   windows remain available only through an explicit `--sessions` whitelist.

   `Whole_day` (00:00–23:00) is opt-in only — §13.6, ledger L-010.

   **Main system** — `market/session_engine.py::SESSION_PRIORITY`. Weights and
   context for the agent council; `maingpt.py` reads `is_kill_zone` and
   `session_weight` but never branches on them, so this layer blocks nothing:
   LONDON_SB 07:00–08:00 (1.2), LONDON_KZ 07:00–10:00 (1.0),
   NY_KZ 12:00–15:00 (1.0), NY_AM_SB 14:00–15:00 (1.2),
   LONDON_CLOSE_KZ 15:00–17:00 (0.8), NY_PM_SB 18:00–19:00 (1.2),
   ASIA 00:00–06:00 (0.4). Off-session weight 0.3.

4. **The Lot Floor is NOT a filter — RETRACTED 2026-08-22.** This section used
   to claim that skipping wide-SL setups below the 0.01 broker minimum acted as
   a beneficial volatility filter. It does the opposite. A `LOT_FLOOR`
   rejection costs the agent nothing — no position slot, no cooldown — so it
   re-scans and takes a degraded re-entry into the same move. Measured over
   2026-05-15 -> 08-23 on XAUUSD, the 141 trades a $10 budget takes that a $30
   budget does not win **24.8%** of the time at the same median stop width,
   against 47.8% on the trades both budgets agree on. Position sizing is a
   silent signal-admission filter: if `risk_usd / pip_value` is below the
   median stop the signal asks for, the run measures the residue, not the
   strategy. See `docs/RESEARCH_NOTES.md` §10.

5. **No maingpt.py backtester exists yet.** Only SA-V2 has `backtest_scalper.py`. The main system can be run with `--dry-run` for paper trading.

---

## 13. Research-Backed Invariants (v12)

These are load-bearing. Changing one without new walk-forward evidence
reintroduces a defect that was already diagnosed and fixed. Full citations and
extracted numbers live in `docs/RESEARCH_NOTES.md`.

### 13.1 The survival condition

```
RRR  >  (1 - win_rate) / win_rate
```

Source: MQL5 art. 18991, read in full. The scalper previously used **TP1 = 1.0R**
against a measured **17.6% win rate** (37 wins vs 173 losses and 67 timeouts
over 287 live trades, 2026-05-06 → 15). That required RRR > 4.6. The system was
arithmetically incapable of profit at any signal quality.

**Now:** TP1 = 2.0R, TP2 = 3.0R (`SATriggerEngine.TP1_R` / `TP2_R`).
Corroborated three ways — art. 19141 tabulates RRR ≥ 2.0 as the floor for a 45%
win rate; MQL5's two reference SMC EAs ship RR defaults of 2.0 and 2.14.

**Never lower TP1 below 2R on the argument that a nearer target wins more
often.** Recompute the condition against the current measured win rate first.

### 13.2 Targets must clear costs, not just the stop

Art. 19141 explicitly excludes spread, commission and swap from its model, so
its floors are optimistic. `step3_validate` therefore adds a fourth layer:

```
net_R = (reward_pips - 2 x spread) / (sl_pips + 2 x spread)     # must be >= 1.5
```

Measured: the old 1R geometry nets **0.95R** at a 400-pip SL and 5-pip spread —
a losing proposition on every trade taken. The same setup at 2R nets **1.91R**.

### 13.3 Exits are strategies and must be measured separately

Source: MQL5 art. 19211, read in full (Monte Carlo, 100 sims/strategy, 500
combined). Its finding: a negative-expectancy exit profile must be *eliminated*,
not diluted — combining it with good profiles drags the whole book down.

**23% of live trades (67 of 287) exited on the timeout.** That is a distinct
exit profile that was never measured. The timeout is now expressed in
trigger-frame bars (`TIMEOUT_BARS = 24`, so 6h on M15) and the trade journal
records an outcome label per trade so its expectancy can be computed.

### 13.4 A backtest that does not run the live decision path is not evidence

The donor repo's campaign discovered its simulator never applied the CONSUL
gates; after the fix the best rule's trade count fell from **72 to 8** on the
same window, and every prior result in that journal was invalidated.

**Rule:** any gate added to `scalper_agent._scan_symbol` is mirrored into
`backtest_scalper.py` in the same change.

**Enforced structurally since 2026-08-23.** A comment cannot hold an
invariant; four constants had already drifted while one was asking editors to
keep them in step — the consultant classified regime on **M5** while the
simulator used **M15**, the trigger window was 150 bars live and unbounded in
the simulator, the confirmation window 150 vs 200, and live read the forming
bar while the simulator could not.

- Every shared decision value lives in **`scalper/decision_params.py`**. Both
  processes import it. Never restate one.
- The consultation is **one function** — `sa_consultant.analyse()` — imported
  by the simulator, not re-implemented in it.
- Every decision frame excludes the **forming bar** (MQL5 shift=1). Live reads
  `copy_rates_from_pos(..., 1, bars)`; the simulator slices `time < now`.
- The simulator uses the broker's **per-bar spread** from the MT5 rates feed.
  `--spread-pips` is only a fallback where the field is zero. The old fixed
  2.5 understated XAUUSD's measured **4.78 pip** average by roughly half, and
  spread also enters `step3_validate`'s net-R test — so the simulator was both
  under-charging trades and admitting setups live would reject.
- `tests/test_live_sim_parity.py` asserts *identity* with `decision_params`,
  so a re-divergence fails the suite rather than silently changing what the
  backtest measures.

### 13.5 No promotion from a single window

Chronological 60/20/20 split; the same sign in all three folds. Sign
instability across folds is the standing rejection criterion. Seven strategy
concepts have already been rejected under this bar — `BOS_RETEST` (all
variants), `SWEEP_REJECTION` (unfiltered, scored, and by session), `FVG_FILL`,
`PULLBACK_CONTINUATION`, `SWEEP_BOS_CONTINUATION`. Check
`docs/RESEARCH_NOTES.md` §7 before proposing a "new" idea; it has probably been
tried.

### 13.6 Session gating must actually gate

The `Whole_day` 00:00-23:00 window sat last in an ordered first-match list, so
it matched every hour outside a kill zone and made `IDLE` unreachable - the
agent scanned 23 hours a day. `Whole_day` is now opt-in
(`--allow-whole-day`) and should stay off outside plumbing tests.

**Re-measured on the current stack 2026-08-26** (`docs/RESEARCH_NOTES.md` S15,
ledger L-010), because the original rationale came from the donor campaign on a
different strategy. Six paired arms, `--allow-whole-day` the only variable:
kill-zone gating is fold-**CONSISTENT in all six**, the whole-day arm is
fold-**UNSTABLE in all six**. On the favourable window it cuts net P&L 78%
(+$2742 -> +$609) and nearly doubles drawdown; on the unfavourable window it is
flat in dollars and worse in drawdown.

The mechanism is **cannibalisation, not toxic hours**. The extra trades are
~breakeven standalone (224 trades, +$27.78, PF 1.01); the kill-zone book is
what collapses, +$2742 -> +$581 on the same hours. One position per symbol
(`SYMBOL_DEDUP` 856 -> 3656) is the structural channel and cannot be disabled.
Hour 00 is the natural control - nothing precedes it, its trade count is
identical across arms (77 -> 77), while hour 06 goes +$330 -> -$263 and hour 12
+$394 -> -$354. Turning the cooldown and daily loss limit off *widens* the gap,
so those were masking damage rather than causing it.

**Correction to a claim this section used to make.** It cited the donor finding
that the Asia block carried 68-73% of trade volume and the largest absolute
loss. That does not reproduce: Asia is 43.0% / 40.2% of volume in the two
windows tested and carries the largest loss in **neither** - it is the only
profitable block in the recent window, and `TOKYO_OPEN` is the best single
session in the book (109 trades, 51.38% WR, +$1820, PF 1.60). The conclusion
survives; the reason did not. Reasoning from the old rationale would point at
cutting `TOKYO_OPEN`, which is the most expensive session to remove.

### 13.7 Sources that are not evidence

MQL5 marketplace EA listings are sales pages with no verifiable methodology or
track record. Most MQL5 articles publish signal screenshots and equity-curve
images but no win rate, profit factor, sample size or date range — art. 20569
is typical, and a commenter's repainting question went unanswered. Treat these
as **design** evidence (rule shapes, parameter defaults) and never as
**performance** evidence.

---

### 13.8 A trend filter fights this book — the H1 EMA(18) band

EMA(18) on H1 highs and EMA(18) on H1 lows, longs only above both and shorts
only below both, was implemented in full and measured. It **fails §13.5**: at
3% risk it turns +$2742 / PF 1.35 / positive-in-all-folds into +$230 / PF 1.06
with F3 negative; at 2% it flips F2 and F3. Inverting the mapping is worse
still, in every fold.

The band removes 197 trades whose PF was **1.52** and keeps 83 whose PF was
**0.94**, and drawdown *rises* from 25.8% to 32.4%. The cause is structural,
not a tuning problem: 276 of 280 trades come from `SWEEP_REJECTION`, which
fades a liquidity grab, and a trend band vetoes a fade by construction.

It ships **off** (`EMA_BAND_ENABLED = False`), switchable via `--ema-band`
and `--ema-band-mode {TREND,FADE}`. **Do not enable it on the current
sweep-fade trigger mix.** It would need re-measuring against a
continuation-heavy mix, which does not currently produce a sample. Full table:
`docs/RESEARCH_NOTES.md` §11.

### 13.9 Position sizing decides what the run measures

Restated from §12.4 because it bit again: a 1%-risk arm of the EMA campaign
took **604** `LOT_FLOOR` rejections against **13** at 3% risk, and its
baseline was −$11 where the 2% and 3% baselines were +$1617 and +$2742. That
arm was measuring the residue, not the strategy. Any comparison across risk
levels must report `LOT_FLOOR` counts, or it is comparing two different
samples and calling the difference an edge.

### 13.10 The agent diagnoses itself; it does not retune itself

Every closed trade is replayed against its confirmation bars and labelled with
one failure mode (`scalper/postmortem.py`), and every gate veto is counted by
stage (`scalper/reject_log.py`). Both layers are **observation-only by
construction** and `tests/test_forensics_parity.py` enforces it: the record
carries no permission-shaped field, and `_scan_symbol` never branches on a
counter. A counter that fed back into gating would be a strategy change wearing
telemetry's clothes, and would have to be mirrored and fold-tested like any
other gate.

The path from a finding to a live parameter runs through `docs/REMEDY_KB.md`
(candidate remedies, evidence graded A/B/C/D per §13.7) and
`docs/REMEDY_LEDGER.md` (append-only, one row per proposal, hypothesis stated
before the arm is run). **§13.5 is unchanged**: 60/20/20, same sign in all three
folds. A diagnosis points research at a defect; it never authorises a change.

Two facts that bit immediately and will bite again:

- **`logs/scalper_log.json` is not the account of record for P&L.** It booked
  140 of 282 closes at $0; 131 of 187 reconcilable positions disagreed with the
  broker. Because `_on_trade_closed` derives the win/loss label from the P&L it
  is handed, a $0 booking becomes a `LOSS`, and a profitable trade that peaked
  at 1.2R then classifies as a given-back winner. The first diagnosis run
  invented a 61-trade exit defect that was really 15. Read P&L from
  `history_deals_get`. Ledger L-004.

  **The live booking path was fixed 2026-08-25** — `_book_settled_close()`
  books a vanished position only against settled deal history, defers an
  unsettled one to the next cycle, and after `PNL_RECONCILE_GRACE_MIN` books
  `OUTCOME_UNRECONCILED` rather than inventing a `LOSS` at $0.00. **Rows
  written before that fix still carry the fabrications** and must be repriced
  against the broker before being used as a research input. Never restore the
  shortcut this replaced: booking a fabricated $0.00 is not neutral —
  `register_close(0.0)` resets the consecutive-loss counter and
  `record_trade_result(0.0)` arms the *loss* cooldown, because neither
  `0.0 < 0` nor `0.0 > 0` is true.
- **The simulator models no trailing; the live Guardian does.** The running
  Guardian arms breakeven at peak **1.0R**, trails at 1.5R and 2.5R — §8 of
  this file still says 0.5R/1.0R/2.0R and is **stale**. 36 of 115 reconciled
  live losses closed without price ever reaching the original stop (median MAE
  0.54R), which a breakeven stop cannot produce; the timeout and EOD flatten
  are the live candidates. Until that gap closes, *entry*-side remedies are
  measurable and *exit*-side ones are not. Ledger L-003.

Loop playbook: `.claude/skills/apex-postmortem/SKILL.md`.

### 13.12 The book is centred on zero across nine years, and thinner than its own slippage

**L-008 closed 2026-08-27.** 17 disjoint 6-month windows, 2018-03 -> 2026-08,
XAUUSD, $1000 @ 3%, gate chain exactly as shipped, 5.0-pip spread floor:

**4694 trades, 40.48% pooled WR, net +$2282, pooled PF 1.0289, 7 windows
positive / 7 negative, median window +$16.30**, PF between 0.87 and 1.23 in 13
of 14 trade-bearing windows.

+$2742 (2026-05-15 .. 08-23) and -$657 (2025-08-01 .. 2026-05-14) are **both
tails of a near-zero-mean distribution**. Neither is an expected return.

Three findings that bind on all future work:

- **Every REJECTED verdict in §§9-13 is downgraded.** Each was decided by a few
  hundred dollars of net P&L on one draw from a distribution centred on zero.
  They establish that a gate did not rescue one window; they do **not**
  establish that the gate is harmful. All stay off — this is a caveat on the
  evidence, not a promotion.
- **§13.5 does not do what it claims.** 60/20/20 inside one window is a
  within-window stability test. It fired in only 4 of 14 windows here, and
  those four split +$904 / +$1187 / -$172 / -$680 — so it carries **no**
  cross-regime information. Promotion requires folding across **disjoint
  multi-month windows**.
- **The survival condition (§13.1) holds in 14 of 14 windows** — required RRR
  0.88-1.84, median 1.43, always under the shipped 2.0. But realized
  expectancy is **+0.016R against a naive +0.214R**: 93% of the theoretical
  edge is lost between signal and settlement. **TP1 geometry is not the
  defect.** Do not aim there.

**What actually kills it is L-012.** Gross gains $81,265 vs gross losses
$78,982 is a 2.9% margin. Applying only the measured reward-leg haircut
(2.000R -> 1.852R), holding the loss leg constant although stops widen too:
**net -$3,731, PF 0.9528.** No gate measured so far is large enough to matter
against that. Entry-side gate work is not where the remaining value is.

**That projection used the n=11 haircut (0.9260) and overstates the drag.**
§13.14 was re-measured 2026-08-28 on n=23 at **0.9723**, so the true drag is
roughly 38% of the figure above. The corrected nine-year number has **not**
been computed — it needs an L-008 re-run. Quote the haircut, not a revised net.
The conclusion is unchanged in direction: a 2.8% reward haircut is still the
same order as the 2.9% gross margin.

**Two data facts about this broker's archive.** The 2018-03 -> 2019-09 windows
produce **zero trades** — bars and triggers exist, but the archived spread is
111-145 points and `STEP3` correctly rejects everything; that is a cost result,
not a data gap. And the spread field is **zeroed 2020-03 -> 2025-03**, so
`_spread_series` falls back to `--spread-pips` across 11 of 14 windows. Raising
that floor 2.5 -> 5.0 costs $1264, 36% of the headline. **State the spread
floor on any multi-year run; the archive cannot supply one.**

Standing caveat: current parameters (TP1 = 2R, cooldown, sessions) were chosen
with recent data in view, so this tests the **gates** out of sample, not the
parameters. Full tables: `docs/RESEARCH_NOTES.md` §17; ledger L-008.


### 13.11 The volume-profile location rule is inverted — do not re-propose it

"Sell from VAH, buy from VAL, no trade at the POC" is the natural reading of a
volume profile and it is **backwards for this book**. Implemented in full and
measured on XAUUSD 2026-05-15 -> 08-23, $1000 @ 3%, eight arms:

- baseline (gate off) **+$2742.35**, 280 trades, 46.07% WR, positive in all
  three folds;
- every gated arm worse, with damage monotonic in the POC band width
  (0.02 -> -$511, 0.05 -> -$744, 0.10 -> -$1328);
- the two arms applying the full buy-low/sell-high rule **flip sign across
  folds** — §13.5's standing rejection criterion — and `ALWAYS` also fails the
  survival condition at a 35.92% win rate.

Attribution of the baseline book says why: **`AT_POC` is the best location
bucket** — 42 trades, PF 2.10, +$1038, avg +$24.72 — and **`AT_VAL` is the only
negative one**, PF 0.93. The rule deletes the best trades and keeps the worst.
The inverse (`POC_REQUIRE`) does pass folds and earns +$325 on 54 trades, which
is not a rescue: it throws away 80% of the book to raise the average.

Ships off (`VP_GATE_ENABLED = False`). Full tables: `docs/RESEARCH_NOTES.md`
§12; ledger L-005.

**Two traps this campaign walked into, worth remembering:**

- **A correct diagnosis does not imply a correct remedy.** The live loss really
  was a POC entry, 0.93 from the POC. The same session's two *winners* were
  also at the POC. One trade cannot distinguish a defect from a coincidence.
- **Attribution is not a filter forecast.** The 42-trade `AT_POC` bucket and
  the 54-trade `POC_REQUIRE` book are different trades. Vetoing a trade frees a
  position slot and skips a cooldown, so gating changes which trades come
  *later*. Only a full re-run measures a gate.

### 13.13 PDH/PDL is genuinely absent from the decision path — and adding it as a veto is measured and rejected

Both halves matter, because the first invites the second.

**The gap is real.** `liquidity_engine._prev_day_hl` computes PDH/PDL and
publishes them at the highest strength in the map (0.92);
`sa_consultant.analyse()` runs that engine and then keeps only `price_zone`,
`nearest_bsl`, `nearest_ssl`. `SAConsultResult` has no PDH/PDL field, and the
pools it does keep feed the Gate 3 TP2 realignment only. Separately,
`trigger_engine.step1_liquidity` reads **M5** over 50 bars and takes
`session_high/low` from **today's bars only**, so no daily level can reach a
trigger at all. PDH/PDL is computed and discarded on every scan.

**Closing that gap with a veto does not work.** `scalper/pdr_gate.py` was
implemented in full, mirrored into the simulator, and measured across both
L-008 windows. All three modes lose money where the strategy works
(-$805 / -$714 / -$253) and return almost nothing where it does not
(+$45 / +$46 / +$135); `LONG_PREMIUM` and `SYMMETRIC` also fail §13.5 folds.
Ships off (`PDR_GATE_ENABLED = False`), switchable via `--pdr-gate` /
`--pdr-gate-mode`. Two further operationalizations — proximity to the opposing
unswept pool, and alignment with a formed daily sweep bias — died at the
attribution stage.

**The intuition behind it is backwards, the same way §13.11's was.** "Selling
just above an unswept PDL" looks like the error. It is the book's **best**
bucket: where the pool was swept during the trade, 4 of 4 window x threshold
cells beat book PF (2.88 / 1.48 / 2.14 / 1.77 against 1.35 / 0.91). The sweep
travels in the trade's favour first. The pattern is also only 1.3-10% of trades.

**And attribution lied again.** The bucket said vetoing `PD_PREMIUM`/`BULLISH`
would add +$463; the arm lost $714. Two arms produced *more* trades than the
control while vetoing hundreds of signals, because a veto frees a position slot
and skips a cooldown. Second occurrence after L-005 — **never promote from a
bucket; only a full re-run measures a gate.**

Ledger L-011; full tables `docs/RESEARCH_NOTES.md` §16.

### 13.14 SL/TP are anchored to the signal price, so live geometry is worse than every backtest says

`_execute` sends `trigger.stop_loss` and `trigger.tp1` as absolute prices
derived from `trigger.entry_price` and never re-anchors them to the actual fill
(`scalper_agent.py` l.920-921); sizing uses the same signal price (l.812,
l.1360). Adverse slippage therefore widens real risk and narrows real reward at
the same time.

**Re-measured 2026-08-28 on n=23** (agents stopped, signal prices from
`scalper_log.json` against `history_deals_get`): **14 of 23 filled adversely
(60.9%), median realized R:R 1.945 against an intended 2.000** — a haircut of
**0.9723**. Worst case 0.543R; p10 1.438R; stops run 1.9% wider than intended;
**30.4% fall below the 1.81 survival line** §13.1 demands at a 35.53% win rate.

This supersedes the original n=11 reading (8 of 11 adverse, median 1.852,
haircut 0.9260) — the defect is real but roughly half as large as first
measured. Three cautions: the **mean and max realized R:R are meaningless**
(a fill landing on the stop sends actual risk to zero and the ratio to 16R —
use the median); **mean slippage is favourable**, which is a demo-server
artefact, so 0.9723 is a **floor**, not an estimate of live execution; and a
2.8% haircut is still the same order as §13.12's entire 2.9% gross margin.

**The simulator fills at the signal price by construction.** Every backtest
number in this repository — including every table in §§13.8-13.13 — assumes a
perfect fill, and no fold test can see this gap. `logs/scalper_log.json` cannot
show it either: it records the *signal* price, so it reads exactly 2.000R by
construction. Read fills from `history_deals_get`.

Not fixed: n=11, and re-anchoring moves a stop, which makes it an exit-side
change under L-003. Ledger L-012, open.

### 13.15 VP_LIQUIDITY_REACTION is measured and rejected — and dollar deltas alone were hiding a compounding effect

A first-class trigger (not a filter): H4 volume-profile levels as *locations*,
with direction taken from a liquidity raid plus an M5 MSS. Built in full,
mirrored into both decision paths, 328 tests. Two correctness bugs in the first
detector were found by tracing a live setup and fixed (raid recency bound;
candidates ranked by recency then depth), and the campaign was re-run.

XAUUSD, $1000 @ 3%, two **disjoint** windows, delta against each window's own
baseline:

| arm | delta W1 | delta W2 |
|---|---:|---:|
| `--vplr-scope ASIA_ONLY` | **-$1933.60** | **-$131.07** |
| `--vplr-scope ALL_SESSIONS` | **-$3531.75** | **-$121.35** |

Negative in both windows — §13.12's same-sign standard, met in the direction of
rejection. The trigger's own expectancy flips sign across the windows
(-0.2145R / +0.0220R inside ASIA_ONLY), and it fails the §13.1 survival
condition in 4 of 6 cells because realised wins average 1.30-1.82R against a
shipped 2.0R target: 10-26% of its trades exit on the timeout.

Ships **off** (`VPLR_ENABLED = False`), switchable via `--vplr` /
`--vplr-scope`. The `VP_ONLY` state and the 00:00-06:30 allowance are inert
while it is off. Full tables: `docs/RESEARCH_NOTES.md` §18.3-§18.4b; ledger
L-014.

**Two methodological facts from this campaign that bind on all future work:**

- **Report `sumR` next to net $.** On the 205 bars shared between the W1
  baseline and the ASIA_ONLY arm, entry/stop/exit prices are identical and the
  trigger changed on zero of them — yet dollars differ by $1153. Sum R is
  39.587 vs 39.583. The gap is entirely compounding: the arm sizes 0.01 lots
  where the baseline sized 0.02, because it lost money earlier. A losing
  addition charges twice, and a dollar delta therefore cannot be read as a
  statement about trade *selection*. Every gate effect measured before this was
  reported in dollars only.
- **The attribution trap fired a fourth and fifth time.** The ALL_SESSIONS arm
  attributes its `VP_ASIA` bucket at +$179.40 PF 1.11; the ASIA_ONLY arm that
  isolates exactly those hours books -$157.77 PF 0.89. The same arm attributes
  `POC` at +$1434.24 PF 1.68 in W2 and -$356.33 PF 0.50 in W1. After L-005 and
  L-011 this is settled — **never promote from a bucket.**

**Do not re-propose this trigger without addressing the stop width.** Its stop
sits beyond the raid extreme by construction, so `LOT_FLOOR` runs 48-1950
against a baseline 12-872 (§13.9). That is the one aspect not yet measured
rather than measured-and-rejected, and it needs a new ledger row.


## 14. Verification Gate

Before any live run:

```powershell
cd apex_ai
py -3.14 -E -m unittest discover -s tests   # 270 tests: cooldown, geometry,
                                           # cost gate, EMA band, parity,
                                           # close paths, P&L settlement,
                                           # pool allocation, forensics,
                                           # telemetry, volume profile,
                                           # regime classifier, TGA
                                           # partial close + PDR gate
py -3.14 -E -m compileall -q .
```

Use `py -3.14` — system CPython 3.14 at
`C:\Users\<user>\AppData\Local\Python\pythoncore-3.14-64\python.exe`. It carries
pandas, MetaTrader5, dotenv, numpy, rich, scipy and colorama, and runs the suite
clean. Alternate, also verified clean on this repo:
`D:\Hermes Quant\GPTMain\.venv\Scripts\python.exe` (3.12.13, adds pandas_ta,
which nothing here imports). Prefer `py -3.14` — it does not depend on a second
repository being present, and that dependency has already cost one session.

**Pass `-E`, and never use bare `python` off PATH.** Bare `python` resolves to
the Hermes agent venv — dotenv yes, pandas and MetaTrader5 no. Separately, some
agent harnesses export `PYTHONPATH` pointing at that venv's CPython 3.11
`site-packages`; a 3.14 run then loads cp311 numpy binaries and fails with
`No module named 'numpy._core._multiarray_umath'`. `-E` makes the interpreter
ignore `PYTHON*` variables and defeats it. Both faults look like repo breakage
and are not.


Operational playbook for repeated tasks: `.claude/skills/apex-ops/SKILL.md`.
Change history: `CHANGELOG.md`. Audit findings: `TECHNICAL_REFERENCE.md`.
