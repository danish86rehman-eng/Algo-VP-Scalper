"""
APEX AI — RDA: Regime Detection Agent
Votes based on current market regime and behavior state.
"""
from __future__ import annotations
from dataclasses import dataclass
from agents.agent_base import BaseAgent, AgentVote
from intelligence.regime_engine import RegimeState


@dataclass
class RDAContext:
    regime: RegimeState
    daily_dd_pct: float = 0.0


class RegimeDetectionAgent(BaseAgent):
    """
    RDA — assesses current market regime and determines if conditions
    favor exploitation or observation.
    """

    def __init__(self):
        super().__init__("RDA")

    def analyze(self, context: RDAContext) -> AgentVote:
        regime = context.regime
        dd     = context.daily_dd_pct

        # Hard stop states
        if regime.behavior_state == "DISENGAGE":
            return self._make_vote("ABSTAIN", 1.0,
                f"DISENGAGE: drawdown={dd:.1f}%. No trading.")

        if regime.behavior_state == "REDUCE":
            return self._make_vote("NEUTRAL", 0.5,
                f"REDUCE: drawdown={dd:.1f}%. Minimizing exposure.")

        # Regime votes
        if regime.regime == "MANIPULATION":
            direction = "BULLISH" if "BULLISH" in regime.sub_regime else "BEARISH"
            return self._make_vote(direction, regime.confidence,
                f"Manipulation regime → real move is {direction}. Model={regime.preferred_model}")

        if regime.regime == "EXPANSION":
            direction = "BULLISH" if "BULLISH" in regime.sub_regime else "BEARISH"
            return self._make_vote(direction, regime.confidence * 0.9,
                f"Expansion regime: {direction}. Use continuation model.")

        if regime.regime == "ROTATION":
            return self._make_vote("NEUTRAL", 0.5,
                "Range/rotation regime. Avoid directional trades.")

        # TRANSITION
        return self._make_vote("NEUTRAL", 0.35,
            "Transition regime: wait for clarity.")
