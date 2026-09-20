# APEX AI — `SWEEP_REJECTION` Technical Document

**Generated:** 2026-09-20 UTC
**Scope:** `apex_ai` SA-V2 scalper, live decision path, replay path, location gates, geometry, execution, telemetry, and evidence
**Status:** Runtime contract implemented and verified on 2026-09-20 UTC. The
base detector and strategy parameters remain unchanged; the changes are
activation, causal provenance, final quote/location integrity, telemetry, and
live/replay parity.

## 1. Executive summary

`SWEEP_REJECTION` is a reversal trigger. It attempts to buy after sell-side liquidity is taken and reclaimed, or sell after buy-side liquidity is taken and reclaimed.

The current implementation has four distinct concepts that must not be conflated:

1. **Event detection:** Did price breach a locally detected level and close back across it?
2. **Economic location:** Was that level an independently identifiable liquidity pool or a meaningful structural/auction area?
3. **Reaction confirmation:** Did the M5 market reclaim the area and show displacement or MSS evidence?
4. **Trade outcome:** Did the resulting order produce positive risk-adjusted expectancy after spread, commission, timeout, EOD close, and execution effects?

The base detector answers only the first question. The runtime requires
top-down market-location permission and, whenever `SWEEP_REJECTION` is enabled,
an ACTIVE independent named-liquidity provenance policy loaded with
`--sweep-location-config`. Missing or inactive policy configuration fails
startup; it cannot silently fall back to OFF.

The detector is technically causal and supports both directions, but the repository does not currently have outcome evidence sufficient to claim that ordinary sweep rejection is profitable. The latest sweep-location replay recorded 18,031 raw detections and 6,011 stable candidates, but zero executed sweep candidates. Those counts are population/telemetry results, not performance evidence.

The preferred production launch contract for future DEMO starts is:

```powershell
py -3.14 -E scalper_agent.py --pool 900 --risk 0.03 --symbols XAUUSD `
  --interval 30 --loss-limit 100.0 --pool-mode FRESH `
  --sweep-location-config config.json --market-location-mode ACTIVE
```

It logs `MARKET_LOCATION_MODE=ACTIVE`, `SWEEP_REJECTION`,
`SWEEP_LOCATION_POLICY=ACTIVE`, the resolved config path, and
`ALLOW_UNKNOWN_LOCAL=False ALLOW_CONSUMED=False`. The verified DEMO process
was left running without `--dry-run`; no manual order was submitted.

Runtime proof captured at `2026-09-20 09:42:04 +05:00`
(`2026-09-20 04:42:04Z`):

| Evidence | Verified value |
|---|---|
| Scalper PID | `15104` |
| Process start time | `2026-09-20 09:32:09 +05:00` |
| Exact process command | `"C:\Users\nauman.afzal.ORIENTPET\AppData\Local\Python\pythoncore-3.14-64\python.exe" -E scalper_agent.py --pool 500 --risk 0.02 --symbols XAUUSD --interval 30 --loss-limit 50.0 --pool-mode FRESH --sweep-location-config config.json --market-location-mode ACTIVE` |
| MT5 account | `40280210` |
| MT5 server | `Exness-MT5Trial2` |
| MT5 trade mode | `DEMO` (`trade_mode=0`) |
| Latest XAUUSD tick | `2026-09-18T20:57:59.983Z` |
| Guardian PID | none; no `trade_guardian_agent.py` process was running |

This dated process evidence records the previous `500 / 0.02 / 50.0`
launch. It is retained as historical runtime proof and is not the preferred
profile for future starts.

The process command contains `--sweep-location-config config.json` and
`--market-location-mode ACTIVE` and does not contain `--dry-run`. Its matching
startup log records `MARKET_LOCATION_MODE=ACTIVE`,
`SWEEP_LOCATION_POLICY=ACTIVE`, `MATCH_TOLERANCE_ATR=0.1000`,
`ALLOW_UNKNOWN_LOCAL=False`, and `ALLOW_CONSUMED=False`.

## 2. Scope and non-goals

This document covers:

- M15 trigger-frame liquidity construction;
- M15 sweep/reclaim event detection;
- M5 confirmation and downstream admission;
- top-down market-location permission;
- independent liquidity-pool provenance;
- trigger priority and pre-emption;
- SL/TP, spread, cost, sizing, execution, and exit behavior;
- live/replay parity and research evidence;
- known limitations and the correct measurement plan.

