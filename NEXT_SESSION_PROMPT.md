Repo: D:\Hermes Quant\Quant GPT Test Claude - Final  (APEX AI, MT5 demo, not a git repo)

CONTEXT — read these first, they hold everything from prior sessions:
  - CHANGELOG.md                  top two entries are 2026-08-22; read both
  - docs/RESEARCH_NOTES.md        §7 = already-rejected concepts, §9 = the run
                                  that just finished. Read §7 BEFORE proposing
                                  any "new" idea.
  - TECHNICAL_REFERENCE.md        original audit (F-01..F-12; most now fixed)
  - .claude/skills/apex-ops       operational playbook — invoke it
  Memory is already built (7 entries incl. "owner owns the risk" — demo
  account, no loss disclaimers, run research tooling without asking).

ENVIRONMENT
  Default shell python has NO pandas. Use:
      "D:\Hermes Quant\GPTMain\.venv\Scripts\python.exe"
  MT5: account 40280210 / Exness-MT5Trial2. The connectors now ATTACH to an
  already-authorised terminal before trying a credentialed login, so if the
  terminal is signed in it just works. NOTE: apex_ai/.env still holds the OLD
  account's password (sha256 prefix 8c680c31a89d) — attach covers reads, but
  fix it before a live run. Terminal AutoTrading was OFF; turn it on to trade.
  Verify: cd apex_ai && python -m unittest discover -s tests   (47 tests)

STATE AS OF 2026-08-22 22:05 UTC
  - Scalper is M15 trigger / M5 confirm. TP1 = 2R, TP2 = 3R. Net-R cost gate.
    Cooldown: loss -> next UTC hour, win -> 5 min. Whole_day window opt-in.
  - Backtester now runs the LIVE decision path: cooldown, per-symbol dedup,
    EOD 23:00 close, STB gate, thin-liq gate, SA-CRG + daily loss limit, news
    30/15, no look-ahead, entry bar included in the exit scan, one
    chronological event loop across symbols, P&L net of spread + commission,
    per-gate rejection counters. 15 new tests pin this.
  - analyze_walkforward.py does 60/20/20 folds, per-exit-type expectancy with
    leave-one-out, and the survival condition against REALISED payoff.
  - CONFIG CHANGED: symbols = ["XAUUSD"] only. Scalper defaults now
    pool $1000, risk 3%, daily loss limit $100.

THE RESULT THAT MOTIVATES THIS SESSION
  Window 2026-05-15 -> 2026-08-21, XAUUSD/XAGUSD/USOIL, $500 @ 2%:
    198 trades, 32.83% WR, net -$76.97, PF 0.94, maxDD 36.36%
    folds: dev -$40.87 | val -$61.42 | test +$25.32  -> SIGN UNSTABLE, rejected
    survival: needs RRR > 2.05, realised 1.99. Short by 0.06.
    costs were NOT the cause: gross -$70.07 vs $7.36 of costs.
    exits: TP 32.3% (+2.00R), SL 66.2% (-1.00R), TIMEOUT 0.5%, EOD 1.0%
      -> the timeout profile (23% of live trades) is now designed out.
         There is no bad exit left to eliminate; the ENTRY signal is the
         binding constraint.
    SWEEP_REJECTION was 196 of 198 trades and netted -$91.63 at PF 0.92.

TASK
1. SWEEP_REJECTION is greedy — stop feeding it. Test BOS_RETEST properly.
   IMPORTANT NUANCE, do not skip: RESEARCH_NOTES §7 records BOS_RETEST as
   already rejected (all variants, PF 0.36-0.80; with M5 confirmation negative
   in dev and test). That rejection is NOT binding here, because it tested a
   detector that only ever implemented the bullish branch (the bearish half was
   added 2026-08-22) on a different timeframe stack. A re-test is legitimate.
   Say so explicitly in the writeup so the record stays honest.
   First thing to check: BOS_RETEST produced ZERO trades in the run above. It
   is whitelisted to the EXPANSION regime only AND requires displacement
   confirmation (CONSUL Gate 2). Find out whether it fires at all before
   drawing any conclusion about its edge — a zero-trade result is not a
   negative result. Instrument the rejection counters.
2. Go find better strategies. Read sources IN FULL and extract real numbers
   (win rate, PF, sample size, date range) — headlines and equity-curve images
   are not evidence. Everything on MQL5 is open source; do not spend time on
   licensing questions. Cover:
     - MQL5 articles and the code base / EA source
     - MQL5 books and blogs
     - donor repositories. Known donor: D:\Hermes Quant\GPTMain (walk-forward
       research engine, typed config, runtime/, 100+ tests,
       docs\SCALPER_RESEARCH_JOURNAL.md). Look for others on disk too.
   RESEARCH_NOTES §7.3 is the standing warning: MQL5 marketplace listings are
   sales pages. Treat articles as DESIGN evidence (rule shapes, parameter
   defaults), never as PERFORMANCE evidence, unless they publish methodology
   and numbers.
3. Anything promising goes through the same bar as everything else:
   chronological 60/20/20, same sign in all three folds, expectancy per exit
   type, costs applied, live gate chain mirrored. No promotion from one window.
4. Report factually. If a result is negative, say so plainly and say what it
   rules out. Do not tune parameters on a window you already looked at.
5. Append a UTC-timestamped entry to CHANGELOG.md and add findings to
   docs/RESEARCH_NOTES.md.

NEW PARAMETERS (already applied to config + CLI defaults)
  pool $1000 | risk 3% per trade | max daily loss $100 | XAUUSD only
  Run:
    cd apex_ai && python backtest_scalper.py --from 2026-05-15T00:00:00 \
      --to 2026-08-23T00:00:00 --pool 1000 --risk 0.03 --symbols XAUUSD \
      --loss-limit 100.0 --out logs/bt_bos.json
    cd apex_ai && python analyze_walkforward.py logs/bt_bos.json
  Note: at $30 risk the lot floor no longer excludes XAGUSD/USOIL, so if a
  strategy wants them back that is now a live option, not a blocked one.

INVARIANTS (do not break)
  - Magic numbers only from core/constants.py, never literals.
  - Any gate added to scalper_agent._scan_symbol must be mirrored into
    backtest_scalper.py in the SAME change. A backtest that does not run the
    live decision path is not evidence — this already voided one full campaign.
  - Never lower TP1 below 2R without recomputing RRR > (1-WR)/WR against the
    current measured win rate, using REALISED payoff not the nominal target.
  - Implement both directions of any detector, always. A bullish-only
    BOS_RETEST produced 0 of 287 live trades and nobody noticed for months.
  - Eight strategy concepts have now been rejected under walk-forward with
    cost + gate fidelity. Check RESEARCH_NOTES §7 and §9 before proposing
    anything as "new".

COST DISCIPLINE
  The last two sessions cost ~$209 and ~$52. Read the docs above instead of
  re-auditing the codebase. Prefer one broad research pass over many small
  probes. Do not re-run the 2026-05-15 -> 08-21 window for SWEEP_REJECTION —
  it is done and recorded.
