"""
Volume profile — POC, Value Area High, Value Area Low.

What this computes
------------------
A volume histogram over price (not over time), then three levels read off it:

    POC   Point of Control — the price bin holding the most volume. The price
          the market spent the most effort agreeing on.
    VAH   Value Area High — upper edge of the contiguous band around the POC
          holding `va_pct` (70% by convention) of the window's volume.
    VAL   Value Area Low  — lower edge of that same band.

Together they describe where business was done. Price inside the value area is
"accepted"; price outside it is being auctioned somewhere the market has not
yet agreed on, and in a balanced market tends to be rejected back inside.

Provenance, and one deliberate divergence
-----------------------------------------
The POC scan and the value-area expansion are transcribed from MQL5 art. 23169
(*Automatic Session Volume Profile Builder*), read in full. Its loop expands
outward from the POC bin, at each step comparing the bin immediately above with
the bin immediately below and absorbing whichever holds more volume, until the
running total reaches the threshold. Its tie-break — `vol_above >= vol_below`
— resolves to the upper bin, and that asymmetry is reproduced here rather than
"corrected", because a value area is only comparable against other value areas
computed the same way. Exhausted sides are marked with a sentinel so expansion
continues into whichever side still has bins.

The divergence: that article walks raw ticks, attributing each tick's volume to
the single bin containing its price. This module is handed OHLCV bars, because
that is what the rest of this repository fetches and what the simulator can
replay deterministically. A bar's volume is therefore spread across every bin
its [low, high] range touches, weighted by how much of the bin that range
covers. This is the standard bar-based approximation and it is an
approximation: a bar that opened and closed at its low, having spiked once to
its high, contributes as though trade were uniform across the whole span. On
M15 and finer the error is small relative to a sane bin width; on daily bars it
would not be. **The result is not identical to a tick-built profile.** It is,
critically, identical between the live agent and the simulator, which is the
property invariant #2 of this repository actually requires.

Art. 23169 publishes no win rate, profit factor, sample size or date range, and
states plainly that volume profiles "do not predict where price will move in
the following session." Under §13.7 it is **design** evidence — the shape of
the calculation — and never performance evidence. Nothing here is claimed to be
profitable; that question is settled by walk-forward, not by citation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import pandas as pd

#: Fraction of total volume enclosed by the value area. 70% is the convention
#: the source article exposes as a parameter and defaults to.
DEFAULT_VALUE_AREA_PCT = 0.70

#: Refuse to build a histogram finer than this many bins. Art. 23169 guards at
#: 10000; the same ceiling is used here so a mis-specified bin size fails loudly
#: instead of allocating an enormous array.
MAX_BINS = 10000

#: Below this many bins the profile is too coarse to separate the POC from the
#: value-area edges, and every level collapses onto the same price.
MIN_BINS = 8


@dataclass(frozen=True)
class VolumeProfile:
    """An immutable read of one volume profile. All prices in symbol units."""
    poc: float
    vah: float
    val: float
    profile_high: float
    profile_low: float
    bin_size: float
    bin_count: int
    total_volume: float
    #: Volume enclosed by the value area, i.e. what the expansion accumulated.
    value_area_volume: float
    bars_used: int

    @property
    def valid(self) -> bool:
        return self.bin_count > 0 and self.total_volume > 0.0 and self.vah > self.val

    @property
    def value_area_width(self) -> float:
        return self.vah - self.val

    @property
    def value_area_pct_actual(self) -> float:
        if self.total_volume <= 0:
            return 0.0
        return self.value_area_volume / self.total_volume

    def summary(self) -> str:
        return (f"VAH={self.vah:.2f} POC={self.poc:.2f} VAL={self.val:.2f} "
                f"(width={self.value_area_width:.2f}, {self.bin_count} bins, "
                f"{self.bars_used} bars, {self.value_area_pct_actual * 100:.1f}% vol)")


# -- Location classification -------------------------------------------------

#: Where a price sits relative to a profile. These are the labels the trading
#: rule branches on, so they are named for the auction state, not for geometry.
LOC_ABOVE_VAH = "ABOVE_VAH"      # premium — outside value, above it
LOC_AT_VAH = "AT_VAH"            # at the upper edge — the short zone
LOC_UPPER_VALUE = "UPPER_VALUE"  # inside value, above the POC band
LOC_AT_POC = "AT_POC"            # fair value — the no-trade zone
LOC_LOWER_VALUE = "LOWER_VALUE"  # inside value, below the POC band
LOC_AT_VAL = "AT_VAL"            # at the lower edge — the long zone
LOC_BELOW_VAL = "BELOW_VAL"      # discount — outside value, below it
LOC_UNKNOWN = "UNKNOWN"


def classify_location(price: float,
                      profile: VolumeProfile,
                      edge_tolerance: float,
                      poc_band: float) -> str:
    """
    Label where `price` sits on the profile.

    edge_tolerance  How near VAH/VAL counts as "at" the edge. A fade entry is
                    never a single-tick event, so the edge is a zone.
    poc_band        Half-width of the dead zone around the POC. Inside it no
                    trade is permitted in either direction — see
                    `is_poc_dead_zone`.

    Both are absolute price distances and both are supplied by the caller, so
    the live agent and the simulator cannot derive them differently.

    The POC test runs first. A value area can be narrow enough that the POC
    band overlaps an edge zone, and in that overlap the dead zone must win —
    otherwise the one rule this module exists to enforce is silently skipped
    exactly when the profile is tightest, which is when it matters most.
    """
    if profile is None or not profile.valid or price <= 0:
        return LOC_UNKNOWN

    if abs(price - profile.poc) <= poc_band:
        return LOC_AT_POC
    if price > profile.vah + edge_tolerance:
        return LOC_ABOVE_VAH
    if price >= profile.vah - edge_tolerance:
        return LOC_AT_VAH
    if price < profile.val - edge_tolerance:
        return LOC_BELOW_VAL
    if price <= profile.val + edge_tolerance:
        return LOC_AT_VAL
    return LOC_UPPER_VALUE if price > profile.poc else LOC_LOWER_VALUE


def is_poc_dead_zone(price: float, profile: VolumeProfile,
                     poc_band: float) -> bool:
    """
    True when price is close enough to the POC that no fade may be taken.

    The POC is where the auction is in balance: the most volume traded there
    precisely because neither side could reject it. Entering there buys a
    coin-flip and pays the spread for it, and any stop must sit outside a band
    of heavy two-way trade, so it gets hit by noise rather than by being wrong.

    This is the rule that would have vetoed the 2026-08-25 16:45 UTC long.
    """
    if profile is None or not profile.valid:
        return False
    return abs(price - profile.poc) <= poc_band


# -- Construction ------------------------------------------------------------

def _accumulate(highs: np.ndarray, lows: np.ndarray, vols: np.ndarray,
                profile_low: float, bin_size: float,
                bin_count: int) -> np.ndarray:
    """
    Spread each bar's volume across the bins its range covers.

    A bar spanning [low, high] deposits volume into every bin that range
    overlaps, in proportion to the overlapped width. A bar narrower than one
    bin — or a flat bar where high == low — puts its whole volume into the
    single bin containing it.
    """
    hist = np.zeros(bin_count, dtype=np.float64)
    edges_lo = profile_low + np.arange(bin_count) * bin_size
    edges_hi = edges_lo + bin_size

    for h, l, v in zip(highs, lows, vols):
        if not np.isfinite(h) or not np.isfinite(l):
            continue
        if v <= 0 or not np.isfinite(v):
            # Art. 23169: where the broker reports no volume, weight the
            # observation as 1 rather than discarding it. A CFD feed with a
            # zeroed volume field would otherwise build an empty histogram.
            v = 1.0
        if h <= l:
            idx = int((h - profile_low) / bin_size)
            hist[min(max(idx, 0), bin_count - 1)] += v
            continue
        overlap = np.minimum(edges_hi, h) - np.maximum(edges_lo, l)
        np.clip(overlap, 0.0, None, out=overlap)
        total = overlap.sum()
        if total <= 0:
            idx = int((l - profile_low) / bin_size)
            hist[min(max(idx, 0), bin_count - 1)] += v
        else:
            hist += v * (overlap / total)
    return hist


def _value_area(hist: np.ndarray, poc_bin: int,
                va_threshold: float) -> Tuple[int, int, float]:
    """
    Greedy two-sided expansion from the POC bin. Transcribed from art. 23169.

    Returns (upper_bin, lower_bin, accumulated_volume). The sentinel -1.0 marks
    an exhausted side, so the comparison keeps expanding into the side that
    still has bins; when both are exhausted the loop breaks and the value area
    is the whole histogram.
    """
    n = len(hist)
    va_volume = float(hist[poc_bin])
    upper = lower = poc_bin

    while va_volume < va_threshold:
        vol_above = float(hist[upper + 1]) if upper + 1 < n else -1.0
        vol_below = float(hist[lower - 1]) if lower - 1 >= 0 else -1.0
        if vol_above < 0.0 and vol_below < 0.0:
            break
        # Ties go to the upper bin — the source article's `>=`.
        if vol_above >= vol_below:
            upper += 1
            va_volume += vol_above
        else:
            lower -= 1
            va_volume += vol_below
    return upper, lower, va_volume


def build_volume_profile(df: pd.DataFrame,
                         bin_size: float,
                         va_pct: float = DEFAULT_VALUE_AREA_PCT,
                         volume_column: str = "tick_volume") -> Optional[VolumeProfile]:
    """
    Build a profile from a frame of COMPLETED bars.

    Caller contract, and it matters: `df` must contain only closed bars. A
    forming bar's high and low grow during the bar, so a profile including it
    repaints — the VAH that permitted a short can move before that bar closes,
    and neither the simulator nor the operator could reproduce the decision.
    Every decision frame in this repository excludes the forming bar (§13.4);
    this one is no exception.

    Returns None when the frame cannot support a profile, which the caller must
    treat as "no opinion" rather than as a neutral zero.
    """
    if df is None or len(df) < 2 or bin_size <= 0:
        return None
    if not {"high", "low"}.issubset(df.columns):
        return None
    if not 0.0 < va_pct < 1.0:
        raise ValueError(f"va_pct must be in (0, 1), got {va_pct}")

    highs = df["high"].to_numpy(dtype=np.float64)
    lows = df["low"].to_numpy(dtype=np.float64)
    if volume_column in df.columns:
        vols = df[volume_column].to_numpy(dtype=np.float64)
    else:
        vols = np.ones(len(df), dtype=np.float64)

    profile_high = float(np.nanmax(highs))
    profile_low = float(np.nanmin(lows))
    if not np.isfinite(profile_high) or not np.isfinite(profile_low):
        return None
    if profile_high <= profile_low:
        return None

    bin_count = int(np.ceil((profile_high - profile_low) / bin_size)) + 1
    if bin_count < MIN_BINS or bin_count > MAX_BINS:
        return None

    hist = _accumulate(highs, lows, vols, profile_low, bin_size, bin_count)
    total = float(hist.sum())
    if total <= 0:
        return None

    poc_bin = int(np.argmax(hist))
    upper, lower, va_volume = _value_area(hist, poc_bin, total * va_pct)

    # Art. 23169 reads every level off the LOWER edge of its bin. Doing the
    # same keeps POC/VAH/VAL on one convention; mixing edges would bias the
    # value area upward by one bin width.
    return VolumeProfile(
        poc=profile_low + poc_bin * bin_size,
        vah=profile_low + upper * bin_size,
        val=profile_low + lower * bin_size,
        profile_high=profile_high,
        profile_low=profile_low,
        bin_size=bin_size,
        bin_count=bin_count,
        total_volume=total,
        value_area_volume=float(va_volume),
        bars_used=len(df),
    )


def bin_size_for_range(profile_high: float, profile_low: float,
                       target_bins: int) -> float:
    """
    Bin size that divides an observed range into roughly `target_bins` rows.

    Art. 23169 fixes the bin at 10 points, which is sane for one intraday
    session on one symbol and wrong across instruments whose price scales
    differ by orders of magnitude. Deriving the bin from the range the profile
    actually covers holds resolution constant in *rows* — which is what
    determines whether the value area can be located at all — instead of
    constant in points.
    """
    if target_bins <= 0 or profile_high <= profile_low:
        return 0.0
    return (profile_high - profile_low) / float(target_bins)


def build_profile_auto(df: pd.DataFrame,
                       target_bins: int,
                       va_pct: float = DEFAULT_VALUE_AREA_PCT,
                       volume_column: str = "tick_volume") -> Optional[VolumeProfile]:
    """
    Convenience: size the bin from the frame's own range, then build.

    This is the entry point both the live agent and the simulator call, so the
    bin-size derivation cannot drift between them.
    """
    if df is None or len(df) < 2:
        return None
    if not {"high", "low"}.issubset(df.columns):
        return None
    hi = float(np.nanmax(df["high"].to_numpy(dtype=np.float64)))
    lo = float(np.nanmin(df["low"].to_numpy(dtype=np.float64)))
    bin_size = bin_size_for_range(hi, lo, target_bins)
    if bin_size <= 0:
        return None
    return build_volume_profile(df, bin_size, va_pct=va_pct,
                                volume_column=volume_column)
