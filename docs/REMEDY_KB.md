# Remedy knowledge base

Failure mode → candidate remedies → the evidence behind each.

Keyed on the exact labels `scalper/postmortem.py` emits. `analyze_incidents.py`
names a mode; this file says what is known about fixing it and how strong that
knowledge is. Nothing here is enabled by reading it — a remedy reaches the live
configuration only through a row in `REMEDY_LEDGER.md` that passed the
CLAUDE.md 13.5 fold test.

## How evidence is graded

CLAUDE.md 13.7 is the standing rule and it is applied strictly here.

| Grade | Meaning |
|---|---|
| **A — measured here** | Walk-forward on this repo's data, folds reported. The only grade that can promote a change. |
| **B — measured elsewhere** | An external source with a stated method, sample size and numbers. Argues a rule is worth testing; never that it works here. |
| **C — design only** | A source that shows a rule's *shape* — thresholds, defaults, structure — with no performance evidence. Most MQL5 articles are C. Use for what to build, never for whether it works. |
| **D — assertion** | Screenshots, marketplace copy, "consistently identifies", no numbers. Recorded so nobody re-finds it and mistakes it for evidence. |

A remedy with only C and D evidence is a hypothesis. That is fine — it is what
the backtest arm is for — but it must be labelled as one.

---

## `SIGNAL_FALSE`

*Never went meaningfully onside — the read was wrong.*

**Standing measurement (shipping config, XAUUSD 2026-05-15 → 08-23, 280 sim
trades):** 43 occurrences, **−$2,209.84**, mean MFE **0.17R**, mean MAE 1.33R.
The second-largest cost in the book and the cleanest defect in it: these trades
did essentially nothing right. In the reconciled live book (282 trades, mixed
config) the same mode is the single largest cost at −$690.91 over 33 trades.

### Why it happens here — read the detector before proposing anything

`SATriggerEngine._check_sweep_rejection` currently qualifies a sweep on two
conditions only:

1. any low in the last 4 bars pierced the level, and
2. the **last** bar closes back through it in the trigger's direction.

There is no requirement that the piercing candle actually *rejected* the level.
A shallow tag with a two-pip wick and a marginally-bullish close is admitted on
identical terms to a violent rejection wick. There is also no freshness
requirement linking the rejection bar to the piercing bar — the sweep may be
three bars stale.

### R1 — Wick-ratio qualification on the sweep candle

Require the sweeping candle's wick beyond the level to be at least 45% of that
candle's total range before the setup is admitted.

- **Evidence: C — design only.** [MQL5 art. 22140][22140] specifies
  `MIN_WICK_RATIO = 45`, with the ratio computed as
  `(high − max(open,close)) / range` for a buy-side sweep and
  `(min(open,close) − low) / range` for a sell-side one. The article ships a
  full detector around this rule but publishes **no** win rate, profit factor,
  trade count or date range — only a screenshot and a tester GIF. Its threshold
  is a starting value, not a validated one.
- **Why it is worth testing anyway:** it is the missing half of the word
  "rejection" in `SWEEP_REJECTION`, it is arithmetic on bars already in hand,
  and it discriminates precisely on the axis the failure mode names.
- **Acceptance test:** `SIGNAL_FALSE` count falls, and net P&L and profit
  factor hold or improve, in **all three** chronological folds. A version that
  strips `SIGNAL_FALSE` while also removing profitable trades has to clear the
  same bar as anything else — see the EMA band in CLAUDE.md 13.8, which failed
  exactly this way.
- **Known risk:** 276 of 280 trades come from `SWEEP_REJECTION`. Any filter on
  it moves the whole book, so a small win-rate gain bought with a large trade
  count loss is the likely failure shape. Sweep the threshold (35/45/55) rather
  than adopting 45 because an article said so.

### R2 — Dual-candle engulfing confirmation

Accept a sweep where the prior candle closed beyond the level and the current
candle closes back through it with an engulfing body, as an alternative
qualification path to R1.

- **Evidence: C — design only.** Also [art. 22140][22140]. Same absence of
  performance data.
- **Note:** overlaps R1. Measure them separately before measuring them
  together, or the interaction is uninterpretable.

### R3 — Sweep freshness (rejection bar must follow the pierce)

Require the rejecting close to occur within N bars of the pierce (N = 1 or 2),
rather than anywhere in a 4-bar window.

- **Evidence: D — none.** This is a defect observation from reading the
  detector, not a sourced rule. Listed because it is cheap to test and is a
  plausible contributor to the same mode.

