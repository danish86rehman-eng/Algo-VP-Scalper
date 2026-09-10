# External-liquidity-raid trigger research

Reviewed 3 September 2026 for the operator's XAUUSD HTF CRT request. These are source-authored, inspectable implementations, not certified profitable systems. Report images were opened and read rather than treating missing extracted text as missing published numbers.

| Candidate | Mechanical contribution | Published evidence inspected | Decision for APEX |
|---|---|---|---|
| [Turtle Soup, Part 50](https://www.mql5.com/en/articles/23155), 10 July 2026 | Range raid, reference age/depth, closed return inside and duplicate control | Report: 110 trades, PF 1.33, wins 56.36%, relative equity DD 25.96%; no independent XAUUSD replication here | Substantially overlaps existing sweep/CRT logic. Borrow explicit event ordering, not its reported profitability. |
| [Quasimodo, Part 49](https://www.mql5.com/en/articles/23141), 1 July 2026 | Sweep the prior extreme, break the opposing leg, then return to the shoulder | Report: **10 trades**, PF 3.28, wins 60%, relative equity DD 31.16% | Too few trades to establish an edge. Explicit structural confirmation informs our separate MSS check; a full QM trigger is not added. |
| [Inverse FVG](https://www.mql5.com/en/articles/16659), 28 January 2025 | A failed gap changes role after a close through its far edge; source adds MA400, rejection and size filters | NAS100 M3, January 2020–December 2024; report **484 trades**, PF **1.27**, wins **46.49%**, equity relative DD **0.32%**, 456 longs/28 shorts | More quantitative evidence than a screenshot example, but a different instrument and execution/exit system. Not validated for this XAUUSD scalper. Existing vault note is supplemented with the report numbers. |
| [tCISD + SSMT, Part 52](https://www.mql5.com/en/articles/23549), August 2026 | Time-aligned gold/silver sweep disagreement, opposing-candle-open confirmation and optional retest; New York clock/DST | Report: 220 trades, PF 1.51, wins 85%, relative equity DD 2.99%; average winner 13.29 versus average loser -49.78. The report image does not identify its test dates/settings | Useful next independent data dimension, but no isolated contribution or replication yet. Do not combine it with the current two-arm confluence experiment or assume gold/silver divergence guarantees reversal. |
| [Liquidity raids versus MSS](https://www.mql5.com/en/articles/21212), 15 February 2026 | Distinguishes continuation/pullback raids from structural reversals after external liquidity | Author's GBPJPY H1 example is not an incremental XAUUSD study | Motivates requiring the FVG-producing displacement to close through a previously known opposing M15 swing. Implemented as an observable, separately testable condition. |
| [Sweep with MA filter](https://www.mql5.com/en/articles/18379), 11 June 2025 | Optional trend/candle-colour constraints on a sweep | Visual examples, no controlled incremental result in inspected material | No blanket MA filter activated. Relevant vault indicator comparisons have already failed to establish improvement. |

Source report images are archived in the repository's `docs/reviews/2026-09-03-crt-confluence-evidence/` with their article numbers. These published numbers are not broker-reconciled APEX results, not comparable returns at the same risk, and not walk-forward replications. In particular, a high hit rate with much larger losses can still be fragile.

## What the vault already establishes

- `indicator-confluence-results` (FS-5): 96 predeclared cells on 2,707,751 Exness M1 bars through 1 September 2026; adjudicated **0 useful, 15 confounded, 81 no incremental value**. Apparent Bollinger-width gains largely reflected cost divided by stop distance. This rules out claiming success for those tested cells; it does not mathematically prove every possible future confluence is useless.
- `crt-htf-ict-smc-results` (FS-4): H4 baseline rejected, D1 inconclusive; external-raid plus H1 MSS/displacement subset had only **16 trades in 7.7 years**, with a confidence interval spanning losses and gains. The present D/W/M→M15 specification is different, so those results neither validate nor automatically falsify it.
- `stop-clusters-liquidity-cascades-sweep-vs-acceptance`: stops can fuel continuation as well as reversals. Candle patterns alone do not identify actual institutional orders. Its own aggressiveness study found stop-width confounding and insufficient out-of-sample support; no sweep-depth/ATR filter is added as a claimed edge.
- `pdl-sweep-reclaim`: rejection of the earlier proximity veto must remain distinct from testing a new sweep/reclaim entry. Its history is retained.

## Implemented experiment

The frozen [L-020 preregistration](2026-09-03-crt-confluence-preregistration.md) specifies only two additional conditions: A1 M15 MSS and A2 MSS plus fresh M5 FVG-return rejection. ATR/displacement, native higher-timeframe liquidity, costs, target distance, sessions and risk already contribute to the baseline. The same evaluator is used by live and replay. Default **OBSERVE** records evidence without turning an unvalidated condition into an entry veto.

The evaluation separates baseline-trade selection from full-system operational effects, following the methodological distinction in [Does This Entry Filter Really Add Edge?](https://www.mql5.com/en/articles/23665), 25 August 2026. A filtered rerun can change occupancy, lower-priority entries, cooldown and sizing; a favorable subset alone does not measure those effects. Dependence-aware placebo tests are preregistered only when both label groups have enough observations for a diagnostic. The previously used development window cannot establish untouched out-of-sample performance.

## Next candidates, kept outside this experiment

1. **SMT paired with the existing external-raid event**, as an observation first: obtain synchronized completed XAUUSD/XAGUSD bars, declare one comparison anchor and divergence definition, validate missing-bar/DST handling and isolate SMT from the source's other filters. No pair selection or quarterly timing search after seeing P&L.
2. **IFVG role reversal** as a separate source of entry candidates: declare formation, invalidation close, return, expiry and one-use state. Test with APEX's actual Guardian and costs rather than importing the NAS100 MA400 result.
3. **VP/POC location** only after locking the source timeframe, profile anchors, bins, feed and tick-volume interpretation. An overhead POC alone remains insufficient to authorize a sell.

These candidates are catalogued, not enabled or presented as successful strategies. Any further experiment must receive its own frozen specification and hypothesis count.
