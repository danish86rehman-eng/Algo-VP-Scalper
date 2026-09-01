# Anomaly — SA daily P&L drifts from realized total on restart

**Found:** 2026-08-31 19:39 UTC, live monitoring session (no code touched, observe-only).

## What happened

`scalper_agent.py` was restarted at 19:31:28Z (per operator instruction, `--pool-mode FRESH`).
On restart it restored state from `data/sa_pool_state.json`:

```
2026-08-31 19:31:28,548Z [SA.Pool] INFO: SA Capital Pool restored: daily_pnl=$+10.92 | pool=$1000.00 | trades_today=5 | consecutive_losses=0
```

`trades_today=5` is correct — five trades closed earlier that day. But `daily_pnl=$+10.92`
does not match the actual sum of those five trades.

## The five trades (from `apex_ai/logs/scalper_agent.log`, 2026-08-31)

| Ticket | Result | PnL |
|---|---|---|
| 494588161 | WIN_TP1 | +$26.33 |
| 494592170 | WIN_TP1 | +$14.53 |
| 494596660 | LOSS | −$37.31 |
| 494625128 | WIN_TP1 | +$45.87 |
| 494635862 | TIMEOUT | +$3.13 |

Sum = **+$52.55**, which matches the in-memory figure logged immediately before the
restart:

```
2026-08-31 19:31:08,455Z [SA] INFO: SA Cycle #1755 | State=IDLE | Session=IDLE | Pool=$1052.55 | Daily PnL=$+52.55 | Open=0
```

So the persisted `daily_pnl` in `sa_pool_state.json` (+$10.92) is **stale/wrong** even
though the persisted `trades_today` counter (5) is correct — the two fields disagree
about the same set of trades. $52.55 − $10.92 = $41.63 unaccounted for.

## Why it matters

- This is the same class of bug already documented in `CLAUDE.md` §13.10 for
  `logs/scalper_log.json` (not the account of record for P&L) — except this time it's
  the *persisted pool state* used for the daily loss-limit calculation, not just a
  display log.
- Currently benign direction: the tracked daily P&L is *lower* than reality, so the
  $100 daily-loss-limit check is more conservative than it needs to be, not less. No
  overtrading risk today.
- Not benign in general: if `trades_today` can advance correctly while `daily_pnl`
  falls out of sync, the reverse (tracked P&L overstated vs. real) is presumably also
  possible depending on when/how the state file gets written — that direction *would*
  let the agent under-count real losses against the daily cap.
- Broker balance itself is unaffected ($1317.98 at reconnect) — this is a bookkeeping
  drift in the isolated-pool accounting layer (`scalper/capital_pool.py` /
  `data/sa_pool_state.json`), not a broker-side problem.

## Not done (out of scope for this session)

- Did not inspect `scalper/capital_pool.py` save/load logic to find the root cause
  (e.g., save cadence, whether it saves on every close vs. periodically, race with the
  19:31 restart landing between a trade close and its persisted write).
- Did not touch `data/sa_pool_state.json`, `.env`, or any live process.
- No research/promotion implied — this is a data-integrity observation, not a
  strategy or gate finding, and needs no ledger row under §13.5/§13.12 methodology.

## Suggested next step (for tomorrow)

Read `scalper/capital_pool.py`'s save/restore path and check whether `daily_pnl` is
written transactionally with each trade close, or on a separate/delayed cadence from
`trades_today`. Cross-check against `get_agent_pnl.py` (broker deal history) as the
actual account of record, per §13.10.
