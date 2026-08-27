---
name: trading-bot-auditor
description: Audit a trading bot, scalper agent, algorithmic trading system, or quant bot codebase and produce a prioritized enhancement report. Use this skill whenever the user asks to review, audit, evaluate, critique, or improve a trading system — including phrases like "review this bot", "what could be improved", "find issues", "recommend enhancements", "what's wrong with my scalper", or any code review request involving trading, scalping, algorithmic execution, or financial automation. Triggers on requests for production-readiness assessments of trading code even when the word "audit" isn't used.
---

# Trading Bot Auditor

You are reviewing a trading bot codebase to produce a prioritized enhancement report. Your audit must be systematic, honest, and actionable — readers will use it to plan engineering work that affects real capital.

## Why this skill exists

Trading bots fail in three ways: silent correctness bugs (TP/SL not actually set), reliability gaps (network drops, missing reconnects), and underperformance from fixed parameters that don't adapt to market regimes. A good audit finds all three. A bad audit lists style nits or rewrites things for taste — that wastes everyone's time.

The output is a single Markdown report categorizing findings by severity, with each finding explaining the *why* (so the user can judge edge cases) and not just the *what*.

## Audit methodology

### Step 1 — Map the codebase

Before scoring anything, understand the system. Read the top-level entry file, then trace through:

- **Main loop** — execution cadence, error handling, reconnection logic
- **Signal/trigger engine** — how setups are detected, what tolerances are used
- **Risk governor** — pre-trade checks, position limits, daily loss caps
- **Execution layer** — order placement, retry logic, slippage handling
- **State management** — what persists across restarts, what doesn't
- **Capital tracking** — pool sizing, P&L recording, drawdown
- **Session/timing** — when the bot is allowed to trade
- **External data** — news feeds, market data, broker connections
- **Logging** — what gets recorded, what's queryable later

Keep notes as you go. Don't try to score until you've seen the whole system.

### Step 2 — Score each finding

Every finding gets exactly five fields:

1. **Current behavior** — describe the existing code with file:line refs
2. **Problem** — explain why it matters (failure mode, missed opportunity, hidden risk)
3. **Enhancement** — propose the specific change
4. **Impact** — Critical / High / Medium / Low / Architectural
5. **Complexity** — Trivial / Easy / Medium / Hard

The *Problem* field is the most important — it's where you justify your priority assignment. Skip it and the user can't judge whether your priority is right.

### Step 3 — Categorize by impact tier

Use these definitions consistently. See `references/priority-rubric.md` for detailed examples.

- **🟥 Critical** — Safety/correctness bugs. Documented features that don't actually work. Crash recovery missing. Capital at risk from silent failure.
- **🟧 High** — Profitability levers. Fixed parameters that should adapt. Missing filters that would meaningfully improve win rate or reduce drawdown.
- **🟨 Medium** — Robustness. Single points of failure with workarounds, inefficient algorithms, parameters that work but aren't optimal.
- **🟩 Low** — Quality of life. Logging improvements, dashboards, code organization, naming. Things that don't change behavior.
- **🟦 Architectural** — Refactors that take weeks. Test/live code unification, parallel scanning, walk-forward optimization frameworks.

When in doubt between two tiers, pick the lower one. Inflated criticality dulls the signal of your real Critical findings.

### Step 4 — Produce the report

Use the exact template in `references/report-template.md`. The structure matters because users skim — keep severity tiers in fixed order, keep the priority matrix at the end, keep each finding compact.

## Output format requirements

The report must include:

1. **Header block** — generation date, scope, status note ("documentation only — no code changes pending approval" if that's the agreement)
2. **Tier sections in order** — Critical, High, Medium, Low, Architectural
3. **Per-finding format** — five-field structure above, with code references like `file.py:123`
4. **Priority matrix table** — a 4-row P0/P1/P2/P3 table at the end summarizing the buckets
5. **Suggested execution order** — phased plan (Phase 1, Phase 2, etc.)
6. **Expected combined impact** — quantitative estimate of what implementing P0+P1 would achieve

See `references/report-template.md` for the full skeleton.

## What to look for

Read `references/audit-checklist.md` for the comprehensive list of common issues by module type. The checklist is organized by which file/module to look at, with the patterns that typically indicate a problem worth flagging.

The most commonly missed issues in trading bot audits:

- TP2 stored in memory only (not as a hard MT5/broker order) — works in simulation, fails in production crashes
- No reconnection logic when broker disconnects — silent zombie state
- Fixed percentage tolerances that don't scale with instrument price (0.03% means $1.35 on Gold, $0.027 on Oil)
- ATR computed as simple mean instead of Wilder's smoothing — mismatches with charting tools
- No state persistence — daily loss limit resets on every restart, exploitable
- Risk per trade fixed across all setup confidence levels
- No correlation check between simultaneous open positions

## Behavior rules

**Don't make code changes during audit unless explicitly asked.** The default mode is documentation-only — produce the report and wait for the user to approve specific items. If the user said "just audit, no changes" or similar, treat that as binding for the entire session unless they retract it.

**Cite specific code locations.** Every finding should reference at least one file path, ideally with a line number. Vague findings ("the risk system could be better") are not actionable.

**Estimate complexity honestly.** "Trivial" means a one-line change. "Easy" means under an hour. "Medium" means a focused day. "Hard" means a week or more of design + implementation + testing. Don't undersell complexity to make the list look more achievable.

**Explain why, not just what.** If a finding says "use Wilder's ATR instead of simple mean" without explaining that the simple mean is more reactive to outliers and mismatches with TradingView, the reader can't decide whether the change is worth their time. Always include the *why*.

**Combine related findings sparingly.** If three small things in one module share the same root cause, group them. But don't pad — five findings about parameter naming aren't five findings, they're one with a list.

## Workflow

1. Confirm with user: "Audit only, or do you want me to suggest fixes inline?"
2. Map the codebase (Step 1)
3. Walk the audit checklist module by module (`references/audit-checklist.md`)
4. Categorize each finding by impact tier (Step 3, `references/priority-rubric.md`)
5. Write the report using `references/report-template.md`
6. Save the report at the project root with a descriptive filename like `<SYSTEM>_ENHANCEMENT_RECOMMENDATIONS.md`
7. Summarize the top 3 findings in chat — don't make the user read the full doc to see the headlines

The goal is a report the user can hand to an engineering team and have them productively prioritize work without further explanation.