It does not claim that a sweep represents “institutional manipulation” merely because a local high/low was pierced. It does not promote any backtest result, location family, confidence label, or selector change into a validated edge.

## 3. Terminology

| Term | Meaning in this system |
|---|---|
| BSL | Buy-side liquidity; normally resting above highs. |
| SSL | Sell-side liquidity; normally resting below lows. |
| Bullish sweep | SSL is breached and price reclaims it with a bullish close; candidate direction is `BULLISH`. |
| Bearish sweep | BSL is breached and price reclaims it with a bearish close; candidate direction is `BEARISH`. |
| Trigger frame | M15 in the live and replay scalper path. Local variable names `df_m5`/`df_m15_upto` are legacy names. |
| Confirmation frame | M5 in the live and replay scalper path. |
| `R` | Original entry-to-stop distance: `abs(entry - stop_loss)`. |
| Reclaim | A close back through the swept level/zone in the expected direction. |
| MSS | Market-structure shift detected by the existing M5 structure helper. |
| Independent pool | A liquidity pool reconstructed from source bars strictly before the decision event, separately from the local detector. |
| Consumed pool | A previously identified pool that was breached after its confirmation and before the current decision time. |
| Candidate | One stable logical setup identified from symbol, trigger, direction, event timestamp, reference level, and setup metadata. |

## 4. Source-of-truth modules

| Responsibility | Current source |
|---|---|
| Trigger data class and detector | `apex_ai/scalper/trigger_engine.py:54-112`, `:559-615` |
| Local liquidity construction | `apex_ai/scalper/trigger_engine.py:277-295`, `:917-949` |
| Target and cost geometry | `apex_ai/scalper/trigger_engine.py:198-244`, `:603-684` |
| Top-down market location | `apex_ai/scalper/market_location.py` |
| Directional location permission | `apex_ai/scalper/location_permission.py:355-540` |
| M5 reaction confirmation | `apex_ai/scalper/location_permission.py:546-640` |
| Independent named-liquidity provenance | `apex_ai/scalper/sweep_location.py` |
| Live scan pipeline | `apex_ai/scalper_agent.py:757-1680` |
| Replay pipeline | `apex_ai/backtest_scalper.py:815-1860` |
| Candidate funnel telemetry | `apex_ai/scalper/candidate_funnel.py` |
| Research-only outcome evaluator | `apex_ai/scalper/independent_outcomes.py` |
| Runtime parameters | `apex_ai/scalper/decision_params.py`, `apex_ai/config.json` |

## 5. High-level decision flow

```text
Session/state/cooldown/dedup
        |
        v
Closed M15 trigger frame + closed M5 confirmation frame
        |
        v
Step 1: local M15 liquidity
        |
        v
Step 2: fixed-priority trigger selection
        |
        +--> market-location permission for SWEEP_REJECTION
        |
        +--> mandatory ACTIVE independent named-pool permission
        |
        v
Global reclaim/FVG sequence gate
        |
        v
Optional EMA / PDR / VP / leg-confluence gates
        |
        v
Short-term bias + thin-liquidity confidence
        |
        v
Consultant regime whitelist
        |
        v
Spread, SL floor, net-R, news, CRG, exposure, lot floor
        |
        v
Fresh MT5 quote -> frozen location/pool revalidation
        |
        v
ASK/BID spread, spread/stop, minimum-SL and net-R revalidation
        |
        v
Market order with SL + TP1
        |
        v
Guardian / timeout / EOD / close journal
```

A failure of sweep-location permission does not automatically terminate the complete trigger scan. The `consider()` function records the rejected candidate and continues evaluating lower-priority detectors.

## 5.1 Runtime integrity contract

The independent pool is built from source bars strictly before the recorded
`sweep_time`. A candidate pool must already exist and be confirmed before that
event; a pool formed by the sweep itself or by future bars cannot match. Pool
matching uses the configurable `sweep_location.match_tolerance_atr` value
(currently `0.10`) and records the detector level, matched level, distance in
price/points/ATR, threshold, pool ID/type, age, freshness, and consumption
state. `UNKNOWN_LOCAL` and consumed pools are blocked by the production policy.

