# HTF CRT external-raid confluences — L-020 results

**3 September 2026. Verdict: implemented and software-validated; incremental trading edge unestablished. Default remains OBSERVE.**

The operator asked for authentic MQL5/vault triggers and additional confirmations that make the HTF CRT external-liquidity-raid model work. The implementation now distinguishes a raid and range reclaim from an explicit opposing-swing break and a fresh, completed FVG-return rejection. The historical comparison supplies no eligible CRT population on which to establish an improvement. It would be misleading to describe these additions as profitable or successful trading filters.

## Exact programmed sequence

The existing first-priority CRT source uses the prior completed MN1/W1/D1 range, an M15 raid from inside it, displacement closing back inside, a three-candle M15 FVG produced by that displacement, and a later executable in-gap quote. Stops remain beyond the raid; targets remain capped by the nearest permissible range/structural objective, with >=2R and existing cost floors. Existing broken-support reclaim, sessions, news, bias/regime, cooldown and risk rules remain binding.

Two extra conditions are calculated in `scalper/crt_confluence.py`:

1. **MSS:** freeze the latest strict opposing M15 swing whose three right-hand confirmation candles all closed before the raid opened. The FVG-producing displacement must close past that level by the frozen raid buffer. A wick does not qualify; missing pre-raid swing evidence fails.
2. **MSS_RETEST:** additionally require a later completed M5 candle overlapping the gap, closing directionally inside it and beyond the buffered reclaimed level. Its confirmation lasts less than five minutes. The final executable quote must still satisfy gap, reclaim, no-chase and 2R conditions; expiry is rechecked immediately before submission. SELL mirrors BUY.

`--crt-confluence-mode OBSERVE` records both checks without vetoing; `MSS` and `MSS_RETEST` enforce their respective conditions for reproducible experiments. Both live and backtest call the same detector. A strict rejection can permit another existing trigger through normal priority; it is not a blanket ban on every source of entry. Startup, eligible candidates and CRT trade metadata record the mode/evidence.

## Fixed comparison

Definitions were saved to the repo and vault at **05:32 UTC before outcome inspection**: [preregistration](2026-09-03-crt-confluence-preregistration.md). No threshold search or additional strategy arms followed.

XAUUSD, **1 June through 31 August 2026**, native broker M5/M15/H1/H4/D1/W1/MN1 data, shared full decision path, actual current trigger priority, Guardian simulation, $1,000 pool, 3% risk, $100 daily loss limit and existing entry sessions/cooldown. Cost is the vault's measured **0.35 USD/oz total round trip**, replacing the old spread/commission debit. Historical bar spread still gates entries; missing spread uses measured 0.06 USD/oz. All order submission was prohibited by a raising stub.

| Arm | Whole-bot trades | Eligible CRT candidates / trades | Net simulated P&L | Sum net R | PF |
|---|---:|---:|---:|---:|---:|
| Existing CRT + OBSERVE labels | 139 | 0 / 0 | +$91.48 | +6.768 | 1.040 |
| Require MSS | 139 | 0 / 0 | +$91.48 | +6.768 | 1.040 |
| Require MSS + M5 rejection | 139 | 0 / 0 | +$91.48 | +6.768 | 1.040 |

All executed simulated trades came from other families: **130 SWEEP_REJECTION, 9 FVG_FILL**. The three arms are identical because no baseline CRT candidate reached eligibility. Zero CRT opportunities means **insufficient evidence**, not proof the confirmations are harmful or useless. Accepted/rejected CRT selection tests and permutation p-values are deliberately not reported from an empty sample.

The whole-bot aggregate is fragile: gross +$266.83, costs $175.35, net +$91.48. Mean gross R is +0.0869; mean net R +0.0487. The fixed 60/20/20 elapsed-time partitions give:

