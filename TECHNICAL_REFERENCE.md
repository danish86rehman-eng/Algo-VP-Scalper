# APEX AI Trading System — Technical Reference

> **STATUS — superseded in part, 2026-08-22.** This document records the audit
> as found. Findings **F-01 through F-05, F-07 through F-12 have since been
> fixed**; see [CHANGELOG.md](CHANGELOG.md) for what changed and how it was
> verified. F-06 (Guardian/Scalper co-management) is now governed by an explicit
> ownership flag in `core/constants.py` but the two processes still act
> independently. The scalper described here as M5/M1 now runs **M15/M5**, and
> TP1 has moved from 1R to 2R.

> Verified against source on **2026-08-22**. Where this document and `CLAUDE.md` / `AGENTS.md`
> disagree, this document reflects the code as read. Runtime observations are drawn from
> `apex_ai/logs/` and `apex_ai/data/`.

| | |
|---|---|
| Runtime | Python 3 + MetaTrader5 |
| Source size | ~10,000 LOC across 58 modules |
| Processes | 3 (main · scalper · guardian) |
| Instruments | XAUUSD, XAGUSD, USOIL |
| Broker | Exness MT5 Trial (demo) |
| Version control | Not a git repository |

---

## 1. System overview

APEX AI encodes ICT / Smart Money Concepts as executable engines. It reads OHLCV from a
MetaTrader 5 terminal, derives market structure, liquidity pools, manipulation events and
displacement from raw candles, and passes candidates through layered approval before sending
market orders back to the same terminal. No machine learning, no external data feed beyond a
Forex Factory calendar mirror. Every decision is deterministic given the bars.

Three processes share one broker account and one code tree. They coordinate **only** through MT5
state — positions, deal history, magic numbers. There is no IPC, message bus, or shared database.

| Process | Magic | Timeframe | Cycle | Role |
|---|---|---|---|---|
| `maingpt.py` | 20260426 | M15 exec, D1→M5 analysis | 60s | Pool A — swing/intraday, 7-agent council, 5-gate approval |
| `scalper_agent.py` | 88880 | M1/M5 | 30s | Pool B — micro-scalping, isolated ledger, 11-stage filter chain |
| `trade_guardian_agent.py` | 99990 | M5/M15 | 2s | Universal position manager; never opens positions |

---

## 2. Repository map

One package root, `apex_ai/`. No `setup.py`, `pyproject.toml`, test suite, or CI. Modules are
imported via `sys.path.insert(0, Path(__file__).parent)` at each entry point.

| Path | Contents |
|---|---|
| `apex_ai/*.py` | 3 daemons, 1 backtester, 14 operator scripts |
| `apex_ai/core/` | Candle-level primitives: liquidity, manipulation, structure, displacement, execution_models, news_guard, news_fetcher |
| `apex_ai/market/` | mt5_connector, session_engine, multi_timeframe |
| `apex_ai/intelligence/` | probabilistic, regime, order_flow, portfolio, risk, adaptive_memory |
| `apex_ai/agents/` | LIA, MSA, RDA, CAIA, SEE, EA, CRG + `agent_base` |
| `apex_ai/fund/` | hedge_fund_os, simulation, capital_governance, compounding, meta_fund |
| `apex_ai/scalper/` | Pool B only — 10 modules |
| `apex_ai/dashboard/` | Rich live terminal UI (Pool A) |
| `apex_ai/logs/`, `data/` | Runtime artefacts |
| `Redundant/` | 11 superseded design notes, not imported |

**Declared dependencies.** `requirements.txt` pins `MetaTrader5`, `pandas`, `numpy`, `pandas-ta`,
`rich`, `scipy`, `python-dotenv`, `colorama`. Of these, **`pandas-ta`, `scipy` and `colorama` are
never imported**; **`matplotlib` is imported** by `plot_backtest_results.py` but is missing from
the file.

---

## 3. Runtime topology & process contract

### Lifecycle

`maingpt.py` spawns the Guardian as a detached child *before* parsing its own arguments
(`subprocess.Popen([sys.executable, "trade_guardian_agent.py"])`), so it starts even for a
`--dry-run` session and is never reaped on shutdown. The Scalper's equivalent auto-spawn block is
commented out. The Guardian guards against duplicates by binding TCP `127.0.0.1:55555` and exiting
quietly if the port is taken.

### Magic numbers

Attribution rests entirely on the MT5 `magic` field. Values are not centralised — each writer
hard-codes its own and each reader hard-codes its own mapping.

| Magic | Written by | Recognised by | Status |
|---|---|---|---|
| `20260426` | `market/mt5_connector.py` — every Pool A order/close | 5 operator scripts → MAIN; **TGA does not** | ⚠ Drift |
| `12345` | **Nothing** | TGA → MAIN; scripts → MAIN | ⚠ Phantom |
| `88880` | `scalper_agent.py` | TGA → SA; SA history restore | ✅ Consistent |
| `99990` | `trade_guardian_agent.py` | Operator scripts → TGA | ✅ Consistent |
| `77770` | Nothing (reserved "CHA") | TGA + 2 scripts | — Reserved |

**Consequence:** every Pool A position registers with `origin_agent="UNKNOWN"` in the Guardian.
Management still happens (the Guardian manages all positions regardless of origin), but attribution
in `tga_action_log.json` is wrong, and any future origin-conditional logic would miss Pool A.

