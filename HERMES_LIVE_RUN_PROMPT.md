Repo: D:\Hermes Quant\Quant GPT Test Claude - Final   (APEX AI, MT5 demo, not a git repo)

MISSION
Run the scalper (Pool B) and the Trade Guardian on the DEMO account for a full
observation session, watch what the code actually does, and produce a ranked
list of LOGICAL ERRORS and MISSING PIECES. This is a behaviour audit of the
running system — not a strategy hunt and not a performance study. Do not try to
make it profitable in this session. Do not tune parameters. Observe, diagnose,
document, and only then propose fixes.

READ FIRST — these hold everything from prior sessions, do not re-derive them
  - .claude/skills/apex-ops/SKILL.md   invoke this skill; it is the playbook
  - CLAUDE.md section 13               the load-bearing invariants
  - CHANGELOG.md                       top two entries (2026-08-23 21:40 and
                                       2026-08-22 22:23) — parity work, lot floor
  - docs/RESEARCH_NOTES.md             §7 rejected concepts, §10 lot floor,
                                       §11 EMA band
  - TECHNICAL_REFERENCE.md             original audit F-01..F-12, most now fixed

ENVIRONMENT — read this carefully, the last attempt died here
  Use the system CPython 3.14. Invoke it as `py -3.14`, or by full path:
      C:\Users\<user>\AppData\Local\Python\pythoncore-3.14-64\python.exe
  It carries pandas, MetaTrader5, dotenv, numpy, rich, scipy, colorama.
  Verified 2026-08-24: 99 tests OK, compileall clean.

  ALWAYS pass -E. Your own process environment sets PYTHONPATH to the Hermes
  agent venv:
      ...\AppData\Local\hermes\hermes-agent;
      ...\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages
  That injects CPython 3.11 packages ahead of 3.14's, so numpy loads cp311
  binaries and dies with `No module named 'numpy._core._multiarray_umath'`.
  `-E` makes the interpreter ignore PYTHON* environment variables. Reproduced
  and fixed on this machine 2026-08-24:
      PYTHONPATH set, no -E : numpy._core._multiarray_umath ImportError
      PYTHONPATH set, -E    : numpy 2.4.4 / pandas 3.0.2 OK, 99 tests OK,
                              compileall clean, scalper_agent.py --help OK

  THIS IS THE FIX, NOT A WORKAROUND — you are authorised to use it. `-E`
  changes nothing about the repo, the strategy, or the decision path; it only
  stops a foreign interpreter's site-packages from being prepended to sys.path.
  Clearing PYTHONPATH for the child process instead is equally acceptable. Do
  not stop the mission over this. (The "report rather than work around it" rule
  below governs the REPO and the STRATEGY — never the launcher environment.)

  Do NOT use bare `python` off PATH. It resolves to the Hermes agent venv
  (...\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe), which has
  dotenv but NO pandas and NO MetaTrader5. The resulting ModuleNotFoundError
  looks like repo breakage; it is an interpreter mistake.

  `D:\Hermes Quant\GPTMain\.venv\Scripts\python.exe` (3.12.13) also runs this
  repo's suite clean and is a valid fallback. It was absent when the previous
  attempt ran and has since been restored — prefer `py -3.14`, which does not
  depend on a second repository being present.

  Do not install anything, do not create a venv, do not edit requirements.txt.
  The environment is already complete under `py -3.14`.

  MT5: account 40280210 / Exness-MT5Trial2 / DEMO. The connectors ATTACH to an
  already-authorised terminal before attempting a credentialed login, so if the
  terminal is signed in it just works.
  CHECK BEFORE STARTING:
    - MT5 terminal running and signed in
    - AutoTrading is ON (it has been found OFF before — a silent no-trade cause)
    - apex_ai/.env may still hold a stale password. Attach covers it; if the
      attach path fails, fix .env rather than working around it.

PRE-FLIGHT (both must be clean; if not, stop and report — do not start the run)
    cd apex_ai && py -3.14 -E -c "import numpy, pandas, MetaTrader5, dotenv; print('env ok')"
    cd apex_ai && py -3.14 -E -m unittest discover -s tests   # expect 99 tests, OK
    cd apex_ai && py -3.14 -E -m compileall -q .

  All three were run and passed on this machine on 2026-08-24, with PYTHONPATH
  deliberately set to the contaminating Hermes value, to prove -E defeats it.
  If the import line fails you have dropped the -E. If the import line passes
  and the test suite still fails, THAT is a real finding — report it.

THE RUN — three phases, do all three

  Phase 0 — plumbing proof, dry-run, ~20 minutes
    cd apex_ai
    py -3.14 -E scalper_agent.py --pool 1000 --risk 0.03 --symbols XAUUSD --interval 30 --loss-limit 100.0 --allow-whole-day --dry-run

    Purpose only: force the agent out of IDLE so the whole decision chain
    executes and every stage logs. Confirm the pipeline reaches STEP 3 / sizing
    at least once. --allow-whole-day is a plumbing switch and must NOT appear in
    Phase 2. Kill this process once the path is proven.

  Phase 1 — start the Guardian standalone
    cd apex_ai && py -3.14 -E trade_guardian_agent.py

    maingpt.py is NOT part of this session, so nothing will auto-spawn the
    Guardian — start it yourself. It holds a lock on 127.0.0.1:55555; a second
    copy exits quietly. Confirm it is up and reporting before Phase 2.

  Phase 2 — the real observation run, live on demo, default gating
    cd apex_ai
    py -3.14 -E scalper_agent.py --pool 1000 --risk 0.03 --symbols XAUUSD --interval 30 --loss-limit 100.0

    No --allow-whole-day. No --ema-band. No --sessions. No --triggers. Leave
    every default alone — the point is to observe the shipped configuration.
    Run across at least two different session windows (ideally LONDON_OPEN or
    LONDON_NY plus one other) so you see IDLE -> ACTIVE -> COOLDOWN transitions,
    and let it reach the 23:00 UTC EOD close if the clock allows.

    Verify the process set:
      Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Format-List ProcessId, CommandLine
    (Windows execution aliases show each script as two PIDs — that is one
    instance.)

