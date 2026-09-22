"""
APEX AI — Regime Engine (v5+)
Classifies market regime: Expansion / Manipulation / Rotation / Transition.
Execution strategy depends entirely on regime.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import pandas as pd
import numpy as np
from datetime import datetime, timezone

from core.structure_engine import StructureState
from core.manipulation_engine import ManipulationSignal
from core.displacement_engine import DisplacementMap


@dataclass
class RegimeState:
    regime: str = "ROTATION"          # 'EXPANSION' | 'MANIPULATION' | 'ROTATION' | 'TRANSITION'
    sub_regime: str = ""
    confidence: float = 0.0
    preferred_model: str = ""
    behavior_state: str = "OBSERVE"   # EXPLOIT | OBSERVE | ADAPT | REDUCE | DISENGAGE
    description: str = ""
    timestamp: Optional[datetime] = None
    # Observation fields only.  They expose values already used below; no
    # decision reads them.
    atr_ratio: float = 0.0
    momentum: float = 0.0
    structure_trend: str = "RANGING"
    manipulation_detected: bool = False
    manipulation_confidence: float = 0.0

    def allows_trading(self) -> bool:
        return self.behavior_state in ("EXPLOIT", "OBSERVE") and self.confidence >= 0.55

    def __repr__(self):
        return f"Regime({self.regime}/{self.sub_regime} state={self.behavior_state} conf={self.confidence:.2f})"


class RegimeEngine:
    """
    APEX Regime Engine — determines the current market operating mode.
    Uses ATR expansion, structure quality, sweep activity, and momentum.
    """

    def __init__(self, atr_expansion_threshold: float = 1.5,
                 range_threshold: float = 0.6):
        self.atr_expansion_threshold = atr_expansion_threshold
        self.range_threshold = range_threshold

    def analyze(self, df: pd.DataFrame, structure: StructureState,
                manip: ManipulationSignal, displacement: DisplacementMap,
                daily_dd_pct: float = 0.0) -> RegimeState:
        state = RegimeState(timestamp=datetime.now(timezone.utc))

        if df is None or len(df) < 20:
            state.regime = "ROTATION"
            state.behavior_state = "OBSERVE"
            state.confidence = 0.3
            return state

        atr_ratio = self._atr_ratio(df)
        momentum   = displacement.momentum_score
        is_swept   = manip.detected
        trend      = structure.trend
        state.atr_ratio = atr_ratio
        state.momentum = momentum
        state.structure_trend = trend
        state.manipulation_detected = is_swept
        state.manipulation_confidence = manip.confidence

        # ── Regime classification ──────────────────────────────────────────

        # EXPANSION: price is moving strongly in one direction
        if atr_ratio >= self.atr_expansion_threshold and momentum >= 0.65:
            state.regime = "EXPANSION"
            state.sub_regime = f"{trend}_EXPANSION" if trend != "RANGING" else "EXPANSION"
            state.preferred_model = "CONTINUATION"
            state.confidence = min(1.0, 0.6 + momentum * 0.2 + (atr_ratio - 1) * 0.1)
            state.behavior_state = "EXPLOIT" if state.confidence >= 0.70 else "OBSERVE"
            state.description = (f"Strong directional expansion. ATR ratio={atr_ratio:.2f}, "
                                  f"Momentum={momentum:.2f}. Use continuation model.")

        # MANIPULATION: sweep detected, real move incoming
        elif is_swept and manip.confidence >= 0.65:
            state.regime = "MANIPULATION"
            state.sub_regime = f"{manip.direction}_MANIPULATION"
            state.preferred_model = "RETURN"
            state.confidence = manip.confidence
            state.behavior_state = "EXPLOIT"
            state.description = (f"Manipulation regime: {manip.signal_type}. "
                                  f"Smart money setting up {manip.direction} move. "
                                  f"Use return model.")

        # ROTATION/RANGE: price going sideways, no clear direction
        elif atr_ratio < self.range_threshold and momentum < 0.4:
            state.regime = "ROTATION"
            state.sub_regime = "RANGE"
            state.preferred_model = "MEAN_REVERSION"
            state.confidence = 0.6
            state.behavior_state = "OBSERVE"
            state.description = ("Range / rotation regime. ATR contracting, "
                                  "no directional bias. Avoid or use mean reversion.")

        # TRANSITION: between regimes
        else:
            state.regime = "TRANSITION"
            state.sub_regime = "TRANSITION"
            state.preferred_model = "WAIT"
            state.confidence = 0.4
            state.behavior_state = "ADAPT"
            state.description = ("Transition regime: market switching modes. "
                                  "Wait for clarity before committing capital.")

        # ── Drawdown-based behavior override ──────────────────────────────
        if daily_dd_pct >= 7.0:
            state.behavior_state = "DISENGAGE"
            state.description += " | ⚠️ Drawdown limit — DISENGAGE"
        elif daily_dd_pct >= 5.0:
            state.behavior_state = "REDUCE"
            state.description += " | ⚠️ Drawdown elevated — REDUCE exposure"

        return state

    def _atr_ratio(self, df: pd.DataFrame, short: int = 5, long: int = 20) -> float:
        """Ratio of short-term ATR to long-term ATR. >1 = expanding, <1 = contracting."""
        if len(df) < long + 1:
            return 1.0
        hi = df['high'].values
        lo = df['low'].values
        cl = df['close'].values
        trs = [max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
               for i in range(1, len(df))]
        atr_s = np.mean(trs[-short:])
        atr_l = np.mean(trs[-long:])
        return round(float(atr_s / (atr_l + 1e-10)), 3)
