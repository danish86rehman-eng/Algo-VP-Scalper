# Remedy ledger

Append-only. One entry per remedy that was proposed, from the moment it was
proposed. Entries are never deleted and never silently edited — a rejected
remedy is more valuable than an absent one, because it is what stops the same
idea being re-proposed next quarter.

**This file is the promotion gate.** A remedy reaches the live configuration
only via an entry here whose verdict is `ACCEPTED`, and a verdict can only be
`ACCEPTED` when the CLAUDE.md 13.5 bar is met: chronological 60/20/20 folds,
the same sign in all three. Nothing about the diagnosis pipeline shortens that.

## Entry format

```
## <ID> — <one-line claim>
- **Opened**      : YYYY-MM-DD, from <diagnosis file> (<mode>, n=<N>, $<cost>)
- **Failure mode**: the postmortem label this targets
- **Remedy**      : REMEDY_KB.md reference + the exact parameter change
- **Evidence**    : grade + source
- **Hypothesis**  : the falsifiable prediction, stated before the run
- **Arm**         : the exact backtest command
- **Result**      : baseline vs arm, per fold
- **Verdict**     : PROPOSED | TESTING | ACCEPTED | REJECTED | SUPERSEDED
- **Notes**       : what was learned, including what the number does NOT say
```

A `Hypothesis` written after seeing the result is not a hypothesis. Write it
into the entry before running the arm.

## Standing baseline

Every arm is compared against this, not against a remembered figure:

```bash
py -3.14 -E backtest_scalper.py --from 2026-05-15T00:00:00 --to 2026-08-23T00:00:00 --pool 1000 --risk 0.03 --symbols XAUUSD --loss-limit 100.0 --incidents logs/sim_incidents.jsonl --out logs/bt_baseline.json
```

