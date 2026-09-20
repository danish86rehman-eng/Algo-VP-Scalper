"""
Causal market-location engine for the SA scalper.

This module is deliberately a location/evidence layer.  It does not create an
order and it does not decide whether a trigger is admissible.  It answers one
question on completed data only: where is price relative to structural zones
and volume-profile levels?

The engine reuses ``scalper.volume_profile`` for the histogram arithmetic and
``core.structure_engine`` for confirmed swing points.  The live agent and the
replay pass the same closed-bar frames to this module, which keeps the result
causal and parity-safe.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

from scalper.anchored_vp import atr as _atr
from scalper.volume_profile import (VolumeProfile, _accumulate,
                                    build_profile_auto)


# ── Public labels -------------------------------------------------------------

SUPPORT = "SUPPORT"
RESISTANCE = "RESISTANCE"
FLIP = "FLIP"

PROFILE_ACTIVE = "ACTIVE"
PROFILE_REFERENCE = "REFERENCE"
PROFILE_RETIRED = "RETIRED"

AT_MAJOR_SUPPORT = "AT_MAJOR_SUPPORT"
AT_MAJOR_RESISTANCE = "AT_MAJOR_RESISTANCE"
AT_VAH = "AT_VAH"
AT_VAL = "AT_VAL"
AT_POC = "AT_POC"
ABOVE_VALUE = "ABOVE_VALUE"
BELOW_VALUE = "BELOW_VALUE"
INSIDE_VALUE = "INSIDE_VALUE"
VP_SR_SUPPORT_CONFLUENCE = "VP_SR_SUPPORT_CONFLUENCE"
VP_SR_RESISTANCE_CONFLUENCE = "VP_SR_RESISTANCE_CONFLUENCE"
MID_RANGE_NO_LOCATION = "MID_RANGE_NO_LOCATION"
UNKNOWN_LOCATION = "UNKNOWN_LOCATION"

VAH_REJECTION = "VAH_REJECTION"
VAH_ACCEPTANCE = "VAH_ACCEPTANCE"
VAL_REJECTION = "VAL_REJECTION"
VAL_ACCEPTANCE = "VAL_ACCEPTANCE"
POC_RECLAIM = "POC_RECLAIM"
POC_LOSS = "POC_LOSS"
NO_VALUE_EVENT = "NO_VALUE_EVENT"


@dataclass(frozen=True)
class MarketLocationConfig:
    """Geometry and selection policy; all distances are volatility-normalised."""

    sr_swing_lookback_d1: int = 2
    sr_swing_lookback_w1: int = 2
    sr_swing_lookback_h4: int = 3
    sr_swing_lookback_m15: int = 3
    atr_period: int = 14
    cluster_atr: float = 0.35
    zone_atr: float = 0.25
    profile_level_atr: float = 0.30
    value_edge_frac: float = 0.10
    acceptance_bars: int = 2
    acceptance_buffer_atr: float = 0.10
    rejection_wick_frac: float = 0.35
    consolidation_bars_h4: int = 6
    min_impulse_atr: float = 1.50
    min_anchor_leg_atr: float = 2.00
    replacement_score_margin: float = 0.20
    h4_recent_score_floor: float = 0.70
    w1_recent_score_floor: float = 0.995
    min_profile_bars: int = 5
    # Stable row policy.  The range determines the bin size; HTF ATR never
    # silently changes the histogram resolution.
    profile_target_bins: int = 48
    max_profiles_per_timeframe: int = 3
    max_total_profiles: int = 8
    max_zones: int = 16
    d1_bars: int = 260
    w1_bars: int = 260
    h4_bars: int = 260
    m15_bars: int = 180


@dataclass(frozen=True)
class SRZone:
    """A clustered, causal structural location."""

    zone_id: str
    price: float
    zone_low: float
    zone_high: float
    type: str
    timeframe: str
    created_at: Optional[str]
    last_touch: Optional[str]
    touch_count: int
    rejection_count: int
    strength_score: float
    source: str
    active: bool
    distance_from_price: float
    flip_to: Optional[str] = None
    original_type: Optional[str] = None
    acceptance_confirmed_at: Optional[str] = None

    @property
    def contains_price(self) -> bool:
        return self.distance_from_price == 0.0

    @property
    def role(self) -> str:
        if self.type == FLIP:
            return self.flip_to or FLIP
        return self.type

    def record(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ProfileRecord:
    """A VP distribution plus its causal anchor and lifecycle status."""

    profile_id: str
    status: str
    timeframe: str
    profile: VolumeProfile
    profile_start: Optional[str]
    profile_end: Optional[str]
    profile_high: float
    profile_low: float
    direction: str
    total_volume: float
    data_quality: str
    source: str
    symbol: str = ""
    high_volume_nodes: tuple[float, ...] = ()
    low_volume_nodes: tuple[float, ...] = ()
    # Explicit anchor/lifecycle fields keep the selected auction leg
    # inspectable.  They are deliberately separate from the histogram's
    # observed high/low: a profile may contain a wick outside its pivots.
    anchor_low: Optional[float] = None
    anchor_high: Optional[float] = None
    anchor_start_price: Optional[float] = None
    anchor_end_price: Optional[float] = None
    anchor_start_time: Optional[str] = None
    anchor_end_time: Optional[str] = None
    confirmation_time: Optional[str] = None
    replacement_reason: Optional[str] = None
    selection_score: float = 0.0
    leg_size_atr: float = 0.0
    anchor_bars: int = 0
    row_count: int = 48
    volume_source: str = "tick_volume"
    source_timeframe: str = ""
    actual_value_area_percentage: float = 0.0

    @property
    def poc(self) -> float:
        return float(self.profile.poc)

    @property
    def vah(self) -> float:
        return float(self.profile.vah)

    @property
    def val(self) -> float:
        return float(self.profile.val)

    def record(self) -> dict:
        return {
            "symbol": self.symbol,
            "profile_id": self.profile_id,
            "status": self.status,
            "timeframe": self.timeframe,
            "poc": self.poc,
            "vah": self.vah,
            "val": self.val,
            "profile_start": self.profile_start,
            "profile_end": self.profile_end,
            "profile_high": self.profile_high,
            "profile_low": self.profile_low,
            "direction": self.direction,
            "total_volume": self.total_volume,
            "data_quality": self.data_quality,
            "source": self.source,
            "high_volume_nodes": list(self.high_volume_nodes),
            "low_volume_nodes": list(self.low_volume_nodes),
            "anchor_low": self.anchor_low,
            "anchor_high": self.anchor_high,
            "anchor_start_price": self.anchor_start_price,
            "anchor_end_price": self.anchor_end_price,
            "anchor_start_time": self.anchor_start_time,
            "anchor_end_time": self.anchor_end_time,
            "confirmation_time": self.confirmation_time,
            "replacement_reason": self.replacement_reason,
            "selection_score": self.selection_score,
            "leg_size_atr": self.leg_size_atr,
            "anchor_bars": self.anchor_bars,
            "row_count": self.row_count,
            "volume_source": self.volume_source,
            "source_timeframe": self.source_timeframe,
            "actual_value_area_percentage": self.actual_value_area_percentage,
            "bars_used": int(self.profile.bars_used),
            "bin_count": int(self.profile.bin_count),
            "bin_size": float(self.profile.bin_size),
            "value_area_volume": float(self.profile.value_area_volume),
        }


@dataclass(frozen=True)
class MarketLocationSnapshot:
    """One complete, JSON-safe market-location read."""

    symbol: str
    as_of: Optional[str]
    current_price: float
    atr: float
    location_type: str = UNKNOWN_LOCATION
    vp_state: str = UNKNOWN_LOCATION
    sr_state: str = UNKNOWN_LOCATION
    nearest_support: Optional[float] = None
    nearest_resistance: Optional[float] = None
    nearest_poc: Optional[float] = None
    nearest_vah: Optional[float] = None
    nearest_val: Optional[float] = None
    distance_to_support_atr: Optional[float] = None
    distance_to_resistance_atr: Optional[float] = None
    distance_to_poc_atr: Optional[float] = None
    distance_to_vah_atr: Optional[float] = None
    distance_to_val_atr: Optional[float] = None
    vp_sr_confluence_score: float = 0.0
    value_area_event: str = NO_VALUE_EVENT
    active_profile_id: Optional[str] = None
    active_profile_timeframe: Optional[str] = None
    active_profile_source: Optional[str] = None
    profile_anchor_start: Optional[str] = None
    profile_anchor_end: Optional[str] = None
    profiles: tuple[ProfileRecord, ...] = ()
    zones: tuple[SRZone, ...] = ()
    retired_profile_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    # The two authoritative top-down auctions remain addressable separately.
    w1_profile_id: Optional[str] = None
    h4_profile_id: Optional[str] = None
    w1_anchor_start: Optional[str] = None
    w1_anchor_end: Optional[str] = None
    w1_anchor_start_price: Optional[float] = None
    w1_anchor_end_price: Optional[float] = None
    w1_profile_high: Optional[float] = None
    w1_profile_low: Optional[float] = None
    w1_poc: Optional[float] = None
    w1_vah: Optional[float] = None
    w1_val: Optional[float] = None
    h4_anchor_start: Optional[str] = None
    h4_anchor_end: Optional[str] = None
    h4_anchor_start_price: Optional[float] = None
    h4_anchor_end_price: Optional[float] = None
    h4_profile_high: Optional[float] = None
    h4_profile_low: Optional[float] = None
    h4_poc: Optional[float] = None
    h4_vah: Optional[float] = None
    h4_val: Optional[float] = None
    distance_to_w1_poc_atr: Optional[float] = None
    distance_to_w1_vah_atr: Optional[float] = None
    distance_to_w1_val_atr: Optional[float] = None
    distance_to_h4_poc_atr: Optional[float] = None
    distance_to_h4_vah_atr: Optional[float] = None
    distance_to_h4_val_atr: Optional[float] = None
    vp_confluences: tuple["VPConfluence", ...] = ()
    top_down_available: bool = True
    w1_reference_profile_id: Optional[str] = None
    w1_reference_poc: Optional[float] = None
    w1_reference_vah: Optional[float] = None
    w1_reference_val: Optional[float] = None
    h4_reference_profile_id: Optional[str] = None
    h4_reference_poc: Optional[float] = None
    h4_reference_vah: Optional[float] = None
    h4_reference_val: Optional[float] = None

    def record(self) -> dict:
        """Return a flat enough JSON-safe representation for telemetry."""
        value = asdict(self)
        value["profiles"] = [p.record() for p in self.profiles]
        value["zones"] = [z.record() for z in self.zones]
        value["vp_confluences"] = [c.record() for c in self.vp_confluences]
        return value


@dataclass(frozen=True)
class VPConfluence:
    """Independent VP/SR components that occupy one ATR-normalised area."""

    confluence_id: str
    price: float
    zone_low: float
    zone_high: float
    components: tuple[str, ...]
    structural_zone_ids: tuple[str, ...]
    strength_score: float
    distance_from_price_atr: Optional[float]
    kind: str

    def record(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _LevelCandidate:
    price: float
    role: str
    timeframe: str
    created_at: Optional[pd.Timestamp]
    confirmed_at: Optional[pd.Timestamp]
    source: str
    weight: float


@dataclass
class _CandidateCluster:
    candidates: list[_LevelCandidate] = field(default_factory=list)

    @property
    def prices(self) -> list[float]:
        return [float(c.price) for c in self.candidates]

    @property
    def mean(self) -> float:
        weights = np.asarray([max(c.weight, 0.1) for c in self.candidates])
        prices = np.asarray(self.prices)
        return float(np.average(prices, weights=weights))


@dataclass(frozen=True)
class _Swing:
    index: int
    price: float
    role: str
    point_type: str
    pivot_at: Optional[pd.Timestamp]
    confirmed_at: Optional[pd.Timestamp]


@dataclass(frozen=True)
class _LegCandidate:
    left: _Swing
    right: _Swing
    direction: str
    score: float
    leg_size_atr: float
    bars: int
    displacement: float
    kind: str = "CONSECUTIVE_SWING_LEG"


@dataclass(frozen=True)
class _AuctionCandidate:
    """One auction discovered from the current structural state.

    These rows are deliberately created *before* any historical-leg score is
    consulted.  Ranking therefore cannot resurrect an unrelated old leg just
    because it was large.
    """

    leg: _LegCandidate
    contains_current_price: bool
    directly_governs_price: bool
    structural_event: str
    structural_event_index: int
    bos_displacement: float
    pivot_significance: float


def _timestamp(value) -> Optional[pd.Timestamp]:
    if value is None:
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts


def _iso(value) -> Optional[str]:
    ts = _timestamp(value)
    return ts.isoformat() if ts is not None else None


def _as_of(moment) -> Optional[pd.Timestamp]:
    return _timestamp(moment)


def _timeframe_minutes(timeframe: str) -> int:
    return {"M5": 5, "M15": 15, "H1": 60, "H4": 240,
            "D1": 1440, "W1": 10080}.get(timeframe, 0)


def _closed_frame(df: Optional[pd.DataFrame], timeframe: str,
                  as_of: Optional[pd.Timestamp]) -> pd.DataFrame:
    """Normalize and remove bars that were not closed at ``as_of``."""
    if df is None or len(df) == 0:
        return pd.DataFrame()
    frame = df.copy().reset_index(drop=True)
    if "time" in frame.columns:
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
        if as_of is not None:
            minutes = _timeframe_minutes(timeframe)
            if minutes:
                cutoff = as_of - pd.Timedelta(minutes=minutes)
                frame = frame.loc[frame["time"] <= cutoff].copy()
            else:
                frame = frame.loc[frame["time"] < as_of].copy()
    required = {"open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()
    numeric = frame[list(required)].apply(pd.to_numeric, errors="coerce")
    frame.loc[:, list(required)] = numeric
    frame = frame.dropna(subset=list(required)).reset_index(drop=True)
    return frame


def _volume_column(frame: pd.DataFrame) -> Optional[str]:
    """Prefer real volume, then MT5 tick volume; never fabricate volume."""
    for name in ("real_volume", "tick_volume", "volume"):
        if name not in frame.columns:
            continue
        values = pd.to_numeric(frame[name], errors="coerce").to_numpy(dtype=float)
        if np.isfinite(values).any() and np.nanmax(values) > 0:
            return name
    return None


def _profile_target_bins(frame: pd.DataFrame, config: MarketLocationConfig) -> int:
    del frame
    return max(8, int(config.profile_target_bins))


def _build_profile(frame: pd.DataFrame, config: MarketLocationConfig):
    if frame is None or len(frame) < config.min_profile_bars:
        return None, "NO_PROFILE"
    # Active/referenced auction profiles are broker tick-volume profiles.  Do
    # not substitute synthetic unit volume for missing/zero observations.
    if "tick_volume" not in frame.columns:
        return None, "NO_USABLE_VOLUME"
    clean = frame.copy()
    clean["tick_volume"] = pd.to_numeric(clean["tick_volume"], errors="coerce")
    clean = clean.loc[np.isfinite(clean["tick_volume"])
                      & (clean["tick_volume"] > 0)].copy()
    if len(clean) < config.min_profile_bars:
        return None, "NO_USABLE_VOLUME"
    profile = build_profile_auto(
        clean,
        target_bins=_profile_target_bins(clean, config),
        va_pct=0.70,
        volume_column="tick_volume",
    )
    if profile is None or not profile.valid:
        return None, "INVALID_PROFILE"
    return profile, "TICK_VOLUME"


def _profile_nodes(frame: pd.DataFrame, profile: VolumeProfile,
                   volume_column: str) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Classify occupied histogram bins without treating empty bins as LVNs."""
    highs = frame["high"].to_numpy(dtype=np.float64)
    lows = frame["low"].to_numpy(dtype=np.float64)
    volumes = frame[volume_column].to_numpy(dtype=np.float64)
    hist = _accumulate(highs, lows, volumes, profile.profile_low,
                       profile.bin_size, profile.bin_count)
    occupied = hist[hist > 0]
    if len(occupied) < 2:
        return (), ()
    mean = float(occupied.mean())
    std = float(occupied.std())
    if std <= 0:
        return (), ()
    hvn = tuple(float(profile.profile_low + i * profile.bin_size)
                for i, value in enumerate(hist) if value >= mean + std)
    lvn = tuple(float(profile.profile_low + i * profile.bin_size)
                for i, value in enumerate(hist) if 0 < value <= mean - std)
    return hvn, lvn


