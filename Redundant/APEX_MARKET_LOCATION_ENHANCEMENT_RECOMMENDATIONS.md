# 🚀 APEX SA — Market-Location Enhancement Recommendations

**Generated:** 2026-09-19
**Status:** Review and implementation
**Scope:** VP, structural support/resistance, causal replay parity, and candidate telemetry for the XAUUSD scalper

The repository already had reusable VP arithmetic and several experimental VP
triggers, but it did not have one causal state object combining structural zones,
anchored profiles, flips, value-area behaviour, and entry telemetry. That gap is
implemented in this change.

---

## 🟥 CRITICAL (Safety/Correctness)

### C1. No causal combined location state existed

**Current behavior:** `scalper/volume_profile.py:327` builds a profile, while
`scalper/anchored_vp.py:155` builds a separate anchored H4 profile. Neither
returned the complete D1/H4/M15 location contract requested by the brief.

**Problem:** Entry and replay code could observe different partial VP concepts;
there was no single auditable answer to “where is price?” and no shared state to
prove that historical decisions used only known anchors.

**Enhancement:** Implemented `scalper/market_location.py:817` with closed-bar
filtering, a single snapshot schema, shared profile selection, structural zones,
value-area events, and profile lifecycle telemetry.

**Impact:** CRITICAL
**Complexity:** Hard

---

## 🟧 HIGH IMPACT (Performance/Profitability)

### H1. Structural levels were not clustered or flip-aware

**Current behavior:** Existing liquidity helpers identify individual swing/liquidity
pools, but did not provide the requested ATR-normalized clustered SR zones with
acceptance-based flips.

**Problem:** A chart-like level can be represented by many nearby prices, while a
broken support/resistance level can disappear from the operator’s context or be
mistaken for a fresh level.

**Enhancement:** Implemented candidate clustering, touch/rejection scoring,
support/resistance/FLIP roles, multi-close acceptance, and persistent retired
profile IDs in `scalper/market_location.py:586-646`.

**Impact:** HIGH
**Complexity:** Hard

### H2. VP levels were not exposed as location evidence on every candidate

**Current behavior:** `SATrigger` had sweep-location context, but no shared
VP/SR snapshot; the live path selected triggers before any combined location
state could be attached.

**Problem:** Researchers could not measure location buckets consistently across
trigger types, and a location touch could be confused with an entry permission.

**Enhancement:** `SATrigger`/`step2_trigger` now carry the facts-only
`market_location` snapshot; live and replay both pass it at
`scalper_agent.py:939` and `backtest_scalper.py:1363`. No risk, SL/TP, cooldown,
session, news, or Guardian rule branches on it.

**Impact:** HIGH
**Complexity:** Medium

---

## 🟨 MEDIUM IMPACT (Robustness/Reliability)

### M1. Runtime/replay frame-window drift could invalidate location evidence

**Current behavior:** Live fetches bounded closed frames; replay has access to
the full historical dataset.

**Problem:** A growing replay frame can expose older swings and profiles that the
live process never had, making location telemetry non-reproducible.

**Enhancement:** The new engine applies the same configured D1/H4/M15 caps in
both paths and requires completed bars at `as_of`; focused causality tests cover
forming/future bars.

**Impact:** MEDIUM
**Complexity:** Easy

### M2. Value-area state did not distinguish rejection from acceptance

**Current behavior:** Existing value-area logic classified price location and
could generate a fade, but had no shared `VAH_REJECTION`, `VAH_ACCEPTANCE`,
`VAL_REJECTION`, `VAL_ACCEPTANCE`, `POC_RECLAIM`, or `POC_LOSS` evidence state.

**Problem:** A brief excursion and a multi-close breakout could be pooled as the
same location, obscuring whether continuation or rejection was actually present.

**Enhancement:** Implemented completed-bar event classification in
`scalper/market_location.py:747` and serializes it with candidate telemetry.

**Impact:** MEDIUM
**Complexity:** Medium

---

## 🟩 LOW IMPACT (Nice-to-Have)

### L1. No operator-facing historical diagnostic existed

**Current behavior:** VP and structure tests existed, but there was no one report
showing all selected profiles, nearest levels, zones, and current location for a
representative XAUUSD period.

**Problem:** Visual comparison against the supplied chart required manually
re-running several independent components.

**Enhancement:** Added `apex_ai/diagnose_market_location.py` and generated
`docs/reviews/market_location_xauusd_2026-09-02.md` from exported completed MT5
bars. Screenshot prices were not used as inputs.

**Impact:** LOW
**Complexity:** Easy

---

## 🟦 ARCHITECTURAL (Future-Looking)

### A1. Location evidence still needs a pre-registered walk-forward study

**Current behavior:** The new state is observation-only and does not alter entry
selection, consistent with the repository’s research invariants.

**Problem:** A plausible confluence score is not performance evidence. Promoting
it to a veto, trigger, or sizing modifier without disjoint chronological folds
would create selection bias.

**Enhancement:** Compare baseline telemetry against location buckets using the
existing 60/20/20 walk-forward protocol, with costs, fill timing, and the live
decision path frozen before promotion.

**Impact:** ARCHITECTURAL
**Complexity:** Hard

---

## 📊 PRIORITY MATRIX

| Priority | Items | Impact | Effort |
|---|---|---|---|
| **P0 — Do First** | C1 | Causal correctness and single-source state | Implemented |
| **P1 — High Value** | H1, H2 | Structural location quality and measurable evidence | Implemented |
| **P2 — Quality of Life** | M1, M2, L1 | Replay parity, state attribution, diagnostics | Implemented |
| **P3 — Future** | A1 | Performance validation before any promotion | Hard / research cycle |

---

## 💡 SUGGESTED EXECUTION ORDER

**Phase 1 — Causal state and safety**

1. Keep C1’s closed-bar and bounded-frame contract frozen.
2. Keep `market_location` facts-only until the walk-forward study is complete.

**Phase 2 — Research and promotion**

3. Run A1 on disjoint XAUUSD periods.
4. Only after stable sign across folds consider a location veto or entry-quality
   feature; do not change sizing, SL/TP, or Guardian behaviour in that study.

---

## 🎯 EXPECTED COMBINED IMPACT

P0+P1 changes are deliberately observation-first, so no honest win-rate,
profit-factor, or drawdown delta can be claimed before walk-forward measurement.
They do provide:

- **Calculation parity:** one shared location engine for live and replay.
- **Causality:** no forming/future bars or unconfirmed swing anchors.
- **Operational coverage:** every selected candidate can carry POC/VAH/VAL,
  structural support/resistance, profile anchors, event state, and confluence.
- **Direct risk impact:** none by design until A1 is validated.

**Net effect:** the scalper gains a reproducible market-location evidence layer
without changing the existing trading permissions or risk model.

---

## ⚠️ NOTES

- The attached chart was treated as a visual reference only; its displayed
  prices were not hard-coded.
- When real volume is unavailable, the engine uses MT5 tick volume. It does not
  silently fall back to invented unit volume.
- Bar-based VP distributes volume across the candle’s traded price range; it is
  deterministic and replay-safe but not identical to a tick-built profile.
- The repository was already dirty before this work. The full suite currently
  reports unrelated pre-existing failures in session/VPLR defaults and mocked
  replay fixtures; the new focused market-location suite passes 15/15.

---

**End of Recommendations Document**
