Repo: D:\Hermes Quant\Quant GPT Test Claude - Final   (APEX AI, MT5 demo, not a git repo)

MISSION
Restart the scalper (Pool B) and the Trade Guardian on the DEMO account against
the **corrected** code, confirm the fixes from the 2026-08-24 04:20 UTC repair
are actually live, and continue the behaviour audit from where the last session
stopped. This is still an observation run: do not tune parameters, do not chase
profitability, do not change strategy defaults. Observe, diagnose, document.

This is a CONTINUATION, not a restart of the audit. The findings from the last
session are already written up and fixed — do not re-derive them.

────────────────────────────────────────────────────────────────────────────────
READ FIRST — these hold everything from prior sessions
  - .claude/skills/apex-ops/SKILL.md   invoke this skill; it is the playbook
  - CHANGELOG.md, TOP ENTRY            "2026-08-24 04:20 UTC — Forced closes
                                       were fire-and-forget". Everything you
                                       are verifying is described there.
  - CLAUDE.md §13                      the load-bearing invariants
  - CLAUDE.md §3.2                     the new --pool-mode flag and the
                                       single-instance lock
  - docs/RESEARCH_NOTES.md             §7 rejected concepts, §10 lot floor,
                                       §11 EMA band

────────────────────────────────────────────────────────────────────────────────
WHAT CHANGED SINCE YOUR LAST RUN  (all in CHANGELOG top entry)

Ten defects you surfaced or that fell out of them have been repaired. None of
them touched a trigger, gate, target, session window, or risk percentage.

  1. `_close_at_market` now returns a verified bool — retcode checked AND
     `positions_get` re-read to confirm the position is gone. A failed close
     leaves the ticket in `_open_trades` and is retried next cycle.
  2. Nothing is booked for an unconfirmed close. `_on_trade_closed` is no
     longer reachable from a rejected order.
  3. New `_settled_pnl` polls for the DEAL_ENTRY_OUT leg before reading P&L
     back, and returns None rather than a plausible-looking zero.
     `_get_closed_pnl` now returns Optional[float].
  4. `mt5.Close(ticket)` in scalper/daily_reset.py is gone. The reset takes the
     agent's own closer, and only broker-confirmed tickets are forgotten.
  5. `_perform_daily_reset` closes and BOOKS through `_on_trade_closed` before
     the day's ledger is snapshotted.
  6. News fetch has a 15-minute retry backoff and warns when the calendar on
     disk is over 24h old.
  7. Both agents force stdout to UTF-8 — no more cp1252 tracebacks — and both
     now stamp logs in **UTC** (format ends `...Z`).
  8. New `--pool-mode {RESUME,FRESH}`. Both modes warn loudly when the live
     pool differs from `--pool`.
  9. Both agents attach to a running MT5 session before trying a credentialed
     login. A stale password can no longer sign the terminal out.
 10. The scalper holds a single-instance lock on 127.0.0.1:55556.

New tests: `apex_ai/tests/test_close_paths.py` (14). Suite is now **113**.

────────────────────────────────────────────────────────────────────────────────
ENVIRONMENT — the two traps that have already cost two sessions

  Use system CPython 3.14 and PASS `-E`:
      py -3.14 -E ...
      (full path: C:\Users\<user>\AppData\Local\Python\pythoncore-3.14-64\python.exe)

  - Bare `python` on PATH resolves to the Hermes agent venv: dotenv yes,
    pandas and MetaTrader5 no.
  - Some harnesses export PYTHONPATH at that venv's CPython 3.11 site-packages;
    a 3.14 run then loads cp311 numpy and dies with
    `No module named 'numpy._core._multiarray_umath'`. `-E` defeats it.

  Both faults look like repo breakage and are not.

  NETWORK: last session the news fetch failed every cycle with
  `WinError 10061` (connection actively refused). The feed is fine — it fetches
  66 events from a normal shell with no proxy configured. Something in the
  environment the agent was LAUNCHED from was pointing at a dead local proxy.
  Before launching, check and report:
      py -3.14 -E -c "import urllib.request; print(urllib.request.getproxies())"
      echo "$HTTP_PROXY / $HTTPS_PROXY / $ALL_PROXY"
  If a proxy is set, unset it for the launched processes or report that you
  could not. Do NOT edit repo code to work around it.

  CREDENTIALS: `apex_ai/.env` is correct as of 2026-08-24. Do not modify it.
  Do not enter, guess, or rotate any MT5 password. If authentication fails,
  stop and report — the attach-first fix means a failure should now leave the
  terminal signed in.