def _profile_id(symbol: str, timeframe: str, start: Optional[pd.Timestamp],
                end: Optional[pd.Timestamp], status: str) -> str:
    raw = f"{symbol}|{timeframe}|{_iso(start)}|{_iso(end)}|{status}"
    return "VP-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _resolve_dual_pivot(frame: pd.DataFrame, index: int,
                        lookback: int) -> str:
    """Resolve a candle that is both a fractal high and low.

    The subsequent confirmed displacement is the primary evidence.  Relative
    extremity and the preceding close trend are deterministic tie-breakers.
    Returning exactly one role prevents the same candle from being both ends
    of one structural path.
    """
    row = frame.iloc[index]
    high, low = float(row["high"]), float(row["low"])
    before = frame.iloc[max(0, index - 2 * lookback):index]
    after = frame.iloc[index + 1:min(len(frame), index + 1 + 2 * lookback)]
    if not after.empty:
        up = float(after["high"].max()) - low
        down = high - float(after["low"].min())
        if up > down + 1e-12:
            return SUPPORT
        if down > up + 1e-12:
            return RESISTANCE

    surrounding = pd.concat((before, after), ignore_index=True)
    if not surrounding.empty:
        high_prominence = high - float(surrounding["high"].max())
        low_prominence = float(surrounding["low"].min()) - low
        if low_prominence > high_prominence + 1e-12:
            return SUPPORT
        if high_prominence > low_prominence + 1e-12:
            return RESISTANCE

    if len(before) >= 2:
        return (SUPPORT if float(before["close"].iloc[-1])
                <= float(before["close"].iloc[0]) else RESISTANCE)
    # Stable final tie-break: the close location describes which rejection
    # survived the candle.
    midpoint = (high + low) * 0.5
    return SUPPORT if float(row["close"]) >= midpoint else RESISTANCE


