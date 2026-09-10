"""
APEX AI — Liquidity Engine (v1 Core)
Identifies BSL, SSL, equal highs/lows, previous day H/L, session H/L.
Core question: WHERE are traders trapped?
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
import pandas as pd
import numpy as np
from datetime import datetime, timezone


@dataclass
class LiquidityPool:
    level: float
    pool_type: str          # 'BSL' or 'SSL'
    strength: float         # 0.0 – 1.0
    origin: str             # 'swing' | 'equal_hl' | 'prev_day' | 'session'
    timestamp: Optional[datetime] = None
    swept: bool = False

    def __repr__(self):
        return f"LP({self.pool_type}@{self.level:.4f} str={self.strength:.2f} [{self.origin}])"


@dataclass
class LiquidityMap:
    symbol: str
    timeframe: str
    bsl_pools: List[LiquidityPool] = field(default_factory=list)
    ssl_pools: List[LiquidityPool] = field(default_factory=list)
    nearest_bsl: Optional[LiquidityPool] = None
    nearest_ssl: Optional[LiquidityPool] = None
    prev_day_high: Optional[float] = None
    prev_day_low: Optional[float] = None
    session_high: Optional[float] = None
    session_low: Optional[float] = None
    current_price: float = 0.0
    timestamp: Optional[datetime] = None

    def nearest_target(self) -> Optional[LiquidityPool]:
        all_pools = [p for p in self.bsl_pools + self.ssl_pools if not p.swept]
        if not all_pools:
            return None
        return min(all_pools, key=lambda p: abs(p.level - self.current_price))

    def premium_or_discount(self) -> str:
        """Is price in premium (above equilibrium) or discount (below)?"""
        if not self.bsl_pools or not self.ssl_pools:
            return "UNKNOWN"
        highest_bsl = max(p.level for p in self.bsl_pools)
        lowest_ssl = min(p.level for p in self.ssl_pools)
        equilibrium = (highest_bsl + lowest_ssl) / 2
        return "PREMIUM" if self.current_price > equilibrium else "DISCOUNT"


class LiquidityEngine:
    """
    APEX Liquidity Engine — identifies all liquidity pools in the market.
    """

    def __init__(self, swing_lookback: int = 5, equal_tolerance_pct: float = 0.002):
        self.swing_lookback = swing_lookback
        self.equal_tolerance_pct = equal_tolerance_pct

    def analyze(self, df: pd.DataFrame, symbol: str, timeframe: str,
                session_high: Optional[float] = None,
                session_low: Optional[float] = None) -> LiquidityMap:
        """Full liquidity analysis. df must have: time, open, high, low, close, volume."""
        lmap = LiquidityMap(symbol=symbol, timeframe=timeframe)
        lmap.timestamp = datetime.now(timezone.utc)

        if df is None or len(df) < self.swing_lookback * 2 + 2:
            return lmap

        lmap.current_price = float(df['close'].iloc[-1])
        n = self.swing_lookback

        # ── 1. Swing High/Low Liquidity ────────────────────────────────────
        swing_highs, swing_lows = self._detect_swings(df, n)
        for sh in swing_highs:
            lmap.bsl_pools.append(LiquidityPool(
                level=sh['level'], pool_type='BSL',
                strength=sh['strength'], origin='swing',
                timestamp=sh.get('time')))
        for sl in swing_lows:
            lmap.ssl_pools.append(LiquidityPool(
                level=sl['level'], pool_type='SSL',
                strength=sl['strength'], origin='swing',
                timestamp=sl.get('time')))

        # ── 2. Equal Highs / Equal Lows ────────────────────────────────────
        eq_highs, eq_lows = self._detect_equal_hl(df)
        for eh in eq_highs:
            lmap.bsl_pools.append(LiquidityPool(
                level=eh, pool_type='BSL', strength=0.88, origin='equal_hl'))
        for el in eq_lows:
            lmap.ssl_pools.append(LiquidityPool(
                level=el, pool_type='SSL', strength=0.88, origin='equal_hl'))

        # ── 3. Previous Day High / Low ─────────────────────────────────────
        pdh, pdl = self._prev_day_hl(df)
        if pdh:
            lmap.prev_day_high = pdh
            lmap.bsl_pools.append(LiquidityPool(
                level=pdh, pool_type='BSL', strength=0.92, origin='prev_day'))
        if pdl:
            lmap.prev_day_low = pdl
            lmap.ssl_pools.append(LiquidityPool(
                level=pdl, pool_type='SSL', strength=0.92, origin='prev_day'))

        # ── 4. Session High / Low ──────────────────────────────────────────
        if session_high:
            lmap.session_high = session_high
            lmap.bsl_pools.append(LiquidityPool(
                level=session_high, pool_type='BSL', strength=0.75, origin='session'))
        if session_low:
            lmap.session_low = session_low
            lmap.ssl_pools.append(LiquidityPool(
                level=session_low, pool_type='SSL', strength=0.75, origin='session'))

        # ── Sort & trim ────────────────────────────────────────────────────
        cp = lmap.current_price
        lmap.bsl_pools = sorted(
            [p for p in lmap.bsl_pools if p.level > cp], key=lambda p: p.level)[:8]
        lmap.ssl_pools = sorted(
            [p for p in lmap.ssl_pools if p.level < cp],
            key=lambda p: p.level, reverse=True)[:8]

        lmap.nearest_bsl = lmap.bsl_pools[0] if lmap.bsl_pools else None
        lmap.nearest_ssl = lmap.ssl_pools[0] if lmap.ssl_pools else None
        return lmap

    # ── Private helpers ────────────────────────────────────────────────────

    def _detect_swings(self, df: pd.DataFrame, n: int):
        highs, lows = [], []
        hi = df['high'].values
        lo = df['low'].values
        times = df['time'].values if 'time' in df.columns else [None] * len(df)

        for i in range(n, len(df) - n):
            window_hi = hi[i - n: i + n + 1]
            window_lo = lo[i - n: i + n + 1]

            if hi[i] == window_hi.max():
                std = window_hi.std() or 1e-6
                strength = min(1.0, 0.55 + (hi[i] - window_hi.mean()) / std * 0.15)
                highs.append({'level': float(hi[i]), 'strength': strength,
                               'index': i, 'time': times[i]})

            if lo[i] == window_lo.min():
                std = window_lo.std() or 1e-6
                strength = min(1.0, 0.55 + (window_lo.mean() - lo[i]) / std * 0.15)
                lows.append({'level': float(lo[i]), 'strength': strength,
                              'index': i, 'time': times[i]})

        highs = self._dedup(highs)[-10:]
        lows  = self._dedup(lows)[-10:]
        return highs, lows

    def _detect_equal_hl(self, df: pd.DataFrame):
        tol = self.equal_tolerance_pct
        eq_h = self._find_clusters(df['high'].values[-50:], tol)
        eq_l = self._find_clusters(df['low'].values[-50:], tol)
        return eq_h, eq_l

    def _find_clusters(self, values: np.ndarray, tol: float) -> List[float]:
        if len(values) < 2:
            return []
        clusters, used = [], set()
        sv = np.sort(values)[::-1]
        for i, v in enumerate(sv):
            if i in used:
                continue
            grp = [v]
            for j, v2 in enumerate(sv):
                if j != i and j not in used and abs(v2 - v) / (v + 1e-10) <= tol:
                    grp.append(v2)
                    used.add(j)
            if len(grp) >= 2:
                clusters.append(float(np.mean(grp)))
            used.add(i)
        return clusters

    def _prev_day_hl(self, df: pd.DataFrame):
        if 'time' not in df.columns:
            return None, None
        df2 = df.copy()
        df2['_date'] = pd.to_datetime(df2['time']).dt.date
        today = df2['_date'].iloc[-1]
        yest = df2[df2['_date'] < today]
        if yest.empty:
            return None, None
        last_date = yest['_date'].iloc[-1]
        prev = yest[yest['_date'] == last_date]
        return float(prev['high'].max()), float(prev['low'].min())

    def _dedup(self, objs: list) -> list:
        tol = self.equal_tolerance_pct
        result = []
        for obj in objs:
            if not any(abs(obj['level'] - r['level']) / (obj['level'] + 1e-10) < tol
                       for r in result):
                result.append(obj)
        return result
