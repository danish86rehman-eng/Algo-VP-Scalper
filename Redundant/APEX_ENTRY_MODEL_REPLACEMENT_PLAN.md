# APEX Entry-Model Replacement Plan

**Generated:** 2026-09-17  
**Scope:** Scalper entry selection and trigger replacement  
**Status:** Documentation only — no code changes or live configuration changes

## Executive recommendation

Replace the current pattern-first entry model with one staged entry contract:

`HTF/M15 location → confirmed displacement/MSS → fresh M15 FVG → completed M5 return → executable quote and cost gate`

The existing eight names should become evidence labels, not independent entry permissions. The implementation should reuse the already-built `reclaim_fvg.py` and `m15_fvg_entry.py` contracts, with the stronger structural sequence promoted only after a frozen, causal walk-forward comparison.

This is a candidate design, not a claim of profitability. The vault explicitly says the M15 FVG and HTF CRT implementations are demo-validated but expectancy-unverified, while the prior sweep-led book is independently negative.

## Findings

### Critical

None newly established. Existing risk, cost, news, session, cooldown, broker-recovery and final-quote controls must remain binding.

### High

#### H1 — The selector is greedy and lets a weak local signal pre-empt stronger evidence

**Current behavior:** `apex_ai/scalper/trigger_engine.py:297-390` evaluates multiple detectors but selects the first detected candidate. `SWEEP_REJECTION` is still ahead of HTF CRT, VP liquidity reaction, FVG entry, BOS retest and Judas in `apex_ai/scalper/trigger_engine.py:225-226,340-385`.

**Problem:** The research notes report that sweep rejection produced 196 of 198 trades in the fresh 2026-05-15→08-21 replay and lost money; this also prevented meaningful measurement of lower-priority models. A pattern firing is not evidence that price has accepted a level or that the entry has a viable path to target.

**Enhancement:** Replace first-match selection with a single staged setup evaluator. A setup may enter only after structural reclaim/displacement, fresh FVG formation, M5 return confirmation and final quote validation. Preserve all detector names as telemetry labels (`matched_triggers`), not as independent bypasses.

**Impact:** High  
**Complexity:** Hard

#### H2 — The proposed better entry already exists in pieces, but it is not the sole entry contract

**Current behavior:** `apex_ai/scalper/reclaim_fvg.py` enforces broken-level reclaim, displacement, FVG creation and M5 return for legacy candidates; `apex_ai/scalper/m15_fvg_entry.py:45-127` independently evaluates completed M15 displacement/FVG zones, structural target clearance and quote-in-zone entry.

**Problem:** Combining these as optional post-detection gates leaves a path where a legacy detector can still generate a candidate without the full location-to-return sequence. The vault records this exact weakness: local sweep plus green close could buy while prior support remained broken.

**Enhancement:** Define one `STRUCTURAL_RECLAIM_RETURN` evaluator shared by live and backtest. Required sequence: confirmed HTF/M15 level, close-based break, directional reclaim with frozen ATR buffer, displacement-created FVG, first valid completed M5 return, no invalidation/staleness, quote inside the zone, and net-R/cost clearance.

**Impact:** High  
**Complexity:** Hard

#### H3 — Existing evidence does not justify promoting any replacement yet

**Current behavior:** The vault contains demo activation and synthetic/unit validation for M15 FVG and HTF CRT, but no untouched OOS profitability proof. HTF CRT had zero eligible CRT entries in its fixed comparison; M15 FVG had zero qualifying trades in its bounded smoke.

**Problem:** Enabling a new trigger because it looks cleaner would replace one unproven entry hypothesis with another and could overfit the observed losses.

**Enhancement:** Freeze the candidate contract before replay. Compare current baseline, staged replacement, and staged replacement plus optional location veto on disjoint data, with identical live/backtest decision paths, measured costs, fill timing, cooldown, lot-floor behavior and Guardian exits.

**Impact:** High  
**Complexity:** Medium

### Medium

#### M1 — Silent lot-floor rejection can create degraded re-entry selection

**Current behavior:** The research notes identify `_calc_volume`/`LOT_FLOOR` as an admission filter; an unaffordable setup does not consume the opportunity, so the scanner can later take a worse candidate.

**Problem:** This contaminates trigger comparisons: a smaller risk budget samples a low-quality residue rather than the strategy itself.