Immediately before `order_send`, ordinary sweep candidates are rechecked
against the same frozen location and pool identity. The check rejects expiry,
acceptance, profile/location migration, pool consumption, M5 reaction mismatch,
and any attempt to discover a replacement location. The executable price is
the live ASK for BUY and BID for SELL. The shared Step-3 geometry returns the
stable blockers `FINAL_SPREAD_FAIL`, `FINAL_SPREAD_TO_STOP_FAIL`,
`FINAL_MIN_SL_FAIL`, and `FINAL_NET_R_FAIL`.

Signal and fill values are stored separately: `signal_entry`, signal stop/TP1
and `signal_R` are never overwritten by the executable quote. Final telemetry
also records executable bid/ask, selected executable price, final stop
distance/spread/net-R, arrival latency, price drift in points/R, permission,
first blocker, ticket, named-pool context, W1/H4 profile IDs, and M5
reaction/MSS/displacement evidence. Candidate IDs include symbol, direction,
event, swept level, frozen location ID, and named pool ID, so repeated scans
deduplicate deterministically.

## 6. Step 0 — operational eligibility

Before the sweep detector is reached, the agent can be blocked by:

- inactive SA behavior state or outside the enabled session window;
- post-trade cooldown;
- an existing open position for the same symbol;
- missing M15 or M5 data;
- a news blackout or risk-state restriction later in the pipeline.

The normal scalper uses completed bars. The live connector fetches from MT5 with shift 1, and the replay excludes the forming decision bars. This is the required anti-repaint contract.

The current default trigger whitelist includes `SWEEP_REJECTION` (`scalper_agent.py:3175-3179`; replay `backtest_scalper.py:2126-2128`). A research run can isolate it with `--triggers SWEEP_REJECTION`.

## 7. Step 1 — local M15 liquidity

`SATriggerEngine.step1_liquidity()` constructs a `MicroLiquidity` object from the trigger frame:

1. Read the latest closed M15 close as `current_price`.
2. Inspect the last 50 trigger-frame highs and lows.
3. Cluster near-equal highs and lows using the engine tolerance.
4. Retain only highs above current price and lows below current price.
5. Add the current calendar-day high and low as session extremes.
6. Select the nearest usable BSL above price and SSL below price.

The engine constructor default is `equal_hl_tolerance_pct=0.0003`; the scalper does not pass the similarly named `config.json` value into this constructor. `_find_equals()` requires at least two values in a cluster, deduplicates the cluster, and filters it by side of price.

The local output is a numeric level. It does not preserve whether the selected level came from an equal high/low, a session extreme, a previous-day level, a weekly level, or a confirmed structural swing.

## 8. Step 2 — exact sweep detector

The implementation is `SATriggerEngine._check_sweep_rejection()` (`apex_ai/scalper/trigger_engine.py:686-746`). It requires at least four trigger-frame bars and examines `df.iloc[-4:]`.

### 8.1 Bullish rule

Let `SSL = liq.nearest_ssl` and let `recent` be the last four closed M15 bars.

```text
if min(recent.low) < SSL
and last.close > SSL
and last.close > last.open:
    direction = BULLISH
    entry = last.close
    stop  = min(recent.low) - 0.2 * ATR10
```

The detector records the bar containing the lowest low as `sweep_time`, and records `SSL` as `swept_level`.

### 8.2 Bearish rule

Let `BSL = liq.nearest_bsl`.

```text
if max(recent.high) > BSL
and last.close < BSL
and last.close < last.open:
    direction = BEARISH
    entry = last.close
    stop  = max(recent.high) + 0.2 * ATR10
```

The detector records the bar containing the highest high as `sweep_time`, and records `BSL` as `swept_level`.

### 8.3 Important interpretation

The detector does not require:

- a minimum sweep depth in ATR, points, or spread units;
- an independently named liquidity pool;
- a fresh or untouched pool;
- M5 displacement;
- M5 MSS/CHoCH;
- FVG or order-block proximity;
- a higher-timeframe POI;
- a session-specific liquidity identity.

The reclaim is not necessarily on the same bar as the extreme. Any one of the last four M15 bars can contain the extreme, while the final M15 bar supplies the reclaim/body confirmation. Therefore “immediate wick rejection” is the intended model description, but the exact implementation allows up to three bars between the extreme and the final reclaim bar.

## 9. Wick qualification

The detector always computes `wick_ratio` for attribution, but the filter is disabled by default:

- `SWEEP_WICK_RATIO_MIN = 0.45`;
- `SWEEP_WICK_FILTER_ENABLED = False`;
- CLI switch: `--sweep-wick-filter` / `--no-sweep-wick-filter`.

