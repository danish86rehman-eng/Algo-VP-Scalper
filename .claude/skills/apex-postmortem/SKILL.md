---
name: apex-postmortem
description: Use when diagnosing why the APEX scalper is losing, running the self-improvement loop, investigating a specific failure mode (SIGNAL_FALSE, GAVE_BACK_WINNER, STOP_TOO_TIGHT, timeouts), researching a remedy against MQL5 articles or the web, opening or closing a row in the remedy ledger, or after a live session when new closed trades have accumulated. Also use when asked what the bot is getting wrong, how it improves over time, or why a loss happened.
---

# APEX scalper — the self-improvement loop

The scalper records what it got wrong while it runs. This skill turns that
record into a researched, evidence-graded remedy that has to earn its way into
the live configuration.

**The loop has one rule that overrides convenience: nothing promotes without
folds.** The agent diagnoses itself; it does not retune itself. Every layer
below is observation-only by construction, and the only path from a finding to
a live parameter runs through `docs/REMEDY_LEDGER.md`.

## The pieces

| Piece | What it does |
|---|---|
| `apex_ai/scalper/postmortem.py` | Replays each closed trade's bars, measures MFE/MAE in R, looks 24 M5 bars PAST the exit, assigns one failure mode. Zero decision authority. |
| `apex_ai/scalper/reject_log.py` | Counts every gate veto by stage, per day, per symbol. |
| `apex_ai/analyze_incidents.py` | Aggregates incidents into a ranked diagnosis; `--backfill` reconstructs history from the broker. |
| `docs/REMEDY_KB.md` | Failure mode → candidate remedies, each with a graded source. |
| `docs/REMEDY_LEDGER.md` | Append-only proposals and verdicts. The promotion gate. |

## Run the loop

### 1. Get incidents

Live trades produce them automatically — the agent queues a forensic pass on
every close and runs it two hours later, once the look-ahead bars exist.

The shipping configuration has almost no live history, so **the simulator is
the incident source that matters**:

```bash
cd apex_ai && py -3.14 -E backtest_scalper.py --from 2026-05-15T00:00:00 --to 2026-08-23T00:00:00 --pool 1000 --risk 0.03 --symbols XAUUSD --loss-limit 100.0 --incidents logs/sim_incidents.jsonl --out logs/bt_baseline.json
```

To read the live book instead, reconstruct it from the broker:

```bash
cd apex_ai && py -3.14 -E analyze_incidents.py --backfill
```

`--backfill` sources P&L **and** the win/loss label from MT5 deal history, not
from `scalper_log.json`. That is not optional tidiness: the trade log booked
140 of 282 closes at $0, and reading it at face value produced a confident and
completely false diagnosis. See ledger L-004.

### 2. Diagnose

```bash
cd apex_ai && py -3.14 -E analyze_incidents.py --incidents logs/sim_incidents.jsonl --out logs/sim_diagnosis.md
```

Read the report top-down, and apply three filters before believing anything:

- **Skip `LOSS_ORDINARY`.** It sorts first by dollars every time and it is not
  a defect — a 46% win rate at 2R is supposed to produce losses of that shape.
- **Check `n priced` against `n`.** A mode whose dollar column rests on 5 of 37
  occurrences is telling you about frequency, not cost.
- **Respect the n≥20 floor.** Below it the report says so; do not redesign
  around three bad trades.

### 3. Research the remedy

Look the mode up in `docs/REMEDY_KB.md` first — it is probably already there,
including things already ruled out. **Check the "already ruled out" sections
before proposing anything**; a trend filter on sweeps has been measured and
rejected here with grade-A evidence, and it gets re-proposed constantly because
it sounds obviously right.

If the KB has no candidate, research one and add it. Grade every source:

- **A** — measured on this repo's data with folds. The only grade that promotes.
- **B** — external, with stated method, sample size and numbers.
- **C** — design only: shows the rule's shape, no performance data. Most MQL5
  articles are C.
- **D** — screenshots and assertions. Record it so nobody re-finds it.

