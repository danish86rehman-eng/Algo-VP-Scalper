# Trigger research continuation — 2026-09-14

Objective: find a better executable scalper entry using vault evidence and MQL5 designs.
Status: incomplete; no replacement has demonstrated superiority. No live activation.

## Verified current results

No `research_pullback.py` or `backtest_scalper.py` process was present at inspection.
The existing `comparison.json` was stale: it reported 15 tracked-arm trades and only
`BREAK_HISTORY_MISSING`, whereas the current `tracked.json` has an actual setup lifecycle.
Recomputed with the existing summarizer into
`apex_ai/logs/research/tracked-pullback-20260913/comparison-verified-20260914.json`.
The original report is preserved.

Period: 2026-06-01 through 2026-09-12, initial pool $1,000, 3% risk.

| Replay arm | Trades | Net USD | Mean R | Profit factor |
|---|---:|---:|---:|---:|
| Reclaim on | 231 | +967.61 | +0.1013 | 1.2195 |
| Reclaim off | 542 | -641.47 | -0.0362 | 0.8827 |
| Tracked pullback | 21 | -244.96 | -0.4439 | 0.3262 |

Tracked arm contains 15 FVG_FILL trades and six SWEEP_REJECTION trades; aggregate
performance must not be attributed exclusively to the new pullback lifecycle.
Its chronological fold net results are -185.76, -51.28 and -7.92 USD (14/4/3 trades).
These are already-inspected diagnostic partitions, not untouched OOS.

The tracked lifecycle records 89 unique breaks, 78 frozen setups, 35 READY rejection
events, 26 MISSED_ENTRY:READY_WINDOW_ELAPSED transitions and six durable attempts.
These counters describe events and states; they are not additive independent cohorts.
Next audit: establish why ready setups miss the 60-second window using setup chronology
and replay scheduling before changing any deadline or adding another entry model.

## Fidelity limits

The current runner explicitly discloses M5 sampling rather than 30-second live scans,
a current news cache rather than historical news, and assumed 0.35 USD/oz costs.
Consequently these numbers do not establish executable live superiority. Reclaim-off
trades and their outcomes do not prove individual rejected trades were mistakes.
The archived final2 test output says 548 tests with three failures; the changelog's 549
test claim is not independently verified by that artifact. No new suite was run here.

## MQL5 design lead

Read: https://www.mql5.com/en/articles/24184 — *Decoding Market Intent: Reading
Structure, Liquidity, and Price Behavior* (9 September 2026).
The article specifies H4/H1 context, M15 liquidity and M5 confirmation, and separates
mandatory sweep/displacement/structure gates from a weighted score. This is a relevant
design reference, not verified evidence of a better APEX trigger. Its score thresholds
are not calibrated probabilities. Do not copy them into production based on the article.

Vault prior evidence also rejects broad liquidity-dwell variants and requires resolving
earlier VP replay mismatches; rebranding those conditions as a sniper entry adds no evidence.

## Next concrete work

1. Attribute the 26 missed windows to exact completed-bar timestamps, creation time,
   session eligibility, portfolio blocks and replay sampling.
2. Validate replay timing against the unchanged live decision path using selected cases.
3. Only then define the next preregistered comparison with measured execution costs and
   historical news coverage. Keep the incumbent as the baseline and retain both directions.

The goal stays active. Current results oppose activating the tracked-pullback replacement.

## Follow-up — missed-window attribution

`audit_pullback_windows.py` reads the saved replay without connecting to MT5 and records its
source SHA-256. Output: `logs/research/tracked-pullback-20260913/window-audit-20260914.json`.
Of 26 expired READY setups, 16 confirmed outside current enabled sessions and ten inside.
None of the ten overlapped an executed position in this replay. Their individual downstream
blockers remain unrecorded; aggregate STB/consultant counts cannot identify them reliably.
The replay already has an M5 event clock when tracked pullback is enabled
(`backtest_scalper.py:827`). A blanket claim that M15 scheduling caused all expiries is unsupported.
Session classification uses the current checker; historical code hashes are absent.

Separating actual entries by their `pullback` metadata yields six tracked entries, net -$57.17,
sumR -2.5046, versus 15 native entries, net -$187.79, sumR -6.8168. This is descriptive attribution,
not an independently replayed standalone model. It corrects attribution of the whole arm to the
new lifecycle. The next replay must retain candidate ID and first blocker at each READY decision
to resolve the ten in-session expiries before changing the contract.

## Attribution replay launched

Added optional `--audit-ready` to the research runner, using a Counter subclass that records
ordered rejection-counter updates against READY setup IDs and decision times. This does not
alter replay gates or the live agent. A direct diagnostic verified Counter result parity,
ordered STB/detail capture, and exclusion of non-READY states.

Full-period command launched on 2026-09-14:

```powershell
py -3.14 -E -B research_pullback.py --audit-ready --out logs/research/tracked-pullback-20260913/ready-audit-replay-20260914.json
```

At launch verification, Python PID 24404 was alive; tool session handle is 4729. Poll the same
handle and inspect the exact process before deciding whether work is stopped. Output is written
on completion. The runner blocks `order_send` and credential login. The running terminal path
was verified before attachment. Completion and outcome parity remain unverified.

Next: join `ready_rejection_audit` to the ten in-session IDs in `window-audit-20260914.json`,
report the first gate and its detail, and compare executed trades with the prior `tracked.json`.
Whole-worktree whitespace checking found pre-existing trailing whitespace at `get_session_hl.py:275`;
it was not changed as part of this diagnostic work.

Follow-up: the vault's `wiki/concepts/xauusd-execution-costs.md` identifies $0.35/oz as
the measured DEMO mean, with account 40280210 provenance. The research runner uses this
number as a flat cost assumption on real-server prices; it does not load the measured
distribution or establish a LIVE cost model. Thus the constant has historical demo
provenance, but still cannot validate live expectancy.

`summarize_ready_audit.py` is prepared to join the completed replay to the ten unresolved
IDs and compare executed entry/exit/risk/PnL fields against the previous replay. Missing
gate records remain explicitly unresolved. It also hashes all input artifacts.