WHAT TO WATCH — the rejection chain, first match is the answer
    cd apex_ai && grep -E "BLOCKED|COOLDOWN|GATE" logs/scalper_agent.log | tail -60

  State=IDLE                     outside every kill-zone window
  COOLDOWN                       loss -> next UTC hour, win -> 5 min
  scan skipped — open position   per-symbol dedup
  STB GATE blocked               range guard / don't-chase / direction
  THIN-LIQ BLOCKED               19-22 or 16-17 UTC need HIGH confidence
  CONSUL BLOCKED / Gate 1        regime not whitelisted for that trigger
  Gate 2                         BOS_RETEST without confirmed displacement
  STEP 3 BLOCKED                 spread cap, spread:SL ratio, SL floor, net-R
  CRG BLOCKED                    daily loss, main DD, position count, news, pause
  LOT SIZE BLOCKED               below broker minimum — count these, see item 6

  Logs: logs/scalper_agent.log · logs/scalper_log.json · logs/sa_daily_journal.json
        logs/tga_log.txt · logs/tga_action_log.json · logs/apex_ai.log

HUNT LIST — the classes of logical error to look for

  1. Crashes, tracebacks, silent excepts, retry storms, MT5 error codes that
     get swallowed instead of surfaced.
  2. State machine: does IDLE/ACTIVE/PAUSED/COOLDOWN actually reach every
     state? Is any state unreachable or sticky? Check the cooldown boundary at
     the top of the UTC hour for off-by-one and naive-vs-aware datetimes.
  3. Session attribution: does the window the agent reports match the wall
     clock, and does the first-match ordering pick the window you expect when
     two windows overlap?
  4. Gates rejecting for the WRONG reason. A rejection is not automatically
     correct. For each distinct rejection, confirm the inputs justify it — a
     gate reading a stale bar, an empty dataframe, or a default-on-error value
     looks identical in the log to a legitimate block.
  5. Forming-bar discipline. Every decision frame must exclude the forming bar
     (copy_rates_from_pos(..., 1, bars)). Verify it in the running process, not
     by reading the constant.
  6. LOT_FLOOR count. Record it explicitly. A LOT_FLOOR rejection costs the
     agent nothing — no position slot, no cooldown — so it re-scans and takes a
     degraded re-entry into the same move. A non-trivial count at 3% risk is
     itself a finding (CLAUDE.md §13.9).
  7. Position lifecycle: SL/TP actually placed on the broker, timeout at
     TIMEOUT_BARS(24) x 15m = 6h honoured, 23:00 UTC EOD close fires, no
     orphans left open, no duplicate entries on the same symbol.
  8. TGA behaviour on Pool B positions: does it trail SA trades, and should it?
     Which magic does it write (99990) and does the SA still recognise its own
     position afterwards? Watch for the two agents fighting over the same SL,
     and for a trail that closes a trade the SA's own timeout was going to
     manage.
  9. Accounting: reconcile the three sources at the end. They have disagreed
     historically — the broker deal history is the only account of record.
         cd apex_ai && py -3.14 -E get_agent_pnl.py
         cd apex_ai && py -3.14 -E check_trade_status.py
     Any drift between scalper_log.json, sa_daily_journal.json and the broker is
     a P1 finding.
 10. Missing pieces — what SHOULD exist and does not: an exit-reason label that
     is not recorded, a gate with no counter, a failure mode with no log line,
     a restart that loses in-memory state, no crash recovery for positions left
     open when the process dies.

RULES FOR THIS SESSION
  - Do not change strategy parameters, targets, triggers or gates to improve
    results. If a fix is needed, write it down; land it only after the
    observation window closes, and only with a test.
  - Nothing gets promoted on the basis of this run. One window is not evidence
    (CLAUDE.md §13.5).
  - Any gate you do add to scalper_agent._scan_symbol must be mirrored into
    backtest_scalper.py in the same change, with a test (invariant #2). Shared
    decision values live in scalper/decision_params.py and are never restated.
  - Magic numbers come from core/constants.py. Never a literal.
  - Report factually. "The agent took no trades" is a result only after you
    have identified which stage rejected and verified the rejection was
    correct. A zero-trade window with an unexplained cause is an open finding,
    not a clean run.
  - Demo account, owner accepts the risk. No loss disclaimers, no asking
    permission to run the tooling.
  - Scope of the stop rule: stop for a broken REPO or a failing test. Do not
    stop for your own launcher environment. Adding -E, clearing PYTHONPATH, or
    choosing between two verified interpreters are yours to decide — make the
    call, note it in the run metadata, and continue.

DELIVERABLES
  1. docs/LIVE_RUN_FINDINGS.md — new file containing:
       - run metadata: UTC start/end, exact command lines, MT5 build, account,
         symbols, terminal AutoTrading state
       - a table of every rejection stage with its count for the window
       - findings ranked P1/P2/P3, each with: what happened, the evidence (log
         excerpt with timestamp), the file:line, why it is wrong, the proposed
         fix
       - a separate CORRECT vs ADD list: what is broken and must be fixed,
         versus what is absent and should be built
       - what you checked and found correct — the negative results matter
  2. A UTC-timestamped entry appended to CHANGELOG.md.
  3. If any finding invalidates something asserted in CLAUDE.md section 13 or
     docs/RESEARCH_NOTES.md, say so explicitly and correct the record there.