def _structure_swings(frame: pd.DataFrame, symbol: str, timeframe: str,
                      lookback: int) -> list[_Swing]:
    del symbol
    if frame is None or len(frame) < max(20, 2 * lookback + 1):
        return []
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    raw: list[tuple[int, float, str]] = []
    for index in range(lookback, len(frame) - lookback):
        is_high = highs[index] == np.nanmax(
            highs[index - lookback:index + lookback + 1])
        is_low = lows[index] == np.nanmin(
            lows[index - lookback:index + lookback + 1])
        if is_high and is_low:
            role = _resolve_dual_pivot(frame, index, lookback)
            raw.append((index, float(lows[index] if role == SUPPORT
                                     else highs[index]), role))
        elif is_high:
            raw.append((index, float(highs[index]), RESISTANCE))
        elif is_low:
            raw.append((index, float(lows[index]), SUPPORT))

    # Enforce alternation after dual-pivot resolution.  Consecutive pivots of
    # one role collapse to the more extreme observation.
    alternating: list[tuple[int, float, str]] = []
    for pivot in raw:
        if not alternating or pivot[2] != alternating[-1][2]:
            alternating.append(pivot)
            continue
        if ((pivot[2] == RESISTANCE and pivot[1] > alternating[-1][1])
                or (pivot[2] == SUPPORT and pivot[1] < alternating[-1][1])):
            alternating[-1] = pivot

    result: list[_Swing] = []
    last_high: Optional[float] = None
    last_low: Optional[float] = None
    for index, price, role in alternating[-20:]:
        if role == RESISTANCE:
            point_type = "HH" if last_high is None or price > last_high else "LH"
            last_high = price
        else:
            point_type = "LL" if last_low is None or price < last_low else "HL"
            last_low = price
        pivot_at = (_timestamp(frame.iloc[index].get("time"))
                    if "time" in frame else None)
        confirm_index = index + lookback
        confirm_at = (_timestamp(frame.iloc[confirm_index].get("time"))
                      if "time" in frame else None)
        if confirm_at is not None:
            confirm_at += pd.Timedelta(minutes=_timeframe_minutes(timeframe))
        result.append(_Swing(index, price, role, point_type,
                             pivot_at, confirm_at))
    return result


def _structure_candidates(frame: pd.DataFrame, symbol: str, timeframe: str,
                           lookback: int, config: MarketLocationConfig):
    swings = _structure_swings(frame, symbol, timeframe, lookback)
    candidates: list[_LevelCandidate] = []
    for swing in swings:
        candidates.append(_LevelCandidate(
            price=swing.price, role=swing.role, timeframe=timeframe,
            created_at=swing.pivot_at, confirmed_at=swing.confirmed_at,
            source=f"{timeframe}_STRUCTURAL_{swing.point_type}", weight=2.0))
    # A large alternating move gives the pivot a second, independent reason to
    # matter: it is the origin/extreme of a displacement leg.
    for left, right in zip(swings[:-1], swings[1:]):
        if left.role == right.role:
            continue
        move = abs(right.price - left.price)
        local_atr = _atr(frame.iloc[:right.index + 1], config.atr_period)
        if local_atr > 0 and move >= config.min_impulse_atr * local_atr:
            candidates.append(_LevelCandidate(
                price=left.price, role=left.role, timeframe=timeframe,
                created_at=left.pivot_at, confirmed_at=right.confirmed_at,
                source=f"{timeframe}_IMPULSE_ORIGIN", weight=1.5))
            candidates.append(_LevelCandidate(
                price=right.price, role=right.role, timeframe=timeframe,
                created_at=right.pivot_at, confirmed_at=right.confirmed_at,
                source=f"{timeframe}_IMPULSE_EXTREME", weight=1.5))
    return candidates, swings


def _daily_reference_candidates(df_d1: pd.DataFrame,
                                as_of: Optional[pd.Timestamp]):
    candidates: list[_LevelCandidate] = []
    if df_d1 is None or df_d1.empty:
        return candidates
    last = df_d1.iloc[-1]
    last_at = _timestamp(last.get("time"))
    confirmed = (last_at + pd.Timedelta(days=1) if last_at is not None else None)
    candidates.extend((
        _LevelCandidate(float(last["high"]), RESISTANCE, "D1", last_at,
                        confirmed, "PDH", 2.5),
        _LevelCandidate(float(last["low"]), SUPPORT, "D1", last_at,
                        confirmed, "PDL", 2.5),
    ))
    if "time" not in df_d1.columns:
        return candidates
    times = pd.to_datetime(df_d1["time"], utc=True)
    week_keys = times.map(lambda x: (x.isocalendar().year,
                                     x.isocalendar().week))
    complete = []
    for key in dict.fromkeys(week_keys.tolist()):
        rows = df_d1.loc[week_keys == key]
        if rows.empty:
            continue
        start = _timestamp(rows["time"].iloc[0])
        week_end = (start - pd.Timedelta(days=start.weekday())
                    + pd.Timedelta(days=7)) if start is not None else None
        if as_of is None or week_end is None or week_end <= as_of:
            complete.append((key, rows))
    if complete:
        _, rows = complete[-1]
        start = _timestamp(rows["time"].iloc[0])
        end = _timestamp(rows["time"].iloc[-1])
        candidates.extend((
            _LevelCandidate(float(rows["high"].max()), RESISTANCE, "W1", start,
                            end, "PWH", 2.0),
            _LevelCandidate(float(rows["low"].min()), SUPPORT, "W1", start,
                            end, "PWL", 2.0),
        ))
    return candidates


def _rejection_candidates(frame: pd.DataFrame, timeframe: str,
                          config: MarketLocationConfig):
    result: list[_LevelCandidate] = []
    if frame is None or len(frame) < 3:
        return result
    local_atr = _atr(frame, config.atr_period)
    if local_atr <= 0:
        return result
    tail = frame.tail(min(len(frame), 120))
    for _, row in tail.iterrows():
        high, low = float(row["high"]), float(row["low"])
        open_, close = float(row["open"]), float(row["close"])
        span = high - low
        if span < 1.2 * local_atr:
            continue
        upper_wick = high - max(open_, close)
        lower_wick = min(open_, close) - low
        at = _timestamp(row.get("time"))
        confirmed = (at + pd.Timedelta(minutes=_timeframe_minutes(timeframe))
                     if at is not None else None)
        if upper_wick >= config.rejection_wick_frac * span and close <= low + 0.40 * span:
            result.append(_LevelCandidate(high, RESISTANCE, timeframe, at,
                                          confirmed, f"{timeframe}_STRONG_REJECTION", 1.2))
        if lower_wick >= config.rejection_wick_frac * span and close >= high - 0.40 * span:
            result.append(_LevelCandidate(low, SUPPORT, timeframe, at,
                                          confirmed, f"{timeframe}_STRONG_REJECTION", 1.2))
    return result


def _consolidation_candidates(frame: pd.DataFrame, timeframe: str,
                              config: MarketLocationConfig):
    result: list[_LevelCandidate] = []
    if frame is None or len(frame) < config.consolidation_bars_h4:
        return result
    local_atr = _atr(frame, config.atr_period)
    if local_atr <= 0:
        return result
    width = config.consolidation_bars_h4
    # Non-overlapping blocks keep this evidence sparse; clustering handles
    # boundaries that recur in adjacent blocks.
    start = max(0, len(frame) - 5 * width)
    for i in range(start, len(frame) - width + 1, width):
        block = frame.iloc[i:i + width]
        high, low = float(block["high"].max()), float(block["low"].min())
        close_range = float(block["close"].max() - block["close"].min())
        if high - low > 3.0 * local_atr or close_range > 2.0 * local_atr:
            continue
        at = _timestamp(block["time"].iloc[0]) if "time" in block else None
        confirmed = (_timestamp(block["time"].iloc[-1])
                     + pd.Timedelta(minutes=_timeframe_minutes(timeframe))
                     if "time" in block else None)
        result.extend((
            _LevelCandidate(high, RESISTANCE, timeframe, at, confirmed,
                            f"{timeframe}_CONSOLIDATION_HIGH", 1.0),
            _LevelCandidate(low, SUPPORT, timeframe, at, confirmed,
                            f"{timeframe}_CONSOLIDATION_LOW", 1.0),
        ))
    return result


def _cluster(candidates: Sequence[_LevelCandidate], tolerance: float):
    clusters: list[_CandidateCluster] = []
    for candidate in sorted(candidates, key=lambda c: c.price):
        target = None
        for cluster in reversed(clusters):
            if abs(candidate.price - cluster.mean) <= tolerance:
                target = cluster
                break
            if candidate.price > cluster.mean + tolerance:
                break
        if target is None:
            target = _CandidateCluster()
            clusters.append(target)
        target.candidates.append(candidate)
    return clusters


def _touch_stats(zone_low: float, zone_high: float, candidates: Sequence[_LevelCandidate],
                 frames: Sequence[tuple[str, pd.DataFrame]], config: MarketLocationConfig):
    created = min((c.confirmed_at or c.created_at for c in candidates
                   if c.confirmed_at is not None or c.created_at is not None),
                  default=None)
    touches: list[pd.Timestamp] = []
    rejections: list[pd.Timestamp] = []
    role = SUPPORT if sum(c.role == SUPPORT for c in candidates) >= sum(c.role == RESISTANCE for c in candidates) else RESISTANCE
    for timeframe, frame in frames:
        if frame is None or frame.empty:
            continue
        local_atr = _atr(frame, config.atr_period)
        for _, row in frame.iterrows():
            at = _timestamp(row.get("time"))
            if at is None or (created is not None and at < created):
                continue
            high, low = float(row["high"]), float(row["low"])
            if high < zone_low or low > zone_high:
                continue
            touches.append(at)
            span = max(high - low, 1e-12)
            open_, close = float(row["open"]), float(row["close"])
            upper = high - max(open_, close)
            lower = min(open_, close) - low
            if role == RESISTANCE and upper / span >= config.rejection_wick_frac and close < zone_low:
                rejections.append(at)
            elif role == SUPPORT and lower / span >= config.rejection_wick_frac and close > zone_high:
                rejections.append(at)
    return (len(touches), len(rejections), max(touches) if touches else None,
            created)


