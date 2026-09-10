"""
Anchored H4 volume profile — a profile of ONE impulse leg, not a rolling window.

Why a second profile mode exists
--------------------------------
`vp_gate.py` and `va_fade_trigger.py` read a rolling 42-bar H4 profile: the last
seven days of trade, whatever the market was doing. That answers "where has
business been done lately", which is the right question for a location filter.

It is the wrong question for a liquidity reaction. When price declines sharply
and then recovers, the volume that matters is the volume of *that recovery leg* —
the POC of the leg is where the recovery spent its effort, and it is where the
orders that drove it are resting. A rolling window straddling both the decline
and the recovery is bimodal, and its single POC describes neither (the same
failure documented for the 60-bar window in `regime_classifier.py`).

So this module profiles a leg: from a confirmed swing pivot to the extreme
reached since. It reuses `volume_profile.build_profile_auto` unchanged — the
arithmetic, the upward tie-break and the bar-based volume attribution are all
the transcribed art. 23169 behaviour. Only the *window* is different.

Causality — the property this module has to earn
------------------------------------------------
An anchored profile is worthless if the anchor can only be known in hindsight,
and "draw it from the low to the high" is exactly the kind of instruction that
smuggles in future data. Two facts make this causal:

1. **The pivot is right-side confirmed.** `StructureEngine._identify_swings`
   accepts index `i` only when `hi[i] == max(hi[i-n : i+n+1])`, iterating
   `i in range(n, len(df) - n)`. A pivot is therefore never reported until `n`
   bars have CLOSED after it. It cannot see the future; it waits for the past
   to become unambiguous.

2. **The frame excludes the forming bar.** Callers pass `_closed_tf(..., 240)`
   (simulator) or `copy_rates_from_pos(..., H4, 1, n)` (live). The leg's
   extreme is therefore taken over settled bars only.

The cost of (1) is latency: the leg is recognised `n` H4 bars after its pivot.
That is the honest price of not repainting, and it is why the *trigger* built on
top of this reads its liquidity event on M15/M5 — the profile locates the zone,
the lower frames time the entry.

What this module does NOT do
----------------------------
It does not decide anything. It returns a profile and the anchor metadata that
justifies it, or `None`. Direction, liquidity and confirmation all belong to
`vp_liquidity_trigger.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from core.structure_engine import StructureEngine, StructurePoint
from scalper.volume_profile import VolumeProfile, build_profile_auto

#: Leg runs upward from a confirmed swing low to the high made since.
LEG_UP = "UP"
#: Leg runs downward from a confirmed swing high to the low made since.
LEG_DOWN = "DOWN"


def atr(df: pd.DataFrame, period: int) -> float:
    """
    Wilder-style true range mean over the last `period` bars.

    Duplicated in shape from `SATriggerEngine._atr` rather than imported, to keep
    this module free of a circular import back into the engine. The formula is
    the standard one and is asserted equal to the engine's in the test suite.
    """
    if df is None or len(df) < 2:
        return 0.0
    hi = df["high"].to_numpy(dtype=np.float64)
    lo = df["low"].to_numpy(dtype=np.float64)
    cl = df["close"].to_numpy(dtype=np.float64)
    trs = np.maximum.reduce([
        hi[1:] - lo[1:],
        np.abs(hi[1:] - cl[:-1]),
        np.abs(lo[1:] - cl[:-1]),
    ])
    if len(trs) == 0:
        return 0.0
    window = trs[-period:] if len(trs) >= period else trs
    return float(np.mean(window))


@dataclass(frozen=True)
class AnchoredProfile:
    """A leg profile plus the evidence that the leg was chosen causally."""
    profile: VolumeProfile
    direction: str                  # LEG_UP | LEG_DOWN
    anchor_index: int               # index INTO the frame handed in
    anchor_price: float             # the confirmed pivot
    anchor_time: Optional[pd.Timestamp]
    extreme_price: float            # high (LEG_UP) or low (LEG_DOWN) since
    extreme_time: Optional[pd.Timestamp]
    bars_in_leg: int
    leg_range: float
    leg_atr_multiple: float

    @property
    def poc(self) -> float:
        return self.profile.poc

    @property
    def vah(self) -> float:
        return self.profile.vah

    @property
    def val(self) -> float:
        return self.profile.val

    def summary(self) -> str:
        span = ""
        if self.anchor_time is not None and self.extreme_time is not None:
            span = f" {self.anchor_time:%Y-%m-%dT%H:%M}->{self.extreme_time:%Y-%m-%dT%H:%M}"
        return (f"ANCHORED {self.direction}{span} "
                f"({self.bars_in_leg} bars, {self.leg_atr_multiple:.1f}xATR) "
                f"POC={self.poc:.2f} VAH={self.vah:.2f} VAL={self.val:.2f}")


def _candidate_pivots(points: List[StructurePoint]) -> List[StructurePoint]:
    """
    Confirmed pivots, newest first, de-duplicated by index.

    Newest-first because the caller wants the most RECENT leg that qualifies.
    Taking the single newest pivot unconditionally does not work: when price is
    balancing at the top of a move the newest pivot is three bars old, and the
    resulting four-bar "leg" describes the chop rather than the impulse that
    produced it. Walking outward until a leg clears the size floors finds the
    impulse instead.

    This stays causal. Every pivot in the list is already right-side confirmed,
    and the acceptance test applied to each — bar count and ATR multiple — is
    computed from bars that have closed. Nothing here ranks a pivot by what
    happened after the frame ends.

    `StructureEngine` can emit two points at the same index (a high and a low
    label for one bar); keeping the first occurrence makes the walk
    deterministic.
    """
    seen = set()
    ordered: List[StructurePoint] = []
    for point in reversed(points or []):
        if point.index in seen:
            continue
        seen.add(point.index)
        ordered.append(point)
    return ordered


def build_anchored_profile(df_h4_closed: pd.DataFrame,
                           symbol: str,
                           swing_lookback: int,
                           min_leg_bars: int,
                           min_leg_atr: float,
                           atr_period: int,
                           target_bins: int,
                           value_area_pct: float) -> Optional[AnchoredProfile]:
    """
    Profile the current impulse leg, or return None if there isn't a clean one.

    `df_h4_closed` MUST contain only completed H4 bars — the caller owns that,
    exactly as it does for `vp_gate.build_profile`.

    Returns None (never a degraded profile) when:
      * the frame is too short for the swing engine;
      * no pivot has been confirmed yet;
      * the leg is shorter than `min_leg_bars` or smaller than
        `min_leg_atr` x ATR — a two-bar wiggle is not an impulse and its
        "profile" is noise;
      * the underlying profile build fails its own guards.
    """
    if df_h4_closed is None or len(df_h4_closed) < max(min_leg_bars, 20):
        return None
    if not {"high", "low", "close"}.issubset(df_h4_closed.columns):
        return None

    frame = df_h4_closed.reset_index(drop=True)
    frame_atr = atr(frame, atr_period)
    if frame_atr <= 0:
        return None

    state = StructureEngine(swing_lookback=swing_lookback).analyze(
        frame, symbol, "H4")

    for pivot in _candidate_pivots(state.structure_points):
        start = int(pivot.index)
        if start < 0 or start > len(frame) - min_leg_bars:
            continue

        leg = frame.iloc[start:]
        bars_in_leg = len(leg)
        if bars_in_leg < min_leg_bars:
            continue

        # A pivot labelled HH/LH is a swing HIGH, so the leg since it runs DOWN.
        is_high_pivot = pivot.point_type in ("HH", "LH")
        if is_high_pivot:
            direction = LEG_DOWN
            extreme_pos = int(np.argmin(leg["low"].to_numpy(dtype=np.float64)))
            extreme_price = float(leg["low"].iloc[extreme_pos])
        else:
            direction = LEG_UP
            extreme_pos = int(np.argmax(leg["high"].to_numpy(dtype=np.float64)))
            extreme_price = float(leg["high"].iloc[extreme_pos])

        leg_range = abs(extreme_price - float(pivot.level))
        leg_atr_multiple = leg_range / frame_atr
        if leg_atr_multiple < min_leg_atr:
            continue

        profile = build_profile_auto(leg, target_bins=target_bins,
                                     va_pct=value_area_pct)
        if profile is None or not profile.valid:
            continue

        times = leg["time"] if "time" in leg.columns else None
        return AnchoredProfile(
            profile=profile,
            direction=direction,
            anchor_index=start,
            anchor_price=float(pivot.level),
            anchor_time=(pd.Timestamp(times.iloc[0])
                         if times is not None else None),
            extreme_price=extreme_price,
            extreme_time=(pd.Timestamp(times.iloc[extreme_pos])
                          if times is not None else None),
            bars_in_leg=bars_in_leg,
            leg_range=leg_range,
            leg_atr_multiple=leg_atr_multiple,
        )

    return None
