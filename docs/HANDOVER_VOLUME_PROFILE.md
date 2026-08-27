# Handover — Volume Profile / VAH / VAL / POC

**Subject:** the H4 volume-profile research programme in the APEX SA-V2 scalper
**Ledger rows:** L-005 (closed, REJECTED), L-006 (closed, REJECTED), L-007 (PARTIAL — open), L-009 (OPEN)
**Primary sources in repo:** `docs/RESEARCH_NOTES.md` §12, §13.1, §14; `docs/REMEDY_LEDGER.md`; `CLAUDE.md` §13.11
**Repo state at handover:** 279 tests pass (`py -3.14 -E -m unittest discover -s tests`), `compileall` clean, **every switch produced by this work ships OFF**
**Prepared:** 2026-08-27

---

## 0. One-paragraph summary

The operator proposed a standard volume-profile location rule — *sell from VAH,
buy from VAL, no trade at the POC* — after a live loss that was, correctly, a
POC entry. The rule was built twice: once as a **veto layer** (`vp_gate.py`) and
once as a **signal generator** (`va_fade_trigger.py`). The veto was measured
across eight walk-forward arms and **rejected**: every arm was worse than doing
nothing, damage was monotonic in the POC band width, and the two arms applying
the full buy-low/sell-high rule flipped sign across folds. Attribution then
showed **the rule is inverted for this book** — `AT_POC` is the single best
location bucket (PF 2.10) and `AT_VAL` the only negative one. The signal version
has never executed a trade, because an unrelated gate (`STB/opposes_short_term`)
forbids mean-reversion entries by construction; that claim is therefore
**untested, not refuted**. All modules remain in the tree, disabled, because the
profile and regime code are useful telemetry regardless of the gate's fate.

---

## 1. Why this was opened

A live XAUUSD loss the operator diagnosed by eye:

| field | value |
|---|---|
| time | 2026-08-25 16:45:25 UTC |
| side | BUY @ 4642.26 |
| stop | 4634.49 (hit) |
| P&L | **−$23.29** |
| H4 POC (42-bar) at that moment | **4641.33** |
| distance from POC | **0.93**, on a value area **248.43** wide |

The diagnosis was correct — it was a textbook POC entry, price at fair value
where neither side has an edge and the stop sits inside two-way business.

**The counter-evidence was in the same session, and was found before any code
shipped.** The two *winning* shorts that day were also at the POC:

| time (UTC) | side | entry | POC then | distance | P&L |
|---|---|---:|---:|---:|---:|
| 11:59:50 | SELL | 4642.94 | 4647.55 | 4.61 | **+$30.38** |
| 12:15:21 | SELL | 4638.78 | 4641.33 | 2.55 | **+$59.42** |
| 16:45:25 | BUY | 4642.26 | 4641.33 | 0.93 | **−$23.29** |

No POC band width rejects the loss and keeps both winners. A blanket veto over
those four trades nets **−$74.79**. n = 4 settles nothing in either direction —
which is precisely why it went through the fold test rather than straight to
production.

---

## 2. What shipped (module inventory)

All paths relative to `apex_ai/`.

| file | lines | role | ships |
|---|---:|---|---|
| `scalper/volume_profile.py` | 347 | POC / VAH / VAL arithmetic, location classifier | always available |
| `scalper/vp_gate.py` | 248 | the veto layer (4 modes) | `VP_GATE_ENABLED = False` |
| `scalper/regime_classifier.py` | 277 | deterministic trending/ranging read | always available |
| `scalper/hmm_backend.py` | 184 | optional `hmmlearn` regime backend | `VP_REGIME_BACKEND = "DETERMINISTIC"` |
| `scalper/va_fade_trigger.py` | 283 | `VALUE_AREA_FADE` as a first-class **trigger** | `VA_FADE_ENABLED = False` |
| `scalper/regime_direction_gate.py` | — | L-006 spin-off: don't fade a classified trend | `RD_GATE_ENABLED = False` |
| `tests/test_volume_profile.py` | 405 | 41 tests | — |
| `tests/test_regime_direction_gate.py` | 126 | 14 tests | — |

