# APEX HTF CRT correction

Generated 2026-09-03. Scope: requested D1/W1/MN1 trigger and DEMO integration. Status: implemented; efficacy unverified. Full definition, research context and validation: [L-019 review](docs/reviews/2026-09-03-htf-crt-trigger.md).

## Critical

No additional critical finding established in this bounded review.

## High

### Missing higher-timeframe sweep trigger

- **Current behavior:** `apex_ai/scalper/trigger_engine.py:227` now registers `HTF_CRT_SWEEP` first; the prior selector contained no native D1/W1/MN1 trigger.
- **Problem:** a local sweep or location filter could not express the operator's prior-period liquidity raid, displacement reclaim and FVG-return sequence.
- **Enhancement:** shared `apex_ai/scalper/htf_crt.py:204` evaluator, explicit bid/ask entry, both directions/all three frames, immutable completed ranges, durable setup reuse protection, matching backtest and TGA target cap.
- **Impact:** High — strategy correctness and observability; financial benefit unmeasured.
- **Complexity:** Medium — detector plus entry, replay and recovery integration.

## Medium

No additional independent finding in scope.

## Low

No additional independent finding in scope.

## Architectural

No architectural refactor needed for this correction.

## Priority matrix

| Priority | Work |
|---|---|
| P0 | Completed-bar causality, costs and risk remain mandatory |
| P1 | Implement the actual HTF trigger and target/recovery parity |
| P2 | Record demo funnel and rejection evidence before claiming an edge |
| P3 | No optional expansion proposed |

Execution order: frozen rule definition, shared implementation, causal/directional and integration checks, broker-data diagnostic replay, verified demo reload. Combined expected financial impact cannot be estimated from the screenshot or synthetic tests. Prior vault CRT results are negative or inconclusive; no profit improvement is claimed.