────────────────────────────────────────────────────────────────────────────────
STEP 0 — STOP THE OLD PROCESSES (they are running pre-fix code)

At handover the following were live and MUST be stopped before relaunching:
    scalper_agent.py   PID 19944   (--pool 1000 --risk 0.03 --symbols XAUUSD)
    trade_guardian_agent.py PID 24892 (holds 127.0.0.1:55555)

Stop only these two. Do NOT blanket-kill python.exe — the Hermes agent's own
interpreters are in that process list and killing them ends your session.

    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
      Where-Object { $_.CommandLine -match 'scalper_agent|trade_guardian' } |
      Select-Object ProcessId, CommandLine

Then stop those PIDs specifically. Confirm 55555 and 55556 are both free before
continuing:

    Get-NetTCPConnection -LocalPort 55555,55556 -ErrorAction SilentlyContinue

Record open positions and account state BEFORE the stop, so the restart can be
reconciled against it:

    cd apex_ai && py -3.14 -E check_trade_status.py
    cd apex_ai && py -3.14 -E get_agent_pnl.py

────────────────────────────────────────────────────────────────────────────────
STEP 1 — PRE-FLIGHT GATE (must be clean, or stop and report)

    cd apex_ai
    py -3.14 -E -c "import numpy, pandas, MetaTrader5, dotenv; print('env ok')"
    py -3.14 -E -m unittest discover -s tests
    py -3.14 -E -m compileall -q .

Expect **113 tests, OK** and silent compileall. Anything less is a real
regression — stop and report it, do not "fix" it by changing a test.

────────────────────────────────────────────────────────────────────────────────
STEP 2 — LAUNCH

Guardian first, in its own terminal:

    cd apex_ai
    py -3.14 -E trade_guardian_agent.py

Then the scalper, in a separate terminal. **Use this exact command:**

    cd apex_ai
    py -3.14 -E scalper_agent.py --pool 1000 --risk 0.03 --symbols XAUUSD --interval 30 --loss-limit 100.0 --pool-mode FRESH

`--pool-mode FRESH` is deliberate and is the single most important change to
your launch line. Without it the pool restores to ~$423.77 and sizes at $12.71
per trade instead of $30 — 42% of the intended risk unit. Per CLAUDE.md §13.9
that does not produce a smaller version of the same run; it takes far more
LOT_FLOOR rejections and therefore measures a different, degraded sample.
FRESH also matches what `backtest_scalper.py` does (`balance = sa_pool`, no
restore), so the live run and the simulator finally share a sizing basis.

Do NOT add `--allow-whole-day`. Kill-zone gating must stay on.
Do NOT change --risk, --triggers, --sessions, --ema-band, or any cooldown flag.

────────────────────────────────────────────────────────────────────────────────
STEP 3 — VERIFY THE FIXES ARE ACTUALLY LIVE

