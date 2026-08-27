# 🚀 APEX AI Project — Whole-System Enhancement Recommendations

**Generated:** 2026-05-09
**Status:** Documentation only — no code changes pending approval
**Scope:** Full audit of `apex_ai/` — main orchestrator, council agents, core engines, intelligence layer, fund OS, market layer, scalper agent, trade guardian agent

This is the master audit covering the entire APEX AI Trading System. It complements the existing module-specific audits:
- [`SCALPER_ENHANCEMENT_RECOMMENDATIONS.md`](SCALPER_ENHANCEMENT_RECOMMENDATIONS.md) — Scalper Agent specifics
- [`TGA_ENHANCEMENT_RECOMMENDATIONS.md`](TGA_ENHANCEMENT_RECOMMENDATIONS.md) — Trade Guardian Agent specifics

This master audit focuses on **system-wide / cross-cutting issues**, plus per-module findings for files not covered elsewhere.

---

## 🟥 CRITICAL (Safety/Correctness)

### C1. Three Independent MT5 Connections — No Coordination Protocol

**Current behavior:** [`maingpt.py`](apex_ai/maingpt.py) instantiates `MT5Connector` (magic 20260426). [`scalper_agent.py`](apex_ai/scalper_agent.py:600) calls `mt5.initialize()` directly (magic 88880). [`trade_guardian_agent.py`](apex_ai/trade_guardian_agent.py:646) calls `mt5.initialize()` directly (magic 99990 for modifications).

**Problem:** MT5 supports only **one active connection per process group** in many configurations. Three processes attempting connection on the same machine can race during initialization, fail authentication, or step on each other's request streams. There's no documented protocol for which process owns the connection. Worse: TGA modifies positions opened by MAIN and SA without any handshake — it scans by magic number and assumes it can act. If SA opens a trade and TGA's `_sync_registry` runs before SA finishes setting TP, TGA may read incomplete state.

**Enhancement:** Define an inter-process coordination protocol. Options: (a) shared lock file, (b) message queue (Redis/SQLite-based), (c) MAIN process owns MT5 connection and SA/TGA query MAIN via local IPC. At minimum, document the assumed sequence (MAIN starts first, SA second, TGA third) and add startup checks to detect concurrent initialization.

**Impact:** CRITICAL
**Complexity:** Hard (architectural)

---

### C2. State Persistence Missing Across All Three Bots

**Current behavior:** SA, TGA, and MAIN all hold state in memory only (registries, daily counters, peak profits, pause timers).

**Problem:** Any restart loses critical tracking state:
- **SA:** daily P&L resets → daily loss limit becomes exploitable across restarts
- **TGA:** peak_profit_r resets → trade at 2R peak would re-trail at Stage 0 instead of Stage 3
- **MAIN:** strategy weights reload from SQLite (good), but cycle counter, peak balance, last execution all reset

**Enhancement:** Create a unified state directory `apex_ai/state/` with one JSON file per bot (`main_state.json`, `sa_state.json`, `tga_state.json`). Each cycle, write atomic snapshot. On startup, load if same UTC date. Reconcile against MT5 positions to drop orphaned entries.

**Impact:** CRITICAL
**Complexity:** Medium

---

### C3. No Reconnection Logic in SA or TGA

**Current behavior:** Only `MT5Connector` (used by MAIN) has retry logic ([`mt5_connector.py:68-84`](apex_ai/market/mt5_connector.py:68)). SA's `mt5.initialize()` is a one-shot call. TGA never reconnects.

**Problem:** If MT5 drops mid-session, SA and TGA enter zombie states — running, polling, but unable to read/write. Open positions sit unmanaged. The robust reconnection logic in `MT5Connector` exists but isn't reused.

**Enhancement:** SA and TGA should use `MT5Connector` instead of raw `mt5.initialize()`. This unifies retry logic, dry-run handling, and synthetic data fallback. Or extract the connection logic into a shared `core/mt5_session.py` and use it everywhere.

**Impact:** CRITICAL
**Complexity:** Medium

---

### C4. RiskEngine Doesn't Check Broker Volume Minimum

**Current behavior:** [`risk_engine.py:111`](apex_ai/intelligence/risk_engine.py:111) — `lot_size = max(0.01, round(lot_size, 2))` hardcodes 0.01 as minimum, ignoring per-symbol broker `volume_min`.