### Capital isolation

Pool B's "$500 isolated pool" is an accounting construct in `scalper/capital_pool.py`, not a broker
partition. Isolation is enforced only by (a) sizing against `pool.current_pool × risk_pct` instead
of account equity, and (b) a startup guard refusing `--pool` greater than account balance. Nothing
prevents both pools consuming the same margin, and neither sees the other's exposure. The Scalper
does read main-account drawdown (equity vs balance) and halts above 3%.

---

## 4. Shared analysis core

Both pools call the same four `core/` engines. They are pure functions of a DataFrame — no I/O, no
broker calls — which is why the Scalper's read-only consultant reuses them without touching Pool A.

| Engine | Produces | Key constants |
|---|---|---|
| **LiquidityEngine** | BSL/SSL pools from swings, equal H/L, previous-day H/L, session H/L; nearest pool per side; premium/discount vs the mid of extremes | swing lookback 5 · equal tolerance 0.2% · 8 pools/side · 50-bar equal scan |
| **ManipulationEngine** | One signal, first match wins: sweep → Judas → false breakout. Direction, confidence, swept pool | pierce thresholds: XAUUSD 0.0637%, XAGUSD 0.20%, USOIL 0.268%, BTCUSD 0.10%, default 0.10% · reversal 2 bars |
| **StructureEngine** | Alternating HH/HL/LH/LL sequence, trend verdict, BOS/MSS flags; `is_tradeable()` needs one of BOS/MSS | trend = ≥2 HH + ≥1 HL (or ≥2 LL + ≥1 LH) in last 8 swings · keeps 20 points |
| **DisplacementEngine** | FVGs, order blocks, 0–1 momentum score, nearest unfilled FVG / unmitigated OB | momentum = mean 5-bar body ÷ (ATR×1.5) · displaced ≥0.60 · min FVG 0.1% · lookback 30 · ATR 14 |

### Derived state

| Engine | Logic |
|---|---|
| **ProbabilisticEngine** | Weighted vote → bull/bear/range %. Weights: structure **0.30**, manipulation **0.25**, MTF **0.25**, displacement **0.20**. Directional scores × session weight; range score damped by it. Clarity HIGH ≥0.70, MEDIUM ≥0.50, else LOW — clarity selects the risk tier. |
| **RegimeEngine** | `EXPANSION` (ATR(5)/ATR(20) ≥1.5 and momentum ≥0.65) → CONTINUATION; `MANIPULATION` (sweep, confidence ≥0.65) → RETURN; `ROTATION` (ratio <0.6, momentum <0.4) → mean reversion; else `TRANSITION` → wait. Drawdown overrides behaviour: REDUCE ≥5%, DISENGAGE ≥7%. |
| **OrderFlowEngine** | Candle-anatomy delta, recency-weighted pressure, absorption, move type. **Computed, never consumed** — assigned to a local in `maingpt` and dropped. |
| **PortfolioEngine** | Directional exposure, correlated pairs from a static table (XAUUSD/XAGUSD ρ=0.85 binds), position/exposure ceilings. Total risk sums `p.get('risk_pct', 1.0)`; the connector never sets that key, so exposure ≈ 1% per open position. |
| **RiskEngine** | See §5. |
| **AdaptiveMemory** | SQLite journal (`data/trade_journal.db`) with `trades` + `strategy_weights`. **Inert** — `log_trade()` has no callers; both tables hold **0 rows**. |

### Session clock (`market/session_engine.py`)

Ordered list, first match wins, so overlaps resolve to the most specific window.

| Window | UTC | Weight | Kill zone |
|---|---|---:|---|
| London Silver Bullet | 07:00–08:00 | 1.2 | yes |
| London Kill Zone | 07:00–10:00 | 1.0 | yes |
| NY AM Silver Bullet | 14:00–15:00 | 1.2 | yes |
| London Close KZ | 15:00–17:00 | 0.8 | yes |
| NY Kill Zone | 12:00–15:00 | 1.0 | yes |
| NY PM Silver Bullet | 18:00–19:00 | 1.2 | yes |
| Asia | 00:00–06:00 | 0.4 | no |
| Off-session | remainder | 0.3 | no |

The weight feeds both probabilistic bias and the risk multiplier, so an off-session setup is
penalised twice.

### News blackout

`core/news_fetcher.py` pulls the FairEconomy mirror of the Forex Factory weekly calendar, keeps
HIGH-impact rows, maps currency → instruments, writes `news_events.json`; any failure preserves the
existing file. `core/news_guard.py` re-reads the file per query and reports a blackout from 30
minutes before to 15 minutes after an event.

**Pool A does not use it.** `NewsGuard` and `news_fetcher` are imported only by `scalper_agent.py`
and `backtest_scalper.py`. `maingpt.py` imports neither, so the swing pool trades straight through
high-impact releases despite the documentation describing the blackout as system-wide.

---

## 5. Pool A — main pipeline (`maingpt.py`)

Loops on `analysis_interval_seconds` (60), iterating symbols serially. Cycle-level state (account,
governance, compounding, positions, portfolio) is computed once; the rest is per symbol. Exceptions
inside a cycle are caught, logged, and followed by a 10s backoff — crash-resistant, but a broken
symbol is retried forever.

