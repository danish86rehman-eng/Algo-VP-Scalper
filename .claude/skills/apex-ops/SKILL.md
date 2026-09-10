---
name: apex-ops
description: Use when working on the APEX AI trading repo — running or restarting the agents, adding or changing a scalper filter/trigger, changing risk or target geometry, investigating why a setup did not fire, reconciling P&L, running the backtester, or auditing before a live run. Covers the repeated operational and development tasks in this repository and the invariants that must not be broken.
---

# APEX AI — operations and change playbook

Two capital pools plus a position manager, three processes, one MT5 account.
`apex_ai/maingpt.py` (Pool A, magic 20260426), `apex_ai/scalper_agent.py`
(Pool B, magic 88880), `apex_ai/trade_guardian_agent.py` (magic 99990).

## Invariants — never break these

1. **Magic numbers come from `core/constants.py`.** Never type a literal.
   Writer and reader drifting apart is how Pool A positions became "UNKNOWN".
2. **A backtest must run the live decision path.** If you add a gate to
   `_scan_symbol`, add it to `backtest_scalper.py` in the same change, or the
   backtest measures a different strategy. This has already invalidated one
   full research campaign.
3. **Targets must clear costs.** `RRR > (1 - win_rate) / win_rate` is the
   survival condition. TP1 is 2R for a reason — see `docs/RESEARCH_NOTES.md`.
   Never reduce it without new walk-forward evidence.
4. **No promotion from a single window.** Chronological 60/20/20, same sign in
   all three folds, or it is not evidence.
5. **`.env` is never committed.** It is in `.gitignore`; keep it there.

## Verify before any live run

Name the interpreter explicitly **and pass `-E`**. Two distinct environment
traps produce `ModuleNotFoundError` that looks like repo breakage and is not:

1. Bare `python` on PATH resolves to the Hermes agent venv — dotenv yes,
   pandas and MetaTrader5 no.
2. Some agent harnesses export `PYTHONPATH` pointing at that venv's
   `Lib\site-packages`. It is CPython 3.11, so a 3.14 run loads cp311 numpy
   binaries and fails with `No module named 'numpy._core._multiarray_umath'`.
   `-E` makes the interpreter ignore `PYTHON*` variables and defeats this.

`py -3.14` is the default; `D:\Hermes Quant\GPTMain\.venv\Scripts\python.exe`
(3.12.13) also runs this suite clean.

```bash
py -3.14 -E -c "import numpy, pandas, MetaTrader5, dotenv; print('env ok')"
```

```bash
py -3.14 -E -m unittest discover -s tests
```

```bash
py -3.14 -E -m compileall -q .
```

113 tests, all clean. Run the import line first — it isolates an environment
fault from a real failure in one step.

Both must be clean. The test suite pins cooldown semantics, target geometry,
and the cost gate — the three things most likely to be broken by a careless
edit.

## Start the stack

```bash
taskkill /F /IM python.exe /T
```

```bash
cd apex_ai && python maingpt.py
```

Pool A spawns the Guardian as a child. Then, in a separate terminal:

```bash
cd apex_ai && python scalper_agent.py --pool 1000 --risk 0.03 --symbols XAUUSD --interval 30 --loss-limit 100.0
```

Confirm three scripts are up (Windows execution aliases show each as two PIDs
— that is one instance):

```bash
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Format-List ProcessId, CommandLine
```

The Guardian holds a lock on TCP 127.0.0.1:55555; a duplicate exits quietly.
Killing `maingpt` does **not** stop the Guardian — stop it explicitly if the
account should be unmanaged.

## Diagnose "why didn't it trade?"

Every filter logs its own name on rejection. Work down the chain in order:

```bash
cd apex_ai && grep -E "BLOCKED|COOLDOWN|GATE" logs/scalper_agent.log | tail -40
```

The chain, in execution order — the first match is the answer:

| Log fragment | Stage |
|---|---|
| `State=IDLE` | outside every kill-zone window |
| `COOLDOWN` | post-trade pause (loss → next UTC hour, win → 5 min) |
| `scan skipped — open position` | per-symbol dedup |
| `STB GATE blocked` | short-term bias: range guard, don't-chase, or direction |
| `THIN-LIQ BLOCKED` | 19–22 or 16–17 UTC needs HIGH confidence |
| `CONSUL BLOCKED` / `Gate 1` | regime not whitelisted for that trigger |
| `Gate 2` | BOS_RETEST without confirmed displacement |
| `STEP 3 BLOCKED` | spread cap, spread:SL ratio, SL floor, or net-R |
| `CRG BLOCKED` | daily loss, main DD, position count, news, loss pause |
| `LOT SIZE BLOCKED` | below broker minimum — the lot floor filter |

## Reconcile P&L

The trade log and the daily journal have disagreed historically. The broker's
deal history is the only account of record:

```bash
cd apex_ai && python get_agent_pnl.py
```

```bash
cd apex_ai && python check_trade_status.py
```

## Change a trigger or filter — checklist

1. Edit `scalper/trigger_engine.py` (detection) or `scalper_agent.py` (gating).
2. Implement **both directions**. A bullish-only detector produced 0 of 287
   live trades and nobody noticed for months.
3. Mirror the change into `backtest_scalper.py`.
4. Add a test in `tests/` covering both directions and the invalidation case.
5. Run the suite. Update `CHANGELOG.md` with a UTC-timestamped entry.

## Change risk or target geometry — checklist

1. `SATriggerEngine.TP1_R` / `TP2_R` for targets; `config.json` `risk.*` and
   `single_trade_dollar_cap_pct` for Pool A sizing.
2. Recompute the survival condition for the observed win rate before changing
   anything downward.
3. `single_trade_dollar_cap_pct` (5.0) is the binding Pool A cap — the 16%
   clarity tier is never reached. Change the cap, not the tier.
4. Run the suite; record the reasoning in `docs/RESEARCH_NOTES.md`.

## Run the backtester

```bash
cd apex_ai && python backtest_scalper.py --from 2026-05-15T00:00:00 --to 2026-08-23T00:00:00 --pool 1000 --risk 0.03 --symbols XAUUSD --loss-limit 100.0 --out logs/backtest_results.json
```

```bash
cd apex_ai && python analyze_walkforward.py logs/backtest_results.json
```

It replays M15 triggers with M5 confirmation, matching the live stack. Results
are diagnostic until they clear the 60/20/20 fold test.

## Session windows (UTC)

Enabled by default: Tokyo 00:00–02:00 and London/NY 12:00–13:30. Defined but
disabled by default: Pre-London 06:30–07:00, London Open 07:00–08:30, and NY
Lunch 16:30–17:30. An explicit `--sessions` whitelist can re-enable a named
window for controlled research. The `Whole_day` catch-all is opt-in only
(`--allow-whole-day`) and should stay off outside plumbing tests.

## Where things live

- Findings and audit trail — `TECHNICAL_REFERENCE.md`
- Evidence and citations — `docs/RESEARCH_NOTES.md`
- Change history — `CHANGELOG.md`
- Donor repo with the walk-forward research engine and typed config —
  `D:\Hermes Quant\GPTMain` (`runtime/`, `configuration.py`, `config.yaml`,
  `docs/SCALPER_RESEARCH_JOURNAL.md`, 100+ tests). It was briefly removed from
  disk on 2026-08-24 and restored; confirm it is present before citing it.
  Another tree on disk: `D:\Hermes Quant\Goldy-2` (unassessed).
