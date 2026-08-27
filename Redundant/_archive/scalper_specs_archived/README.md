# Archived Scalper Agent Specifications

**Archived on:** 2026-05-09

This folder contains **superseded** Scalper Agent specification documents.

## Files in this archive

| File | Status | Reason |
|---|---|---|
| `SCALPER_AGENT.md` | ❌ SUPERSEDED | Old proposal document (pre-implementation) |
| `SCALPER_UPGRADE_V1.md` | ❌ SUPERSEDED | Early upgrade proposal (partially implemented) |

## Current spec

**All SA functionality is now documented in:**  
→ **`SCALPER_AGENT_V1_FINAL.md`** (root directory)

This is the authoritative, complete specification including:
- All V1 upgrades (RDA regime awareness, LIA macro magnets, displacement validation)
- New session windows (TOKYO_OPEN added)
- HTF bias filter refinement (NEUTRAL now blocks)
- News fetcher automation (Forex Factory integration)
- EOD enforcement (no trades after 23:00 UTC, force close all open positions)
- Extended news blackout (30 min before / 15 min after)

## Why archived

The old specs were:
1. **Fragmented** — split across proposal + upgrade documents
2. **Outdated** — did not reflect implemented code (especially recent changes)
3. **Confusing** — redundant information, conflicting rules

Having one authoritative final spec prevents:
- Version confusion ("which doc is current?")
- Inconsistent decision-making based on stale rules
- Git conflicts / merge madness

## If you need historical context

These files show the design evolution of the SA system, but should NOT be used for implementation or decision-making. Always refer to `SCALPER_AGENT_V1_FINAL.md`.

---

*Archive maintained to preserve design history, not for active use.*
