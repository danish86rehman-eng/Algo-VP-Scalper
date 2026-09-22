# APEX early-entry trigger audit

Generated: 2026-09-18  
Scope: XAUUSD scalper and strategy vault; documentation only — no code, configuration, or live-process changes.

## Conclusion

The best candidate for an earlier entry is not a new “predictive” trigger. It is a stricter, session-owned liquidity-raid reclaim: register the prior session external level before the raid, wait for the first completed M5 reclaim/MSS, and use the first valid executable return. This is earlier than the current JUDAS rule while retaining causal confirmation.

Do not enable a new trigger yet. The vault contains no profitability evidence for this exact version.

## Findings

### 🟥 Critical

None found in this read-only review.

### 🟧 High — JUDAS is structurally late

**Current behavior:** `apex_ai/scalper/trigger_engine.py:728-777` defines JUDAS as the first session move followed by a 50%+ retracement. The last two trades were both JUDAS.

**Problem:** The entry waits for a large portion of the reversal to occur, so the stop/target geometry is evaluated after the move has already matured. It is a confirmation-of-reversal model, not an early-reclaim model.

**Enhancement:** Evaluate a separate `SESSION_RAID_RECLAIM` candidate from the vault’s session-liquidity ledger: prior-session high/low registered before the event → meaningful raid → first completed M5 close back through the level with directional displacement/MSS → executable quote in the reclaimed zone. Keep JUDAS unchanged as a comparison arm.

**Impact:** High. **Complexity:** Medium.

### 🟧 High — the existing session-sweep path is the closest fit

**Current behavior:** `apex_ai/scalper/session_sweep.py:218-321` already enforces closed M5 observations, raid/reclaim/MSS sequencing, freshness, target distance, and expiry. It is selected before JUDAS in `apex_ai/scalper/trigger_engine.py:338-345`.

**Problem:** Recent scans often remained in `SESSION_SWEEP_WAIT_NEXT_M5_CLOSE`, `WAIT_RECLAIM_MSS`, or stale/invalid states, so JUDAS became the executable fallback. A “faster” entry that bypasses these states would reintroduce the premature-sweep problem the vault rejected.

**Enhancement:** First instrument and replay the existing session-sweep lifecycle by latency: raid time, reclaim close, MSS close, quote-return time, and JUDAS entry time. Only then consider a narrowly defined first-reclaim arm.

**Impact:** High. **Complexity:** Medium.

### 🟨 Medium — generic sweep and dwell variants are not safe shortcuts

**Current behavior:** The vault’s support/retest review documents that generic `SWEEP_REJECTION` can reuse a local sweep and lacks durable level role-state. The M15 liquidity-dwell research was closed; the R5/TGA comparison worsened all seven paired timer arms.

**Problem:** Promoting “first wick” or a dwell timer because it enters earlier would trade away causal sequencing and rely on already-rejected or unvalidated evidence.

**Enhancement:** Preserve these as shadow/replay baselines only; do not enable them as production entries.

**Impact:** Medium. **Complexity:** Easy.

### 🟦 Architectural — measure entry latency and opportunity cost

**Current behavior:** Existing logs record selected triggers, but do not yet provide a single comparable event timeline for raid, reclaim, MSS, first executable return, JUDAS readiness, and final gate rejection.

**Problem:** Without that timeline, “early” can mean earlier visually but worse after spread, stop distance, and target reachability.

**Enhancement:** Use the candidate-funnel recorder to compare session-sweep, JUDAS, M15 FVG, and shadow first-reclaim candidates on identical completed bars and costs.

**Impact:** Architectural. **Complexity:** Medium.

## Vault evidence used

- `journal/reviews/2026-09-10-desired-session-vah-short.md`: proposes prior-NY liquidity ownership and leaves first rejection versus later retest unresolved.
- `journal/reviews/2026-09-02-xauusd-support-retest-review.md`: recommends durable role-state plus later M5 confirmation and warns that generic sweep reuse caused repeated entries.
- `journal/reviews/2026-09-03-htf-crt-trigger-review.md`: the D/W/M → M15 displacement → FVG → M5 return chain is implemented but efficacy is unverified and the bounded replay produced zero CRT entries.
- `research/m15_liquidity_dwell_reversal/` and `journal/prospective-oos-register.md`: dwell/TGA branch closed without promotion.

## Priority matrix

| Priority | Action | Status |
|---|---|---|
| P0 | No live trigger change; preserve current gates and costs | Required |
| P1 | Instrument/replay session-raid-to-entry latency | Recommended |
| P2 | Compare first-reclaim shadow arm against JUDAS | After P1 |
| P3 | Consider production enablement only after chronological OOS evidence | Not authorized yet |

## Suggested execution order

1. Collect funnel telemetry over several sessions without changing selection.
2. Replay the existing session-sweep path and classify where it loses time or fails geometry.
3. Test the first-reclaim candidate as shadow-only with both directions, costs, sessions, news, and TGA exits.
4. Promote only if the candidate clears the vault evidence bar and beats the existing path out-of-sample.

## Expected combined impact

No defensible win-rate or expectancy improvement can be estimated from the current evidence. The mechanical timing objective is earlier observation at the first completed M5 reclaim rather than waiting for JUDAS’s 50% retracement; profitability impact remains UNKNOWN until replay data supports it.

## Recommendation

Use the existing `SESSION_SWEEP` lifecycle as the next early-entry research arm. Do not add or enable a new trigger yet; first measure whether its `WAIT_RECLAIM_MSS`/`WAIT_NEXT_M5_CLOSE` path is genuinely later than the desired entry and whether the resulting geometry still clears costs.
