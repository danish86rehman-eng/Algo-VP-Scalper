"""Directional permission and reaction contracts for market-location evidence.

This module consumes a frozen ``MarketLocationSnapshot``.  It deliberately
does not calculate volume profiles, structural zones, or liquidity pools.  It
answers the next question in the causal chain: may a trigger direction use
the location, and has a sweep reacted there strongly enough to continue?
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import timedelta
from typing import Optional

import numpy as np
import pandas as pd

from scalper.anchored_vp import atr
from scalper.market_location import (AT_MAJOR_RESISTANCE, AT_MAJOR_SUPPORT,
                                     AT_POC, AT_VAL, AT_VAH, BELOW_VALUE,
                                     FLIP, MID_RANGE_NO_LOCATION,
                                     RESISTANCE, SUPPORT, UNKNOWN_LOCATION,
                                     VP_SR_RESISTANCE_CONFLUENCE,
                                     VP_SR_SUPPORT_CONFLUENCE,
                                     ABOVE_VALUE)


ALLOW_LONG = "ALLOW_LONG"
ALLOW_SHORT = "ALLOW_SHORT"
CONTEXT_ONLY = "CONTEXT_ONLY"
BLOCK = "BLOCK"

NO_REACTION = "NO_REACTION"
TOUCH = "TOUCH"
RAID = "RAID"
REJECTION = "REJECTION"
RECLAIM = "RECLAIM"
DISPLACEMENT_CONFIRMED = "DISPLACEMENT_CONFIRMED"
ACCEPTANCE = "ACCEPTANCE"

M5_CONFIRMATION_NONE = "NONE"
M5_CONFIRMATION_DISPLACEMENT = "DISPLACEMENT_CONFIRMED"
M5_CONFIRMATION_MSS = "MSS_CONFIRMED"
M5_CONFIRMATION_BOTH = "MSS_AND_DISPLACEMENT"
M5_CONFIRMATION_SWEEP_REJECTION = "SWEEP_REJECTION_CONFIRMED"
MARKET_LOCATION_OFF = "OFF"
MARKET_LOCATION_ACTIVE = "ACTIVE"
AT_POC_ZONE = "AT_POC_ZONE"


def normalize_market_location_mode(value: str) -> str:
    mode = str(value or MARKET_LOCATION_OFF).upper()
    if mode not in {MARKET_LOCATION_OFF, MARKET_LOCATION_ACTIVE}:
        raise ValueError("market-location mode must be OFF or ACTIVE")
    return mode


@dataclass(frozen=True)
class LocationPermissionConfig:
    """Volatility-normalised permission and confirmation thresholds."""

    proximity_atr: float = 0.35
    acceptance_buffer_atr: float = 0.10
    m5_displacement_atr: float = 0.80
    m5_mss_lookback: int = 120
    m5_mss_swing_lookback: int = 2
    acceptance_bars: int = 2
    sweep_expiry_minutes: int = 120
    poc_sweep_direct_entry: bool = True
    # POC is stored as the lower edge of its histogram bin.  A small
    # volatility-normalised pad lets the execution frame touch that bin even
    # when the chart/broker rounds the displayed POC differently, without
    # turning the whole value area into a POC location.
    poc_zone_pad_atr: float = 0.10
    poc_zone_pad_bins: float = 0.50


@dataclass(frozen=True)
class FrozenLocation:
    """The location identity captured when a candidate begins forming."""

    location_id: str
    direction: str
    location_kind: str
    zone_low: float
    zone_high: float
    location_price: float
    timeframe: Optional[str]
    profile_id: Optional[str]
    poc: Optional[float]
    vah: Optional[float]
    val: Optional[float]
    triggered_at: Optional[str]
    w1_profile_id: Optional[str] = None
    w1_anchor_start: Optional[str] = None
    w1_anchor_end: Optional[str] = None
    w1_anchor_start_price: Optional[float] = None
    w1_anchor_end_price: Optional[float] = None
    w1_poc: Optional[float] = None
    w1_vah: Optional[float] = None
    w1_val: Optional[float] = None
    h4_profile_id: Optional[str] = None
    h4_anchor_start: Optional[str] = None
    h4_anchor_end: Optional[str] = None
    h4_anchor_start_price: Optional[float] = None
    h4_anchor_end_price: Optional[float] = None
    h4_poc: Optional[float] = None
    h4_vah: Optional[float] = None
    h4_val: Optional[float] = None
    support_id: Optional[str] = None
    resistance_id: Optional[str] = None
    swept_level: Optional[float] = None

    def record(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class LocationPermission:
    """Normalized location result carried by every executable candidate."""

    permission: str = BLOCK
    market_location_mode: str = MARKET_LOCATION_ACTIVE
    direction: str = "NONE"
    reason: str = "NO_IMPORTANT_LOCATION"
    rejection_code: Optional[str] = None
    reasons: tuple[str, ...] = ()
    location_id: Optional[str] = None
    location_kind: str = UNKNOWN_LOCATION
    location_price: Optional[float] = None
    zone_low: Optional[float] = None
    zone_high: Optional[float] = None
    profile_id: Optional[str] = None
    timeframe: Optional[str] = None
    current_price: Optional[float] = None
    distance_to_location_atr: Optional[float] = None
    distance_to_support_atr: Optional[float] = None
    distance_to_resistance_atr: Optional[float] = None
    distance_to_poc_atr: Optional[float] = None
    distance_to_vah_atr: Optional[float] = None
    distance_to_val_atr: Optional[float] = None
    vp_state: str = UNKNOWN_LOCATION
    sr_state: str = UNKNOWN_LOCATION
    vp_sr_confluence_score: float = 0.0
    active_profile_id: Optional[str] = None
    poc: Optional[float] = None
    vah: Optional[float] = None
    val: Optional[float] = None
    w1_profile_id: Optional[str] = None
    w1_anchor_start: Optional[str] = None
    w1_anchor_end: Optional[str] = None
    w1_anchor_start_price: Optional[float] = None
    w1_anchor_end_price: Optional[float] = None
    w1_poc: Optional[float] = None
    w1_vah: Optional[float] = None
    w1_val: Optional[float] = None
    h4_profile_id: Optional[str] = None
    h4_anchor_start: Optional[str] = None
    h4_anchor_end: Optional[str] = None
    h4_anchor_start_price: Optional[float] = None
    h4_anchor_end_price: Optional[float] = None
    h4_poc: Optional[float] = None
    h4_vah: Optional[float] = None
    h4_val: Optional[float] = None
    distance_to_w1_poc_atr: Optional[float] = None
    distance_to_w1_vah_atr: Optional[float] = None
    distance_to_w1_val_atr: Optional[float] = None
    distance_to_h4_poc_atr: Optional[float] = None
    distance_to_h4_vah_atr: Optional[float] = None
    distance_to_h4_val_atr: Optional[float] = None
    swept_level: Optional[float] = None
    sweep_type: Optional[str] = None
    reaction_state: str = NO_REACTION
    confirmation_state: str = M5_CONFIRMATION_NONE
    m5_confirmation_state: str = M5_CONFIRMATION_NONE
    mss_level: Optional[float] = None
    mss_at: Optional[str] = None
    displacement_atr: Optional[float] = None
    triggered_at: Optional[str] = None
    frozen: Optional[FrozenLocation] = None

    @property
    def executable(self) -> bool:
        return self.permission in {ALLOW_LONG, ALLOW_SHORT}

    @property
    def allowed(self) -> bool:
        return self.executable

    def record(self) -> dict:
        value = asdict(self)
        value["frozen"] = self.frozen.record() if self.frozen else None
        value["final_permission"] = self.permission
        return value


def _distance_to_band(price: float, low: float, high: float, atr_value: float):
    distance = 0.0 if low <= price <= high else min(abs(price - low),
                                                    abs(price - high))
    return distance, distance / atr_value if atr_value > 0 else None


def _snapshot_values(snapshot):
    return {
        "current_price": float(snapshot.current_price),
        "distance_to_support_atr": snapshot.distance_to_support_atr,
        "distance_to_resistance_atr": snapshot.distance_to_resistance_atr,
        "distance_to_poc_atr": snapshot.distance_to_poc_atr,
        "distance_to_vah_atr": snapshot.distance_to_vah_atr,
        "distance_to_val_atr": snapshot.distance_to_val_atr,
        "vp_state": snapshot.vp_state,
        "sr_state": snapshot.sr_state,
        "vp_sr_confluence_score": float(snapshot.vp_sr_confluence_score),
        "active_profile_id": snapshot.active_profile_id,
        "poc": snapshot.nearest_poc,
        "vah": snapshot.nearest_vah,
        "val": snapshot.nearest_val,
        "w1_profile_id": getattr(snapshot, "w1_profile_id", None),
        "w1_anchor_start": getattr(snapshot, "w1_anchor_start", None),
        "w1_anchor_end": getattr(snapshot, "w1_anchor_end", None),
        "w1_anchor_start_price": getattr(snapshot, "w1_anchor_start_price", None),
        "w1_anchor_end_price": getattr(snapshot, "w1_anchor_end_price", None),
        "w1_poc": getattr(snapshot, "w1_poc", None),
        "w1_vah": getattr(snapshot, "w1_vah", None),
        "w1_val": getattr(snapshot, "w1_val", None),
        "h4_profile_id": getattr(snapshot, "h4_profile_id", None),
        "h4_anchor_start": getattr(snapshot, "h4_anchor_start", None),
        "h4_anchor_end": getattr(snapshot, "h4_anchor_end", None),
        "h4_anchor_start_price": getattr(snapshot, "h4_anchor_start_price", None),
        "h4_anchor_end_price": getattr(snapshot, "h4_anchor_end_price", None),
        "h4_poc": getattr(snapshot, "h4_poc", None),
        "h4_vah": getattr(snapshot, "h4_vah", None),
        "h4_val": getattr(snapshot, "h4_val", None),
        "distance_to_w1_poc_atr": getattr(snapshot, "distance_to_w1_poc_atr", None),
        "distance_to_w1_vah_atr": getattr(snapshot, "distance_to_w1_vah_atr", None),
        "distance_to_w1_val_atr": getattr(snapshot, "distance_to_w1_val_atr", None),
        "distance_to_h4_poc_atr": getattr(snapshot, "distance_to_h4_poc_atr", None),
        "distance_to_h4_vah_atr": getattr(snapshot, "distance_to_h4_vah_atr", None),
        "distance_to_h4_val_atr": getattr(snapshot, "distance_to_h4_val_atr", None),
        "triggered_at": snapshot.as_of,
    }


def _zone_candidate(snapshot, direction: str, config: LocationPermissionConfig):
    role = SUPPORT if direction == "BULLISH" else RESISTANCE
    atr_value = float(snapshot.atr or 0.0)
    candidates = []
    for zone in snapshot.zones:
        if not getattr(zone, "active", False) or getattr(zone, "role", None) != role:
            continue
        distance, distance_atr = _distance_to_band(
            float(snapshot.current_price), float(zone.zone_low),
            float(zone.zone_high), atr_value)
        if distance_atr is not None and distance_atr <= config.proximity_atr:
            candidates.append((0 if distance == 0 else 1,
                               distance_atr, -float(zone.strength_score), zone))
    if not candidates:
        # Keep the shared engine authoritative even when a test/adapter only
        # supplies the flattened nearest level and not its zone list.
        level = (snapshot.nearest_support if role == SUPPORT
                 else snapshot.nearest_resistance)
        distance_atr = (snapshot.distance_to_support_atr if role == SUPPORT
                        else snapshot.distance_to_resistance_atr)
        if level is not None and distance_atr is not None and distance_atr <= config.proximity_atr:
            half = atr_value * config.proximity_atr
            return {
                "location_id": f"{snapshot.active_profile_id or 'SR'}:{role}",
                "kind": AT_MAJOR_SUPPORT if role == SUPPORT else AT_MAJOR_RESISTANCE,
                "price": float(level), "low": float(level) - half,
                "high": float(level) + half, "distance_atr": float(distance_atr),
                "timeframe": None, "profile_id": snapshot.active_profile_id,
            }
        return None
    zone = sorted(candidates)[0][-1]
    return {
        "location_id": zone.zone_id, "kind": zone.type,
        "price": float(zone.price), "low": float(zone.zone_low),
        "high": float(zone.zone_high), "distance_atr": float(sorted(candidates)[0][1]),
        "timeframe": zone.timeframe, "profile_id": snapshot.active_profile_id,
    }


def _vp_candidate(snapshot, direction: str, config: LocationPermissionConfig):
    # M15-only compatibility snapshots may still expose VP telemetry, but
    # they are not an admissible top-down location for a live/replay entry.
    if not getattr(snapshot, "top_down_available", True):
        return None
    level_name = "VAL" if direction == "BULLISH" else "VAH"
    level = snapshot.nearest_val if direction == "BULLISH" else snapshot.nearest_vah
    distance_atr = (snapshot.distance_to_val_atr if direction == "BULLISH"
                    else snapshot.distance_to_vah_atr)
    state = snapshot.vp_state
    event = getattr(snapshot, "value_area_event", "")
    edge_state = AT_VAL if direction == "BULLISH" else AT_VAH
    edge_event = "VAL_REJECTION" if direction == "BULLISH" else "VAH_REJECTION"
    if level is None or distance_atr is None or distance_atr > config.proximity_atr:
        return None
    # Proximity is necessary but not sufficient: a naked VAL/VAH touch is
    # context only.  A value edge becomes directional after its causal
    # rejection/reclaim event; structural S/R may independently qualify.
    if event != edge_event:
        return None
    return {
        "location_id": f"{snapshot.active_profile_id or 'VP'}:{level_name}",
        "kind": edge_state, "price": float(level),
        "low": float(level) - float(snapshot.atr) * config.proximity_atr,
        "high": float(level) + float(snapshot.atr) * config.proximity_atr,
        "distance_atr": float(distance_atr), "timeframe": snapshot.active_profile_timeframe,
        "profile_id": snapshot.active_profile_id,
    }


def _frame_touches_band(frame, low: float, high: float, *, after=None) -> bool:
    """Return whether a completed confirmation bar overlaps a price band."""
    if frame is None or len(frame) == 0 or not {"high", "low"}.issubset(frame.columns):
        return False
    work = frame
    if after is not None and "time" in work.columns:
        try:
            times = pd.to_datetime(work["time"], utc=True, errors="coerce")
            cutoff = pd.Timestamp(after)
            cutoff = (cutoff.tz_localize("UTC") if cutoff.tzinfo is None
                      else cutoff.tz_convert("UTC"))
            work = work.loc[times >= cutoff]
        except (TypeError, ValueError):
            return False
    if len(work) == 0:
        return False
    highs = pd.to_numeric(work["high"], errors="coerce").to_numpy()
    lows = pd.to_numeric(work["low"], errors="coerce").to_numpy()
    return bool(np.any((lows <= float(high)) & (highs >= float(low))))


def _poc_profile(snapshot, profile_id: Optional[str], timeframe: str):
    """Find the active top-down profile record for POC-bin geometry."""
    for profile in getattr(snapshot, "profiles", ()):
        if (getattr(profile, "profile_id", None) == profile_id
                and getattr(profile, "timeframe", None) == timeframe
                and getattr(profile, "status", None) == "ACTIVE"):
            return profile
    return None


def _poc_candidate(snapshot, config: LocationPermissionConfig, *,
                   swept_level: Optional[float] = None,
                   confirmation_frame=None, sweep_time=None):
    """Return an active W1/H4 POC *bin* touched by the setup.

    ``snapshot.vp_state`` describes the current close against the H4 primary
    profile.  It is not sufficient to decide whether an independent W1 POC
    was touched, and it loses a wick after the candle closes away.  Match the
    causal sweep level or completed M5 range against each authoritative
    profile's own histogram bin instead.
    """
    if not getattr(snapshot, "top_down_available", True):
        return None

    atr_value = float(getattr(snapshot, "atr", 0.0) or 0.0)
    if atr_value <= 0:
        return None
    current_price = float(snapshot.current_price)
    options = []
    authoritative = (
        ("W1", getattr(snapshot, "w1_poc", None),
         getattr(snapshot, "w1_profile_id", None)),
        ("H4", getattr(snapshot, "h4_poc", None),
         getattr(snapshot, "h4_profile_id", None)),
    )
    for timeframe, level, profile_id in authoritative:
        if level is None:
            continue
        level = float(level)
        profile = _poc_profile(snapshot, profile_id, timeframe)
        raw_bin = getattr(getattr(profile, "profile", None), "bin_size", 0.0)
        bin_size = float(raw_bin or 0.0)
        # Synthetic/legacy adapters may expose a POC without profile rows.
        # Keep their point semantics while live/replay top-down snapshots use
        # the real histogram-bin geometry.
        pad = min(atr_value * float(config.poc_zone_pad_atr),
                  bin_size * float(config.poc_zone_pad_bins)) if bin_size > 0 else (
                      atr_value * float(config.poc_zone_pad_atr))
        zone_low = level - pad
        zone_high = level + (bin_size if bin_size > 0 else 0.0) + pad
        _, current_distance = _distance_to_band(
            current_price, zone_low, zone_high, atr_value)
        swept_distance = None
        if swept_level is not None:
            _, swept_distance = _distance_to_band(
                float(swept_level), zone_low, zone_high, atr_value)
        contact = _frame_touches_band(
            confirmation_frame, zone_low, zone_high, after=sweep_time)
        swept_contact = swept_distance is not None and swept_distance <= config.proximity_atr
        current_contact = current_distance is not None and current_distance <= config.proximity_atr
        if not (contact or swept_contact or current_contact):
            continue
        options.append({
            "location_id": f"{profile_id or timeframe}:POC",
            "kind": AT_POC,
            "price": level,
            "low": zone_low,
            "high": zone_high,
            "distance_atr": current_distance,
            "timeframe": timeframe,
            "profile_id": profile_id,
            "poc_contact": bool(contact or swept_contact),
            "contact_priority": 0 if contact else (1 if swept_contact else 2),
        })

    # Compatibility fallback for unit/test adapters that expose only the
    # flattened nearest POC.  Live/replay always supplies W1/H4 profiles.
    if not options and snapshot.nearest_poc is not None:
        level = float(snapshot.nearest_poc)
        half = atr_value * float(config.poc_zone_pad_atr)
        _, distance_atr = _distance_to_band(
            current_price, level - half, level + half, atr_value)
        swept_distance = None
        if swept_level is not None:
            _, swept_distance = _distance_to_band(
                float(swept_level), level - half, level + half, atr_value)
        contact = _frame_touches_band(
            confirmation_frame, level - half, level + half, after=sweep_time)
        swept_contact = swept_distance is not None and swept_distance <= config.proximity_atr
        current_contact = distance_atr is not None and distance_atr <= config.proximity_atr
        if contact or swept_contact or current_contact:
            options.append({
                "location_id": f"{snapshot.active_profile_id or 'VP'}:POC",
                "kind": AT_POC, "price": level,
                "low": level - half, "high": level + half,
                "distance_atr": distance_atr,
                "timeframe": snapshot.active_profile_timeframe,
                "profile_id": snapshot.active_profile_id,
                "poc_contact": bool(contact or swept_contact),
                "contact_priority": 0 if contact else (1 if swept_contact else 2),
            })
    if not options:
        return None
    return min(options, key=lambda item: (
        item["contact_priority"],
        item["distance_atr"] if item["distance_atr"] is not None else float("inf"),
        0 if item["timeframe"] == "H4" else 1,
    ))


def _continuation_candidate(snapshot, direction: str,
                            config: LocationPermissionConfig):
    if direction == "BULLISH" and snapshot.vp_state == ABOVE_VALUE:
        return {
            "location_id": f"{snapshot.active_profile_id or 'VP'}:ABOVE_VALUE",
            "kind": ABOVE_VALUE, "price": snapshot.nearest_vah,
            "low": snapshot.nearest_vah, "high": snapshot.current_price,
            "distance_atr": snapshot.distance_to_vah_atr,
            "timeframe": snapshot.active_profile_timeframe,
            "profile_id": snapshot.active_profile_id,
        }
    if direction == "BEARISH" and snapshot.vp_state == BELOW_VALUE:
        return {
            "location_id": f"{snapshot.active_profile_id or 'VP'}:BELOW_VALUE",
            "kind": BELOW_VALUE, "price": snapshot.nearest_val,
            "low": snapshot.current_price, "high": snapshot.nearest_val,
            "distance_atr": snapshot.distance_to_val_atr,
            "timeframe": snapshot.active_profile_timeframe,
            "profile_id": snapshot.active_profile_id,
        }
    return None


def _freeze(snapshot, candidate, direction: str, values: dict,
            swept_level: Optional[float] = None):
    frozen = FrozenLocation(
        location_id=str(candidate["location_id"]), direction=direction,
        location_kind=str(candidate["kind"]), zone_low=float(candidate["low"]),
        zone_high=float(candidate["high"]), location_price=float(candidate["price"]),
        timeframe=candidate.get("timeframe"), profile_id=candidate.get("profile_id"),
        poc=values["poc"], vah=values["vah"], val=values["val"],
        triggered_at=values["triggered_at"],
        w1_profile_id=values["w1_profile_id"],
        w1_anchor_start=values["w1_anchor_start"],
        w1_anchor_end=values["w1_anchor_end"],
        w1_anchor_start_price=values["w1_anchor_start_price"],
        w1_anchor_end_price=values["w1_anchor_end_price"],
        w1_poc=values["w1_poc"], w1_vah=values["w1_vah"],
        w1_val=values["w1_val"], h4_profile_id=values["h4_profile_id"],
        h4_anchor_start=values["h4_anchor_start"],
        h4_anchor_end=values["h4_anchor_end"],
        h4_anchor_start_price=values["h4_anchor_start_price"],
        h4_anchor_end_price=values["h4_anchor_end_price"],
        h4_poc=values["h4_poc"], h4_vah=values["h4_vah"],
        h4_val=values["h4_val"],
        support_id=(str(candidate["location_id"]) if direction == "BULLISH" else None),
        resistance_id=(str(candidate["location_id"]) if direction == "BEARISH" else None),
        swept_level=swept_level,
    )
    return frozen


def _frozen_location_is_current(snapshot, frozen: FrozenLocation) -> bool:
    """Return whether the exact frozen location still exists in the snapshot."""
    location_id = str(frozen.location_id)
    active_zone_ids = {
        str(zone.zone_id) for zone in getattr(snapshot, "zones", ())
        if getattr(zone, "active", False)
    }
    if location_id in active_zone_ids:
        return True

    # Flattened snapshot adapters do not always carry structural zones.  Their
    # synthetic IDs are still exact identities, so validate them against the
    # same current profile and level rather than accepting a nearby substitute.
    profile_id = getattr(snapshot, "active_profile_id", None)
    flattened = {
        f"{profile_id or 'SR'}:{SUPPORT}": getattr(snapshot, "nearest_support", None),
        f"{profile_id or 'SR'}:{RESISTANCE}": getattr(snapshot, "nearest_resistance", None),
        f"{profile_id or 'VP'}:POC": getattr(snapshot, "nearest_poc", None),
        f"{profile_id or 'VP'}:VAL": getattr(snapshot, "nearest_val", None),
        f"{profile_id or 'VP'}:VAH": getattr(snapshot, "nearest_vah", None),
        f"{getattr(snapshot, 'w1_profile_id', None) or 'W1'}:POC": getattr(snapshot, "w1_poc", None),
        f"{getattr(snapshot, 'h4_profile_id', None) or 'H4'}:POC": getattr(snapshot, "h4_poc", None),
    }
    return location_id in flattened and flattened[location_id] is not None


def build_location_permission(snapshot, *, direction: str, trigger_type: str,
                              swept_level: Optional[float] = None,
                              triggered_at=None,
                              frozen_location: Optional[FrozenLocation] = None,
                              confirmation_frame=None,
                              sweep_time=None,
                              config: Optional[LocationPermissionConfig] = None) -> LocationPermission:
    """Return one directional permission from the authoritative snapshot."""
    config = config or LocationPermissionConfig()
    direction = str(direction or "NONE").upper()
    values = _snapshot_values(snapshot) if snapshot is not None else {}
    if triggered_at is not None:
        values["triggered_at"] = str(triggered_at)
    if direction not in {"BULLISH", "BEARISH"} or snapshot is None:
        return LocationPermission(reason="NO_IMPORTANT_LOCATION", reasons=(
            "NO_IMPORTANT_LOCATION",), direction=direction,
            rejection_code=("SWEEP_REJECTION_BLOCKED_NO_LOCATION"
                            if trigger_type == "SWEEP_REJECTION" else
                            "NO_IMPORTANT_LOCATION"),
            swept_level=swept_level,
            sweep_type="SSL" if direction == "BULLISH" else "BSL"
            if direction == "BEARISH" else None, **values)

    role = SUPPORT if direction == "BULLISH" else RESISTANCE
    sr_state = str(snapshot.sr_state or "")
    opposite_sr = ("AT_RESISTANCE", "AT_FLIP_RESISTANCE") if role == SUPPORT else (
        "AT_SUPPORT", "AT_FLIP_SUPPORT")
    structural = _zone_candidate(snapshot, direction, config)
    vp_edge = _vp_candidate(snapshot, direction, config)
    poc_direct = (_poc_candidate(
                      snapshot, config, swept_level=swept_level,
                      confirmation_frame=confirmation_frame,
                      sweep_time=sweep_time)
                  if (trigger_type == "SWEEP_REJECTION"
                      and config.poc_sweep_direct_entry) else None)
    opposite = _zone_candidate(snapshot,
                               "BEARISH" if direction == "BULLISH" else "BULLISH",
                               config)
    confluence = (snapshot.location_type in {
        VP_SR_SUPPORT_CONFLUENCE, VP_SR_RESISTANCE_CONFLUENCE
    } and ((direction == "BULLISH" and snapshot.location_type == VP_SR_SUPPORT_CONFLUENCE)
           or (direction == "BEARISH" and snapshot.location_type == VP_SR_RESISTANCE_CONFLUENCE)))
    reasons = []
    candidate = poc_direct or structural or vp_edge
    if poc_direct:
        reasons.append("POC_SWEEP_DIRECT")
        if poc_direct.get("poc_contact"):
            reasons.append("POC_BIN_CONTACT")
    if structural:
        reasons.append(f"{snapshot.active_profile_timeframe or 'SR'}_{role}")
        if structural["kind"] == FLIP:
            reasons.append(f"FLIP_{role}")
    if vp_edge:
        reasons.append(vp_edge["kind"])
    elif ((direction == "BULLISH" and snapshot.vp_state == AT_VAL
           and snapshot.distance_to_val_atr is not None
           and snapshot.distance_to_val_atr <= config.proximity_atr)
          or (direction == "BEARISH" and snapshot.vp_state == AT_VAH
              and snapshot.distance_to_vah_atr is not None
              and snapshot.distance_to_vah_atr <= config.proximity_atr)):
        # Preserve the edge as confluence telemetry even when it has not, by
        # itself, earned directional permission through a rejection event.
        reasons.append(AT_VAL if direction == "BULLISH" else AT_VAH)
    if confluence:
        reasons.append("VP_SR_CONFLUENCE")

    value_event = str(getattr(snapshot, "value_area_event", ""))
    blocked_by_acceptance = ((direction == "BULLISH" and value_event == "VAL_ACCEPTANCE")
                             or (direction == "BEARISH" and value_event == "VAH_ACCEPTANCE"))
    if blocked_by_acceptance:
        acceptance_reason = ("VAL_ACCEPTANCE_BLOCK_LONG" if direction == "BULLISH"
                             else "VAH_ACCEPTANCE_BLOCK_SHORT")
        return LocationPermission(
            permission=BLOCK, direction=direction, reason=acceptance_reason,
            rejection_code=acceptance_reason,
            reasons=(acceptance_reason, value_event), swept_level=swept_level,
            sweep_type="SSL" if direction == "BULLISH" else "BSL", **values)

    if frozen_location is not None:
        active_ids = {getattr(snapshot, "active_profile_id", None),
                      getattr(snapshot, "w1_profile_id", None),
                      getattr(snapshot, "h4_profile_id", None)}
        if (frozen_location.profile_id is not None
                and frozen_location.profile_id not in active_ids):
            return LocationPermission(
                permission=BLOCK, direction=direction,
                reason="LOCATION_INVALIDATED",
                rejection_code="LOCATION_INVALIDATED",
                reasons=tuple(reasons + ["PROFILE_REPLACED"]),
                swept_level=swept_level,
                sweep_type="SSL" if direction == "BULLISH" else "BSL",
                frozen=frozen_location, **values)
        if not _frozen_location_is_current(snapshot, frozen_location):
            return LocationPermission(
                permission=BLOCK, direction=direction,
                reason="LOCATION_INVALIDATED",
                rejection_code="LOCATION_INVALIDATED",
                reasons=tuple(reasons + ["FROZEN_LOCATION_REPLACED"]),
                swept_level=swept_level,
                sweep_type="SSL" if direction == "BULLISH" else "BSL",
                frozen=frozen_location, **values)
        values["triggered_at"] = frozen_location.triggered_at
        candidate = {
            "location_id": frozen_location.location_id,
            "kind": frozen_location.location_kind,
            "price": frozen_location.location_price,
            "low": frozen_location.zone_low, "high": frozen_location.zone_high,
            "distance_atr": _distance_to_band(float(snapshot.current_price),
                                                frozen_location.zone_low,
                                                frozen_location.zone_high,
                                                float(snapshot.atr))[1],
            "timeframe": frozen_location.timeframe,
            "profile_id": frozen_location.profile_id,
            "poc_contact": frozen_location.location_kind in {AT_POC, AT_POC_ZONE},
        }
        reasons.append("FROZEN_LOCATION")

    # POC remains context-only unless the operator-enabled SWEEP_REJECTION
    # path is active.  That path still requires the detector's completed M5
    # sweep/rejection candle; it only removes the extra MSS/displacement wait.
    # A frozen POC is deliberately allowed to survive a later close away from
    # the bin; profile replacement and expiry are still checked below.
    poc_only = (snapshot.vp_state == AT_POC and not structural and not vp_edge
                and not confluence and not poc_direct and frozen_location is None)
    if poc_only:
        return LocationPermission(
            permission=CONTEXT_ONLY, direction=direction,
            reason="POC_ONLY_NO_STRUCTURE", reasons=("POC_ONLY_NO_STRUCTURE",),
            swept_level=swept_level,
            sweep_type="SSL" if direction == "BULLISH" else "BSL", **values)

    # Continuation triggers may use a directional move outside value, but a
    # reversal trigger must be at a support/resistance or value edge.
    continuation = trigger_type in {"BOS_RETEST", "FVG_FILL",
                                    "S01_REFERENCE_CANDLE_RAID_VP"}
    if not candidate and continuation:
        candidate = _continuation_candidate(snapshot, direction, config)
        if candidate:
            reasons.append(candidate["kind"])

    if candidate is None:
        if opposite is not None or sr_state in opposite_sr:
            reason = "WRONG_LOCATION_DIRECTION"
        elif snapshot.location_type in {MID_RANGE_NO_LOCATION, UNKNOWN_LOCATION} or snapshot.vp_state in {
                "INSIDE_VALUE", "ABOVE_VALUE", "BELOW_VALUE"}:
            reason = "MID_RANGE" if snapshot.location_type == MID_RANGE_NO_LOCATION else "NO_IMPORTANT_LOCATION"
        else:
            reason = "NO_IMPORTANT_LOCATION"
        return LocationPermission(permission=BLOCK, direction=direction,
                                  reason=reason,
                                  rejection_code=("SWEEP_REJECTION_BLOCKED_NO_LOCATION"
                                                  if trigger_type == "SWEEP_REJECTION"
                                                  and reason in {"NO_IMPORTANT_LOCATION", "MID_RANGE"}
                                                  else reason),
                                  reasons=tuple(reasons or (reason,)),
                                  swept_level=swept_level,
                                  sweep_type="SSL" if direction == "BULLISH" else "BSL",
                                  **values)

    # VALUE_AREA_FADE is deliberately edge-specific.  A structural support
    # alone cannot turn it into a VAL fade, and vice versa.
    if trigger_type == "VALUE_AREA_FADE" and candidate["kind"] not in {
            AT_VAL if direction == "BULLISH" else AT_VAH,
            VP_SR_SUPPORT_CONFLUENCE if direction == "BULLISH" else VP_SR_RESISTANCE_CONFLUENCE}:
        return LocationPermission(permission=BLOCK, direction=direction,
                                  reason="WRONG_LOCATION_DIRECTION",
                                  reasons=tuple(reasons or ("WRONG_LOCATION_DIRECTION",)),
                                  swept_level=swept_level,
                                  sweep_type="SSL" if direction == "BULLISH" else "BSL",
                                  **values)

    # For a sweep, the swept liquidity must be at/through the frozen location,
    # not merely followed by a later close somewhere else.
    distance_atr = candidate["distance_atr"]
    if swept_level is not None:
        distance_atr = abs(float(swept_level) - float(candidate["price"])) / max(float(snapshot.atr), 1e-12)
        if (distance_atr > config.proximity_atr
                and not (candidate["kind"] in {AT_POC, AT_POC_ZONE}
                         and candidate.get("poc_contact", False))):
            return LocationPermission(
                permission=BLOCK, direction=direction,
                reason="TOO_FAR_FROM_LOCATION",
                rejection_code="TOO_FAR_FROM_LOCATION",
                reasons=tuple(reasons + ["TOO_FAR_FROM_LOCATION"]),
                location_id=candidate["location_id"], location_kind=candidate["kind"],
                location_price=candidate["price"], zone_low=candidate["low"],
                zone_high=candidate["high"], profile_id=candidate.get("profile_id"),
                timeframe=candidate.get("timeframe"), distance_to_location_atr=distance_atr,
                swept_level=swept_level,
                sweep_type="SSL" if direction == "BULLISH" else "BSL", **values)

    permission = ALLOW_LONG if direction == "BULLISH" else ALLOW_SHORT
    frozen = frozen_location or _freeze(snapshot, candidate, direction, values,
                                         swept_level)
    permission_reason = ("POC_SWEEP_REJECTION_PENDING"
                         if candidate["kind"] == AT_POC
                         else "LOCATION_PERMISSION_GRANTED")
    return LocationPermission(
        permission=permission, direction=direction, reason=permission_reason,
        reasons=tuple(dict.fromkeys(reasons)), location_id=candidate["location_id"],
        location_kind=candidate["kind"], location_price=candidate["price"],
        zone_low=candidate["low"], zone_high=candidate["high"],
        profile_id=candidate.get("profile_id"), timeframe=candidate.get("timeframe"),
        distance_to_location_atr=distance_atr, swept_level=swept_level,
        sweep_type="SSL" if direction == "BULLISH" else "BSL", frozen=frozen,
        **values)


def _frame_after(frame, when):
    if frame is None or len(frame) == 0:
        return None
    work = frame.copy().reset_index(drop=True)
    if "time" not in work.columns or when is None:
        return work
    times = pd.to_datetime(work["time"], utc=True, errors="coerce")
    cutoff = pd.Timestamp(when)
    cutoff = (cutoff.tz_localize("UTC") if cutoff.tzinfo is None
              else cutoff.tz_convert("UTC"))
    return work.loc[times >= cutoff].reset_index(drop=True)


def evaluate_sweep_reaction(permission: LocationPermission, df_confirm,
                            *, sweep_time=None, config=None):
    """Require M5 reclaim; non-POC paths still require displacement/MSS."""
    config = config or LocationPermissionConfig()
    if not permission.executable:
        return permission
    if permission.triggered_at is not None and sweep_time is not None:
        try:
            started = pd.Timestamp(permission.triggered_at)
            current = pd.Timestamp(df_confirm["time"].iloc[-1])
            if current - started > timedelta(minutes=config.sweep_expiry_minutes):
                return replace(permission, permission=BLOCK, reason="SETUP_EXPIRED",
                               rejection_code="SETUP_EXPIRED",
                               reasons=permission.reasons + ("SETUP_EXPIRED",))
        except (TypeError, ValueError, KeyError):
            pass
    direction = permission.direction
    bearish = direction == "BEARISH"
    full = _frame_after(df_confirm, None)
    work = _frame_after(full, sweep_time)
    if full is None or work is None or len(work) < 3:
        return replace(permission, permission=BLOCK, reason="NO_REACTION",
                       reasons=permission.reasons + ("NO_REACTION",))
    atr_value = float(atr(full, 14) or 0.0)
    if atr_value <= 0:
        return replace(permission, permission=BLOCK, reason="NO_DISPLACEMENT",
                       reasons=permission.reasons + ("NO_DISPLACEMENT",))
    swept = float(permission.swept_level)
    level = float(permission.location_price)
    lows = work["low"].astype(float).to_numpy()
    highs = work["high"].astype(float).to_numpy()
    opens = work["open"].astype(float).to_numpy()
    closes = work["close"].astype(float).to_numpy()
    raid_seen = bool((highs >= swept).any() if bearish else (lows <= swept).any())
    if not raid_seen:
        return replace(permission, permission=BLOCK, reason="NO_REACTION",
                       reaction_state=NO_REACTION,
                       reasons=permission.reasons + ("NO_REACTION",))

    acceptance_buffer = config.acceptance_buffer_atr * atr_value
    recent = closes[-config.acceptance_bars:]
    if len(recent) >= config.acceptance_bars and (
            bool(np.all(recent > float(permission.zone_high) + acceptance_buffer))
            if bearish else
            bool(np.all(recent < float(permission.zone_low) - acceptance_buffer))):
        reason = ("VAH_ACCEPTANCE_BLOCK_SHORT" if bearish
                  else "VAL_ACCEPTANCE_BLOCK_LONG")
        return replace(permission, permission=BLOCK, reason=reason,
                       rejection_code=reason,
                       reaction_state=ACCEPTANCE,
                       reasons=permission.reasons + (reason,))

    directional = ((closes > opens) & (closes >= level) if not bearish else
                   (closes < opens) & (closes <= level))
    opposite_displacement = (((closes[-1] - opens[-1]) >=
                              config.m5_displacement_atr * atr_value) if bearish
                             else ((opens[-1] - closes[-1]) >=
                                   config.m5_displacement_atr * atr_value))
    if opposite_displacement:
        return replace(permission, permission=BLOCK,
                       reason="LOCATION_INVALIDATED",
                       rejection_code="LOCATION_INVALIDATED",
                       reaction_state=ACCEPTANCE,
                       reasons=permission.reasons + ("OPPOSITE_DISPLACEMENT",))
    if not bool(directional[-1]):
        return replace(permission, permission=BLOCK, reason="NO_RECLAIM",
                       reaction_state=RAID,
                       reasons=permission.reasons + ("NO_RECLAIM",))

    reaction_state = RECLAIM
    if (config.poc_sweep_direct_entry
            and permission.location_kind == AT_POC):
        confirmation = M5_CONFIRMATION_SWEEP_REJECTION
        return replace(
            permission, reason="POC_SWEEP_REJECTION_CONFIRMED",
            reaction_state=REJECTION,
            confirmation_state=confirmation,
            m5_confirmation_state=confirmation,
            reasons=permission.reasons + ("RECLAIM", confirmation),
        )
    body = np.abs(closes - opens)
    displacement = directional & (body >= config.m5_displacement_atr * atr_value)
    displacement_idx = np.flatnonzero(displacement)
    displacement_atr = (float(body[displacement_idx[-1]] / atr_value)
                        if len(displacement_idx) else None)

    mss_level = None
    mss_at = None
    try:
        # This is the repository's existing M5 MSS implementation, used by
        # VP_LIQUIDITY_REACTION; this layer does not create a second detector.
        from scalper.vp_liquidity_trigger import _find_mss
        mss_level, mss_at = _find_mss(
            full, sweep_time, bearish, config.m5_mss_lookback,
            config.m5_mss_swing_lookback)
    except Exception:
        mss_level, mss_at = None, None

    has_mss = mss_level is not None
    has_displacement = len(displacement_idx) > 0
    if not has_mss and not has_displacement:
        return replace(permission, permission=BLOCK, reason="NO_M5_CONFIRMATION",
                       rejection_code="NO_M5_CONFIRMATION",
                       reaction_state=reaction_state,
                       reasons=permission.reasons + ("NO_M5_CONFIRMATION",))
    if has_mss and has_displacement:
        confirmation = M5_CONFIRMATION_BOTH
    elif has_mss:
        confirmation = M5_CONFIRMATION_MSS
    else:
        confirmation = M5_CONFIRMATION_DISPLACEMENT
    return replace(
        permission, reason="LOCATION_REACTION_CONFIRMED",
        reaction_state=DISPLACEMENT_CONFIRMED,
        confirmation_state=confirmation, m5_confirmation_state=confirmation,
        mss_level=float(mss_level) if mss_level is not None else None,
        mss_at=(mss_at.isoformat() if mss_at is not None else None),
        displacement_atr=displacement_atr,
        reasons=permission.reasons + ("RECLAIM", confirmation),
    )