def _acceptance(zone_low: float, zone_high: float,
                candidates: Sequence[_LevelCandidate], frames,
                config: MarketLocationConfig):
    original = (SUPPORT if sum(c.role == SUPPORT for c in candidates)
                >= sum(c.role == RESISTANCE for c in candidates)
                else RESISTANCE)
    if not frames:
        return original, None, None
    frame = max((f for _, f in frames if f is not None and not f.empty),
                key=len, default=None)
    if frame is None or len(frame) < config.acceptance_bars:
        return original, None, None
    local_atr = _atr(frame, config.atr_period)
    buffer = config.acceptance_buffer_atr * (local_atr or 0.0)
    closes = frame["close"].astype(float).to_numpy()
    tail = closes[-config.acceptance_bars:]
    at = (_timestamp(frame["time"].iloc[-1]) if "time" in frame else None)
    if original == RESISTANCE and len(tail) == config.acceptance_bars and np.all(tail > zone_high + buffer):
        return FLIP, SUPPORT, at
    if original == SUPPORT and len(tail) == config.acceptance_bars and np.all(tail < zone_low - buffer):
        return FLIP, RESISTANCE, at
    return original, None, None


def _make_zones(candidates: Sequence[_LevelCandidate], frames,
                current_price: float, atr_value: float,
                config: MarketLocationConfig, symbol: str = "") -> tuple[SRZone, ...]:
    if not candidates or atr_value <= 0:
        return ()
    tolerance = max(config.cluster_atr * atr_value, 1e-9)
    zones = []
    for cluster in _cluster(candidates, tolerance):
        if not cluster.candidates:
            continue
        prices = cluster.prices
        mean = cluster.mean
        low = min(prices) - tolerance * 0.5
        high = max(prices) + tolerance * 0.5
        roles = {c.role for c in cluster.candidates}
        original = (next(iter(roles)) if len(roles) == 1 else FLIP)
        zone_type, flip_to, acceptance_at = _acceptance(low, high,
                                                        cluster.candidates,
                                                        frames, config)
        touch_count, rejection_count, last_touch, created = _touch_stats(
            low, high, cluster.candidates, frames, config)
        source = "|".join(dict.fromkeys(c.source for c in cluster.candidates))
        timeframe = "+".join(dict.fromkeys(c.timeframe for c in cluster.candidates))
        source_weight = sum(c.weight for c in cluster.candidates)
        strength = min(1.0, 0.25 + 0.08 * source_weight
                       + min(0.25, touch_count * 0.025)
                       + min(0.25, rejection_count * 0.06))
        distance = (0.0 if low <= current_price <= high else
                    min(abs(current_price - low), abs(current_price - high)))
        raw = f"{symbol}|{timeframe}|{mean:.8f}|{source}"
        zone_id = "SR-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        zones.append(SRZone(
            zone_id=zone_id, price=mean, zone_low=low, zone_high=high,
            type=zone_type, timeframe=timeframe,
            created_at=_iso(created), last_touch=_iso(last_touch),
            touch_count=touch_count, rejection_count=rejection_count,
            strength_score=round(strength, 6), source=source, active=True,
            distance_from_price=distance, flip_to=flip_to,
            original_type=original, acceptance_confirmed_at=_iso(acceptance_at)))
    # Keep the authoritative display/evidence set small.  Historical source
    # candidates are not deleted; only the current location view is capped.
    zones.sort(key=lambda z: (0 if z.contains_price else 1,
                              z.distance_from_price, -z.strength_score))
    return tuple(zones[:config.max_zones])


def _rank_structural_legs(frame: pd.DataFrame, swings: Sequence[_Swing],
                          config: MarketLocationConfig) -> list[_LegCandidate]:
    """Rank confirmed swing-to-swing auctions without making recency dominant."""
    legs: list[_LegCandidate] = []
    for left, right in zip(swings[:-1], swings[1:]):
        if left.role == right.role or right.index <= left.index:
            continue
        segment = frame.iloc[left.index:right.index + 1]
        if len(segment) < config.min_profile_bars:
            continue
        atr_value = max(_atr(frame.iloc[:right.index + 1], config.atr_period),
                        _atr(segment, config.atr_period))
        if atr_value <= 0:
            continue
        move = abs(right.price - left.price)
        leg_size_atr = move / atr_value
        if leg_size_atr < config.min_anchor_leg_atr:
            continue
        closes = segment["close"].astype(float).to_numpy()
        path = float(np.abs(np.diff(closes)).sum()) if len(closes) > 1 else move
        displacement = min(1.0, move / max(path, move, 1e-9))
        volume_bonus = 0.0
        volume_column = _volume_column(segment)
        if volume_column and left.index >= config.atr_period:
            prior = frame.iloc[max(0, left.index - config.atr_period):left.index]
            prior_volume = pd.to_numeric(prior[volume_column], errors="coerce").mean()
            leg_volume = pd.to_numeric(segment[volume_column], errors="coerce").mean()
            if np.isfinite(prior_volume) and prior_volume > 0 and np.isfinite(leg_volume):
                volume_bonus = min(1.0, max(0.0, leg_volume / prior_volume - 1.0))
        duration_bonus = min(1.0, len(segment) / max(2.0 * config.atr_period, 1.0))
        # Size and displacement are the primary evidence.  Duration/volume
        # break ties; recency is intentionally a small tie-breaker only.
        recency_bonus = (right.index + 1) / max(len(frame), 1)
        score = (0.60 * min(leg_size_atr, 8.0)
                 + 1.25 * displacement
                 + 0.35 * duration_bonus
                 + 0.30 * volume_bonus
                 + 0.10 * recency_bonus)
        direction = "UP" if right.price >= left.price else "DOWN"
        legs.append(_LegCandidate(
            left=left, right=right, direction=direction,
            score=float(score), leg_size_atr=float(leg_size_atr),
            bars=int(len(segment)), displacement=float(displacement)))
    # A visible auction can run from a confirmed range extreme through
    # several internal swings.  Add those composite candidates separately;
    # they must be materially larger than the minimum leg, so this does not
    # turn every pair of small pivots into a profile.
    composite_floor = config.min_anchor_leg_atr * 1.50
    for left_index, left in enumerate(swings[:-1]):
        for right in swings[left_index + 1:]:
            if left.role == right.role or right.index <= left.index:
                continue
            segment = frame.iloc[left.index:right.index + 1]
            if len(segment) < config.min_profile_bars * 2:
                continue
            atr_value = max(_atr(frame.iloc[:right.index + 1], config.atr_period),
                            _atr(segment, config.atr_period))
            if atr_value <= 0:
                continue
            move = abs(right.price - left.price)
            leg_size_atr = move / atr_value
            if leg_size_atr < composite_floor:
                continue
            closes = segment["close"].astype(float).to_numpy()
            path = float(np.abs(np.diff(closes)).sum()) if len(closes) > 1 else move
            displacement = min(1.0, move / max(path, move, 1e-9))
            duration_bonus = min(1.0, len(segment) / max(4.0 * config.atr_period, 1.0))
            recency_bonus = (right.index + 1) / max(len(frame), 1)
            score = (0.60 * min(leg_size_atr, 8.0)
                     + 1.00 * displacement
                     + 0.25 * duration_bonus
                     + 0.10 * recency_bonus)
            legs.append(_LegCandidate(
                left=left, right=right,
                direction="UP" if right.price >= left.price else "DOWN",
                score=float(score), leg_size_atr=float(leg_size_atr),
                bars=int(len(segment)), displacement=float(displacement),
                kind="COMPOSITE_STRUCTURAL_LEG"))
    return sorted(legs, key=lambda leg: (-leg.score, -leg.leg_size_atr,
                                         leg.right.index))


def _current_auction_leg(frame: pd.DataFrame, left: _Swing, right: _Swing,
                         config: MarketLocationConfig) -> Optional[_LegCandidate]:
    """Measure one already-discovered structural auction.

    Unlike ``_rank_structural_legs`` this function never enumerates history.
    It is called only after current structure selected the endpoints.
    """
    if right.index <= left.index:
        return None
    segment = frame.iloc[left.index:right.index + 1]
    if len(segment) < config.min_profile_bars:
        return None
    atr_value = max(_atr(frame.iloc[:right.index + 1], config.atr_period),
                    _atr(segment, config.atr_period))
    if atr_value <= 0:
        return None
    move = abs(right.price - left.price)
    leg_size_atr = move / atr_value
    if leg_size_atr < config.min_anchor_leg_atr:
        return None
    closes = segment["close"].astype(float).to_numpy()
    path = float(np.abs(np.diff(closes)).sum()) if len(closes) > 1 else move
    displacement = min(1.0, move / max(path, move, 1e-9))
    duration_bonus = min(1.0, len(segment) / max(2.0 * config.atr_period, 1.0))
    score = (0.60 * min(leg_size_atr, 8.0)
             + 1.25 * displacement + 0.35 * duration_bonus)
    return _LegCandidate(
        left=left, right=right,
        direction="UP" if right.price >= left.price else "DOWN",
        score=float(score), leg_size_atr=float(leg_size_atr),
        bars=int(len(segment)), displacement=float(displacement),
        kind="CURRENT_AUCTION")