### Per-symbol pipeline

1. **Fetch** — 200 bars each of D1/H4/H1/M15/M5. A timeframe with <21 bars is dropped; missing M15
   (the execution timeframe) skips the symbol.
2. **Core engines** — liquidity, manipulation, displacement on **M15**; structure on **H1**. Order
   flow runs on M15 and is discarded.
3. **Multi-timeframe** — structure trend per TF, alignment score = share of non-ranging TFs agreeing
   with the majority; `aligned` at ≥0.67.
4. **Bias & regime** — `ProbabilisticEngine` → bull/bear/range + clarity; `RegimeEngine` → regime +
   preferred model.
5. **Setup construction** — `ExecutionModels.evaluate()` requires all three: a manipulation signal,
   tradeable structure (BOS or MSS), confirmed displacement. Then RETURN (entry at nearest FVG/OB
   after a sweep, SL beyond the zone by 1×ATR, TP at nearest opposing pool), falling back to
   CONTINUATION (momentum ≥0.75, entry at market). Either must clear `min_rr_ratio` = 2.0.
6. **Council vote** — LIA, MSA, RDA, CAIA, SEE always vote. EA and CRG vote only if a setup *and* a
   risk allocation exist; otherwise both are stubbed to ABSTAIN. CAIA re-runs the structure engine
   for every configured symbol against the current symbol's H1 frame as a fallback, making its
   cross-asset comparison self-referential when data is missing.
7. **Sizing** — see below.
8. **Approval** — reached only when `risk.approved`. Monte Carlo, then five gates.
9. **Execution** — `ExecutionAgent.execute()` re-validates and sends a market order with SL and TP
   via `MT5Connector.place_order` (deviation 20, IOC).

### Sizing chain

`RiskEngine.calculate()` rejects if realised RR < 2.0 or the clarity tier maps to 0%. Otherwise:

```
risk_pct = base[clarity]           # HIGH 16.0 · MEDIUM 8.0 · LOW 0.0
         × governance              # AGGRESSIVE 1.2 · BALANCED 1.0 · DEFENSIVE 0.6 · PRESERVATION 0.2
         × session_weight          # 0.3 – 1.2
         × regime                  # MANIPULATION 1.1 · ROTATION 0.5 · TRANSITION 0.4 · else 1.0
         × (0.7 + confidence × 0.5)

risk_pct = min(risk_pct, high_clarity_risk_pct × 1.5)   # 24.0
risk_pct = min(risk_pct, single_trade_dollar_cap_pct)   # 5.0  <- binding cap

lot = (balance × risk_pct/100) / (sl_pips × point × contract_size)
lot = max(0.01, round(lot, 2))
```

The **5% absolute cap is what actually governs** — a HIGH-clarity London setup computes far above
it, so the configured 16% tier is never realised. The trailing `max(0.01, …)` is the inverse of
Pool B's behaviour: Pool A rounds an undersized lot **up** to the broker minimum and therefore
over-risks on small accounts, where Pool B rejects the trade.

### The five gates (`fund/hedge_fund_os.py`)

Evaluated in this order, returning on first failure. **The code order differs from the order
documented in `CLAUDE.md`.**

| Gate | Name | Pass condition | Limit source |
|---|---|---|---|
| 1 | Simulation | Blended win prob ≥ `min_win_prob_to_trade` (0.52) **and** EV > 0. Win prob = 0.6 × directional bias + 0.4 × setup confidence; the 500 seeded Monte Carlo draws add noise but never change the verdict. | config |
| 2 | Agent consensus | Aligned ≥ `agent_consensus_threshold` (5) **and zero abstentions**. Only LIA/MSA/RDA/CAIA/SEE are passed in — EA and CRG are filtered out — so 5-of-5 means unanimity. | config |
| 3 | CRG risk | CRG vote ≠ ABSTAIN and `risk.approved`. | agent |
| 4 | Portfolio | Fewer than **4** open symbols, total risk under **6.0%**, no correlated pairs — hard-coded literals, not `max_positions` (2) or `max_total_exposure_pct` (40). | hard-coded |
| 5 | Regime alignment | MANIPULATION↔RETURN, EXPANSION↔CONTINUATION, or ROTATION/TRANSITION with either model. | hard-coded |

Gate 2 is the practical bottleneck: a NEUTRAL vote counts as aligned only at confidence ≥0.55, SEE
always returns NEUTRAL 0.55 (it has no history — see F-01), and any single ABSTAIN fails the gate
regardless of count.

### Governance

`CapitalGovernance` maps drawdown-from-peak to a mode each cycle; the peak is in-process and resets
to current balance on restart. Thresholds come from config (2/5/7%); the per-mode caps it returns
(`RISK_CAPS`) are hard-coded and read by no caller. The mode is consumed twice: as a sizing
multiplier, and as a ceiling in CRG via `config["governance_limits"]` (20/12/4/1%).

---

## 6. Pool B — scalper pipeline (SA-V2)

A linear chain of eleven filters; any failure returns early with a log line naming the stage. It
deliberately bypasses the voting council for latency, consulting the core engines read-only.

### Startup sequence

1. **Pool ledger** — constructed first, then seeded from MT5 deal history filtered on magic 88880:
   cumulative P&L since a persisted inception timestamp (`data/sa_pool_state.json`), today's
   realised P&L, today's entry count, current consecutive-loss streak.