**Problem:** Some brokers/symbols require 0.10 minimum (e.g., index CFDs, low-priced commodities). Submitting 0.01 lots is rejected by the broker, the trade is logged as "executed" but actually fails silently. SA's [`scalper_agent.py:493`](apex_ai/scalper_agent.py:493) handles this correctly (rejects undercapitalized trades) — but main `RiskEngine` doesn't.

**Enhancement:** Pass `volume_min`, `volume_max`, `volume_step` from `MT5Connector.get_symbol_info()` into `RiskEngine.calculate()`. Reject trade if `lot_size < volume_min`. Round to `volume_step` (not just 2 decimal places). Match SA's logic.

**Impact:** CRITICAL
**Complexity:** Easy

---

### C5. AdaptiveMemory Database Has No Migration Strategy

**Current behavior:** [`adaptive_memory.py:61`](apex_ai/intelligence/adaptive_memory.py:61) — `DB_VERSION = 1` constant declared but never used.

**Problem:** When schema changes (adding columns, renaming tables), there's no migration path. `_init_db()` uses `CREATE TABLE IF NOT EXISTS` which won't update existing tables. Production deployments stuck on old schemas silently fail when new code expects new columns.

**Enhancement:** Implement a versioned migration system: store `DB_VERSION` in a `meta` table, on startup compare with code's `DB_VERSION`, run incremental migrations between versions. Even a basic `migrations/v1_to_v2.sql` directory pattern works.

**Impact:** CRITICAL (upgrade path)
**Complexity:** Easy-Medium

---

## 🟧 HIGH IMPACT (Performance/Profitability)

### H1. ATR Computed Inconsistently Across Modules

**Current behavior:** Different ATR implementations across the codebase:
- `trigger_engine.py:382` — simple mean of TR values
- `trade_guardian_agent.py:143` — simple mean (`true_range.rolling(period).mean()`)
- `displacement_engine.py` — has its own `_calc_atr` (called from `maingpt.py:353`)
- Possibly more in regime/structure engines

**Problem:** Different ATR values across modules mean the SA's stop placement, TGA's trail distance, and MAIN's risk sizing all use slightly different volatility estimates. Mismatches with TradingView/MT5 indicator (which uses Wilder's smoothing) make manual debugging impossible. Industry standard is Wilder's: `ATR_today = ((period-1) × ATR_prev + TR) / period`.

**Enhancement:** Create `core/indicators.py` with a single canonical `wilder_atr(df, period)` function. Replace all simple-mean ATR calls. Document that this matches TradingView and MT5 default ATR.

**Impact:** HIGH (correctness across system)
**Complexity:** Easy

---

### H2. CAIA Structure Analysis Runs O(n²) Inside Symbol Loop

**Current behavior:** [`maingpt.py:347-350`](apex_ai/maingpt.py:347) — for each symbol, the per-symbol loop calls `caia.analyze(...)` which builds a `structures` dict by re-running `self.structure_eng.analyze()` for **every** symbol. With 4 symbols, that's 16 structure analyses per cycle instead of 4.

**Problem:** Each `structure_eng.analyze()` call processes 200 OHLCV bars. Running it 4× per cycle for a 4-symbol portfolio means 4× wasted compute. This scales poorly: 8 symbols → 64 analyses per cycle, 16 symbols → 256.

**Enhancement:** Compute all structures once at the start of the cycle, pass the dict into the per-symbol loop. Cache by (symbol, timeframe, last_bar_time) so a structure analysis is reused if the latest bar hasn't changed.

**Impact:** HIGH (cycle latency)
**Complexity:** Easy

---

### H3. HedgeFundOS Counts NEUTRAL Votes as Aligned

**Current behavior:** [`hedge_fund_os.py:92-95`](apex_ai/fund/hedge_fund_os.py:92) — `if vote.vote == expected_vote or (vote.vote == "NEUTRAL" and vote.confidence >= 0.55)` counts NEUTRAL as aligned if confidence ≥ 0.55.

