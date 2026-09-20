"""Causal, independent liquidity provenance for SWEEP_REJECTION."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np

from core.structure_engine import StructureEngine
from scalper.short_term_bias import SESSION_WINDOWS

EQUAL_LIQUIDITY_ATR_TOLERANCE = 0.10  # fixed research default; not outcome-fitted
IDENTITY_PRIORITY = (
    "PWH", "PWL", "PDH", "PDL", "H4_SWING_HIGH", "H4_SWING_LOW",
    "ASIA_HIGH", "ASIA_LOW", "LONDON_HIGH", "LONDON_LOW", "NY_HIGH", "NY_LOW",
    "H1_SWING_HIGH", "H1_SWING_LOW", "M15_EQUAL_HIGH", "M15_EQUAL_LOW",
    "UNKNOWN_LOCAL",
)


@dataclass(frozen=True)
class LiquidityPool:
    pool_id: str
    side: str
    pool_type: str
    timeframe: str
    level: float
    created_at: Optional[str]
    last_confirmed_at: Optional[str]
    touch_count: int
    fresh: bool
    consumed: bool
    source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SweepLocationPolicy:
    enabled: bool = False
    mode: str = "off"
    allow_previous_week: bool = True
    allow_previous_day: bool = True
    allow_session_liquidity: bool = True
    allow_h4_swing: bool = True
    allow_h1_swing: bool = True
    allow_m15_equal_liquidity: bool = True
    allow_unknown_local: bool = False
    allow_consumed: bool = False
    # The named-pool match is causal and volatility-normalised.  This is a
    # policy value, not an instrument-dollar constant.
    match_tolerance_atr: float = 0.10
    # Stamped at startup so telemetry can prove which policy was active.
    config_path: Optional[str] = None

    @property
    def active(self) -> bool:
        return bool(self.enabled and self.mode.lower() == "active")

    @property
    def allowed_families(self) -> tuple[str, ...]:
        families = []
        if self.allow_previous_week:
            families.append("PREVIOUS_WEEK")
        if self.allow_previous_day:
            families.append("PREVIOUS_DAY")
        if self.allow_session_liquidity:
            families.append("SESSION_LIQUIDITY")
        if self.allow_h4_swing:
            families.append("H4_SWING")
        if self.allow_h1_swing:
            families.append("H1_SWING")
        if self.allow_m15_equal_liquidity:
            families.append("M15_EQUAL_LIQUIDITY")
        return tuple(families)


@dataclass(frozen=True)
class LiquidityClassification:
    primary: str
    identities: tuple[str, ...]
    pools: tuple[LiquidityPool, ...]
    allowed: bool
    reason: str


@dataclass(frozen=True)
class SweepLocationContext:
    swept_level: float
    event_time: Optional[str] = None
    liquidity_type: str = "UNKNOWN_LOCAL"
    source_timeframe: Optional[str] = None
    level_low: Optional[float] = None
    level_high: Optional[float] = None
    distance_price: Optional[float] = 0.0
    distance_points: Optional[float] = None
    distance_atr: Optional[float] = 0.0
    matched_pool_level: Optional[float] = None
    pool_match_distance_points: Optional[float] = None
    pool_match_distance_atr: Optional[float] = None
    pool_match_threshold_atr: Optional[float] = None
    match_tolerance_atr: Optional[float] = None
    match_threshold_atr: Optional[float] = None
    fresh: Optional[bool] = None
    already_consumed: Optional[bool] = None
    pdh_distance_atr: Optional[float] = None
    pdl_distance_atr: Optional[float] = None
    session_level_type: Optional[str] = None
    session_level_distance_atr: Optional[float] = None
    htf_swing_type: Optional[str] = None
    htf_swing_distance_atr: Optional[float] = None
    equal_liquidity_type: Optional[str] = None
    equal_liquidity_distance_atr: Optional[float] = None
    fvg_timeframe: Optional[str] = None
    fvg_distance_atr: Optional[float] = None
    fvg_fresh: Optional[bool] = None
    ob_timeframe: Optional[str] = None
    ob_distance_atr: Optional[float] = None
    vp_reference_type: Optional[str] = None
    vp_distance_atr: Optional[float] = None
    premium_discount: Optional[str] = None
    structure_trend_h1: Optional[str] = None
    structure_trend_h4: Optional[str] = None
    mss: Optional[bool] = None
    bos: Optional[bool] = None
    displacement: Optional[bool] = None
    matched_triggers: tuple[str, ...] = ()
    liquidity_pool_id: Optional[str] = None
    primary_liquidity_identity: str = "UNKNOWN_LOCAL"
    all_liquidity_identities: tuple[str, ...] = ()
    source_swing_ids: tuple[str, ...] = ()
    touch_count: int = 0
    created_at: Optional[str] = None
    last_confirmed_at: Optional[str] = None
    pool_age_minutes: Optional[float] = None
    identity_reason: str = "no independent pre-existing pool"
    allowed: bool = True
    permission_reason: str = "OBSERVE_ONLY"

    def record(self) -> dict:
        return asdict(self)

    def with_matches(self, matches) -> "SweepLocationContext":
        return replace(self, matched_triggers=tuple(str(x) for x in matches))


def _dt(value: Any) -> Optional[datetime]:
    if value is None: return None
    if hasattr(value, "to_pydatetime"): value = value.to_pydatetime()
    if not isinstance(value, datetime): value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: Any) -> Optional[str]:
    value = _dt(value)
    return value.astimezone(timezone.utc).isoformat() if value else None


def _prior(frame, now: datetime):
    if frame is None or len(frame) == 0 or "time" not in frame: return frame
    return frame.loc[frame["time"].map(_dt) < now].copy()


def _atr(frame, period: int = 14) -> Optional[float]:
    if frame is None or len(frame) < 2: return None
    high, low, close = (frame[x].to_numpy(dtype=float) for x in ("high", "low", "close"))
    prev = np.r_[close[0], close[:-1]]
    tr = np.maximum(high - low, np.maximum(abs(high - prev), abs(low - prev)))
    value = float(np.mean(tr[-period:])) if len(tr) else 0.0
    return value if value > 0 else None


def _distance(anchor, level, atr):
    if level is None: return None, None
    price = abs(float(anchor) - float(level))
    return price, price / atr if atr else None


def _band_distance(anchor, low, high, atr):
    price = 0.0 if low <= anchor <= high else min(abs(anchor - low), abs(anchor - high))
    return price, price / atr if atr else None


def _pool_id(pool_type, timeframe, source_ids, level):
    raw = "|".join([pool_type, timeframe, *sorted(map(str, source_ids)), f"{level:.8f}"])
    return "LP-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _consumed(frame, level, side, after, before):
    if frame is None or len(frame) == 0: return False
    times = frame["time"].map(_dt)
    bars = frame.loc[(times >= after) & (times < before)]
    if bars.empty: return False
    return bool((bars["high"] > level).any() if side == "HIGH" else (bars["low"] < level).any())


def _consumed_after_event(frame, level, side, event_time, before):
    """Detect a later invalidation without counting the causal sweep bar."""
    if frame is None or len(frame) == 0 or event_time is None:
        return False
    times = frame["time"].map(_dt)
    bars = frame.loc[(times > event_time) & (times < before)]
    if bars.empty:
        return False
    return bool((bars["high"] > level).any() if side == "HIGH" else
                (bars["low"] < level).any())


def _pool(side, kind, tf, level, created, confirmed, source_ids, frame, now, touches=1):
    used = _consumed(frame, level, side, confirmed, now)
    return LiquidityPool(_pool_id(kind, tf, source_ids, level), side, kind, tf, float(level),
                         _iso(created), _iso(confirmed), int(touches), not used, used,
                         tuple(map(str, source_ids)))


def _structure_pools(frame, timeframe, now):
    history = _prior(frame, now)
    if history is None or len(history) < 20: return []
    state = StructureEngine(swing_lookback=5).analyze(history, "XAUUSD", timeframe)
    result = []
    for point in state.structure_points:
        if point.point_type in {"HH", "LH"}: side, kind = "HIGH", f"{timeframe}_SWING_HIGH"
        elif point.point_type in {"HL", "LL"}: side, kind = "LOW", f"{timeframe}_SWING_LOW"
        else: continue
        confirmed = _dt(history.iloc[min(point.index + 5, len(history)-1)]["time"])
        if confirmed is None or confirmed >= now: continue
        sid = f"{timeframe}:{_iso(point.timestamp)}:{point.point_type}"
        result.append(_pool(side, kind, timeframe, point.level, point.timestamp, confirmed,
                            (sid,), history, now))
    return result


def _equal_pools(frame, now, atr):
    history = _prior(frame, now)
    if history is None or len(history) < 20 or not atr: return []
    state = StructureEngine(swing_lookback=5).analyze(history, "XAUUSD", "M15")
    tolerance = atr * EQUAL_LIQUIDITY_ATR_TOLERANCE
    result = []
    for side, types, kind in (("HIGH", {"HH", "LH"}, "M15_EQUAL_HIGH"),
                              ("LOW", {"HL", "LL"}, "M15_EQUAL_LOW")):
        clusters = []
        candidates = [p for p in state.structure_points if p.point_type in types]
        distinct = []
        for point in candidates:
            # A flat/plateau run can produce several labels for one swing.
            # Treat points inside two swing-lookback widths as one event.
            if distinct and point.index - distinct[-1].index < 10:
                continue
            distinct.append(point)
        for point in distinct:
            confirmed = _dt(history.iloc[min(point.index + 5, len(history)-1)]["time"])
            if confirmed is None or confirmed >= now: continue
            cluster = next((c for c in clusters if abs(point.level - c["mean"]) <= tolerance), None)
            if cluster is None:
                cluster = {"points": [], "mean": float(point.level)}; clusters.append(cluster)
            cluster["points"].append((point, confirmed))
            cluster["mean"] = sum(float(p.level) for p, _ in cluster["points"]) / len(cluster["points"])
        for cluster in clusters:
            if len(cluster["points"]) < 2: continue
            points = cluster["points"]
            ids = tuple(f"M15:{_iso(p.timestamp)}:{p.point_type}" for p, _ in points)
            created = min((_dt(p.timestamp) for p, _ in points if _dt(p.timestamp)), default=None)
            confirmed = max((x for _, x in points), default=None)
            result.append(_pool(side, kind, "M15", cluster["mean"], created, confirmed,
                                ids, history, now, len(points)))
    return result


def _daily_pools(frame, now):
    history = _prior(frame, now)
    if history is None or history.empty: return []
    last = history.iloc[-1]; day = _dt(last["time"])
    result = [_pool("HIGH", "PDH", "D1", last.high, day, day, (f"D1:{_iso(day)}:HIGH",), history, now),
              _pool("LOW", "PDL", "D1", last.low, day, day, (f"D1:{_iso(day)}:LOW",), history, now)]
    key = (day.isocalendar().year, day.isocalendar().week)
    previous = history.loc[history["time"].map(_dt).map(lambda x: (x.isocalendar().year, x.isocalendar().week) < key)]
    if not previous.empty:
        weeks = previous["time"].map(_dt).map(lambda x: (x.isocalendar().year, x.isocalendar().week))
        wk = previous.groupby(weeks).agg(high=("high", "max"), low=("low", "min")); wk_key = wk.index[-1]
        rows = previous.loc[weeks == wk_key]; created = _dt(rows["time"].iloc[0]); confirmed = _dt(rows["time"].iloc[-1])
        week_row = wk.iloc[-1]
        result.extend([_pool("HIGH", "PWH", "W1", week_row["high"], created, confirmed, (f"W1:{wk_key}:HIGH",), history, now),
                       _pool("LOW", "PWL", "W1", week_row["low"], created, confirmed, (f"W1:{wk_key}:LOW",), history, now)])
    return result


def _session_pools(frame, now):
    history = _prior(frame, now)
    if history is None or history.empty: return []
    result = []
    dates = sorted(set(history["time"].map(_dt).map(lambda x: x.date())))
    for date in dates:
        for name, (start_h, end_h) in SESSION_WINDOWS.items():
            end = datetime(date.year, date.month, date.day, end_h, tzinfo=timezone.utc)
            if end >= now: continue  # only completed session liquidity
            mask = history["time"].map(_dt).map(lambda x: x.date() == date and start_h <= x.hour < end_h)
            bars = history.loc[mask]
            if bars.empty: continue
            formed = _dt(bars["time"].iloc[-1]); created = _dt(bars["time"].iloc[0])
            result.extend([_pool("HIGH", f"{name}_HIGH", "SESSION", bars.high.max(), created, formed,
                                  (f"SESSION:{name}:{date}:HIGH",), history, now),
                           _pool("LOW", f"{name}_LOW", "SESSION", bars.low.min(), created, formed,
                                  (f"SESSION:{name}:{date}:LOW",), history, now)])
    return result


def build_liquidity_pools(*, df_m15, df_h1=None, df_h4=None, df_d1=None, now) -> tuple[LiquidityPool, ...]:
    """Build only pools with source bars and confirmations before ``now``."""
    moment = _dt(now) or datetime.now(timezone.utc)
    atr = _atr(_prior(df_m15, moment))
    return tuple(_daily_pools(df_d1, moment) + _session_pools(df_m15, moment) +
                 _structure_pools(df_h4, "H4", moment) + _structure_pools(df_h1, "H1", moment) +
                 _equal_pools(df_m15, moment, atr))


def classify_liquidity(*, level: float, direction: str, pools: Iterable[LiquidityPool],
                       now, policy: SweepLocationPolicy,
                       atr_value: Optional[float] = None) -> LiquidityClassification:
    side = "LOW" if direction == "BULLISH" else "HIGH"
    threshold = float(policy.match_tolerance_atr)
    # No ATR means there is no defensible causal normalization.  Failing
    # closed here is intentional: exact-number equality was the old silent
    # false-negative path and dollar tolerances are not portable across
    # XAUUSD/XAGUSD/USOIL.
    if atr_value is None or not np.isfinite(float(atr_value)) or float(atr_value) <= 0:
        matches = []
    else:
        moment = _dt(now)
        def causal(pool: LiquidityPool) -> bool:
            confirmed = _dt(pool.last_confirmed_at)
            created = _dt(pool.created_at)
            # A pool must exist and be confirmed strictly before the raid.
            # Equality is rejected so a detector cannot explain itself.
            return (moment is not None and confirmed is not None
                    and confirmed < moment
                    and (created is None or created < moment))
        matches = [
            p for p in pools
            if p.side == side
            and causal(p)
            and abs(float(p.level) - float(level)) / float(atr_value) <= threshold
        ]
    if not matches:
        return LiquidityClassification("UNKNOWN_LOCAL", ("UNKNOWN_LOCAL",), (), policy.allow_unknown_local,
                                       "no independent pre-existing pool or causal ATR match")
    identities = tuple(dict.fromkeys(p.pool_type for p in matches))
    primary = min(identities, key=lambda x: IDENTITY_PRIORITY.index(x) if x in IDENTITY_PRIORITY else len(IDENTITY_PRIORITY))
    matches = sorted(matches, key=lambda p: (
        IDENTITY_PRIORITY.index(p.pool_type) if p.pool_type in IDENTITY_PRIORITY else len(IDENTITY_PRIORITY),
        abs(float(p.level) - float(level))))
    consumed = any(p.consumed for p in matches) and not policy.allow_consumed
    allowed_types = set()
    if policy.allow_previous_week: allowed_types.update({"PWH", "PWL"})
    if policy.allow_previous_day: allowed_types.update({"PDH", "PDL"})
    if policy.allow_session_liquidity: allowed_types.update({"ASIA_HIGH", "ASIA_LOW", "LONDON_HIGH", "LONDON_LOW", "NY_HIGH", "NY_LOW"})
    if policy.allow_h4_swing: allowed_types.update({"H4_SWING_HIGH", "H4_SWING_LOW"})
    if policy.allow_h1_swing: allowed_types.update({"H1_SWING_HIGH", "H1_SWING_LOW"})
    if policy.allow_m15_equal_liquidity: allowed_types.update({"M15_EQUAL_HIGH", "M15_EQUAL_LOW"})
    allowed = primary in allowed_types and not consumed
    reason = "PASS_INDEPENDENT_POOL" if allowed else ("CONSUMED_POOL" if consumed else "IDENTITY_NOT_PERMITTED")
    return LiquidityClassification(primary, identities, tuple(matches), allowed, reason)


def load_sweep_location_policy(path: Optional[str], *, require_active: bool = False) -> SweepLocationPolicy:
    """Load the named-liquidity contract and return its resolved path.

    A missing path is only valid for callers that have explicitly disabled the
    sweep trigger.  Live and replay entry points pass ``require_active=True``
    whenever SWEEP_REJECTION is in their trigger whitelist.
    """
    if not path:
        if require_active:
            raise ValueError(
                "SWEEP_REJECTION requires --sweep-location-config <path>; "
                "named-liquidity policy cannot silently fall back to OFF"
            )
        return SweepLocationPolicy()
    resolved = str(Path(path).expanduser().resolve())
    payload = json.loads(Path(resolved).read_text(encoding="utf-8"))
    raw = payload.get("sweep_location", payload)
    mode = str(raw.get("mode", "off")).lower()
    if mode not in {"off", "active"}:
        raise ValueError(f"sweep_location.mode must be off or active, got {mode!r}")
    allow = raw.get("allow", {}) or {}
    policy = SweepLocationPolicy(
        enabled=bool(raw.get("enabled", mode == "active")),
        mode=mode,
        allow_previous_week=bool(allow.get("previous_week", True)),
        allow_previous_day=bool(allow.get("previous_day", True)),
        allow_session_liquidity=bool(allow.get("session_liquidity", True)),
        allow_h4_swing=bool(allow.get("h4_swing", True)),
        allow_h1_swing=bool(allow.get("h1_swing", True)),
        allow_m15_equal_liquidity=bool(allow.get("m15_equal_liquidity", True)),
        allow_unknown_local=bool(raw.get("allow_unknown_local", False)),
        allow_consumed=bool(raw.get("allow_consumed", False)),
        match_tolerance_atr=float(raw.get("match_tolerance_atr", 0.10)),
        config_path=resolved,
    )
    if not np.isfinite(policy.match_tolerance_atr) or policy.match_tolerance_atr <= 0:
        raise ValueError("sweep_location.match_tolerance_atr must be a positive finite number")
    if require_active and not policy.active:
        raise ValueError(
            f"SWEEP_REJECTION requires an ACTIVE named-liquidity policy; "
            f"loaded {resolved!r} with enabled={policy.enabled} mode={policy.mode!r}"
        )
    return policy


def build_sweep_location_context(*, symbol: str, trigger: Any, df_trigger,
                                 liquidity: Any = None, now, df_d1=None, df_h1=None,
                                 df_h4=None, df_w1=None, session_tracker=None,
                                 vp_profile=None, m15_fvg=None,
                                 policy: SweepLocationPolicy = None,
                                 point: Optional[float] = None) -> SweepLocationContext:
    """Build causal context. ``liquidity`` is intentionally not consulted."""
    policy = policy or SweepLocationPolicy()
    moment = _dt(now) or datetime.now(timezone.utc)
    sweep_time = _dt(getattr(trigger, "sweep_time", None))
    # The liquidity identity is frozen at the raid event.  In particular, the
    # current sweep bar is not allowed to create or consume the pool it is
    # supposed to explain.
    event_time = min(sweep_time, moment) if sweep_time else moment
    level = float(getattr(trigger, "swept_level", 0.0) or 0.0)
    atr = _atr(_prior(df_trigger, event_time))
    pools = build_liquidity_pools(df_m15=df_trigger, df_h1=df_h1, df_h4=df_h4, df_d1=df_d1, now=event_time)
    c = classify_liquidity(level=level, direction=str(getattr(trigger, "direction", "")),
                           pools=pools, now=event_time, policy=policy,
                           atr_value=atr)
    selected = c.pools[0] if c.pools else None
    selected_type = selected.pool_type if selected else None
    selected_distance_price, selected_distance = _distance(level, selected.level, atr) if selected else (None, None)
    session_type = selected_type if selected_type in {"ASIA_HIGH", "ASIA_LOW", "LONDON_HIGH", "LONDON_LOW", "NY_HIGH", "NY_LOW"} else None
    htf_type = selected_type if selected_type in {"H1_SWING_HIGH", "H1_SWING_LOW", "H4_SWING_HIGH", "H4_SWING_LOW"} else None
    equal_type = selected_type if selected_type in {"M15_EQUAL_HIGH", "M15_EQUAL_LOW"} else None
    result = SweepLocationContext(
        swept_level=level, event_time=_iso(sweep_time or moment),
        liquidity_type=c.primary, source_timeframe=selected.timeframe if selected else "M15",
        distance_price=selected_distance_price,
        distance_points=(selected_distance_price / float(point)
                         if selected_distance_price is not None and point and point > 0
                         else None),
        distance_atr=selected_distance, match_tolerance_atr=policy.match_tolerance_atr,
        match_threshold_atr=policy.match_tolerance_atr,
        matched_pool_level=selected.level if selected else None,
        pool_match_distance_points=(selected_distance_price / float(point)
                                    if selected_distance_price is not None and point and point > 0
                                    else None),
        pool_match_distance_atr=selected_distance,
        pool_match_threshold_atr=policy.match_tolerance_atr,
        fresh=selected.fresh if selected else None, already_consumed=selected.consumed if selected else None,
        session_level_type=session_type, session_level_distance_atr=selected_distance if session_type else None,
        htf_swing_type=htf_type, htf_swing_distance_atr=selected_distance if htf_type else None,
        equal_liquidity_type=equal_type, equal_liquidity_distance_atr=selected_distance if equal_type else None,
        liquidity_pool_id=selected.pool_id if selected else None, primary_liquidity_identity=c.primary,
        all_liquidity_identities=c.identities, source_swing_ids=selected.source_ids if selected else (),
        touch_count=selected.touch_count if selected else 0, created_at=selected.created_at if selected else None,
        last_confirmed_at=selected.last_confirmed_at if selected else None,
        identity_reason=("independent pre-existing pool" if selected else
                         "no independent pre-existing pool or causal ATR match"),
        allowed=c.allowed if policy.active else True, permission_reason=c.reason if policy.active else "OBSERVE_ONLY")
    # Once the candidate exists, a subsequent breach invalidates the named
    # pool.  The causal event bar itself is intentionally excluded: that is
    # the sweep being explained, not a prior consumption.
    if selected and _consumed_after_event(df_trigger, selected.level, selected.side,
                                          event_time, moment):
        result = replace(result, fresh=False, already_consumed=True,
                         allowed=False if policy.active else result.allowed,
                         permission_reason=("CONSUMED_POOL" if policy.active
                                            else result.permission_reason))
    if selected and selected.created_at and sweep_time:
        created = _dt(selected.created_at)
        if created:
            result = replace(result, pool_age_minutes=max(
                0.0, (event_time - created).total_seconds() / 60.0))
    if df_d1 is not None and len(df_d1):
        history = _prior(df_d1, moment)
        if len(history):
            day = history.iloc[-1]; _, pdh = _distance(level, day.high, atr); _, pdl = _distance(level, day.low, atr)
            result = replace(result, pdh_distance_atr=pdh, pdl_distance_atr=pdl)
    for frame, tf in ((df_h1, "H1"), (df_h4, "H4")):
        history = _prior(frame, moment)
        if history is None or len(history) < 20: continue
        state = StructureEngine(swing_lookback=5).analyze(history, symbol, tf)
        result = replace(result, structure_trend_h1=state.trend if tf == "H1" else result.structure_trend_h1,
                         structure_trend_h4=state.trend if tf == "H4" else result.structure_trend_h4,
                         mss=bool(result.mss or state.mss_confirmed), bos=bool(result.bos or state.bos_confirmed))
    if m15_fvg is not None:
        low, high = getattr(m15_fvg, "fvg_low", None), getattr(m15_fvg, "fvg_high", None)
        if low is not None and high is not None:
            _, dist = _band_distance(level, float(low), float(high), atr)
            result = replace(result, fvg_timeframe="M15", fvg_distance_atr=dist,
                             fvg_fresh=getattr(m15_fvg, "reason", "") != "M15_FVG_CONSUMED")
    if vp_profile is not None and getattr(vp_profile, "valid", False):
        name, price = min((("POC", vp_profile.poc), ("VAH", vp_profile.vah), ("VAL", vp_profile.val)), key=lambda x: abs(level-float(x[1])))
        _, dist = _distance(level, price, atr); result = replace(result, vp_reference_type=name, vp_distance_atr=dist)
    if df_trigger is not None and len(df_trigger):
        lo, hi = float(df_trigger.low.min()), float(df_trigger.high.max())
        result = replace(result, premium_discount="PREMIUM" if level > (lo+hi)/2 else "DISCOUNT")
    return result
