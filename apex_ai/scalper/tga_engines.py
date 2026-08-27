"""
Trade Guardian decision engines — the shared, MT5-free core.
============================================================

Why this module exists
----------------------
L-003 recorded that "the simulator models no trailing; the live Guardian
does", which made every exit-side result in this repository a measurement of
a strategy nobody runs. Closing that gap means the backtester has to make the
Guardian's decisions.

Invariant #2 and CLAUDE.md 13.4 say how: not by re-implementing them. These
three engines are the Guardian's entire decision surface, and they are pure
functions of (position record, candle frame, current price). They live here so
`trade_guardian_agent.py` and `scalper/exit_manager.py` execute the SAME code
rather than two copies that drift — which is exactly how the consultation
timeframe, the trigger window and the forming bar drifted before
`decision_params` existed.

Nothing here imports MetaTrader5 or configures logging, so the module is
importable from a unit test and from the simulator without acquiring the
Guardian's file handlers or its stdout reconfiguration.

`TGADataFeed` and `TradeGuardianAgent` stay in `trade_guardian_agent.py`: they
are MT5 I/O and process orchestration, which the simulator supplies itself.

Thresholds come from `scalper.decision_params`, so the live Guardian and the
simulated one cannot disagree about the stage ladder.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from scalper import decision_params as dp

# Same logger name the Guardian has always used, so live records are unchanged.
logger = logging.getLogger("TGA")


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Wilder-range ATR, mean-smoothed — the Guardian's own definition.

    Kept identical to the original `TGADataFeed.get_atr` rather than replaced
    with the repo's other ATR helpers: the stage ladder's trail distances were
    chosen against THIS number, and swapping the estimator would silently
    re-tune every stage.
    """
    if df is None or len(df) < period:
        return 0.0
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    return true_range.rolling(period).mean().iloc[-1]


def recent_structure(df: pd.DataFrame, window: int = 5) -> Tuple[float, float]:
    """Recent swing high/low, excluding the in-flight candle at iloc[-1]."""
    if df is None or len(df) < window:
        return 0.0, 0.0
    recent = df.iloc[-window - 1:-1]
    return recent['high'].max(), recent['low'].min()


class StructureFeed:
    """Minimal `data_feed` for `SLEngine.evaluate`.

    `SLEngine` asks its feed for exactly one thing — `get_recent_structure` —
    so the simulator does not need MT5 to drive it. Passing this instead of
    widening `evaluate`'s signature keeps the live call site untouched.
    """

    @staticmethod
    def get_recent_structure(df: pd.DataFrame,
                             window: int = 5) -> Tuple[float, float]:
        return recent_structure(df, window)


# ── Configuration ─────────────────────────────────────────────────────────────
@dataclass
class TGAConfig:
    # Stage trigger thresholds (R-multiples of original risk).
    # Raised from {0.5 / 1.0 / 2.0} → {1.0 / 1.5 / 2.5} after today's data
    # showed three trades stopped at breakeven on routine 0.5R retraces.
    # Healthy ICT continuations frequently dip 50-70% of the initial leg —
    # locking BE at +0.5R was killing winners. Stage 1 now also requires
    # structure confirmation (price holding the trade side of entry across
    # the last N closed M5 candles) before SL moves to breakeven.
    breakeven_trigger_r: float = dp.TGA_BREAKEVEN_TRIGGER_R
    stage2_trigger_r: float = dp.TGA_STAGE2_TRIGGER_R
    stage3_trigger_r: float = dp.TGA_STAGE3_TRIGGER_R
    breakeven_min_close_candles: int = dp.TGA_BREAKEVEN_MIN_CLOSE_CANDLES   # Closed M5 candles required for BE
    stage2_trail_atr: float = dp.TGA_STAGE2_TRAIL_ATR
    stage3_trail_atr: float = dp.TGA_STAGE3_TRAIL_ATR
    early_close_min_profit_r: float = dp.TGA_EARLY_CLOSE_MIN_PROFIT_R
    structure_lock_atr: float = dp.TGA_STRUCTURE_LOCK_ATR
    tp_extension_partial_pct: float = dp.TGA_TP_EXTENSION_PARTIAL_PCT
    momentum_candles_check: int = dp.TGA_MOMENTUM_CANDLES_CHECK
    entry_breathing_candles: int = dp.TGA_ENTRY_BREATHING_CANDLES
    # No-progress early close — if a position has been open for this many
    # minutes and peak_profit_r is still below this R-multiple, the trade
    # is dead; close at market rather than letting it ride the full 120-min
    # timeout and risk hitting the original SL near expiry.
    no_progress_minutes:    int   = dp.TGA_NO_PROGRESS_MINUTES
    no_progress_max_peak_r: float = dp.TGA_NO_PROGRESS_MAX_PEAK_R


