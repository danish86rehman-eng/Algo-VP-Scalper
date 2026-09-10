# Sweep-first activation — 2026-09-07 09:47 UTC

Operator explicitly approved the sequence after reviewing the study and its
limitations. This is a policy change, not a claim that a profitable live edge
has been established.

## Implemented and verified live

`SWEEP_REJECTION -> HTF_CRT_SWEEP -> FVG_FILL -> BOS_RETEST -> JUDAS`

`VP_LIQUIDITY_REACTION=False`; `VALUE_AREA_FADE=False`.

- Shared selection: `apex_ai/scalper/trigger_engine.py:288`, Sweep check at
  line 316. Both `scalper_agent.py` and `backtest_scalper.py` import this same
  engine. Detector definitions, gates, targets and timeframes unchanged.
- Disabled defaults: `apex_ai/scalper/decision_params.py:309` and `:458`.
  These were already false; regression tests now explicitly pin them in both
  live and simulator signatures. Optional research activation remains possible.
- Era: `apex_ai/scalper/decision_params.py:593`, `2026-09-07-sweep-first`.
- Live startup evidence: `apex_ai/scalper_agent.py:471`; the actual log at
  `2026-09-07 09:47:16,514Z` reports the exact order, both false flags and era.
  See `startup-evidence.log`, which preserves original source-log line numbers.

## Validation

An isolated source snapshot, without credentials or live logs, passed
**492 tests in 9.437 seconds** (`tests.log`). `compileall -q .` completed
successfully and silently. Tests include both directions and fallback through
all five active priorities, whitelist precedence independence, shared-engine
identity, and disabled VP/value-area defaults. Old CRT/FVG/VP precedence tests
were changed only to reflect the explicitly approved new priority contract.
Deployed trigger engine, decision parameters, live agent, backtester and new
test file were hash-compared with the tested snapshot and matched.

## Reload and account safety

- Before reload (09:46:25Z): attached real account 172783529 on
  Exness-MT5Real2, connected, terminal/account Algo permissions true;
  **zero open positions and zero deals today**. Live pool $1,000, daily P&L $0.
- Only verified scalper PID **12340** was stopped with `Stop-Process`;
  this was a targeted process stop, not a claimed graceful telemetry shutdown.
  Existing watchdog PID **11476** reloaded the scalper as PID **13336**.
- Guardian PID **12332** was not stopped or changed. Lock 55555 remained
  owned by it; new scalper owns 55556. Exactly one of each agent was observed.
- New scalper attached to the same account at **09:47:16Z**. News startup
  succeeded with 10 events. Cycles 1 and 2 were healthy and IDLE outside the
  existing sessions, pool $1,000, daily P&L $0, open positions 0.
- Post-reload MT5 API check: same real server/account, connected,
  Algo Trading true, Python trading API enabled, account expert permission
  true, zero positions. Credentials were neither read for login nor edited.

Command preserved by the existing watchdog:

```powershell
py -3.14 -E scalper_agent.py --pool 1000 --risk 0.03 --symbols XAUUSD --interval 30 --loss-limit 100.0 --pool-mode FRESH
```

Tokyo and London/NY remain the only default sessions. No risk, cooldown,
session, stop, target, Guardian-management or credential change was made.
Original research snapshots and results remain untouched.
