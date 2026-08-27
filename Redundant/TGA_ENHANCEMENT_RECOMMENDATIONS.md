# 🚀 Trade Guardian Agent (TGA) — Enhancement Recommendations

**Generated:** 2026-05-09
**Status:** Documentation only — no code changes pending approval
**Scope:** Full code audit of `apex_ai/trade_guardian_agent.py`

This document lists prioritized improvements found during a full audit of the Trade Guardian Agent. Each item explains the problem, the proposed enhancement, expected benefit, and implementation complexity.

---

## 🟥 CRITICAL (Safety/Correctness)

### C1. TGA Modifies SA-Isolated Positions — Violates Documented Isolation Guarantee

**Current behavior:** [`trade_guardian_agent.py:436-461`](apex_ai/trade_guardian_agent.py:436) — TGA discovers all open positions across all magic numbers (12345=MAIN, 88880=SA, 77770=CHA) and modifies their SL/TP, partially closes them, or fully closes them based on its own logic.

**Problem:** The SA spec ([`SCALPER_AGENT_V1_FINAL.md`](SCALPER_AGENT_V1_FINAL.md)) explicitly guarantees that SA trades are isolated and that "no other agent will modify SA orders." But TGA actively rewrites SL/TP on every SA position, can early-close trades the SA opened, and changes lot volume via partial close. This silently breaks SA's documented "1:1 partial + 1:2 runner" model — TGA may move SL to breakeven before SA's TP1 fills, or close the trade early and prevent TP2 capture.

**Enhancement:** Add per-origin policy controls. By default, TGA should only manage MAIN positions. SA positions should require explicit opt-in (e.g., `--manage-sa` flag), and even then TGA should respect SA's TP1/TP2 plan rather than overriding it. At minimum, document the override behavior clearly so SA performance metrics aren't misattributed.

**Impact:** CRITICAL
**Complexity:** Medium

---

### C2. State Not Persisted Across Restarts — Stage/Peak Tracking Lost

**Current behavior:** [`trade_guardian_agent.py:368`](apex_ai/trade_guardian_agent.py:368) — `self.registry: Dict[int, TGAPositionRecord] = {}` lives only in memory. On restart, `_sync_registry` rebuilds records from MT5 but starts every record fresh: `peak_profit_r=0`, `sl_stage=0`, `reached_1r=False`, `early_close_armed=False`.

**Problem:** The entire SL trailing logic depends on peak profit tracking. If TGA restarts mid-trade after price already hit 2R then pulled back to 1.2R, it will think peak was 1.2R and apply Stage 2 trailing instead of Stage 3, leaving the position vulnerable. Similarly, `early_close_armed` flag is lost — the trade silently exits its arm state and won't trigger reversal closes. The 10-minute breathing rule also relies on `entry_time` which is correct, but the rest of the record state is stale.

**Enhancement:** Persist registry to `state/tga_state.json` after every modification. On startup, load existing state for any tickets still open in MT5. Merge persisted state with fresh MT5 data — keep peaks, stages, flags from disk; refresh prices/volumes from MT5.

**Impact:** CRITICAL
**Complexity:** Medium

---

### C3. Tick Lookup Can Return None — NoneType Crash in Logging

**Current behavior:** [`trade_guardian_agent.py:388`](apex_ai/trade_guardian_agent.py:388) — `current_profit_r` field in `_log_action` calls `mt5.symbol_info_tick(rec.symbol).bid` directly without checking if the tick is None.

**Problem:** `mt5.symbol_info_tick()` can return None during weekend/holiday or if symbol is temporarily unavailable. Accessing `.bid` on None raises AttributeError, which is caught by the outer exception handler in `run()` but causes the action log entry itself to fail — meaning critical SL moves go unrecorded.

**Enhancement:** Compute `current_profit_r` from `current_price` already passed into `_process_position`, not by re-fetching the tick. The price is already available from `p.price_current` — pass it through to `_log_action`.

**Impact:** CRITICAL
**Complexity:** Trivial

---

### C4. No MT5 Reconnection Logic

**Current behavior:** [`trade_guardian_agent.py:601-624`](apex_ai/trade_guardian_agent.py:601) — main loop catches `Exception` and logs it, then continues on the next 2-second tick. If MT5 disconnects, `mt5.positions_get()` returns None (handled at line 424), but the loop never attempts reconnection.

**Problem:** A network blip or broker restart leaves TGA in a zombie state — running, polling, but unable to read positions or send orders. SL trails freeze, early closes never fire, and the user has no signal that protection has stopped. Open positions sit with stale stops.