# ── Registry Record ───────────────────────────────────────────────────────────
@dataclass
class TGAPositionRecord:
    ticket: int
    symbol: str
    origin_agent: str
    direction: str
    entry_price: float
    original_sl: float
    original_tp: float
    volume: float
    entry_atr: float
    entry_time: datetime
    peak_profit_pips: float = 0.0
    peak_profit_r: float = 0.0
    # Maximum Adverse Excursion — most negative R the trade touched.
    # Used to tune entry filters and BE-confirmation depth: if winners
    # routinely dip beyond -X.XR before reversing, our SL placement or
    # entry timing needs work.
    peak_adverse_r: float = 0.0
    sl_stage: int = 0
    breakeven_reached: bool = False
    reached_1r: bool = False
    reached_2r: bool = False
    early_close_armed: bool = False
    tp_extended: bool = False
    # Set when the position is too small to split, so the TP-extension stage
    # stops re-attempting a partial close it can never fill on every 2s poll.
    tp_extend_blocked: bool = False
    last_action_time: Optional[datetime] = None

    @property
    def r_risk(self) -> float:
        """Distance between entry and original SL."""
        return abs(self.entry_price - self.original_sl)

    def current_r(self, current_price: float) -> float:
        """Current profit expressed in original risk units (R)."""
        if self.r_risk == 0:
            return 0.0
        dist = current_price - self.entry_price if self.direction == "BUY" else self.entry_price - current_price
        return dist / self.r_risk

    def print_registry(self) -> str:
        return (
            f"\n[TGA POSITION REGISTRY]\n"
            f"Position ID        : {self.ticket}\n"
            f"Origin Agent       : {self.origin_agent}\n"
            f"Instrument         : {self.symbol}\n"
            f"Direction          : {self.direction}\n"
            f"Entry Price        : {self.entry_price:.5f}\n"
            f"Original SL        : {self.original_sl:.5f}\n"
            f"Original TP        : {self.original_tp:.5f}\n"
            f"Entry ATR          : {self.entry_atr:.5f}\n"
            f"Entry Time         : {self.entry_time.isoformat()}\n"
            f"Peak Profit (pips) : {self.peak_profit_pips:.1f}\n"
            f"Peak Profit (R)    : {self.peak_profit_r:.2f}R\n"
            f"Peak Adverse (R)   : {self.peak_adverse_r:.2f}R\n"
            f"SL Stage           : STAGE {self.sl_stage}\n"
            f"Breakeven Reached  : {'Yes' if self.breakeven_reached else 'No'}\n"
            f"1R Reached         : {'Yes' if self.reached_1r else 'No'}\n"
            f"2R Reached         : {'Yes' if self.reached_2r else 'No'}\n"
            f"Early Close Armed  : {'Yes' if self.early_close_armed else 'No'}\n"
        )