Confirm each of these from the logs and from process state. Report any that do
not appear — a missing one means the process picked up stale bytecode or an old
working copy.

  [ ] Log lines end their timestamp with `Z` and the clock matches UTC, not
      local. Cross-check: `date -u` vs the newest line in
      logs/scalper_agent.log.
  [ ] `logs/live_audit_tga_stderr.log` (or wherever you redirect TGA stderr)
      contains NO `UnicodeEncodeError`. The shield-emoji banner should now
      render.
  [ ] The scalper logs `SA POOL FRESH: holding the requested $1000.00 ...` with
      `Risk/trade $30.00`. If you instead see `SA POOL OVERRIDE`, the
      `--pool-mode FRESH` flag did not reach the process.
  [ ] Startup line reports the attach path: `SA: attached to running MT5
      session | Account=40280210`.
  [ ] Launch a second scalper on purpose, confirm it exits with the
      port-55556 message, and confirm the first one is unaffected. Then leave
      exactly one running.
  [ ] News: either a successful `SA News refreshed: N HIGH-impact events`, or —
      if the proxy problem persists — failures spaced ~15 minutes apart, NOT
      once per cycle. Count them over 30 minutes; more than 3 means the backoff
      is not working.

────────────────────────────────────────────────────────────────────────────────
STEP 4 — OBSERVE

Run through at least one full kill zone with trades, and if the session spans
it, through 23:00 UTC. Session windows (UTC): Tokyo 00:00–02:00, Pre-London
06:30–07:00, London Open 07:00–08:30, London/NY 12:00–13:30, NY Lunch
16:30–17:30.

What matters most this session, in order:

  1. **A real close, end to end.** When a position exits — SL, TP, timeout or
     EOD — reconcile three numbers against each other: the broker deal history
     (`get_agent_pnl.py`), `logs/scalper_log.json`, and the pool line in
     `logs/scalper_agent.log`. They must agree including commission and swap.
     This is the path that was fabricating entries; it now needs to be shown
     correct, not just shown not-crashing.
  2. **A forced close.** If a 6h timeout or the 23:00 EOD sweep fires, capture
     the full sequence. Confirm the broker comment says `SA_TIMEOUT` or
     `SA_EOD_CLOSE` and that the two are distinguishable in deal history.
  3. **A failed close, if one occurs.** The position must stay in the agent's
     tracking and be retried. Nothing may be booked for it. This is most likely
     around a market-closed boundary.
  4. **Why it did not trade.** Use the rejection-order table in the apex-ops
     skill. The first matching log fragment is the answer — record it per
     scan rather than guessing.

Leave a note in the run log every time a gate blocks, with the fragment name.

────────────────────────────────────────────────────────────────────────────────
KNOWN-OPEN, NOT FIXED — do not report these as new findings

  - Stale news calendar WARNS but does not block. Making it block would be a
    new gate in the live decision path and would have to be mirrored into
    backtest_scalper.py (invariant #2). That is a measurable strategy change,
    not a bug fix, and is out of scope for an observation run.
  - `mt5.Close` fallback in scalper/daily_reset.py is now correct but is never
     exercised while the agent injects its own closer.
  - The `WinError 10061` news failure is environmental, not repo code.
  - `logs/sa_daily_journal.json` holds duplicate same-date rows from May
    (2026-05-07, 05-10, 05-13). Consistent with two scalper instances having
    run concurrently before the 55556 lock existed. Historical; leave it.

────────────────────────────────────────────────────────────────────────────────
RULES

  - Do not change strategy defaults: no trigger, gate, target, session window,
    risk percentage, cooldown or EMA-band change.
  - Any gate you add to `_scan_symbol` must be mirrored into
    `backtest_scalper.py` in the same change (invariant #2). If you cannot
    mirror it, do not add it.
  - Magic numbers come from `core/constants.py`. Never type a literal.
  - `.env` is never committed and never edited by you.
  - Record every finding with file:line and the evidence that produced it —
    log excerpt, process state, or an API response you actually observed. A
    hypothesis is not a finding.
  - When the window closes: stop both processes cleanly, write the findings
    into TECHNICAL_REFERENCE.md, add a UTC-timestamped CHANGELOG entry, and
    leave the observation logs in place.