2. **CRG priming** — the 15-minute loss pause is armed from the *actual* last-loss close time, not
   from process start, so a restart cannot manufacture a fresh lockout or erase a live one.
3. **Position adoption** — surviving magic-88880 positions are pulled back into `_open_trades` so
   timeout and EOD enforcement resume, followed by a one-shot timeout sweep for positions that aged
   past 120 minutes while the process was down.
4. **News calendar** — fetched at startup, refreshed every 6 hours.

### Per-scan filter chain

| # | Stage | Rule |
|---|---|---|
| 0 | State machine | PROTECTED (main DD >3%) → HALTED (daily loss limit) → PAUSED (≥2 consecutive losses, pause live) → IDLE (outside session) → ACTIVE. Scans only in ACTIVE. |
| 1 | Session window | Five named windows (Tokyo 00:00–02:00, Pre-London 06:30–07:00, London Open 07:00–08:30, London/NY 12:00–13:30, NY Lunch 16:30–17:30) **plus** a `Whole_day` 00:00–23:00 fallback — see F-04. |
| 2 | Per-symbol dedup | If an SA position is already open on this symbol, return immediately. Guard against repeated-entry loss clusters. |
| 3 | Micro liquidity | Equal H/L on the last 50 M5 bars within 0.03% tolerance + today's session extremes; nearest BSL above / SSL below. |
| 4 | Trigger detection | First match wins: **SWEEP_REJECTION** (M5, both directions) → **FVG_FILL** (M1, both) → **BOS_RETEST** (M5, *bullish only*) → **JUDAS** (M5 session-open fade, both). Each emits entry, SL, TP1 at 1R, TP2 at 2R. |
| 5 | Short-term bias gate | *Range whipsaw guard*: if the last 20 M5 bars span ≤4×ATR with ≥2 touches per edge, fades are allowed only at the matching extreme (buys ≤25% into range, sells ≥75%). *Don't-chase*: reconstructs today's Asia/London/NY pools and blocks any trigger matching a sweep taken in the last 60 minutes. *Intraday structure*: ATR-normalised 12-bar M5 net move, ±0.5×ATR. *HTF*: H1 then H4 trend — raises confidence to HIGH, never blocks. |
| 6 | Thin liquidity | 19:00–22:00 and 16:00–17:00 UTC require HIGH confidence. |
| 7 | Consultation | 30s-cached read of structure+displacement (M5) and liquidity+manipulation (H1) into the regime engine. Blocks on failure, snapshot >60s, or latency >200ms. Then three gates (below). |
| 8 | Entry validation | Absolute spread ceiling (XAUUSD 8.0p, XAGUSD 5.0p, USOIL 6.0p, default 5.0p); spread ≤25% of SL distance; institutional SL floor (XAUUSD 350, XAGUSD 120, USOIL 200 internal pips). |
| 9 | SA-CRG | Daily loss under limit → main DD ≤3% → <2 SA positions → spread OK → no news blackout → no active 15-minute loss pause. |
| 10 | Lot sizing | `risk_usd / (sl_pips × pip_value)`. Below broker minimum ⇒ **reject**, never round up — the "lot floor filter". |
| 11 | Execute | Market order, magic 88880, deviation 10, IOC, comment `SA_<trigger[:4]>`. **SL and TP1 only** are sent. |

### Consultation gates

| Gate | Rule |
|---|---|
| 1 | Per-trigger regime whitelist: SWEEP_REJECTION and JUDAS need MANIPULATION or ROTATION; BOS_RETEST needs EXPANSION; FVG_FILL needs EXPANSION or MANIPULATION. TRANSITION is whitelisted for nothing and always blocks. A `STRESS` regime is rejected first — but `RegimeEngine` never emits that value, so the branch is dead. |
| 2 | BOS_RETEST additionally requires `is_displaced` **and** momentum ≥0.65 **and** at least one FVG. |
| 3 | If a macro H1 pool sits within 1.5× the TP1 distance and beyond the current TP2, TP2 is realigned to it. Since TP2 is never sent to the broker, this has no execution effect. |

### Position lifecycle

A 30s monitor closes anything open past **120 minutes** at market, and forces every position closed
once UTC hour reaches **23**. Positions that disappeared between polls are reconciled by querying
deal history with the `position=` filter (a documented fix — the earlier `ticket=` filter matched
deal tickets, not position IDs, silently returning $0 for every close and freezing the loss
counters). Realised P&L sums profit + commission + swap. A date rollover triggers the daily reset:
close everything, append to `logs/sa_daily_journal.json`, then adjust the pool — halved after three
consecutive losing days, +10% after five consecutive winning days.

---

## 7. Trade Guardian Agent

Polls every 2 seconds with a 15-second data cache, discovers positions from `positions_get()`, and
reconstructs each one's entry ATR from M5 bars at the position's open time. Never opens a position.
New positions are ignored for their first **10 minutes** ("entry breathing"); after that each poll
runs peak tracking → SL ladder → exit checks → TP extension.

### Stop-loss ladder