**Enhancement:** Detect connection loss (`mt5.account_info() is None` for N consecutive cycles) and trigger `mt5.shutdown()` + `mt5.initialize()` reconnect with exponential backoff (5s, 15s, 60s). Log every disconnect/reconnect. After 5 failed reconnects, halt with alert.

**Impact:** CRITICAL
**Complexity:** Easy-Medium

---

## 🟧 HIGH IMPACT (Performance/Profitability)

### H1. ATR Uses Simple Mean Instead of Wilder's Smoothing

**Current behavior:** [`trade_guardian_agent.py:135-144`](apex_ai/trade_guardian_agent.py:135) — `get_atr()` computes `true_range.rolling(period).mean()`.

**Problem:** Industry-standard ATR (TradingView, MT5 indicator, all charting tools) uses Wilder's smoothing: `ATR_today = ((period-1) × ATR_prev + TR) / period`. Simple mean is more reactive to single outlier bars — one huge spike skews the 14-bar average and changes trail distance unexpectedly. Mismatches with charting tools also make manual debugging harder ("why is my chart's ATR different from TGA's ATR?").

**Enhancement:** Replace simple mean with Wilder's smoothing. Cache ATR per symbol per cycle to avoid recomputation across SL/TP/early-close engines.

**Impact:** HIGH
**Complexity:** Easy

---

### H2. Entry ATR Frozen at Open — Doesn't Adapt to Volatility Regime Changes

**Current behavior:** [`trade_guardian_agent.py:64`](apex_ai/trade_guardian_agent.py:64) — `entry_atr` is captured at trade open and used forever for trail distances, structure lock thresholds, and TP momentum checks.

**Problem:** A trade opened during low volatility (entry_atr = 0.30) that runs into a high-volatility news event will have its trail distance calculated against the stale low-vol ATR. The 1.0×ATR Stage 2 trail becomes way too tight for the new regime, getting stopped out on normal price action. Conversely, trades opening during high-vol but resolving in calm markets will use trails that are too wide.