**Problem:** This significantly weakens the consensus requirement. A "5/7 agents aligned" check passes if 3 are truly aligned and 2 are NEUTRAL — meaning only 3 agents actually agreed with the trade direction. The 0.55 threshold is arbitrary. The whole point of consensus voting is directional agreement; NEUTRAL is by definition non-directional.

**Enhancement:** Drop the NEUTRAL allowance. Only count agents whose vote matches the expected direction. Optionally allow NEUTRAL with very high confidence (>0.85) to count as a half-vote (0.5 instead of 1.0). Document the rationale clearly.

**Impact:** HIGH (signal quality)
**Complexity:** Trivial

---

### H4. Risk Engine Multipliers Compound Without a Hard Cap on Stack

**Current behavior:** [`risk_engine.py:100-102`](apex_ai/intelligence/risk_engine.py:100) — final risk = `base_risk × gov_mult × sess_mult × reg_mult × conf_mult`, capped at `high_risk_pct × 1.5`.

**Problem:** With AGGRESSIVE governance (1.2), prime session (1.0), MANIPULATION regime (1.1), and high confidence (0.7 + 1.0×0.5 = 1.2), the multiplier stack is 1.2 × 1.0 × 1.1 × 1.2 = 1.584x — already above the 1.5x cap. The 1.5x cap is then enforced, but the cap is on `high_risk_pct` which may not be the actual `base_risk` used. If `base_risk` is `medium_risk_pct=1%` but cap is `high_risk_pct × 1.5 = 3%`, the medium-clarity trade can stack to 1.584% → meaningfully over-risked relative to design intent.

**Enhancement:** Cap relative to `base_risk` instead of `high_risk_pct`. Or better: define a single `max_combined_multiplier = 1.5` and enforce `final_risk = base_risk × min(combined_mult, max_combined_multiplier)`. Document the design intent.

**Impact:** HIGH (risk control)
**Complexity:** Easy

---

### H5. SimulationEngine Uses Fixed RNG Seed

**Current behavior:** [`simulation_engine.py:61`](apex_ai/fund/simulation_engine.py:61) — `rng = np.random.default_rng(42)`.

**Problem:** Every Monte Carlo simulation uses the same seed → identical noise distribution → simulation results are deterministic given inputs. This is fine for reproducibility in tests but in production it means the system never sees the full distribution of possible outcomes. Two trades with identical setups get identical sim results, even though real execution will differ.

**Enhancement:** Use a non-deterministic seed in production (`np.random.default_rng()` with no arg, or seed from `os.urandom`). Allow override via environment variable for tests/backtests where reproducibility matters.

**Impact:** HIGH (simulation realism)
**Complexity:** Trivial

---

### H6. Cycle Intervals Inconsistent Across Bots

**Current behavior:**
- MAIN cycle: 60 seconds (configurable, but no shared default)
- SA cycle: 30 seconds
- TGA cycle: 2 seconds

**Problem:** No shared scheduler. Each bot polls at its own rate. TGA's 2-second poll is justified for SL trailing precision, but it means TGA reads MT5 positions 30× per minute — much heavier load than the others combined. Conversely, MAIN's 60s interval means a market move detected at second 1 doesn't get an analysis until second 60 — large gap on volatile instruments.

**Enhancement:** Define cycle intervals based on each bot's actual sensitivity:
- MAIN: 60s (analysis-heavy, OK)
- SA: 30s (M5 scans, OK)
- TGA: 5-10s (SL trails — 2s is overkill, 5s is fine)
Profile MT5 query load: at 2s polling × 24h × 4 symbols = 172,800 queries/day from TGA alone. Reducing to 5s cuts to 69,120 — 60% reduction in API load.

**Impact:** HIGH (operational, broker rate limits)
**Complexity:** Trivial

---

## 🟨 MEDIUM IMPACT (Robustness/Reliability)

### M1. Generic Exception Catch with Sleep — Tight Error Loop Risk

**Current behavior:** Both [`maingpt.py:188-190`](apex_ai/maingpt.py:188) and [`trade_guardian_agent.py:620-621`](apex_ai/trade_guardian_agent.py:620) catch generic `Exception`, log it, and continue.

**Problem:** Persistent errors (malformed broker response, missing config key, broken indicator) cause every cycle to fail, log a stack trace, and retry — flooding logs at 60-second intervals (MAIN) or 2-second intervals (TGA). Same issue identified in TGA audit (M4) and applies to MAIN too.