**Enhancement:** Log the rejected setup identity and hold the same symbol/direction/setup until expiry or a new structural event. Do not allow a lot-floor rejection to fall through to a different entry in the same impulse without fresh evidence.

**Impact:** Medium  
**Complexity:** Medium

#### M2 — “High confidence” is a label, not a calibrated entry edge

**Current behavior:** The high-confidence session study produced negative net results in both reported windows despite requiring HIGH trigger and STB confidence.

**Problem:** Adding confidence labels on top of weak trigger geometry does not solve adverse location, stale evidence or target obstruction.

**Enhancement:** Make confidence a report field derived from the staged evidence chain; do not use it as a substitute for structural acceptance until it survives the same walk-forward test.

**Impact:** Medium  
**Complexity:** Easy

### Low

#### L1 — Naming currently obscures the behavioral change

**Current behavior:** The eight trigger names mix event descriptions, setup models and location filters.

**Problem:** Operators can mistake “FVG_FILL” or “BOS_RETEST” for a complete trade thesis when they are only partial observations.

**Enhancement:** Rename the promoted model around the entry contract, for example `STRUCTURAL_RECLAIM_RETURN`, and retain legacy names only in telemetry and isolated research whitelists.

**Impact:** Low  
**Complexity:** Easy

## Proposed entry contract

1. Build a confirmed, causal HTF/M15 location: prior completed range edge, broken swing level, or structurally confirmed demand/supply zone.
2. Require a close-based break or raid and a later directional reclaim. Wicks alone never qualify.
3. Require displacement of at least the existing declared ATR/body/close-location thresholds; do not optimize these on the loss window.
4. Require the displacement to create a fresh, consecutive three-candle M15 FVG. Formation becomes known only after candle three closes.
5. Wait for the first subsequent completed M5 return into the FVG, closing directionally and remaining on the reclaimed side of the level.
6. Reject consumed, stale, invalidated or reused setups. After a same-direction close, require fresh post-close evidence.
7. Use the current executable bid/ask only; never fill from a historical wick or candle low/high.
8. Reject if the nearest structural obstacle prevents gross 2R or the cost-adjusted net-R floor. Keep target before the obstacle; never tighten the stop to manufacture R.
9. Apply existing session, news, regime, bias, cooldown, exposure, sizing, lot-floor and Guardian controls.

## Execution order

**Phase 1 — Freeze and instrument.** Add setup IDs, stage timestamps, rejection reasons, quote-at-decision, spread, expected/realized R, lot-floor status and selected/overridden trigger labels. No behavior change.

**Phase 2 — Shared evaluator.** Implement the staged evaluator once and call it from both `scalper_agent.py` and `backtest_scalper.py`. Keep the legacy eight-trigger mode available only as an explicit research switch.

**Phase 3 — Causal replay.** Run three arms on untouched data: current baseline; staged replacement; staged replacement plus the existing VP-leg location veto. Use chronological 60/20/20 reporting only as a within-window diagnostic, plus multiple disjoint windows because the vault shows one-window results are unstable.

**Phase 4 — Promotion gate.** Require positive net expectancy after measured costs, no negative exit bucket, adequate trade count, stable sign across disjoint windows, and no material increase in lot-floor or setup-reuse artifacts. If any condition fails, keep the model in OBSERVE/demo mode.

**Phase 5 — Demo forward test.** Run the promoted candidate on DEMO with order submission constrained by the existing risk limits. Review at least 50–100 resolved trades or a pre-registered calendar period, whichever is longer; do not raise risk during this phase.

## Priority matrix

| Priority | Items | Purpose | Effort |
|---|---|---|---|
| P0 | Preserve all current safety/cost/recovery gates | Prevent capital and measurement regressions | None |
| P1 | H1, H2, H3 | Replace greedy entries with a measurable staged contract | Hard/Medium |
| P2 | M1, M2 | Remove admission contamination and confidence overinterpretation | Medium/Easy |
| P3 | L1 | Make operating mode and research mode unambiguous | Easy |

## Expected combined impact

No honest numerical improvement can be estimated from the vault. The strongest defensible expectation is fewer low-information entries, more time spent waiting for acceptance/return, and cleaner attribution of whether entry quality—not exits, costs or sizing artifacts—is responsible for results. The plan should be considered successful only if the replay demonstrates improved net expectancy and stability after costs; fewer trades alone is not success.

