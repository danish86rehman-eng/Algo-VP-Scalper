"""
APEX AI — MSA: Market Structure Agent
Votes based on structure trend + BOS/MSS + MTF alignment.
"""
from __future__ import annotations
from dataclasses import dataclass
from agents.agent_base import BaseAgent, AgentVote
from core.structure_engine import StructureState
from market.multi_timeframe import MTFContext


@dataclass
class MSAContext:
    structure: StructureState
    mtf: MTFContext


class MarketStructureAgent(BaseAgent):
    """
    MSA — evaluates multi-timeframe structure for directional vote.
    """

    def __init__(self):
        super().__init__("MSA")

    def analyze(self, context: MSAContext) -> AgentVote:
        st  = context.structure
        mtf = context.mtf

        if not st.is_tradeable():
            return self._make_vote("NEUTRAL", 0.3, "Structure not tradeable (no BOS/MSS)")

        base_conf = 0.5

        # MSS gets extra conviction (structural shift = higher probability reversal)
        if st.mss_confirmed and st.last_mss:
            if st.trend == "BULLISH":
                conf = base_conf + 0.2 + mtf.alignment_score * 0.2
                return self._make_vote("BULLISH", min(1.0, conf),
                    f"Bullish MSS confirmed at {st.last_mss.level:.4f}. MTF={mtf.structure_bias}")
            else:
                conf = base_conf + 0.2 + mtf.alignment_score * 0.2
                return self._make_vote("BEARISH", min(1.0, conf),
                    f"Bearish MSS confirmed at {st.last_mss.level:.4f}. MTF={mtf.structure_bias}")

        # BOS: trend continuation
        if st.bos_confirmed:
            if st.trend == "BULLISH":
                conf = base_conf + mtf.alignment_score * 0.25
                return self._make_vote("BULLISH", min(1.0, conf),
                    f"Bullish BOS. HH={st.recent_hh} HL={st.recent_hl}. MTF aligned={mtf.aligned}")
            else:
                conf = base_conf + mtf.alignment_score * 0.25
                return self._make_vote("BEARISH", min(1.0, conf),
                    f"Bearish BOS. LH={st.recent_lh} LL={st.recent_ll}. MTF aligned={mtf.aligned}")

        return self._make_vote("NEUTRAL", 0.35, f"Ranging structure. MTF={mtf.macro_bias}")