| Stage | Arms at peak | Stop placement | Extra condition |
|---|---|---|---|
| 0 | — | Original SL from the opening agent | — |
| 1 | ≥ 1.0R | Breakeven (entry) | Last **2 closed** M5 candles must have closed on the trade's side of entry, else BE is deferred and the original stop holds |
| 2 | ≥ 1.5R | Trail 1.0 × entry ATR | Structure lock: if the 10-bar swing is within 0.5×ATR of the trail, snap just beyond that swing |
| 3 | ≥ 2.5R | Trail 0.6 × entry ATR | Structure lock engages when the 5-bar swing is within 1.0×ATR of price |

Every proposed stop is clamped so it can never be worse than entry once stage ≥1, must move only
favourably, and must differ from the live stop by >10 points before an order is sent. Thresholds
were deliberately raised from 0.5/1.0/2.0R after breakeven stops were taken out by routine
retracements.

### Exits

| Mechanism | Arms when | Fires when |
|---|---|---|
| Early close — reversal candle | Peak ≥1.0R, only while in profit | Engulfing against the position, or a rejection wick >2× the body |
| Early close — momentum loss | " | Two successive ranges each <80% of the prior, with no new extreme in the trade's direction |
| Early close — structure shift | " | Price trades beyond the opposing 4-bar swing |
| No-progress close | Always | Open ≥60 minutes with peak ≤0.3R — closed at market before it can drift back to the original stop |
| TP extension | Not yet extended, original TP > 0 | Within 0.5×ATR of TP with three same-direction candles, small closing wick, range ≥0.8×ATR → close 50% and extend TP to the 20-bar M15 structure target |

Every action appends to `logs/tga_action_log.json` with previous/new SL and TP, current R, peak R,
and **maximum adverse excursion** — the richest dataset in the repo for tuning entry timing and stop
distance. The whole file is re-read and rewritten per append, so write cost is O(n²).

**Interaction with Pool B:** the Guardian and the Scalper both manage magic-88880 positions
independently. The Guardian may trail or close early; the Scalper may close the same ticket on its
120-minute timeout or at 23:00. Neither knows about the other, and ordering is undefined.

---

## 8. Configuration

### `config.json` — Pool A only

The Scalper and Guardian never read it; their parameters live in CLI flags and class constants.

| Key | Value | Effect |
|---|---|---|
| `symbols` | XAUUSD, XAGUSD, USOIL | Scan list; also CAIA's cross-asset set |
| `analysis_interval_seconds` | 60 | Cycle period |
| `risk.high_clarity_risk_pct` | 16.0 | HIGH tier — never realised; the 5% cap binds first |
| `risk.medium_clarity_risk_pct` | 8.0 | MEDIUM tier |
| `risk.low_clarity_risk_pct` | 0.0 | LOW ⇒ hard reject |
| `single_trade_dollar_cap_pct` | 5.0 | **Operative per-trade ceiling** |
| `risk.max_positions` | 2 | Read by CRG; Gate 4 ignores it and uses 4 |
| `risk.max_total_exposure_pct` | 40.0 | Read by CRG/PortfolioEngine; Gate 4 uses 6.0 |
| `risk.min_rr_ratio` | 2.0 | Enforced in ExecutionModels, RiskEngine, EA |
| `agent_consensus_threshold` | 5 | Gate 2 threshold against a 5-agent pool ⇒ unanimity |
| `simulation.monte_carlo_runs` | 500 | Draws per candidate; does not affect the verdict |
| `simulation.min_win_prob_to_trade` | 0.52 | Gate 1 threshold |
| `governance.*_dd_threshold` | 2 / 5 / 7 | Mode boundaries by drawdown-from-peak |
| `governance_limits` | 20 / 12 / 4 / 1 | Per-mode risk ceiling enforced by CRG |
| `manipulation.symbol_sweep_pct` | per symbol | Sweep pierce depth; `_doc*` keys stripped before use |
| `meta_fund.*` | 10 / 20 / 0.5 | Rebalance cadence and lookback — inert, see F-01 |
| `dry_run` | false | Overridden by `--dry-run` |

### Command lines

```bash
# Pool A — flags: --dry-run, --config
python maingpt.py
```

```bash
# Pool B — every parameter is a flag
py -3.14 -E scalper_agent.py --pool 900 --risk 0.03 --symbols XAUUSD --interval 30 --loss-limit 100.0 --pool-mode FRESH --sweep-location-config config.json --market-location-mode ACTIVE
```

```bash
# Guardian — no functional flags
python trade_guardian_agent.py
```

```bash
# Backtester (Pool B strategy only)
python backtest_scalper.py --from 2026-04-12T00:00:00 --to 2026-05-12T00:00:00 --pool 500 --risk 0.02 --symbols XAUUSD,XAGUSD,USOIL --out logs/backtest_results.json
```

Pool B defaults: `--pool 100.0`, `--risk 0.02`, `--interval 30`, `--loss-limit 50.0`.

**`--dry-run` means different things.** In Pool A it also switches `MT5Connector` to *synthetic
random-walk OHLCV* and a fake $10,000 account — useful for exercising the pipeline, useless for
evaluating signals. In Pool B it keeps the real connection and real data, skipping only
`order_send`.

### Credentials

`apex_ai/.env` holds `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` (plus `MT5_MODE` per the docs, which
no code reads). Template: `.env.template`; `setup_mt5.py` writes it interactively. All three
processes load and initialise MT5 independently.

