# Report Template

Use this exact structure. Section order, heading levels, and field labels are all part of the contract — readers skim by section, and consistency lets them find what they need.

## Filename

Save to project root as: `<SYSTEM_NAME>_ENHANCEMENT_RECOMMENDATIONS.md`

For example: `SCALPER_ENHANCEMENT_RECOMMENDATIONS.md`, `MARKETMAKER_ENHANCEMENT_RECOMMENDATIONS.md`.

## Skeleton

```markdown
# 🚀 <System Name> — Enhancement Recommendations

**Generated:** <YYYY-MM-DD>
**Status:** <Documentation only — no code changes pending approval | Review and implementation>
**Scope:** <one-line scope, e.g., "Comprehensive review of all SA modules">

This document lists prioritized improvements found during a full code audit of the <system>. Each item explains the problem, the proposed enhancement, expected benefit, and implementation complexity.

---

## 🟥 CRITICAL (Safety/Correctness)

### C1. <Short title>

**Current behavior:** <what the code does today, with file:line references>

**Problem:** <why it matters — the failure mode, missed opportunity, or hidden risk>

**Enhancement:** <specific change to make>

**Impact:** <CRITICAL/HIGH/MEDIUM/LOW>
**Complexity:** <Trivial/Easy/Medium/Hard>

---

### C2. <Short title>

[same five-field structure]

---

## 🟧 HIGH IMPACT (Performance/Profitability)

### H1. <Short title>

[same five-field structure]

---

## 🟨 MEDIUM IMPACT (Robustness/Reliability)

### M1. <Short title>

[same five-field structure]

---

## 🟩 LOW IMPACT (Nice-to-Have)

### L1. <Short title>

[same five-field structure]

---

## 🟦 ARCHITECTURAL (Future-Looking)

### A1. <Short title>

[same five-field structure]

---

## 📊 PRIORITY MATRIX

| Priority | Items | Impact | Effort |
|---|---|---|---|
| **P0 — Do First** | C1, C2, ... | Critical bugs/safety | <effort estimate> |
| **P1 — High Value** | H1, H2, ... | Profitability boost | <effort estimate> |
| **P2 — Quality of Life** | M1, M2, ... | Robustness | <effort estimate> |
| **P3 — Future** | A1, A2, ... | Architectural | <effort estimate> |

---

## 💡 SUGGESTED EXECUTION ORDER

**Phase 1 — Safety & Reliability (1-2 weeks)**
1. C1: <title>
2. C2: <title>
...

**Phase 2 — Profitability (2-3 weeks)**
N. H1: <title>
...

[continue for all phases]

---

## 🎯 EXPECTED COMBINED IMPACT

If P0+P1 implemented:
- **Win rate:** <delta estimate>
- **Profit factor:** <delta estimate>
- **Max drawdown:** <delta estimate>
- **Operational uptime:** <delta estimate>

**Net effect:** <one-sentence summary of expected change to backtest/live profile>

---

## ⚠️ NOTES

- <any caveats specific to this audit>
- <constraints the user mentioned that shaped recommendations>

---

**End of Recommendations Document**

*Awaiting approval before any code changes.*
```

## Numbering convention

- Critical findings: C1, C2, C3, ...
- High findings: H1, H2, H3, ...
- Medium findings: M1, M2, M3, ...
- Low findings: L1, L2, L3, ...
- Architectural findings: A1, A2, A3, ...

These IDs are the contract for follow-up conversations. The user will say "implement C1, H3, M2" and you need to know what those refer to. Don't renumber across iterations — once a finding has an ID in a published report, that ID is stable.

## Field guidelines

**Current behavior:** Cite a specific file and ideally a line number. Format inline code references with backticks. Use `path/to/file.py:123` notation.

**Problem:** This is the most important field. Name the failure mode or missed opportunity. Avoid vague phrases like "could be better" or "is suboptimal". State *what fails* or *what's left on the table*.

**Enhancement:** Be specific enough that an engineer could start work without further clarification. "Use ATR-based tolerances" is too vague. "Replace the 0.0003 constant in `_find_equals` with `0.05 * atr_14`" is actionable.

**Impact:** One word from the rubric.

**Complexity:** Use the four-level scale: Trivial (<10 min), Easy (<1 hour), Medium (1 day), Hard (1+ week).

## Length discipline

A finding should fit on a screen. If you find yourself writing more than ~150 words, split it into multiple findings or move detail to an appendix. Skim-ability is everything.

The full report should typically be 600-1000 lines for a moderately complex bot. Anything longer suggests padding or scope creep.
