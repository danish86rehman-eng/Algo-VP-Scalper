# APEX XAUUSD Real-Tick Availability and Guardian-Parity Audit

**Date:** 2026-09-18  
**Scope:** Read-only MT5/broker history and replay-feasibility audit  
**Code/trading changes:** None; no terminal/process restart; no orders placed or modified

## Verdict

```text
GO — REAL TICK HISTORY IS SUFFICIENT; GUARDIAN PARITY CAN BE IMPLEMENTED
```

This is a data-availability verdict, not permission to activate Neuro V1. Candidate-level replay, shared Guardian transition-engine work, immutable caching, and parity tests remain required before bootstrap or activation.

## 1. Connected MT5 environment

| Item | Observed value |
|---|---|
| Broker/company | Exness Technologies Ltd |
| Server | `Exness-MT5Trial2` |
| Account | `40280210`, demo (`trade_mode=0`) |
| Symbol | `XAUUSD` |
| Symbol path | `Zero\\Forex\\XAUUSD` |
| Terminal | MetaTrader 5, build `6182`, version `500`, 5 Sep 2026 |
| Python package | `MetaTrader5 5.0.5735` |
| Terminal path | `C:\\Users\\nauman.afzal\\AppData\\Roaming\\MetaTrader 5` |
| Data path | `C:\\Users\\nauman.afzal.ORIENTPET\\AppData\\Roaming\\MetaQuotes\\Terminal\\939C30CCA4162E2940A76DECE77865EE` |
| XAUUSD precision | 3 digits; point `0.001`; tick size `0.001` |
| Current spread snapshot | 50 points; bid/ask were nonzero |