> **Secret hygiene.** A populated `.env` is in the working tree. The project is not under version
> control, so nothing has been committed — but before running `git init`, add `.env` to
> `.gitignore` and rotate the credentials if the tree has ever been shared.

---

## 9. Persistence & telemetry

| Artefact | Writer | Shape | Notes |
|---|---|---|---|
| `logs/apex_ai.log` | Pool A | Append-only | ~6.7 MB |
| `logs/scalper_agent.log` | Pool B | Append-only | ~13.1 MB; one block per scan per symbol |
| `logs/tga_log.txt` | Guardian | Append-only | ~379 KB |
| `logs/tga_action_log.json` | Guardian | JSON array, full rewrite per append | Peak and adverse R per action |
| `logs/scalper_log.json` | Pool B | JSON array, updated in place on close | Spec-format trade journal |
| `logs/sa_daily_journal.json` | Pool B daily reset | JSON array, one row per reset | Drives the pool halving/growth rule |
| `data/sa_pool_state.json` | Pool B | Single key — inception timestamp | Lower bound of the cumulative P&L query |
| `data/trade_journal.db` | Pool A (nominally) | SQLite `trades`, `strategy_weights` | **0 rows** — never written |
| `news_events.json` | news_fetcher | HIGH-impact events + affected symbols | Refreshed every 6h by Pool B only |

No log rotation is configured anywhere; both daemon logs grow without bound.

**Operator scripts.** `get_agent_pnl.py` and `pnl_statement.py` break P&L down by magic;
`check_history.py`, `check_trade_status.py`, `export_history_md.py` dump activity;
`analyze_failure.py`, `get_session_hl.py`, `get_xauusd.py` are one-off diagnostics;
`apply_sl_tp.py` retro-fits stops to manual positions; `take_random_trade.py` opens a position for
plumbing tests; `analyze_backtest_results.py` and `plot_backtest_results.py` post-process backtest
JSON. None are part of a pipeline.

---

## 10. Backtesting harness

`backtest_scalper.py` is the only backtester; Pool A has none. It replays `copy_rates_range` per
symbol, walks the bar index forward, and simulates each entry against subsequent bars until TP1, SL,
or a 24-bar cutoff. Size uses `order_calc_profit` against the live symbol spec; balance compounds.

### Fidelity gaps versus the live Scalper

| Area | Live agent | Backtester |
|---|---|---|
| Trigger timeframes | M5 primary, M1 for FVG | **M15 in the M5 slot, M5 in the M1 slot** — different setups entirely |
| Regime handling | Per-trigger whitelist; MANIPULATION is the *preferred* regime for sweep fades | **Blanket veto of MANIPULATION** — rejects the live system's primary trade |
| Bias gate | Short-term bias filter with range guard and don't-chase | Absent |
| Entry validation | `step3_validate(..., sl_pips=…)` | Called without `sl_pips` ⇒ spread-ratio and SL-floor guards skipped |
| Risk governor | Five SA-CRG checks, loss pause, position cap | Absent; `max_open` bookkeeping marks every position closed immediately so the cap never binds |
| Thin-liquidity hours | HIGH confidence required in two UTC bands | Absent |
| Spread | Live bid/ask per scan | Fixed `--spread-pips` (default 2.5), not deducted from P&L |
| Exit priority | Broker fills | SL checked before TP within a bar — pessimistic for longs; no intrabar path modelling |
| Guardian | Trails, extends, early-closes | Absent — every trade runs to TP1/SL/timeout |

> The 62.5% win rate / +82.5% return figures in `CLAUDE.md` and `BACKTEST_REPORT_30DAYS_500USD.md`
> come from this harness. Given the timeframe substitution and the inverted regime veto, they
> describe a **different strategy** from the one currently trading and should not be used as a live
> expectation.

---

## 11. Observed behaviour

From `logs/scalper_log.json` — 287 records spanning **2026-05-06 → 2026-05-15**.

| Dimension | Distribution |
|---|---|
| Outcome | LOSS 173 · TIMEOUT 67 · WIN_TP1 37 · OPEN 6 · EOD_CLOSE 4 |
| Trigger | SWEEP_REJECTION 279 · JUDAS 8 · FVG_FILL 0 · BOS_RETEST 0 |
| Instrument | USOIL 117 · XAGUSD 80 · XAUUSD 54 · BTCUSD 31 · ETHUSD 5 |

Three things follow directly:

- **One trigger carries the system.** 97% of entries are sweep rejections. FVG_FILL and BOS_RETEST
  have never fired — consistent with BOS_RETEST having no bearish branch and being gated to
  EXPANSION only.
- **Timeouts are a third of closes.** 67 trades hit the 120-minute cap rather than a target, so the
  TP1-at-1R geometry frequently fails to resolve inside the window.
- **Traded symbols drifted from config.** BTCUSD and ETHUSD appear although the documented
  production set is XAUUSD/XAGUSD/USOIL — passed via `--symbols` during the period.

The daily journal is rougher than the trade log: eleven rows including `-86.16` (2026-05-15) and
`-19.32` (2026-05-13) against `+152.62` (2026-05-08), with `pool_end` jumping between $84, $500,
$652 and $980 — the pool size was changed between runs, so the series is not a continuous equity
curve. Summing `sa_pool_pnl` across the trade log gives `+$53.63`, which does not reconcile.
**Treat neither as an account of record**; broker deal history via `get_agent_pnl.py` is the only
authoritative P&L.

