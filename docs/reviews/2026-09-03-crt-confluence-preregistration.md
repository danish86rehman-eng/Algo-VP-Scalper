# CRT external-raid confluence experiment — L-020

Frozen before new outcome inspection: **2026-09-03 05:32 UTC**.
Operator request: research MQL5/vault and strengthen HTF CRT external-liquidity raids with measured confirmations. This is a development experiment, not a profitability or funded-trading claim.

## Fixed hypotheses (three arms, no parameter search)

Baseline OBSERVE: existing D1/W1/MN1 CRT trigger and all existing entry/exit controls; calculate the following labels without filtering.

A1 MSS: the most recent strict M15 opposing swing with three completed bars on each side **known before the raid opens** is frozen. After that raid, the same M15 displacement candle that generates the entry FVG must close beyond that swing by the already-frozen raid buffer. A wick beyond it is insufficient. Missing swing history fails the additional check. SELL mirrors BUY.

A2 MSS_RETEST: A1 plus a later completed M5 candle overlapping the FVG, closing directionally inside the gap and on the buffered reclaimed side. It must close after formation and after the MSS. Its confirmation expires after one M5 bar (five minutes). The executable quote must still satisfy the existing gap, reclaimed-level, no-chase and >=2R geometry. No entry at the historical low is assumed.

ATR/displacement already exist. No RSI, ADX, moving-average, tick-volume or POC threshold will be tuned in this experiment. POC is not exchange-wide gold order flow; a matched profile definition/feed is required before evaluating it.

## Data and comparison

- XAUUSD, Exness historical broker bars, **2026-06-01 00:00 UTC <= time < 2026-09-01 00:00 UTC**, fixed 92-day window, native calendar HTF bars and existing warm-up. No orders; monkey-patch order submission to raise.
- Run the shared full decision path for all three arms with the existing full trigger priority, sessions/news cache, Guardian exits, pool $1,000, risk 3%, daily limit $100, cooldown and one open position. Strict CRT rejection can allow a lower-priority candidate exactly as live does. Attribute CRT separately from whole-bot operational results.
- Development stability partitions: first 60%, next 20%, last 20% of elapsed time, assigned by entry time. This history has already been exposed in vault studies and is **not untouched OOS**.
- Baseline trade labels measure selection separately from independently rerun operational results; log all pre-entry ready candidates and their labels, including subsequent gate failures.
- Use vault's measured 0.35 USD/oz round-trip mean (spread + commission + slippage combined) as the execution debit, replacing the old spread/commission debit, never adding twice. The cost affects realized balance, loss limits and cooldown. Secondary fixed 0.17/0.75 cost overlays are sensitivity summaries, not extra optimized arms. Historical observed bar spread still gates entries; missing spread uses measured 0.06 USD/oz. Bar fills and cached news remain approximations, disclosed with results.
- Report trades, CRT count, gate funnel, gross/net R, P&L, PF, fold signs and costs. Report lack of candidates rather than relaxing filters to create a sample. When comparing labels, check gross R and inverse initial stop distance to distinguish selection from denominator/cost effects.
- If both label groups have at least 30 baseline CRT trades: fixed seed 20260903, 2,000 circular-shift/block-permutation draws, block size five consecutive trades; report tail truncation and plus-one p-value. Otherwise mark selection evidence insufficient. These minimum counts permit a diagnostic, not an assertion of statistical power or proof. Two candidate comparisons require multiplicity control (Bonferroni .025).

## Deployment decision

OBSERVE is the deployment default: collect both confirmations with actual candidates/trades. No strict gate is promoted from screenshots, source-author claims, this already-used window or a tiny favorable subset. A strict mode is executable for reproducible research. Any later activation needs a separately recorded operator contract or prospective evidence; failed/underpowered results stay failed/underpowered.

## Primary design/method sources

- [Optimizing Liquidity Raids: raids versus market-structure shifts](https://www.mql5.com/en/articles/21212): motivates separating an external sweep from the subsequent structural reversal; its example is not XAUUSD incremental evidence.
- [Turtle Soup strategy implementation](https://www.mql5.com/en/articles/23155): closed-back-inside range raids with explicit age/depth and duplicate rules; related to existing sweep logic, not an independent proven edge.
- [Does This Entry Filter Really Add Edge?](https://www.mql5.com/en/articles/23665): distinguishes accepted/rejected baseline-trade selection from the operational effect of rerunning a filtered strategy; motivates dependence-aware placebo comparisons.
- Vault FS-4 and FS-5: H4/D1 CRT results do not validate this different operator-defined D/W/M rule; 96 indicator cells produced no validated incremental feature after adjudication. Do not erase or reinterpret those results.
