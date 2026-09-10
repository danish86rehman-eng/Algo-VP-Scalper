"""
APEX AI — Manipulation Engine (v1 Core)
Detects: liquidity sweeps, Judas Swing, inducement, false breakouts.
Core question: WHO is being trapped, and WHY?
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import pandas as pd
import numpy as np
from datetime import datetime, timezone

from core.liquidity_engine import LiquidityMap, LiquidityPool


@dataclass
class ManipulationSignal:
    detected: bool = False
    signal_type: str = "NONE"       # SWEEP_BSL | SWEEP_SSL | JUDAS_SWING | INDUCEMENT | FALSE_BREAKOUT
    direction: str = "NONE"         # BULLISH (swept SSL, now going up) | BEARISH (swept BSL, now going down)
    swept_pool: Optional[LiquidityPool] = None
    confidence: float = 0.0         # 0.0 – 1.0
    description: str = ""
    timestamp: Optional[datetime] = None

    def is_bullish_sweep(self) -> bool:
        return self.detected and self.direction == "BULLISH"

    def is_bearish_sweep(self) -> bool:
        return self.detected and self.direction == "BEARISH"


class ManipulationEngine:
    """
    APEX Manipulation Engine — detects engineered market moves designed
    to trap retail traders before the real institutional move.

    Per-Symbol Sweep Calibration (sweep_wick_pct):
    ─────────────────────────────────────────────────────────────────
    sweep_wick_pct defines HOW FAR price must pierce a liquidity pool
    before it qualifies as an institutional sweep.

    XAUUSD (Gold) — $3 minimum sweep:
      Mid ~$4708 → $3 / 4708 = 0.000637 (~0.064%)
      Rationale: Gold sweeps are precise. Institutions clear stops by
      just $1-$3 before reversing. A wider threshold misses entries.

    XAGUSD (Silver) — $0.15 minimum sweep:
      Mid ~$75.68 → $0.15 / 75.68 = 0.002000 (~0.20%)
      Rationale: Silver is volatile with wide spreads. At $75, $0.15
      is ~2 ticks and sufficient to confirm an engineered stop run.

    BTCUSD (Bitcoin) — $80 minimum sweep:
      Mid ~$78256 → $80 / 78256 = 0.001022 (~0.10%)
      Rationale: BTC has high nominal moves. A $80 threshold filters
      noise while capturing real institutional session-high sweeps.

    USOIL (Crude Oil) — $0.25 minimum sweep:
      Mid ~$93.39 → $0.25 / 93.39 = 0.002677 (~0.27%)
      Rationale: Oil respects key levels tightly. $0.25 above/below
      a swing is sufficient to confirm a trap before the reversal.

    Global Default (all other symbols): 0.001 (0.10%)
    ─────────────────────────────────────────────────────────────────
    To override at runtime, pass symbol_sweep_pct dict:
      ManipulationEngine(symbol_sweep_pct={'XAUUSD': 0.000637, ...})
    """

    # Built-in per-symbol calibrated defaults (based on live price data)
    SYMBOL_SWEEP_PCT: dict = {
        "XAUUSD": 0.000637,   # $3 sweep on ~$4708 gold  (0.064%)
        "XAGUSD": 0.002000,   # $0.15 sweep on ~$75 silver (0.20%)
        "BTCUSD": 0.001022,   # $80 sweep on ~$78256 BTC  (0.10%)
        "USOIL":  0.002677,   # $0.25 sweep on ~$93 oil   (0.27%)
    }

    def __init__(self, sweep_wick_pct: float = 0.001, reversal_bars: int = 3,
                 judas_window_bars: int = 10,
                 symbol_sweep_pct: dict = None):
        self.sweep_wick_pct    = sweep_wick_pct    # Global fallback (0.001 = 0.10%)
        self.reversal_bars     = reversal_bars      # Bars to confirm reversal after sweep
        self.judas_window_bars = judas_window_bars  # Bars from session open to detect Judas
        # Merge built-in defaults with any caller-supplied overrides
        self._sym_pct = {**self.SYMBOL_SWEEP_PCT, **(symbol_sweep_pct or {})}

    def _get_sweep_pct(self, symbol: str) -> float:
        """Return the calibrated sweep_wick_pct for this symbol, or global default."""
        return self._sym_pct.get(symbol, self.sweep_wick_pct)

    def analyze(self, df: pd.DataFrame, lmap: LiquidityMap,
                session_open_idx: Optional[int] = None) -> ManipulationSignal:
        """Full manipulation analysis on OHLCV + liquidity map."""
        sig = ManipulationSignal(timestamp=datetime.now(timezone.utc))

        if df is None or len(df) < self.reversal_bars + 2:
            return sig

        # ── 1. Liquidity Sweep Detection ───────────────────────────────────
        sweep = self._detect_sweep(df, lmap)
        if sweep.detected:
            return sweep

        # ── 2. Judas Swing (session false move) ───────────────────────────
        if session_open_idx is not None:
            judas = self._detect_judas_swing(df, lmap, session_open_idx)
            if judas.detected:
                return judas

        # ── 3. False Breakout Detection ────────────────────────────────────
        fb = self._detect_false_breakout(df, lmap)
        if fb.detected:
            return fb

        return sig

    # ── Private methods ────────────────────────────────────────────────────

    def _detect_sweep(self, df: pd.DataFrame, lmap: LiquidityMap) -> ManipulationSignal:
        """
        Liquidity sweep: price wicks through a pool level then closes back on the other side.
        Uses per-symbol calibrated sweep_wick_pct for precise detection.
        """
        sig = ManipulationSignal()
        last = df.iloc[-1]
        prev_bars = df.iloc[-self.reversal_bars - 1: -1]
        symbol = lmap.symbol
        wick_pct = self._get_sweep_pct(symbol)  # Symbol-specific threshold

        # ── BSL Sweep (wick above BSL, close below) → BEARISH manipulation
        for pool in lmap.bsl_pools[:3]:  # Check top 3 BSL pools
            level = pool.level
            # Price pierced above by at least wick_pct
            if prev_bars['high'].max() > level * (1 + wick_pct):
                # But closed back below (reversal confirmed)
                if last['close'] < level and last['close'] < last['open']:
                    pool.swept = True
                    confidence = min(1.0, 0.6 + pool.strength * 0.3 +
                                     (prev_bars['high'].max() - level) / level * 20)
                    sig.detected = True
                    sig.signal_type = "SWEEP_BSL"
                    sig.direction = "BEARISH"
                    sig.swept_pool = pool
                    sig.confidence = round(confidence, 2)
                    sig.description = (f"BSL swept @ {level:.4f} [{symbol} thresh={wick_pct:.6f}] — "
                                       f"wick above, close back below. Bearish reversal expected.")
                    return sig

        # ── SSL Sweep (wick below SSL, close above) → BULLISH manipulation
        for pool in lmap.ssl_pools[:3]:
            level = pool.level
            if prev_bars['low'].min() < level * (1 - wick_pct):
                if last['close'] > level and last['close'] > last['open']:
                    pool.swept = True
                    confidence = min(1.0, 0.6 + pool.strength * 0.3 +
                                     (level - prev_bars['low'].min()) / level * 20)
                    sig.detected = True
                    sig.signal_type = "SWEEP_SSL"
                    sig.direction = "BULLISH"
                    sig.swept_pool = pool
                    sig.confidence = round(confidence, 2)
                    sig.description = (f"SSL swept @ {level:.4f} [{symbol} thresh={wick_pct:.6f}] — "
                                       f"wick below, close back above. Bullish reversal expected.")
                    return sig

        return sig

    def _detect_judas_swing(self, df: pd.DataFrame, lmap: LiquidityMap,
                             session_open_idx: int) -> ManipulationSignal:
        """
        Judas Swing: false initial move at session open before the real institutional move.
        e.g., London opens bearish, sweeps SSL, then reverses bullish.
        """
        sig = ManipulationSignal()
        end_idx = min(session_open_idx + self.judas_window_bars, len(df) - 1)
        if end_idx <= session_open_idx:
            return sig

        session_window = df.iloc[session_open_idx:end_idx]
        if len(session_window) < 3:
            return sig

        open_price = float(df.iloc[session_open_idx]['open'])
        session_low  = float(session_window['low'].min())
        session_high = float(session_window['high'].max())
        current_close = float(df.iloc[-1]['close'])

        # Pattern: Initial drop (SSL sweep) then recovery above open
        if session_low < open_price * 0.998 and current_close > open_price:
            sig.detected = True
            sig.signal_type = "JUDAS_SWING"
            sig.direction = "BULLISH"
            sig.confidence = 0.72
            sig.description = (f"Judas Swing: session dropped to {session_low:.4f} "
                                f"(below open {open_price:.4f}), now recovered — "
                                f"bulls trapped the bears.")
        # Pattern: Initial rally (BSL sweep) then rejection below open
        elif session_high > open_price * 1.002 and current_close < open_price:
            sig.detected = True
            sig.signal_type = "JUDAS_SWING"
            sig.direction = "BEARISH"
            sig.confidence = 0.72
            sig.description = (f"Judas Swing: session rallied to {session_high:.4f} "
                                f"(above open {open_price:.4f}), now rejected — "
                                f"bears trapped the bulls.")

        return sig

    def _detect_false_breakout(self, df: pd.DataFrame, lmap: LiquidityMap) -> ManipulationSignal:
        """
        False breakout: price breaks a key level but fails to sustain — closes back inside.
        """
        sig = ManipulationSignal()
        if len(df) < 5:
            return sig

        recent = df.iloc[-5:]
        breakout_bar = recent.iloc[-3]
        confirm_bar  = recent.iloc[-1]

        # Check BSL false breakout
        for pool in lmap.bsl_pools[:2]:
            level = pool.level
            if (breakout_bar['high'] > level and
                    breakout_bar['close'] < level and
                    confirm_bar['close'] < level):
                sig.detected = True
                sig.signal_type = "FALSE_BREAKOUT"
                sig.direction = "BEARISH"
                sig.swept_pool = pool
                sig.confidence = 0.65
                sig.description = f"False breakout above BSL {level:.4f} — confirmed rejection."
                return sig

        # Check SSL false breakout
        for pool in lmap.ssl_pools[:2]:
            level = pool.level
            if (breakout_bar['low'] < level and
                    breakout_bar['close'] > level and
                    confirm_bar['close'] > level):
                sig.detected = True
                sig.signal_type = "FALSE_BREAKOUT"
                sig.direction = "BULLISH"
                sig.swept_pool = pool
                sig.confidence = 0.65
                sig.description = f"False breakout below SSL {level:.4f} — confirmed rejection."
                return sig

        return sig
