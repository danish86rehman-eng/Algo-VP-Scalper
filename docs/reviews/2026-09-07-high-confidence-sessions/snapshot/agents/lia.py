"""
APEX AI — LIA: Liquidity Intelligence Agent
Votes based on liquidity pool proximity + manipulation detection.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from agents.agent_base import BaseAgent, AgentVote
from core.liquidity_engine import LiquidityMap
from core.manipulation_engine import ManipulationSignal


@dataclass
class LIAContext:
    lmap: LiquidityMap
    manip: ManipulationSignal
    current_price: float


class LiquidityIntelligenceAgent(BaseAgent):
    """
    LIA — evaluates liquidity pools and manipulation to cast directional vote.
    If SSL swept + reversal → BULLISH
    If BSL swept + reversal → BEARISH
    """

    def __init__(self):
        super().__init__("LIA")

    def analyze(self, context: LIAContext) -> AgentVote:
        lmap  = context.lmap
        manip = context.manip
        cp    = context.current_price

        # SWEEP detected — highest conviction
        if manip.detected and manip.confidence >= 0.6:
            vote = "BULLISH" if manip.direction == "BULLISH" else "BEARISH"
            return self._make_vote(vote, manip.confidence,
                f"Manipulation detected: {manip.signal_type} → {manip.direction}. "
                f"Swept pool: {manip.swept_pool}")

        # No sweep — assess liquidity proximity bias
        if lmap.nearest_bsl and lmap.nearest_ssl:
            dist_bsl = abs(lmap.nearest_bsl.level - cp)
            dist_ssl = abs(lmap.nearest_ssl.level - cp)
            total = dist_bsl + dist_ssl + 1e-10
            # If price is closer to SSL → likely going up to sweep BSL
            if dist_ssl < dist_bsl * 0.6:
                conf = 0.45 + lmap.nearest_bsl.strength * 0.2
                return self._make_vote("BULLISH", conf,
                    f"Price near SSL ({lmap.nearest_ssl.level:.4f}), BSL target at {lmap.nearest_bsl.level:.4f}")
            # If price is closer to BSL → likely going down to sweep SSL
            elif dist_bsl < dist_ssl * 0.6:
                conf = 0.45 + lmap.nearest_ssl.strength * 0.2
                return self._make_vote("BEARISH", conf,
                    f"Price near BSL ({lmap.nearest_bsl.level:.4f}), SSL target at {lmap.nearest_ssl.level:.4f}")

        return self._make_vote("NEUTRAL", 0.3, "No clear liquidity setup")
