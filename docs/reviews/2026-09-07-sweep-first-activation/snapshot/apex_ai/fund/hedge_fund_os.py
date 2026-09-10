"""
APEX AI — Hedge Fund OS (v10)
5-Gate Execution Approval Pipeline.
All 5 gates must pass or trade is rejected.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from datetime import datetime, timezone
import logging

from agents.agent_base import AgentVote
from fund.simulation_engine import SimulationResult
from intelligence.risk_engine import RiskAllocation
from intelligence.portfolio_engine import PortfolioState
from core.execution_models import TradeSetup

logger = logging.getLogger("APEX.HedgeFundOS")


@dataclass
class GateResult:
    gate_number: int
    gate_name: str
    passed: bool
    reason: str


@dataclass
class ApprovalDecision:
    approved: bool
    gates: List[GateResult] = field(default_factory=list)
    consensus_score: float = 0.0    # % of agents aligned
    final_direction: str = "NONE"
    failure_gate: Optional[int] = None
    timestamp: Optional[datetime] = None

    def summary(self) -> str:
        status = "✅ APPROVED" if self.approved else "❌ REJECTED"
        gates_str = " | ".join(
            f"G{g.gate_number}{'✅' if g.passed else '❌'}" for g in self.gates)
        return f"{status} [{gates_str}] consensus={self.consensus_score:.0%}"


class HedgeFundOS:
    """
    APEX Hedge Fund OS — the institutional 5-gate execution approval system.

    Gate 1: Simulation Approval
    Gate 2: Agent Consensus (min_agent_consensus of the votes SUPPLIED)
    Gate 3: Risk Governor Approval (CRG)
    Gate 4: Portfolio Clearance
    Gate 5: Regime Alignment
    """

    def __init__(self, min_agent_consensus: int = 5,
                 max_positions: int = 4, max_total_risk_pct: float = 6.0):
        """
        Args:
            min_agent_consensus : Aligned votes required at Gate 2.
            max_positions       : Portfolio position ceiling enforced at Gate 4.
            max_total_risk_pct  : Portfolio exposure ceiling enforced at Gate 4.

        The two portfolio limits used to be hard-coded literals (4 and 6.0)
        inside `evaluate()` while config declared max_positions=2 and
        max_total_exposure_pct=40.0 — so CRG and Gate 4 enforced different
        books. They are now injected from the same config keys CRG reads.
        """
        self.min_agent_consensus = min_agent_consensus
        self.max_positions = int(max_positions)
        self.max_total_risk_pct = float(max_total_risk_pct)

    def evaluate(self, setup: TradeSetup,
                 simulation: SimulationResult,
                 agent_votes: Dict[str, AgentVote],
                 crg_vote: AgentVote,
                 risk: RiskAllocation,
                 portfolio: PortfolioState,
                 regime_name: str,
                 preferred_model: str) -> ApprovalDecision:

        decision = ApprovalDecision(approved=False, timestamp=datetime.now(timezone.utc))
        gates = []

        # ══════════════════════════════════════════════════════════════════
        # GATE 1: Simulation Approval
        # ══════════════════════════════════════════════════════════════════
        g1 = GateResult(1, "Simulation",
                        simulation.approved,
                        f"WinProb={simulation.win_probability:.1%} EV={simulation.expected_value:.2f} "
                        f"— {'PASS' if simulation.approved else 'FAIL'}")
        gates.append(g1)
        if not g1.passed:
            decision.gates = gates
            decision.failure_gate = 1
            self._log_rejection(decision, g1)
            return decision

        # ══════════════════════════════════════════════════════════════════
        # GATE 2: Agent Consensus
        # ══════════════════════════════════════════════════════════════════
        aligned_direction = setup.direction  # BUY→BULLISH, SELL→BEARISH
        expected_vote = "BULLISH" if aligned_direction == "BUY" else "BEARISH"

        aligned_agents = [
            name for name, vote in agent_votes.items()
            if vote.vote == expected_vote or
               (vote.vote == "NEUTRAL" and vote.confidence >= 0.55)
        ]
        abstained = [name for name, vote in agent_votes.items() if vote.vote == "ABSTAIN"]
        total_agents = len(agent_votes)
        consensus_count = len(aligned_agents)
        consensus_pct = consensus_count / max(total_agents, 1)

        g2_passed = consensus_count >= self.min_agent_consensus and not abstained
        g2 = GateResult(2, "Agent Consensus",
                        g2_passed,
                        f"{consensus_count}/{total_agents} agents aligned "
                        f"(need {self.min_agent_consensus}). "
                        f"Abstained: {abstained or 'none'}")
        gates.append(g2)
        decision.consensus_score = consensus_pct
        if not g2.passed:
            decision.gates = gates
            decision.failure_gate = 2
            self._log_rejection(decision, g2)
            return decision

        # ══════════════════════════════════════════════════════════════════
        # GATE 3: CRG Risk Approval
        # ══════════════════════════════════════════════════════════════════
        g3_passed = crg_vote.vote != "ABSTAIN" and risk.approved
        # Include crg_vote.reason so the specific CRG block (e.g. governance
        # limit, drawdown, correlated position) is visible in logs — prior
        # builds only surfaced risk.reason, hiding the actual CRG verdict.
        g3 = GateResult(3, "CRG Risk Approval", g3_passed,
                        f"CRG: {crg_vote.vote} ({crg_vote.reasoning}) | "
                        f"Risk: {risk.reason}")
        gates.append(g3)
        if not g3.passed:
            decision.gates = gates
            decision.failure_gate = 3
            self._log_rejection(decision, g3)
            return decision

        # ══════════════════════════════════════════════════════════════════
        # GATE 4: Portfolio Clearance
        # ══════════════════════════════════════════════════════════════════
        port_ok = (len(portfolio.open_symbols) < self.max_positions and
                   portfolio.total_risk_pct < self.max_total_risk_pct and
                   not portfolio.correlated_pairs)
        warnings_str = "; ".join(portfolio.warnings) if portfolio.warnings else "Clean"
        g4 = GateResult(4, "Portfolio Clearance", port_ok,
                        f"Positions={len(portfolio.open_symbols)}/{self.max_positions}, "
                        f"TotalRisk={portfolio.total_risk_pct:.1f}%/"
                        f"{self.max_total_risk_pct:.1f}%. {warnings_str}")
        gates.append(g4)
        if not g4.passed:
            decision.gates = gates
            decision.failure_gate = 4
            self._log_rejection(decision, g4)
            return decision

        # ══════════════════════════════════════════════════════════════════
        # GATE 5: Regime Alignment
        # ══════════════════════════════════════════════════════════════════
        regime_model_match = (
            (regime_name == "MANIPULATION" and setup.model_type == "RETURN") or
            (regime_name == "EXPANSION"    and setup.model_type == "CONTINUATION") or
            (regime_name in ("ROTATION", "TRANSITION") and setup.model_type in ("RETURN", "CONTINUATION"))
        )
        g5 = GateResult(5, "Regime Alignment", regime_model_match,
                        f"Regime={regime_name}, Model={setup.model_type}, "
                        f"Preferred={preferred_model}")
        gates.append(g5)
        if not g5.passed:
            decision.gates = gates
            decision.failure_gate = 5
            self._log_rejection(decision, g5)
            return decision

        # ══════════════════════════════════════════════════════════════════
        # ALL GATES PASSED
        # ══════════════════════════════════════════════════════════════════
        decision.approved = True
        decision.gates = gates
        decision.final_direction = aligned_direction
        logger.info(f"🟢 ALL GATES PASSED: {setup.symbol} {setup.direction} "
                   f"RR={setup.risk_reward:.1f} conf={setup.confidence:.2f}")
        return decision

    def _log_rejection(self, decision: ApprovalDecision, failed_gate: GateResult):
        logger.warning(f"🔴 Gate {failed_gate.gate_number} ({failed_gate.gate_name}) FAILED: "
                      f"{failed_gate.reason}")