For a bullish trade, the lower rejecting wick ratio is:

```text
(min(open, close) - low) / (high - low)
```

For a bearish trade, the upper rejecting wick ratio is:

```text
(high - max(open, close)) / (high - low)
```

If enabled, a candidate is rejected when its ratio is below the configured threshold. The implementation and both-direction behavior are covered by `tests/test_sweep_wick_filter.py`.

The 0.45 threshold is a design starting point, not a measured probability. It must not be described as validated performance evidence.

## 10. Sweep trigger output

On success the detector creates `SATrigger` with:

| Field | Current value |
|---|---|
| `detected` | `True` |
| `trigger_type` | `SWEEP_REJECTION` |
| `direction` | `BULLISH` or `BEARISH` |
| `entry_price` | Last closed M15 close at detection |
| `stop_loss` | Four-bar extreme plus/minus `0.2 * ATR10` |
| `tp1` | `2.0R` by default |
| `tp2` | `3.0R` by default |
| `confidence` | Always `HIGH` after local conditions pass |
| `swept_level` | Selected local SSL or BSL numeric level |
| `sweep_time` | Timestamp of the extreme bar |
| `requires_displacement` | `False` in the base detector |
| `wick_ratio` | Computed even when the filter is off |

The unconditional `HIGH` label is a classification label, not a calibrated win probability. It currently says “the local detector fired,” not “this context has historically produced a high probability of success.”

## 11. Trigger priority and overlap

`step2_trigger()` evaluates every enabled detector for overlap telemetry, but the first qualifying candidate becomes the winner. Current effective order is:

1. `S01_REFERENCE_CANDLE_RAID_VP` when explicitly enabled and mature;
2. `SESSION_SWEEP` when enabled and valid;
3. `SWEEP_REJECTION`;
4. `HTF_CRT_SWEEP`;
5. `VP_LIQUIDITY_REACTION`;
6. `FVG_FILL`;
7. `BOS_RETEST`;
8. `JUDAS`;
9. `VALUE_AREA_FADE`.

The code stores every detector that fired in `matched_triggers`. The first entry is the winner; later entries are candidates that were detected but pre-empted. `SWEEP_REJECTION` can therefore own a bar before a later trigger with stronger explicit structural or auction evidence.

This is a selector property, not proof that sweep is worse or that a lower-priority trigger would have produced a better trade. A valid selector comparison requires independent per-candidate outcomes and must not reuse portfolio state in a way that changes the control book.

## 12. Top-down market-location permission

The current default is `MARKET_LOCATION_MODE = "ACTIVE"`, and `SWEEP_REJECTION` is the binding trigger family (`decision_params.py:599-603`). The live scan builds one causal `MarketLocationSnapshot` before trigger selection from closed W1/D1/H4/M15/H1/M5 data (`scalper_agent.py:818-858`).

### 12.1 Directional location rules

`build_location_permission()` permits a bullish sweep only when the current price is near a directional support or a causal VAL rejection. It permits a bearish sweep only near directional resistance or a causal VAH rejection.

The principal thresholds are:

| Parameter | Default |
|---|---:|
| Location proximity | `0.35 ATR` |
| Acceptance buffer | `0.10 ATR` |
| M5 displacement body | `0.80 ATR` |
| Acceptance bars | `2` |
| Sweep setup expiry | `120 minutes` |
| M5 MSS lookback | `120` bars |
| M5 MSS swing lookback | `2` |

The permission layer blocks:

- missing location data (`SWEEP_REJECTION_BLOCKED_NO_LOCATION`);
- mid-range or no-important-location sweeps;
- wrong-direction support/resistance;
- a swept level more than `0.35 ATR` from the permitted location;
- a naked POC touch (`POC_ONLY_NO_STRUCTURE`);
- a consumed or invalidated frozen location;
- value-area acceptance against the proposed fade.

The location identity is frozen for the candidate. A later profile replacement cannot silently migrate an already forming setup to a different area.

### 12.2 M5 reaction contract

After location permission is initially granted, `evaluate_sweep_reaction()` examines closed M5 bars from the sweep event onward:

1. The sweep must be visible on the confirmation frame.
2. The latest M5 bar must reclaim the location in the trade direction and have a directional body.
3. The latest bar must not show opposite displacement through the location.
4. At least one of the following must exist:
   - M5 MSS; or
   - directional body displacement of at least `0.80 ATR`.
5. The setup expires after 120 minutes.