### Already ruled out for this mode — do not re-propose

- **Moving-average / trend-band filtering of sweeps.**
  [MQL5 art. 18379][18379] proposes exactly this (`MALength` default 20,
  chart timeframe, keep bullish sweeps above the MA and bearish below) and
  offers **no** performance numbers — grade D, "consistently identifies... in
  historical backtests" with nothing to substantiate it. This repository has
  already implemented and measured that family as the H1 EMA(18) band: it turns
  +$2742 / PF 1.35 / positive-in-all-folds into +$230 / PF 1.06 with fold 3
  negative, and the inversion is worse still. It removes 197 trades whose PF
  was 1.52 and keeps 83 whose PF was 0.94. **Grade A evidence against.**
  Full table: `RESEARCH_NOTES.md` §11, and CLAUDE.md 13.8. A trend filter
  vetoes a fade by construction, and this book is 98.6% fades.

---

## `GAVE_BACK_WINNER`

*Reached ≥1R profit and closed at a loss — exit management.*

**Standing measurement (shipping config):** 29 occurrences, **−$1,571.92**,
mean MFE **1.43R**, mean MAE 1.27R. The MAE tells you the mechanism: these ran
to +1.43R on average and then travelled all the way back through entry to the
original stop. The simulator has no breakeven move and no partial — it holds
SL/TP1 only — so this is the unmitigated cost of round-tripping a winner.

### R4 — Partial close at 1R, remainder to TP1

Close a fraction (50% is the common default) at +1R and let the rest run.

- **Evidence: C — design only.** [MQL5 art. 19911][19911] specifies the
  mechanism: partial trigger at `partialCloseTriggerPoints` (default 100
  points), `partialClosePercent` default 50%, executed once per position via a
  latch flag. The article states its testing was functional — chart screenshots
  of stops moving — and presents **no** win rate, profit figures or drawdown.
- **Interaction warning:** TP1 is 2R (CLAUDE.md 13.1) precisely because the
  measured win rate cannot support a nearer target. Taking half off at 1R
  lowers the *effective* RRR of the book toward the survival boundary. The
  survival condition must be recomputed on the post-remedy win rate, not
  assumed to hold. This is the single most likely way to make things worse
  while appearing to improve the win rate.

### R5 — Breakeven stop after +1R

Move the stop to entry (optionally +offset) once the trade has reached 1R.

- **Evidence: C — design only** for the mechanism. [art. 19911][19911]:
  breakeven arms at `breakEvenTriggerPoints` (default 50 points), stop moves to
  entry + `breakEvenLockPoints` (default 0), latched so it fires once, and
  trailing is coordinated to start only after breakeven has triggered.
- **Evidence: B — measured elsewhere, and it is a warning.**
  [MQL5 art. 16991][16991] replayed a real account's deals across 9 symbols
  (AUDUSD, EURJPY, EURUSD, GBPUSD, NZDUSD, USDCAD, USDCHF, USDJPY, XAUUSD),
  222–526 deals per symbol, $3,000 deposit, 1:500, initial SL 100 points,
  trailing armed after 150 points with 50-point steps:

  | Variant | Result |
  |---|---|
  | Original trading, no trailing | **−$658.00** |
  | Simple trailing stop | **−$746.10** |
  | Parabolic SAR | +$541.80 |
  | VIDYA | −$283.30 |
  | Moving average | +$563.10 |
  | AMA | +$806.50 |
  | FRAMA | +$1,291.60 |
  | TEMA | +$1,355.10 |
  | DEMA | **+$1,397.10** |

  The author's conclusion is explicit: on an unprofitable book, *simple*
  trailing "results in an even greater loss". The naive mechanical remedy made
  the baseline **$88 worse**, while adaptive indicator-based trailing swung it
  by roughly $2,055. Mechanism matters more than the decision to trail.

- **Live-book caveat that does not apply to the simulator.** The Trade Guardian
  already runs a 4-stage trail on live positions — breakeven at peak ≥0.5R,
  1.0×ATR trail at ≥1.0R, 0.6×ATR at ≥2.0R. So R4/R5 are *unmeasured additions*
  in the simulator but *duplicates* of something already live. Before shipping
  either, measure what the Guardian is already doing: in the reconciled live
  book, **94 of 173 losses (54.3%) closed without price ever reaching the
  original stop**, median MAE 0.62–0.66R. Something closed those trades early.
  That is a Guardian-behaviour question, and it is open — see
  `REMEDY_LEDGER.md` L-003.

