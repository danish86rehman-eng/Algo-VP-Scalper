# Sweep rejection: independent liquidity provenance and active DEMO gate

Date: 2026-09-18  
Instrument: XAUUSD  
Account: Exness-MT5Trial2 demo, login 40280210

## Result

The sweep-location gate is implemented and is activated only when the scalper is started with `--sweep-location-config config.json`. The configuration enables named independent liquidity pools and rejects `UNKNOWN_LOCAL` and consumed pools.

The repaired export is [sweep_identity_repaired.csv](../../apex_ai/logs/sweep_identity_repaired.csv). It retains the project’s causal-location and independent-outcome columns for replay review. The historical replay baseline had 18,031 sweep detections, 5,967 unique sweep candidates, and no executed trades; those are rejection-path observations, not performance evidence.

## Root cause and repair

The prior selector treated the M15 equal-liquidity object as both detector and proof. That made identity self-referential: the detector’s local level could authorize itself. The new `scalper/sweep_location.py` builds a separate map from bars strictly before confirmation:

- PDH/PDL and PWH/PWL from completed daily history;
- completed ASIA/LONDON/NY session highs and lows;
- confirmed H1/H4 swing events;
- M15 equal highs/lows only when at least two distinct confirmed swing events exist, clustered within 0.10 ATR;
- `UNKNOWN_LOCAL` when no independent pool matches.

Each pool has a stable ID, source swing IDs, creation/confirmation times, touch count, freshness, and consumed state. Priority is deterministic: previous week, previous day, completed session, H4, H1, M15 equal, then unknown.

The current sweep bar and repeated scans cannot create a qualifying pool. FVG and volume-profile fields remain telemetry only; they cannot rescue an anonymous sweep.

## Permission and trigger behavior

The active permission contract is explicit: permitted named pools pass; unknown and consumed pools fail. A failed sweep does not terminate the trigger scan, so BOS/JUDAS/FVG/CRT/value-area candidates can continue. An admitted sweep retains the existing sweep priority. The production default trigger set remains unchanged with sweep disabled unless explicitly listed; the DEMO restart lists `SWEEP_REJECTION` explicitly.

The independent evaluator in `scalper/independent_outcomes.py` is research-only. It uses the project’s M5 next-bar fill, stop-first, TP-first-after-stop ordering, timeout, spread/commission, MFE, MAE, and realized-R convention, without mutating live risk, cooldown, or portfolio state.

## Verification

`py -3.14 -E -m compileall -q scalper scalper_agent.py backtest_scalper.py` passed.

The focused liquidity/geometry suite passed 22 tests. The full repository suite ran 598 tests; remaining failures are pre-existing unrelated baseline assertions around VPLR/session defaults plus legacy CRT fixture assumptions. No detector, SL, TP, risk, or execution-sizing parameters were changed by this repair.

Before restart, direct MT5 verification confirmed: `initialize=True`, login `40280210`, server `Exness-MT5Trial2`, `trade_mode=0` (demo), balance/equity `$1293.11`, XAUUSD selected, and zero open positions/orders. Only the scalper process was restarted; TGA and the execution telemetry worker were left untouched. No manual orders were submitted.

## Files

- `apex_ai/scalper/sweep_location.py`
- `apex_ai/scalper/independent_outcomes.py`
- `apex_ai/scalper/trigger_engine.py`
- `apex_ai/scalper_agent.py`
- `apex_ai/backtest_scalper.py`
- `apex_ai/config.json` (`sweep_location` section)
- `apex_ai/logs/sweep_identity_repaired.csv`