**Enhancement:** Track error count per error type. After N consecutive identical errors, escalate (longer sleep, alert, halt). Reset on first successful cycle. Apply consistently across MAIN, SA, TGA.

**Impact:** MEDIUM
**Complexity:** Easy

---

### M2. Multiple Logging Destinations — No Unified Telemetry

**Current behavior:** Each bot logs to a different file:
- `logs/apex_ai.log` (MAIN)
- `logs/scalper_agent.log` (SA)
- `logs/tga_log.txt` (TGA)
- `logs/scalper_log.json` (SA trades)
- `logs/tga_action_log.json` (TGA actions)
- `data/trade_journal.db` (SQLite, MAIN trades only)

**Problem:** No unified view of system activity. Operator must tail 3 log files simultaneously. Cross-bot incidents (SA opens trade → TGA modifies it → MAIN sees it in portfolio) cannot be reconstructed without manually correlating timestamps across files.

**Enhancement:** Migrate all action logs to SQLite (extend the existing `trade_journal.db`):
- `bot_actions` table: timestamp, bot_name, action_type, ticket, details_json
- `state_transitions` table: timestamp, bot_name, from_state, to_state, reason
- Keep human-readable text logs as before for live tailing
- Add `--query` CLI tool for cross-bot trace lookup by ticket

**Impact:** MEDIUM (observability)
**Complexity:** Medium

---

### M3. Magic Numbers Scattered Across Codebase

