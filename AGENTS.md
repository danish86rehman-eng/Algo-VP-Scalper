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
```
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
| 1 | Peak ≥ 0.5R | Entry Price | Breakeven lock |
| 2 | Peak ≥ 1.0R | Trail at 1.0×ATR | Hybrid trail with structure lock |
| 3 | Peak ≥ 2.0R | Trail at 0.6×ATR | Aggressive trail with structure lock |

### Early Close Triggers (armed after 1R reached)
- Trigger 1: Reversal candle pattern (engulfing or rejection wick)
- Trigger 2: Momentum loss at key level (range contraction + no new HH/LL)
- Trigger 3: Market structure shift (swing low/high broken)

### TP Extension
When price is within 0.5×ATR of original TP and strong momentum exists (3 consecutive candles in direction), TGA:
1. Partially closes 50% of position at TP1
2. Extends TP to next M15 structure target

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

3. **Session Kill Zones (UTC):**
   - London KZ: 07:00–10:00 (Silver Bullet: 08:00–09:00)
   - NY KZ: 12:00–15:00 (Silver Bullet: 15:00–16:00)
   - Off-session weight: 0.3 (heavily penalized)

4. **The Lot Floor Filter Discovery:** When risk budget is small ($10 at 2% of $500), trades requiring wide stop losses produce lot sizes below broker minimum (0.01). These are automatically skipped, acting as a natural volatility/noise filter that dramatically improves win rate.

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

### 13.5 No promotion from a single window

Chronological 60/20/20 split; the same sign in all three folds. Sign
instability across folds is the standing rejection criterion. Seven strategy
concepts have already been rejected under this bar — `BOS_RETEST` (all
variants), `SWEEP_REJECTION` (unfiltered, scored, and by session), `FVG_FILL`,
`PULLBACK_CONTINUATION`, `SWEEP_BOS_CONTINUATION`. Check
`docs/RESEARCH_NOTES.md` §7 before proposing a "new" idea; it has probably been
tried.

### 13.6 Session gating must actually gate

The `Whole_day` 00:00–23:00 window sat last in an ordered first-match list, so
it matched every hour outside a kill zone and made `IDLE` unreachable — the
agent scanned 23 hours a day. By-session research found the Asia block carried
68–73% of trade volume and the largest absolute loss. `Whole_day` is now
opt-in (`--allow-whole-day`) and should stay off outside plumbing tests.

### 13.7 Sources that are not evidence

MQL5 marketplace EA listings are sales pages with no verifiable methodology or
track record. Most MQL5 articles publish signal screenshots and equity-curve
images but no win rate, profit factor, sample size or date range — art. 20569
is typical, and a commenter's repainting question went unanswered. Treat these
as **design** evidence (rule shapes, parameter defaults) and never as
**performance** evidence.

---

## 14. Verification Gate

Before any live run:

```powershell
cd apex_ai
python -m unittest discover -s tests   # 32 tests: cooldown, geometry, cost gate
python -m compileall -q .
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