---

## `STOP_TOO_TIGHT`

*Stopped near the extreme, then the target printed anyway.*

**Standing measurement (shipping config):** 6 occurrences, −$359.27 — below the
n≥20 actionable floor, so it is recorded and not acted on. In the live book it
is larger (28 occurrences, −$210.13, mean MAE 0.81R), which is consistent with
the Guardian trailing live positions and the simulator not modelling it.

### R6 — MAE-percentile stop placement

Set the stop from the distribution of adverse excursion on *winning* trades
rather than from a fixed ATR multiple: place it beyond the Nth percentile of
winners' MAE so that the stop is wide enough to survive the noise that winners
routinely take.

- **Evidence: C — design/method only.** MAE/MFE analysis was formalised in
  John Sweeney's *Maximum Adverse Excursion* (1996) and the percentile approach
  is standard practice; the secondary sources located for it are trading-blog
  material without a reproducible study, and the one authoritative-looking page
  was behind bot verification and could not be read. Treat the method as sound
  and the specific percentile as unknown.
- **This repo already has the data to do it properly.** `sa_incidents.jsonl`
  carries `mae_r` per trade with an outcome label, so the winners' MAE
  distribution is a groupby, not a research project. That makes R6 a **grade-A
  measurement waiting to be run** rather than a borrowed rule — the strongest
  candidate in this file on evidence quality.
- **Overfitting warning:** fitting a stop to the MAE of trades you already know
  won is the textbook way to produce a beautiful backtest and a dead live book.
  The percentile must be chosen on fold 1 and *tested* on folds 2 and 3.

---

## `TIMEOUT_STALLED` / `TIMEOUT_CHOPPED` / `TIMEOUT_NEAR_MISS`

**Standing measurement (shipping config):** `TIMEOUT_CHOPPED` +$451.23 over 46,
`TIMEOUT_NEAR_MISS` +$482.49 over 11, `TIMEOUT_STALLED` −$12.58 over 3. **The
timeout profile is net positive under the current 24-bar budget.**

This is the measurement CLAUDE.md 13.3 demanded and it came back clean. The
timeout was a genuine defect when it was 24 M5 bars against a 1R target — 23%
of live trades exited on it — and it is not one now. `TIMEOUT_NEAR_MISS` at
+$482 over 11 trades with mean MFE 1.77R is mildly suggestive that a longer
budget might convert some of them, but n=11 is below the actionable floor.

**No remedy proposed. Re-check after any change to target geometry**, because
the timeout's value is a function of how far the target sits.

---

## `LOSS_ORDINARY`

*Went onside, failed, stopped.*

58 occurrences, −$3,261.36 — the largest single line in the book and **not a
defect**. A strategy with a 46% win rate and a 2R target is supposed to produce
losses of this shape. Chasing this number is chasing the cost of doing
business; the edge lives in the ratio, not in the absence of losers.

Recorded here only so that its size does not tempt anyone into treating it as
the top priority. It sorts first by dollars in every report and it should be
skipped every time.

---

## Sources

Grades reflect what each source *provides*, not its usefulness.

- [MQL5 art. 16991 — Post-factum trailing stop selection][16991] — **B**.
  Real replayed account, 9 symbols, 222–526 deals each, per-variant P&L.
- [MQL5 art. 19911 — Smart trade manager: breakeven, trailing, partial][19911]
  — **C**. Complete parameter defaults, explicitly no performance data.
- [MQL5 art. 22140 — Dynamic STF liquidity sweep indicator][22140] — **C**.
  Wick-ratio and engulfing qualification rules, no performance data.
- [MQL5 art. 18379 — Liquidity sweep with MA filter][18379] — **D**.
  Qualitative claims only; the family it proposes is already grade-A rejected
  here.
- John Sweeney, *Maximum Adverse Excursion: Analyzing Price Fluctuations for
  Trading Management* (Wiley, 1996) — origin of the MAE/MFE method this
  repository's forensics layer implements. Not consulted directly.
- Prior evidence already in this repository: `RESEARCH_NOTES.md` §1 (survival
  condition), §2 (timeouts as an exit profile), §10 (the lot floor), §11 (the
  EMA band).

[16991]: https://www.mql5.com/en/articles/16991
[19911]: https://www.mql5.com/en/articles/19911
[22140]: https://www.mql5.com/en/articles/22140
[18379]: https://www.mql5.com/en/articles/18379