def _discover_active_auctions(frame: pd.DataFrame, swings: Sequence[_Swing],
                              current_price: float,
                              config: MarketLocationConfig) -> list[_AuctionCandidate]:
    """Discover the small set of auctions that can govern price *now*.

    The latest HH/LL is the latest confirmed structural break.  If it already
    has an opposite boundary after it (for example W1 LL -> LH), that is the
    current dealing range.  Otherwise the break is the new boundary and the
    preceding opposite HH/LL is the controlling origin (for example H4
    sweep-LL -> HH).  Only after this structural pass do scores enter.
    """
    if len(swings) < 2:
        return []
    atr_value = max(_atr(frame, config.atr_period), 1e-12)
    discoveries: list[_AuctionCandidate] = []
    seen: set[tuple[int, int]] = set()

    def add(left: _Swing, right: _Swing, event: _Swing,
            event_name: str) -> None:
        if left.index > right.index:
            left, right = right, left
        key = (left.index, right.index)
        if key in seen:
            return
        leg = _current_auction_leg(frame, left, right, config)
        if leg is None:
            return
        low, high = sorted((left.price, right.price))
        contains = low <= current_price <= high
        governs = contains or min(abs(current_price - low),
                                   abs(current_price - high)) <= 0.50 * atr_value
        if not governs:
            return
        local = frame.iloc[max(0, event.index - config.atr_period):
                           min(len(frame), right.index + 1)]
        event_atr = max(_atr(local, config.atr_period), atr_value, 1e-12)
        displacement = abs(right.price - left.price) / event_atr
        neighbours = [s for s in swings
                      if abs(s.index - event.index) <= 2 * config.atr_period
                      and s.index != event.index]
        significance = (min((abs(event.price - s.price) for s in neighbours),
                            default=abs(right.price - left.price)) / event_atr)
        discoveries.append(_AuctionCandidate(
            leg=leg, contains_current_price=contains,
            directly_governs_price=governs,
            structural_event=event_name,
            structural_event_index=event.index,
            bos_displacement=float(displacement),
            pivot_significance=float(significance)))
        seen.add(key)

    events = [s for s in swings if s.point_type in {"HH", "LL"}]
    for event in reversed(events):
        opposite_role = SUPPORT if event.role == RESISTANCE else RESISTANCE
        after = [s for s in swings
                 if s.index > event.index and s.role == opposite_role]
        if after:
            add(event, after[-1], event,
                f"LATEST_{event.point_type}_WITH_CONFIRMED_OPPOSITE_BOUNDARY")
        else:
            before_events = [s for s in events
                             if s.index < event.index
                             and s.role == opposite_role]
            if before_events:
                add(before_events[-1], event, event,
                    f"LATEST_{event.point_type}_WITH_CONTROLLING_ORIGIN")
        if len(discoveries) >= 6:
            break

    # Robust fallback for young/ranging histories: latest confirmed adjacent
    # dealing ranges that actually contain (or directly govern) price.
    for left, right in reversed(list(zip(swings[:-1], swings[1:]))):
        add(left, right, right, "CURRENT_CONFIRMED_DEALING_RANGE")
        if len(discoveries) >= 8:
            break
    return discoveries


def _rank_active_auctions(
        candidates: Sequence[_AuctionCandidate]) -> list[_AuctionCandidate]:
    """Rank only structurally discovered current auctions.

    Lexicographic priority intentionally keeps historical magnitude last:
    containment -> latest confirmed structure -> BOS/displacement -> pivot
    significance -> leg magnitude.  Candidate discovery has already excluded
    irrelevant history.
    """
    return sorted(candidates, key=lambda item: (
        0 if item.contains_current_price else 1,
        -item.structural_event_index,
        0 if item.structural_event.startswith("LATEST_") else 1,
        -item.bos_displacement,
        -item.pivot_significance,
        -item.leg.leg_size_atr,
        -item.leg.right.index,
    ))


def _detail_hierarchy(timeframe: str) -> tuple[str, ...]:
    if timeframe == "W1":
        return ("M15", "H1", "W1")
    if timeframe == "H4":
        return ("M5", "M15", "H4")
    return (timeframe,)


def _profile_source_segment(
        frame: pd.DataFrame, timeframe: str, leg: _LegCandidate,
        detail_frames: dict[str, pd.DataFrame],
        config: MarketLocationConfig) -> tuple[pd.DataFrame, str]:
    """Choose the finest complete tick-volume frame inside the anchor only."""
    native = frame.iloc[leg.left.index:leg.right.index + 1].copy()
    start_at, end_at = leg.left.pivot_at, leg.right.pivot_at
    if start_at is None or end_at is None:
        return native, timeframe
    end_exclusive = end_at + pd.Timedelta(minutes=_timeframe_minutes(timeframe))
    expected_volume = (float(pd.to_numeric(native["tick_volume"], errors="coerce")
                             .fillna(0.0).clip(lower=0.0).sum())
                       if "tick_volume" in native else 0.0)

    for source_timeframe in _detail_hierarchy(timeframe):
        source = (frame if source_timeframe == timeframe
                  else detail_frames.get(source_timeframe, pd.DataFrame()))
        if source is None or source.empty or "time" not in source.columns:
            continue
        times = pd.to_datetime(source["time"], utc=True)
        minutes = _timeframe_minutes(source_timeframe)
        segment = source.loc[(times >= start_at)
                             & (times + pd.Timedelta(minutes=minutes)
                                <= end_exclusive)].copy()
        if len(segment) < config.min_profile_bars:
            continue
        if "tick_volume" not in segment:
            continue
        observed_volume = float(pd.to_numeric(
            segment["tick_volume"], errors="coerce").fillna(0.0)
            .clip(lower=0.0).sum())
        # Native MT5 bars are the completeness checksum for their own lower
        # timeframe reconstruction.  This catches truncated terminal history
        # without guessing around weekend/holiday gaps.
        if source_timeframe != timeframe and expected_volume > 0:
            ratio = observed_volume / expected_volume
            if not 0.995 <= ratio <= 1.005:
                continue
            expected_rows = (len(native) * _timeframe_minutes(timeframe)
                             / max(minutes, 1))
            # W1 contains weekend closures (roughly 5/7 of calendar rows);
            # other MT5 frames should be almost fully represented.
            minimum_coverage = 0.55 if timeframe == "W1" else 0.90
            if len(segment) < expected_rows * minimum_coverage:
                continue
        return segment.reset_index(drop=True), source_timeframe
    return native.reset_index(drop=True), timeframe


def _profile_record(frame: pd.DataFrame, timeframe: str, leg: _LegCandidate,
                    status: str, source: str, config: MarketLocationConfig,
                    symbol: str, replacement_reason: str = "",
                    detail_frames: Optional[dict[str, pd.DataFrame]] = None):
    segment, source_timeframe = _profile_source_segment(
        frame, timeframe, leg, detail_frames or {}, config)
    profile, quality = _build_profile(segment, config)
    if profile is None:
        return None
    start_at = leg.left.pivot_at
    end_at = leg.right.pivot_at
    clean = segment.copy()
    clean["tick_volume"] = pd.to_numeric(clean["tick_volume"], errors="coerce")
    clean = clean.loc[np.isfinite(clean["tick_volume"])
                      & (clean["tick_volume"] > 0)]
    hvn, lvn = _profile_nodes(clean, profile, "tick_volume")
    anchor_low, anchor_high = sorted((float(leg.left.price), float(leg.right.price)))
    return ProfileRecord(
        profile_id=_profile_id(symbol, timeframe,
                               start_at, end_at,
                               "ACTIVE" if status == PROFILE_ACTIVE else "REFERENCE"),
        status=status, timeframe=timeframe, profile=profile,
        profile_start=_iso(start_at), profile_end=_iso(end_at),
        profile_high=float(profile.profile_high), profile_low=float(profile.profile_low),
        direction=leg.direction, total_volume=float(profile.total_volume),
        data_quality=quality,
        source=source,
        symbol=symbol.upper(),
        high_volume_nodes=hvn, low_volume_nodes=lvn,
        anchor_low=anchor_low, anchor_high=anchor_high,
        anchor_start_price=float(leg.left.price),
        anchor_end_price=float(leg.right.price),
        anchor_start_time=_iso(leg.left.pivot_at),
        anchor_end_time=_iso(leg.right.pivot_at),
        confirmation_time=_iso(leg.right.confirmed_at),
        replacement_reason=replacement_reason or None,
        selection_score=round(leg.score, 6),
        leg_size_atr=round(leg.leg_size_atr, 6),
        anchor_bars=leg.bars,
        row_count=int(config.profile_target_bins),
        volume_source="tick_volume",
        source_timeframe=source_timeframe,
        actual_value_area_percentage=round(
            float(profile.value_area_pct_actual * 100.0), 6),
    )