Read sources **in full** and extract the actual numbers — sample size, date
range, win rate, per-variant P&L. A headline or an abstract is not a reading.
MQL5 marketplace listings are sales pages and never count as evidence
(CLAUDE.md 13.7).

### 4. Open a ledger row — before running anything

Write the entry in `docs/REMEDY_LEDGER.md` with the hypothesis stated **before**
you see the result. A hypothesis written afterwards is a description.

### 5. Measure the arm

Implement the change, mirror it into `backtest_scalper.py` in the same commit
if it touches a gate (invariant #2), add a test covering **both directions**,
then run the arm against the standing baseline in the ledger.

Promotion requires chronological 60/20/20 with the same sign in all three
folds (CLAUDE.md 13.5):

```bash
cd apex_ai && py -3.14 -E analyze_walkforward.py logs/bt_arm.json
```

Compare the profit factor of the trades the change **removed** against those it
**kept**, not just the headline P&L. That comparison is what exposed the EMA
band — it stripped out 197 trades at PF 1.52 and kept 83 at PF 0.94, which a
net-P&L number alone would not have shown.

Report `LOT_FLOOR` counts on any arm that changes risk (CLAUDE.md 13.9), or you
are comparing two different samples and calling the difference an edge.

### 6. Record the verdict

Write the result into the ledger row — `ACCEPTED` or `REJECTED`, with the
per-fold numbers and what the result does **not** say. A rejected remedy stays
in the file permanently. That is the point of it.

## Reading a failure mode

| Mode | What it means | Where the fix lives |
|---|---|---|
| `SIGNAL_FALSE` | Never went onside. MFE ≈ 0. | Entry qualification — the trigger detector |
| `GAVE_BACK_WINNER` | Reached ≥1R, closed at a loss | Exit management — partial, breakeven, trail |
| `STOP_TOO_TIGHT` | Stopped near the extreme, target then printed | Stop geometry — buffer, MAE percentile |
| `LOSS_ORDINARY` | Went onside, failed, stopped | Nothing. Cost of doing business. |
| `TIMEOUT_NEAR_MISS` | Timed out close to target | Time budget (`TIMEOUT_BARS`) |
| `TIMEOUT_STALLED` | Timed out having barely moved | Session/volatility admission |
| `TIMEOUT_CHOPPED` | Timed out after two-sided movement | Regime gate |
| `WIN_SURVIVED_DD` | Won, but took real heat first | Not a defect — feeds the MAE distribution for stop work |

`UNCLASSIFIED` means bars were missing for the holding window, not that the
trade was unusual.

## Traps this loop has already fallen into

- **Trusting `scalper_log.json` for P&L.** 140 of 282 closes booked at $0; 131
  of 187 reconcilable positions disagreed with the broker. It inverted the
  headline finding. The broker's deal history is the only account of record.
- **Reading a $0 close as a breakeven exit.** It is a booking failure, not a
  scratch. Check `history_deals_get` before theorising.
- **Ranking by dollars when half the sample is unpriced.** Read `n` and
  `n priced` together, always.
- **Quoting the whole live book as one strategy.** It spans five instruments
  and several configurations; the M15/M5 and 2R geometry landed 2026-08-22.
  Slice with `--since` and `--symbol`. The failure-mode *distribution* travels
  across configs; the win rate and PF do not.
- **Measuring an exit remedy in a simulator with no trailing.** The simulator
  holds SL/TP1 only, while the live Guardian moves to breakeven at peak ≥0.5R.
  No exit-side remedy is measurable until that gap closes — ledger L-003.

## Where things live

- Diagnosis output — `apex_ai/logs/sa_diagnosis.md` (live), `sim_diagnosis.md` (simulated)
- Raw incidents — `apex_ai/logs/sa_incidents.jsonl`, `sim_incidents.jsonl`
- Gate rejections — `apex_ai/logs/sa_rejections.json`
- Remedies and grades — `docs/REMEDY_KB.md`
- Verdicts — `docs/REMEDY_LEDGER.md`
- Prior evidence — `docs/RESEARCH_NOTES.md`, `CLAUDE.md` §13
- Operations — `.claude/skills/apex-ops/SKILL.md`