| Development period, UTC | Trades | Net P&L | Sum net R |
|---|---:|---:|---:|
| 1 June–26 July 04:48 | 86 | +$171.87 | +7.023 |
| 26 July 04:48–13 August 14:24 | 23 | -$276.15 | -6.955 |
| 13 August 14:24–1 September | 30 | +$195.76 | +6.700 |

Signs are inconsistent. Fixed-trade cost sensitivity gives +$181.66 at 0.17 USD/oz and **-$108.92 at 0.75 USD/oz**. Those overlays do not rerun altered cooldown/occupancy/sizing and are not additional operational arms. This already-used history is development evidence, never untouched OOS.

## Why CRT did not enter

The detector's repeated in-session watch states include 1,758 waiting for displacement, 576 reclaims without a qualifying FVG, 376 waiting for the executable FVG return, 249 invalidated gaps, 3,114 expired setups and **3 in-gap observations rejected for insufficient target R**. These are repeated per-frame/direction/time observations, **not independent trades or a sequential funnel**. Another 2,043 states report missing continuous history; weekend/session data gaps remain rejected rather than being treated as continuous candles. The localization artifact records the three near-entry quotes and their actual stop/target geometry.

New confirmations cannot improve the selection of a zero-entry CRT population. Tightening RSI, ADX, MA, sweep depth or POC requirements after seeing these results would not establish success. Existing geometry/session constraints were not relaxed to force fills.

## Sources and vault findings

[MQL5 evidence catalogue](2026-09-03-mql5-trigger-evidence.md) records Turtle Soup, QM, IFVG, tCISD/SMT and raid-versus-MSS rules, with inspected report figures and limits. The earlier vault IFVG source note is supplemented because its text-only review missed numeric report data. The vault's FS-5 **96-cell null/confounding result** and FS-4 **16-trade underpowered external-raid/MSS subset** remain intact. Published source results are not APEX performance and do not establish XAUUSD transfer.

## Validation, deployment and remaining limits

- **488 unit tests pass**, including 15 new tests for both directions, pivot-known time, close versus wick, buffer, return direction/zone/freshness, future-bar invariance, final submission expiry, transient watch retry, mode parity and measured cost/booking. Compilation and diff whitespace checks pass.
- Full 92-day replay completed independently for all three arms. Cached data, input/source hashes, trade lists, watch counters, selection status and exact commands are saved under `docs/reviews/2026-09-03-crt-confluence-evidence/`. Reproduce with `py -3.14 -E ../docs/reviews/crt_confluence_study.py` from `apex_ai`; individual `--arm` runs can be combined using `--summarize`.
- This is M5 bar replay, not every live 30-second decision or tick fill. Bid/ask entry observations share the bar open and execution costs are a separate empirical debit. Same-bar ordering, missing archived news releases, changing spread/slippage distributions and sample reuse limit economic conclusions. No independent successful edge has been established.
- Default era `2026-09-03-crt-confluence-observe`. The new checks collect forward evidence under the operator's demo contract; existing entry gates stay binding. Neither strict mode is promoted, and the evidence-qualified vault playbook is unchanged. Future SMT/IFVG experiments require separate frozen specifications.

## Runtime activation verified

At **05:53 UTC / 10:53 Pakistan** the existing watchdog relaunched scalper **PID 4668** and Guardian **PID 26576**; watchdog **7704** remained active. Broker-authoritative snapshots before and at 05:54:15 UTC after the reload confirm DEMO and zero open positions. Startup/watch logs show `confluence=OBSERVE` and both MSS/retest fields on all six frame/direction watches. The current daily bullish example records the known opposing swing 4334.683 and its later displacement close, while remaining expired; it does not retroactively authorize an entry. The scalper is healthy and IDLE outside its entry sessions. Pool $1,000, risk 3%, XAUUSD, 30-second scans, $100 daily limit and FRESH pool mode are preserved. Evidence: `demo-before.json`, `demo-after.json`, source hashes, process snapshots and `activation.log` in the evidence directory. No discretionary order was submitted.
