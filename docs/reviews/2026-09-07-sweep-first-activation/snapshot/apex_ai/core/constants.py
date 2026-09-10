"""
APEX AI — Shared Runtime Constants
==================================
Single source of truth for values that MUST agree across processes.

Before this module existed, each writer hard-coded its own magic number and
each reader hard-coded its own mapping. `MT5Connector` stamped 20260426 while
`trade_guardian_agent` only recognised 12345, so every main-system position was
registered as "UNKNOWN" by the Guardian. Import from here instead of typing a
literal.
"""
from __future__ import annotations

# ── Agent magic numbers ───────────────────────────────────────────────────────
# These identify which agent opened or modified a position on the broker side.
MAGIC_MAIN = 20260426    # maingpt.py (Capital Pool A) — value the connector has
                         # always written; the docs' 12345 was never emitted.
MAGIC_SCALPER = 88880    # scalper_agent.py (Capital Pool B)
MAGIC_TGA = 99990        # trade_guardian_agent.py — SL/TP modifications, closes
MAGIC_CHA = 77770        # Reserved, unimplemented
MAGIC_LEGACY_MAIN = 12345  # Historical Pool A magic; still recognised on read so
                           # trades opened by older builds keep their attribution.

#: Read-side mapping. Every consumer that labels a position by origin uses this.
MAGIC_TO_AGENT = {
    MAGIC_MAIN: "MAIN",
    MAGIC_LEGACY_MAIN: "MAIN",
    MAGIC_SCALPER: "SA",
    MAGIC_TGA: "TGA",
    MAGIC_CHA: "CHA",
}

#: Magics whose positions the Scalper considers its own (restart adoption,
#: history restore, EOD enforcement).
SCALPER_MAGICS = (MAGIC_SCALPER,)


def agent_for_magic(magic: int) -> str:
    """Human-readable owner of a position, or UNKNOWN for manual trades."""
    return MAGIC_TO_AGENT.get(int(magic or 0), "UNKNOWN")


# ── Position ownership ────────────────────────────────────────────────────────
# The Guardian and the Scalper both see magic-88880 positions. Without a rule,
# both may act on the same ticket in the same second with different P&L
# definitions. The Scalper owns the entry contract (timeout, end-of-day); the
# Guardian owns in-trade stop/target management. TGA_MANAGES_SCALPER makes that
# explicit and switchable.
TGA_MANAGES_SCALPER = True

# ── Trade outcome labels ──────────────────────────────────────────────────────
#: Outcome for a position the broker closed but never priced — its closing deal
#: did not reach deal history within the agent's reconciliation grace.
#:
#: It exists so that "we do not know" stops being spelled "LOSS at $0.00". The
#: booking path used to fabricate a $0.00 P&L on a settlement failure and then
#: derive the win/loss label from that fabrication, which is not neutral:
#: `register_close(0.0)` resets the consecutive-loss counter and
#: `record_trade_result(0.0)` arms the *loss* cooldown, because neither
#: `0.0 < 0` nor `0.0 > 0` is true.
#:
#: Deliberately outside the WIN*/LOSS/TIMEOUT vocabulary that
#: `scalper.postmortem.classify` and `analyze_incidents` switch on, so an
#: unpriced trade cannot be counted as a failure mode it was never shown to
#: have. Both the writer and every reader import it from here — a label whose
#: whole purpose is to be recognised is the last thing to type as a literal.
OUTCOME_UNRECONCILED = "UNRECONCILED"

# ── Log rotation ──────────────────────────────────────────────────────────────
LOG_MAX_BYTES = 10 * 1024 * 1024   # 10 MB per file
LOG_BACKUP_COUNT = 5               # keep 5 rotations (~60 MB ceiling per log)
