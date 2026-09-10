# APEX CRT — Enhancement Recommendations

**Generated:** 2026-09-03
**Status:** Review and implementation; performance adjudication recorded in the linked results.
**Scope:** HTF external-liquidity raids, M15 structural confirmation, M5 return confirmation and empirical execution costs. This is a focused review, not a new audit of every module.

## Critical — Safety/correctness

No new critical execution defect established in this focused review. Existing position, risk, news, session, broken-level reclaim and final-quote controls remain binding.

## High — Performance evidence

### H1. Measure confirmation rather than assuming more filters add edge

**Current behavior:** `apex_ai/scalper/htf_crt.py` admits a raid/displacement/FVG return without an explicit pre-raid opposing-swing break or directional completed return candle when no separate broken-level gate applies.

**Problem:** This leaves a distinction between a range re-entry and a confirmed reversal implicit. The vault's 96 indicator comparisons do not validate adding generic momentum/trend filters, and no available screenshot proves this missing distinction causes negative expectancy.

**Enhancement:** Implement causal MSS and fresh M5 return labels in `apex_ai/scalper/crt_confluence.py`, default OBSERVE, with executable MSS and MSS_RETEST research modes shared by live/replay. Freeze and evaluate exactly two additions. Save candidate timestamps and accepted trade evidence. Implemented; see `docs/reviews/2026-09-03-crt-confluence-preregistration.md` and the evidence folder.

**Impact:** High — resolves an important evidence gap; financial benefit remains unknown.
**Complexity:** Medium

### H2. Include measured slippage in the development replay

**Current behavior:** The historical harness's ordinary execution debit includes spread/commission but no slippage. `_execution_cost` now additionally accepts a measured XAUUSD total-cost override.

**Problem:** Thin apparent edges can vanish after execution. Adding the vault's total cost to the old debit would double-charge spread/commission; using a post-processing overlay alone would miss changes in loss limits, cooldown and sizing.

**Enhancement:** Replace the old debit with the measured 0.35 USD/oz total in all three experiment arms, before booking and feedback. Preserve observed historical spread in the entry gates. Test both the old cost path and replacement path. Implemented; fixed 0.17/0.75 sensitivity overlays are explicitly not full operational reruns.

**Impact:** High — more faithful evidence, not a projected improvement in returns.
**Complexity:** Easy

## Medium — Reliability

### M1. Retry a failed first CRT watch

**Current behavior:** `_watch_htf_crt` previously recorded the quarter-hour stamp before knowing whether lower-timeframe data was available.

**Problem:** A transient missing bar at the boundary could suppress useful observation until the next quarter-hour. Entry scanning separately rechecks data, so this is not evidence that the bot traded with missing bars.

**Enhancement:** Mark the watch complete only after a nonempty valid lower-timeframe result; retry on the next loop after missing data. A regression test covers the failure followed by a successful retry. Implemented.

**Impact:** Medium
**Complexity:** Easy

## Low — Evidence quality

### L1. Inspect published report images and retain source limits

**Current behavior:** The vault's earlier IFVG note said numerical results were unavailable from extracted text.

**Problem:** Source evaluation based on prose alone missed actual report figures. Conversely, a favorable report may contain only a few trades or use another instrument.

**Enhancement:** Inspect and catalogue source reports, append a dated correction retaining the earlier extraction record, and distinguish authored examples from independent XAUUSD replication. Completed in `docs/reviews/2026-09-03-mql5-trigger-evidence.md` and the vault.

**Impact:** Low
**Complexity:** Easy

## Architectural — Future work

No architectural rewrite proposed. Gold/silver SMT and IFVG are separately catalogued candidate experiments; neither is silently introduced into this fixed comparison.

## Priority matrix

| Priority | Items | Purpose | Effort |
|---|---|---|---|
| P0 | None established | Existing protections retained | None |
| P1 | H1, H2 | Make confirmation effects measurable after costs | Medium |
| P2 | M1, L1 | Restore observation and source accuracy | Easy |
| P3 | Separate SMT/IFVG experiments | New evidence after explicit specifications | Unestimated |

## Execution order

Phase 1: freeze definitions and inspect source/vault evidence. Phase 2: implement shared labels, strict modes, empirical costs and regression coverage. Phase 3: compare three fixed historical arms and document the verdict. Phase 4: collect prospective OBSERVE evidence under the existing demo contract; do not label already-used history as untouched OOS.

## Expected combined impact

**Win rate, PF and drawdown improvement: not estimable before adequate evidence.** Completed work supplies two auditable confirmations, a measured-cost comparison and recovery of transient watch failures. Numerical experimental outcomes, rather than invented benefit estimates, belong in the results review. Software test success verifies the contract, not profitability.

Measured outcome: the fixed three-arm 92-day comparison returned the same 139 trades, with zero eligible CRT candidates/trades in each arm. Whole-bot +$91.48 / +6.768R was unstable across periods and became -$108.92 under the higher-cost fixed-trade overlay. These data do not validate either confirmation. See [results and verified demo activation](docs/reviews/2026-09-03-crt-confluence-review.md).