def _profiles_for_frame(frame: pd.DataFrame, symbol: str, timeframe: str,
                        lookback: int, config: MarketLocationConfig,
                        current_price: float,
                        detail_frames: Optional[dict[str, pd.DataFrame]] = None,
                        prior: Optional[ProfileRecord] = None,
                        prior_as_of: Optional[pd.Timestamp] = None):
    swings = _structure_swings(frame, symbol, timeframe, lookback)
    if len(swings) < 2:
        return []
    discovered = _discover_active_auctions(
        frame, swings, current_price, config)
    ranked_active = _rank_active_auctions(discovered)
    if not ranked_active:
        if prior is not None and "CURRENT_AUCTION" in str(prior.source):
            # An absence of a newly confirmed governing auction is not an
            # objective replacement event.  Keep the frozen ACTIVE profile.
            return [replace(prior, replacement_reason="HOLD_CURRENT_AUCTION")]
        return []
    # Historical scoring is intentionally delayed until ACTIVE discovery is
    # complete; it exists only to populate REFERENCE context.
    historical_legs = _rank_structural_legs(frame, swings, config)

    def same_anchor(leg: _LegCandidate, record: ProfileRecord) -> bool:
        return (_iso(leg.left.pivot_at) == record.anchor_start_time
                and _iso(leg.right.pivot_at) == record.anchor_end_time
                and leg.direction == record.direction)

    def same_leg(left: _LegCandidate, right: _LegCandidate) -> bool:
        return (_iso(left.left.pivot_at) == _iso(right.left.pivot_at)
                and _iso(left.right.pivot_at) == _iso(right.right.pivot_at)
                and left.direction == right.direction)

    best = ranked_active[0].leg
    current_legs = [candidate.leg for candidate in ranked_active]
    prior_leg = next((leg for leg in current_legs + historical_legs
                      if prior is not None and same_anchor(leg, prior)), None)
    prior_is_current_architecture = bool(
        prior and "CURRENT_AUCTION" in str(prior.source))
    if prior_leg is not None and prior_is_current_architecture:
        prior_low, prior_high = sorted((prior_leg.left.price,
                                       prior_leg.right.price))
        prior_contains = prior_low <= current_price <= prior_high
        replacement_confirmed = (
            not same_leg(best, prior_leg)
            and best.right.confirmed_at is not None
            and (prior_as_of is None or best.right.confirmed_at > prior_as_of))
        if ((timeframe == "W1" and not replacement_confirmed)
                or (timeframe != "W1" and prior_contains
                    and not replacement_confirmed)):
            chosen = prior_leg
            replacement = "HOLD_CURRENT_AUCTION"
        else:
            chosen = best
            replacement = "STRUCTURAL_REPLACEMENT"
    else:
        chosen = best
        replacement = ("ARCHITECTURE_MIGRATION_CURRENT_AUCTION"
                       if prior is not None else "INITIAL_CURRENT_AUCTION")

    active = _profile_record(
        frame, timeframe, chosen, PROFILE_ACTIVE,
        f"{timeframe}_ACTIVE_CURRENT_AUCTION_{chosen.kind}",
        config, symbol, replacement, detail_frames)
    if active is None:
        return []
    result = [active]
    # Historical ranking is retained only for REFERENCE context.  Reproduce
    # the former near-best/latest policy for the first reference so the old
    # macro profile remains available without controlling active location.
    best_historical = historical_legs[0] if historical_legs else None
    reference_floor = ((best_historical.score * (
        config.w1_recent_score_floor if timeframe == "W1"
        else config.h4_recent_score_floor if timeframe == "H4" else 1.0))
                       if best_historical is not None else math.inf)
    eligible = [leg for leg in historical_legs if leg.score >= reference_floor
                and not same_leg(leg, chosen)]
    ordered_references: list[_LegCandidate] = []
    if eligible:
        ordered_references.append(max(
            eligible, key=lambda leg: (leg.right.index, leg.score)))
    ordered_references.extend(leg for leg in historical_legs
                              if not same_leg(leg, chosen)
                              and all(not same_leg(leg, existing)
                                      for existing in ordered_references))
    for reference_index, leg in enumerate(ordered_references):
        if same_leg(leg, chosen):
            continue
        if len(result) >= config.max_profiles_per_timeframe:
            break
        reference = _profile_record(
            frame, timeframe, leg, PROFILE_REFERENCE,
            f"{timeframe}_REFERENCE_{'PRIMARY_' if reference_index == 0 else ''}{leg.kind}",
            config, symbol,
            detail_frames=detail_frames)
        if reference is not None:
            result.append(reference)
    return result


def _profile_distance(price: float, level: Optional[float], atr_value: float):
    if level is None or atr_value <= 0:
        return None
    return abs(price - float(level)) / atr_value


def _nearest_level(price: float, values: Iterable[tuple[float, object]], below: bool):
    candidates = [(float(level), obj) for level, obj in values
                  if (level <= price if below else level >= price)]
    if not candidates:
        return None
    return (max(candidates, key=lambda x: x[0]) if below
            else min(candidates, key=lambda x: x[0]))


def _select_primary_profile(profiles: Sequence[ProfileRecord], price: float):
    if not profiles:
        return None
    # Hierarchy is semantic, not proximity-based: H4 is the local execution
    # profile, W1 is macro context, and M15 is only a last-resort fallback for
    # legacy callers that did not provide top-down frames.
    for timeframe in ("H4", "W1", "M15"):
        active = [p for p in profiles
                  if p.status == PROFILE_ACTIVE and p.timeframe == timeframe]
        if active:
            return min(active, key=lambda p: abs(p.poc - price))
    return min(profiles, key=lambda p: abs(p.poc - price))


def _value_location(price: float, profile: Optional[ProfileRecord],
                    atr_value: float, config: MarketLocationConfig):
    if profile is None or atr_value <= 0:
        return UNKNOWN_LOCATION
    base = config.profile_level_atr * atr_value
    # Value-area width is useful as a secondary scale, but must not turn a
    # wide, multi-ATR profile into a several-dollar "POC zone".  The ATR cap
    # keeps the label local and comparable across XAUUSD volatility regimes.
    tolerance = max(base, min(config.value_edge_frac * (profile.vah - profile.val),
                              2.0 * base))
    poc_tolerance = max(base * 0.75,
                        min(0.10 * (profile.vah - profile.val), base))
    if abs(price - profile.poc) <= poc_tolerance:
        return AT_POC
    if abs(price - profile.vah) <= tolerance:
        return AT_VAH
    if abs(price - profile.val) <= tolerance:
        return AT_VAL
    if price > profile.vah + tolerance:
        return ABOVE_VALUE
    if price < profile.val - tolerance:
        return BELOW_VALUE
    return INSIDE_VALUE


def _value_event(frame: pd.DataFrame, profile: Optional[ProfileRecord],
                 atr_value: float, config: MarketLocationConfig) -> str:
    if profile is None or frame is None or len(frame) < 2 or atr_value <= 0:
        return NO_VALUE_EVENT
    base = config.profile_level_atr * atr_value
    tolerance = max(base, min(config.value_edge_frac * (profile.vah - profile.val),
                              2.0 * base))
    last = frame.iloc[-1]
    high, low = float(last["high"]), float(last["low"])
    open_, close = float(last["open"]), float(last["close"])
    span = max(high - low, 1e-12)
    if high >= profile.vah - tolerance and close < profile.vah - tolerance * 0.25:
        if (high - max(open_, close)) / span >= config.rejection_wick_frac:
            return VAH_REJECTION
    if low <= profile.val + tolerance and close > profile.val + tolerance * 0.25:
        if (min(open_, close) - low) / span >= config.rejection_wick_frac:
            return VAL_REJECTION
    closes = frame["close"].astype(float).to_numpy()
    if len(closes) >= config.acceptance_bars and np.all(
            closes[-config.acceptance_bars:] > profile.vah + tolerance * 0.25):
        return VAH_ACCEPTANCE
    if len(closes) >= config.acceptance_bars and np.all(
            closes[-config.acceptance_bars:] < profile.val - tolerance * 0.25):
        return VAL_ACCEPTANCE
    previous, current = closes[-2], closes[-1]
    if previous <= profile.poc and current > profile.poc + tolerance * 0.25:
        return POC_RECLAIM
    if previous >= profile.poc and current < profile.poc - tolerance * 0.25:
        return POC_LOSS
    return NO_VALUE_EVENT


def _build_vp_confluences(weekly: Optional[ProfileRecord],
                          local: Optional[ProfileRecord],
                          zones: Sequence[SRZone], price: float,
                          atr_value: float,
                          config: MarketLocationConfig) -> tuple[VPConfluence, ...]:
    """Join independent W1/H4 levels only after each profile is complete."""
    if atr_value <= 0:
        return ()
    tolerance = max(config.cluster_atr * atr_value, 1e-9)
    levels: list[tuple[str, float]] = []
    for label, profile in (("W1", weekly), ("H4", local)):
        if profile is None:
            continue
        levels.extend(((f"{label}_POC", profile.poc),
                       (f"{label}_VAH", profile.vah),
                       (f"{label}_VAL", profile.val)))
    results: list[VPConfluence] = []
    seen: set[tuple[str, ...]] = set()

    def add(components: Sequence[str], raw_prices: Sequence[float],
            structural: Sequence[SRZone], kind: str, base_strength: float):
        unique = tuple(dict.fromkeys(components))
        if len(unique) < 2 or unique in seen:
            return
        seen.add(unique)
        lo = min(raw_prices) - tolerance * 0.5
        hi = max(raw_prices) + tolerance * 0.5
        center = float(np.mean(raw_prices))
        strength = min(1.0, base_strength + 0.10 * len(structural))
        raw = f"{kind}|{'|'.join(unique)}"
        results.append(VPConfluence(
            confluence_id="VPC-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16],
            price=center, zone_low=lo, zone_high=hi, components=unique,
            structural_zone_ids=tuple(z.zone_id for z in structural),
            strength_score=round(strength, 6),
            distance_from_price_atr=round(abs(price - center) / atr_value, 6),
            kind=kind))

    # Two-timeframe joins preserve both labels; no averaged POC is exposed as
    # either source level.
    for left_name, left_price in levels:
        if not left_name.startswith("W1_"):
            continue
        for right_name, right_price in levels:
            if not right_name.startswith("H4_"):
                continue
            if abs(left_price - right_price) > tolerance:
                continue
            nearby_structural = tuple(z for z in zones
                                      if abs(z.price - np.mean((left_price, right_price))) <= tolerance)
            add((left_name, right_name), (left_price, right_price),
                nearby_structural, "MULTI_TF_VP_CONFLUENCE", 0.55)

    # A single timeframe VP level plus structural support/resistance is still
    # useful when the other timeframe has no nearby level.
    for label, level in levels:
        nearby_structural = tuple(z for z in zones if abs(z.price - level) <= tolerance)
        for zone in nearby_structural:
            add((label, f"{zone.timeframe}_STRUCTURAL_{zone.role}"),
                (level, zone.price), (zone,), "VP_SR_CONFLUENCE", 0.45)

    results.sort(key=lambda item: (item.distance_from_price_atr is None,
                                   item.distance_from_price_atr or 0.0,
                                   -item.strength_score))
    return tuple(results)