Wiring (mirrored into both decision paths in the same change, per invariant #2):

- Live: [`scalper_agent.py:545`](apex_ai/scalper_agent.py:545) builds the profile once per scan; [`scalper_agent.py:636`](apex_ai/scalper_agent.py:636) is the gate call.
- Simulator: [`backtest_scalper.py:723`](apex_ai/backtest_scalper.py:723) / [`backtest_scalper.py:789`](apex_ai/backtest_scalper.py:789).
- Constants: [`scalper/decision_params.py:158-232`](apex_ai/scalper/decision_params.py:158) — 20 `VP_*` values, imported by both processes, never restated.
- Parity: `tests/test_live_sim_parity.py:53-80` asserts **identity** of every VP constant across both processes, that the gate appears in both decision paths, and that the profile frame uses `_closed_tf`.

CLI (identical on both binaries): `--vp-gate` / `--no-vp-gate`, `--vp-gate-mode`, `--vp-poc-band`, `--va-fade`.

---

## 3. The arithmetic

### 3.1 Profile construction — `volume_profile.py`

A volume histogram over **price**, not time, then three levels read off it:

- **POC** — the bin holding the most volume.
- **VAH / VAL** — edges of the contiguous band around the POC holding `va_pct` (70%) of window volume.

The POC scan and the value-area expansion are transcribed from **MQL5 art.
23169** (*Automatic Session Volume Profile Builder*), read in full. The
expansion walks outward from the POC bin, comparing the bin above with the bin
below and absorbing whichever holds more, until the running total reaches the
threshold. Its tie-break, `vol_above >= vol_below`, resolves upward — **that
asymmetry is reproduced deliberately, not "corrected"**, because a value area is
only comparable against other value areas computed the same way. Exhausted sides
use a sentinel so expansion continues into the side that still has bins.

**One deliberate divergence from the source, and it matters.** Art. 23169 walks
raw ticks and attributes each tick's volume to a single bin. This module is
handed OHLCV bars — that is what the repo fetches and what the simulator can
replay deterministically — so a bar's volume is spread across every bin its
`[low, high]` touches, weighted by coverage. This is the standard bar-based
approximation, and it is an approximation: a bar that opened and closed at its
low after one spike to its high contributes as though trade were uniform across
the span. On M15 and finer the error is small against a sane bin width; on daily
bars it would not be. **The result is not identical to a tick-built profile. It
is identical between the live agent and the simulator**, which is the property
invariant #2 actually requires.

Guards: max 10 000 bins (art. 23169's own ceiling, so a mis-specified bin size
fails loudly rather than allocating an enormous array); a floor below which the
POC and the value-area edges collapse onto the same price.

### 3.2 Location labels

`classify_location()` returns one of seven auction-state labels (not geometry
names), and these are what the rules branch on:

```
ABOVE_VAH    premium, outside value
AT_VAH       upper edge   — the "short zone"
UPPER_VALUE  inside value, above the POC band
AT_POC       fair value   — the "no-trade zone"
LOWER_VALUE  inside value, below the POC band
AT_VAL       lower edge   — the "long zone"
BELOW_VAL    discount, outside value
UNKNOWN
```

Two tolerances, both expressed as a **fraction of value-area width** so they
travel across instruments: `VP_POC_BAND_FRAC` (half-width of the POC dead zone)
and `VP_EDGE_TOLERANCE_FRAC` (how near VAH/VAL counts as "at").

### 3.3 Why a regime classifier had to exist — `regime_classifier.py`

A value area is a mean-reversion reference **only while the market is in
balance**. Measured on this repo's own data, same instrument, same moment
(2026-08-25):

| profile window | POC | spot | comment |
|---|---:|---:|---|
| 30 H4 bars | ~4641 | 4642 | tracks the market |
| **42 H4 bars** | **4641.33** | 4642 | the shipped default |
| 60 H4 bars | **4394.62** | 4642 | **247 points adrift** — window straddled the rally, histogram went bimodal |

That is why `VP_PROFILE_BARS = 42` (7 days of H4) and why the gate has a
regime-conditional mode. Fading a value area computed across a trend leg is not
a trade, it is an artefact.

The classifier calls **RANGING only when two independent statistics agree** — a
deliberately conservative bar, because trading a fade inside a trend costs far
more than skipping a fade inside a range:

1. **Lag-1 return autocorrelation**, transcribed from MQL5 art. 17737 with its
   published defaults: lookback 100, smoothing 10, trend threshold 0.2,
   volatility threshold 1.5.
2. **Kaufman efficiency ratio** — `|close[n] − close[0]| / Σ|close[i] − close[i−1]|`.
   No fitted parameters, and it cannot be gamed by a series that oscillates with
   persistent sign.

They can disagree hard: a smooth oscillation `100 + 2·sin(x/5)` reports
ρ = **+0.971** (moves persist inside each half-cycle) and ER = **0.051** (goes
nowhere). `combine` decides who wins. `VP_REGIME_ER_THRESHOLD = 0.35` **has no
published source** — Kaufman's ratio is standard but the cut is ours, and it is
the parameter most likely to need moving.

The HMM backend (art. 17917) is seeded, restart-best, and mapped to regimes
through fitted emission parameters so label switching cannot flip meaning
between fits. It costs **~142 ms against ~0.9 ms** for the deterministic path on
400 H1 bars of XAUUSD, where the two agreed on the answer, and needs `hmmlearn`,
which is not in `requirements.txt`. It is off for those reasons.

---

## 4. The gate — `vp_gate.py`

Pure function of closed frames in, decision out. **Fetching the frames and
guaranteeing both end on a completed bar belongs to the caller**, so the live
agent and the simulator feed it from their own data paths and run identical
arithmetic.

Four modes:

| mode | behaviour |
|---|---|
| `POC_ONLY` | enforce only the POC dead zone, in every regime. Narrowest expression of the diagnosis, cheapest arm. |
| `RANGING_ONLY` | full location rules, but only when the classifier says RANGING; abstain otherwise. **The shipped default value.** |
| `ALWAYS` | full location rules in every regime. |
| `POC_REQUIRE` | the **inverse**: admit only POC entries, veto everything else. Added after attribution (§6) found `AT_POC` to be the best bucket. Selected from the same window it would be measured on, so a fold result there is contaminated by construction and can only ever disconfirm, never promote. |

**Abstention is a distinct outcome from allow-on-merit.** `VPGateResult.abstained`
is set when the gate declined to form an opinion — no usable profile, wrong
regime for the mode, missing regime frame, unknown location, unknown direction —
so the reject log can tell "passed" from "not consulted". A gate that cannot see
the regime has no business blocking a trade the rest of the stack approved.

`LONG_LOCATIONS` generalises "buy from VAL" to `{AT_VAL, BELOW_VAL, LOWER_VALUE}`
— the discount half — so a trigger firing two bins above VAL is not discarded on
a technicality while one exactly at VAL is kept. `SHORT_LOCATIONS` mirrors it.

### Placement in the live chain

```
DEDUP → step1_liquidity → step2_trigger  ← H4 profile built here if any consumer needs it
      → EMA band (off) → PDR gate (off) → RD gate (off)
      → VP GATE  ←── here
      → short-term-bias gate → consultation → sizing → execute
```

Placed after the cheap directional vetoes and before the short-term-bias gate:
it costs two OHLCV fetches and a histogram build — more than the EMA check, far
less than the consultation.

**Both frames are fetched closed-only.** A profile including the forming H4 bar
would repaint: that bar's high and low are still growing, so the VAH that
permitted a short can move before the bar closes and the decision could never be
reproduced.

---

## 5. The evidence — walk-forward, eight arms

`XAUUSD 2026-05-15 → 2026-08-23`, pool **$1000 @ 3%**, spread floor 2.5 pips
(per-bar broker spread where non-zero), max 2 open positions, cooldown on,
session windows enforced, news blackout enforced, `warmup_lead_days = 45`.
Every arm identical but for the gate. Fold analysis via `analyze_walkforward.py`,
chronological 60/20/20.

| arm | mode | POC band | trades | win rate | net $ | folds |
|---|---|---:|---:|---:|---:|---|
| **baseline** | off | — | 280 | 46.07% | **+2742.35** | CONSISTENT + + + |
| poc02 | POC_ONLY | 0.02 | 279 | 45.52% | +2231.08 | CONSISTENT + + + |
| poc05 | POC_ONLY | 0.05 | 265 | 44.53% | +1998.36 | — |
| poc10 | POC_ONLY | 0.10 | 265 | 43.77% | +1414.78 | CONSISTENT + + + |
| rng10 | RANGING_ONLY | 0.10 | 231 | 38.96% | +121.46 | **UNSTABLE − + −** |
| alw10 | ALWAYS | 0.10 | 206 | 35.92% | **−149.53** | **UNSTABLE − + −** |
| req10 | POC_REQUIRE | 0.10 | 54 | 44.44% | +324.70 | CONSISTENT + + + |
| req20 | POC_REQUIRE | 0.20 | 98 | 44.90% | +311.51 | CONSISTENT + + + |

Three things to read off this:

1. **Damage is monotonic in band width** — 0.02 costs $511, 0.05 costs $744,
   0.10 costs $1328 — and win rate falls with it, 46.07% → 43.77%. The gate
   removes *winners* preferentially. Same signature the H1 EMA band showed
   (RESEARCH_NOTES §11).
2. **The two arms applying the operator's full rule fail the standing rejection
   criterion.** `RANGING_ONLY` and `ALWAYS` flip sign across folds. `ALWAYS` also
   fails the survival condition: a 35.92% win rate needs RRR > 1.78 and the
   realized payoff does not reach it.
3. **The inverse does not rescue it.** `POC_REQUIRE` passes all three folds and
   earns +$324.70 on 54 trades against the baseline's +$2742.35 on 280. A better
   average per trade cannot pay for discarding 80% of the book.

Raw artifacts, all present on disk: `apex_ai/logs/wf_baseline.json`,
`wf_poc02.json`, `wf_poc05.json`, `wf_poc10.json`, `wf_rng10.json`,
`wf_alw10.json`, `wf_req10.json`, `wf_req20.json`. Each carries its full config
block, so an arm can be verified without re-running it.

---

## 6. Attribution — the rule is inverted for this book

All 280 baseline trades, labelled with the H4 value-area location at entry:

| location | n | win% | net $ | avg $ | PF |
|---|---:|---:|---:|---:|---:|
| **AT_POC** | 42 | 47.6% | **+1038.04** | **+24.72** | **2.10** |
| LOWER_VALUE | 52 | 48.1% | +653.71 | +12.57 | 1.53 |
| BELOW_VAL | 52 | 44.2% | +421.75 | +8.11 | 1.34 |
| AT_VAH | 19 | 47.4% | +291.54 | +15.34 | 1.57 |
| ABOVE_VAH | 47 | 42.6% | +280.55 | +5.97 | 1.15 |
| UPPER_VALUE | 42 | 47.6% | +107.01 | +2.55 | 1.08 |
| **AT_VAL** | 26 | 46.2% | **−50.25** | −1.93 | **0.93** |

`AT_POC` — the location the rule forbids — is the best bucket by both net and
profit factor. `AT_VAL` — "buy from VAL", one of the two rules specified — is the
only negative bucket in the table.

**A plausible mechanism, explicitly not established:** the POC is where resting
liquidity is densest, so a sweep rejection there has the most order flow to
reject against for a given stop distance. **494 of 524 detected triggers are
`SWEEP_REJECTION`** — this book fades liquidity grabs, and a location rule built
for a continuation premise vetoes a fade by construction. That is the same
structural reason the EMA band (§13.8) and the regime-direction gate (L-006)
were rejected. **Three trend/location filters, three rejections, one cause.**

⚠️ **The attribution script was ad-hoc and is not in the tree.** The 280-trade
location labelling was produced in a scratch script during the campaign. The
inputs survive (`wf_baseline.json` carries every trade), but reproducing this
table means re-writing ~30 lines. If a senior engineer intends to build on the
attribution, productionising that labeller is the first task.

---

## 7. Verdicts and ledger rows

### L-005 — the location gate → **REJECTED**
Ships disabled (`VP_GATE_ENABLED = False`). No configuration beat doing nothing.
Stays implemented and switchable so it can be re-measured against a
**continuation-heavy trigger mix**, which this book is not. Evidence: Grade C for
the design (art. 23169 supplies the algorithm and publishes no performance data
at all), Grade A against, from our own walk-forward.

### L-006 — longs into an H1 downtrend → **REJECTED** (closed 2026-08-26)
A lead from regime attribution of the same 280-trade book:

| regime / direction | n | win% | net $ | avg $ | PF |
|---|---:|---:|---:|---:|---:|
| RANGING / BEARISH | 107 | 48.6% | +1532.00 | +14.32 | 1.60 |
| RANGING / BULLISH | 112 | 42.9% | +689.78 | +6.16 | 1.20 |
| **TRENDING_DOWN / BULLISH** | 8 | 37.5% | **−214.91** | **−26.86** | **0.35** |

Also worth knowing from that table: **78% of this book already happens in
RANGING** (219 of 280). The scalper is a range trader that did not know it was
one.

Built as `scalper/regime_direction_gate.py` in two modes and run where the
hypothesis had never been. In-sample the two modes disagree in **sign**
(LONGS_ONLY +$294, SYMMETRIC −$274) — that is fitting a direction to n = 8. Out
of sample on 2025-08-01 → 2026-05-14, LONGS_ONLY is +$32.29 across 622 trades
($0.05/trade); SYMMETRIC is +$268.62 but flips sign across folds. On USOIL the
gate never fires (delta exactly $0.00 on 15 trades); XAGUSD produced no trades.
`RD_GATE_ENABLED = False`.

### L-007 — simulator look-ahead on slow frames → **PARTIAL, still open**
Found while mirroring the VP gate. `_closed(df, now, bars)` compares a bar's
**opening** stamp to `now`, so on H4 at 18:00 it returns the bar stamped 16:00 —
which does not close until 20:00. Live has no equivalent hole:
`copy_rates_from_pos(..., 1, n)` starts at the last fully closed bar on whatever
frame is asked for.

Fixed with `backtest_scalper._closed_tf(df, now, bars, tf_minutes)`
([`backtest_scalper.py:344`](apex_ai/backtest_scalper.py:344)), which requires
`time + tf_minutes <= now`. **The new VP gate uses it on both frames. The
pre-existing H1/H4 reads feeding the short-term-bias and consultation gates were
deliberately left on `_closed`** — re-timing those would change what every
existing number in RESEARCH_NOTES §§9–11 was measured against, inside a change
that was adding a gate.

> **Open item for the incoming engineer.** Until that arm is run, treat all
> H1/H4-derived gate behaviour in the simulator as **marginally optimistic**.
> The arm is well-defined: re-run the baseline with `_closed_tf` everywhere and
> compare.

### L-009 — `VALUE_AREA_FADE` as a signal → **OPEN, never tested**
§12 rejected the rule as a **filter**. That constrains vetoes, not signals, so
the rule as the operator actually specified it was built as a first-class
trigger: `scalper/va_fade_trigger.py` + `SATriggerEngine._check_value_area_fade`
([`trigger_engine.py:243`](apex_ai/scalper/trigger_engine.py:243)), last in the
first-match order so it cannot pre-empt a liquidity-anchored setup on the same
bar.

Confluence, both required: a rejection wick ≥ 33% of the bar range on the correct
side, and RSI(14) beyond 70/30 (art. 17781 pairs exactly those thresholds with a
ranging classification). Stop sits beyond the range marker — the swing bounding
the balance area, the level an operator draws by hand — plus 5% of value-area
width. Targets are the shared 2R/3R, **not the POC**: the intuitive fade target
sits inside 1R on a tight value area and would recreate the sub-2R geometry that
§13.1 exists to prevent. `RangeLevels` derives `range_high`, `range_low` and
`major_liquidity_above/_below` from fractal swing structure — **no price is
hard-coded**, so the detector keeps working when the range moves.

**It executes zero trades:**

| run | window | detections | trades | net $ |
|---|---|---:|---:|---:|
| isolated | 2026-05-15..08-23 | 3 | **0** | 0.00 |
| isolated | 2025-08-01..2026-05-14 | 10 | **0** | 0.00 |
| in the full mix | 2025-08-01..2026-05-14 | 2 | 0 | — |

Two causes:

1. **A gate forbids fades by construction.** `STB/opposes_short_term` rejected
   **3 of 3** and **9 of 10** (the tenth was `THIN_LIQ`). That rule's own
   justification in `decision_params` reads: *"a continuation setup fighting the
   immediate move contradicts itself."* That is written for continuation setups
   and is **false for a fade** — price pushing up into the VAH *is* a bullish
   short-term read, and selling it is the entire trade.
   `STB_CONTINUATION_TRIGGERS` already exists to relax two other rules for
   continuation triggers; the mirror image for fade triggers does not exist.
2. **The conjunction almost never occurs.** ~13 detections in 13 months before
   any gate. Requiring M15 RSI(14) beyond 70/30 at the exact bar rejecting a
   *weekly* H4 value-area edge is a rare coincidence. Even with cause 1 removed,
   13 signals in 13 months is not a strategy.

> **Do not report §12's rejection as covering this.** §12 rejected the rule as a
> filter; that is a different claim. The signal version is neither confirmed nor
> refuted — it has never been allowed to trade, so the measurement so far is of
> the plumbing, not the idea.

Two changes would make it measurable, in this order: (a) a fade-trigger
relaxation for the STB "opposes short-term" rule, mirroring
`STB_CONTINUATION_TRIGGERS`, **mirrored into the simulator in the same change**;
(b) loosen the confluence — RSI toward 60/40, or accept either the wick or the
RSI rather than both — until detections reach a testable sample, then tighten
back. Both are strategy changes; neither was made, because a diagnosis does not
authorise one.

---

## 8. Two traps this campaign walked into — read before proposing a successor

**1. A correct diagnosis does not imply a correct remedy.** The live loss really
was a POC entry, 0.93 from the POC. The same session's two winners were also at
the POC. One trade cannot distinguish a defect from a coincidence.

**2. Attribution is not a filter forecast.** The 42-trade `AT_POC` bucket in §6
and the 54-trade `POC_REQUIRE` book in §5 are **not the same trades**. Vetoing a
trade frees a position slot and skips a cooldown, so gating changes which trades
come *later*. Attribution describes the book that was taken; it does not predict
the book a filter would produce. This recurred in the PDH/PDL campaign (L-011),
where a bucket promised +$463 and the arm lost $714, and where two arms produced
*more* trades than the control while vetoing hundreds of signals. **Never promote
from a bucket. Only a full re-run measures a gate.**

---

## 9. Standing caveat on all of the above — L-008

Everything in §5 was decided on **one 100-day window** (2026-05-15 → 08-23) at a
**2.5-pip spread floor**. L-008 subsequently ran 17 disjoint 6-month windows over
2018-03 → 2026-08 and found the book is **centred on zero**: 4694 trades, 40.48%
pooled win rate, net +$2282, pooled PF 1.0289, 7 windows positive / 7 negative,
median window +$16.30.

Consequences that bind on this research specifically:

- **The +$2742.35 baseline is a tail, not an expected return.** It is one draw
  from a near-zero-mean distribution, as is the −$657 window on the other side.
- **Every REJECTED verdict in §§9–13 of RESEARCH_NOTES, L-005 included, is
  downgraded.** Each was decided by a few hundred dollars of net P&L on that one
  draw. They establish that a gate did not rescue one window; they do **not**
  establish that the gate is harmful. All stay off — this is a caveat on the
  evidence, not a promotion.
- **The 60/20/20 fold test does less than it claims.** Within one window it is a
  stability test, not a cross-regime one. It fired in only 4 of 14 windows in
  L-008, and those four split +$904 / +$1187 / −$172 / −$680. Promotion requires
  folding across **disjoint multi-month windows**.
- Raising the spread floor 2.5 → 5.0 costs **$1264** of the $2742 headline. State
  the spread floor on any multi-year run; the broker archive cannot supply one
  (the spread field is zeroed 2020-03 → 2025-03).

---

## 10. Reproduction

Verification gate first — this must be clean before anything else:

```bash
cd apex_ai && py -3.14 -E -m unittest discover -s tests && py -3.14 -E -m compileall -q .
```

Use `py -3.14` with `-E`. Bare `python` resolves to an agent venv without pandas
or MetaTrader5, and some harnesses export a `PYTHONPATH` that loads cp311 numpy
binaries into 3.14. Both faults look like repo breakage and are not.

Re-run the baseline arm:

```bash
py -3.14 -E backtest_scalper.py --from "2026-05-15T00:00:00" --to "2026-08-23T00:00:00" --pool 1000 --risk 0.03 --symbols XAUUSD --spread-pips 2.5 --out logs/wf_baseline.json
```

Re-run a gated arm (substitute mode and band):

```bash
py -3.14 -E backtest_scalper.py --from "2026-05-15T00:00:00" --to "2026-08-23T00:00:00" --pool 1000 --risk 0.03 --symbols XAUUSD --spread-pips 2.5 --vp-gate --vp-gate-mode POC_ONLY --vp-poc-band 0.10 --out logs/wf_poc10.json
```

Fold analysis:

```bash
py -3.14 -E analyze_walkforward.py
```

MT5 must be running and logged in for the rates feed. Note `analyze_walkforward.py`
was fixed during this campaign: it read `cfg['spread_pips']`, which the simulator
stopped writing when per-bar spread landed, so every result file written after
that raised `KeyError` and fold analysis could not run at all. It now prefers
`spread_pips_observed`.

---

## 11. Sources, and how they may be used

Under the repo's evidence rule (§13.7), MQL5 articles are **design** evidence —
rule shapes and parameter defaults — and **never performance** evidence.

| source | supplied | performance data |
|---|---|---|
| MQL5 art. 23169 — *Automatic Session Volume Profile Builder* | histogram, POC scan, value-area expansion with the upward tie-break, 70% default, 10 000-bin guard | **none.** States outright that volume profiles "do not predict where price will move in the following session." |
| MQL5 art. 17737 — *Custom Market Regime Detection, Part 1* | autocorrelation formula, lookback 100, smoothing 10, trend 0.2, volatility 1.5 | **none** |
| MQL5 art. 17781 — Part 2 (the EA) | RSI(14) 30/70 mean reversion in ranges; the VA_FADE confluence thresholds | "approximately 20% equity growth" pre-optimisation with "significant drawdowns", nothing numeric after; warns its own optimisation "introduces a risk of overfitting" |
| MQL5 art. 17917 — *Hidden Markov Models in ML-Based Trading* | 3–5 hidden states, rolling std features, 2000–2024 training | **none** — equity images only. Records HMMs are "prone to overfitting on non-stationary time series", "can get stuck in local optima", and its variational variant "required several training restarts" |

All four were read in full.

---

## 12. Open items, in the order I would take them

1. **L-007 legacy frames.** Re-run the baseline with `_closed_tf` everywhere and
   compare. Until this is done, every H1/H4-derived gate number in the repo is
   marginally optimistic — including §5's. Cheapest task here, and it prices the
   uncertainty on everything else.
2. **Productionise the location attribution labeller** (§6) if any further
   location work is planned. It is currently unreproducible.
3. **L-009, if the operator still wants the value-area entry model tested.** The
   STB relaxation, then the confluence loosening. Note that even with both, ~13
   signals in 13 months means the detection rate is the binding constraint, not
   the gate.
4. **Do not re-propose the location veto in its original form.** It is measured,
   inverted, and the mechanism is understood: this book is ~99% sweep-fade, and a
   location or trend rule vetoes a fade by construction. Three such filters have
   now been rejected for the same reason. If the trigger mix ever becomes
   continuation-heavy, the code is still there and switchable.
5. **Read L-012 before spending effort on any entry-side gate.** Gross gains
   $81,265 against gross losses $78,982 is a 2.9% margin, and applying only the
   measured reward-leg slippage haircut (2.000R → 1.852R) turns the nine-year
   book to **−$3,731 / PF 0.9528**. No entry gate measured so far is large enough
   to matter against that. The remaining value is probably not here.

---

## Appendix — parameter reference

All in [`scalper/decision_params.py`](apex_ai/scalper/decision_params.py:158). Both processes import them; never restate one.

| constant | value | note |
|---|---|---|
| `VP_PROFILE_TF` | H4 | operator's specification |
| `VP_PROFILE_FETCH_BARS` | 260 | surplus so the frame can also seed the regime read without a second fetch |
| `VP_PROFILE_BARS` | 42 | 7 days of H4. See the 30/42/60 measurement in §3.3 — a profile is a statement about **one** balance area |
| `VP_TARGET_BINS` | 60 | a row count, not art. 23169's fixed 10-point bin, which does not travel across instruments. ~6 points/row on gold |
| `VP_VALUE_AREA_PCT` | 0.70 | universal convention and the article's default |
| `VP_POC_BAND_FRAC` | 0.10 | ±24.8 on a 248-wide area, i.e. 20% of the value area is no-trade. **Must be swept, not guessed** — see §5 |
| `VP_EDGE_TOLERANCE_FRAC` | 0.10 | |
| `VP_GATE_MODE` | `RANGING_ONLY` | |
| `VP_GATE_ENABLED` | **False** | L-005 |
| `VP_REGIME_TF` / `_BARS` | H1 / 400 | slow enough not to flip on one M15 impulse |
| `VP_REGIME_LOOKBACK` | 100 | art. 17737 |
| `VP_REGIME_SMOOTHING` | 10 | art. 17737 |
| `VP_REGIME_TREND_THRESHOLD` | 0.2 | art. 17737 |
| `VP_REGIME_VOL_THRESHOLD` | 1.5 | art. 17737 |
| `VP_REGIME_ER_THRESHOLD` | 0.35 | **ours, unsourced** — most likely to need moving |
| `VP_REGIME_BACKEND` | `DETERMINISTIC` | HMM is ~142 ms vs ~0.9 ms and needs an uninstalled dependency |
| `RD_GATE_ENABLED` | **False** | L-006 |
| `VA_FADE_RSI_PERIOD` / `_OVERBOUGHT` / `_OVERSOLD` | 14 / 70 / 30 | art. 17781 |
| `VA_FADE_MIN_WICK_FRAC` | 0.33 | |
| `VA_FADE_SL_BUFFER_FRAC` | 0.05 | beyond the range marker, as a fraction of VA width |
| `VA_FADE_ENABLED` | **False** | L-009 |