The resulting confirmation can be `DISPLACEMENT_CONFIRMED`, `MSS_CONFIRMED`, or `MSS_AND_DISPLACEMENT`.

## 13. Independent named-liquidity provenance gate

`apex_ai/scalper/sweep_location.py` deliberately does not consult the detector’s `MicroLiquidity` object when establishing pool identity. This prevents the detector from authorizing itself.

### 13.1 Pool families

The independent map can contain:

- previous-week high/low: `PWH`, `PWL`;
- previous-day high/low: `PDH`, `PDL`;
- completed session highs/lows: Asia, London, New York;
- confirmed H4 swing highs/lows;
- confirmed H1 swing highs/lows;
- M15 equal highs/lows.

All source bars and confirmations must be strictly before the decision time. A current sweep bar cannot create its own qualifying pool.

### 13.2 M15 equal-liquidity rule

M15 equal liquidity requires at least two distinct confirmed swing events clustered within `0.10 ATR`. Plateau labels inside two swing-lookback widths are treated as one event. Each pool carries a stable ID, source IDs, creation time, confirmation time, touch count, freshness, and consumed state.

### 13.3 Identity classification

The classifier chooses the side based on direction:

- bullish candidate -> low-side pool;
- bearish candidate -> high-side pool.

The swept level is matched to an independent pool with the configured,
volatility-normalized threshold:

```text
distance_ATR = abs(swept_level - pool_level) / M15_ATR
match when distance_ATR <= sweep_location.match_tolerance_atr
```

The production value is `0.10 ATR`. If the causal M15 ATR is unavailable or
non-positive, matching fails closed to `UNKNOWN_LOCAL`; it does not fall back
to exact-number or instrument-dollar equality. A pool is eligible only when
its creation and confirmation timestamps are strictly earlier than the sweep
event, so the current sweep and future bars cannot explain the candidate.

The priority order is:

```text
PWH/PWL -> PDH/PDL -> H4 -> completed session -> H1 -> M15 equal -> UNKNOWN_LOCAL
```

When no causal pool is within tolerance, the identity is `UNKNOWN_LOCAL`.
Telemetry records the detector level, matched pool level, distance in price,
points and ATR, configured threshold, pool ID/type, source IDs, creation and
confirmation times, age, touch count, freshness, and consumption state.

### 13.4 Runtime policy

`config.json` currently contains:

```json
"sweep_location": {
  "enabled": true,
  "mode": "active",
  "allow": {
    "previous_week": true,
    "previous_day": true,
    "session_liquidity": true,
    "h4_swing": true,
    "h1_swing": true,
    "m15_equal_liquidity": true
  },
  "match_tolerance_atr": 0.10,
  "allow_unknown_local": false,
  "allow_consumed": false
}
```

Whenever `SWEEP_REJECTION` is in the requested trigger whitelist, both live
and replay startup require `--market-location-mode ACTIVE` and an explicitly
loaded ACTIVE policy through `--sweep-location-config`. A missing path raises
`SWEEP_REJECTION requires --sweep-location-config <path>`; a loaded policy with
`enabled=false` or `mode=off` raises `SWEEP_REJECTION requires an ACTIVE
named-liquidity policy`. These checks run before the live entry point connects
to MT5. OFF/omitted policy remains legal only when `SWEEP_REJECTION` itself is
not enabled.

With the approved policy, `allow_unknown_local=false` and
`allow_consumed=false`; either condition rejects the sweep before ordinary
downstream admission. Startup telemetry prints policy mode, resolved path,
both allow flags, `MATCH_TOLERANCE_ATR=0.1000`, and the permitted families. A
failed independent pool check does not stop lower-priority trigger evaluation.

## 14. Downstream gates after sweep detection

### 14.1 Global reclaim/FVG sequence

`RECLAIM_FVG_ENABLED` is currently `True`. The live path applies the reclaim sequence to every entry type when the shared `legacy_required()` contract says it is required (`scalper_agent.py:1036-1102`). A local sweep is not a substitute for displacement reclaim and subsequent FVG return at a broken level.

This is separate from the market-location M5 confirmation layer. A candidate can have a valid sweep reaction and still fail the global reclaim contract.

### 14.2 Optional location and directional gates

The scan may additionally apply:

- H1 EMA(18) high/low band, if enabled;
- previous-day-range gate, if enabled;
- H1 regime-direction gate, if enabled;
- H4 value-area/POC gate, if enabled;
- H4 VP leg-confluence gate, if enabled.