**Current behavior:** Magic numbers hardcoded in multiple places:
- 20260426: `mt5_connector.py:218` (MAIN orders)
- 12345: `trade_guardian_agent.py:439` (claimed MAIN, but doesn't match above)
- 88880: `scalper_agent.py:402`, `trade_guardian_agent.py:440` (SA)
- 77770: `trade_guardian_agent.py:441` (CHA — but no CHA module exists)
- 99990: `trade_guardian_agent.py:477` (TGA modifications)

**Problem:** TGA expects MAIN's magic to be 12345 but `MT5Connector` actually uses 20260426 — TGA misidentifies MAIN trades as "UNKNOWN". The "CHA" magic (77770) suggests a planned but missing module. There's no single source of truth.

**Enhancement:** Create `core/magic_numbers.py` with a single dict: `{"MAIN": 20260426, "SA": 88880, "TGA_MOD": 99990}`. Import everywhere instead of hardcoding. Audit existing trades to confirm what magic numbers are actually live.

**Impact:** MEDIUM
**Complexity:** Easy

---

### M4. Configuration Split Across Multiple Sources

**Current behavior:**
- `CLAUDE.md` — system identity, philosophy
- `config.json` — runtime parameters (loaded by `maingpt.py`)
- `.env` — MT5 credentials
- CLI args — SA pool, risk, symbols
- Hardcoded constants — magic numbers, ATR periods, session times

**Problem:** Operator changing risk parameters must edit `config.json`. Operator changing magic numbers must edit Python source. Operator changing session times must edit `session_checker.py`. No single config view of "what is this system actually doing right now?"

**Enhancement:** Consolidate into a single `config/` directory with versioned files:
- `config/system.json` — operational params (risk, sessions, symbols, magic numbers)
- `config/strategy.json` — algorithm tuning (ATR periods, swing lookback, confidence thresholds)
- `.env` stays for secrets
- Add `--print-config` CLI flag that dumps the merged effective config

**Impact:** MEDIUM (operability)
**Complexity:** Medium

---

### M5. Synthetic Data Fallback Can Mask Real Failures

**Current behavior:** [`mt5_connector.py:64-67`](apex_ai/market/mt5_connector.py:64) — if `MetaTrader5` package import fails, `dry_run` is silently set to True and synthetic data is returned.

**Problem:** This hides production-critical failures. A deployment where `MetaTrader5` isn't installed will appear to "run" (logging trades, scoring setups) but executes nothing. The operator sees positive activity but no real trades. Same risk if MT5 is installed but broken (e.g., DLL mismatch).

**Enhancement:** Require explicit `--allow-synthetic-fallback` flag to enable this behavior. Without the flag, missing `MetaTrader5` should fail loudly at startup. Add a banner log line whenever synthetic data is being used: `"⚠️ SYNTHETIC DATA MODE — no real trades will execute"`.

**Impact:** MEDIUM (operational safety)
**Complexity:** Easy

---

### M6. No Heartbeat / Liveness Check

**Current behavior:** No mechanism to know if MAIN/SA/TGA is alive and processing.

**Problem:** If the process is hung in a synchronous call (MT5 API blocked), the log just goes silent. There's no external way to verify "is the system healthy right now?" without manually inspecting recent log timestamps.

**Enhancement:** Each bot writes a heartbeat file on every cycle: `logs/heartbeat/main.json` with `{cycle, timestamp, state}`. External monitoring (or a simple `check_health.py` CLI) can verify all three bots have updated heartbeats within their expected interval. Stale heartbeat = problem.

**Impact:** MEDIUM (observability)
**Complexity:** Easy

---

### M7. No Unified Backtest Across Bots

**Current behavior:** Only SA has a backtest harness ([`backtest_scalper.py`](apex_ai/backtest_scalper.py)). MAIN and TGA cannot be backtested.

**Problem:** Cannot validate whether the 5-gate Hedge Fund OS approval system actually improves outcomes vs. simpler alternatives. Cannot measure TGA's contribution to win rate. Tuning parameters becomes pure guesswork.

**Enhancement:** Extract pure decision logic from MAIN and TGA into modules that take pure inputs and return pure outputs. Build a backtest harness that replays historical OHLCV data through the full council voting + simulation + risk pipeline. Compare to baseline (single-agent decisions) on 90+ days of data.

**Impact:** HIGH (long-term strategy validation)
**Complexity:** Hard

---

### M8. No Per-Symbol Performance Attribution

**Current behavior:** Trade journal stores trades but doesn't aggregate by symbol/strategy/regime/session.

**Problem:** Cannot answer simple questions: "What's our win rate on XAUUSD during London Open?" or "Is the RETURN model still profitable in MANIPULATION regimes?" The data is there but querying requires custom SQL.

**Enhancement:** Add a daily report generator (`reports/daily.py`) that produces:
- P&L by symbol, by strategy, by regime, by session
- Win rate trends (rolling 7/30 day)
- Strategy degradation alerts (win rate dropping below threshold)
- Output to Markdown and HTML

**Impact:** MEDIUM (analytics)
**Complexity:** Medium

---

## 🟩 LOW IMPACT (Nice-to-Have)

### L1. Empty argparse in TGA

**Current behavior:** [`trade_guardian_agent.py:627-628`](apex_ai/trade_guardian_agent.py:627) — argparse called but no args defined, result not used.

**Problem:** Dead code. Already covered in TGA audit (L1).

**Enhancement:** Remove or wire up to TGA config.

**Impact:** LOW
**Complexity:** Trivial

---

### L2. Dashboard Updates Mid-Cycle

**Current behavior:** [`maingpt.py:184`](apex_ai/maingpt.py:184) — dashboard updates after each cycle's analysis.

**Problem:** During heavy cycles (multi-symbol analysis), dashboard becomes stale for 30-60s. User watching the live dashboard sees frozen data.

**Enhancement:** Run dashboard refresh on its own thread, decoupled from analysis cycle. Pull state from a shared dict that the analysis cycle updates incrementally.

**Impact:** LOW (UX)
**Complexity:** Medium

---

### L3. Unused "_doc" Keys Pattern in Config

**Current behavior:** [`maingpt.py:106-107`](apex_ai/maingpt.py:106) — strips keys starting with `_doc` from config before passing to engines.

**Problem:** This pattern works as a way to embed documentation in JSON config, but it's a hack. JSON doesn't support comments by design — adding `_doc_xyz` keys is a workaround.

**Enhancement:** Migrate config to YAML or TOML which support real comments. Or use a `_meta` block at the top of each config section instead of inline `_doc_xyz` keys.

**Impact:** LOW (config readability)
**Complexity:** Easy

---

### L4. Python Scripts in Root Without `__main__` Discipline

**Current behavior:** Multiple top-level scripts: `analyze_failure.py`, `apply_sl_tp.py`, `check_history.py`, `check_trade_status.py`, `pnl_statement.py`, `export_history_md.py`, `scratch_check_history.py`, `setup_mt5.py`.

**Problem:** Hard to know which scripts are utilities vs. main entry points vs. one-offs. `scratch_check_history.py` is clearly a temporary file. No README documenting what each does.

**Enhancement:** Move utilities to `apex_ai/tools/` with a `tools/README.md` explaining each. Delete `scratch_check_history.py` if obsolete. Promote `setup_mt5.py` to the root README's installation guide.

**Impact:** LOW (organization)
**Complexity:** Trivial

---

## 🟦 ARCHITECTURAL (Future-Looking)

### A1. Backtest/Live Code Divergence Risk

**Current behavior:** [`backtest_scalper.py`](apex_ai/backtest_scalper.py) reimplements parts of `scalper_agent.py`'s decision logic.

**Problem:** Two code paths can drift. Bug fixes apply to one but not the other. Subtle differences invalidate backtest predictions. Same issue identified in SA audit (A2) — applies system-wide.

**Enhancement:** Extract pure decision logic into shared modules (`*/decision_engine.py`). Live path and backtest path both call the same code. This is the canonical fix for backtest fidelity.

**Impact:** HIGH (correctness across system)
**Complexity:** Hard

---

### A2. No Multi-Process Coordination Layer

**Current behavior:** MAIN, SA, and TGA run as separate processes with no communication.

**Problem:** Three independent processes managing positions on the same account, polling the same MT5 connection, and making decisions in isolation. The TGA-SA isolation conflict (TGA audit C1) is the most visible symptom — but the broader problem is the lack of coordination architecture.

**Enhancement:** Add a lightweight message bus. Options:
- **(a)** Redis pub/sub for events ("SA opened ticket 12345", "TGA modified SL on 12345")
- **(b)** SQLite-based event queue (simpler, no new infra)
- **(c)** Single supervisor process that owns MT5 connection and dispatches to bots via in-process queues
Each bot subscribes to relevant events. SA can know when TGA modifies its trade. MAIN can know when SA opens a position.

**Impact:** HIGH (architectural integrity)
**Complexity:** Hard

---

### A3. No Walk-Forward Optimization Framework

**Current behavior:** Backtests are single-period. Parameters are tuned by intuition and re-tested on the same period.

**Problem:** High overfitting risk. Parameters that work on April 2026 may not work in May. No detection of parameter decay over time.

**Enhancement:** Build walk-forward harness:
- Train period (90 days) → optimize parameters
- Validate period (30 days) → measure out-of-sample performance
- Roll forward → repeat
- Output: parameter stability chart, validation performance trend

Also identified in SA audit (A3). Should be system-level.

**Impact:** HIGH (long-term robustness)
**Complexity:** Hard

---

### A4. Missing CHA Agent — Referenced But Not Implemented

**Current behavior:** [`trade_guardian_agent.py:441`](apex_ai/trade_guardian_agent.py:441) — `elif p.magic == 77770: origin = "CHA"`. But no Crash Hunter Agent module exists in `apex_ai/`. The repository root has `CRASH_HUNTER_AGENT.md` documentation though.

**Problem:** Either CHA was planned but never built, or it exists in a separate repo. Codebase references a system that may not exist. Magic number 77770 reservation is dead code unless CHA is real.

**Enhancement:** Audit CHA status:
- If CHA is built and lives elsewhere — document the cross-repo dependency
- If CHA was abandoned — remove the magic number reservation in TGA
- If CHA is planned — create a stub module so other code can correctly handle its absence

**Impact:** LOW-MEDIUM (clarity)
**Complexity:** Easy

---

## 📊 PRIORITY MATRIX

| Priority | Items | Impact | Effort |
|---|---|---|---|
| **P0 — Do First** | C1, C2, C3, C4, C5 | Critical bugs/safety | 2-3 weeks |
| **P1 — High Value** | H1, H2, H3, H4, H5, H6 | Profitability + correctness | 1-2 weeks |
| **P2 — Quality of Life** | M1-M8 | Robustness + observability | 2-3 weeks |
| **P3 — Future** | A1, A2, A3 | Architectural | 2-3 months |

---

## 💡 SUGGESTED EXECUTION ORDER

**Phase 1 — Foundation (3-4 weeks)**
1. C4: Risk engine respects broker volume_min (1 hour fix, do first)
2. H3: Drop NEUTRAL alignment in HedgeFundOS (1 hour fix)
3. H5: Non-deterministic simulation seed (5 min fix)
4. C5: AdaptiveMemory migration framework
5. C2: State persistence across all bots
6. C3: SA + TGA use MT5Connector for reconnection
7. M3: Centralize magic number registry

**Phase 2 — Coordination & Correctness (2-3 weeks)**
8. C1: Inter-process coordination protocol (design + implement)
9. SA audit C1 (TGA-SA isolation conflict resolution)
10. H1: Unify ATR implementation (Wilder's smoothing)
11. H2: Cache structure analyses across symbols
12. H4: Cap risk multiplier stack
13. H6: Tune cycle intervals

**Phase 3 — Robustness (2-3 weeks)**
14. M1: Error rate circuit breakers (all bots)
15. M2: Unified action log in SQLite
16. M4: Consolidate config sources
17. M5: Explicit synthetic-data flag
18. M6: Heartbeat/liveness checks
19. M8: Per-symbol performance reports

**Phase 4 — Polish (1 week)**
20. L1-L4: Cleanup items

**Phase 5 — Architectural (2-3 months)**
21. A1: Decouple backtest/live code paths
22. A2: Multi-process message bus
23. A3: Walk-forward optimization framework
24. M7: Backtest MAIN + TGA
25. A4: Resolve CHA status

---

## 🎯 EXPECTED COMBINED IMPACT

If P0+P1 implemented:
- **Operational uptime:** 90% → 99% (reconnection, state persistence, error breakers)
- **Trade execution accuracy:** +5-10% (volume_min checks, no silent rejections)
- **Council voting integrity:** Restored (no NEUTRAL inflation of consensus)
- **Risk control:** Tighter (no multiplier stack overrun)
- **Backtest realism:** +20% (proper Wilder's ATR matches charting tools)

If P2 also implemented:
- **Mean Time To Detect issues:** hours → minutes (heartbeats + unified logs)
- **Strategy tuning capability:** Quantified (per-symbol/regime/session reports)

If P3 also implemented:
- **Strategy validation:** Possible for first time (backtest covers MAIN + TGA)
- **Parameter overfitting risk:** Detectable via walk-forward
- **System cohesion:** Bots coordinate explicitly instead of stepping on each other

---

## ⚠️ NOTES

- **C1 (3 separate MT5 connections) is the most architecturally important issue.** Every other system-wide fix gets easier once a coordination layer exists. But it's also the highest-effort change. Consider treating Phase 1 items 1-7 as "good wins while the bigger redesign is planned."
- **The TGA-SA isolation conflict (TGA audit C1) is the most urgent specific issue.** It silently breaks documented contracts. Either TGA opts out of SA management or SA spec gets amended.
- **Some findings span audits.** ATR inconsistency (H1) was first noted in TGA audit (H1) and now extends across the system. Cycle intervals (H6) was noted in SA audit and TGA audit. Backtest divergence (A1) was noted in SA audit (A2). Treat the three audits as a single body of work.
- **Several "Critical" findings are easy fixes** (C4, H3, H5 — total ~2 hours). Do those immediately to lock in quick wins before tackling larger items.
- **Architectural items (A1-A4) are months of work** — don't underestimate. Plan them as quarterly initiatives, not sprints.

---

## 📖 RELATED DOCUMENTS

- [`SCALPER_ENHANCEMENT_RECOMMENDATIONS.md`](SCALPER_ENHANCEMENT_RECOMMENDATIONS.md) — SA-specific findings (24 items)
- [`TGA_ENHANCEMENT_RECOMMENDATIONS.md`](TGA_ENHANCEMENT_RECOMMENDATIONS.md) — TGA-specific findings (21 items)
- [`SCALPER_AGENT_V1_FINAL.md`](SCALPER_AGENT_V1_FINAL.md) — current SA spec
- [`CLAUDE.md`](CLAUDE.md) — system-wide philosophy and architecture

**Total findings across all three audits: 24 (SA) + 21 (TGA) + 23 (this) = 68 items**

---

**End of Recommendations Document**

*Awaiting approval before any code changes.*