class ProfileLedger:
    """Small lifecycle ledger for ACTIVE/REFERENCE/RETIRED profile telemetry."""

    STATE_VERSION = 2

    def __init__(self, history_path: Optional[str] = None,
                 state_path: Optional[str] = None):
        self.history_path = Path(history_path) if history_path else None
        self.state_path = Path(state_path) if state_path else None
        self._latest: dict[tuple[str, str], ProfileRecord] = {}
        self._signatures: dict[tuple[str, str], str] = {}
        self._active_by_key: dict[tuple[str, str], ProfileRecord] = {}
        self._active_observed_at: dict[tuple[str, str], Optional[pd.Timestamp]] = {}
        self._load_state()

    @staticmethod
    def _signature(profile: ProfileRecord) -> str:
        return json.dumps(profile.record(), sort_keys=True, separators=(",", ":"))

    def active(self, symbol: str, timeframe: str) -> Optional[ProfileRecord]:
        return self._active_by_key.get((symbol, timeframe))

    def observed_at(self, symbol: str, timeframe: str) -> Optional[pd.Timestamp]:
        return self._active_observed_at.get((symbol, timeframe))

    def update(self, profiles: Sequence[ProfileRecord],
               symbol: Optional[str] = None) -> tuple[str, ...]:
        symbol = str(symbol or (profiles[0].symbol if profiles else "")).upper()
        current_ids = {p.profile_id for p in profiles}
        retired: list[str] = []
        for key, prior in list(self._latest.items()):
            profile_symbol, profile_id = key
            if (profile_symbol == symbol and prior.status != PROFILE_RETIRED
                    and profile_id not in current_ids):
                retired_record = replace(prior, status=PROFILE_RETIRED)
                self._latest[key] = retired_record
                self._write_if_changed(retired_record)
                retired.append(profile_id)
        for profile in profiles:
            key = (str(profile.symbol or symbol).upper(), profile.profile_id)
            self._latest[key] = profile
            self._write_if_changed(profile)
        self._save_state()
        return tuple(retired)

    def remember_active(self, symbol: str, timeframe: str,
                        profile: Optional[ProfileRecord], moment=None) -> None:
        if profile is not None:
            self._active_by_key[(symbol, timeframe)] = profile
            self._active_observed_at[(symbol, timeframe)] = _timestamp(moment)
            self._save_state()

    def _write_if_changed(self, profile: ProfileRecord) -> None:
        signature = self._signature(profile)
        key = (profile.symbol.upper(), profile.profile_id)
        if self._signatures.get(key) == signature:
            return
        self._signatures[key] = signature
        if self.history_path is None:
            return
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(profile.record(), sort_keys=True) + "\n")

    @staticmethod
    def _from_record(row: dict) -> ProfileRecord:
        vp = VolumeProfile(
            poc=float(row["poc"]), vah=float(row["vah"]), val=float(row["val"]),
            profile_high=float(row["profile_high"]),
            profile_low=float(row["profile_low"]), bin_size=float(row["bin_size"]),
            bin_count=int(row["bin_count"]), total_volume=float(row["total_volume"]),
            value_area_volume=float(row["value_area_volume"]),
            bars_used=int(row["bars_used"]),
        )
        names = {field.name for field in ProfileRecord.__dataclass_fields__.values()}
        values = {key: value for key, value in row.items()
                  if key in names and key != "profile"}
        values["high_volume_nodes"] = tuple(values.get("high_volume_nodes", ()))
        values["low_volume_nodes"] = tuple(values.get("low_volume_nodes", ()))
        return ProfileRecord(profile=vp, **values)

    def _state_payload(self) -> dict:
        active = []
        for (symbol, timeframe), profile in sorted(self._active_by_key.items()):
            active.append({"symbol": symbol, "timeframe": timeframe,
                           "observed_at": _iso(self._active_observed_at.get((symbol, timeframe))),
                           "profile": profile.record()})
        return {"version": self.STATE_VERSION, "active": active}

    def _save_state(self) -> None:
        if self.state_path is None:
            return
        payload = self._state_payload()
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        envelope = {"payload": payload,
                    "checksum": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(envelope, sort_keys=True), encoding="utf-8")
        temporary.replace(self.state_path)

    def _load_state(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            envelope = json.loads(self.state_path.read_text(encoding="utf-8"))
            payload = envelope["payload"]
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            if payload.get("version") != self.STATE_VERSION:
                return
            if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != envelope.get("checksum"):
                return
            for item in payload.get("active", []):
                profile = self._from_record(item["profile"])
                key = (str(item["symbol"]).upper(), str(item["timeframe"]))
                self._active_by_key[key] = profile
                self._active_observed_at[key] = _timestamp(item.get("observed_at"))
                self._latest[(key[0], profile.profile_id)] = profile
        except (OSError, ValueError, TypeError, KeyError):
            # Persistence is a hint, never authority.  Closed bars reconstruct
            # the state deterministically on the next snapshot.
            return


class MarketLocationEngine:
    """Build authoritative VP + structural S/R snapshots for a symbol."""

    def __init__(self, config: Optional[MarketLocationConfig] = None,
                 history_path: Optional[str] = None,
                 state_path: Optional[str] = None):
        self.config = config or MarketLocationConfig()
        self.ledger = ProfileLedger(history_path, state_path)

    def snapshot(self, *, symbol: str, current_price: float,
                 df_w1: Optional[pd.DataFrame] = None,
                 df_d1: Optional[pd.DataFrame] = None,
                 df_h4: Optional[pd.DataFrame] = None,
                 df_m15: Optional[pd.DataFrame] = None,
                 df_h1: Optional[pd.DataFrame] = None,
                 df_m5: Optional[pd.DataFrame] = None,
                 as_of=None) -> MarketLocationSnapshot:
        moment = _as_of(as_of)
        w1 = _closed_frame(df_w1, "W1", moment)
        d1 = _closed_frame(df_d1, "D1", moment)
        h4 = _closed_frame(df_h4, "H4", moment)
        # Preserve full causal detail for anchored reconstruction before
        # trimming the M15 execution-context window.
        m15_detail = _closed_frame(df_m15, "M15", moment)
        h1_detail = _closed_frame(df_h1, "H1", moment)
        m5_detail = _closed_frame(df_m5, "M5", moment)
        m15 = m15_detail.copy()
        # Live fetches are bounded.  Applying the same bounds in replay is
        # essential: an ever-growing historical frame would otherwise expose
        # older swings/profiles that live never had in its decision window.
        w1 = w1.tail(self.config.w1_bars).reset_index(drop=True)
        d1 = d1.tail(self.config.d1_bars).reset_index(drop=True)
        h4 = h4.tail(self.config.h4_bars).reset_index(drop=True)
        m15 = m15.tail(self.config.m15_bars).reset_index(drop=True)
        detail_frames = {
            "M5": m5_detail,
            "M15": m15_detail,
            "H1": h1_detail,
        }
        price = float(current_price)
        atr_value = _atr(m15, self.config.atr_period)
        if atr_value <= 0:
            atr_value = _atr(h4, self.config.atr_period)

        candidates: list[_LevelCandidate] = []
        w1_candidates, _ = _structure_candidates(
            w1, symbol, "W1", self.config.sr_swing_lookback_w1, self.config)
        h4_candidates, _ = _structure_candidates(
            h4, symbol, "H4", self.config.sr_swing_lookback_h4, self.config)
        d1_candidates, _ = _structure_candidates(
            d1, symbol, "D1", self.config.sr_swing_lookback_d1, self.config)
        candidates.extend(w1_candidates)
        candidates.extend(h4_candidates)
        candidates.extend(d1_candidates)
        candidates.extend(_daily_reference_candidates(d1, moment))
        candidates.extend(_rejection_candidates(h4, "H4", self.config))
        candidates.extend(_consolidation_candidates(h4, "H4", self.config))
        zones = _make_zones(
            candidates,
            (("M15", m15), ("H4", h4), ("D1", d1), ("W1", w1)),
            price, atr_value, self.config, symbol)

        profiles: list[ProfileRecord] = []
        w1_profiles = _profiles_for_frame(
            w1, symbol, "W1", self.config.sr_swing_lookback_w1, self.config,
            price, detail_frames,
            self.ledger.active(symbol, "W1"),
            self.ledger.observed_at(symbol, "W1"))
        h4_profiles = _profiles_for_frame(
            h4, symbol, "H4", self.config.sr_swing_lookback_h4, self.config,
            price, detail_frames,
            self.ledger.active(symbol, "H4"),
            self.ledger.observed_at(symbol, "H4"))
        m15_profiles = _profiles_for_frame(
            m15, symbol, "M15", self.config.sr_swing_lookback_m15, self.config,
            price, detail_frames,
            self.ledger.active(symbol, "M15"),
            self.ledger.observed_at(symbol, "M15"))
        # The hierarchy is explicit: W1 macro, H4 local, M15 supplementary.
        # M15 can enrich telemetry but is never promoted over either anchor.
        profiles.extend(w1_profiles)
        profiles.extend(h4_profiles)
        profiles.extend(m15_profiles)
        profiles.sort(key=lambda p: (
            0 if p.status == PROFILE_ACTIVE and p.timeframe == "W1" else
            1 if p.status == PROFILE_ACTIVE and p.timeframe == "H4" else
            2 if p.status == PROFILE_ACTIVE else 3,
            {"W1": 0, "H4": 1, "M15": 2}.get(p.timeframe, 3),
            0 if "REFERENCE_PRIMARY_" in p.source else 1,
            -(p.selection_score or 0.0)))
        profiles = profiles[:self.config.max_total_profiles]
        retired_ids = self.ledger.update(profiles, symbol)
        weekly = next((p for p in profiles if p.timeframe == "W1"
                       and p.status == PROFILE_ACTIVE), None)
        local = next((p for p in profiles if p.timeframe == "H4"
                      and p.status == PROFILE_ACTIVE), None)
        weekly_reference = next((p for p in profiles if p.timeframe == "W1"
                                 and p.status == PROFILE_REFERENCE
                                 and "REFERENCE_PRIMARY_" in p.source), None)
        local_reference = next((p for p in profiles if p.timeframe == "H4"
                                and p.status == PROFILE_REFERENCE
                                and "REFERENCE_PRIMARY_" in p.source), None)
        if weekly is not None:
            self.ledger.remember_active(symbol, "W1", weekly, moment)
        if local is not None:
            self.ledger.remember_active(symbol, "H4", local, moment)
        primary = local or weekly or next(
            (p for p in profiles if p.status == PROFILE_ACTIVE), None)
        context_profile = local or weekly or primary
        vp_state = _value_location(price, context_profile, atr_value, self.config)
        value_event = _value_event(m15, context_profile, atr_value, self.config)

        supports = [(z.price, z) for z in zones
                    if z.active and z.role == SUPPORT and z.price <= price]
        resistances = [(z.price, z) for z in zones
                       if z.active and z.role == RESISTANCE and z.price >= price]
        nearest_support_item = _nearest_level(price, supports, below=True)
        nearest_resistance_item = _nearest_level(price, resistances, below=False)
        nearest_support = nearest_support_item[0] if nearest_support_item else None
        nearest_resistance = nearest_resistance_item[0] if nearest_resistance_item else None
        sr_state = UNKNOWN_LOCATION
        containing = [z for z in zones if z.active and z.zone_low <= price <= z.zone_high]
        if containing:
            best = max(containing, key=lambda z: z.strength_score)
            sr_state = ("AT_FLIP_SUPPORT" if best.role == SUPPORT and best.type == FLIP
                        else "AT_FLIP_RESISTANCE" if best.role == RESISTANCE and best.type == FLIP
                        else "AT_SUPPORT" if best.role == SUPPORT else "AT_RESISTANCE")

        authoritative = tuple(p for p in (weekly, local) if p is not None)
        profile_levels = []
        for record in authoritative or tuple(profiles):
            profile_levels.extend(((record.poc, "POC", record),
                                   (record.vah, "VAH", record),
                                   (record.val, "VAL", record)))
        def closest(name: str):
            selected = [(level, record) for level, label, record in profile_levels
                        if label == name]
            return min(selected, key=lambda item: abs(item[0] - price)) if selected else None
        poc_item, vah_item, val_item = (closest("POC"), closest("VAH"), closest("VAL"))

        vp_confluences = _build_vp_confluences(
            weekly, local, zones, price, atr_value, self.config)
        confluence_score = max(
            (c.strength_score for c in vp_confluences
             if c.zone_low <= price <= c.zone_high), default=0.0)
        reasons: list[str] = []
        best_containing = (max(containing, key=lambda z: z.strength_score)
                           if containing else None)
        if containing:
            best = best_containing
            confluence_score = max(confluence_score, best.strength_score)
            reasons.append(f"{best.type} zone {best.zone_id} ({best.source})")
            # Enrich the zone without changing its structural identity: VP
            # coincidence is evidence, not a replacement for the zone.
            nearby_vp = [label for level, label, _ in profile_levels
                          if abs(level - best.price) <= self.config.profile_level_atr * atr_value]
            if nearby_vp:
                confluence_score = min(1.0, confluence_score + 0.20 + 0.05 * (len(nearby_vp) - 1))
                reasons.append("VP " + "+".join(sorted(set(nearby_vp))) + " confluence")
        for confluence in vp_confluences:
            if confluence.zone_low <= price <= confluence.zone_high:
                reasons.append("confluence " + "+".join(confluence.components))
                break
        if vp_state != UNKNOWN_LOCATION:
            reasons.append(f"local {local.timeframe if local else 'W1'} {vp_state}")
        location_type = MID_RANGE_NO_LOCATION
        support_confluence = any(
            c.zone_low <= price <= c.zone_high and any("SUPPORT" in part for part in c.components)
            for c in vp_confluences)
        resistance_confluence = any(
            c.zone_low <= price <= c.zone_high and any("RESISTANCE" in part for part in c.components)
            for c in vp_confluences)
        if not authoritative:
            # Legacy/synthetic callers may provide only an execution frame.
            # Preserve its VP telemetry, but never let M15-only evidence open
            # a location permission; live/replay both supply W1/H4.
            location_type = (UNKNOWN_LOCATION if any(
                p.timeframe == "M15" for p in profiles)
                             else MID_RANGE_NO_LOCATION)
        elif best_containing and best_containing.role == SUPPORT and (support_confluence or any(
                abs(level - best_containing.price) <= self.config.profile_level_atr * atr_value
                for level, _, _ in profile_levels)):
            location_type = VP_SR_SUPPORT_CONFLUENCE
        elif best_containing and best_containing.role == RESISTANCE and (resistance_confluence or any(
                abs(level - best_containing.price) <= self.config.profile_level_atr * atr_value
                for level, _, _ in profile_levels)):
            location_type = VP_SR_RESISTANCE_CONFLUENCE
        elif sr_state == "AT_SUPPORT":
            location_type = AT_MAJOR_SUPPORT
        elif sr_state == "AT_RESISTANCE":
            location_type = AT_MAJOR_RESISTANCE
        elif vp_state != UNKNOWN_LOCATION:
            location_type = vp_state

        def dist(item):
            return _profile_distance(price, item[0], atr_value) if item else None
        return MarketLocationSnapshot(
            symbol=symbol, as_of=_iso(moment), current_price=price,
            atr=float(atr_value or 0.0), location_type=location_type,
            vp_state=vp_state, sr_state=sr_state,
            nearest_support=nearest_support,
            nearest_resistance=nearest_resistance,
            nearest_poc=poc_item[0] if poc_item else None,
            nearest_vah=vah_item[0] if vah_item else None,
            nearest_val=val_item[0] if val_item else None,
            distance_to_support_atr=(abs(price - nearest_support) / atr_value
                                     if nearest_support is not None and atr_value > 0 else None),
            distance_to_resistance_atr=(abs(price - nearest_resistance) / atr_value
                                        if nearest_resistance is not None and atr_value > 0 else None),
            distance_to_poc_atr=dist(poc_item), distance_to_vah_atr=dist(vah_item),
            distance_to_val_atr=dist(val_item),
            vp_sr_confluence_score=round(confluence_score, 6),
            value_area_event=value_event,
            active_profile_id=primary.profile_id if primary else None,
            active_profile_timeframe=primary.timeframe if primary else None,
            active_profile_source=primary.source if primary else None,
            profile_anchor_start=primary.profile_start if primary else None,
            profile_anchor_end=primary.profile_end if primary else None,
            profiles=tuple(profiles), zones=zones,
            retired_profile_ids=retired_ids, reasons=tuple(reasons),
            w1_profile_id=weekly.profile_id if weekly else None,
            h4_profile_id=local.profile_id if local else None,
            w1_anchor_start=weekly.anchor_start_time if weekly else None,
            w1_anchor_end=weekly.anchor_end_time if weekly else None,
            w1_anchor_start_price=weekly.anchor_start_price if weekly else None,
            w1_anchor_end_price=weekly.anchor_end_price if weekly else None,
            w1_profile_high=weekly.profile_high if weekly else None,
            w1_profile_low=weekly.profile_low if weekly else None,
            w1_poc=weekly.poc if weekly else None,
            w1_vah=weekly.vah if weekly else None,
            w1_val=weekly.val if weekly else None,
            h4_anchor_start=local.anchor_start_time if local else None,
            h4_anchor_end=local.anchor_end_time if local else None,
            h4_anchor_start_price=local.anchor_start_price if local else None,
            h4_anchor_end_price=local.anchor_end_price if local else None,
            h4_profile_high=local.profile_high if local else None,
            h4_profile_low=local.profile_low if local else None,
            h4_poc=local.poc if local else None,
            h4_vah=local.vah if local else None,
            h4_val=local.val if local else None,
            distance_to_w1_poc_atr=(_profile_distance(price, weekly.poc, atr_value)
                                    if weekly else None),
            distance_to_w1_vah_atr=(_profile_distance(price, weekly.vah, atr_value)
                                    if weekly else None),
            distance_to_w1_val_atr=(_profile_distance(price, weekly.val, atr_value)
                                    if weekly else None),
            distance_to_h4_poc_atr=(_profile_distance(price, local.poc, atr_value)
                                    if local else None),
            distance_to_h4_vah_atr=(_profile_distance(price, local.vah, atr_value)
                                    if local else None),
            distance_to_h4_val_atr=(_profile_distance(price, local.val, atr_value)
                                    if local else None),
            vp_confluences=vp_confluences,
            top_down_available=bool(authoritative),
            w1_reference_profile_id=(weekly_reference.profile_id
                                     if weekly_reference else None),
            w1_reference_poc=weekly_reference.poc if weekly_reference else None,
            w1_reference_vah=weekly_reference.vah if weekly_reference else None,
            w1_reference_val=weekly_reference.val if weekly_reference else None,
            h4_reference_profile_id=(local_reference.profile_id
                                     if local_reference else None),
            h4_reference_poc=local_reference.poc if local_reference else None,
            h4_reference_vah=local_reference.vah if local_reference else None,
            h4_reference_val=local_reference.val if local_reference else None,
        )


def build_market_location(**kwargs) -> MarketLocationSnapshot:
    """Stateless convenience wrapper for replay/tests."""
    return MarketLocationEngine().snapshot(**kwargs)
