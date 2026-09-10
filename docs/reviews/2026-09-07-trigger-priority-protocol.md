# Current-code trigger isolation and priority experiment

Protocol fixed before viewing outcomes, 2026-09-07 UTC. Research only; no production changes or live orders.

- Read and hash the current working copy, including uncommitted changes. Run its 490-test suite in a separate source snapshot. Freeze history from the attached Exness-MT5Real2 session without login credentials.
- XAUUSD, $1,000 fresh starting pool per arm/window, 3% compounding risk, $100 daily loss limit, current position cap, cooldowns, Tokyo and London/NY sessions only. All other decision defaults unchanged. Shared Guardian enabled explicitly in the simulator.
- Native M15 triggers and M5 confirmations; H1/H4 context; D1/W1/MN1 CRT ranges. Shipped warm-up and closed-frame logic. No timeframe substitution.
- W1: March 1–May 31, 2026; W2: June 1–August 31, 2026. Separate capital resets provide disjoint-window comparisons. September 1–6 is a short recent diagnostic, not a sufficient validation period. Historical dates have been used in earlier research; this is a new experiment against the current configuration, not untouched OOS data.
- Arms: unchanged combined baseline and one isolated arm for each currently active family: HTF_CRT_SWEEP, current M15 FVG_FILL, SWEEP_REJECTION, BOS_RETEST, JUDAS. VP_LIQUIDITY_REACTION and VALUE_AREA_FADE remain disabled; enabling them would measure a different configuration.
- Use the existing measured-total-cost facility at the canonical reference mean, USD 0.35/oz per round trip, replacing rather than adding spread/commission debits. Keep archived spread entry gates with a 6-pip floor where history has zero spread. The cost reference is DEMO-derived and not a live execution calibration. Confirm live commission separately. Report fixed-trade USD 0.17/0.75 sensitivity, labelled as an overlay that does not recompute risk/cooldown paths.
- Rank dollar net profit, then inspect trade count, profit factor, sizing-neutral net R, closed-balance drawdown, disjoint-window signs and within-window 60/20/20 splits. Do not promote a zero/sparse-sample arm as profitable.
- Count competing detections before post-trigger gates. Re-run combined priority changes for the two highest-net trade-bearing isolated families across the disjoint windows. If baseline arbitration never changes under a proposed promotion, record that equivalence instead of pretending the order affected trades. Priority experiments only change selection in the research process, not detector definitions, gates or the live file.
- No automatic production promotion. Multiple comparisons and retrospective selection limit inference.

Known fidelity limits: M5 bar-based decision/fill/Guardian approximation, not 30-second/tick replay; present calendar lacks historical news coverage; no live slippage calibration or broker rejection/restart simulation. Therefore findings rank this implemented simulator under explicit assumptions, not guaranteed real-account profits.

## Operator continuation: remaining triggers, 2026-09-07 09:05 UTC

The five active-family runs finished before this continuation. Run the two dormant families separately as optional research activations, using the same W1/W2 and recent dates. VALUE_AREA_FADE enables only its profile feature. VP_LIQUIDITY_REACTION enables its existing feature and preserves its shipped ASIA_ONLY scope and 00:00–06:30 trigger-specific allowance; under the current session checker this admits VP-only scans outside the regular Tokyo window. It is therefore not the same session sample and must not be mixed into the current-production ranking. No live setting changes.

For the VP-only arm, omit the pure HTF CRT watcher whose result cannot be selected by that singleton whitelist. Keep all data, TFs, feature parameters, risk settings, entry gates, and Guardian exits unchanged. Validate this observation-only optimisation with a paired `--full-watch` replay; compare trades, P&L, rejection counts and equity curves, excluding the deliberately omitted CRT observation telemetry. Priority tests remain the original two highest-net trade-bearing active families: SWEEP_REJECTION and JUDAS.
