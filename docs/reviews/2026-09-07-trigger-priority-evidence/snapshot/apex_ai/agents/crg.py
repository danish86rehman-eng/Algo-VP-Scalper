"""
APEX AI — CRG: Capital Risk Governor (v9)
Enforces hard risk rules. Last line of defense before any trade executes.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple
from agents.agent_base import BaseAgent, AgentVote
from intelligence.portfolio_engine import PortfolioState
from fund.capital_governance import GovernanceMode


@dataclass
class CRGContext:
    symbol: str
    direction: str
    risk_pct: float
    portfolio: PortfolioState
    governance_mode: str
    daily_dd_pct: float
    max_dd_pct: float = 10.0
    max_total_risk_pct: float = 6.0
    max_positions: int = 4


class CapitalRiskGovernor(BaseAgent):
    """
    CRG — the hardest gate in the system.
    If CRG says no → no trade, no exceptions.
    Enforces: max drawdown, max exposure, max positions, governance mode.
    """

    # Default governance limits — overridden by config["governance_limits"]
    # if passed to __init__. The prior hardcoded 2.5%/AGGRESSIVE cap was
    # incompatible with config["risk"]["high_clarity_risk_pct"]=16.0 and
    # produced a chronic ABSTAIN at Gate 3 for every HIGH-clarity setup.
    DEFAULT_GOVERNANCE_LIMITS = {
        "AGGRESSIVE":   20.0,
        "BALANCED":     12.0,
        "DEFENSIVE":     4.0,
        "PRESERVATION":  1.0,
    }

    def __init__(self, governance_limits: dict = None):
        super().__init__("CRG")
        # Allow per-mode caps to be injected from config.json. Filter out
        # documentation/underscore keys so JSON _doc fields are ignored.
        if governance_limits:
            self.GOVERNANCE_LIMITS = {
                k: float(v) for k, v in governance_limits.items()
                if not str(k).startswith("_") and isinstance(v, (int, float))
            }
        else:
            self.GOVERNANCE_LIMITS = dict(self.DEFAULT_GOVERNANCE_LIMITS)

    def analyze(self, context: CRGContext) -> AgentVote:
        dd   = context.daily_dd_pct
        mode = context.governance_mode
        port = context.portfolio

        # ── Hard stops ─────────────────────────────────────────────────────
        if dd >= context.max_dd_pct:
            return self._make_vote("ABSTAIN", 1.0,
                f"🚨 MAX DRAWDOWN HIT ({dd:.1f}%). Trading suspended.")

        if mode == "PRESERVATION":
            return self._make_vote("ABSTAIN", 1.0,
                f"🛡️ PRESERVATION mode — no new trades until capital recovered.")

        # ── Governance risk cap ────────────────────────────────────────────
        max_risk = self.GOVERNANCE_LIMITS.get(mode, 2.0)
        if context.risk_pct > max_risk:
            return self._make_vote("ABSTAIN", 0.9,
                f"Risk {context.risk_pct:.2f}% exceeds {mode} limit ({max_risk}%)")

        # ── Portfolio limits ───────────────────────────────────────────────
        if len(port.open_symbols) >= context.max_positions:
            return self._make_vote("ABSTAIN", 0.9,
                f"Max positions ({context.max_positions}) reached")

        total_risk = port.total_risk_pct + context.risk_pct
        if total_risk > context.max_total_risk_pct:
            return self._make_vote("ABSTAIN", 0.9,
                f"Total risk {total_risk:.1f}% exceeds limit ({context.max_total_risk_pct}%)")

        # ── Correlated position check ──────────────────────────────────────
        if port.correlated_pairs:
            for (s1, s2, corr) in port.correlated_pairs:
                if context.symbol in (s1, s2):
                    return self._make_vote("ABSTAIN", 0.85,
                        f"Correlated position exists: {s1}/{s2} (ρ={corr:.2f})")

        # ── Defensive mode — require higher conviction ─────────────────────
        if mode == "DEFENSIVE" and context.risk_pct > 1.0:
            return self._make_vote("ABSTAIN", 0.7,
                f"DEFENSIVE mode: risk {context.risk_pct:.2f}% > 1.0% max")

        # ── All checks passed ──────────────────────────────────────────────
        conf = min(1.0, 0.7 + (max_risk - context.risk_pct) / max_risk * 0.2)
        return self._make_vote("NEUTRAL", conf,
            f"CRG APPROVED: {mode} mode, risk={context.risk_pct:.2f}%, "
            f"DD={dd:.1f}%, positions={len(port.open_symbols)}")