These are distinct from the mandatory-to-sweep `MARKET_LOCATION_MODE` contract. Their switches must be identical in live and replay before comparing results.

### 14.3 Short-term bias

`SWEEP_REJECTION` belongs to the fade trigger sets in `decision_params.py:185-195`.

The STB filter:

- blocks bullish fades away from the lower range extreme;
- blocks bearish fades away from the upper range extreme;
- tracks recent session-pool sweeps and blocks chasing a fresh sweep in the same direction;
- explicitly allows a sweep fade against a fresh session sweep with `HIGH` STB confidence;
- uses intraday structure as the primary direction and H1/H4 trend as a confidence tiebreaker;
- requires `HIGH` STB confidence during configured thin-liquidity hours.

`entry_confidence_allowed()` currently returns `True`, so the trigger’s unconditional `HIGH` label is not independently calibrated or used as a hard probability threshold.

### 14.4 Consultant regime gate

The regime whitelist for `SWEEP_REJECTION` is:

```text
MANIPULATION, ROTATION
```

`EXPANSION` is not admitted for this trigger by the current whitelist, and `STRESS` is rejected for all triggers. The regime gate is a setup-family compatibility check; it is not proof that a liquidity level is meaningful.

## 15. Entry geometry and cost protection

### 15.1 Target geometry

Targets are calculated from the signal entry and original stop:

```text
risk = abs(entry - stop)
bullish TP1 = entry + 2.0 * risk
bullish TP2 = entry + 3.0 * risk
bearish TP1 = entry - 2.0 * risk
bearish TP2 = entry - 3.0 * risk
```

The geometry is pinned by `tests/test_trigger_geometry.py`. TP1 must not be reduced below 2R without new walk-forward evidence and a fresh expectancy calculation.

### 15.2 Four Step-3 checks

For a detected sweep, `step3_validate()` applies:

1. **Absolute spread ceiling:** XAUUSD 8 pips, XAGUSD 5, USOIL 6, with symbol defaults in `trigger_engine.py:962-976`.
2. **Spread-to-stop ceiling:** `spread <= 0.25 * sl_pips`.
3. **Minimum stop floor:** XAUUSD 350 internal pips (~$3.50), XAGUSD 120 (~$0.12), USOIL 200 (~$0.20), with the remaining symbol floors in `MIN_SL_PIPS`.
4. **Cost-adjusted net-R:**

```text
reward_pips = gross_target_R * sl_pips
round_turn_cost = 2 * spread_pips
net_R = (reward_pips - round_turn_cost) / (sl_pips + round_turn_cost)
net_R >= 1.5
```

The minimum-SL floor and net-R gate are downstream protections. They do not make the underlying sweep event more meaningful; they reject setups whose geometry is too small or whose transaction cost dominates the risk.

## 16. Position sizing and execution

The risk amount is divided by the signal-price stop distance and broker point/pip value. Setups below the broker’s minimum lot are rejected by the lot-floor filter.

The live execution path then:

1. reads a fresh MT5 tick;
2. chooses ASK for a bullish order and BID for a bearish order;
3. rebuilds the current top-down snapshot and named-pool context while retaining
   the original frozen location and pool IDs;
4. blocks missing frozen state, profile migration, pool migration,
   `UNKNOWN_LOCAL`, pool consumption, expiry, acceptance, invalidation, loss of
   M5 reaction/MSS/displacement confirmation, or disappearance of the exact
   frozen location; a nearby replacement ID cannot rescue the setup;
5. calls `final_quote_check()` with the actual bid/ask and repeats the absolute
   spread, spread-to-stop, minimum-SL, and cost-adjusted net-R checks;
6. caps lot size again against the executable-price stop distance and blocks a
   final lot-floor failure;
7. rounds SL and TP1 to symbol digits and submits the same validated price in
   `order_send`.

The final geometry blockers are `FINAL_SPREAD_FAIL`,
`FINAL_SPREAD_TO_STOP_FAIL`, `FINAL_MIN_SL_FAIL`, and `FINAL_NET_R_FAIL`.
Location blockers include `FINAL_PROFILE_MIGRATION`, `FINAL_POOL_MIGRATION`,
`FINAL_UNKNOWN_LOCAL`, `FINAL_CONSUMED_POOL`, `FINAL_SETUP_EXPIRED`,
`FINAL_ACCEPTANCE`, and `FINAL_LOCATION_INVALIDATED`. The original signal entry
is not overwritten: signal geometry remains available for attribution, while
the final executable price, drift, stop distance, spread and net-R are stored
separately. Replay still needs a calibrated tick/slippage model before its P&L
can be treated as live expectancy.

