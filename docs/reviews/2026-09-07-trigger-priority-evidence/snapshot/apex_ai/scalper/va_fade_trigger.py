"""
VALUE_AREA_FADE — an entry model that trades the value-area edges.

This is a **signal generator**, not a filter. `vp_gate.py` is the veto layer
that removes other triggers' trades; this module creates its own:

    price at VAH + bearish confluence  ->  SELL, stop above the range high
    price at VAL + bullish confluence  ->  BUY,  stop below the range low
    price at POC                       ->  nothing, in either direction

The distinction matters and was learned the hard way. The gate version of this
rule was measured first and rejected (docs/RESEARCH_NOTES.md §12): as a veto it
strips winners out of a sweep-fade book whose edge is anchored to liquidity
pools rather than to the volume distribution. That result says nothing about
whether the value area can *originate* a trade, because a filter and a signal
are different claims. This module tests the claim that was actually made.

The marked levels
-----------------
`RangeLevels` carries the three prices an operator would draw by hand:

    range_high   the swing high bounding the balance area  (upper marker)
    range_low    the swing low bounding it                 (lower marker)
    major_liquidity  the nearest untapped external high/low beyond the range —
                 the level a stop-run would reach for

These are derived from swing structure, never hard-coded. A price written into
the source is a fact about one afternoon, not a strategy: the 4700 level that
prompted this was simply the prevailing range high, and the detector finds it
the same way an operator's eye does.

Confluence
----------
Two conditions, both required, on the trigger frame:

1. **Rejection at the level.** The bar must trade into the zone and close back
   out of it, with the wick on the correct side at least `MIN_WICK_FRAC` of the
   bar's range. An edge touched and accepted is not a fade — it is a breakout
   in progress.
2. **Momentum extreme.** RSI(14) beyond 70 for a short or below 30 for a long.
   Taken from MQL5 art. 17781, which pairs exactly this — RSI(14), 30/70 — with
   a *ranging* regime classification and a breakout model elsewhere. That
   article reports "approximately 20% equity growth" pre-optimisation with
   "significant drawdowns", publishes nothing numeric after optimising, and
   warns its own tuning "introduces a risk of overfitting". Design evidence
   under §13.7; the thresholds are starting points, not settled values.

Targets obey §13.1: TP1 is 2R and TP2 is 3R, from the shared `_targets` helper.
The intuitive fade target is the POC, which on a tight value area can be well
under 1R — taking it would recreate exactly the sub-2R geometry that made this
system arithmetically incapable of profit at its measured win rate. The POC is
therefore recorded on the trigger for telemetry and is not used as a target.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from scalper.volume_profile import (
    LOC_AT_POC, LOC_AT_VAH, LOC_AT_VAL, VolumeProfile, classify_location,
)

#: Minimum share of the bar's range that must sit in the rejecting wick.
MIN_WICK_FRAC = 0.33
#: RSI period and the two extremes. Art. 17781's values.
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
#: Swing lookback for the range markers, in trigger-frame bars either side.
SWING_LOOKBACK = 5
#: Stop buffer beyond the range marker, as a fraction of the value-area width.
SL_BUFFER_FRAC = 0.05


@dataclass(frozen=True)
class RangeLevels:
    """The levels an operator would draw on the chart."""
    range_high: float = 0.0
    range_low: float = 0.0
    #: Nearest untapped swing beyond the range, on either side. 0.0 if none.
    major_liquidity_above: float = 0.0
    major_liquidity_below: float = 0.0

    @property
    def valid(self) -> bool:
        return self.range_high > self.range_low > 0.0

    def summary(self) -> str:
        parts = [f"range [{self.range_low:.2f}, {self.range_high:.2f}]"]
        if self.major_liquidity_above:
            parts.append(f"liq above {self.major_liquidity_above:.2f}")
        if self.major_liquidity_below:
            parts.append(f"liq below {self.major_liquidity_below:.2f}")
        return " | ".join(parts)


def _swings(df: pd.DataFrame, lookback: int) -> Tuple[list, list]:
    """Fractal swing highs and lows: an extreme with `lookback` bars either side."""
    highs = df["high"].to_numpy(dtype=np.float64)
    lows = df["low"].to_numpy(dtype=np.float64)
    sh, sl = [], []
    for i in range(lookback, len(highs) - lookback):
        window_h = highs[i - lookback:i + lookback + 1]
        window_l = lows[i - lookback:i + lookback + 1]
        if highs[i] == window_h.max() and (window_h == highs[i]).sum() == 1:
            sh.append(float(highs[i]))
        if lows[i] == window_l.min() and (window_l == lows[i]).sum() == 1:
            sl.append(float(lows[i]))
    return sh, sl


def find_range_levels(df_trigger: pd.DataFrame,
                      profile: VolumeProfile,
                      lookback: int = SWING_LOOKBACK) -> RangeLevels:
    """
    The two markers bounding the balance area, plus external liquidity beyond.

    The range is bounded by the profile's own extremes — that is the span the
    histogram was built over, so it is by construction the balance area being
    traded. External liquidity is the nearest swing lying *outside* that span,
    which is where a stop run would be aimed.
    """
    if profile is None or not profile.valid or df_trigger is None:
        return RangeLevels()
    if len(df_trigger) < 2 * lookback + 1:
        return RangeLevels(range_high=profile.profile_high,
                           range_low=profile.profile_low)

    sh, sl = _swings(df_trigger, lookback)
    above = [h for h in sh if h > profile.profile_high]
    below = [l for l in sl if l < profile.profile_low]
    return RangeLevels(
        range_high=profile.profile_high,
        range_low=profile.profile_low,
        major_liquidity_above=min(above) if above else 0.0,
        major_liquidity_below=max(below) if below else 0.0,
    )


def rsi_wilder(closes: np.ndarray, period: int = RSI_PERIOD) -> Optional[float]:
    """
    Final RSI value, using Wilder's smoothing — what MetaTrader's iRSI computes.

    A simple rolling mean would print numbers that disagree with the operator's
    own chart, which is the same reason `ema_filter.ema_mt5` exists.
    """
    n = len(closes)
    if n < period + 1:
        return None
    delta = np.diff(closes)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    avg_gain = float(gains[:period].mean())
    avg_loss = float(losses[:period].mean())
    for i in range(period, len(delta)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss <= 0.0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


@dataclass(frozen=True)
class FadeSignal:
    """What the detector found, before the engine turns it into an SATrigger."""
    detected: bool = False
    direction: str = "NONE"
    entry: float = 0.0
    stop_loss: float = 0.0
    location: str = ""
    rsi: float = 0.0
    wick_frac: float = 0.0
    poc: float = 0.0
    levels: RangeLevels = RangeLevels()
    reason: str = ""


def detect(df_trigger: pd.DataFrame,
           profile: VolumeProfile,
           edge_tolerance_frac: float,
           poc_band_frac: float,
           rsi_period: int = RSI_PERIOD,
           rsi_overbought: float = RSI_OVERBOUGHT,
           rsi_oversold: float = RSI_OVERSOLD,
           min_wick_frac: float = MIN_WICK_FRAC,
           sl_buffer_frac: float = SL_BUFFER_FRAC) -> FadeSignal:
    """
    Look for a rejection at VAH or VAL on the last COMPLETED trigger bar.

    Caller contract: `df_trigger` ends on a closed bar and `profile` was built
    from closed bars. A rejection read off a forming bar can un-form before the
    bar closes.
    """
    if df_trigger is None or len(df_trigger) < rsi_period + 2:
        return FadeSignal(reason="Insufficient trigger history")
    if profile is None or not profile.valid:
        return FadeSignal(reason="No usable volume profile")

    bar = df_trigger.iloc[-1]
    high, low = float(bar["high"]), float(bar["low"])
    close, open_ = float(bar["close"]), float(bar["open"])
    bar_range = high - low
    if bar_range <= 0:
        return FadeSignal(reason="Degenerate bar (high == low)")

    width = profile.value_area_width
    edge_tol = edge_tolerance_frac * width
    poc_band = poc_band_frac * width
    levels = find_range_levels(df_trigger, profile)

    # The POC dead zone is checked on the CLOSE, before either edge, so a bar
    # that straddles both the POC and an edge is refused rather than traded.
    close_loc = classify_location(close, profile, edge_tol, poc_band)
    if close_loc == LOC_AT_POC:
        return FadeSignal(
            location=LOC_AT_POC, poc=profile.poc, levels=levels,
            reason=(f"Close {close:.2f} is inside the POC dead zone "
                    f"(POC {profile.poc:.2f} +/-{poc_band:.2f}) — no action"))

    closes = df_trigger["close"].to_numpy(dtype=np.float64)
    rsi = rsi_wilder(closes, rsi_period)
    if rsi is None:
        return FadeSignal(reason="RSI could not be seeded")

    upper_wick = (high - max(open_, close)) / bar_range
    lower_wick = (min(open_, close) - low) / bar_range
    buffer = sl_buffer_frac * width

    # SHORT: the bar reached up into the VAH zone and closed back below it.
    touched_vah = high >= profile.vah - edge_tol
    if touched_vah and close < profile.vah:
        if upper_wick < min_wick_frac:
            return FadeSignal(
                location=LOC_AT_VAH, rsi=rsi, wick_frac=upper_wick,
                poc=profile.poc, levels=levels,
                reason=(f"VAH touched but rejection too weak "
                        f"(upper wick {upper_wick:.0%} < {min_wick_frac:.0%})"))
        if rsi < rsi_overbought:
            return FadeSignal(
                location=LOC_AT_VAH, rsi=rsi, wick_frac=upper_wick,
                poc=profile.poc, levels=levels,
                reason=(f"VAH rejection without momentum confluence "
                        f"(RSI {rsi:.1f} < {rsi_overbought:.0f})"))
        anchor = max(levels.range_high, high) if levels.valid else high
        return FadeSignal(
            detected=True, direction="BEARISH", entry=close,
            stop_loss=anchor + buffer, location=LOC_AT_VAH, rsi=rsi,
            wick_frac=upper_wick, poc=profile.poc, levels=levels,
            reason=(f"VAH fade — rejected {profile.vah:.2f} with "
                    f"{upper_wick:.0%} upper wick, RSI {rsi:.1f}, "
                    f"stop above {anchor:.2f}"))

    # LONG: the bar reached down into the VAL zone and closed back above it.
    touched_val = low <= profile.val + edge_tol
    if touched_val and close > profile.val:
        if lower_wick < min_wick_frac:
            return FadeSignal(
                location=LOC_AT_VAL, rsi=rsi, wick_frac=lower_wick,
                poc=profile.poc, levels=levels,
                reason=(f"VAL touched but rejection too weak "
                        f"(lower wick {lower_wick:.0%} < {min_wick_frac:.0%})"))
        if rsi > rsi_oversold:
            return FadeSignal(
                location=LOC_AT_VAL, rsi=rsi, wick_frac=lower_wick,
                poc=profile.poc, levels=levels,
                reason=(f"VAL rejection without momentum confluence "
                        f"(RSI {rsi:.1f} > {rsi_oversold:.0f})"))
        anchor = min(levels.range_low, low) if levels.valid else low
        return FadeSignal(
            detected=True, direction="BULLISH", entry=close,
            stop_loss=anchor - buffer, location=LOC_AT_VAL, rsi=rsi,
            wick_frac=lower_wick, poc=profile.poc, levels=levels,
            reason=(f"VAL fade — rejected {profile.val:.2f} with "
                    f"{lower_wick:.0%} lower wick, RSI {rsi:.1f}, "
                    f"stop below {anchor:.2f}"))

    return FadeSignal(location=close_loc, rsi=rsi, poc=profile.poc,
                      levels=levels,
                      reason=f"No edge interaction (close at {close_loc})")