# ── SLEngine ──────────────────────────────────────────────────────────────────
class SLEngine:
    def __init__(self, config: TGAConfig):
        self.cfg = config

    def _structure_confirmed(self, rec: TGAPositionRecord,
                             df_m5: pd.DataFrame) -> bool:
        """
        Stage 1 (BE) prerequisite: price must have HELD the trade side of
        entry across the last N closed M5 candles. Filters wick-only +1R
        touches that immediately retrace through entry.
        """
        n = self.cfg.breakeven_min_close_candles
        if df_m5 is None or len(df_m5) < n + 1:
            return False
        # Last N CLOSED candles (exclude the in-flight candle at iloc[-1])
        closes = df_m5['close'].iloc[-(n + 1):-1].values
        if rec.direction == "BUY":
            return all(c > rec.entry_price for c in closes)
        else:
            return all(c < rec.entry_price for c in closes)

    def evaluate(self, rec: TGAPositionRecord, current_price: float, df_m5: pd.DataFrame, data_feed: TGADataFeed) -> Tuple[int, Optional[float]]:
        """Returns (new_stage, new_sl). If new_sl is None, no change needed."""
        r = rec.current_r(current_price)

        # Determine target stage based on Peak R (config-driven thresholds)
        target_stage = rec.sl_stage
        if rec.peak_profit_r >= self.cfg.stage3_trigger_r:
            target_stage = 3
        elif rec.peak_profit_r >= self.cfg.stage2_trigger_r:
            target_stage = 2
        elif rec.peak_profit_r >= self.cfg.breakeven_trigger_r:
            target_stage = 1

        new_sl = None

        if target_stage == 1:
            # Breakeven lock — but ONLY if structure has confirmed the move.
            # Prevents the +1R-wick-then-retrace breakeven kills seen earlier.
            if rec.sl_stage < 1:
                if not self._structure_confirmed(rec, df_m5):
                    side = "above" if rec.direction == "BUY" else "below"
                    logger.info(
                        f"TGA {rec.ticket} {rec.symbol}: BE deferred — "
                        f"peak={rec.peak_profit_r:.2f}R reached but last "
                        f"{self.cfg.breakeven_min_close_candles} M5 closes "
                        f"have not held {side} entry. Holding original SL."
                    )
                    return rec.sl_stage, None
                new_sl = rec.entry_price
            elif rec.sl_stage == 1:
                pass # Hold BE

        elif target_stage == 2:
            # Hybrid Trail 1R to 2R (1.0 x ATR)
            trail_dist = self.cfg.stage2_trail_atr * rec.entry_atr
            base_sl = current_price - trail_dist if rec.direction == "BUY" else current_price + trail_dist
            
            # Structure lock
            swing_high, swing_low = data_feed.get_recent_structure(df_m5, window=10)
            struct_dist = self.cfg.structure_lock_atr * rec.entry_atr
            
            if rec.direction == "BUY":
                # If swing_low is within struct_dist of base_sl, lock SL slightly below swing_low
                if abs(base_sl - swing_low) <= struct_dist and swing_low < current_price:
                    base_sl = swing_low - (0.1 * rec.entry_atr)
                new_sl = base_sl
            else:
                if abs(base_sl - swing_high) <= struct_dist and swing_high > current_price:
                    base_sl = swing_high + (0.1 * rec.entry_atr)
                new_sl = base_sl

        elif target_stage == 3:
            # Aggressive Trail (0.6 x ATR)
            trail_dist = self.cfg.stage3_trail_atr * rec.entry_atr
            base_sl = current_price - trail_dist if rec.direction == "BUY" else current_price + trail_dist
            
            swing_high, swing_low = data_feed.get_recent_structure(df_m5, window=5)
            # Structure lock activates if swing is within 1.0 ATR of current price
            if rec.direction == "BUY":
                if (current_price - swing_low) <= (1.0 * rec.entry_atr):
                    base_sl = swing_low - (0.1 * rec.entry_atr)
                new_sl = base_sl
            else:
                if (swing_high - current_price) <= (1.0 * rec.entry_atr):
                    base_sl = swing_high + (0.1 * rec.entry_atr)
                new_sl = base_sl

        # Validate new_sl doesn't move backwards or widen original
        if new_sl is not None:
            # Must not be worse than entry if stage >= 1
            if target_stage >= 1:
                if rec.direction == "BUY" and new_sl < rec.entry_price:
                    new_sl = rec.entry_price
                elif rec.direction == "SELL" and new_sl > rec.entry_price:
                    new_sl = rec.entry_price
            
            return target_stage, new_sl

        return target_stage, None


