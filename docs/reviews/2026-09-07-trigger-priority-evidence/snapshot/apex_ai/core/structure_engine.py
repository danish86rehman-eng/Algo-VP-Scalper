"""
APEX AI — Structure Engine (v1 Core)
Detects MSS, BOS, trend state. Rule: No structure shift → no trade.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import pandas as pd
import numpy as np
from datetime import datetime, timezone


@dataclass
class StructurePoint:
    level: float
    point_type: str   # 'HH' | 'HL' | 'LH' | 'LL'
    index: int
    timestamp: Optional[datetime] = None

    def __repr__(self):
        return f"SP({self.point_type}@{self.level:.4f})"


@dataclass
class StructureState:
    symbol: str
    timeframe: str
    trend: str = "RANGING"
    last_bos: Optional[StructurePoint] = None
    last_mss: Optional[StructurePoint] = None
    recent_hh: Optional[float] = None
    recent_hl: Optional[float] = None
    recent_lh: Optional[float] = None
    recent_ll: Optional[float] = None
    bos_confirmed: bool = False
    mss_confirmed: bool = False
    structure_points: List[StructurePoint] = field(default_factory=list)
    timestamp: Optional[datetime] = None

    def is_tradeable(self) -> bool:
        return self.bos_confirmed or self.mss_confirmed

    def bias(self) -> str:
        return self.trend


class StructureEngine:
    def __init__(self, swing_lookback: int = 5):
        self.swing_lookback = swing_lookback

    def analyze(self, df: pd.DataFrame, symbol: str, timeframe: str) -> StructureState:
        state = StructureState(symbol=symbol, timeframe=timeframe,
                               timestamp=datetime.now(timezone.utc))
        if df is None or len(df) < 20:
            return state
        swings = self._identify_swings(df)
        if len(swings) < 4:
            return state
        state.structure_points = swings
        trend, bos, mss = self._classify(swings)
        state.trend = trend
        for sp in swings:
            if sp.point_type == 'HH':
                state.recent_hh = sp.level
            elif sp.point_type == 'HL':
                state.recent_hl = sp.level
            elif sp.point_type == 'LH':
                state.recent_lh = sp.level
            elif sp.point_type == 'LL':
                state.recent_ll = sp.level
        if bos:
            state.last_bos = bos
            state.bos_confirmed = True
        if mss:
            state.last_mss = mss
            state.mss_confirmed = True
        return state

    def _identify_swings(self, df: pd.DataFrame) -> List[StructurePoint]:
        n = self.swing_lookback
        hi = df['high'].values
        lo = df['low'].values
        times = df['time'].values if 'time' in df.columns else [None] * len(df)
        raw_h, raw_l = [], []
        for i in range(n, len(df) - n):
            if hi[i] == max(hi[i-n:i+n+1]):
                raw_h.append((i, float(hi[i]), times[i]))
            if lo[i] == min(lo[i-n:i+n+1]):
                raw_l.append((i, float(lo[i]), times[i]))
        all_pts = sorted([(idx, lv, t, True) for idx, lv, t in raw_h] +
                         [(idx, lv, t, False) for idx, lv, t in raw_l], key=lambda x: x[0])
        alt, last_high = [], None
        for idx, lv, t, is_high in all_pts:
            if last_high is None or is_high != last_high:
                alt.append((idx, lv, t, is_high))
                last_high = is_high
            else:
                prev = alt[-1]
                if (is_high and lv > prev[1]) or (not is_high and lv < prev[1]):
                    alt[-1] = (idx, lv, t, is_high)
        result: List[StructurePoint] = []
        for idx, lv, t, is_high in alt:
            prev_same = [p for p in result if (p.point_type in ('HH', 'LH')) == is_high]
            if not prev_same:
                label = 'HH' if is_high else 'LL'
            else:
                pl = prev_same[-1].level
                label = ('HH' if lv > pl else 'LH') if is_high else ('LL' if lv < pl else 'HL')
            result.append(StructurePoint(level=lv, point_type=label, index=idx, timestamp=t))
        return result[-20:]

    def _classify(self, swings: List[StructurePoint]) -> Tuple[str, Optional[StructurePoint], Optional[StructurePoint]]:
        recent = swings[-8:]
        hh = sum(1 for s in recent if s.point_type == 'HH')
        hl = sum(1 for s in recent if s.point_type == 'HL')
        lh = sum(1 for s in recent if s.point_type == 'LH')
        ll = sum(1 for s in recent if s.point_type == 'LL')
        trend = "BULLISH" if hh >= 2 and hl >= 1 else ("BEARISH" if ll >= 2 and lh >= 1 else "RANGING")
        bos = mss = None
        for i, sp in enumerate(swings[-4:][1:], 1):
            prev = swings[-4:][i-1]
            if sp.point_type == 'HH' and prev.point_type == 'HL' and trend == "BULLISH":
                bos = sp
            elif sp.point_type == 'LL' and prev.point_type == 'LH' and trend == "BEARISH":
                bos = sp
            elif sp.point_type == 'LH' and trend == "BULLISH":
                mss = sp
            elif sp.point_type == 'HL' and trend == "BEARISH":
                mss = sp
        return trend, bos, mss
