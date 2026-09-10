"""
APEX AI — CAIA: Cross-Asset Intelligence Agent
Monitors XAUUSD, DXY, BTC, USOIL alignment.
Diverging → caution. Aligned → strong conviction.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict
from agents.agent_base import BaseAgent, AgentVote
from core.structure_engine import StructureState


@dataclass
class CAIAContext:
    structures: Dict[str, StructureState]   # symbol → StructureState
    primary_symbol: str = "XAUUSD"


class CrossAssetIntelligenceAgent(BaseAgent):
    """
    CAIA — checks if cross-asset signals align with the primary symbol's direction.
    Uses inter-market relationships defined in claude.md.
    """

    # Known correlations: (symbol, correlation_direction_with_gold)
    # +1 = same direction, -1 = opposite direction
    RELATIONSHIPS = {
        "XAGUSD": +0.85,   # Silver follows gold
        "USOil":  +0.40,   # Oil/Gold mild positive
        "BTCUSD": +0.30,   # BTC/Gold risk asset
    }

    def __init__(self):
        super().__init__("CAIA")

    def analyze(self, context: CAIAContext) -> AgentVote:
        structures = context.structures
        primary    = context.primary_symbol

        primary_struct = structures.get(primary)
        if not primary_struct:
            return self._make_vote("NEUTRAL", 0.3, "No primary symbol data")

        primary_bias = primary_struct.trend
        if primary_bias == "RANGING":
            return self._make_vote("NEUTRAL", 0.35,
                f"{primary} is ranging — cross-asset alignment irrelevant")

        # Check alignment of correlated assets
        aligned_count = 0
        diverged_count = 0
        details = []

        for symbol, correlation in self.RELATIONSHIPS.items():
            struct = structures.get(symbol)
            if not struct or struct.trend == "RANGING":
                continue

            asset_bias = struct.trend  # BULLISH or BEARISH

            # Check if asset agrees with primary direction
            expected = primary_bias if correlation > 0 else \
                       ("BEARISH" if primary_bias == "BULLISH" else "BULLISH")

            if asset_bias == expected:
                aligned_count += 1
                details.append(f"{symbol}✅")
            else:
                diverged_count += 1
                details.append(f"{symbol}⚠️")

        total = aligned_count + diverged_count
        if total == 0:
            return self._make_vote("NEUTRAL", 0.4, "Insufficient cross-asset data")

        alignment_ratio = aligned_count / total

        if alignment_ratio >= 0.67:
            conf = 0.55 + alignment_ratio * 0.3
            return self._make_vote(primary_bias, min(1.0, conf),
                f"Cross-asset ALIGNED ({aligned_count}/{total}): {', '.join(details)}")
        elif alignment_ratio <= 0.33:
            conf = 0.45 + (1 - alignment_ratio) * 0.2
            return self._make_vote("NEUTRAL", conf,
                f"Cross-asset DIVERGING ({diverged_count}/{total}): {', '.join(details)} — CAUTION")
        else:
            return self._make_vote("NEUTRAL", 0.4,
                f"Mixed cross-asset signals: {', '.join(details)}")