## 17. Exit behavior

The default trade lifetime is 24 trigger-frame bars:

```text
24 * 15 minutes = 6 hours
```

Positions can also close through:

- TP1;
- initial SL;
- 23:00 UTC EOD flattening;
- Trade Guardian trailing, early-close, and TP-extension logic.

The replay’s static exit convention is stop-first when a bar touches both stop and target, which is the conservative resolution. The research-only independent evaluator has no portfolio, cooldown, risk, or position-state inputs and is not suitable for execution.

## 18. Live/replay parity

The live and replay paths both:

- call the same `SATriggerEngine.step1_liquidity()` and `step2_trigger()`;
- use closed M15/M5 decision frames;
- pass the wick-filter setting and trigger whitelist;
- pass the market-location mode;
- can build the same independent sweep-location context;
- apply the same target and Step-3 geometry;
- require the same ACTIVE startup contract when `SWEEP_REJECTION` is enabled;
- recheck frozen profile/location/pool identity, consumption, expiry,
  acceptance/invalidation and M5 reaction before the execution boundary;
- call the same `final_quote_check()` with BUY=ASK and SELL=BID semantics and
  the same four final geometry blockers;
- use injected historical clocks for STB and CRG.

The replay also supports a sweep research path that evaluates detected sweep candidates independently with next-M5-bar fill, stop-first exits, TP1, timeout, EOD, MFE, MAE, and explicit cost fields (`backtest_scalper.py:1428-1457`; `scalper/independent_outcomes.py`).

Remaining parity limits:

- live execution uses a real tick after a 30-second scan interval; replay uses a bar fill model;
- independent candidate outcomes are not portfolio P&L and do not model slot/cooldown interactions;
- historical news and spread data may be incomplete or substituted in replay.

## 19. Telemetry and auditability

The trigger carries:

- `swept_level`, `sweep_time`, `wick_ratio`;
- `matched_triggers`;
- `location_context`;
- `market_location`;
- `location_permission`;
- reaction and M5 confirmation state.

The candidate funnel creates a stable ID without wall-clock evaluation time and records location, selection, pre-emption, downstream stage, and terminal events. The live recorder writes to `logs/sa_candidate_funnel.jsonl`.

For a valid sweep study, one logical candidate must be deduplicated across repeated 30-second scans. Raw log line counts are observations, not unique candidate counts.

The minimum auditable sweep record is:

```text
candidate_id
symbol / direction / trigger_type
decision_bar_time / sweep_time
swept_level / entry / SL / TP1 / TP2
wick_ratio / ATR / sweep depth
primary and all liquidity identities
pool ID / source IDs / timeframe / touch count
fresh / consumed / pool age
market-location identity and permission
M5 reaction / MSS / displacement
matched triggers / production winner
first downstream blocker or terminal status
spread / quote / lot size
independent outcome: TP, SL, TIMEOUT, EOD, MFE, MAE, realized R
configuration era / code version / data fingerprint
```

## 20. Evidence status

### 20.1 Current verified implementation evidence

The focused verification run on 2026-09-20 passed 90 tests covering:

- both-direction wick arithmetic and filter behavior;
- active and blocked market-location permission;
- M5 reclaim/displacement/MSS outcomes;
- frozen-location behavior and invalidation;
- mandatory ACTIVE named-liquidity startup, ATR-normalized causal pool matching,
  unknown/consumed blocking, final ASK/BID geometry, stable IDs, and
  live/replay final-quote parity;
- trigger selection after a blocked sweep;
- 2R/3R geometry;
- spread, SL-floor, and net-R cost checks;
- replay exit and fill-model fidelity.

Command:

```powershell
py -3.14 -E -m unittest tests.test_location_permission tests.test_sweep_runtime_contract tests.test_candidate_funnel tests.test_live_sim_parity
```

### 20.2 Historical research evidence

