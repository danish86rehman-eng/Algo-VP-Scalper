# Research Notes — evidence base for strategy decisions

Every entry records the source read in full, what it actually says (with
numbers), and what was changed here as a result. Sources that turned out to be
unusable as evidence are recorded too, so the same ground is not re-covered.

---

## 1. The survival condition — why 1R targets could never work

**Source:** [Building a Trading System (Part 2): The Science of Position
Sizing](https://www.mql5.com/en/articles/18991) — read in full.

The article states the break-even condition for any system directly:

```
RRR  >  (1 - win_rate) / win_rate
```

It also tabulates expected consecutive-loss streaks from 100 Monte Carlo runs
of 500 trades each:

| Win rate | Min streak | Median | Max |
|---|---:|---:|---:|
| 30% | 10 | 15 | 28 |
| 50% | 5 | 8 | 15 |
| 65% | 4 | 5 | 11 |
| 76% | 3 | 4 | 8 |
| 83% | 2 | 3 | 6 |

Recommended risk fraction is 1–2% of current balance for every profile tested;
going beyond 2% is only justified for "a robust and proven system".

**Applied here.** The live scalper sample (287 trades, 2026-05-06→15) was 37
wins against 173 losses and 67 timeouts — a 17.6% win rate on decided trades.
Substituting into the condition gives a required RRR above **4.6**. TP1 was set
at **1.0R**. The configuration was arithmetically incapable of profit
regardless of signal quality. TP1 moved to 2.0R and TP2 to 3.0R.

The streak table also says a 2-consecutive-loss pause (the existing SA-CRG
rule) is far tighter than normal variance warrants — at a 50% win rate the
median streak is 8. The pause was kept because it is cheap, but the new
cooldown is the primary brake and does not assume a streak.

---

## 2. Exit quality — timeouts are an exit profile, and they must be measured

**Source:** [Building a Trading System (Part 4): How Random Exits Influence
Trading Expectancy](https://www.mql5.com/en/articles/19211) — read in full.

Design: three trader profiles × five exit strategies, $1,000 equity, 1% risk,
100 trades per run, 100 simulations per strategy and 500 combined. Win rates
drawn 10–80%, RRR 0.1–3.0. Representative results:

| Win rate | RRR | Expectancy | Median equity | Median DD |
|---:|---:|---:|---:|---:|
| 20.92% | 2.61 | **-0.244** | $777 | 26.99% |
| 36.22% | 2.15 | +0.142 | $1,132 | 21.57% |
| 51.91% | 1.84 | +0.475 | $1,596 | 39.03% |
| 61.24% | 0.55 | **-0.049** | $946 | 10.29% |

Conclusion in the article's own words: profitability "stems from expectancy,
not from rigid profit targets", and a negative-expectancy exit profile "should
be eliminated" rather than diluted — combining it with good profiles drags the
whole book down.

**Applied here.** 67 of 287 live trades (23%) exited on the 120-minute
timeout. That is a distinct exit profile and it was never measured separately.
Two changes: the timeout is now expressed in trigger-frame bars
(`TIMEOUT_BARS = 24`, so 6h on M15) rather than a wall-clock constant that
would have amounted to 8 bars after the migration; and the trade journal now
records the outcome label per trade so the timeout bucket's expectancy can be
computed instead of assumed.

---

## 3. Minimum risk levels and the cost gap

**Source:** [Building a Trading System (Part 3): Determining Minimum Risk
Levels for Realistic Profit Targets](https://www.mql5.com/en/articles/19141) —
read in full.

Growth model: `Ef = P × (1 + f × RRR) + (1 - P) × (1 - f)`, solved for the
minimum risk fraction that reaches a target in n trades. Practical floors from
its tables:

- 30% win rate → RRR ≥ 3.5 at ≥2.0% risk
- 45% win rate → RRR ≥ 2.0 at ≥1.5% risk
- 76% win rate → RRR ≥ 1.0 at ≥1.5% risk

**Important caveat, stated by the author:** the model "did not account for
slippage, transaction costs, or swap fees". So these floors are optimistic;
real costs raise the required RRR further.

**Applied here.** The 45%-win-rate row is the realistic target profile for a
filtered sweep-rejection scalper, and it demands RRR ≥ 2.0 — which is exactly
where TP1 now sits. Because the article excludes costs, a fourth validation
layer was added to `step3_validate` that computes net R after round-turn
spread and rejects anything below 1.5:

```
net_R = (reward_pips - 2 × spread) / (sl_pips + 2 × spread)
```

Verified against the old geometry: a 1R target at 400-pip SL and 5-pip spread
nets **0.95R** — a losing proposition on every trade taken. The same setup at
2R nets **1.91R**.

---

## 4. Liquidity sweep on break of structure

**Source:** [Automating Trading Strategies in MQL5 (Part 46): Liquidity Sweep
on Break of Structure](https://www.mql5.com/en/articles/20569) — read in full.

Entry sequence: detect swings with `SwingLength` bars either side; label
HH/HL/LH/LL; a HH is a bullish BOS and a LL a bearish BOS; then require a
sweep-and-reject of the relevant swing — for longs, the prior bar's low pierces
the swing low, its close returns above it, and the bar is bullish.

Defaults: `SwingLength = 5`, `SL_Buffer_Pips = 10`, **`RiskRewardRatio = 2.0`**,
`MaxTrades = 1`. Stop goes beyond the sweep wick plus the buffer; target is
`entry ± risk × RiskRewardRatio`.

No win rate, profit factor, drawdown or sample size is published — only an
equity-curve image. A commenter raised repainting and the author did not
respond. **Not usable as performance evidence**; usable as design evidence.

**Applied here.** Two things corroborate existing design: `SwingLength = 5`
matches our `swing_lookback`, and the 2.0 default RR independently supports the
TP1 change. The `MaxTrades = 1` default also supports the per-symbol dedup
guard already present in `_scan_symbol`.

---

## 5. Combined SMC (OB + BOS + FVG)

**Source:** [Elevate Your Trading With Smart Money Concepts (SMC): OB, BOS,
and FVG](https://www.mql5.com/en/articles/16340) — read in full.

Definitions match ours closely: bullish FVG as `Low(A) > High(C)`, swing
detection over 5 bars per side, order block as the last opposing candle before
the move. Defaults: `StopLoss = 3500` points, `TakeProfit = 7500` points —
a **1:2.14 RR**.

The important negative finding: the EA runs the three concepts as *independent
parallel strategies*; there is no confluence logic, no volatility threshold, no
session filter and no trend confirmation. The article is explicit that quality
filters are minimal.

**Applied here.** Our stack already exceeds this reference on filtering
(regime whitelist, short-term bias gate, session windows, spread and SL
floors). The reference's value is corroborating the ~2R target default a third
time, and confirming that stacking detectors without confluence is a known weak
pattern — which is why the per-trigger regime whitelist stays.

---

## 6. Cooldowns after losses

**Source:** [Loss Streak Protection in Algorithmic
Trading](https://www.mql5.com/en/blogs/post/770285) — read in full.

Describes five defensive layers (adaptive risk reduction, cooldown logic,
loss-streak thresholds, pending-order re-evaluation, capital protection
priority) and one tiered example: "normal after one loss, reduce risk after
two, soft cooldown after three, deeper protective mode after four."

**It provides no durations or formulas** and says so — exact numbers "depend on
the strategy profile, win rate, payoff ratio, and expected sequence variance".
Recorded honestly: this source supports the *existence* of a cooldown, not any
particular length.

**Applied here.** The owner-specified rule (loss → next UTC hour, win → 5
minutes) is a concrete instantiation of the philosophy. Its merit is that the
pause is anchored to a market clock boundary rather than an arbitrary
countdown, so the agent sits out the remainder of the hourly candle it just
lost in. Implemented in `scalper/cooldown.py` with 17 tests pinning the exact
semantics.

---

## 7. Prior campaign — what has already been rejected

**Source:** `D:\Hermes Quant\GPTMain\docs\SCALPER_RESEARCH_JOURNAL.md` (donor
repository), read in full. That repository ran a walk-forward campaign with
broker-cost modelling, CONSUL-gate fidelity and locked fresh test data.

Seven independently constructed concepts were tested and **all rejected**:

| Concept | Sample | Outcome |
|---|---:|---|
| `BOS_RETEST`, all parameter variants | 72–hundreds | Negative full-period, PF 0.36–0.80 |
| `SWEEP_REJECTION` unfiltered | 7,709 candidates | Negative in **every** fold |
| `SWEEP_REJECTION` by session | — | Every session negative in every fold |
| `FVG_FILL` | 30 candidates | Too sparse; trends negative |
| `PULLBACK_CONTINUATION` | 44 candidates | 9 of 9 fold/rule combos negative |
| `SWEEP_BOS_CONTINUATION` | 121 candidates | 9 of 9 negative |
| `BOS_RETEST` with M5 confirmation | 33 candidates | Negative dev and test |

Two findings from that journal matter most here:

1. **The Asia block carried 68–73% of trade volume and the largest absolute
   loss.** That is a direct argument against the `Whole_day` fallback window,
   which is what admitted that volume. Now opt-in only.
2. **A gate-fidelity defect invalidated every earlier result** — the simulator
   never applied the CONSUL gates, so it took entries live SA would refuse.
   After the fix, the best rule's trade count fell from 72 to 8 on the same
   window. The lesson is procedural: a backtest that does not run the live
   decision path is not evidence. That is why the backtester here was realigned
   to the live regime whitelist and SL floor in this release.

**Honest position:** that campaign's conclusion was that the ceiling is data
availability, not strategy construction. Nothing in this release contradicts
it. What this release does is remove defects that made positive expectancy
*arithmetically impossible* (1R targets, cost-blind validation, a session gate
that did not gate, a directionally half-implemented trigger). Those had to be
fixed before any edge could show through. They are necessary, not sufficient.

---

## 8. What must happen before claiming profitability

> **Status: executed 2026-08-22. See §9 — the result was negative and the
> fold signs were unstable, so nothing was promoted.**

1. Re-run `backtest_scalper.py` on the M15/M5 stack now that live and
   simulated decision paths match, over the full available window.
2. Split chronologically 60/20/20 and require the same sign in all three
   folds. Sign instability across folds has been the standing rejection
   criterion and should remain so.
3. Compute expectancy **per exit type** (TP, SL, TIMEOUT, EOD). Eliminate any
   exit profile with negative expectancy rather than tuning around it.
4. Only then consider a live sizing change, and only on a genuinely untouched
   window.

---

---

## 9. Fresh-window walk-forward, 2026-05-15 -> 2026-08-21

**Source:** own run. `logs/backtest_20260515_20260821.json`, report at
`logs/walkforward_20260515_20260821.md`. 99 days, XAUUSD / XAGUSD / USOIL,
$500 pool at 2% risk, 2.5-pip round-turn spread charged, M15 trigger / M5
confirmation, full live gate chain.

This is the run §8 asked for. It is the first time the live and simulated
decision paths have actually matched — see the gate reconciliation below,
without which the numbers would not have been evidence.

### The result is negative and the folds are unstable

| | trades | win rate | net $ | exp (R) | PF | maxDD |
|---|---:|---:|---:|---:|---:|---:|
| full period | 198 | 32.83% | **-76.97** | -0.0180 | 0.94 | 36.36% |
| development | 118 | 33.05% | -40.87 | -0.0104 | 0.95 | 32.52% |
| validation | 40 | 27.50% | -61.42 | -0.1793 | 0.74 | 17.80% |
| test | 40 | 37.50% | +25.32 | +0.1208 | 1.13 | 8.27% |

Signs run `-` `-` `+`. Sign instability is the standing rejection criterion
(§8.2), so this is rejected. The single positive fold is 40 trades with
t = +0.52; that is indistinguishable from noise and must not be read as a
regime change.

### The survival condition is missed by a hair

A 32.83% win rate requires `RRR > (1 - 0.3283) / 0.3283 = 2.05`. Realised RRR
is **1.99** — avg win +1.98R against avg loss -0.99R. The geometry is doing
what §1 said it should; the entry signal is 0.06R short of paying for itself.

Note the requirement must be evaluated against *realised* payoff, not the
nominal 2.0R target. Timeout and EOD exits close inside the stop, so the
average loss is not a full 1R and the naive comparison mis-scores the book.
`analyze_walkforward.survival()` does it correctly.

**Costs are not the cause.** Gross -$70.07, costs $7.36. Removing friction
entirely still leaves a loss. This is signal quality.

### Expectancy per exit type — the timeout profile is gone

| exit | trades | share | exp (R) | book without it |
|---|---:|---:|---:|---:|
| TP | 64 | 32.32% | +1.9961 | -$1,175.20 |
| SL | 131 | 66.16% | -1.0039 | +$1,099.69 |
| TIMEOUT | 1 | 0.51% | -0.0881 | -$76.11 |
| EOD | 2 | 1.01% | +0.1374 | -$79.29 |

§2 flagged the 120-minute timeout as a distinct, never-measured exit profile
carrying 23% of live trades (67 of 287). On the M15 stack with
`TIMEOUT_BARS = 24` it is **0.51%** — one trade in 198. 98.5% of trades now
resolve at the target or the stop.

That closes the §2 action item, but not in the way it anticipated. There is no
negative-expectancy exit profile left to eliminate: the timeout was designed
out by the timeframe migration rather than tuned around, and what remains is a
clean two-outcome book whose entry signal is simply not good enough. Art.
19211's remedy does not apply because there is no longer a bad exit to remove.

### Two structural findings

**1. It is not a three-symbol portfolio.** All 198 trades are XAUUSD. XAGUSD
and USOIL produced zero. At $10 of risk (2% of $500) their pip values — 50x
and 10x XAUUSD's respectively — put every candidate lot below the 0.01 broker
floor, giving 676 `LOT_FLOOR` rejections. CLAUDE.md §12.4 describes the lot
floor as a beneficial noise filter; at this pool size it is also silently
deleting two thirds of the instrument list. Any claim of diversification here
is unfounded, and concentration risk is total.

**2. `SWEEP_REJECTION` is rejected an eighth time.** It is 196 of 198 trades
and nets -$91.63 at PF 0.92. §7 records the donor campaign rejecting it as
negative in *every* fold across 7,709 candidates. This run reproduces that
independently — fresh window, different timeframe stack, broker costs applied,
full gate fidelity. Two concepts in §7's table have now been rejected twice by
unrelated harnesses. `FVG_FILL` fired twice here, which is too few to say
anything about.

### Gate reconciliation that had to happen first

The backtester was measuring a different strategy from the live agent. Running
the window before fixing this would have repeated precisely the defect that
invalidated the donor repository's campaign (§7.2). Found and mirrored:

| Gate | State before | Rejections after |
|---|---|---:|
| Cooldown | **absent entirely** | 462 |
| Per-symbol dedup | no-op (`closed: True` always) | 279 |
| EOD 23:00 close | **absent** — bucket unmeasurable | — |
| STB gate | absent | 676 |
| Thin-liquidity gate | absent | 233 |
| SA-CRG + daily loss limit | absent | 51 |
| News blackout | 15/15 | live is 30/15 |

Three further defects, none of which were gates:

- **Look-ahead.** Decision frames were sliced to `i + 1`, handing the
  simulator the still-forming M15 bar's final high, low and close. The live
  agent sees that bar half-built.
- **The exit scan started at `entry_idx + 1`**, skipping the entry bar and
  deferring every stop by one M5 bar.
- **Per-symbol loops cannot express global state.** Cooldown, balance, daily
  loss and the position cap are all global in the live agent — a loss on
  XAUUSD pauses USOIL. Rewritten as a single chronological event loop over the
  merged M15 timeline.

Two gates read the wall clock internally (`ShortTermBiasFilter.check`,
`SACRG.check`), so replaying May bars against today's date silently disabled
the session-liquidity tracker and the consecutive-loss pause. Both now take an
injected `now`, defaulting to the wall clock for the live agent.

### Where this leaves the strategy

The 2026-08-22 release removed the defects that made positive expectancy
*arithmetically impossible*. This run confirms they were necessary and shows
they were not sufficient: realised RRR went from hopeless to 1.99 against a
2.05 requirement, and the book is still a loss with unstable fold signs.

The honest reading is that §7's conclusion still stands — the binding
constraint is entry-signal quality, and `SWEEP_REJECTION` on liquidity sweeps
is not producing an edge on this instrument set. Raising the win rate above
33.3% or the payoff above 2.05R is what would change the answer; nothing else
will.

Next steps that would be evidence rather than motion:

1. Do **not** re-tune `SWEEP_REJECTION` parameters on this window. That is
   what §7 already exhausted across 7,709 candidates.
2. Measure the XAGUSD/USOIL lot-floor exclusion deliberately — either raise
   the pool so their setups can size, or drop them from the config so the
   system stops advertising diversification it does not have.
3. Any new concept goes through the same 60/20/20 bar before it is discussed
   as a candidate, and §7's rejected list is checked first.

---

---

## 10. The lot floor is a free look — why every prior campaign was negative

**Source:** own runs, 2026-08-22. `logs/bt_baseline.json`,
`logs/bt_xau_1pct.json`, `logs/bt_xau_1pct_dll33.json`,
`logs/bt_baseline_nodll.json`, `logs/bt_asiaonly.json`,
`logs/bt_nolondon.json`, `logs/bt_control_prior.json`, `logs/bt_bos.json`.
Window 2026-05-15 -> 08-23, XAUUSD, full live gate chain, 2.5-pip round-turn
spread charged.

### The finding

Holding the window, the instrument, the signal and every gate constant and
changing **only the risk budget** flips the book:

| risk | trades | win rate | net $ | realised RRR | folds |
|---|---:|---:|---:|---:|---|
| 1% of $1,000 = $10/trade | 210 | 32.38% | -91.15 | — | `- - +` UNSTABLE |
| 3% of $1,000 = $30/trade | 269 | 46.84% | +3,211.19 | 1.63 | `+ + +` CONSISTENT |

Partitioning the two trade sets by open timestamp gives the mechanism:

| set | trades | win rate | exp R | median SL |
|---|---:|---:|---:|---:|
| taken by both runs | 69 | 47.8% | +0.413 | 750p |
| **only at $10 risk** | 141 | **24.8%** | **-0.249** | 713p |
| only at $30 risk | 200 | 46.5% | +0.151 | 1,573p |

The overlap is healthy under both budgets. The small budget adds 141 trades of
its own that win 24.8% of the time, at the *same* median stop width as the
shared set.

### Why they exist

`_calc_volume` returns 0 when `risk_usd / (sl_pips x pip_value)` falls below
the 0.01 broker minimum, and `LOT_FLOOR` is the last gate in the chain. A
rejection there costs the agent nothing: no position is opened, so the
per-symbol dedup guard stays open, and no trade closes, so no cooldown arms.
The agent re-scans on the next bar and takes a degraded re-entry into the same
move — later, at a worse price, with a tighter stop that the same volatility
then takes out.

On XAUUSD the affordable stop is `risk_usd / $1 per pip`, so a $10 budget can
only ever hold a stop up to ~1,000 pips. The median stop the signal actually
asks for on this window is ~1,316 pips. The budget could not afford the median
setup, so the agent spent the window trading the residue.

### What was ruled out, each by its own run

- **Stop width.** Matched SL buckets (0-600, 600-800, 800-1000, 1000-1200)
  show the $30 run winning 44.8-47.8% against the $10 run's 14.3-34.4% at
  every width. Not a width effect.
- **Daily loss limit.** At 3% risk with compounding, growing risk against a
  fixed $100 limit means one late-run loss ends the day, which would inflate
  win rate. Removing the limit entirely leaves 301 trades, 45.51%, +$3,108.15,
  all folds positive. Not the driver. The matched test in the other direction
  (1% risk, $33 limit) stayed at 31.71% and negative.
- **Symbol list.** XAUUSD-only at $10 risk reproduces the three-symbol result
  (210 trades / 32.38% / -$91.15 against §9's 198 / 32.83% / -$76.97).
- **Clustering.** Median inter-trade gap 405min at $10 vs 375min at $30; 0% vs
  3% of gaps under 30 minutes. The free-look trades are not rapid re-fires.
- **This session's code changes.** A control run at the exact prior
  configuration returns 197 trades / 32.99% / -$65.30 against the recorded
  198 / 32.83% / -$76.97 — one trade of window difference. The gate chain is
  unchanged.

### What this retracts

**`CLAUDE.md` §12.4 is wrong.** The lot floor was documented as a beneficial
volatility filter, credited with lifting the win rate from 50.94% to 62.50% in
the superseded 30-day backtest. It is the opposite: it removes the affordable
trade and leaves the agent free to take a worse one. The §12.4 figures come
from the harness §9 already marked superseded, so nothing else rests on them.

**§7's conclusion needs qualifying.** Seven concepts were rejected in the
donor campaign and an eighth here, all at small pool sizes. If those runs
carried the same defect — and the mechanism is in `_calc_volume`, not in any
strategy — then what was rejected was not the concepts but the concepts as
sampled through an unaffordable risk budget. That does not make them good; it
means the rejections are not clean, and any of them could be re-run at a
budget that affords the median stop.

**Session attribution was contaminated by it.** At $10 risk the two London
windows show -$202.68 over 69 trades, 23.19% win rate, negative in all three
folds — the only block with a stable sign, and the obvious candidate to switch
off. At $30 risk the same window shows LONDON at +$233.61. 74% of the $10
run's London trades were free-look re-entries, winning 22.2%; London is the
highest-volatility window and therefore where the budget most often could not
afford the setup. ASIA had the lowest free-look share at 46%, which is why it
looked uniquely healthy. Asia remains positive in all three folds under both
sizings (+$104.53 `+++` at $500/2%, +$1,867.26 `+++` at $1,000/3%), and that
is the most robust single fact on this data — but the reason is contamination
elsewhere, not an Asian edge.

### Standing consequence

Position sizing was treated here as a risk parameter. It is also a **signal
admission filter**, and a silent one. Before any strategy verdict, check that
`risk_usd / pip_value` exceeds the median stop the signal produces; if it does
not, the run is measuring the residue, not the strategy.

The open defect: on a losing streak the balance falls, the affordable stop
falls with it, and the agent degrades into free-look re-entry mode exactly
when it is already losing. That is a drawdown accelerator and it is still
present. The candidate fix is to make a `LOT_FLOOR` rejection consume the
setup — arm the cooldown or hold the symbol down — so an unaffordable signal
is a skipped opportunity rather than an invitation to take a worse one. Not
implemented: the expiry rule is a design choice with a free parameter, and
fitting it on this window is exactly what §8 forbids.

### BOS_RETEST re-test

§7 records BOS_RETEST as rejected in all variants (PF 0.36-0.80; negative in
dev and test with M5 confirmation). **That rejection is not binding on the
current detector.** It tested a version that only implemented the bullish
branch — the bearish half was added 2026-08-22 — on a different timeframe
stack. A re-test is legitimate and the record should say so.

Isolated with `--triggers BOS_RETEST` (the whitelist added in this release;
`step2_trigger` returns the first detector that fires, and SWEEP_REJECTION sits
at the head of the order, which is why BOS_RETEST showed zero trades in §9):

**1,278 detections, 3 trades.** Rejections attributed to it: 676 short-term
bias, 578 CONSUL Gate 1 regime (BOS_RETEST is whitelisted to EXPANSION only),
21 thin liquidity. The detector fires constantly and the gate chain consumes
essentially all of it.

Three trades is not a sample. **No verdict on BOS_RETEST's edge is available
in either direction.** §9's "zero trades" was a statement about the priority
order and the regime whitelist, never about the signal — a zero-trade result
is not a negative result. Evaluating it requires either widening its regime
whitelist or relaxing the STB gate for it, both of which are gate changes
needing their own evidence.

## 11. The H1 EMA(18) high/low band — measured and rejected

**Requested rule.** Two 18-period EMAs on H1, one on the high series and one
on the low series, forming a channel. Longs permitted only while price trades
above both, shorts only while below both, nothing while inside.

### Sources — design evidence only

**[Price Action Analysis Toolkit Part 54](https://www.mql5.com/en/articles/20851)**
— read in full. Builds exactly this construct: `EMA20` on high as the upper
boundary, `EMA20` on low as the lower, plus an `EMA50` on close as a slope
filter. Buy requires close above the EMA-high band *and* above EMA50 *and* a
rising EMA50. Evaluated once per closed bar with timestamp deduplication.
Reports no win rate, profit factor, trade count, date range or drawdown — it
is a signal tool, and the article says so.

**[From Novice to Expert: Extending a Liquidity Strategy with Trend Filters](https://www.mql5.com/en/articles/21133)**
— read in full, and the closest published analogue to this repository: a
liquidity/zone strategy having a trend filter bolted on. Uses `EMA50` close,
shift 1, stating that this "ensures the value comes from a fully closed
candle". Reports "approximately 35% fewer trades" and smoother drawdowns.
No win rate, profit factor, net profit, symbol or date range.

Both are **design** evidence under §13.7 and neither is performance evidence.
The one number worth carrying forward is the ~35% trade reduction, as an
order-of-magnitude expectation. Measured here: 32% at 3% risk.

### What was measured

Walk-forward, XAUUSD, 2026-05-15 -> 08-23, chronological 60/20/20. Both arms
run the identical gate chain, identical windows and identical per-bar broker
spread; the band is the only difference. `TREND` is the requested mapping,
`FADE` its inversion.

| Risk | Arm | Trades | Win% | Net $ | PF | MaxDD% | F1 | F2 | F3 | §13.5 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 3% | off | 280 | 46.1 | +2742 | 1.35 | 25.8 | + | + | + | **pass** |
| 3% | TREND | 190 | 41.0 | +230 | 1.06 | 32.4 | + | + | − | fail |
| 3% | FADE | 170 | 34.1 | −339 | 0.87 | 48.8 | − | − | − | fail |
| 2% | off | 303 | 46.2 | +1617 | 1.36 | 19.8 | + | + | + | **pass** |
| 2% | TREND | 182 | 40.7 | +281 | 1.11 | 20.5 | + | − | − | fail |
| 2% | FADE | 161 | 34.2 | −244 | 0.87 | 34.1 | − | + | − | fail |

The 1%-risk arm is **excluded as non-evidence**: it takes 604 `LOT_FLOOR`
rejections against 13 at 3%, so it measures the §10 residue rather than the
strategy. Its baseline is −$11 and every arm fails, which is a statement about
a $10 risk budget on a ~1400-point stop, not about the band.

### Why it fails — structural, not polarity

The band removes the better half of the book:

| | Trades | TP% | Net $ | PF | mean R |
|---|---:|---:|---:|---:|---:|
| removed by the band | 197 | 31.5 | +2874 | **1.52** | +0.216 |
| kept by the band | 83 | 26.5 | −131 | **0.94** | +0.114 |

276 of 280 trades come from `SWEEP_REJECTION`, which fades a liquidity grab.
A trend band vetoes a fade by construction — the trigger's premise is that
price has over-extended and will revert, and the band's premise is that
over-extension should be followed. Inverting the mapping does not rescue it
(`FADE` is worse than `TREND` in every fold), which rules out a sign error and
points at the filter itself being wrong for this book.

Drawdown *rises* when the band is on (25.8% -> 32.4% at 3% risk) despite a
third fewer trades: the removed trades were carrying the recovery.

### Disposition

Implemented in full, in both processes, switchable via `--ema-band` /
`--ema-band-mode`, and **off by default**. Promoting it would violate §13.5 —
its sign is not stable across folds at any risk level where the lot floor is
not binding.

It is kept rather than deleted because the failure is specific to a
sweep-fade trigger mix. Against a continuation-heavy mix — `BOS_RETEST`,
`FVG_FILL` — the band is aligned with the trigger premise rather than opposed
to it, and art. 20851 pairs it with a slope filter for exactly that use.
Measuring that requires first getting a `BOS_RETEST` sample, which §9 shows
does not currently exist (3 trades).

**Do not enable the band on the current trigger mix without new fold
evidence.** It has been measured and it loses money.

---

## 12. The H4 volume profile / value-area gate — measured and rejected

**Proposal.** Build a volume profile on H4. Sell from VAH, buy from VAL, take
no trade at the POC. Gate it on a trending-vs-ranging classifier so the value
area is only used while the market is actually rotating.

**Origin.** A live loss the operator diagnosed by eye: XAUUSD 2026-08-25
16:45:25 UTC, BUY at 4642.26, stopped at 4634.49 for **-$23.29**.

**The diagnosis was correct.** The 42-bar H4 profile at that moment put the POC
at **4641.33** — the entry was **0.93** away, on a value area **248.43** wide.
A textbook POC entry.

**The remedy built from it is wrong, and by a wide margin.**

### 12.1 The same day already contained the counter-evidence

The two *winning* shorts earlier that session were also at the POC:

| time (UTC) | side | entry | POC then | distance | P&L |
|---|---|---|---|---:|---:|
| 11:59:50 | SELL | 4642.94 | 4647.55 | 4.61 | **+$30.38** |
| 12:15:21 | SELL | 4638.78 | 4641.33 | 2.55 | **+$59.42** |
| 16:45:25 | BUY  | 4642.26 | 4641.33 | 0.93 | **-$23.29** |

No POC band exists that rejects the loss and keeps both winners. Across those
four trades a blanket POC veto nets **-$74.79**.

### 12.2 Walk-forward, XAUUSD 2026-05-15 -> 08-23, pool $1000 @ 3%

Every arm identical but for the gate. `analyze_walkforward.py`, 60/20/20.

| arm | mode | POC band | trades | win rate | net $ | fold verdict |
|---|---|---:|---:|---:|---:|---|
| baseline | off | — | 280 | 46.07% | **+2742.35** | CONSISTENT + + + |
| poc02 | POC_ONLY | 0.02 | 279 | 45.52% | +2231.08 | CONSISTENT + + + |
| poc05 | POC_ONLY | 0.05 | 265 | 44.53% | +1998.36 | — |
| poc10 | POC_ONLY | 0.10 | 265 | 43.77% | +1414.78 | CONSISTENT + + + |
| rng10 | RANGING_ONLY | 0.10 | 231 | 38.96% | +121.46 | **UNSTABLE - + -** |
| alw10 | ALWAYS | 0.10 | 206 | 35.92% | **-149.53** | **UNSTABLE - + -** |
| req10 | POC_REQUIRE | 0.10 | 54 | 44.44% | +324.70 | CONSISTENT + + + |
| req20 | POC_REQUIRE | 0.20 | 98 | 44.90% | +311.51 | CONSISTENT + + + |

Damage is monotonic in band width — 0.02 costs $511, 0.05 costs $744, 0.10
costs $1328 — and win rate falls with it, 46.07% -> 43.77%. The gate removes
winners preferentially: the same signature the EMA band showed in section 11.

The two arms applying the full buy-low/sell-high rule (`RANGING_ONLY`,
`ALWAYS`) **fail §13.5 outright**: the sign flips across folds. `ALWAYS` also
fails the survival condition — a 35.92% win rate needs RRR > 1.78 and the
realised payoff does not reach it.

### 12.3 Attribution: the POC is the best place this book trades, not the worst

All 280 baseline trades, labelled with the H4 value-area location at entry:

| location | n | win% | net $ | avg $ | PF |
|---|---:|---:|---:|---:|---:|
| **AT_POC** | 42 | 47.6% | **+1038.04** | **+24.72** | **2.10** |
| LOWER_VALUE | 52 | 48.1% | +653.71 | +12.57 | 1.53 |
| BELOW_VAL | 52 | 44.2% | +421.75 | +8.11 | 1.34 |
| AT_VAH | 19 | 47.4% | +291.54 | +15.34 | 1.57 |
| ABOVE_VAH | 47 | 42.6% | +280.55 | +5.97 | 1.15 |
| UPPER_VALUE | 42 | 47.6% | +107.01 | +2.55 | 1.08 |
| **AT_VAL** | 26 | 46.2% | **-50.25** | -1.93 | **0.93** |

The proposed rule is inverted with respect to the data. `AT_POC` is the single
best bucket by both net and profit factor; `AT_VAL` — "buy from VAL", one of
the two rules specified — is the only negative bucket in the table.

A plausible mechanism, **not established**: the POC is where resting liquidity
is densest, so a sweep rejection there has the most order flow to reject
against for a given stop distance. 494 of 524 detected triggers are
`SWEEP_REJECTION`.

### 12.4 The inverse does not rescue it either

`POC_REQUIRE` (admit *only* POC entries) passes all three folds — and earns
**+$324.70 on 54 trades** against the baseline's **+$2742.35 on 280**. A better
average per trade cannot pay for discarding 80% of the book.

Note that the 42-trade `AT_POC` bucket in §12.3 and the 54-trade `req10` book
are **not the same trades**. Gating is path-dependent: vetoing a trade frees a
position slot and skips a cooldown, so different trades enter later.
Attribution describes the book that was taken; it does not predict the book a
filter would produce. §12.2 is the evidence; §12.3 is only a lead.

### 12.5 Verdict

**Rejected.** `VP_GATE_ENABLED = False`. No configuration of this gate beat
doing nothing. It stays implemented and switchable so it can be re-measured
against a continuation-heavy trigger mix, which this book is not.

Kept from the work, because they are useful independent of the gate:

* `scalper/volume_profile.py` — POC/VAH/VAL, transcribed from MQL5 art. 23169
  including its upward tie-break.
* `scalper/regime_classifier.py` — deterministic trending/ranging read.
* `scalper/hmm_backend.py` — optional HMM, off by default.

### 12.6 Regime attribution — the lead worth following

The same 280 trades, labelled with the H1 regime at entry:

| regime | n | win% | net $ | avg $ | PF |
|---|---:|---:|---:|---:|---:|
| RANGING | 219 | 45.7% | +2221.78 | +10.15 | 1.37 |
| TRENDING_UP | 12 | 58.3% | +442.10 | +36.84 | 2.24 |
| VOLATILE | 28 | 46.4% | +205.43 | +7.34 | 1.23 |
| **TRENDING_DOWN** | 21 | 42.9% | **-126.96** | -6.05 | **0.80** |

Split by direction, the loss concentrates hard:

| regime / direction | n | win% | net $ | avg $ | PF |
|---|---:|---:|---:|---:|---:|
| RANGING / BEARISH | 107 | 48.6% | +1532.00 | +14.32 | 1.60 |
| RANGING / BULLISH | 112 | 42.9% | +689.78 | +6.16 | 1.20 |
| **TRENDING_DOWN / BULLISH** | 8 | 37.5% | **-214.91** | **-26.86** | **0.35** |

Two readings, both worth stating:

1. **78% of this book already happens in RANGING.** The scalper is a range
   trader that did not know it was one. "Trade the sideways market better" is
   therefore mostly a question about the 219 trades it already takes there.
2. **Every materially losing cell is a long into a downtrend.**
   `TRENDING_DOWN / BULLISH` is 8 trades and -$214.91 at PF 0.35 —
   mechanically coherent, and the same shape as the live loss that started
   this. **n=8. That is a lead, not a finding**, and §13.10 does not let a
   diagnosis authorise a change. Ledger row L-006.

### 12.7 Sources

* MQL5 art. 23169, *Automatic Session Volume Profile Builder* — read in full.
  Supplied the histogram, POC scan and value-area expansion including the
  `vol_above >= vol_below` upward tie-break, the 70% default and the 10000-bin
  guard. **No performance data of any kind**, and it states outright that
  volume profiles "do not predict where price will move in the following
  session." Design evidence only (§13.7).
* MQL5 art. 17737, *Building a Custom Market Regime Detection System, Part 1* —
  read in full. Autocorrelation formula, lookback 100, smoothing 10, trend
  threshold 0.2, volatility threshold 1.5. **No performance data.**
* MQL5 art. 17781, Part 2 (the EA) — RSI(14) 30/70 mean reversion in ranges,
  Bollinger breakout when volatile, gold M1. Reports "approximately 20% equity
  growth" before optimisation with "significant drawdowns", nothing numeric
  after it, and warns its own optimisation "introduces a risk of overfitting".
* MQL5 art. 17917, *Hidden Markov Models in ML-Based Trading Systems* — read in
  full. 3-5 hidden states, rolling standard deviations as features, 2000-2024
  training. **No win rate, profit factor, trade count or Sharpe** — equity
  images only. Records that HMMs are "prone to overfitting on non-stationary
  time series", "can get stuck in local optima", and that its variational
  variant "required several training restarts".

## 13. L-006 tested out-of-sample — and what it exposed about the baseline

### 13.1 The L-006 result: not confirmed

The hypothesis (§12.6) was that longs into an H1 downtrend are the book's one
structural defect: 8 trades, PF 0.35, -$214.91. Implemented as
`scalper/regime_direction_gate.py` in two modes — `SYMMETRIC` (veto any
counter-trend trigger) and `COUNTER_TREND_LONGS` (the literal observation,
longs only) — and run where the hypothesis had never been.

| window | arm | trades | WR | net $ | vs base | folds |
|---|---|---:|---:|---:|---:|---|
| XAUUSD 05-15..08-23 **\*** | baseline | 280 | 46.07% | +2742.35 | — | + + + |
| XAUUSD 05-15..08-23 **\*** | SYMMETRIC | 279 | 45.16% | +2468.35 | **-274.00** | + + + |
| XAUUSD 05-15..08-23 **\*** | LONGS_ONLY | 279 | 45.88% | +3036.02 | **+293.67** | + + + |
| XAUUSD 2025-08-01..2026-05-14 | baseline | 622 | 35.53% | -650.66 | — | - - - |
| XAUUSD 2025-08-01..2026-05-14 | SYMMETRIC | 654 | 36.09% | -382.04 | +268.62 | **- - +** |
| XAUUSD 2025-08-01..2026-05-14 | LONGS_ONLY | 621 | 35.43% | -618.37 | +32.29 | - - - |
| USOIL 05-15..08-23 | baseline | 15 | 40.00% | -63.30 | — | - - - |
| USOIL 05-15..08-23 | both arms | 15 | 40.00% | -63.30 | **0.00** | - - - |
| XAGUSD 05-15..08-23 | all arms | 0 | — | 0.00 | — | no trades |

**\*** = the window the hypothesis was read off. Contaminated by construction.

**Verdict: rejected.** The two modes disagree in sign on the window the
hypothesis came from (`LONGS_ONLY` +$294, `SYMMETRIC` -$274), which is the
signature of fitting a direction to n=8 rather than finding a mechanism. Out of
sample the effect collapses: `LONGS_ONLY` is +$32.29 across **622** trades —
0.05 dollars per trade, indistinguishable from nothing — and on USOIL the gate
never fires at all, changing the book by exactly $0.00. `SYMMETRIC` looks
better out of sample at +$268.62, but flips sign across folds, which is §13.5's
standing rejection criterion.

This is the third trend filter rejected on this book, after the EMA band (§11)
and the value-area location rule (§12). The pattern is consistent and is
probably structural: ~99% of trades come from `SWEEP_REJECTION`, which fades a
liquidity grab. Anything that vetoes a fade vetoes the strategy.

### 13.2 The finding that matters more: the baseline window is the outlier

Running the earlier window produced a number nobody was looking for.

> **XAUUSD 2025-08-01 .. 2026-05-14, current configuration, unchanged gates:
> 622 trades, 35.53% win rate, -$650.66, negative in all three folds.**

The nine months immediately preceding the celebrated window **lose money
consistently**, on a sample **2.2x larger** than the one every promotion
decision in this document has been measured against.

| window | days | trades | WR | net $ | fold signs |
|---|---:|---:|---:|---:|---|
| 2025-08-01 .. 2026-05-14 | 286 | 622 | **35.53%** | **-650.66** | - - - |
| 2026-05-15 .. 2026-08-23 | 100 | 280 | **46.07%** | **+2742.35** | + + + |

The survival condition makes the gap concrete. At 35.53% the book needs
RRR > 1.81 to break even; at 46.07% it needs only RRR > 1.17. The same geometry
sits on the right side of that line in one window and the wrong side in the
other. The strategy did not change between them — the market did.

**What this does and does not mean.**

- It does **not** mean the +$2742 is fake. Those trades were simulated with
  the broker's own per-bar spread through the live decision path.
- It **does** mean §13.5 has been applied at the wrong scale. "No promotion
  from a single window" was enforced *within* 2026-05-15..08-23 by splitting it
  60/20/20 — three folds of one regime. Every rule accepted or rejected in
  §§9-12, including the EMA band and the value-area gate, was judged against a
  100-day sample that is now visibly unrepresentative.
- The correct reading of the 8-arm table in §12.2 is therefore narrower than
  it looked: it shows those gates did not help *in a window where the strategy
  was already working*. It says nothing about whether they help in the regime
  where it is not.

**This supersedes nothing yet and settles nothing.** It is one additional
window, and it inherits its own selection problem: the current parameters
(TP1 = 2R, the cooldown policy, the session whitelist) were chosen while
looking at recent data, so replaying them on older data is not a clean
out-of-sample test of the parameters — only of the gates layered on top.

**What has to happen before any further gate work is worth doing** — recorded
here because continuing to tune against the 100-day window is now known to be
measuring the wrong thing:

1. Re-run the baseline across several disjoint multi-month windows back to
   2018 and tabulate net, win rate and fold signs for each. Establish whether
   2026-05-15..08-23 is one good window among many or the only one.
2. Re-derive the survival condition from the *pooled* win rate rather than the
   recent one, and check TP1 = 2R against it.
3. Only then revisit any of the rejected gates. A filter that loses money in a
   working regime may be exactly what a non-working regime needs, and none of
   §§11-13 can currently distinguish those cases.

Ledger rows L-006 (rejected) and L-008 (opened for the window audit).

## 14. VALUE_AREA_FADE as an entry model — built, and structurally inert

§12 measured the value-area rule as a **filter** and rejected it. That result
constrains vetoes, not signals, so the rule as the operator actually specified
it — *sell a rejection at VAH, buy one at VAL, nothing at the POC* — was built
as a first-class trigger and measured separately.

`scalper/va_fade_trigger.py` + `SATriggerEngine._check_value_area_fade`.
Confluence is two conditions, both required: a rejection wick of at least 33%
of the bar range on the correct side, and RSI(14) beyond 70/30 (art. 17781
pairs exactly those thresholds with a ranging classification). The stop sits
beyond the range marker — the swing bounding the balance area, the level an
operator draws by hand — plus 5% of value-area width. Targets are the shared
2R/3R, **not** the POC: the intuitive fade target sits inside 1R on a tight
value area and would recreate the sub-2R geometry of §13.1.

### 14.1 It executes zero trades

| run | window | detections | trades taken | net $ |
|---|---|---:|---:|---:|
| isolated | 2026-05-15..08-23 | 3 | **0** | 0.00 |
| isolated | 2025-08-01..2026-05-14 | 10 | **0** | 0.00 |
| in the full mix | 2025-08-01..2026-05-14 | 2 | 0 | — |

Two independent causes, and the first is the interesting one.

**Cause 1 — an existing gate vetoes fades by construction.**

    STB/opposes_short_term : VALUE_AREA_FADE   3 of 3   (100-day window)
    STB/opposes_short_term : VALUE_AREA_FADE   9 of 10  (9-month window)

The short-term-bias gate's third rule blocks a trigger whose direction opposes
a clear short-term read. `decision_params` documents it as "direction-generic
and stays enforced for every trigger: a continuation setup fighting the
immediate move contradicts itself."

That justification is written for continuation setups and is **false for a
fade**. Price pushing up into the VAH *is* a bullish short-term read; selling
it is the entire trade. The rule therefore vetoes every mean-reversion entry as
a matter of definition, not of evidence. `STB_CONTINUATION_TRIGGERS` already
exists to relax two other rules for continuation triggers; the mirror image —
relaxing rule 3 for fade triggers — does not exist.

**Cause 2 — the conjunction almost never occurs.** 3 detections in 100 days and
10 in nine months, before any gate. Requiring M15 RSI(14) to be beyond 70/30 at
the exact bar that rejects a *weekly* H4 value-area edge is a rare coincidence.
Even with cause 1 removed, ~13 signals in 13 months is not a strategy.

### 14.2 What this does and does not settle

It does **not** show the operator's rule is wrong. It shows the rule cannot be
evaluated inside the current gate chain, because a gate written for a
trend-following premise silently forbids the mean-reversion premise. Until that
is fixed the measurement is of the plumbing, not the idea.

Two changes would make it measurable, in this order:

1. Add a fade-trigger relaxation for the STB "opposes short-term" rule, mirroring
   `STB_CONTINUATION_TRIGGERS`, and mirror it into the simulator (invariant #2).
2. Loosen the confluence conjunction — RSI thresholds toward 60/40, or accept
   either the wick or the RSI rather than both — until detections reach a
   sample size where a fold test can say something. Then tighten back.

Neither is done here: both are strategy changes, and §13.10 does not let a
diagnosis authorise one. Ledger L-009.

### 14.3 The marked levels

`RangeLevels` carries what the operator draws: `range_high` and `range_low`
bounding the balance area, and `major_liquidity_above` / `_below` — the nearest
untapped swing outside that span, which is what a stop run aims at. These are
derived from fractal swing structure and the profile's own extremes; **no price
is hard-coded**. The 4700 level that prompted the request is simply the
prevailing range high, and the detector locates it the way an eye does, so it
keeps working when the range moves.

## 15. The `Whole_day` window — measured on both windows, and rejected

**Question asked:** would enabling the `Whole_day` 00:00-23:00 catch-all help
or hurt? It has been off since the §13.6 fix, but the rationale on record came
from the donor campaign, on a different strategy and a pre-2R geometry. It had
never been measured on the current stack.

**Source:** own runs, `logs/wd_*.json`. XAUUSD unless stated, $1000 pool,
per-bar MT5 broker spread, full live gate chain, `--allow-whole-day` toggled as
the only variable.

### 15.1 The headline: it fails §13.5 in every configuration tested

| window | arm | trades | WR | net $ | PF | maxDD | folds |
|---|---|---:|---:|---:|---:|---:|---|
| 2026-05-15..08-23 | kill zones | 280 | 46.07% | **+2742.35** | 1.35 | 25.8% | + + + CONSISTENT |
| 2026-05-15..08-23 | whole day | 421 | 41.81% | +608.90 | 1.06 | 41.2% | + - + **UNSTABLE** |
| 2025-08-01..2026-05-14 | kill zones | 625 | 35.52% | -656.96 | 0.91 | 80.3% | - - - CONSISTENT |
| 2025-08-01..2026-05-14 | whole day | 1060 | 37.36% | -661.75 | 0.93 | 84.5% | - - + **UNSTABLE** |

Four further arms, same window (2026-05-15..08-23):

| arm | trades | net $ | PF | folds | `LOT_FLOOR` |
|---|---:|---:|---:|---|---:|
| 3 symbols @ 3%, kill zones | 289 | +2606.56 | 1.33 | + + + CONSISTENT | 53 |
| 3 symbols @ 3%, whole day | 446 | +532.20 | 1.05 | + - - **UNSTABLE** | 183 |
| XAUUSD @ 2%, kill zones | 303 | +1616.88 | 1.36 | + + + CONSISTENT | 52 |
| XAUUSD @ 2%, whole day | 463 | +866.48 | 1.11 | + - - **UNSTABLE** | 134 |

Six configurations. **Kill-zone gating is fold-consistent in all six; the
whole-day arm is fold-unstable in all six.** In the favourable window it cuts
net P&L by 78% and nearly doubles drawdown. In the unfavourable window it is
flat in dollars and worse in drawdown. There is no configuration in which it
helps.

`LOT_FLOOR` counts are reported per §13.9: the whole-day arms take ~3x as many
lot-floor rejections, so they are also admitting a slightly different sample —
which weakens the whole-day arm's case further, not the baseline's.

### 15.2 The mechanism is not "off-session hours are toxic"

Attribution inside the whole-day book, favourable window:

| block | share of volume | net $ |
|---|---:|---:|
| kill-zone hours | 46.8% | +581.12 |
| extra hours the fallback admits | 53.2% | **+27.78** |

The extra 224 trades are very nearly a coin flip (PF 1.01) — not a disaster on
their own. The damage is that **the kill-zone book collapses from +$2742 to
+$581 on substantially the same hours.** Per session, favourable window:

| session | kill-zones only | with whole day |
|---|---|---|
| TOKYO_OPEN | 109 tr / +1820.27 | 111 tr / +1585.09 |
| LONDON_NY | 56 tr / +552.85 | 37 tr / **-655.74** |
| PRE_LONDON | 38 tr / +330.10 | **2 tr** / +15.09 |
| NY_LUNCH_REV | 36 tr / +240.72 | 13 tr / +46.04 |
| LONDON_OPEN | 41 tr / -201.59 | 34 tr / -409.36 |

**Hour 00 is the natural control.** It is the one kill-zone hour that nothing
can precede — the daily reset clears and there is no earlier session to spend
the budget. Its trade count is *identical* across arms (77 -> 77) and its P&L
barely moves. Every kill-zone hour that *can* be preceded by off-session
activity loses trades and worsens: hour 06 goes +$330 -> **-$263**, hour 12
goes +$394 -> **-$354**, hour 17 goes +$396 -> +$93. PRE_LONDON, a 30-minute
window sitting directly downstream of newly-opened hours, loses 95% of its
trades.

The rejection counters price the crowding: `SYMBOL_DEDUP` 856 -> 3656,
`DAILY_LOSS_LIMIT` 237 -> 896, `COOLDOWN` 200 -> 526.

### 15.3 But crowding is not the whole story either

If crowding through the two *tunable* risk controls were the cause, disabling
them should close the gap. It widens it:

| config | kill zones | whole day | gap |
|---|---:|---:|---:|
| loss-limit on, cooldown on (shipped) | +2742.35 | +608.90 | -2133 |
| loss-limit off, cooldown on | +3108.10 | +365.73 | -2742 |
| loss-limit on, cooldown off | +2371.89 | +749.66 | -1622 |
| loss-limit off, cooldown off | +2686.39 | **-276.70** | -2963 |

With both risk controls off the whole-day arm is outright **negative** while
the kill-zone arm is unchanged. So the daily loss limit and the cooldown were
*masking* part of the off-session damage by capping how much could be lost out
there. The residual channel is `SYMBOL_DEDUP`, which is structural and cannot
be switched off: one position per symbol, and an off-session entry with a 6h
bar timeout can still be open when London Open arrives.

Both effects are real and both point the same way.

### 15.4 Correction: the "Asia" rationale on record is wrong

`session_checker.py` and CLAUDE.md §13.6 both justified the default with the
donor finding that *"the Asia block carried 68-73% of trade volume and the
largest absolute loss."* Measured on the current stack, in the whole-day arm
where those hours actually trade:

| block | recent window | prior window |
|---|---|---|
| ASIA 00-07 | 43.0% vol, **+$1872.80** (only positive block) | 40.2% vol, -$46.99 (**smallest** loss) |
| LONDON 07-12 | 22.1% vol, -$314.79 | 24.0% vol, **-$517.79** (largest loss) |
| NY 12-17 | 20.9% vol, -$875.23 | 19.1% vol, -$288.86 |
| LATE 17-24 | 14.0% vol, -$73.88 | 16.8% vol, +$191.89 |

Asia is **not** 68-73% of volume in either window, and carries the largest loss
in **neither**. In the favourable window it is the only profitable block, and
`TOKYO_OPEN` is the single best session in the book (109 trades, 51.38% WR,
+$1820, PF 1.60). The donor figure described a different strategy — unfiltered
`SWEEP_REJECTION` on 1R geometry with a different gate set — and should not
have been carried forward as a live rationale.

**The conclusion survives the correction; the reason does not.** Keep
`Whole_day` off because it is fold-unstable in six of six configurations and
because it cannibalises the kill-zone book — not because Asian hours are bad.
Anyone reasoning from the old rationale would plausibly propose cutting
`TOKYO_OPEN`, which on this evidence would be the single most expensive session
to remove.

### 15.5 Disposition

`allow_whole_day` stays **False**, `--allow-whole-day` stays a plumbing-test
switch. Ledger L-010.

One incidental lead, not acted on: the $100 daily loss limit costs the
kill-zone baseline about $366 on the favourable window (+$3108 without it vs
+$2742 with). That is a single-window observation on a parameter that exists
for tail protection, it inherits L-008's caveat, and it is a separate change
requiring its own fold test. Noted, not proposed.

---


## §18 — VP_LIQUIDITY_REACTION: the capability gap was real, the trigger is not promotable

> Re-run against the corrected detector 2026-08-27. REJECTED, ships disabled
> (`VPLR_ENABLED = False`). Both scopes lose in **both** disjoint windows, which is
> a stronger verdict than the voided campaign reached. Numbers: §18.3.

Ledger L-014. Full design: `docs/DESIGN_VP_LIQUIDITY_REACTION.md`.

### 18.1 The gap was real and is now measured, not inferred

The agent went `ACTIVE -> IDLE | Outside SA session window` at **02:00:12Z** on
2026-08-27 and back to `ACTIVE` at **06:30:18Z**. `TOKYO_OPEN` ends at 02:00 and
`PRE_LONDON` starts at 06:30, so those 4.5 hours are a structural hole rather
than a filter decision — `_scan_symbol` is never called, so **no** trigger runs.
The block was the state machine (`behavior_state.py:89`), not STB, not the
regime whitelist. A trigger-scoped fix therefore had to live in the state
machine; nothing added inside `_scan_symbol` could have reached those hours.

### 18.2 Anchoring a profile causally is possible — this is settled

The stop-condition question was whether a swing-anchored H4 profile can be built
without future data. It can. `StructureEngine._identify_swings` confirms a pivot
only when `hi[i] == max(hi[i-n : i+n+1])` over `i in range(n, len(df)-n)`, so a
pivot is never reported until `n` bars have CLOSED after it. Combined with a
frame that excludes the forming bar, anchor selection is causal by construction.
Asserted by test: the anchor index can never exceed `len(frame) - 1 - lookback`,
and appending violent future bars leaves a past decision bit-identical.

The cost is latency — the leg is recognised `n` H4 bars after its pivot — which
is the honest price of not repainting.

### 18.3 The campaign, re-run against the FIXED detector — REJECTED, and this time the rejection is consistent across disjoint windows

Everything in the previous version of §18.3/§18.4 was produced by the buggy
detector (§18.5) and has been deleted rather than annotated. These are the
replacement numbers.

XAUUSD, $1000 @ 3%, `--spread-pips 2.5`, gate chain exactly as shipped, two
**disjoint** windows. `sumR` is the sizing-neutral total; see §18.4 for why it
has to be reported alongside dollars.

| arm | W1 n | W1 net $ | W1 PF | W1 sumR | W2 n | W2 net $ | W2 PF | W2 sumR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline (VPLR off) | 281 | **+2757.57** | 1.35 | +53.10 | 632 | **-650.02** | 0.91 | -17.99 |
| `--vplr-scope ASIA_ONLY` | 323 | +823.97 | 1.15 | +29.47 | 662 | -781.09 | 0.89 | -29.54 |
| `--vplr-scope ALL_SESSIONS` | 224 | -774.18 | 0.62 | -44.90 | 614 | -771.37 | 0.92 | -26.95 |
| VP-only (`--triggers VP_LIQUIDITY_REACTION`) | 98 | -155.32 | 0.89 | -5.40 | 257 | +323.26 | 1.07 | +13.85 |

W1 = 2026-05-15 .. 2026-08-23, W2 = 2025-08-01 .. 2026-05-14. Raw runs:
`logs/l014b_{w1,w2}_{baseline,asia,all,vponly}.json`.

**Whole-book delta against the arm's own baseline:**

| arm | delta W1 | delta W2 | sign |
|---|---:|---:|---|
| ASIA_ONLY | **-1933.60** | **-131.07** | negative in both |
| ALL_SESSIONS | **-3531.75** | **-121.35** | negative in both |

This is a **stronger** rejection than the void campaign produced, not a weaker
one. There the ASIA_ONLY delta flipped (+252 / -133) and the verdict rested on
a sign instability. Here both scopes lose money in **both disjoint windows** —
the same-sign standard of §13.12, satisfied in the direction of rejection.

**The trigger's own expectancy is not stable.** Per-trade R for
`VP_LIQUIDITY_REACTION` trades only:

| cell | W1 | W2 |
|---|---:|---:|
| inside ASIA_ONLY | -0.2145 R (n=48) | +0.0220 R (n=144) |
| inside ALL_SESSIONS | -0.2283 R (n=144) | -0.0473 R (n=409) |
| standalone, VP-only arm | -0.0551 R (n=98) | +0.0539 R (n=257) |

Two of three cells flip sign across the disjoint windows. The trigger does not
have a measurable edge in either direction; it has a window-dependent one,
which §13.12 says is the same thing as none.

**The VP-only W2 delta of +$973.28 is not a rescue and must not be read as
one.** The W2 baseline is *itself* losing (-$650.02). Replacing a losing
632-trade book with a near-breakeven 257-trade book improves the total without
the replacement having any edge — the standalone arm is +0.0539 R in W2 and
**-0.0551 R in W1**, and its W1 delta is -$2912.89. This is the mirror image of
the void campaign's ALL_SESSIONS result: the delta's sign follows the
baseline's sign, which is a property of the baseline, not of the trigger.

**Survival condition (§13.1) on the VP trades**, which is where the geometry
actually shows up:

| cell | WR | required RRR | realised (avg win / avg loss) | achieved | clears? |
|---|---:|---:|---|---:|:--:|
| W1 ASIA_ONLY | 31.25% | 2.20 | +1.299R / -0.902R | 1.44 | no |
| W1 ALL_SESSIONS | 26.39% | 2.79 | +1.823R / -0.964R | 1.89 | no |
| W1 VP-only | 35.71% | 1.80 | +1.518R / -0.929R | 1.63 | no |
| W2 ASIA_ONLY | 36.81% | 1.72 | +1.696R / -0.953R | 1.78 | just |
| W2 ALL_SESSIONS | 35.45% | 1.82 | +1.590R / -0.947R | 1.68 | no |
| W2 VP-only | 43.58% | 1.29 | +1.328R / -0.930R | 1.43 | yes |

Fails in 4 of 6 cells. Note the shape: the trigger ships TP1 = 2.0R, but the
*realised* average win is 1.30-1.82R because 10-26% of its trades exit on the
timeout rather than at a target — the §13.3 problem, reproduced by a new
trigger. Timeout share: W1 VP-only 20/98, W2 VP-only **67/257**.

**Both scopes fail for different mechanical reasons, and both mechanisms are
already named in this document.**

*ALL_SESSIONS is L-010 cannibalisation.* The trigger detects 1044 (W1) and 2608
(W2) times and sits at the head of the priority order, so it displaces
`SWEEP_REJECTION`: 277 taken -> 80 in W1, 631 -> 205 in W2. One position per
symbol means an extra entry replaces a later one rather than adding to the
book; `SYMBOL_DEDUP` goes 1661 -> 3099 in W2.

*ASIA_ONLY is not cannibalisation and still loses.* It cannot displace anything
in the 02:00-06:30 hole, because nothing else runs there. In W1 it adds 118
trades and loses 76, and its VP trades are -$337.84 at PF 0.65. The trigger is
simply not profitable in the hours it was built to unlock.

### 18.4 Dollar deltas overstate the damage — report R as well

On the 205 bars shared between the W1 baseline and the W1 ASIA_ONLY arm, entry,
stop and exit prices are **identical** and the trigger changed on **zero** of
them, yet the arm books $+1130.46 against the baseline's $+2283.66. Sum R on
those same bars is **39.587 vs 39.583** — equal to rounding.

The whole $1153 gap is compounding. The simulator sizes off the running
balance, so an arm that has lost money earlier takes 0.01 lots where the
baseline took 0.02 on the very same signal. A losing addition therefore charges
twice: once for its own losses, and again by shrinking every subsequent winner.

That is a real cost of a bad trigger, not an artifact to be corrected away, but
it means a dollar delta cannot be read as a statement about trade *selection*.
Every arm above is therefore reported in R as well, and the verdict does not
depend on the dollars: W1 baseline +53.10R -> ASIA_ONLY +29.47R.

**Any future campaign in this repository should report sumR next to net $.**
This was not previously done, and it silently inflated the apparent size of
every gate effect measured so far.

### 18.4a The attribution trap, walked into a FOURTH time

Two independent instances in this single re-run:

* The W2 ALL_SESSIONS arm attributes its `VP_ASIA` session bucket at
  **+$179.40, PF 1.11** — positive, and precisely the bucket that would argue
  for shipping ASIA_ONLY. Running ASIA_ONLY as its own arm puts the same
  session at **-$157.77, PF 0.89**. A $337 miss, sign included.
* The W2 ALL_SESSIONS arm attributes `POC` at **+$1434.24, PF 1.68** — the most
  attractive cell in the entire campaign, and an obvious "ship POC-only"
  candidate. The same bucket in W1 is **-$356.33, PF 0.50**.

After L-005 §12.4, L-011 §16.3 and L-014's own void campaign, these are the
fourth and fifth occurrences. **An attribution bucket is not a forecast of the
arm that isolates it**, because vetoing or admitting a trade frees a position
slot and skips a cooldown, so the gated book is made of different trades. Only
a full re-run measures a trigger.

No sub-mode survives either. POC is negative in all three W1 arms and positive
in all three W2 arms; VAH is positive in W1 ASIA_ONLY and negative in all three
W2 arms; VAL is negative in 5 of 6 cells. Direction is equally unstable —
BULLISH is the worse side in W1 ASIA_ONLY (PF 0.53) and the better side in W2
ASIA_ONLY (PF 1.18). Both directions do fire, and all three levels do produce
trades, in both windows — the engineering claims of §18.6 are re-confirmed by
this run.

### 18.4b The arms are partly different samples — §13.9

`LOT_FLOOR` rejections, which decide which signals the run is even allowed to
admit:

| arm | W1 | W2 |
|---|---:|---:|
| baseline | 12 | 872 |
| ASIA_ONLY | 48 | 1782 |
| ALL_SESSIONS | **976** | 1950 |
| VP-only | 54 | 75 |

The VPLR stop sits beyond the raid extreme plus a buffer, so it is wide, and at
a $30 risk unit a large share of its signals cannot be sized at the 0.01
minimum. **W1 ALL_SESSIONS at 976 against a baseline 12 is not an A/B
comparison** and its -$3531.75 delta should be read as directional only. The
W1 ASIA_ONLY (48) and VP-only (54) arms are close enough to the baseline to
carry weight, and they are the arms the verdict rests on.

The VP-only W2 count of 75 is *below* the baseline's 872 for the opposite
reason: with every other trigger off there are far fewer signals in total.

**A wide stop is the one open engineering lead here.** It is not a tuning knob
— narrowing the stop moves it inside the raid, which is the structural
invalidation the setup is built on — but it does mean the trigger has never
been measured on a risk unit large enough to admit its own signals.
### 18.5 Two defects — found by tracing the operator's setup, and FIXED

Corrected 2026-08-27 after the operator supplied the H4 and M15 charts. The
first version of this section blamed "pool proximity"; that was half of it.
Tracing every M15 bar of the 2026-08-27 Asia reversal through the live detector
found two independent bugs, both of which are correctness defects rather than
tuning choices:

1. **No recency bound on the raid, and depth grew with age.** `detect` took
   `pierced[0]` — the FIRST bar in a 32-bar window to breach the level — and
   measured depth to a running extreme. A level breached hours earlier therefore
   still read as "price just raided it", and scored *deeper* the staler it got.
   Measured: a swing low at 4629.40 scored **4.43xATR** and won every bar from
   02:00 to 06:00. Fixed: `pierced[-1]`, plus `VPLR_RAID_MAX_AGE_BARS = 8`
   (2h on M15). Depths on the same bars are now 0.2-0.7xATR.
2. **Fixed POC->VAH->VAL order with first-match, and pools sorted by distance
   to the level.** Neither says anything about which liquidity event is driving
   price. The equal highs at **4625.14** — the BSL that was actually raided,
   pierced at 01:30-01:45 and swept to 4643.19 — sat 3.3 from the POC and lost
   to a swing low 1.02 away. Fixed: enumerate every (level, pool) coincidence
   carrying a recent raid, then rank by **raid recency, then depth**.

**Effect on the reference setup.** Before: BULLISH on every bar, flipping
BEARISH only at 06:15 with price already at 4605 — the move over. After:
BEARISH from **04:30**, correctly attributed to `EQUAL_HIGHS 4625.14`, with
price at ~4620 and the move running to 4594.53.

It is still BULLISH from 02:00 to 03:45. That is not obviously wrong: the 01:45
bar sweeps BOTH sides (high 4641.16 above the equal highs, low 4623.59 below the
swing low), so both candidates are age 0 and the M5 CHoCH had not yet completed.
Forcing an earlier entry would mean fitting to this one trade, which §18 exists
to avoid.

**The campaign was re-run against the fixed detector on 2026-08-27 and §18.3
now carries those numbers.** The void figures have been deleted rather than
annotated, so nothing in this document quotes the buggy detector any more. For
the record of what changed: the buggy ASIA_ONLY arm read +$3009.29 in W1, the
fixed one reads **+$823.97** against a +$2757.57 baseline. The bug fix removed
the entire apparent gain, and the rejection became fold-consistent across both
disjoint windows instead of sign-unstable.

The engineering conclusions in §18.1, §18.2 and §18.6 were unaffected by the
bug and are re-confirmed by the re-run.

### 18.6 What is nonetheless established

Engineering, verified rather than asserted:

* disabled mode reproduces the baseline **byte-for-byte** — 281 trades,
  +$2757.57, identical trade list;
* the trigger fires end-to-end in the previously unreachable hours, with the
  full evidence chain recorded
  (`VP_ASIA POC EQUAL_LOWS DISPLACEMENT+RECLAIM+FVG+REJECTION_WICK+HTF_STRUCTURE`);
* POC, VAH and VAL all produce trades in both windows, and both directions fire;
* a bare touch cannot fire and cannot consume the bar — asserted by test;
* live and simulator run one code path, asserted by parity test.

### 18.7 Spread sensitivity could not be exercised on these windows

Re-running W1 at a 5.0-pip floor produced **byte-identical** results to 2.5. The
broker archive carries real per-bar spread for 2025-08 onward, so
`--spread-pips` is a fallback that never engages here (§13.12: the field is
zeroed only 2020-03 -> 2025-03). Testing the trigger's cost sensitivity needs a
window inside the zeroed range, which is also a window where every result is a
function of the assumed floor.

## Source index

- [MQL5 art. 18991 — Position Sizing](https://www.mql5.com/en/articles/18991)
- [MQL5 art. 19141 — Minimum Risk Levels](https://www.mql5.com/en/articles/19141)
- [MQL5 art. 19211 — Random Exits and Expectancy](https://www.mql5.com/en/articles/19211)
- [MQL5 art. 20569 — Liquidity Sweep on BoS](https://www.mql5.com/en/articles/20569)
- [MQL5 art. 16340 — SMC: OB, BOS, FVG](https://www.mql5.com/en/articles/16340)
- [MQL5 art. 20851 — EMA high/low channel + EMA50 slope](https://www.mql5.com/en/articles/20851)
- [MQL5 art. 21133 — Trend filters on a liquidity strategy](https://www.mql5.com/en/articles/21133)
- [MQL5 blog 770285 — Loss Streak Protection](https://www.mql5.com/en/blogs/post/770285)
- Donor repository research journal — `D:\Hermes Quant\GPTMain\docs\SCALPER_RESEARCH_JOURNAL.md`


---

## §16 — PDH/PDL: the premise is right, the remedy is wrong, the mechanism is inverted

Opened on an operator hypothesis (2026-08-26): *"until you keep track of
PDH/PDL you will keep taking trades on the wrong side of the market. NY session
today is a great example, and the current open position is on the wrong side."*

Ledger L-011. Three claims, tested separately, because they have different
answers.

### 16.1 The premise is confirmed — no daily level reaches a decision

Verified by code read, not inference:

- `core/liquidity_engine._prev_day_hl` (l.187) computes PDH/PDL correctly and
  `analyze()` publishes them as `LiquidityMap.prev_day_high/low` plus two
  pools at strength 0.92 — the highest in the map.
- `sa_consultant.analyse()` (l.149) runs that engine, then keeps exactly three
  fields: `price_zone`, `nearest_bsl`, `nearest_ssl`. `SAConsultResult` has
  **no PDH/PDL field**. The two pools it does keep feed Gate 3 — the TP2
  realignment — and nothing else. They never touch admission or direction.
- The trigger's own liquidity pass, `trigger_engine.step1_liquidity`, runs on
  **M5** over the last 50 bars and derives `session_high/low` from **today's
  bars only**. `MicroLiquidity.nearest_bsl/ssl` are built from that set, so a
  daily level cannot reach a trigger even in principle.

Net: PDH/PDL is computed and discarded on every scan. The gap is real.

### 16.2 The remedy is rejected — every PDH/PDL veto makes the book worse

`scalper/pdr_gate.py`, three modes, mirrored into the simulator in the same
change (invariant #2), 29 tests. Control reproduces the standing baseline
exactly (280 trades, +$2742.35), so the added D1 fetch is inert when off.

| window | arm | n | WR% | net $ | PF | vetoes | LOT_FLOOR | folds |
|---|---|---:|---:|---:|---:|---:|---:|---|
| recent | control        | 280 | 46.07 | +2742.35 | 1.35 |   0 |  13 | + + + |
| recent | SHORT_DISCOUNT | 279 | 45.16 | +1937.66 | 1.29 |  49 |  23 | + + + |
| recent | LONG_PREMIUM   | 290 | 45.17 | +2028.20 | 1.27 |  80 |  13 | + **-** + |
| recent | SYMMETRIC      | 282 | 46.10 | +2489.53 | 1.37 | 138 |  27 | + + + |
| prior  | control        | 625 | 35.52 |  -656.96 | 0.91 |   0 | 914 | - - - |
| prior  | SHORT_DISCOUNT | 622 | 35.69 |  -612.21 | 0.92 | 192 | 807 | - - - |
| prior  | LONG_PREMIUM   | 620 | 35.16 |  -611.15 | 0.92 | 320 | 747 | - - - |
| prior  | SYMMETRIC      | 647 | 35.55 |  -521.90 | 0.93 | 505 | 584 | - - **+** |

Every mode costs money where the strategy works and returns almost nothing
where it does not. Two fail §13.5 outright. `SHORT_DISCOUNT` — the operator's
literal rule — keeps its sign in both windows and is still rejected on P&L:
-$805 in the profitable window buys +$45 in the unprofitable one.

Two other operationalizations died earlier, at the attribution stage:

- **proximity to the opposing unswept pool (<1R)**: PF 1.31 recent vs 0.44
  prior — flips, and every sub-bucket is under the n=20 floor;
- **alignment with a formed daily sweep bias** (PDL swept and reclaimed ⇒
  bullish): OPPOSES PF 1.33 vs AGREES 1.31 in the recent window, 0.90 vs 0.98
  in the prior. No signal in either.

### 16.3 §13.11's trap, walked into again — and why the re-run is not optional

Attribution of the 905-trade book said `PD_PREMIUM`/`BULLISH` was the one cell
holding a negative sign in both windows (PF 0.60 recent, 0.86 prior), worth
-$463 and -$129. Vetoing it should therefore have *added* that much.

The `LONG_PREMIUM` arm **lost $714** in the recent window. A $1,177 miss in
both sign and magnitude.

The mechanism is visible in the trade counts: two arms produced **more** trades
than the control (280 → 290, 625 → 647) while vetoing 80 and 505 signals. A
veto frees a position slot (`SYMBOL_DEDUP`) and skips a cooldown, so the gated
book is made of different trades than the bucket the rule was read off. This is
the second campaign to fail this way after L-005. **An attribution bucket is a
description of trades that happened; it is not a forecast of a filter.**

### 16.4 The mechanism is inverted — the "wrong side" bucket is the best bucket

The hypothesis says entering just before an unswept daily pool is the error.
Measured directly — trades entered against an unswept pool within `thr` R,
where that pool was then swept during the trade's life:

| pool within | window | n | WR | net $ | PF |
|---|---|---:|---:|---:|---:|
| 0.5R | recent | 12 | 58.3% | +324.65 | 2.14 |
| 0.5R | prior  | 12 | 58.3% |  +90.60 | 1.77 |
| 1.0R | recent | 20 | 65.0% | +669.92 | 2.88 |
| 1.0R | prior  | 18 | 50.0% |  +83.68 | 1.48 |

Book PF is 1.35 and 0.91. **Four of four beat it**, in both windows, at both
thresholds. Selling just above an unswept PDL is not the bot's mistake; on this
evidence it is the bot's best setup, because the sweep travels in the trade's
favour first.

The pattern is also rare — 1.3% to 10% of trades depending on threshold — so it
cannot be what drives the book in either direction.

### 16.5 The prompting trade, re-read

Ticket 494125751, XAUUSD SELL 0.01 @ 4611.38, opened 2026-08-26 13:00:10 UTC,
SL 4635.377, TP 4571.912.

- PDL (25 Aug low) = 4605.33 — **0.25R below entry, unswept**.
- The next M15 bar (13:15) printed **4598.10**, sweeping PDL by $7.23, closed
  back above at 4611.19, then ran to 4629.31.
- MFE **+0.55R**, MAE 0.93R.

So the trade *did* go the way the entry argued for. It is not on the wrong
side. It is a right-side trade whose target sat **1.64R** away while the sweep
delivered 0.55R — and 1.64R is itself a defect, not a design choice (§16.6).

The vivid detail that makes this look like a PDH/PDL failure — "it sold right
above PDL and PDL then got swept" — is, per §16.4, the signature of the book's
*best* bucket. One trade cannot distinguish a defect from a coincidence
(§13.11), and this one is not even a loss yet.

### 16.6 What the investigation actually found — ledger L-012

Backing the intended geometry out of the SL/TP shows the signal price was
4614.222 and the fill was 4611.380: **2.842 of adverse slippage**, which turns
an intended 2.00R into a realized **1.64R**. `_execute` sends
`trigger.stop_loss` / `trigger.tp1` as absolute prices computed from
`trigger.entry_price` and never re-anchors them to the fill
(`scalper_agent.py` l.920-921); sizing uses the same signal price
(l.812, l.1360).

11 post-migration live trades reconciled against broker fills:

- 8 of 11 filled adversely;
- median realized R:R **1.852** against an intended 2.000;
- worst case #494039170: +4.310 slippage → **0.54R**.

§13.1 demands RRR > 1.81 at the prior window's 35.53% win rate. The median
realized geometry sits *on* that line and several trades fall well below it.

Note what this does to every number above and everywhere else in this file:
**the simulator fills at the signal price by construction**, so every backtest
in this repository assumes a perfect fill, and live is structurally worse than
sim by this margin. No existing fold test can see it. n=11 is far below
anything §13.5 acts on, and the fix moves a stop — L-003 applies. Left open.

### 16.7 What this does not say

- It does not say daily levels are useless. It says a **veto** built on them
  is, and that the three cheap operationalizations tested have no stable edge.
- PDH/PDL as a **signal** — a pool `SWEEP_REJECTION` is permitted to fade — is
  untested. `step1_liquidity` selects the *nearest* pool, so merely adding
  daily levels to the candidate set would rarely change a decision; a real test
  needs a strength-ranked pool set. Own row when someone takes it up.
- L-008 still stands: two windows are two windows.


## §17 — L-008 closed: nine years, 17 windows, and a book centred on zero

Run 2026-08-27. XAUUSD, 17 disjoint 6-month windows 2018-03 -> 2026-08,
`--pool 1000 --risk 0.03 --loss-limit 100.0`, gate chain exactly as shipped
(every optional gate off). One process, one MT5 attach, `run_backtest` called
per window; fold signs from `analyze_walkforward.folds()` on each window's own
trade sequence. Raw: `apex_ai/logs/l008_window_audit.json` (spread floor 2.5)
and `l008_window_audit_sp5.json` (floor 5.0).

### The table (floor 2.5 / floor 5.0)

| window | n | WR% | net @2.5 | net @5.0 | PF@5 | LOT_FLOOR | folds@5 | req RRR |
|---|---:|---:|---:|---:|---:|---:|:--|---:|
| 2018-03..09 | 0 | - | 0.00 | 0.00 | - | 0 | n/a | - |
| 2018-09..2019-03 | 0 | - | 0.00 | 0.00 | - | 0 | n/a | - |
| 2019-03..09 | 0 | - | 0.00 | 0.00 | - | 0 | n/a | - |
| 2019-09..2020-03 | 77 | 53.25 | +919.29 | +904.30 | 2.05 | 2 | +++ | 0.88 |
| 2020-03..09 | 311 | 39.55 | -268.04 | -260.01 | 0.94 | 4 | -+- | 1.53 |
| 2020-09..2021-03 | 380 | 39.21 | -371.97 | -372.91 | 0.92 | 1 | -+- | 1.55 |
| 2021-03..09 | 300 | 43.67 | +230.54 | +129.24 | 1.02 | 1 | ++- | 1.29 |
| 2021-09..2022-03 | 237 | 44.73 | +1238.79 | +1186.59 | 1.23 | 0 | +++ | 1.24 |
| 2022-03..09 | 363 | 41.60 | +798.03 | +516.22 | 1.09 | 0 | +-+ | 1.40 |
| 2022-09..2023-03 | 309 | 40.78 | -91.08 | -172.48 | 0.96 | 0 | --- | 1.45 |
| 2023-03..09 | 268 | 39.18 | -95.19 | -160.68 | 0.96 | 0 | ++- | 1.55 |
| 2023-09..2024-03 | 230 | 43.04 | -68.43 | -79.48 | 0.97 | 3 | -++ | 1.32 |
| 2024-03..09 | 408 | 38.48 | -399.68 | -419.35 | 0.94 | 0 | +-- | 1.60 |
| 2024-09..2025-03 | 405 | 41.98 | +718.39 | +394.36 | 1.04 | 0 | +-+ | 1.38 |
| 2025-03..09 | 443 | 39.73 | +393.08 | +112.09 | 1.01 | 2 | +-- | 1.52 |
| 2025-09..2026-03 | 455 | 35.16 | -642.14 | -680.30 | 0.87 | **490** | --- | 1.84 |
| 2026-03..08-27 | 494 | 41.70 | +1184.89 | +1184.86 | 1.14 | **153** | -++ | 1.40 |

**Totals @5.0 pips: 4694 trades, 40.48% pooled WR, net +$2282, pooled PF
1.0289, 7 windows positive / 7 negative, median window +$16.30.**

### The three questions L-008 was opened to answer

**1. Is +$2742 an outlier, or modal? Neither — the distribution is centred on
zero.** PF sits between 0.87 and 1.23 in 13 of 14 trade-bearing windows; the
median window earns $16. +$2742 and -$657 are both tails of a near-zero-mean
process. This is a worse finding than "unrepresentative sample": **every gate
decision in §§9-13 was judged by a few hundred dollars of net P&L on one draw
from a distribution whose centre is nothing.** Those REJECTED verdicts were
measuring noise, and none of them should be read as having established that the
gate is harmful — only that it did not rescue one window.

**2. Fold-consistency carries no cross-regime information. Confirmed.** Only
4 of 14 windows are fold-consistent at floor 5.0 (2 all-positive, 2
all-negative) — the criterion fires 29% of the time, so most windows would fail
it whatever their sign, and the ones that pass split +$904 / +$1187 / -$172 /
-$680. §13.5's 60/20/20 is a *within-window stability* test being read as an
out-of-sample claim. It is not one. Three folds inside a 100-day window is a
weaker statement than it appears.

**3. The survival condition holds everywhere — and that is the problem.**
Required RRR `(1-WR)/WR` is **below the shipped 2.0 in 14 of 14 windows**
(range 0.88-1.84, median 1.43). §13.1's geometry is not the binding constraint
anywhere in nine years. Yet realized expectancy is **+0.016R** against a naive
+0.214R implied by WR 40.48% at RRR 2.0. **93% of the theoretical edge is
consumed between signal and settlement** — by timeouts, EOD flattens, the
cooldown, and costs — not by the win-rate/RRR relationship. Further work on TP1
geometry aims at the one component the data says is fine.

### Two data facts that change how the table reads

**The 2018-2019 zero-trade windows are a cost result, not a data gap.** Bars
exist (M5 5221 in 2018-03) and triggers fire (3379, 3272, 3326 detections). All
of it dies at `STEP3`: the broker's archived spread for 2018-19 is 111-145
points (11.1-14.5 pips), and the net-R gate correctly rejects every setup. The
strategy is not tradeable at 2018 retail gold spreads. Do not "fix" this.

**The broker's spread field is zeroed for 2020-03 through 2025-03.** Median
exactly 0 in every March probed; 111/145 in 2018/2019 and 57 in 2026-03 (which
matches the live `symbol_info().spread` of 50 and the 4.78-pip measurement in
§13.4). `_spread_series` therefore substituted the `--spread-pips` floor across
11 of the 14 trade-bearing windows. Raising that floor 2.5 -> 5.0 costs
**$1264, 36% of the headline**, and the two windows carrying a real spread
field barely move (2026-03: -$0.03; 2019-09: -$14.99) — which confirms the
mechanism rather than merely its size. Relative spread compressed ~8x over the
period (8.4bp in 2018 -> 1.1bp in 2026), so 5.0 pips is a defensible and
probably still-optimistic estimate for the middle years. **Any future
multi-year window work must state its spread floor; the archive cannot supply
one.**

**`LOT_FLOOR` is not comparable across the table (§13.9).** 0-4 in every window
before 2025-09, then **490 and 153**. Gold running $3000 -> $5000 against a
fixed $30 risk unit pushed a large share of signals under the 0.01 broker
minimum. The last two windows — including the +$2742 reference — admit a
materially different sample than the other twelve.

### What kills it: L-012, not the gates

Pooled gross gains $81,265 against gross losses $78,982 — PF 1.0289, a 2.9%
margin. L-012 measured live fills turning an intended 2.000R into a median
1.852R. Applying that haircut to the reward leg alone, **holding the loss leg
constant even though L-012 says stops widen too**:

    gains $81,265 x 0.926 = $75,251   vs   losses $78,982
    -> net -$3,731, PF 0.9528

**The entire nine-year book is thinner than its own measured slippage.** No gate
in §§9-13 is large enough to matter against that, in either direction. L-012 is
not a documentation footnote; on this evidence it is the binding constraint on
the strategy.


## §18 — L-013 S1: modelling the entry fill, and why it changed nothing

Implemented and measured 2026-08-27. `_entry_fill_price` in
`backtest_scalper.py`; `FILL_AT_NEXT_BAR_OPEN = True`, disable with
`--fill-signal-price`. Nine new tests in `tests/test_backtest_fidelity.py`
(suite 270 -> 279).

### What changed

P&L is booked against the open of the first M5 bar at or after the decision
instant instead of `trigger.entry_price`. `SimTrade` now carries both `entry`
(the fill) and `signal_price`, so slippage is measurable after a run rather
than inferred.

Deliberately unchanged, and pinned by tests: **SL/TP stay anchored to the
signal price** (that is what live does — re-anchoring is L-012's remedy, which
is exit-side and blocked by L-003), and **sizing stays on the signal price**
(live does too; fixing it in the simulator alone would open a fresh §13.4
divergence).

### The numbers

| | perfect fill | S1 |
|---|---:|---:|
| baseline window trades | 280 | 281 |
| baseline window net | +$2742.35 | +$2757.57 |
| baseline window PF | 1.3495 | 1.3475 |
| nine-year net (14 windows, 5.0-pip floor) | +$2282.45 | +$2570.90 |
| windows positive / negative | 7 / 7 | 7 / 7 |
| fold-consistent windows | 4 | 4 |
| windows flipping sign | — | **0** |

The +$288 aggregate is not a systematic effect. **12 of 14 windows move by
under $40**; two move +$294 and +$88, and those are balance-feedback path
divergence — P&L feeds `balance` feeds `risk_usd` feeds sizing and the
daily-loss gate, so the arm and its control take partly different trades. This
is the third occurrence of that trap after L-005 and L-011: **a change that
alters P&L alters which trades come later, so before/after is never
like-for-like.**

### The result that matters: S1 is not a slippage model

The row's own calibration check settles it. S1's TP exits book a median
**1.997R**; L-012's broker-reconciled live fills gave **1.852R** against an
intended 2.000R. Modelled median slippage is **$0.048**, against live cases of
$2.84 and $4.31 — 50-90x larger. Adverse-fill rate 54.8% modelled vs 8 of 11
live.

**Why, mechanically:** `trigger.entry_price` is `df_m5['close'].iloc[-1]`
(`scalper/trigger_engine.py` l.184) — the last closed M5 bar's close — and the
simulator's decision instant is that same bar boundary. The next bar's open is
the immediately following tick. S1 therefore measures tick-boundary noise, and
**bar granularity is eliminated as the explanation for L-012.** No finer entry
timing will produce the live gap.

That leaves three candidates, and one of them is new:

- **S2, spread sidedness.** Currently `cost` is deducted after the fact while
  both fills happen at mid, so spread changes what an exit *costs* but never
  which exit *fires*. That channel is absent entirely.
- **S3, gap-through exits.**
- **S4, scan latency — not anticipated when L-013 was opened.** The live agent
  scans on a 30-second interval and sends a market order, so it acts 0-30s
  after the bar the simulator decides on, and fills at the ask. This is the
  only remaining candidate whose *scale* matches the measured gap, and it
  should be built before S2.

### What S1 is still worth

It closed a real internal incoherence. `_schedule_exit` has always scanned from
the entry bar "because the position is opened at that bar's open" while P&L was
booked at the signal price — the simulator disagreed with itself about when the
fill happened. That is fixed, it costs nothing, and it makes `signal_price`
available for every future fill-model stage to calibrate against.