A representative live scan (tail of `logs/scalper_agent.log`) rejecting on three different stages in
one cycle:

```
SA Cycle #416 | State=ACTIVE | Session=Whole_day | Pool=$401.04 | Daily PnL=$+13.26 | Open=1
SA USOIL:  STB GATE blocked BULLISH SWEEP_REJECTION — Don't chase — trigger BULLISH
           matches fresh sweep (ASIA_HIGH@99.1480 taken by LONDON 54m ago)
SA XAGUSD: STB GATE passed [MEDIUM] — Short-term BEARISH matches trigger; HTF=BULLISH
SA XAGUSD: CONSUL Gate 1 passed | Regime=MANIPULATION matches SWEEP_REJECTION
SA XAGUSD: STEP 3 BLOCKED — SL 40.8p below institutional floor 120p (noise-grade sweep)
```

Note `Session=Whole_day` — the fallback window, not one of the five kill zones.

---

## 12. Findings

Severity reflects impact on live trading behaviour, not code aesthetics.

### F-01 — HIGH — The learning loop is disconnected
`AdaptiveMemory.log_trade()` has no callers anywhere in the tree; both tables in
`data/trade_journal.db` hold zero rows. Downstream, SEE always returns "no history — neutral trust"
at 0.55, and `MetaFundEngine._rebalance()` always scores every sub-fund at the 0.33 default, so
capital weights never move. `MetaFundEngine.get_capital_weight()` is never called either, so even a
working rebalance would be display-only. Everything described as v6–v7 strategy evolution and v11
meta-fund evolution is presentation, not control.
*`intelligence/adaptive_memory.py` · `agents/see.py` · `fund/meta_fund.py` · `maingpt.py:134`*

### F-02 — HIGH — Pool A's magic number is invisible to the Guardian
`MT5Connector` stamps `20260426`; the Guardian's registry recognises only `12345`, `88880`, `77770`.
Every Pool A position logs as `origin_agent="UNKNOWN"`. Fix by making the magic a single shared
constant used by both writer and readers.
*`market/mt5_connector.py:218,249` · `trade_guardian_agent.py:495-498`*

### F-03 — HIGH — Gate 4 ignores the configured portfolio limits
Gate 4 hard-codes `< 4` positions and `< 6.0%` total risk while config declares `max_positions: 2`
and `max_total_exposure_pct: 40.0`. Two independent limits apply depending on which component is
asked. Compounding this, `PortfolioEngine` derives total risk from a `risk_pct` key the connector
never sets, defaulting to 1.0 per position — so the exposure figure the gate tests is a position
count in disguise.
*`fund/hedge_fund_os.py:141-143` · `intelligence/portfolio_engine.py:76`*

### F-04 — MEDIUM — The Scalper's session gate is neutralised by its own fallback
`SESSION_WINDOWS` lists five kill zones and then `Whole_day` 00:00–23:00. Because `get_state()`
returns the first match, any time outside a kill zone still matches `Whole_day` and reports
`in_window=True`. IDLE is effectively unreachable and the agent scans 23 hours a day; live logs
confirm `Session=Whole_day` during ordinary trading. If round-the-clock scanning is intended, delete
the kill-zone list and say so; if not, remove the fallback.
*`scalper/session_checker.py:37`*

### F-05 — MEDIUM — TP2 is computed everywhere and used nowhere
Every trigger emits TP2, the consultant's Gate 3 realigns it to a macro pool, and the trade logger
records it — but `_execute_trade` sends only `tp = trigger.tp1`. There is no runner leg and no
partial-close-at-TP1 in the Scalper, so the two-target design exists only on paper. The Guardian's
TP extension is the only mechanism that can take a trade past 1R.
*`scalper_agent.py:531` · `scalper/sa_consultant.py:_apply_lia_override`*

### F-06 — MEDIUM — Two processes manage the same positions without coordination
The Guardian trails and closes magic-88880 positions while the Scalper independently enforces its
120-minute timeout and 23:00 close on the same tickets. A Guardian partial close mutates volume the
Scalper still believes is whole, and the Scalper's timeout path books `pos.profit` rather than the
reconciled deal sum used on the normal close path — two P&L definitions for the same event.
*`trade_guardian_agent.py:_process_position` · `scalper_agent.py:_monitor_open_trades`*

### F-07 — MEDIUM — Backtest results do not describe the live strategy
See §10. The harness feeds M15 bars where the agent uses M5, vetoes the MANIPULATION regime the live
agent specifically trades, and omits the bias gate, the risk governor, the SL floor and the
spread-ratio guard.
*`backtest_scalper.py:170-215`*

### F-08 — MEDIUM — Gate 2 demands unanimity, not a 5-of-7 majority
`maingpt` passes only the five non-execution agents into Gate 2 while the threshold stays at 5, and
a single ABSTAIN fails the gate regardless of count. The documented "≥5 of 7" is arithmetically a
different rule. Either pass all seven votes or lower the threshold; as written the system is far
more conservative than intended.
*`maingpt.py:414-417` · `fund/hedge_fund_os.py:96-110`*