**Restated 2026-08-27** when L-013 S1 landed. Every figure below is now booked
against the *fill* (the entry bar's open) rather than the signal price. The
pre-S1 column is kept because §§9-17 were all measured on it; regenerate it
with `--fill-signal-price`.

| | Baseline (S1, current) | pre-S1 (perfect fill) |
|---|---|---|
| Trades | 281 | 280 |
| Win rate | 45.91% | 46.07% |
| Net | +$2,757.57 | +$2,742.35 |
| Profit factor | 1.3475 | 1.3495 |
| Folds | +$1336 / +$256 / +$1165 — positive in all three | positive in all three |
| `LOT_FLOOR` | 12 | 13 |

The two columns differ by $15 on 281 trades. S1 is a fidelity fix, **not** a
slippage model — see L-013 for why that distinction is the whole result.

Report `LOT_FLOOR` counts with any arm that changes risk (CLAUDE.md 13.9), and
state the spread floor on any multi-year arm (CLAUDE.md 13.12) — the broker's
archived spread field is zeroed 2020-2025.

---

## L-001 — A wick-ratio floor on the sweep candle should cut `SIGNAL_FALSE`

- **Opened**: 2026-08-25, from `logs/sim_diagnosis.md`
  (`SIGNAL_FALSE`, n=43, −$2,209.84, mean MFE 0.17R, mean MAE 1.33R)
- **Failure mode**: `SIGNAL_FALSE`
- **Remedy**: REMEDY_KB R1. Add a minimum wick-ratio test to
  `SATriggerEngine._check_sweep_rejection`: the candle that pierces the level
  must carry a wick beyond it of at least X% of its own range. Sweep
  X ∈ {35, 45, 55}. Mirror into `backtest_scalper.py` in the same change —
  it is a gate, so invariant #2 applies.
- **Evidence**: grade C. [MQL5 art. 22140](https://www.mql5.com/en/articles/22140)
  specifies `MIN_WICK_RATIO = 45` and the exact ratio formula, but publishes no
  win rate, profit factor, trade count or date range. The threshold is a
  starting value, not a validated one.
- **Hypothesis**: at X=45, `SIGNAL_FALSE` occurrences fall by ≥30% and net P&L
  and profit factor hold or improve in all three folds. If net P&L falls while
  win rate rises, the filter is removing profitable trades along with the bad
  ones and fails on the same structural ground the EMA band failed on.
- **Arm**: pending — the filter is not implemented yet.
- **Result**: —
- **Verdict**: **PROPOSED**
- **Notes**: 276 of 280 trades come from `SWEEP_REJECTION`, so this moves the
  whole book. Expect trade count to fall; the question is whether the trades it
  removes were worse than the ones it keeps. Compare the PF of the removed
  subset against the kept subset explicitly, the way §11 did for the EMA band —
  that comparison is what exposed the band, and a headline P&L number would
  not have.

---

## L-002 — Winners' MAE distribution should set the stop, not a fixed ATR multiple

- **Opened**: 2026-08-25, from `logs/sim_diagnosis.md`
  (`STOP_TOO_TIGHT` n=6 in sim — below the actionable floor — but n=28 and
  −$210.13 in the reconciled live book, and `WIN_SURVIVED_DD` n=37 with mean
  MAE 0.71R says winners routinely take real heat before working)
- **Failure mode**: `STOP_TOO_TIGHT`, and indirectly `LOSS_ORDINARY`
- **Remedy**: REMEDY_KB R6. Replace the fixed `atr * 0.2` buffer in
  `_check_sweep_rejection` with a buffer derived from the MAE distribution of
  winning trades.
- **Evidence**: grade C for the method (Sweeney 1996, standard practice), but
  this is the one candidate where **grade-A evidence is directly obtainable**:
  `sa_incidents.jsonl` already carries `mae_r` and an outcome per trade, so the
  winners' MAE distribution is a groupby over data in hand.
- **Hypothesis**: winners' MAE has a long right tail, and a buffer at its 85th
  percentile converts a measurable share of `STOP_TOO_TIGHT` and
  `LOSS_ORDINARY` into wins without widening risk enough to lose more on the
  trades that were always going to fail.
- **Arm**: step 1 is measurement, not a code change — compute the winners' MAE
  distribution from the existing incident stream before touching the detector.
- **Result**: —
- **Verdict**: **PROPOSED**
- **Notes**: the overfitting risk here is severe and specific. Fitting a stop
  to the MAE of trades already known to have won is the standard way to
  manufacture a backtest that cannot survive live. Choose the percentile on
  fold 1 only, then test it on folds 2 and 3 without re-choosing.

---

## L-003 — Something closes live trades before the stop; the Guardian is the suspect

- **Opened**: 2026-08-25, from the reconciled live backfill
  (`logs/sa_diagnosis.md`, 282 trades, 2026-05-06 → 08-24)
- **Failure mode**: cross-cutting — inflates `GAVE_BACK_WINNER` and
  `STOP_TOO_TIGHT` in the live book, and is invisible in the simulator
- **Finding (broker-reconciled)**: **36 of 115 live losses (31.3%) closed
  without price ever reaching the original stop**, median MAE **0.54R**, total
  cost **−$89.29**. The simulator models SL/TP1 only and produces nothing of
  the kind, so the two books exit by different rules — the simulator is not a
  faithful model of live *exits*, whatever its fidelity on entries.
- **Confirmed cause, partially**: `logs/tga_action_log.json` records 175
  Guardian actions over 113 positions, 158 of them on SA-origin trades.
  **20 of the 36 early-closed losses carry a Guardian modification; 16 do
  not.** The Guardian's dominant action is `Trailing SL Stage 1` (111 of 175,
  the breakeven move at peak ≥0.5R), then Stage 2 (39). `EARLY_CLOSE` fired
  only 11 times and `NO_PROGRESS_CLOSE` once — so this is the **breakeven
  trail**, not the early-close engine. A median MAE of 0.54R sits squarely in
  the Stage 1 band, which is consistent.
  The other 16 have no Guardian record and remain unexplained — EOD flatten and
  the bar timeout are the next candidates.
- **Evidence for caution**: grade B, and it points the opposite way to
  intuition. [MQL5 art. 16991](https://www.mql5.com/en/articles/16991) found a
  *simple* trailing stop made an unprofitable book worse (−$658 → −$746) while
  adaptive trailing swung it to +$1,397 over the same deals. "Trail less" and
  "trail more" are both plausible and neither is free.
- **Hypothesis**: not yet stated — this remains an investigation, not a remedy.
  The factual half is now answered (Stage 1 breakeven, 20 of 36). The open
  question is whether the breakeven trail is net positive or negative, which
  needs the simulator to model it before it can be measured at all.
- **Verdict**: **PROPOSED** (investigation, partially resolved)
- **Notes**: the dollar cost is **small — −$89.29** — and an earlier reading of
  this entry claimed 54.3% of losses on the unreconciled log, which was wrong;
  L-004 explains why. The reason to keep this open is not its size but its
  kind: it is the only finding that says the simulator and the live agent
  disagree about something structural, and invariant #2 exists to stop exactly
  that. Until the simulator models the Guardian's breakeven move, every fold
  result for L-001 and L-002 is measured against an exit policy the live agent
  does not actually use. That does not invalidate those arms for *entry*-side
  questions, which is what both of them are — but it does mean no exit-side
  remedy (R4, R5) can be measured at all yet.

---

## L-004 — The trade log's P&L booking is unreliable and must not be a research input

- **Opened**: 2026-08-25, during the first backfill
- **Failure mode**: n/a — a data-integrity defect in the measurement chain
- **Finding**: `logs/scalper_log.json` booked **140 of 282** closed trades at
  $0. Reconciling against broker deal history priced **187** of them, and
  **131 disagreed with the log** and were repriced. The uncorrected log reports
  the book as +$61.80 / 27.66% WR / PF 1.09; the broker reports
  **+$947.93 / 52.78% WR / PF 1.48**.
- **Consequence, and it is not academic**: the first diagnosis run off the
  uncorrected log produced a confident, entirely false headline —
  `GAVE_BACK_WINNER` at 61 trades and "34.8% of trades destroyed by the exit".
  After reconciliation that mode is 15 trades. Trades that made money were
  labelled `LOSS` because `_on_trade_closed` derives the label from the P&L it
  is handed, so a $0 booking becomes a loss by construction, and a profitable
  trade that peaked at 1.2R then gets classified as a given-back winner. The
  pipeline invented an exit defect that did not exist.
- **Remedy applied**: `analyze_incidents.py --backfill` now sources both P&L
  and the win/loss label from `mt5.history_deals_get(position=...)`, including
  commission and swap, and falls back to the log only when the broker cannot
  price the position. `_broker_pnl()` carries the reasoning.
- **Verdict**: **ACCEPTED** (measurement fix, not a strategy change — it alters
  no gate and no parameter, so 13.5 does not apply)
- **Live agent fixed 2026-08-25 10:32 UTC**: the booking path no longer
  fabricates. `_book_settled_close()` books a vanished position only against
  settled deal history; an unsettled close is left unbooked and retried on the
  next cycle, bounded by `PNL_RECONCILE_GRACE_MIN = 10`, after which it books
  as `OUTCOME_UNRECONCILED` — a label outside the `WIN*`/`LOSS`/`TIMEOUT`
  vocabulary, so an unpriced trade can no longer be counted as a loss it was
  never shown to be. `_on_trade_closed(settled=False)` keeps that placeholder
  out of the journal and the forensic queue. `log_close` matches the most
  recent row for a ticket rather than only an `OPEN` one, so a correction can
  land, and writes a close-only row for adopted positions that never had a
  `log_open`. 172 tests clean; 13 added, each confirmed failing first.
- **What the evidence corrected**: the 140 $0 bookings are all from May 2026
  and the `booking $0.00` warning appears zero times in either log file, so
  they came from the older `history_deals_get(ticket=N)` defect rather than
  from the settlement path. The settlement path was a *latent* instance of the
  same fabrication and was reproduced directly before being fixed.
- **Still open**: a trade booked `UNRECONCILED` leaves the pool understated,
  and no live sweep re-prices it afterwards — that is still the analysis path.
  Closing it needs a correction-delta API on `SACapitalPool`; opened as a
  follow-up rather than folded in here, because it touches the capital ledger.
- **Notes**: the broker's deal history remains the only account of record,
  exactly as the operations playbook says. Any *historical* row in
  `scalper_log.json` predating this fix still carries the old fabrications and
  must be repriced before use as a research input.

---


## L-005 — H4 value-area location gate (sell VAH / buy VAL / no POC)
- **Opened**      : 2026-08-25, from a live loss the operator diagnosed by eye
                    (XAUUSD 16:45:25 UTC BUY 4642.26, SL 4634.49, -$23.29,
                    entry 0.93 from the 42-bar H4 POC at 4641.33)
- **Failure mode**: entry taken at fair value, where neither side has an edge
                    and the stop sits inside two-way business
- **Remedy**      : `scalper/vp_gate.py`, `VP_GATE_ENABLED`, modes
                    POC_ONLY / RANGING_ONLY / ALWAYS / POC_REQUIRE
- **Evidence**    : Grade C for the design (MQL5 art. 23169 supplies the
                    algorithm and publishes no performance data at all);
                    Grade A against, from our own walk-forward below
- **Hypothesis**  : stated before the arms were run — removing POC entries
                    raises win rate and net P&L by deleting coin-flip trades
- **Result**      : falsified. Baseline +$2742.35 / 280 trades / 46.07%.
                    Every arm worse. Damage monotonic in band width
                    (0.02 -> -$511, 0.05 -> -$744, 0.10 -> -$1328). The two
                    arms applying the full buy-low/sell-high rule flip sign
                    across folds (UNSTABLE — standing rejection criterion);
                    ALWAYS also fails the survival condition at 35.92% WR.
                    Attribution shows the rule is inverted: AT_POC is the best
                    location bucket (42 trades, PF 2.10, +$1038) and AT_VAL the
                    only negative one (26 trades, PF 0.93, -$50).
- **Verdict**     : **REJECTED**. Ships disabled. Re-measure only against a
                    continuation-heavy trigger mix.
- **Notes**       : docs/RESEARCH_NOTES.md §12. The volume-profile and regime
                    modules are kept — they are useful as telemetry whatever
                    happens to the gate.

## L-006 — longs into an H1 downtrend
- **Opened**      : 2026-08-25, from regime attribution of the 280-trade
                    baseline book (observation only, §12.6)
- **Failure mode**: counter-trend long taken while the H1 regime is
                    TRENDING_DOWN
- **Remedy**      : candidate only — veto BULLISH triggers when
                    `RegimeClassifier` reads TRENDING_DOWN on H1. **Not
                    implemented.**
- **Evidence**    : Grade D. n=8, single window, in-sample selection.
- **Hypothesis**  : not yet under test.
- **Result**      : pending. Observed: TRENDING_DOWN/BULLISH = 8 trades,
                    37.5% WR, -$214.91, PF 0.35, avg -$26.86 — the only
                    materially negative regime x direction cell. TRENDING_DOWN
                    overall is 21 trades and -$126.96 at PF 0.80.
- **Verdict**     : **OPEN — DO NOT PROMOTE.** n=8 cannot clear §13.5. Needs a
                    longer window or a second instrument before an arm is worth
                    running. Recorded so the lead is neither lost nor acted on
                    prematurely.

## L-007 — simulator read unclosed H4/H1 bars via `_closed`
- **Opened**      : 2026-08-25, found while mirroring the VP gate
- **Failure mode**: look-ahead / repaint in the simulator on frames slower than
                    the trigger frame
- **Remedy**      : `backtest_scalper._closed_tf(df, now, bars, tf_minutes)`,
                    which requires `time + tf_minutes <= now`
- **Evidence**    : Grade A, structural. `_closed` compares a bar's OPENING
                    stamp to `now`, so on H4 at 18:00 it returns the bar
                    stamped 16:00 — which does not close until 20:00. Live has
                    no equivalent hole: `copy_rates_from_pos(..., 1, n)` starts
                    at the last fully closed bar on whatever frame is asked for.
- **Result**      : the new VP gate uses `_closed_tf` on both its H4 and H1
                    frames. The **pre-existing** H1/H4 reads feeding the
                    short-term-bias and consultation gates were deliberately
                    left on `_closed`.
- **Verdict**     : **PARTIAL — OPEN for the legacy frames.** Re-timing those
                    would change what every existing number in RESEARCH_NOTES
                    §§9-11 was measured against, inside a change that was
                    adding a gate. It needs its own arm: re-run the baseline
                    with `_closed_tf` everywhere and compare. Until then, treat
                    H1/H4-derived gate behaviour in the simulator as marginally
                    optimistic.


### L-006 — VERDICT (closed 2026-08-26)

- **Result**  : **REJECTED.** Implemented as
                `scalper/regime_direction_gate.py`, modes SYMMETRIC and
                COUNTER_TREND_LONGS, mirrored into both decision paths, 14 new
                tests. Measured on three arms the hypothesis had never seen.
                In-sample the two modes disagree in *sign* (LONGS_ONLY +$294,
                SYMMETRIC -$274) — fitting a direction to n=8. Out of sample on
                XAUUSD 2025-08-01..2026-05-14, LONGS_ONLY is +$32.29 across 622
                trades ($0.05/trade); SYMMETRIC is +$268.62 but flips sign
                across folds. On USOIL the gate never fires: delta exactly
                $0.00 on 15 trades. XAGUSD produced no trades.
- **Verdict** : REJECTED. `RD_GATE_ENABLED = False`. Third trend filter
                rejected on this book after §11 (EMA band) and §12 (value-area
                location) — all three vetoed the fade that is ~99% of the
                trades.
- **Notes**   : docs/RESEARCH_NOTES.md §13.1. The run that settled it also
                produced L-008, which matters considerably more.

## L-008 — the baseline window may be unrepresentative
- **Opened**      : 2026-08-26, incidentally, while running L-006 out of sample
- **Failure mode**: not a trade defect — a *measurement* defect. Every gate
                    decision in RESEARCH_NOTES §§9-13 was judged against one
                    100-day window.
- **Remedy**      : none proposed. This is an audit, not a change.
- **Evidence**    : Grade A, our own simulator, gates unchanged.
                    XAUUSD 2025-08-01..2026-05-14: **622 trades, 35.53% WR,
                    -$650.66, negative in all three folds.**
                    XAUUSD 2026-05-15..08-23: 280 trades, 46.07% WR,
                    +$2742.35, positive in all three folds.
                    The earlier sample is 2.2x larger and loses consistently.
- **Hypothesis**  : 2026-05-15..08-23 is a favourable regime rather than the
                    strategy's steady state. Untested beyond these two windows.
- **Result**      : **CONFIRMED, and worse than the hypothesis stated.** Run
                    2026-08-27: 17 disjoint 6-month windows, 2018-03 -> 2026-08,
                    XAUUSD, $1000 @ 3%, $100 loss limit, gate chain exactly as
                    shipped. 14 windows produced trades; 3 (2018-03 -> 2019-09)
                    produced none. At a 5.0-pip spread floor: **4694 trades,
                    40.48% pooled WR, net +$2282, pooled PF 1.0289, 7 windows
                    positive / 7 negative, median window +$16.30.**
                    The recent +$2742 window is not an outlier and not modal —
                    the distribution is **centred on zero**, PF 0.87-1.23 in 13
                    of 14 windows. Full table: RESEARCH_NOTES.md §17.
                    Answers to the three questions the audit was opened on:
                    (1) neither outlier nor modal — near-zero-mean;
                    (2) fold-consistency carries **no** cross-regime
                    information (fires in only 4 of 14 windows, and those split
                    +$904/+$1187/-$172/-$680);
                    (3) the survival condition **holds in 14 of 14** (required
                    RRR 0.88-1.84, median 1.43, all below the shipped 2.0) —
                    yet realized expectancy is +0.016R against a naive +0.214R,
                    so **93% of the theoretical edge is lost between signal and
                    settlement** and TP1 geometry is not the defect.
                    Two data facts found en route: the 2018-19 zero-trade
                    windows are a **cost** result (spread 111-145 points, all
                    rejected at STEP3), not a data gap; and the broker's spread
                    field is **zeroed 2020-03 -> 2025-03**, so 11 of 14 windows
                    ran on the `--spread-pips` fallback. Raising it 2.5 -> 5.0
                    costs $1264 (36% of the headline) while the two windows with
                    a real spread field move by $0.03 and $14.99.
                    `LOT_FLOOR` is 0-4 before 2025-09 then 490 and 153 (§13.9):
                    the last two windows admit a different sample.
- **Verdict**     : **CLOSED — the baseline window was unrepresentative, and the
                    strategy's steady state is approximately break-even before
                    slippage and negative after it.**
                    Pooled gross gains $81,265 vs gross losses $78,982 is a 2.9%
                    margin. Applying **only** L-012's measured reward-leg
                    haircut (2.000R -> 1.852R median), and holding the loss leg
                    constant although L-012 says stops widen too, gives
                    **net -$3,731, PF 0.9528**. The nine-year book is thinner
                    than its own measured slippage.
                    Consequences, binding until superseded:
                    * **Do not quote +$2742 as an expected return** — nor
                      -$657 as a refutation. Both are tails.
                    * **Every REJECTED verdict in §§9-13 is downgraded.** Each
                      was decided by a few hundred dollars on one draw from a
                      near-zero-mean process. They establish that a gate did not
                      rescue one window; they do **not** establish that the gate
                      is harmful. Re-read L-005, L-006, L-010, L-011 with that
                      caveat. None is thereby promoted — they stay off.
                    * **§13.5 as written does not do what it claims.** 60/20/20
                      inside one window tests within-window stability, not
                      out-of-sample validity. Any future promotion must fold
                      across **disjoint multi-month windows**, and must state
                      its spread floor, which the archive cannot supply.
                    * **L-012 is the binding constraint, not the gate chain.**
                      No gate measured so far is large enough to matter against
                      a 2.9% margin. Entry-side gate work is not where the
                      remaining value is.
                    Standing caveat, unchanged: current parameters (TP1 = 2R,
                    cooldown, session windows) were chosen with recent data in
                    view, so this tests the **gates** out of sample and not the
                    parameters, which remain fitted.
- **Notes**       : docs/RESEARCH_NOTES.md §17. Raw:
                    `apex_ai/logs/l008_window_audit.json` (floor 2.5) and
                    `l008_window_audit_sp5.json` (floor 5.0).


### L-008 — pickup notes (2026-08-26) — SUPERSEDED, audit ran 2026-08-27

> Kept as the record of what was scoped. The audit is CLOSED above; read
> the verdict, not this. Two claims here did not survive contact: the
> 2018-03 data floor is real but produces **zero trades** (spread, not
> bars), and the archive's spread field is zeroed 2020-2025 so no window
> set can be run without stating a `--spread-pips` floor.

Scoped and costed, then deferred to a fresh session. Everything needed to start
cold is here; do not re-derive it.

**Data availability — settled.** M5 and M15 both reach back to **2018-03** for
XAUUSD on Exness-MT5Trial2, via `copy_rates_range`, which is the API
`backtest_scalper._fetch` already uses. The apparent 2023-10-27 floor is an
artifact of `copy_rates_from_pos` hitting its 200,000-bar cap and is **not** a
real limit. Verified bar counts for a one-month probe in each March:

    2018 M5=5749 M15=1917 | 2019 5756/1921 | 2020 6092/2033 | 2021 6357/2121
    2022 M5=6357 M15=2121 | 2023 6296/2100 | 2024 5527/1845 | 2025 5816/1941

H1/H4/D1 reach 2014-01, so every slower gate frame is covered with lead-in to
spare.

**The audit to run.** 17 disjoint 6-month windows, 2018-03 -> 2026-08, XAUUSD,
`--pool 1000 --risk 0.03 --loss-limit 100.0`, gate chain untouched (all optional
gates off, as shipped). Per window record: trades, win rate, net $, PF,
`LOT_FLOOR` count (§13.9 — otherwise the windows are different samples), and
60/20/20 fold signs via `analyze_walkforward.py`.

Batch every arm into one shell command and report one table. The runs are cheap;
narrating them is what is not.

**The three questions it has to answer**, stated before the numbers exist:

1. Is the +$2742 recent window an **outlier**, or the modal outcome? If most
   windows are negative, every "REJECTED" verdict in §§9-13 was measured against
   an unrepresentative sample and reads far stronger than it is.
2. Does **fold-consistency inside a window** carry any information across
   regimes? Both existing windows are fold-consistent and have opposite signs,
   which is already weak evidence that it does not.
3. Does the **survival condition** hold out of sample? Compute
   `(1 - WR) / WR` per window against the shipped RRR of 2.0. The prior window's
   35.53% WR demands 1.81; if most windows sit near that, the geometry has no
   margin and §13.1 needs revisiting rather than the gates.

**Standing caveat that survives whatever the audit finds.** Current parameters
(TP1 = 2R, cooldown, session windows) were chosen with recent data in view.
Replaying them backwards tests the *gates* out of sample but **not the
parameters** — those remain fitted no matter how many windows are added.

**Read L-012 before acting on any of it.** The simulator fills at the signal
price by construction, so every window in this audit will also assume a perfect
fill. Measured live slippage turns an intended 2.000R into a median 1.852R. The
audit measures the strategy's shape, not its live expectancy.

## L-009 — VALUE_AREA_FADE cannot execute: the STB gate forbids fades
- **Opened**      : 2026-08-26, on building the operator's rule as a trigger
- **Failure mode**: not a trade defect — a **gate contradiction**. The
                    short-term-bias rule "trigger opposes a clear short-term
                    read" vetoes mean-reversion entries by definition.
- **Remedy**      : candidate only. (a) a fade-trigger relaxation mirroring
                    `STB_CONTINUATION_TRIGGERS`; (b) looser VA_FADE confluence
                    so detections reach a testable sample. **Not implemented.**
- **Evidence**    : Grade A, structural, from our own simulator.
                    Isolated VALUE_AREA_FADE: 3 detections / 0 trades over
                    2026-05-15..08-23; 10 detections / 0 trades over
                    2025-08-01..2026-05-14. Rejections:
                    `STB/opposes_short_term` 3 of 3 and 9 of 10; the tenth was
                    `THIN_LIQ`.
- **Hypothesis**  : untested — the rule has never been allowed to trade.
- **Verdict**     : **OPEN.** The value-area entry model is implemented,
                    tested and shipped disabled (`VA_FADE_ENABLED = False`).
                    It is neither confirmed nor refuted: it has taken zero
                    trades. Do not report §12's rejection as covering this —
                    §12 rejected the rule as a *filter*, which is a different
                    claim.
- **Notes**       : docs/RESEARCH_NOTES.md §14. Detection rate (~13 signals in
                    13 months) means (b) matters as much as (a).

## L-010 — should the `Whole_day` catch-all window be enabled?
- **Opened**      : 2026-08-26, on an operator question ("would Whole_day help
                    or hurt?")
- **Failure mode**: not a trade defect — a **standing default never measured on
                    the current stack**. `Whole_day` was disabled in §13.6 on a
                    donor-campaign rationale (different strategy, 1R geometry,
                    different gate set). The default was inherited, not tested.
- **Hypothesis**  : stated before the arms were run — enabling `Whole_day`
                    increases trade count and therefore net P&L if off-session
                    signal quality is comparable to kill-zone quality.
                    **Refuted.**
- **Remedy**      : none. Confirms the existing default; no code change.
- **Evidence**    : Grade A, own simulator, `--allow-whole-day` the only
                    variable, gate chain otherwise untouched. Six paired arms:
                    XAUUSD 3% recent  +$2742 (+ + +) vs +$609 (+ - -/unstable);
                    XAUUSD 3% prior   -$657 (- - -) vs -$662 (- - +/unstable);
                    3-symbol 3%       +$2607 (+ + +) vs +$532 (unstable);
                    XAUUSD 2%         +$1617 (+ + +) vs +$866 (unstable).
                    Kill-zone gating is fold-CONSISTENT 6/6; whole-day is
                    fold-UNSTABLE 6/6. `LOT_FLOOR` reported per §13.9 (53 -> 183,
                    52 -> 134): the whole-day arms admit a different sample too.
- **Result**      : the extra hours are ~breakeven standalone (224 trades,
                    +$27.78, PF 1.01). The loss comes from **cannibalisation**:
                    the kill-zone book falls +$2742 -> +$581 on the same hours.
                    Hour 00 is a natural control — nothing precedes it, its
                    trade count is identical (77 -> 77) and its P&L barely
                    moves, while every downstream kill-zone hour loses trades
                    and flips negative (hr 06 +$330 -> -$263, hr 12 +$394 ->
                    -$354). `SYMBOL_DEDUP` 856 -> 3656 is the structural channel
                    and cannot be switched off; disabling the cooldown and the
                    daily loss limit *widens* the gap to -$2963, so those two
                    were masking damage, not causing it.
- **Verdict**     : **REJECTED / CLOSED.** `allow_whole_day` stays False.
                    `--allow-whole-day` remains a plumbing-test switch only.
- **Notes**       : docs/RESEARCH_NOTES.md §15. Carries a **correction**: the
                    "Asia block = 68-73% of volume and the largest absolute
                    loss" rationale in `session_checker.py` and CLAUDE.md §13.6
                    does not reproduce. Asia is 43.0% / 40.2% of volume in the
                    two windows and carries the largest loss in neither — it is
                    the only *profitable* block in the recent window, and
                    `TOKYO_OPEN` is the best single session in the book. The
                    conclusion stands; the reason was wrong, and acting on the
                    old reason would point at cutting `TOKYO_OPEN`, the most
                    expensive session to remove. Inherits L-008: both windows
                    are still only two windows.

## L-011 — the decision path never reads PDH/PDL
- **Opened**      : 2026-08-26, on an operator hypothesis: "until you keep track
                    of PDH/PDL you will keep taking trades on the wrong side of
                    the market. NY session today is a great example, and the
                    current open position is on the wrong side."
- **Failure mode**: not a trade defect — a **missing input**. Confirmed by code
                    read, not inference: `core/liquidity_engine.py` computes
                    `prev_day_high` / `prev_day_low` (`_prev_day_hl`, l.187) and
                    `sa_consultant.analyse()` calls that engine (l.149), but
                    keeps only `price_zone`, `nearest_bsl`, `nearest_ssl`.
                    `SAConsultResult` has **no PDH/PDL field**, and the two
                    pools it does carry feed Gate 3 (TP2 realignment) only —
                    never admission or direction. The trigger's own liquidity
                    pass, `trigger_engine.step1_liquidity`, runs on **M5** over
                    50 bars and defines `session_high/low` from **today's bars
                    only**, so no daily level can reach a trigger at all.
                    Net: PDH/PDL is computed, then discarded, on every scan.
- **The operator's example** (verified against broker bars, not the trade log):
                    XAUUSD SELL 0.01 @ 4611.38, ticket 494125751, opened
                    2026-08-26 13:00:10 UTC, SL 4635.377. PDL (25 Aug low) =
                    4605.33 — **$6.05 below the entry, unswept**. The next M15
                    bar (13:15) printed 4598.10, sweeping PDL by $7.23, closed
                    back above at 4611.19, then ran to 4629.31. The agent sold
                    into the pool 15 minutes before the pool was swept and
                    rejected. Mechanically exactly the described defect.
- **Hypothesis**  : stated before any arm was run — the previous day's range
                    carries directional information the book currently ignores,
                    so vetoing triggers that enter against it raises net P&L
                    with the same sign in all three folds, in both disjoint
                    windows of L-008.
- **Remedy**      : `scalper/pdr_gate.py` — previous-day-range location veto,
                    three modes so the operator's literal claim and the
                    attribution-backed variant are measured separately rather
                    than argued about:
                      `SHORT_DISCOUNT` — veto BEARISH at loc <= 0.25 (the
                                         operator's literal hypothesis; the
                                         bucket today's live trade falls in)
                      `LONG_PREMIUM`   — veto BULLISH at loc >= 0.75
                      `SYMMETRIC`      — both
                    Ships OFF (`PDR_GATE_ENABLED = False`), mirrored into
                    `backtest_scalper.py` in the same change per invariant #2.
- **Evidence so far (attribution only — NOT a filter forecast, §13.11)**:
                    905 baseline trades, both L-008 windows, labelled by entry
                    location in the previous day's range:
                      PD_PREMIUM  BULLISH  recent PF 0.60 (-$463, n=28)
                                           prior  PF 0.86 (-$129, n=68)  STABLE
                      PD_DISCOUNT BULLISH  recent PF 1.73  prior PF 1.53  STABLE
                      PD_DISCOUNT BEARISH  recent PF 1.56  prior PF 0.72  FLIPS
                      ABOVE_PDH / BELOW_PDL / PD_MID                      FLIP
                    Two operationalizations already **refuted** at the
                    attribution stage:
                      - proximity to the opposing unswept pool (<1R): PF 1.31
                        recent vs 0.44 prior — flips, sub-buckets under n=20;
                      - alignment with a formed daily sweep bias: OPPOSES 1.33
                        vs AGREES 1.31 (recent), 0.90 vs 0.98 (prior) — no
                        signal in either window.
                    So the one stable cell is on the **long** side, which is the
                    opposite side from the trade that prompted the hypothesis.
- **Result**      : all three modes measured against a control that reproduces
                    the standing baseline **exactly** (280 trades, +$2742.35 —
                    so the added D1 fetch is inert when the gate is off).

                    | window | arm | n | WR% | net $ | PF | PDR vetoes | LOT_FLOOR | folds |
                    |---|---|---:|---:|---:|---:|---:|---:|---|
                    | recent | control        | 280 | 46.07 | +2742.35 | 1.35 |   0 |  13 | + + + |
                    | recent | SHORT_DISCOUNT | 279 | 45.16 | +1937.66 | 1.29 |  49 |  23 | + + + |
                    | recent | LONG_PREMIUM   | 290 | 45.17 | +2028.20 | 1.27 |  80 |  13 | + **-** + |
                    | recent | SYMMETRIC      | 282 | 46.10 | +2489.53 | 1.37 | 138 |  27 | + + + |
                    | prior  | control        | 625 | 35.52 |  -656.96 | 0.91 |   0 | 914 | - - - |
                    | prior  | SHORT_DISCOUNT | 622 | 35.69 |  -612.21 | 0.92 | 192 | 807 | - - - |
                    | prior  | LONG_PREMIUM   | 620 | 35.16 |  -611.15 | 0.92 | 320 | 747 | - - - |
                    | prior  | SYMMETRIC      | 647 | 35.55 |  -521.90 | 0.93 | 505 | 584 | - - **+** |

                    Every mode costs money in the window where the strategy
                    works (-$805 / -$714 / -$253) and returns almost nothing
                    where it does not (+$45 / +$46 / +$135). `LONG_PREMIUM` and
                    `SYMMETRIC` fail §13.5 outright. `SHORT_DISCOUNT` holds its
                    sign in both windows and is still rejected on P&L.

                    `LOT_FLOOR` reported per §13.9 and it moves a lot (13 -> 27,
                    914 -> 584): the arms admit materially different samples,
                    which is a second reason not to read the deltas as an edge.
- **The §13.11 trap, walked into again and caught by the re-run**:
                    attribution said vetoing PD_PREMIUM/BULLISH should **add**
                    +$463 (recent) and +$129 (prior). The `LONG_PREMIUM` arm
                    actually **lost $714** in the recent window — a $1,177 miss
                    in both sign and size. Trade counts *rose* in two arms
                    (280 -> 290, 625 -> 647) because a veto frees a position slot
                    and skips a cooldown, so the gated book is composed of
                    different trades than the bucket it was read off. This is
                    now the second campaign where an attribution bucket failed
                    to survive contact with a full re-run (cf. L-005).
- **The mechanism claim is INVERTED**: the operator's reading is that entering
                    just before an unswept daily pool is the error. Measured
                    directly, that bucket is the book's **best**, and it holds
                    in both windows at every threshold tested:

                    | pool within | window | trades where the pool was swept during the trade | WR | net $ | PF |
                    |---|---|---:|---:|---:|---:|
                    | 0.5R | recent | 12 | 58.3% |  +324.65 | 2.14 |
                    | 0.5R | prior  | 12 | 58.3% |   +90.60 | 1.77 |
                    | 1.0R | recent | 20 | 65.0% |  +669.92 | 2.88 |
                    | 1.0R | prior  | 18 | 50.0% |   +83.68 | 1.48 |

                    against book PF 1.35 / 0.91. The pattern is also rare —
                    1.3-10% of trades depending on threshold — so it cannot be
                    what drives the book either way.
- **The prompting trade, re-read**: ticket 494125751 sits in exactly that
                    bucket. PDL was 0.25R below entry and **was swept** (low
                    4598.10 vs PDL 4605.33). MFE **+0.55R**: the trade went the
                    operator's "wrong" way, which is to say the right way. It
                    then gave the move back (MAE 0.93R). It is not a wrong-side
                    trade; it is a right-side trade whose target sat 1.64R away
                    while the sweep delivered 0.55R. See L-012 for why 1.64R and
                    not 2R.
- **Verdict**     : **REJECTED / CLOSED.** `PDR_GATE_ENABLED` stays False. The
                    module, its three modes and `--pdr-gate` ship as a measured
                    negative so the idea is not re-proposed from intuition.
                    **The premise is nonetheless confirmed and remains true**:
                    the decision path reads no daily level at all. What is
                    refuted is that closing that gap with a veto helps.
- **Notes**       : docs/RESEARCH_NOTES.md §16. Inherits L-008 — two windows are
                    still two windows. Not tested: PDH/PDL as a *signal*
                    (a pool `SWEEP_REJECTION` is allowed to fade) rather than as
                    a veto. `trigger_engine.step1_liquidity` picks the *nearest*
                    pool, so simply adding daily levels to the candidate set
                    would rarely change a decision — a real test needs a
                    strength-ranked pool set, which is its own row.

## L-012 — SL/TP are anchored to the signal price, never re-anchored to the fill
- **Opened**      : 2026-08-26, found while re-reading ticket 494125751 for L-011.
- **Failure mode**: `_execute` sends `trigger.stop_loss` and `trigger.tp1` as
                    absolute prices computed from `trigger.entry_price` — the
                    price at signal time — and never recomputes them against the
                    actual fill (`scalper_agent.py` l.920-921). Position sizing
                    uses the same signal price (`sl_pips_for_check`, l.812 and
                    l.1360). An adverse fill therefore silently widens the real
                    risk **and** narrows the real reward, and the trade carries
                    a worse R:R than the geometry §13.1 requires.
- **Measured**    : 11 post-migration live trades reconciled against broker
                    fills. `scalper_log.json` stores the *signal* price, so it
                    reads exactly 2.000R by construction and cannot show this at
                    all — the fill has to come from `history_deals_get`:
                      - 8 of 11 filled adversely
                      - median realized R:R **1.852** against an intended 2.000
                      - worst case #494039170: +4.310 slippage -> **0.54R**
                      - the prompting trade #494125751: +2.842 -> **1.64R**
                    §13.1 demands RRR > 1.81 at the prior window's 35.53% win
                    rate. The median realized geometry sits *at* that line and
                    several trades fall well under it.
- **Why it matters beyond the dollars**: the simulator fills at the signal price
                    by construction, so **every backtest number in this
                    repository assumes a perfect fill**. Live is structurally
                    worse than sim by this amount and no existing fold test can
                    see it.
- **Hypothesis**  : re-anchoring SL/TP and the volume calculation to the fill
                    price preserves the intended R multiple without changing
                    which trades are admitted.
### L-012 — RE-MEASURED 2026-08-28 on n=23: the haircut is less than half what n=11 showed

Run with both agents stopped (no MT5 contention), `logs/scalper_log.json`
signal prices against `history_deals_get` fills, deal history from 2026-08-01.
287 of 310 log rows have no deal record — the broker archive starts 2026-08-01
while the log reaches back to May — so 23 is everything available.

| | n=11 (2026-08-26) | **n=23 (2026-08-28)** |
|---|---:|---:|
| adverse fills | 8/11 (72.7%) | **14/23 (60.9%)** |
| median realized R:R | 1.852 | **1.945** |
| reward-leg haircut | 0.9260 | **0.9723** |
| worst case | 0.54R | 0.543R |

Median slippage **+0.347** price units (adverse); median risk inflation
(actual stop / intended stop) **1.0188**. Distribution: p10 **1.438R**,
p25 **1.614R**, p75 **2.734R**. **7 of 23 (30.4%)** land below the 1.81
survival line.

**Three cautions that stop this from being good news.**

* **The mean and max of realized R:R are meaningless — use the median only.**
  Mean 2.723, max 16.195R. When a fill lands almost on the stop, actual risk
  tends to zero and the ratio explodes. This is a defect of the metric, not a
  favourable tail.
* **Mean slippage is FAVOURABLE (-0.832).** The demo server fills better than
  the signal on average. That is a demo artefact and will not survive a live
  book, so **0.9723 is a floor, not an estimate.**
* **It is still the size of the whole edge.** §13.12 records gross gains
  $81,265 against gross losses $78,982 — a 2.9% margin. A 2.8% reward-leg
  haircut is the same order of magnitude as the entire edge.

**Consequence for the -$3,731 / PF 0.9528 figure in §13.12.** That projection
applied a **0.9260** haircut. The measured haircut is **0.9723**, so the drag
is roughly 38% of what was projected. **The corrected nine-year figure has NOT
been computed** — it requires re-running the L-008 campaign, which was not done
here. Do not quote a revised number; quote the haircut and say the projection
overstates the drag.

Status unchanged: **OPEN, measurement only.** n=23 is still far below anything
§13.5 acts on, and re-anchoring moves a stop, which makes it an exit-side
change under L-003. L-003 is now closed (the simulator models the Guardian
behind `--tga-exits`), so the prerequisite that blocked this is gone — but the
fix itself is still unmeasured.

- **Status**      : **OPEN — measurement only, no code change.** n=11 is far
                    below anything §13.5 acts on, and the fix is not free: it
                    moves the stop, so it is an exit-side change and L-003
                    applies (the simulator models no trailing). Needs its own
                    arm design before anything is touched.
- **Verdict**     : **OPEN.**
- **Blocked by**  : added 2026-08-27 — **L-013**. This row's remedy cannot be
                    measured at all while the simulator fills at the signal
                    price, because the arm and its baseline would both book a
                    perfect fill and the delta would be identically zero.
                    L-013 models the fill (simulator-only, no live change) and
                    is the prerequisite. Do not attempt L-012's re-anchoring
                    before it closes.
                    L-008 (§17) also raised this row's stakes considerably: the
                    nine-year book is pooled PF 1.0289, and this row's measured
                    haircut alone is arithmetically enough to invert it.

---

## L-013 — the simulator fills at the signal price; model the fill instead
- **Opened**      : 2026-08-27, on closing L-008. Opened as a **measurement**
                    row, not a remedy: it changes what the simulator reports,
                    not what the live agent does.
- **Failure mode**: not a trade defect — a **fidelity defect**, the same class
                    as L-007 (unclosed bars) and the §13.4 parity drifts. Three
                    separate idealisations, all pushing results optimistic:
                    1. **Entry fills at the signal price.** `entry=trigger.entry_price`
                       (`backtest_scalper.py` l.866, and `_order_pnl` is handed
                       the same value at l.843). Live sends a market order on
                       the tick after the decision bar closes and fills wherever
                       the book is.
                    2. **Exits fill exactly at the level.** `_schedule_exit`
                       (l.436-444) returns `trigger.stop_loss` / `trigger.tp1`
                       verbatim whenever a bar's range touches them, so a bar
                       that gaps $3 through the stop still books a stop-price
                       fill. Live fills at or beyond.
                    3. **Spread is a P&L deduction, not a fill displacement.**
                       `cost = bar_spread * pip_val * volume` (l.851) is
                       subtracted after the fact while both fills happen at mid.
                       Live buys the ask and its stop triggers on the bid, so
                       the effective stop is *nearer* and the effective target
                       *further* than `_schedule_exit` models. Spread therefore
                       changes which exits fire, not just what they cost — and
                       that channel is currently absent.
                    Sizing (`_calc_volume(..., trigger.entry_price, ...)`,
                    l.829) is **faithful** — live sizes off the signal price too
                    (L-012, `scalper_agent.py` l.812, l.1360). Do not "fix" that
                    one in isolation; it would create a fresh divergence.
- **Remedy**      : simulator-only. No `scalper_agent.py` change, so **L-003
                    does not block this** — nothing moves a live stop. Proposed
                    in three separable stages, cheapest first:
                    * **S1 — next-bar-open entry.** Fill at the open of the M5
                      bar at or after the decision instant rather than at
                      `trigger.entry_price`. Mechanical, needs no calibration,
                      no free parameter. SL/TP stay anchored to the signal
                      price because that is exactly what live does (L-012).
                    * **S2 — sided fills.** Entry at mid ± spread/2 in the
                      adverse direction; stop and target tested against the
                      side that actually triggers them. Replaces the flat
                      `cost` deduction rather than stacking on it — double-
                      charging spread is the obvious way to get this wrong.
                    * **S3 — gap-through exits.** When a bar's open is already
                      beyond the level, fill at the open, not the level.
                    Each stage lands with its own before/after table so their
                    contributions stay separable.
- **Evidence**    : grade A for the defect (read directly off the code above);
                    grade A but **n=11** for its size — L-012's broker
                    reconciliation, 8 of 11 adverse, median realized 1.852R
                    against an intended 2.000R, worst case 0.54R.
                    Grade A for why it is now decisive: L-008 (§17) puts the
                    nine-year book at pooled **PF 1.0289** on 4694 trades, and
                    an arithmetic haircut of L-012's reward leg alone — loss
                    leg held constant, which is the optimistic side — gives
                    **net -$3,731, PF 0.9528**.
- **Hypothesis**  : stated before any arm is run. **Modelling fills flips the
                    nine-window L-008 book from positive to negative, by an
                    amount of the same order as the L-012 arithmetic estimate.**
                    Concretely: S1+S2+S3 takes 2018-03..2026-08 at a 5.0-pip
                    floor from +$2282 / PF 1.0289 to **net < $0 with PF < 1.00**,
                    and takes the standing baseline window (2026-05-15..08-23)
                    materially below its +$2742.
                    **Falsified if** the modelled book stays positive at
                    PF >= 1.02 — which would mean the n=11 live sample overstates
                    typical slippage and L-012's severity is wrong, itself a
                    result worth having.
                    Deliberately **not** predicted: which way trade *count*
                    moves. S2 changes which exits fire, so the arm is not a pure
                    re-pricing of the same book and per-trade comparison will
                    not be like-for-like.
- **Arm**         : standing baseline window first, then the nine-window L-008
                    replay, gates as shipped, spread floor stated:
                    `py -3.14 -E backtest_scalper.py --from 2026-05-15T00:00:00 --to 2026-08-23T00:00:00 --pool 1000 --risk 0.03 --symbols XAUUSD --loss-limit 100.0 --spread-pips 5.0 --out logs/bt_fillmodel.json`
                    plus the 17-window driver used for L-008, each run with and
                    without the fill model. Validation against reality: the
                    modelled realized-R distribution must be compared to
                    L-012's 11 broker-reconciled fills — if the simulator's
                    median lands far from 1.852R the model is miscalibrated,
                    whichever direction the P&L moved.
- **Result**      : **S1 measured 2026-08-27. Implemented, shipped enabled, and
                    it does not do what the row hoped.**
                    Baseline window (2026-05-15..08-23): perfect fill +$2742.35
                    / PF 1.3495 / 280 trades -> S1 **+$2757.57 / PF 1.3475 /
                    281 trades**. Nine-year replay, 14 trade-bearing windows at
                    a 5.0-pip floor: +$2282.45 -> **+$2570.90**, 7 positive / 7
                    negative unchanged, fold-consistent count unchanged at 4,
                    **zero windows flip sign**.
                    The aggregate +$288 is **not** a re-pricing effect: 12 of 14
                    windows move by less than $40, while two move +$294 and +$88.
                    Those two are balance-feedback path divergence (P&L feeds
                    `balance` feeds `risk_usd` feeds sizing and the daily-loss
                    gate), which is chaotic, not systematic. S1's direct effect
                    is indistinguishable from zero.
                    **The calibration check written into this row fails, and
                    that is the finding.** S1's TP exits book a median
                    **1.997R** against L-012's live **1.852R** — it reproduces
                    ~2% of the gap it was built to expose. Median modelled
                    slippage is **$0.048**; L-012's live cases were $2.84 and
                    $4.31, 50-90x larger. 54.8% of modelled fills are adverse
                    against 8 of 11 live.
                    **Mechanism, now settled:** `trigger.entry_price` is
                    `df_m5['close'].iloc[-1]` (`trigger_engine.py` l.184) — the
                    last closed M5 bar's close — and the simulator's decision
                    instant is that same bar boundary. The "next bar open" S1
                    fills at is the immediately following tick. S1 measures
                    tick-boundary noise. Live slippage is a 30-second scan
                    interval plus a market order filling at the ask, and **no
                    refinement of bar-granularity entry timing will produce
                    it.**
- **Verdict**     : **S1 ACCEPTED as a fidelity fix; REJECTED as a model of
                    live slippage.** Ships enabled (`FILL_AT_NEXT_BAR_OPEN =
                    True`), `--fill-signal-price` regenerates pre-S1 tables.
                    §13.5 folds are **not** the bar here and were not applied:
                    S1 changes no gate, no parameter and nothing in
                    `scalper_agent.py`. It is a measurement change, and the
                    only question a fold test could answer about it is one
                    nobody asked.
                    What S1 *did* buy, and it is worth the change on its own:
                    it closed a genuine internal incoherence — `_schedule_exit`
                    had always timed exits from the entry bar's open while P&L
                    was booked at the signal price, so the simulator disagreed
                    with itself about when the fill happened.
                    **The row stays OPEN for S2 and S3.** S1 has eliminated
                    bar granularity as the explanation for L-012, which narrows
                    the remaining candidates to (a) spread sidedness — S2, and
                    note spread currently changes what an exit *costs* but not
                    which exit *fires*, so that channel is entirely absent —
                    (b) gap-through exits (S3), and (c) a latency stage this
                    row did not anticipate: the live agent scans on a 30s
                    interval, so it acts 0-30s after the bar the simulator
                    decides on. (c) is new and should be added as **S4** before
                    S2 is run, because it is the only candidate whose scale
                    matches the measured gap.
- **Notes**       : Three things this row must not be allowed to become.
                    * **It is not a strategy change and must not acquire one.**
                      If an arm looks bad, the response is not to re-anchor
                      SL/TP to make it look better — that is L-012, it is
                      exit-side, and L-003 still blocks it. L-013 only makes
                      L-012 *measurable*; it does not implement it.
                    * **It invalidates prior numbers rather than adding to
                      them.** On landing, every table in RESEARCH_NOTES §§9-17
                      and the ledger's Standing baseline were computed on
                      perfect fills. The baseline must be re-run and restated in
                      the same change, or the file starts mixing two cost models
                      — the §13.4 failure, repeated.
                    * **It is a one-way fidelity change.** Unlike a gate there
                      is no "off" position that is more correct. Ship it
                      enabled, with a flag only so the before/after tables can
                      be regenerated.
                    Sequencing: L-013 is a **prerequisite for L-012** and for
                    the exit-side half of L-003. Neither can be measured while
                    fills are perfect. Given L-008's finding that no gate is
                    large enough to matter against a 2.9% margin, this row is
                    ahead of any further entry-side gate work.

## L-014 — VP_LIQUIDITY_REACTION: a liquidity raid at an anchored H4 VP level
- **Opened**      : 2026-08-27, on an operator specification. Distinct from
                    L-005 (VP as a veto) and L-009 (the value-area edge as a
                    fade signal): here the profile is a LOCATION and the
                    direction comes from the liquidity raid plus an M5 MSS.
- **Failure mode**: not a trade defect — a **capability gap**. No daily or
                    profile level could originate a trade, and the agent was
                    structurally IDLE 02:00-06:30 UTC (measured:
                    `ACTIVE -> IDLE` 02:00:12Z, `IDLE -> ACTIVE` 06:30:18Z on
                    2026-08-27), so late-Asia setups were never even scanned.
- **Remedy**      : `scalper/anchored_vp.py` (causal leg-anchored profile),
                    `scalper/vp_liquidity_trigger.py` (the setup contract),
                    `SAState.VP_ONLY` + `SASessionChecker.vp_window_open`
                    (trigger-scoped Asia allowance), `VPLR_*` in
                    `decision_params`. Modes ASIA_ONLY / ALL_SESSIONS.
- **Evidence**    : Grade A against, from our own disjoint-window runs below.
                    The design reuses art. 23169 arithmetic unchanged (Grade C
                    design evidence, no performance data — §13.7).
- **Hypothesis**  : stated before the arms were run — a raid at a profile level
                    confirmed by structure is a higher-quality entry than the
                    bare sweep rejection it out-ranks, and the Asia hours it
                    unlocks are incremental rather than cannibalising.
- **Result**      : falsified in both scopes. XAUUSD, $1000 @ 3%, two disjoint
                    windows:

                    | arm | W1 2026-05-15..08-23 | W2 2025-08-01..2026-05-14 |
                    |---|---:|---:|
                    | baseline            | +2757.57 | -650.02 |
                    | VP ALL_SESSIONS     |  +420.55 | +3839.08 |
                    | VP ASIA_ONLY        | +3009.29 |  -783.25 |

                    ALL_SESSIONS: delta -$2337 / +$4489 — sign flips. It
                    displaces SWEEP_REJECTION almost entirely (277 taken
                    trades -> 23 in W1) because it detects 1082-1546 times per
                    window and sits at the head of the priority order.
                    ASIA_ONLY: delta +$252 / -$133 — sign flips, and the VP
                    trades themselves LOSE in both windows (-$41.70 PF 0.94;
                    -$69.96 PF 0.90). The W1 gain is a path effect, not the
                    trigger earning money.
- **Verdict**     : **REJECTED for promotion. Ships disabled**
                    (`VPLR_ENABLED = False`). Engineering is complete and
                    verified — 41 new tests, 326 total, live/sim parity
                    asserted, disabled mode reproduces the baseline trade list
                    byte-for-byte (281 trades, +$2757.57, identical).
- **Notes**       : docs/RESEARCH_NOTES.md §18;
                    docs/DESIGN_VP_LIQUIDITY_REACTION.md. Two defects recorded
                    there and NOT fixed: direction is resolved by pool
                    proximity rather than by raid recency/depth (which is why
                    the reference setup replays as a LONG), and the trigger's
                    stops are wide enough to take 146-2947 `LOT_FLOOR`
                    rejections against the baseline's 12-872 — so the arms are
                    partly different samples (§13.9).

### L-014 — AMENDED 2026-08-27 (results VOID, re-run required)

Tracing the operator's own 2026-08-27 Asia setup through the live detector found
two correctness bugs: no recency bound on the raid (a breach hours old still
counted, and scored deeper the older it got — 4.43xATR observed), and a fixed
POC->VAH->VAL first-match order with pools ranked by distance to the level, so
the equal highs that were actually swept lost to a nearer swing low. Both fixed:
`pierced[-1]`, `VPLR_RAID_MAX_AGE_BARS`, and candidate ranking by raid recency
then depth. The reference setup now reads BEARISH off `EQUAL_HIGHS 4625.14` from
04:30 instead of BULLISH until 06:15.

**Consequence: the two-window campaign recorded above measured the buggy
detector and is VOID.** The REJECTED verdict is withdrawn to OPEN pending a
re-run. `VPLR_ENABLED` stays False either way. Suite 328 tests, all passing.

### L-014 — CLOSED 2026-08-27 (re-run complete, REJECTED)

The campaign was re-run against the corrected detector. Same command shape,
same gate chain, same two disjoint windows; only the detector differs.
Raw runs: `logs/l014b_{w1,w2}_{baseline,asia,all,vponly}.json`.

| arm | W1 net $ | W1 PF | W1 sumR | W2 net $ | W2 PF | W2 sumR |
|---|---:|---:|---:|---:|---:|---:|
| baseline           | **+2757.57** | 1.35 | +53.10 | **-650.02** | 0.91 | -17.99 |
| VP ASIA_ONLY       |   +823.97 | 1.15 | +29.47 |  -781.09 | 0.89 | -29.54 |
| VP ALL_SESSIONS    |   -774.18 | 0.62 | -44.90 |  -771.37 | 0.92 | -26.95 |
| VP-only (triggers) |   -155.32 | 0.89 |  -5.40 |  +323.26 | 1.07 | +13.85 |

**Delta vs baseline — ASIA_ONLY -$1933.60 / -$131.07, ALL_SESSIONS -$3531.75 /
-$121.35. Negative in BOTH disjoint windows.** The fix made the rejection
stronger, not weaker: the void campaign's ASIA_ONLY delta flipped sign
(+252 / -133), so the old verdict rested on instability. This one meets §13.12's
same-sign standard in the direction of rejection.

The trigger's own per-trade expectancy is window-dependent, not absent-then-
present: -0.2145R / +0.0220R inside ASIA_ONLY, -0.0551R / +0.0539R standalone.
Two of three cells flip. It fails the §13.1 survival condition in 4 of 6 cells,
because realised average wins are 1.30-1.82R against a shipped TP1 of 2.0R —
10-26% of its trades exit on the timeout (§13.3 reproduced by a new trigger).

**The VP-only W2 +$973.28 delta is a trap and is not a promotion case.** The W2
baseline is itself losing, so replacing it with a near-breakeven book improves
the total without the replacement having an edge; the same arm is -$2912.89 in
W1. The delta's sign follows the baseline's sign.

**Attribution failed a fourth and fifth time.** W2 ALL_SESSIONS attributes
`VP_ASIA` at +$179.40 PF 1.11; the ASIA_ONLY arm that isolates it books
-$157.77 PF 0.89. The same arm attributes `POC` at +$1434.24 PF 1.68, while W1
POC is -$356.33 PF 0.50. After L-005 and L-011, treat this as settled: never
promote from a bucket.

- **Verdict** : **REJECTED for promotion. Ships disabled**
                (`VPLR_ENABLED = False`, `VPLR_SCOPE = "ASIA_ONLY"`), switchable
                via `--vplr` / `--vplr-scope`. No code change from this re-run —
                the arms are measurements, the shipped default was already off.
- **Caveats** : two, both recorded rather than resolved.
  1. *§13.9 sample drift.* `LOT_FLOOR` 12 -> 48 / 976 / 54 in W1 and
     872 -> 1782 / 1950 / 75 in W2. W1 ALL_SESSIONS at 976 against 12 is not an
     A/B comparison; read its delta as directional only. The verdict rests on
     ASIA_ONLY and VP-only, whose counts are close to the baseline.
  2. *Dollar deltas double-count.* On the 205 bars shared between the W1
     baseline and ASIA_ONLY, prices are identical and sum R is 39.587 vs
     39.583, yet dollars differ by $1153 — pure compounding, the arm sizing
     0.01 where the baseline sized 0.02. Every figure above is therefore also
     given in R, and the verdict holds in R. **Future campaigns should report
     sumR next to net $.**
- **Open lead**: the wide stop is the one thing not yet measured rather than
  measured-and-rejected. The stop sits beyond the raid extreme by construction,
  so it cannot be narrowed without destroying the setup's invalidation — but it
  means the trigger has never run on a risk unit large enough to admit its own
  signals. That would be a new ledger row, not a revision of this one.
- **Notes**   : docs/RESEARCH_NOTES.md §18.3-§18.4b. Engineering unchanged and
                re-confirmed: 328 tests pass, compileall clean, and the W1
                baseline reproduces `logs/bt_baseline.json` on every decision
                field (281 trades, +$2757.57, identical trade list; the only
                diff is the six telemetry columns added since that file was
                written).


## L-003 — CLOSED 2026-08-28: the simulator now models the live Guardian
- **Opened**      : the simulator modelled a static stop and target while the
                    live Guardian trails through three stages, arms an early
                    close at +1R, kills no-progress trades at 60 minutes and
                    banks a partial into an extended target. Consequence
                    recorded at the time: entry-side remedies were measurable,
                    exit-side ones were not.
- **Remedy**      : `scalper/tga_engines.py` (the Guardian's decision surface,
                    MT5-free, imported by BOTH processes — `assertIs`, not an
                    equivalence check), `scalper/exit_manager.py` (bar-driven
                    replay), `TGA_*` in `decision_params`, `--tga-exits`.
- **Result**      : the gap was large enough to have invalidated the exit-side
                    record. W2 flips sign, -$650.02 -> +$585.54. Early close is
                    the dominant live exit (119 / 290 occurrences) and had no
                    simulator representation at all. `NO_PROGRESS` explains
                    §13.10's 36 losses that closed without price reaching the
                    original stop.
- **Verdict**     : **CLOSED as a fidelity fix. Ships OFF**
                    (`TGA_EXITS_IN_SIM = False`) so every stored baseline stays
                    byte-comparable; flag-off reproduces the W1 baseline trade
                    list exactly. Flipping the default would force re-measuring
                    §§13.8-13.15 against a new reference and is a separate
                    decision, deliberately not taken here.
- **Caveats**     : the W2 dollar delta is confounded — `LOT_FLOOR` 872 -> 91
                    because the managed arm compounds to a larger risk unit, so
                    only 386 of 908/632 trades are shared. Read the +35.80
                    shared-bar R, not the dollars. The Guardian's own effect is
                    NOT fold-consistent (-$749 / +$1236), which does not matter:
                    it runs live regardless, so the simulator models it because
                    it happens.
- **Follow-on**   : 91 trades exit `TP -> EARLY_CLOSE` for about -90R across the
                    two windows — entries that would have reached 2R, closed
                    early for roughly -1R each. Best-evidenced exit defect in
                    the repository. Recorded, not acted on; needs its own row.
- **Notes**       : docs/RESEARCH_NOTES.md §19. 378 tests, compileall clean.


## L-015 — VP_LEG_CONFLUENCE: two completed H4 swing legs as a location filter
- **Opened**      : 2026-08-28, on an operator hypothesis — "if we use the VP
                    anchor points, we can find better entries. Quality over
                    quantity." P1 low -> P2 high -> P3 low; a profile per leg;
                    entries still come from `SWEEP_REJECTION` / `BOS_RETEST`.
- **Distinct from**: L-005 (rolling 42-bar profile, "no trade at the POC" —
                    inverted, per §13.11) and L-014 (profile as SIGNAL, 1044-2608
                    detections/window). Here the profile is a **veto only** and
                    the legs are COMPLETED pivot-to-pivot, so their levels are
                    static once formed.
- **Evidence**    : Grade C design only (§13.7) — MQL5 art. 20327, blog 772228,
                    CodeBase 76264. None publishes a win rate, PF or sample size.
                    Blog 772228 supplies the orientation (a zone overlapping the
                    POC is backed by volume; one inside an LVN is fragile),
                    which corrects L-005's direction but is not an edge.
- **Hypothesis**  : stated before the arms were run — strictness should raise
                    per-trade expectancy enough to pay for the trades given up.
- **Result**      : **half true in W1, inverted in W2.** In W1 PF rises
                    monotonically with strictness (1.35 -> 1.42 -> 1.49) and
                    avgR with it (+0.1890 -> +0.2055 -> +0.2352). In W2 the same
                    knob runs backwards (PF 0.91 -> 0.87 -> 0.87; avgR -0.0285
                    -> -0.0719 -> -0.0851). Sign flip across disjoint windows.

                    | arm | delta W1 | delta W2 |
                    |---|---:|---:|
                    | `LVN_VETO`        | -$799.84 | +$31.48 |
                    | `AT_LEVEL`        | -$574.95 | -$117.66 |
                    | `CONFLUENCE_ONLY` | -$835.65 | -$144.90 |

                    **The quality gain never paid for itself even where it was
                    real**: `CONFLUENCE_ONLY` lifted avgR 24% while cutting
                    trades 38%; net fell $835.65 and sumR fell 12.17.
- **Verdict**     : **REJECTED for promotion. Ships disabled**
                    (`LEG_CONF_ENABLED = False`, mode `LVN_VETO`), switchable
                    via `--leg-conf` / `--leg-conf-mode`. Kept as research at
                    the operator's instruction: the live bot is performing and
                    nothing here touches it.
- **Notes**       : two findings worth more than the verdict.

                    1. **A veto buys a lottery ticket on what comes next.** W1
                       `LVN_VETO` removed 18 trades worth -$272.86 (avgR
                       -0.2434) and the book still fell $799.84, because the 15
                       NEW trades it enabled ran WR 6.67%, -$605.11, avgR
                       -0.8200. Every arm produced new trades despite being a
                       pure veto — freeing a position slot and skipping a
                       cooldown changes which trades come later.
                    2. **The attribution trap fired a sixth time.** W1
                       `CONFLUENCE_ONLY`'s kept set reads +0.2767 avgR on
                       baseline numbers; the arm delivered +0.2352. The trades
                       it removed were profitable in aggregate (138 trades,
                       +$393.68, PF 1.10).

                    Coverage caps the effect: a leg pair exists on 49-73% (W1)
                    and 23-32% (W2) of candidates; elsewhere the filter is
                    NO_OPINION and admits. The leg floors were not loosened to
                    manufacture reach.
- **Docs**        : docs/DESIGN_VP_LEG_CONFLUENCE.md;
                    docs/RESEARCH_NOTES.md §20. 378 tests, compileall clean,
                    filter-off reproduces the baseline trade list exactly.

## L-016 — wick-ratio qualification on the sweep candle (`SIGNAL_FALSE`)
- **Opened**      : 2026-09-01, **pre-registered before any arm was run and
                    before the enabling telemetry has produced a single row.**
                    Source: `wiki/synthesis/correction-plan.md` §5 item 2,
                    which ranks this as the largest addressable *entry-side*
                    defect. **This entry authorises no code change.** It fixes
                    the prediction and the rejection criteria in advance so
                    that neither can be edited after seeing a result.
- **Failure mode**: `SIGNAL_FALSE` — "never went meaningfully onside; the read
                    was wrong."
- **Measured cost (three populations, deliberately not pooled)**:

                    | population | n | net $ | mean MFE |
                    |---|---:|---:|---:|
                    | sim, shipping config, W1 (280 trades) | 43 | -$2,209.84 | 0.17R |
                    | live, reconciled book (282 trades, mixed config) | 33 | -$690.91 | — |
                    | live, current era only (24 trades) | 7 | -$118.17 | — |

                    The sim W1 figure is the one the arm is measured against;
                    the other two are context. They are **not** the same book
                    and must never be summed. The current-era live slice is
                    n=7 and cannot support anything on its own.
- **Remedy**      : `REMEDY_KB.md` § `SIGNAL_FALSE` → **R1**. Require the
                    sweeping candle's wick beyond the level to be at least
                    `MIN_WICK_RATIO` of that candle's total range before
                    `SATriggerEngine._check_sweep_rejection` admits the setup.
                    `(high - max(open,close)) / range` for a buy-side sweep,
                    `(min(open,close) - low) / range` for a sell-side one.
                    **Sweep the threshold 35 / 45 / 55.** Do not adopt 45
                    because an article said so.
- **Evidence**    : **Grade C — design only** (CLAUDE.md §13.7). MQL5 art.
                    22140 ships a full detector around `MIN_WICK_RATIO = 45`
                    and publishes **no** win rate, profit factor, trade count
                    or date range — a screenshot and a tester GIF. The
                    threshold is a starting value, not a validated one. Per the
                    vault's §6 ladder this is a **hypothesis**, not a finding.
- **Hypothesis**  : *stated before the run.* The detector currently qualifies a
                    sweep on two conditions — any low in the last 4 bars
                    pierced the level, and the last bar closes back through it
                    — with **no requirement that the piercing candle rejected
                    anything**, and no freshness link between the piercing bar
                    and the rejection bar. A shallow two-pip tag is therefore
                    admitted on identical terms to a violent rejection. The
                    prediction: requiring a real wick removes a
                    disproportionate share of `SIGNAL_FALSE` trades, whose mean
                    MFE of 0.17R says they never go onside, and the trades it
                    keeps carry a higher avgR by enough to pay for the volume
                    given up.
- **Rejection criteria** : *also stated before the run — any one of these
                    rejects the remedy.*

                    1. **Sign flip across the two disjoint windows.** Same sign
                       in W1 and W2 or it is rejected. **This supersedes the
                       acceptance test written in `REMEDY_KB.md`,** which still
                       says "all three chronological folds": §13.12 retired
                       60/20/20 as a within-window stability test carrying *no*
                       cross-regime information — it fired in 4 of 14 windows
                       and those four split +$904 / +$1187 / -$172 / -$680.
                       Folds may be reported; they cannot promote.
                    2. **The EMA-band failure shape** (§13.8): removing losers
                       *and* profitable trades. A rise in win rate bought with
                       a fall in net and sumR is a rejection, not a result.
                    3. **`sumR` falls** even where net $ rises, or vice versa
                       without explanation (§13.15 — a dollar delta conflates
                       trade selection with compounding; on 205 shared bars
                       with identical prices and zero trigger changes, dollars
                       differed by $1153 while sumR differed by 0.004).
                    4. **The quality-without-payment shape** (L-015): avgR up,
                       net down. `CONFLUENCE_ONLY` lifted avgR 24% while
                       cutting trades 38% and lost $835.65.
- **Known risk**  : 276 of 280 W1 trades come from `SWEEP_REJECTION`. This
                    filter moves the **whole book**, not a corner of it. The
                    likely failure shape is a small win-rate gain bought with a
                    large trade-count loss.
- **Arm**         : W1 and W2, identical flags, filter default-off. Each window
                    is compared against its own baseline run with byte-identical
                    flags, never against a remembered figure, and the spread
                    floor is stated on every run (§13.12 — the archive cannot
                    supply one, and raising it 2.5 -> 5.0 cost $1264, 36% of the
                    headline).

                    W1: `py -3.14 -E backtest_scalper.py --from 2026-05-15T00:00:00 --to 2026-08-23T00:00:00 --pool 1000 --risk 0.03 --symbols XAUUSD --loss-limit 100.0 --incidents logs/sim_incidents.jsonl --out logs/bt_L016_w1_THR.json`

                    W2: `py -3.14 -E backtest_scalper.py --from 2025-08-01T00:00:00 --to 2026-05-14T00:00:00 --pool 1000 --risk 0.03 --symbols XAUUSD --loss-limit 100.0 --incidents logs/sim_incidents.jsonl --out logs/bt_L016_w2_THR.json`
- **Must report** : net $ **and** sumR side by side (§13.15); trade counts;
                    `LOT_FLOOR` and every rejection counter (§13.9 — an arm
                    that changes the admitted sample is measuring a different
                    book); the `SIGNAL_FALSE` count itself; **the number of
                    WINNERS that fail the wick test** — per the attribution
                    trap this is the question that decides it, and an answer of
                    "I did not count" means there is no finding; and the count
                    of **NEW** trades the arm takes that the baseline does not.
                    L-015: a pure veto still created 15 new trades running
                    6.67% WR and -$605.11, because vetoing frees a position
                    slot and skips a cooldown.
- **Result**      : **NOT RUN.** No data exists for this entry.
- **Verdict**     : **PROPOSED.**
- **Notes**       : three things this entry does not claim.

                    1. It does not claim the remedy will work. Grade C is a
                       hypothesis; §13.12 downgraded every REJECTED verdict in
                       this ledger precisely because each was decided on a few
                       hundred dollars from a distribution centred on zero, and
                       the same caveat will apply to this one's result.
                    2. It does not claim `SIGNAL_FALSE` is the biggest leak.
                       It is the biggest *addressable entry-side* leak. The
                       largest known defect remains **L-012 execution
                       slippage** — a 2.8% reward haircut against a 2.9% gross
                       margin — which no entry-side gate can touch, and which
                       is blocked behind L-003.
                    3. It does not depend on the Stage 0 telemetry. The wick
                       ratio is arithmetic on bars already in hand. The
                       telemetry unblocks a *different* question (confidence
                       tier x HTF agreement) that remains partly unmeasurable —
                       see the `htf_trend` gap recorded in
                       `wiki/synthesis/open-questions.md`.


## L-017 — failed-reclaim context and fresh setup after a Guardian reversal exit

**Current status: IMPLEMENTED AND VALIDATED as an operator-directed demo entry restriction; efficacy unverified. See the implementation mandate and outcome below. The original proposal fields are retained as history.**

- **Opened**      : 2026-09-02, operator report of broken 4324 support and M15-chart POC near 4328; broker-reconciled pair 494903819 (+$15.73) then 494906413 (-$27.18), net -$11.45. Full review: `APEX_SUPPORT_RETEST_ENHANCEMENT_RECOMMENDATIONS.md`; evidence: `docs/reviews/2026-09-02-support-retest-evidence/`.
- **Failure mode**: second entry reuses pre-exit sweep evidence after a bearish Guardian reversal exit. Existing application label is `SIGNAL_FALSE`; the preceding `WIN_TP1` label is wrong about exit type (broker says `TGA_EARLY_CLOSE`).
- **Remedy**      : candidate R7 in REMEDY_KB.md. A: persist reversal exit/setup identity, require a new closed M15 confirmation and fresh setup after invalidation. B: track support/resistance through break, retest and successful/failed reclaim, with closed M5 confirmation. C: add explicitly anchored intraday VP context to B, then measure incremental value. Symmetric for BUY/SELL. Defaults remain unchanged.
- **Evidence**    : broker-verified transaction facts plus exact two-case trigger/STB reproduction. Remedy efficacy remains an anecdotal hypothesis; POC 4328 remains operator-reported with anchors/bins UNKNOWN. No Grade A performance experiment was run for this entry.
- **Hypothesis**  : exit-aware setup invalidation reduces losing same-direction re-entries after reversal exits, beyond a timer alone. A stateful failed-reclaim rule might distinguish continuation from a transient rebound; defined VP context might add information beyond that rule. Test each contribution separately, including lost winners and new downstream trades.
- **Arm**         : A/B/C are design drafts, NOT runnable or fully preregistered arms. Record exact buffers, freshness, profile definition, evaluation dates, costs, config hash and commands before testing. A and its baseline must use the existing `--tga-exits` path; L-003 already implemented Guardian replay on 28 August, but it is off by default. Existing sessions, costs, cooldown, risk and position limits must match.
- **Result**      : diagnostic reconstruction only. Both historical signal entries/stops/targets and MEDIUM/BULLISH/BEARISH bias decisions matched. The second candidate's M15 confirmation closed at 08:15, before the first broker exit at 08:15:52; the proposed new-bar predicate rejects it. Holding every other trade fixed would avoid $27.18, but that is NOT a portfolio-performance result. At a 45% sweep-wick floor, the first winner (21.39%) fails and the second loser (48.61%) passes.
- **Verdict**     : **PROPOSED. No production change or promotion.**
- **Notes**       : do not re-enable L-005's H4 no-POC gate or claim a short at 4328 was proven profitable. Pre-register before running performance arms and respect the vault's current seven-part evidence gate: untouched OOS, prespecified power, pooled confidence intervals, null comparison, regime stability, measured costs and variant accounting. Same-sign folds alone cannot promote. This incident is development data. Correct exit labels/fill/cost snapshots before using incident classifications for outcome research.


### L-017 screenshot addendum — 2026-09-02

Operator supplied the chart and clarified that a long must wait for reclaim of prior broken support. The chart marks **4324.76**; the first actual buy 4324.048 was below it. The second signal's completed M15 close 4325.452 was above it, and the last completed M5 low 4324.770 was 0.010 above it. Therefore neither a bare price-above-level check nor an unbuffered M5 hold necessarily blocks the second trade. Require an explicitly defined, separately completed acceptance/retest and reset old long permission after a subsequent bearish invalidation. The Guardian exit follows the last M15 confirmation, so no fresh post-invalidation M15 evidence existed for the second buy. Profile settings and entry-time POC remain unverified by a 12:32 UTC screenshot. The wider reclaim rule may remove the first winner too; its benefit cannot be equated with the $27.18 single-trade exclusion counterfactual. Full addendum and original image are in the vault review; production settings unchanged.


### L-017 implementation mandate — 2026-09-02

The operator explicitly requested correction after the repeated loss: no long after broken support until displacement reclaims it and price subsequently returns to the resulting FVG; symmetric after broken resistance. This authorizes implementation and the corrected demo entry contract. It is not a claim of proven profitability or authorization to promote to real-money execution.

Frozen implementation definition, before validation: confirmed M15 swing pivots with 3 bars each side; confirmation must precede the break. Use the existing 150-bar decision window. Consider broken levels inside the candidate's original stop-to-TP1 corridor, prioritizing the nearest unresolved obstacle ahead of entry, otherwise the nearest broken level behind it. Freeze a 0.1 x preceding ATR(14) zone buffer at the break. Reclaim body >=0.8 x preceding ATR(14), >=60% of range, close in the outer 25% and beyond the zone; reclaim must touch/cross the level. It must form a same-direction three-candle M15 FVG of >=0.05 x preceding ATR, confirmed only when candle 3 closes. Only a later first M5 return, closing directionally inside the FVG and on the reclaimed side of the level, qualifies. Breached gaps, stale returns and entry quotes outside the gap are blocked. FVG lifetime 24 M15 bars. Any new same-direction trade after a close needs a reclaim formed after that close; broker history restores this barrier across restarts. All constants are initial engineering definitions, not fitted or performance-validated thresholds.

Apply as one shared post-detection gate to all SA entry types, without enabling any previously rejected VP/counter-HTF strategy. Preserve candidate SL/TP, risk, sessions, news, costs and trigger priority. The gate narrows eligible entries; a reclaim/FVG without an existing valid trigger does not itself create an order. Full live/backtest gate and final quote-check parity are required. Scope is the SA scalper that produced the reported trades, not Pool A or Guardian exit geometry. The first profitable bounce may also be rejected. Compare prior trades and synthetic valid/invalid paths; make no performance claim from the motivating losses.


### L-017 implementation outcome - 2026-09-02

Implemented shared `scalper/reclaim_fvg.py`, enabled by default, configuration era `2026-09-02-reclaim-fvg`. Every SA entry type now checks the declared displacement-reclaim/FVG-return sequence at relevant broken levels. Same-direction close barriers include Guardian exits and restore from broker history. Missing history, incomplete sequences, missing candles and quotes outside the qualified FVG block entry. Backtest uses the same evaluator and an M5 decision clock; the trigger frame remains M15. SELL is symmetric.

Validation: 425 tests pass (27 new), compileall passes, bounded full-pipeline smoke with Guardian exits completes. Yesterday's 494729996 loss (-$28.32) and both morning buys today (+$15.73, -$27.18) fail WAIT_LEVEL under broker-confirmed swing levels. This is implementation validation on development data, not demonstrated loss reduction. Full details and artifact paths: `docs/reviews/2026-09-02-reclaim-fvg-correction.md`. VP/profile arm C remains open; no automatic short or real-money promotion.


**Demo activation verified:** 2 September 13:11:41 UTC (18:11:41 Pakistan), gate ON, five close barriers restored, existing position adopted. Guardian remained running. See the correction report for process, broker and code-hash evidence.


## L-018 - M15 FVG entry toward a structural target

**Status: IMPLEMENTED AND VALIDATED, operator-directed DEMO contract; efficacy unverified.** Operator supplied a second screenshot, proposed entry around 4300 at a bullish M15 FVG with TP around 4324, and explicitly requested implementation and relaunch of scalper/TGA.

Shared `scalper/m15_fvg_entry.py` uses closed M15 displacement/FVG formation, current executable in-gap quote, existing 0.3 ATR FVG stop buffer, TP before the nearest broken M15 swing level, and unchanged >=2R/cost floors. Reuses L-017 displacement, gap-age and structural-break definitions. Full mitigation consumes a gap; another entry after a close needs a fresh departure and return. No fallback to the old M5 FVG when the M15 model is enabled. Both directions, all existing session/news/risk gates, live/backtest parity. TGA preserves structural TP using the durable order comment, with matching simulation behavior. Config era `2026-09-02-m15-fvg-target`.

Broker ticks: first revisit minimum ask 4301.357 at 04:20:37.795 UTC; second 4301.901 at 10:06:59.372. Exact 4300 was unavailable. Broker gap 4294.004-4302.272 was confirmed at 04:15. First structural target ~4322.103 and stop ~4289.939 admit hypothetical 4300 geometry but reject the actual higher ask for R<2; a nearer broken level limits the second revisit further. Both are outside current session windows; clarification requested, no session override added.

447 tests, compileall and bounded Guardian-enabled pipeline smoke pass. No new M15 FVG fills in that smoke; no efficacy, baseline or OOS claim. Full definition, artifacts and relaunch evidence: `docs/reviews/2026-09-02-m15-fvg-entry.md`. Preserve the chart example as a plan, not a fabricated executed trade.


**L-018 activation verified:** scalper 22532 and TGA 19324 running since 13:39 UTC, DEMO account flat, M15 FVG mode and L-017 ON. Existing session windows preserved. Source hashes/process/broker evidence saved in the L-018 evidence directory.


## L-019 - Priority D1/W1/MN1 liquidity-sweep CRT trigger

Operator explicitly requests an actual first-priority trigger for prior daily, weekly and monthly BSL/SSL sweeps, supported by the 2 September daily reversal. Broker confirms PDL 4322.777, low 4282.332, daily close 4385.502 back inside. Implemented `HTF_CRT_SWEEP` with shared closed-bar raid, displacement reclaim, resulting M15 FVG and later executable return. Range-based target capped by nearer broken structure, SL beyond raid, >=2R and cost floor. Native calendar frames, both directions, first watch outside sessions but normal entry gates, durable per-symbol setup identifiers, live/backtest parity and Guardian target preservation. See `docs/reviews/2026-09-03-htf-crt-trigger.md` for exact conditions and validation. Default enabled for operator-directed DEMO; no efficacy or real-money-promotion claim. Prior vault FS-4 negatives/inconclusive results are retained.


L-019 verification: 473 tests and compileall pass. Final 11:00–15:00 UTC broker-data replay completes with zero eligible entries; no profitability claim. Demo activation verified 3 September 05:11 UTC / 10:11 Pakistan: scalper PID 25460 and TGA PID 192, watchdog 7704. Broker flat; all three frame watches active and existing entry gates retained. Source hashes, process snapshots and activation logs are in the L-019 review evidence folder.

## L-020 - Measure CRT confluences before claiming improvement

Operator requested MQL5/vault research and stronger HTF external-raid confirmations. Frozen two additions before inspecting new outcomes: M15 opposing-swing break on the FVG-producing displacement, then fresh completed M5 directional FVG-return rejection. Implemented shared live/replay OBSERVE, MSS and MSS_RETEST modes, final strict confirmation-expiry check, measured XAUUSD total execution-cost replacement, candidate/trade labels and retry after transient watch failure. Default OBSERVE; no generic RSI/ADX/MA/POC threshold activated.

488 tests and compileall pass. Full June-August 2026 three-arm replay: all arms 139 whole-bot trades, zero eligible CRT candidates/trades, identical +$91.48 / +6.768R, PF1.040. Fold P&L +171.87/-276.15/+195.76; fixed-trade 0.75 USD/oz cost scenario -$108.92. No incremental inference or promotion possible. Preserve prior FS-4/FS-5 findings. Source report images inspected; vault IFVG missing-statistics note corrected by dated addition, without confidence promotion.

Activated OBSERVE on broker-verified DEMO at 05:53 UTC / 10:53 Pakistan, scalper4668/TGA26576/watchdog7704, flat before and after. Full conditions, results, limitations and artifacts: `docs/reviews/2026-09-03-crt-confluence-review.md`; frozen spec and MQL5 catalogue are adjacent. This is measurement infrastructure, not a validated profitable filter.