Python used UTC-aware datetimes and `COPY_TICKS_ALL`. The official Python API returns named tick fields including time, bid, ask, last, and flags; the documentation requires UTC handling for request datetimes. [copy_ticks_range](https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksrange_py), [CopyTicksRange](https://www.mql5.com/en/docs/series/copyticksrange).

## 2. Direct MT5 tick recovery

### Earliest/latest

- Earliest retrievable tick found by `copy_ticks_from("XAUUSD", ...)`: `2026-01-01 23:05:00.140 UTC`.
- Latest observed in the one-day probe: `2026-09-18 05:25:11.172 UTC`.
- Requests before the earliest available history returned that same earliest tick, indicating no older tick was exposed by the connected terminal/server through the Python API.

### Progressive range probes

| Requested range | Ticks | BID/ASK present | Non-monotonic `time_msc` | Largest gap |
|---|---:|---:|---:|---:|
| Recent 1 day | 281,286 | 100% / 100% | 0 | 176,670 s; weekend/session closure |
| Recent 7 days | 1,640,544 | 100% / 100% | 0 | 176,611.9 s; weekend/session closure |
| Recent 30 days | 7,281,478 | 100% / 100% in sampled boundary records | 0 in sampled boundary inspection | Range returned successfully |
| 90 days in one request | failed with IPC `-10002` | not applicable | not applicable | Request too large; chunking required |

The failed 90-day request is an API/request-size limitation, not evidence of missing history. Smaller chronological chunks are required.

## 3. Intended bootstrap coverage

The intended fresh walk-forward window is 2026-05-15 through 2026-08-21. Seven-day `COPY_TICKS_ALL` chunks were requested across the full interval. Every chunk returned ticks, and every returned tick in every chunk had positive BID and ASK fields and monotonic `time_msc` ordering.

| Chunk | Ticks | BID/ASK | Non-monotonic | Largest observed gap |
|---|---:|---:|---:|---:|
| 2026-05-15→21 | 1,830,614 | 100% / 100% | 0 | 176,670.1 s |
| 2026-05-22→28 | 1,252,827 | 100% / 100% | 0 | 176,611.9 s |
| 2026-05-29→Jun-04 | 1,242,522 | 100% / 100% | 0 | 176,610.6 s |
| Jun-05→11 | 2,282,956 | 100% / 100% | 0 | 176,611.0 s |
| Jun-12→18 | 1,610,953 | 100% / 100% | 0 | 176,650.0 s |
| Jun-19→25 | 2,114,173 | 100% / 100% | 0 | 190,890.6 s |
| Jun-26→Jul-02 | 1,681,017 | 100% / 100% | 0 | 176,616.8 s |
| Jul-03→09 | 1,444,621 | 100% / 100% | 0 | 190,891.3 s |
| Jul-10→16 | 1,488,134 | 100% / 100% | 0 | 176,611.3 s |
| Jul-17→23 | 1,380,163 | 100% / 100% | 0 | 176,611.9 s |
| Jul-24→30 | 1,496,348 | 100% / 100% | 0 | 176,610.3 s |
| Jul-31→Aug-06 | 1,356,333 | 100% / 100% | 0 | 176,611.0 s |
| Aug-07→13 | 1,453,073 | 100% / 100% | 0 | 176,611.0 s |
| Aug-14→20 | 1,364,424 | 100% / 100% | 0 | 176,610.3 s |
| Aug-21 | 267,956 | 100% / 100% | 0 | 7.84 s |

The approximately 49–53 hour gaps align with weekend/session closures rather than intraday non-monotonicity. The final Aug-21 probe ended at the last available tick around 20:58 UTC, so candidates whose complete holding interval crosses the dataset end must be excluded until the required subsequent ticks are present.

The exact number of V1 model-eligible candidates with complete holding intervals has not yet been generated because the Neuro candidate producer is not implemented. It must be calculated by joining replay candidates to tick-coverage intervals; it must not be inferred from the 198 executed/backtest trades.

## 4. Local terminal history and Strategy Tester findings

The terminal data directory contains XAUUSD tick database partitions, including:

- `bases\\Exness-MT5Trial2\\ticks\\XAUUSD\\202601.tkc` through `202609.tkc`;
- corresponding XAUUSD monthly partitions under `Exness-MT5Trial16` for 202601–202605;
- recent partitions under `Exness-MT5Real2` for 202608–202609.

This corroborates the direct Python recovery result. No Strategy Tester run was launched during this read-only audit. The official tester documentation states that “Every tick based on real ticks” uses broker real ticks where available, but discards inconsistent minute tick data and substitutes generated ticks when a minute has no real ticks or fails consistency checks. Therefore a tester report is acceptable only if its real-tick coverage/statistics demonstrate that the relevant minutes were real; the mode name alone is not proof. [Testing trading strategies](https://www.mql5.com/en/docs/runtime/testing), [Testing trading strategies on real ticks](https://www.mql5.com/en/articles/2612).

For exact Python replay, raw `COPY_TICKS_ALL` coverage is the authority. Generated tester ticks are not interchangeable with those raw broker ticks.

## 5. Guardian tick-dependency matrix

| Guardian rule | Tick dependency | Required state/side | Exact replay feasibility |
|---|---|---|---|
| Initial SL | First executable quote and frozen stop | Long exits on BID; short exits on ASK; entry side preserved | Feasible with complete ticks and a declared fill model |
| Original TP | First executable target touch | Long BID / short ASK | Feasible with complete ticks |
| Partial TP extension | Intrabar target proximity, momentum, partial volume, next target | Position state, volume step/minimum, M15 structure | Feasible if the same transition engine consumes the same tick/closed-bar inputs |
| Breakeven | Peak reaching configured R and subsequent stop modification | Peak R, current SL, direction | Feasible with chronological ticks and modification timing |
| Trailing stages | Peak/adverse path, ATR/structure state, poll/bar timing | Current SL stage and causal frame | Feasible only through shared live/replay state machine; current M5 simulator is not proof |
| Early close | Reversal/momentum/structure triggers after arming | Armed state, completed frame, executable close side | Feasible with shared engine and tick/poll schedule |
| No-progress exit | Elapsed time and peak R threshold | Entry time, peak R, current executable close | Feasible with ticks and exact clock semantics |
| Timeout | Trigger-frame completed-bar count | 24 subsequent completed bars / existing timeout rule | Feasible from bars plus tick-confirmed fill/exit |
| Adverse movement/MAE | Chronological adverse quote path | Side-aware BID/ASK | Feasible with complete ticks |
| Fill price | Entry/exit quote, spread, slippage | ASK for long entry, BID for short entry; inverse exit side | Historical quote is feasible; realized future slippage must use a frozen deterministic model |
| EOD/session close | UTC schedule and current executable quote | State and close side | Feasible with complete interval and declared fill model |

`apex_ai/scalper/exit_manager.py` currently documents the limitation: `SimulatedGuardian` walks M5 bars, uses bar extremes, applies new stops on the next bar, and fills market decisions at bar close. That implementation must not be used as the final V1 counterfactual engine. The smallest correct implementation is to extract/reuse one Guardian transition engine with a live tick adapter and a historical real-tick adapter.

## 6. Minimum implementation/data work

1. Download raw ticks in safe UTC chunks and store immutable partitions by `symbol/date` with broker/server, interval, count, first/last `time_msc`, SHA-256, download time, and `source=MT5_REAL_TICK`.
2. Add an explicit tick-coverage index and reject candidate holding intervals with missing/incomplete bid/ask coverage.
3. Refactor live Guardian transitions into a shared state machine consumed by live ticks and replay ticks. Do not maintain the existing bar approximation as the V1 research authority.
4. Define and version deterministic historical fill/slippage behavior; real historical quotes do not reveal the exact slippage of an unexecuted counterfactual order.
5. Replay the existing V1 candidate family, then report the exact model-eligible candidate count, complete-tick count, and label/outcome counts.
6. Only after parity tests pass, build the Neuro bootstrap and strategy counterfactual outcome dataset.

## 7. Final assessment

Real broker tick history is recoverable from the connected MT5 environment over the intended bootstrap window. The history is sufficient to remove the original “no historical ticks” blocker and make exact Guardian parity implementable. It does not itself prove candidate-level coverage or actual broker slippage parity; those are explicit implementation gates.

The official MT5 documentation also confirms why the raw-tick path is required: tester real-tick mode may fall back to generated ticks for missing or inconsistent minutes, while `COPY_TICKS_ALL`/`CopyTicksRange` provide the chronological tick records available from the terminal.