| Evidence | Result | Interpretation |
|---|---:|---|
| 2026-09-18 sweep-location replay | 18,031 raw detections; 6,011 stable candidates; 0 executed | No realized-R, MFE, MAE, WR, PF, or fold-stability conclusion is estimable. |
| 2026-09-18 active DEMO gate observation | 105 logged permission failures; 0 visible passes/executions | Confirms conservative unknown-location rejection in that observation window; raw count is not unique candidates. |
| Archived ordinary sweep telemetry | Small historical sample with missing named-location fields | Demonstrates telemetry insufficiency, not random-location profitability or loss. |
| Superseded early SA-V2 backtest | Explicitly invalidated in `AGENTS.md` and `docs/RESEARCH_NOTES.md` | Do not quote as current sweep evidence. |

The correct status is **unproven**, not “profitable” and not “worthless.”

## 21. Known limitations and risks

1. **Expectancy remains unproven.** Runtime integrity prevents known admission
   defects, but it does not establish positive out-of-sample expectancy.
2. **Confidence overstates evidence.** Every local detector pass is labeled
   `HIGH`; that label is not a calibrated probability.
3. **No detector-level depth floor.** A shallow tag can pass the event detector;
   the downstream minimum-SL and cost checks may reject it later.
4. **The four-bar event window is broad.** The extreme may precede the reclaim
   by several M15 bars.
5. **Selector cannibalisation remains measurable risk.** Sweep priority can
   pre-empt other detectors, but its opportunity cost needs paired outcome data.
6. **Location and outcome evidence are not yet joined at scale.** Historical
   replay candidates with zero executions cannot estimate named-pool effect sizes.
7. **Residual replay limits remain.** Bar fills, substituted historical spread
   or news data, and omission of live slot/cooldown portfolio interactions can
   still diverge from broker execution after the shared execution boundary.

## 22. Correct research and promotion protocol

Any future change to sweep rejection should follow this sequence:

1. Freeze the current detector, target geometry, gates, exit model, cost model, and data hash.
2. Run both bullish and bearish cases.
3. Record every candidate, including rejected and pre-empted candidates.
4. Deduplicate repeated scans by stable candidate ID.
5. Attribute the first terminal blocker in the shared live/replay path.
6. Compute independent M5 outcomes without mutating portfolio state.
7. Compare only pre-registered single-variable arms: detector, wick filter, location policy, reaction requirement, or selector.
8. Use chronological 60/20/20 discovery/validation/confirmation folds.
9. Require the same sign across all folds and report sample size, costs, timeout/EOD share, MFE/MAE, and confidence intervals.
10. Promote only after an untouched confirmation period and DEMO observation confirms the runtime contract.

Do not:

- lower TP1 below 2R to increase apparent hit rate;
- call `HIGH` confidence a probability;
- make PDH/PDL, VP, FVG, MSS, BOS, or equal liquidity mandatory without outcome evidence;
- infer performance from population counts;
- compare a gated arm against a historical attribution bucket without replaying the complete gated book;
- use the superseded April/May SA-V2 figures as sweep performance evidence.

## 23. Recommended DEMO launch profile

The preferred non-dry-run DEMO profile for future launches is:

```powershell
cd apex_ai
py -3.14 -E scalper_agent.py `
  --pool 900 `
  --risk 0.03 `
  --symbols XAUUSD `
  --interval 30 `
  --loss-limit 100.0 `
  --pool-mode FRESH `
  --sweep-location-config config.json `
  --market-location-mode ACTIVE
```

Before any DEMO launch or restart, execute:

```powershell
py -3.14 -E -m unittest discover -s tests
py -3.14 -E -m compileall -q .
```

This profile is authorized only for the configured MT5 DEMO account. It is not
authorization for a live-money account, and it does not authorize manual order
submission.

## 24. Final assessment

`SWEEP_REJECTION` is ACTIVE on the verified MT5 DEMO process without
`--dry-run`. It is a closed-bar reversal event detector with symmetric
bullish/bearish implementation, fixed 2R/3R geometry, mandatory ACTIVE
top-down and named-liquidity permission, M5 confirmation, frozen identity
revalidation, and signal-time plus final-quote cost gates.

Its current weakness is not runtime activation or missing final quote/location
protection. The remaining weakness is evidence: event detection, selector
ownership, and realized outcome measurement have historically been separate,
and the latest outcome-bearing evidence is still empty.

Therefore the correct engineering posture is:

```text
Keep the detector measurable.
Keep named-liquidity permission explicit.
Keep live/replay paths identical.
Collect deduplicated outcomes.
Do not claim a sweep edge until executed or independent outcomes are stable across chronological folds.
```

**End of technical document.**
