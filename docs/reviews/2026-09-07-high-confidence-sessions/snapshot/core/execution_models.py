"""
APEX AI — Execution Models (v1 Core)
Return Model: FVG/OB entry after sweep + displacement.
Continuation Model: strong BOS + momentum, no retracement.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timezone

from core.liquidity_engine import LiquidityMap
from core.manipulation_engine import ManipulationSignal
from core.structure_engine import StructureState
from core.displacement_engine import DisplacementMap


@dataclass
class TradeSetup:
    symbol: str
    direction: str          # 'BUY' | 'SELL'
    model_type: str         # 'RETURN' | 'CONTINUATION'
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    confidence: float       # 0.0 – 1.0
    entry_zone_high: float = 0.0
    entry_zone_low: float = 0.0
    reasoning: str = ""
    valid: bool = True
    timestamp: Optional[datetime] = None

    def __repr__(self):
        return (f"Setup({self.symbol} {self.direction} {self.model_type} "
                f"@ {self.entry_price:.4f} RR={self.risk_reward:.1f} "
                f"conf={self.confidence:.2f})")


class ExecutionModels:
    """
    APEX Execution Models — translates engine signals into concrete trade setups.
    Implements the two core ICT execution templates:
    1. Return Model  — price returns to FVG/OB after manipulation + displacement
    2. Continuation Model — strong momentum BOS with no retracement
    """

    def __init__(self, min_rr: float = 2.0, atr_sl_multiplier: float = 1.0):
        self.min_rr = min_rr
        self.atr_sl_multiplier = atr_sl_multiplier

    def evaluate(self, symbol: str, current_price: float, atr: float,
                 lmap: LiquidityMap, manip: ManipulationSignal,
                 structure: StructureState, displacement: DisplacementMap) -> Optional[TradeSetup]:
        """
        Evaluate all conditions and return a TradeSetup if valid, else None.
        """
        # GATE 1: Must have manipulation signal
        if not manip.detected:
            return None

        # GATE 2: Structure must be tradeable
        if not structure.is_tradeable():
            return None

        # GATE 3: Must have displacement
        if not displacement.is_displaced:
            return None

        # Try Return Model first (higher probability)
        setup = self._return_model(symbol, current_price, atr,
                                   lmap, manip, structure, displacement)
        if setup and setup.risk_reward >= self.min_rr:
            return setup

        # Try Continuation Model
        setup = self._continuation_model(symbol, current_price, atr,
                                         lmap, manip, structure, displacement)
        if setup and setup.risk_reward >= self.min_rr:
            return setup

        return None

    def _return_model(self, symbol, cp, atr, lmap, manip, structure, displacement) -> Optional[TradeSetup]:
        """
        Return Model: Entry at FVG or OB after liquidity sweep + displacement.
        Bullish: SSL swept → displacement up → entry at nearest bullish FVG/OB
        Bearish: BSL swept → displacement down → entry at nearest bearish FVG/OB
        """
        now = datetime.now(timezone.utc)

        if manip.is_bullish_sweep() and displacement.displacement_direction in ("BULLISH", "NONE"):
            # Look for bullish FVG or demand OB below current price
            entry_zone = None
            if displacement.nearest_bullish_fvg:
                fvg = displacement.nearest_bullish_fvg
                entry_zone = (fvg.low, fvg.high, fvg.midpoint)
            elif displacement.nearest_demand_ob:
                ob = displacement.nearest_demand_ob
                entry_zone = (ob.low, ob.high, (ob.low + ob.high) / 2)

            if entry_zone:
                entry_low, entry_high, entry = entry_zone
                sl = entry_low - atr * self.atr_sl_multiplier
                # TP = nearest BSL
                tp = lmap.nearest_bsl.level if lmap.nearest_bsl else entry + (entry - sl) * 3
                rr = (tp - entry) / (entry - sl) if (entry - sl) > 0 else 0
                confidence = round(min(1.0, 0.55 + manip.confidence * 0.25 +
                                       displacement.momentum_score * 0.2), 2)
                return TradeSetup(
                    symbol=symbol, direction='BUY', model_type='RETURN',
                    entry_price=entry, stop_loss=sl, take_profit=tp,
                    risk_reward=round(rr, 2), confidence=confidence,
                    entry_zone_high=entry_high, entry_zone_low=entry_low,
                    reasoning=(f"Return Model BUY: {manip.signal_type} detected. "
                               f"Displacement {displacement.displacement_direction}. "
                               f"Entry at FVG/OB {entry:.4f}. TP={tp:.4f} SL={sl:.4f}"),
                    timestamp=now)

        elif manip.is_bearish_sweep() and displacement.displacement_direction in ("BEARISH", "NONE"):
            entry_zone = None
            if displacement.nearest_bearish_fvg:
                fvg = displacement.nearest_bearish_fvg
                entry_zone = (fvg.low, fvg.high, fvg.midpoint)
            elif displacement.nearest_supply_ob:
                ob = displacement.nearest_supply_ob
                entry_zone = (ob.low, ob.high, (ob.low + ob.high) / 2)

            if entry_zone:
                entry_low, entry_high, entry = entry_zone
                sl = entry_high + atr * self.atr_sl_multiplier
                tp = lmap.nearest_ssl.level if lmap.nearest_ssl else entry - (sl - entry) * 3
                rr = (entry - tp) / (sl - entry) if (sl - entry) > 0 else 0
                confidence = round(min(1.0, 0.55 + manip.confidence * 0.25 +
                                       displacement.momentum_score * 0.2), 2)
                return TradeSetup(
                    symbol=symbol, direction='SELL', model_type='RETURN',
                    entry_price=entry, stop_loss=sl, take_profit=tp,
                    risk_reward=round(rr, 2), confidence=confidence,
                    entry_zone_high=entry_high, entry_zone_low=entry_low,
                    reasoning=(f"Return Model SELL: {manip.signal_type} detected. "
                               f"Entry at supply FVG/OB {entry:.4f}. TP={tp:.4f} SL={sl:.4f}"),
                    timestamp=now)
        return None

    def _continuation_model(self, symbol, cp, atr, lmap, manip, structure, displacement) -> Optional[TradeSetup]:
        """
        Continuation Model: strong BOS + high momentum, no retracement needed.
        """
        now = datetime.now(timezone.utc)
        if displacement.momentum_score < 0.75:
            return None

        if (structure.trend == "BULLISH" and structure.bos_confirmed and
                displacement.displacement_direction == "BULLISH"):
            sl = cp - atr * (self.atr_sl_multiplier + 0.5)
            tp = lmap.nearest_bsl.level if lmap.nearest_bsl else cp + (cp - sl) * 2.5
            rr = (tp - cp) / (cp - sl) if (cp - sl) > 0 else 0
            confidence = round(min(1.0, 0.5 + displacement.momentum_score * 0.35), 2)
            return TradeSetup(
                symbol=symbol, direction='BUY', model_type='CONTINUATION',
                entry_price=cp, stop_loss=sl, take_profit=tp,
                risk_reward=round(rr, 2), confidence=confidence,
                reasoning=(f"Continuation BUY: BOS confirmed, momentum={displacement.momentum_score:.2f}. "
                           f"Target BSL {tp:.4f}"),
                timestamp=now)

        elif (structure.trend == "BEARISH" and structure.bos_confirmed and
              displacement.displacement_direction == "BEARISH"):
            sl = cp + atr * (self.atr_sl_multiplier + 0.5)
            tp = lmap.nearest_ssl.level if lmap.nearest_ssl else cp - (sl - cp) * 2.5
            rr = (cp - tp) / (sl - cp) if (sl - cp) > 0 else 0
            confidence = round(min(1.0, 0.5 + displacement.momentum_score * 0.35), 2)
            return TradeSetup(
                symbol=symbol, direction='SELL', model_type='CONTINUATION',
                entry_price=cp, stop_loss=sl, take_profit=tp,
                risk_reward=round(rr, 2), confidence=confidence,
                reasoning=(f"Continuation SELL: BOS confirmed, momentum={displacement.momentum_score:.2f}. "
                           f"Target SSL {tp:.4f}"),
                timestamp=now)

        return None
