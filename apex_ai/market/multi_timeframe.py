"""
APEX AI — Multi-Timeframe Engine (v3)
Top-down analysis: D1 → H4/H1 → M15/M5.
Scores alignment across timeframes to determine conviction.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import pandas as pd
from datetime import datetime, timezone

from core.structure_engine import StructureEngine, StructureState
from core.liquidity_engine import LiquidityEngine, LiquidityMap


@dataclass
class MTFContext:
    symbol: str
    macro_bias: str = "RANGING"        # D1 direction
    structure_bias: str = "RANGING"    # H4 direction
    execution_bias: str = "RANGING"    # M15 direction
    alignment_score: float = 0.0       # 0.0 – 1.0 (how aligned all TFs are)
    aligned: bool = False
    per_tf: Dict[str, str] = field(default_factory=dict)  # TF → bias
    timestamp: Optional[datetime] = None

    def dominant_direction(self) -> str:
        if self.aligned:
            return self.macro_bias
        bulls = sum(1 for b in self.per_tf.values() if b == "BULLISH")
        bears = sum(1 for b in self.per_tf.values() if b == "BEARISH")
        if bulls > bears:
            return "BULLISH"
        elif bears > bulls:
            return "BEARISH"
        return "RANGING"


class MultiTimeframeEngine:
    """
    APEX Multi-Timeframe Engine — stacks D1/H4/H1/M15/M5 structure.
    Question: Is the lower TF aligning with the higher TF intent?
    """

    TF_GROUPS = {
        "macro":     ["D1"],
        "structure": ["H4", "H1"],
        "execution": ["M15", "M5"],
    }

    def __init__(self, swing_lookback: int = 5):
        self.struct_engine = StructureEngine(swing_lookback=swing_lookback)
        self.liq_engine = LiquidityEngine(swing_lookback=swing_lookback)

    def analyze(self, symbol: str,
                ohlcv_data: Dict[str, pd.DataFrame]) -> MTFContext:
        """
        ohlcv_data: dict mapping timeframe string → DataFrame
        e.g. {"D1": df_d1, "H4": df_h4, ...}
        """
        ctx = MTFContext(symbol=symbol, timestamp=datetime.now(timezone.utc))
        biases: Dict[str, str] = {}

        for tf, df in ohlcv_data.items():
            if df is None or len(df) < 20:
                biases[tf] = "RANGING"
                continue
            state = self.struct_engine.analyze(df, symbol, tf)
            biases[tf] = state.trend

        ctx.per_tf = biases

        # Macro bias from D1
        ctx.macro_bias = biases.get("D1", "RANGING")

        # Structure bias from H4 (primary) or H1 (fallback)
        ctx.structure_bias = biases.get("H4", biases.get("H1", "RANGING"))

        # Execution bias from M15 (primary) or M5 (fallback)
        ctx.execution_bias = biases.get("M15", biases.get("M5", "RANGING"))

        # Alignment score
        valid = [b for b in biases.values() if b != "RANGING"]
        if not valid:
            ctx.alignment_score = 0.0
        else:
            dominant = max(set(valid), key=valid.count)
            ctx.alignment_score = round(valid.count(dominant) / len(valid), 2)
            ctx.aligned = ctx.alignment_score >= 0.67

        return ctx

    def is_aligned_bullish(self, ctx: MTFContext) -> bool:
        return (ctx.macro_bias in ("BULLISH", "RANGING") and
                ctx.structure_bias == "BULLISH" and
                ctx.execution_bias == "BULLISH" and
                ctx.alignment_score >= 0.6)

    def is_aligned_bearish(self, ctx: MTFContext) -> bool:
        return (ctx.macro_bias in ("BEARISH", "RANGING") and
                ctx.structure_bias == "BEARISH" and
                ctx.execution_bias == "BEARISH" and
                ctx.alignment_score >= 0.6)