### F-09 — LOW — Gate 3's rejection log always prints "?"
Gate 3 formats `getattr(crg_vote, 'reason', '?')`, but `AgentVote` defines `reasoning`. The specific
CRG veto — governance cap, drawdown, correlated position — is never visible in the log, which is
exactly the information the comment above the line says it was added to surface.
*`fund/hedge_fund_os.py:128`*

### F-10 — LOW — BOS_RETEST is bullish-only
`_check_bos_retest` implements the break-above-and-retest case and returns; there is no bearish
counterpart. It is also whitelisted to EXPANSION only. Both facts are consistent with the trigger
never firing in 287 logged trades.
*`scalper/trigger_engine.py:_check_bos_retest`*

### F-11 — LOW — Dead branches and unused computation
The consultant's `STRESS` regime check can never fire (`RegimeEngine` emits only EXPANSION,
MANIPULATION, ROTATION, TRANSITION). `OrderFlowEngine` runs every cycle and is discarded.
`HTFBiasFilter` is constructed on every Scalper start but superseded by the short-term bias filter.
`CapitalGovernance.RISK_CAPS` is returned but read by no caller. `_find_equals` accepts
`current_price` and `side` and uses neither, and runs an O(n²) pairwise scan per trigger check.

### F-12 — LOW — Operational hygiene
No log rotation (6.7 MB and 13.1 MB and growing). `tga_action_log.json` is fully re-read and
rewritten per action. `matplotlib` is imported but unpinned; `pandas-ta`, `scipy`, `colorama` are
pinned but unused. No tests of any kind, and the tree is not under version control, so none of the
behaviour above is protected against regression.

### Suggested order of work

1. Unify magic numbers behind one constant (F-02) — one line per site, immediately correct attribution.
2. Decide the Scalper's session policy and make the code say it (F-04).
3. Reconcile Gate 2 and Gate 4 with config (F-03, F-08), then re-observe Pool A's execution rate.
4. Either wire `log_trade()` into both close paths or delete the SEE/meta-fund surface (F-01). A
   half-connected feedback loop is worse than none, because the dashboard reports it as live.
5. Define ownership between the Guardian and the Scalper for magic-88880 positions (F-06).
6. Rebuild the backtester against the live filter chain before quoting any performance number again (F-07).

---

## 13. Operations

### Cold start (Windows)

```bash
taskkill /F /IM python.exe /T
```

```bash
taskkill /F /IM terminal64.exe /T
```

```bash
cd apex_ai && python maingpt.py
```

```bash
py -3.14 -E scalper_agent.py --pool 900 --risk 0.03 --symbols XAUUSD --interval 30 --loss-limit 100.0 --pool-mode FRESH --sweep-location-config config.json --market-location-mode ACTIVE
```

Verify three scripts are up:

```bash
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Format-List ProcessId, CommandLine
```

Windows execution aliases make each script appear as two PIDs (alias + real interpreter) — that is
one instance. The Guardian's port lock is the reliable duplicate check: a second Guardian exits with
`"TGA is already running in another process."`

### Health checks

| Question | Command / signal |
|---|---|
| Is the Scalper scanning? | `tail logs/scalper_agent.log` — one `SA Cycle #n` block every 30s |
| Why did a setup not fire? | grep for `BLOCKED` — each stage names itself |
| Is the Guardian acting? | `logs/tga_action_log.json` — newest entry timestamp |
| What has each agent earned? | `python get_agent_pnl.py` |
| What is open right now? | `python check_trade_status.py` |
| Is pool state sane after restart? | Look for `SA Capital Pool restored` and `SA adopted … position(s)` |

### Safe shutdown

Both daemons handle `KeyboardInterrupt`; Pool A disconnects MT5 in a `finally`, Pool B shuts MT5
down after `run()` returns. The Guardian is never signalled by its parent, so killing `maingpt`
leaves the Guardian running and still managing positions — stop it explicitly if the account is
meant to be unmanaged.

---

## 14. Glossary

| Term | Meaning in this codebase |
|---|---|
| BSL / SSL | Buy-/sell-side liquidity — resting stop clusters above highs and below lows, modelled as `LiquidityPool` with a strength score |
| Sweep | Price pierces a pool by the symbol's calibrated percentage and closes back through it. Trigger for the RETURN model and SWEEP_REJECTION |
| BOS / MSS / CHoCH | Break of structure (continuation) vs market structure shift (reversal). `is_tradeable()` requires at least one |
| FVG | Fair value gap — a three-candle imbalance where candle 1 and candle 3 extremes do not overlap |
| Order block | Last opposing candle before an impulsive move; entry zone when no FVG is available |
| Judas swing | False directional move at session open that reverses through the opening price |
| Displacement | Momentum ≥0.60 — mean 5-bar body relative to 1.5×ATR. Prerequisite for any Pool A setup |
| Kill zone | High-participation UTC window. Pool A weights it into bias and sizing; Pool B nominally gates on it (see F-04) |
| Premium / discount | Position relative to the midpoint of the dealing range — buys preferred in discount, sells in premium |
| R | One unit of initial risk (entry-to-stop distance). The Guardian's ladder is expressed in R |
| MAE | Maximum adverse excursion — most negative R a position reached; recorded per Guardian action |
| Lot floor filter | Pool B rejecting any trade whose correct lot size falls below the broker minimum, rather than rounding up and over-risking |