# ── EarlyCloseEngine ──────────────────────────────────────────────────────────
class EarlyCloseEngine:
    def __init__(self, config: TGAConfig):
        self.cfg = config

    def check_triggers(self, rec: TGAPositionRecord, df_m5: pd.DataFrame, current_price: float) -> Tuple[bool, str]:
        """Returns (should_close, reason)."""
        if not rec.early_close_armed:
            return False, ""
        
        # If in loss or breakeven, let SL handle it
        if rec.current_r(current_price) <= 0.0:
            return False, ""
            
        if df_m5 is None or len(df_m5) < 6:
            return False, ""

        # Use completed candles
        c1 = df_m5.iloc[-2] # Last closed candle
        c2 = df_m5.iloc[-3]
        
        body1 = abs(c1['close'] - c1['open'])
        wick_up1 = c1['high'] - max(c1['open'], c1['close'])
        wick_dn1 = min(c1['open'], c1['close']) - c1['low']
        
        # Trigger 1: Reversal Candle Pattern
        if rec.direction == "BUY":
            # Bearish engulfing
            if c1['close'] < c1['open'] and c2['close'] > c2['open']:
                if c1['open'] >= c2['close'] and c1['close'] < c2['open']:
                    return True, "Trigger 1: Bearish Engulfing"
            # Large rejection wick against BUY (large upper wick)
            if wick_up1 > 2 * body1 and body1 > 0:
                return True, "Trigger 1: Large Top Rejection Wick"
        else:
            # Bullish engulfing
            if c1['close'] > c1['open'] and c2['close'] < c2['open']:
                if c1['open'] <= c2['close'] and c1['close'] > c2['open']:
                    return True, "Trigger 1: Bullish Engulfing"
            # Large rejection wick against SELL (large lower wick)
            if wick_dn1 > 2 * body1 and body1 > 0:
                return True, "Trigger 1: Large Bottom Rejection Wick"

        # Trigger 2: Momentum Loss at Key Level
        # (Simplified: just checking momentum contraction)
        c3 = df_m5.iloc[-4]
        range1 = c1['high'] - c1['low']
        range2 = c2['high'] - c2['low']
        if range1 < range2 * 0.8 and range2 < (c3['high'] - c3['low']) * 0.8:
            # Momentum contracting
            recent_high = df_m5['high'].iloc[-6:-1].max()
            recent_low = df_m5['low'].iloc[-6:-1].min()
            if rec.direction == "BUY" and c1['high'] < recent_high:
                return True, "Trigger 2: Momentum Loss & No New High"
            if rec.direction == "SELL" and c1['low'] > recent_low:
                return True, "Trigger 2: Momentum Loss & No New Low"

        # Trigger 3: Market Structure Shift (Simplified BOS)
        swing_high = df_m5['high'].iloc[-6:-2].max()
        swing_low = df_m5['low'].iloc[-6:-2].min()
        if rec.direction == "BUY" and current_price < swing_low:
             return True, "Trigger 3: MS Shift (Swing Low Broken)"
        if rec.direction == "SELL" and current_price > swing_high:
             return True, "Trigger 3: MS Shift (Swing High Broken)"

        return False, ""


# ── TPEngine ──────────────────────────────────────────────────────────────────
class TPEngine:
    def __init__(self, config: TGAConfig):
        self.cfg = config

    def check_momentum(self, rec: TGAPositionRecord, df_m5: pd.DataFrame) -> bool:
        """Returns True if strong momentum, False if weak/stalling."""
        if df_m5 is None or len(df_m5) < 4:
            return False
            
        c1 = df_m5.iloc[-2]
        c2 = df_m5.iloc[-3]
        c3 = df_m5.iloc[-4]
        
        # 3 candles in trade direction
        if rec.direction == "BUY":
            if not (c1['close'] > c1['open'] and c2['close'] > c2['open'] and c3['close'] > c3['open']):
                return False
            wick_up = c1['high'] - c1['close']
            body = c1['close'] - c1['open']
            if wick_up > 0.4 * body:
                return False
        else:
            if not (c1['close'] < c1['open'] and c2['close'] < c2['open'] and c3['close'] < c3['open']):
                return False
            wick_dn = c1['close'] - c1['low']
            body = c1['open'] - c1['close']
            if wick_dn > 0.4 * body:
                return False
                
        # ATR contraction check
        current_range = c1['high'] - c1['low']
        if current_range < 0.8 * rec.entry_atr:
            return False
            
        return True

    def get_next_structure_target(self, rec: TGAPositionRecord, df_m15: pd.DataFrame) -> Optional[float]:
        if df_m15 is None or len(df_m15) < 20:
            return None
        # Very simplified structure target
        recent = df_m15.iloc[-20:-1]
        if rec.direction == "BUY":
            target = recent['high'].max()
            return target if target > rec.original_tp else rec.original_tp + rec.entry_atr * 2
        else:
            target = recent['low'].min()
            return target if target < rec.original_tp else max(0.00001, rec.original_tp - rec.entry_atr * 2)