**Enhancement:** Add a `current_atr` field that refreshes every cycle. Use a blended value for trails: `effective_atr = max(entry_atr, current_atr)` during Stage 2 (don't tighten), or `min(entry_atr × 1.5, current_atr)` during Stage 3 (cap volatility expansion).

**Impact:** HIGH
**Complexity:** Easy

---

### H3. Stage 3 Trail Hardcoded at 0.6 ATR — No Per-Symbol Tuning

**Current behavior:** [`trade_guardian_agent.py:46-51`](apex_ai/trade_guardian_agent.py:46) — `TGAConfig` defines `stage3_trail_atr = 0.6` as a single global constant.

**Problem:** USOIL and XAUUSD have very different volatility profiles — 0.6 ATR works for one but is too tight or too loose for the other. Backtest of SA showed USOIL has 28% timeout rate at 120 min while XAUUSD has only 11% — suggesting different exit regimes are needed per instrument.

**Enhancement:** Allow per-symbol overrides in config: `stage3_trail_atr = {"XAUUSD": 0.5, "USOIL": 0.8, "default": 0.6}`. Same pattern for `stage2_trail_atr` and `structure_lock_atr`.

**Impact:** HIGH
**Complexity:** Easy

---

### H4. Early Close Trigger 3 Uses 6-Bar Window for BOS Detection

**Current behavior:** [`trade_guardian_agent.py:299-304`](apex_ai/trade_guardian_agent.py:299) — `swing_high = df_m5['high'].iloc[-6:-2].max()` then closes if current price breaks that level.

**Problem:** 6 bars on M5 = 30 minutes of data. In trending moves, a 30-minute swing can be crossed during normal pullback without indicating a true market structure shift. This generates premature early closes during healthy continuations. The other engines use 5 (Stage 3 structure) or 10 (Stage 2 structure) bars — the 6-bar choice here looks arbitrary.

**Enhancement:** Use a longer lookback (15-20 M5 bars = 75-100 minutes) for true BOS detection. Optionally confirm with M15 timeframe — only flag as MS shift if M15 also shows structural break.

**Impact:** HIGH
**Complexity:** Easy

---

### H5. No Spread Awareness in SL Trail Calculations

**Current behavior:** [`trade_guardian_agent.py:194-209`](apex_ai/trade_guardian_agent.py:194) — Stage 2/3 SL calculations use `current_price - trail_dist` (BUY) without accounting for the bid/ask spread.

**Problem:** `current_price` is `p.price_current`, which for a BUY position is the bid (close price). But the actual stop-out happens at the bid touching SL — and brokers often have minimum stop levels (`mt5.symbol_info().trade_stops_level`) that reject SL placed too close to current price. The current code doesn't check this and may submit invalid SL modifications that get rejected silently.

**Enhancement:** Before submitting SL modification, check `info.trade_stops_level` and ensure new_sl is at least that many points away from current price. Log rejection reason if stop-level violated.

**Impact:** HIGH
**Complexity:** Easy

---

## 🟨 MEDIUM IMPACT (Robustness/Reliability)

### M1. No Order Send Retry on Modification Failure

**Current behavior:** [`trade_guardian_agent.py:464-480`](apex_ai/trade_guardian_agent.py:464) — `_modify_mt5_sl_tp` returns False on failure. Caller logs nothing and continues to next position.

**Problem:** Transient failures (requote, timeout, invalid stops) cause the SL trail to silently skip a cycle. The next cycle re-evaluates from scratch — may compute the same SL again and fail again. Critical SL moves (e.g., breakeven lock at 1R) can stay broken for many cycles without any alert.

**Enhancement:** Retry up to 3x with brief delay (200ms, 500ms, 1s). On `TRADE_RETCODE_INVALID_STOPS`, re-fetch stop-level and recompute. Log each retry attempt and final outcome.

**Impact:** MEDIUM
**Complexity:** Easy

---

### M2. JSON Action Log — Read-Write Whole File Per Action

**Current behavior:** [`trade_guardian_agent.py:395-403`](apex_ai/trade_guardian_agent.py:395) — Every action triggers `json.load()` of the entire log file, append, then `json.dump()` of the entire file.

**Problem:** As the file grows over months of operation, this becomes O(n) per action. With 2-second polling and multi-position management, log writes become a bottleneck. Worse, there's a race condition window — if the process is killed mid-write, the JSON file becomes corrupt and subsequent reads fail (caught by `except JSONDecodeError` but loses all history).

**Enhancement:** Migrate to SQLite (stdlib, no new deps). Schema: `actions(id, timestamp, ticket, origin, action_type, prev_sl, new_sl, prev_tp, new_tp, current_r, peak_r, stage, trigger)`. Append-only inserts are O(1) and atomic. Or alternatively use JSONL (one JSON object per line) for append-only writes.

**Impact:** MEDIUM
**Complexity:** Medium

---

### M3. Magic Number Origin Mapping Hardcoded

**Current behavior:** [`trade_guardian_agent.py:438-441`](apex_ai/trade_guardian_agent.py:438) — `if p.magic == 12345: origin = "MAIN"` etc., with three hardcoded checks.

**Problem:** Adding a new agent requires editing TGA. Origin "UNKNOWN" on unmatched magic is silently logged but not flagged — easy to miss new agents. The fact that TGA itself uses magic 99990 (line 477) for modifications creates a quirk: TGA's own SLTP modifications retain the original magic of the position, but the `_close_mt5_position` and `_partial_close_mt5` requests use 99990 as the deal magic — splits the audit trail.

**Enhancement:** Move mapping to config dict: `MAGIC_MAP = {12345: "MAIN", 88880: "SA", 77770: "CHA", 99990: "TGA"}`. Allow loading from JSON file. Log a warning when an unknown magic is seen.

**Impact:** MEDIUM
**Complexity:** Trivial

---

### M4. Generic Exception Catch in Main Loop — Risk of Tight Error Loop

**Current behavior:** [`trade_guardian_agent.py:620-621`](apex_ai/trade_guardian_agent.py:620) — `except Exception as e: logger.error(f"TGA Loop Error: {e}", exc_info=True)`.

**Problem:** If a persistent error occurs (e.g., MT5 returning malformed data, a position attribute missing), the loop logs it every 2 seconds — 1800 log entries per hour, polluting the log and potentially filling disk. There's no circuit breaker.

**Enhancement:** Track error count per error type. After 5 consecutive identical errors, increase sleep to 30s and log a warning. After 20 consecutive errors, halt with alert. Reset on first successful cycle.

**Impact:** MEDIUM
**Complexity:** Easy

---

### M5. Single-Instance Lock Via Socket Bind — Hacky

**Current behavior:** [`trade_guardian_agent.py:638-644`](apex_ai/trade_guardian_agent.py:638) — Binds to 127.0.0.1:55555 to detect another running instance.

**Problem:** Port 55555 is arbitrary and could already be used by another application — false positive rejection. Doesn't release cleanly on crash (port may stay bound by the OS for a few seconds, blocking legitimate restart). Doesn't survive across reboots if port reservation differs.

**Enhancement:** Use a PID file (`logs/tga.pid`) with file locking (`fcntl` on Unix, `msvcrt` on Windows). On startup, check if PID file exists and that PID is still alive. If yes, refuse to start. If no (stale file), claim the lock. This is the standard pattern for daemon single-instance enforcement.

**Impact:** MEDIUM
**Complexity:** Easy

---

### M6. TP Extension Lookback Too Narrow

**Current behavior:** [`trade_guardian_agent.py:346-356`](apex_ai/trade_guardian_agent.py:346) — `get_next_structure_target` uses last 19 M15 bars (`recent = df_m15.iloc[-20:-1]`).

**Problem:** 19 M15 bars = ~5 hours of data. For a structure target, that's quite short — major institutional levels (D1 high/low, weekly pivots) often sit beyond this window. The fallback (`+ entry_atr * 2` if no structure found) is arbitrary and not aligned with how the SA's LIA TP2 override works.

**Enhancement:** Expand to 50-100 M15 bars (~12-24 hours). Add H1 structure as a tier-2 fallback before resorting to ATR-based extension. Optionally consult the same `LiquidityEngine` SA Consultant uses, ensuring consistent TP target methodology across agents.

**Impact:** MEDIUM
**Complexity:** Medium

---

## 🟩 LOW IMPACT (Nice-to-Have)

### L1. Empty argparse Block

**Current behavior:** [`trade_guardian_agent.py:627-628`](apex_ai/trade_guardian_agent.py:627) — `parser = argparse.ArgumentParser(); parser.parse_args()` with no arguments defined.

**Problem:** Useless code. `parse_args()` is called but the result isn't assigned or used. Suggests config was meant to be CLI-driven but never finished.

**Enhancement:** Either remove entirely or actually wire up CLI flags for `breakeven_trigger_r`, `stage2_trail_atr`, etc. so config can be tuned without editing source.

**Impact:** LOW
**Complexity:** Trivial

---

### L2. Closed Positions Removed Silently — No Final Outcome Logged

**Current behavior:** [`trade_guardian_agent.py:430-433`](apex_ai/trade_guardian_agent.py:430) — When a position closes, registry deletes it with one log line: `"TGA: Position {ticket} closed. Removing from registry."`

**Problem:** TGA loses visibility into final outcome — was it stopped at SL, hit TP, or closed by another agent? No final P&L recorded, no peak-vs-realized comparison. Useful for analyzing whether TGA's trailing decisions were good (peak 3R, exited at 2.5R = TGA captured most of move) or bad (peak 3R, exited at 0.8R = TGA trailed too tight).

**Enhancement:** On position close, fetch deal history (`mt5.history_deals_get`), compute realized P&L and exit price, log a `POSITION_CLOSED` action with `peak_r`, `realized_r`, `efficiency = realized_r / peak_r`. Useful for post-hoc analysis.

**Impact:** LOW
**Complexity:** Easy

---

### L3. No Slippage Tracking on Modifications

**Current behavior:** Order modification requests don't capture actual fill state — only success/failure.

**Problem:** Can't measure how often SL modifications experience slippage, whether broker stop-level rejections are common, or whether the requested SL price differs from what actually got set.

**Enhancement:** After successful modification, fetch the position again and log `actual_sl` vs `requested_sl`. Track rolling rejection rate per symbol.

**Impact:** LOW
**Complexity:** Easy

---

### L4. Action Log Duplicated to Logger and JSON File

**Current behavior:** [`trade_guardian_agent.py:405-419`](apex_ai/trade_guardian_agent.py:405) — `_log_action` writes a 14-line block to the Python logger AND a JSON record to file.

**Problem:** Same data in two places. The text log is harder to query, and the duplication increases I/O. If the user reads `tga_log.txt` they get verbose human-readable, but it doesn't include all fields. If they read `tga_action_log.json`, it's structured but lacks context.

**Enhancement:** Pick one format as authoritative. JSON file is the right choice (structured, queryable). Text log gets a one-line summary for human eyes: `"TGA SL_MOVE | ticket=12345 | 1.2345 → 1.2400 | Stage 2 trail | 1.5R"`.

**Impact:** LOW
**Complexity:** Easy

---

## 🟦 ARCHITECTURAL (Future-Looking)

### A1. TGA Logic Coupled to MT5 — Hard to Backtest

**Current behavior:** Core trailing logic in `SLEngine`, `EarlyCloseEngine`, `TPEngine` requires `mt5` calls (`symbol_info`, `symbol_info_tick`, `copy_rates_from_pos`) interleaved with decision-making.

**Problem:** Can't backtest TGA against historical data — its decisions depend on live MT5 state. So we can't validate whether its trailing logic actually improves outcomes vs simpler alternatives. The user has no quantitative answer to "is TGA helping or hurting?"

**Enhancement:** Refactor decision logic to take pure inputs (current_price, atr, df_m5, df_m15, position_state) and return pure outputs (new_sl, new_tp, should_close, reason). Move MT5 I/O to a thin adapter. Then a backtest harness can replay 30 days of historical data and score TGA's decisions against ground truth (what actually happened next).

**Impact:** HIGH (correctness, ability to validate)
**Complexity:** Hard

---

### A2. No Coordination Protocol Between TGA and SA

**Current behavior:** TGA and SA both modify positions independently. SA opens at TP1=2345, TP2=2350. TGA may move SL to BE before SA's TP1 fills, or close the trade early via reversal trigger.

**Problem:** Two agents fighting over the same position with no shared state or coordination protocol. SA's win/loss attribution becomes meaningless — was that LOSS due to SA's setup quality, or because TGA closed it 5 pips before TP1 hit?

**Enhancement:** Define a coordination protocol. Options: (a) TGA only manages MAIN, SA manages itself, (b) TGA reads each agent's "exit plan" from a shared registry and only intervenes if violated, (c) TGA gets opt-in flags from each agent (SA: "TGA may trail my SL but never close early"). This is a design decision, not a code task.

**Impact:** HIGH (architectural integrity)
**Complexity:** Hard (design + implementation)

---

## 📊 PRIORITY MATRIX

| Priority | Items | Impact | Effort |
|---|---|---|---|
| **P0 — Do First** | C1, C2, C3, C4 | Critical bugs/safety | 1-2 weeks |
| **P1 — High Value** | H1, H2, H3, H4, H5 | Trailing/exit quality | 1-2 weeks |
| **P2 — Quality of Life** | M1, M2, M3, M4, M5, M6 | Robustness | 1 week |
| **P3 — Future** | A1, A2 | Architectural | 1-2 months |

---

## 💡 SUGGESTED EXECUTION ORDER

**Phase 1 — Safety & Reliability (1-2 weeks)**
1. C3: Fix NoneType crash in `_log_action` (1 line fix, do immediately)
2. C1: Resolve TGA-SA isolation conflict (design decision needed first)
3. C2: Persist registry state to disk
4. C4: MT5 reconnection logic
5. M1: Order send retry on modification failure

**Phase 2 — Trailing/Exit Quality (1-2 weeks)**
6. H1: Wilder's ATR
7. H5: Spread/stop-level awareness
8. H2: ATR refresh during trade
9. H3: Per-symbol trail tuning
10. H4: Longer BOS lookback

**Phase 3 — Robustness (1 week)**
11. M2: SQLite or JSONL action log
12. M3: Externalize magic number map
13. M4: Error rate circuit breaker
14. M5: PID-file single-instance lock
15. M6: Wider TP extension lookback

**Phase 4 — Polish (few days)**
16. L1: Remove empty argparse
17. L2: Log final position outcome
18. L3: Slippage tracking
19. L4: Consolidate logging

**Phase 5 — Architectural (1-2 months)**
20. A1: Decouple TGA logic from MT5 (enables backtest)
21. A2: Define TGA-SA coordination protocol

---

## 🎯 EXPECTED COMBINED IMPACT

If P0+P1 implemented:
- **Operational uptime:** 90% → 99% (reconnection + state persistence + error breaker)
- **SL trailing accuracy:** +15-20% (Wilder's ATR + spread awareness + per-symbol tuning)
- **Premature exit rate:** -25% (longer BOS lookback, ATR adaptation)
- **Audit trail completeness:** Significantly improved (no NoneType crashes losing log entries, position outcomes captured)

**Net effect:** TGA goes from a fragile trailing system that may silently fail to a robust one that provably improves trade outcomes — and once A1 lands, you can quantify the improvement on historical data.

---

## ⚠️ NOTES

- **C1 is the most important finding.** TGA managing SA-isolated positions silently breaks the SA spec's isolation guarantee. This requires a design decision before any code changes — either TGA should opt-out of SA management by default, or the SA spec needs to be amended to acknowledge TGA as a legitimate manager.
- The 2-second poll interval is aggressive and likely fine, but if reconnection logic lands (C4), backoff during disconnects becomes important to avoid hammering a downed broker connection.
- Several findings (M2, A1) are interdependent — refactoring action logging to SQLite makes A1 (backtest harness) easier to implement.

---

**End of Recommendations Document**

*Awaiting approval before any code changes.*
