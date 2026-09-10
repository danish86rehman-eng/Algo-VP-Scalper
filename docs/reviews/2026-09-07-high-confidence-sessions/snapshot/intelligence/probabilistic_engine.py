"""
APEX AI — Probabilistic Engine (v4)
Aggregates all engine signals into Bull%/Bear%/Range% probability estimates.
No fixed bias — always adaptive.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timezone

from core.liquidity_engine import LiquidityMap
from core.manipulation_engine import ManipulationSignal
from core.structure_engine import StructureState
from core.displacement_engine import DisplacementMap
from market.multi_timeframe import MTFContext
from market.session_engine import SessionState


@dataclass
class ProbabilisticBias:
    bullish_pct: float   # 0–100
    bearish_pct: float
    range_pct: float
    dominant: str        # 'BULLISH' | 'BEARISH' | 'RANGING'
    confidence: float    # Overall signal confidence 0.0–1.0
    clarity: str         # 'HIGH' | 'MEDIUM' | 'LOW'
    explanation: str = ""
    timestamp: Optional[datetime] = None

    def __repr__(self):
        return (f"Bias(Bull={self.bullish_pct:.0f}% Bear={self.bearish_pct:.0f}% "
                f"Range={self.range_pct:.0f}% | {self.clarity} clarity)")


class ProbabilisticEngine:
    """
    APEX Probabilistic Engine — synthesizes all signals into a probability distribution.
    Weights: Structure (30%) + Liquidity/Manipulation (30%) + MTF (25%) + Displacement (15%)
    """

    WEIGHTS = {
        "structure":     0.30,
        "manipulation":  0.25,
        "mtf":           0.25,
        "displacement":  0.20,
    }

    def analyze(self, lmap: LiquidityMap, manip: ManipulationSignal,
                structure: StructureState, displacement: DisplacementMap,
                mtf: MTFContext, session: SessionState) -> ProbabilisticBias:

        now = datetime.now(timezone.utc)
        bull_score = 0.0
        bear_score = 0.0
        range_score = 0.0

        # ── Structure score ────────────────────────────────────────────────
        w = self.WEIGHTS["structure"]
        if structure.trend == "BULLISH":
            bull_score += w * (1.0 + (0.2 if structure.mss_confirmed else 0))
        elif structure.trend == "BEARISH":
            bear_score += w * (1.0 + (0.2 if structure.mss_confirmed else 0))
        else:
            range_score += w

        # ── Manipulation / Liquidity score ─────────────────────────────────
        w = self.WEIGHTS["manipulation"]
        if manip.detected:
            if manip.direction == "BULLISH":
                bull_score += w * manip.confidence
            elif manip.direction == "BEARISH":
                bear_score += w * manip.confidence
        else:
            # No manipulation detected — look at liquidity bias
            if lmap.nearest_bsl and lmap.nearest_ssl:
                dist_bsl = abs(lmap.nearest_bsl.level - lmap.current_price)
                dist_ssl = abs(lmap.nearest_ssl.level - lmap.current_price)
                if dist_ssl < dist_bsl:
                    bull_score += w * 0.4  # Price closer to SSL = might sweep then go up
                else:
                    bear_score += w * 0.4
            range_score += w * 0.3

        # ── MTF score ──────────────────────────────────────────────────────
        w = self.WEIGHTS["mtf"]
        if mtf.macro_bias == "BULLISH":
            bull_score += w * 0.5
        elif mtf.macro_bias == "BEARISH":
            bear_score += w * 0.5
        if mtf.structure_bias == "BULLISH":
            bull_score += w * 0.3
        elif mtf.structure_bias == "BEARISH":
            bear_score += w * 0.3
        if mtf.execution_bias == "BULLISH":
            bull_score += w * 0.2
        elif mtf.execution_bias == "BEARISH":
            bear_score += w * 0.2
        if not mtf.aligned:
            range_score += w * 0.3

        # ── Displacement score ─────────────────────────────────────────────
        w = self.WEIGHTS["displacement"]
        if displacement.is_displaced:
            if displacement.displacement_direction == "BULLISH":
                bull_score += w * displacement.momentum_score
            elif displacement.displacement_direction == "BEARISH":
                bear_score += w * displacement.momentum_score
        else:
            range_score += w * 0.5

        # ── Session weight adjustment ──────────────────────────────────────
        sw = session.session_weight
        bull_score  *= sw
        bear_score  *= sw
        range_score *= max(0.5, 1.0 - sw * 0.3)

        # Normalize to 100%
        total = bull_score + bear_score + range_score + 1e-10
        b_pct = round(bull_score / total * 100, 1)
        s_pct = round(bear_score / total * 100, 1)
        r_pct = round(max(0.0, 100 - b_pct - s_pct), 1)

        dominant = "BULLISH" if b_pct > s_pct and b_pct > r_pct else \
                   ("BEARISH" if s_pct > b_pct and s_pct > r_pct else "RANGING")

        confidence = round(min(1.0, max(b_pct, s_pct) / 100 + mtf.alignment_score * 0.2), 2)
        clarity = "HIGH" if confidence >= 0.70 else ("MEDIUM" if confidence >= 0.50 else "LOW")

        explanation = (
            f"Structure={structure.trend} | Manip={'✅' if manip.detected else '❌'} {manip.direction} | "
            f"MTF={mtf.dominant_direction()} aligned={mtf.aligned} | "
            f"Session={session.active_session}(×{session.session_weight})"
        )

        return ProbabilisticBias(
            bullish_pct=b_pct, bearish_pct=s_pct, range_pct=r_pct,
            dominant=dominant, confidence=confidence, clarity=clarity,
            explanation=explanation, timestamp=now)
