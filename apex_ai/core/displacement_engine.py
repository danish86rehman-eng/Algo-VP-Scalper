"""
APEX AI — Displacement Engine (v1 Core)
Detects FVGs, Order Blocks, measures momentum/imbalance.
Weak displacement = ignore setup.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
import pandas as pd
import numpy as np
from datetime import datetime, timezone


@dataclass
class FairValueGap:
    high: float
    low: float
    direction: str      # 'BULLISH' | 'BEARISH'
    midpoint: float
    size_pct: float
    bar_index: int
    filled: bool = False
    timestamp: Optional[datetime] = None

    def __repr__(self):
        return f"FVG({self.direction} {self.low:.4f}-{self.high:.4f})"

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high


@dataclass
class OrderBlock:
    high: float
    low: float
    direction: str      # 'BULLISH' (demand OB) | 'BEARISH' (supply OB)
    strength: float
    bar_index: int
    mitigated: bool = False
    timestamp: Optional[datetime] = None

    def __repr__(self):
        return f"OB({self.direction} {self.low:.4f}-{self.high:.4f})"

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high


@dataclass
class DisplacementMap:
    symbol: str
    timeframe: str
    fvgs: List[FairValueGap] = field(default_factory=list)
    order_blocks: List[OrderBlock] = field(default_factory=list)
    momentum_score: float = 0.0     # 0.0 – 1.0
    is_displaced: bool = False
    displacement_direction: str = "NONE"
    nearest_bullish_fvg: Optional[FairValueGap] = None
    nearest_bearish_fvg: Optional[FairValueGap] = None
    nearest_demand_ob: Optional[OrderBlock] = None
    nearest_supply_ob: Optional[OrderBlock] = None
    timestamp: Optional[datetime] = None


class DisplacementEngine:
    """
    APEX Displacement Engine — confirms institutional intent through
    imbalance creation, momentum, and order block identification.
    """

    def __init__(self, min_fvg_pct: float = 0.001, atr_multiplier: float = 1.5,
                 lookback: int = 30):
        self.min_fvg_pct = min_fvg_pct
        self.atr_multiplier = atr_multiplier
        self.lookback = lookback

    def analyze(self, df: pd.DataFrame, symbol: str, timeframe: str) -> DisplacementMap:
        dmap = DisplacementMap(symbol=symbol, timeframe=timeframe,
                               timestamp=datetime.now(timezone.utc))
        if df is None or len(df) < 10:
            return dmap

        cp = float(df['close'].iloc[-1])
        atr = self._calc_atr(df)
        dmap.fvgs = self._detect_fvgs(df, atr)
        dmap.order_blocks = self._detect_order_blocks(df, atr)
        dmap.momentum_score = self._momentum_score(df, atr)
        dmap.is_displaced = dmap.momentum_score >= 0.6

        # Displacement direction from last few bars
        last3 = df.iloc[-3:]
        net_move = float(last3['close'].iloc[-1]) - float(last3['close'].iloc[0])
        if abs(net_move) > atr * 0.5:
            dmap.displacement_direction = "BULLISH" if net_move > 0 else "BEARISH"

        # Nearest FVGs relative to current price
        bull_fvgs = [f for f in dmap.fvgs if f.direction == 'BULLISH' and f.high < cp and not f.filled]
        bear_fvgs = [f for f in dmap.fvgs if f.direction == 'BEARISH' and f.low > cp and not f.filled]
        dmap.nearest_bullish_fvg = max(bull_fvgs, key=lambda f: f.high) if bull_fvgs else None
        dmap.nearest_bearish_fvg = min(bear_fvgs, key=lambda f: f.low) if bear_fvgs else None

        # Nearest OBs
        demand_obs = [o for o in dmap.order_blocks if o.direction == 'BULLISH' and o.high < cp and not o.mitigated]
        supply_obs = [o for o in dmap.order_blocks if o.direction == 'BEARISH' and o.low > cp and not o.mitigated]
        dmap.nearest_demand_ob = max(demand_obs, key=lambda o: o.high) if demand_obs else None
        dmap.nearest_supply_ob = min(supply_obs, key=lambda o: o.low) if supply_obs else None

        return dmap

    def _detect_fvgs(self, df: pd.DataFrame, atr: float) -> List[FairValueGap]:
        fvgs = []
        closes = df['close'].values
        highs  = df['high'].values
        lows   = df['low'].values
        times  = df['time'].values if 'time' in df.columns else [None] * len(df)

        for i in range(2, min(len(df), self.lookback + 2)):
            # Bullish FVG: gap between candle[i-2] high and candle[i] low
            if lows[i] > highs[i-2]:
                size = lows[i] - highs[i-2]
                size_pct = size / (highs[i-2] + 1e-10)
                if size_pct >= self.min_fvg_pct:
                    fvgs.append(FairValueGap(
                        high=float(lows[i]), low=float(highs[i-2]),
                        direction='BULLISH',
                        midpoint=float((lows[i] + highs[i-2]) / 2),
                        size_pct=size_pct, bar_index=i,
                        timestamp=times[i] if times[i] is not None else None))
            # Bearish FVG: gap between candle[i-2] low and candle[i] high
            elif highs[i] < lows[i-2]:
                size = lows[i-2] - highs[i]
                size_pct = size / (lows[i-2] + 1e-10)
                if size_pct >= self.min_fvg_pct:
                    fvgs.append(FairValueGap(
                        high=float(lows[i-2]), low=float(highs[i]),
                        direction='BEARISH',
                        midpoint=float((lows[i-2] + highs[i]) / 2),
                        size_pct=size_pct, bar_index=i,
                        timestamp=times[i] if times[i] is not None else None))

        return fvgs[-15:]

    def _detect_order_blocks(self, df: pd.DataFrame, atr: float) -> List[OrderBlock]:
        obs = []
        o = df['open'].values
        h = df['high'].values
        l = df['low'].values
        c = df['close'].values
        times = df['time'].values if 'time' in df.columns else [None] * len(df)

        for i in range(1, min(len(df) - 1, self.lookback + 1)):
            body = abs(c[i] - o[i])
            if body < atr * 0.3:
                continue
            next_bar = c[i+1] if i+1 < len(df) else c[i]
            # Bearish OB: last up candle before strong down move
            if c[i] > o[i] and c[i+1] < l[i]:
                strength = min(1.0, body / atr)
                obs.append(OrderBlock(
                    high=float(h[i]), low=float(o[i]),
                    direction='BEARISH', strength=strength,
                    bar_index=i, timestamp=times[i] if times[i] is not None else None))
            # Bullish OB: last down candle before strong up move
            elif c[i] < o[i] and c[i+1] > h[i]:
                strength = min(1.0, body / atr)
                obs.append(OrderBlock(
                    high=float(o[i]), low=float(l[i]),
                    direction='BULLISH', strength=strength,
                    bar_index=i, timestamp=times[i] if times[i] is not None else None))

        return obs[-10:]

    def _momentum_score(self, df: pd.DataFrame, atr: float) -> float:
        if atr == 0 or len(df) < 5:
            return 0.0
        last5 = df.iloc[-5:]
        avg_body = (last5['close'] - last5['open']).abs().mean()
        score = min(1.0, avg_body / (atr * self.atr_multiplier))
        return round(float(score), 3)

    def _calc_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        if len(df) < period + 1:
            return float((df['high'] - df['low']).mean())
        hi = df['high'].values
        lo = df['low'].values
        cl = df['close'].values
        trs = []
        for i in range(1, len(df)):
            tr = max(hi[i] - lo[i], abs(hi[i] - cl[i-1]), abs(lo[i] - cl[i-1]))
            trs.append(tr)
        return float(np.mean(trs[-period:]))
