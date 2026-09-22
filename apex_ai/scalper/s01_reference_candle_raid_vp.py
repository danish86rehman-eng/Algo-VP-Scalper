"""S01: reference-candle raid -> frozen raid-leg VP -> M5 execution.

This module owns the stateful part of the S01 strategy.  It deliberately does
not import the trigger engine or MT5: the same causal transition function is
used by the live agent and the chronological replay.

All frames handed to :class:`S01Engine` must contain completed bars.  The
caller owns that contract, just as it does for the existing VP modules.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from scalper.anchored_vp import atr
from scalper.execution_telemetry import technical_snapshot
from scalper.vp_liquidity_trigger import fractal_swings
from scalper.volume_profile import VolumeProfile, build_profile_auto


S01_TRIGGER = "S01_REFERENCE_CANDLE_RAID_VP"

IDLE = "IDLE"
REFERENCE_ARMED = "REFERENCE_ARMED"
RAID_IN_PROGRESS = "RAID_IN_PROGRESS"
RAID_REJECTED = "RAID_REJECTED"
BREAKOUT_ACCEPTED = "BREAKOUT_ACCEPTED"
WAIT_M15_MSS = "WAIT_M15_MSS"
MSS_CONFIRMED = "MSS_CONFIRMED"
IDENTIFY_RAID_LEG = "IDENTIFY_RAID_LEG"
BUILD_VP = "BUILD_VP"
VP_FROZEN = "VP_FROZEN"
WAIT_ENTRY_RETEST = "WAIT_ENTRY_RETEST"
WAIT_M5_CONFIRMATION = "WAIT_M5_CONFIRMATION"
READY = "READY"
EXECUTED = "EXECUTED"
INVALIDATED = "INVALIDATED"
EXPIRED = "EXPIRED"
COMPLETE = "COMPLETE"

TERMINAL_STATES = frozenset({BREAKOUT_ACCEPTED, EXECUTED, INVALIDATED,
                             EXPIRED, COMPLETE})


@dataclass(frozen=True)
class S01Params:
    """Shared strategy constants; live and replay construct the same object."""

    m15_atr_period: int = 14
    m5_atr_period: int = 14
    swing_lookback: int = 3
    m5_swing_lookback: int = 2
    min_raid_depth_atr: float = 0.05
    min_raid_depth_spreads: float = 1.5
    acceptance_bars: int = 2
    displacement_atr: float = 0.80
    m5_displacement_atr: float = 0.80
    entry_zone_atr: float = 0.15
    sl_buffer_atr: float = 0.10
    target_bins: int = 40
    value_area_pct: float = 0.70
    max_setup_bars: int = 48
    max_m5_wait_bars: int = 18

    @classmethod
    def from_decision_params(cls, dp) -> "S01Params":
        return cls(
            m15_atr_period=dp.S01_M15_ATR_PERIOD,
            m5_atr_period=dp.S01_M5_ATR_PERIOD,
            swing_lookback=dp.S01_SWING_LOOKBACK,
            m5_swing_lookback=dp.S01_M5_SWING_LOOKBACK,
            min_raid_depth_atr=dp.S01_MIN_RAID_DEPTH_ATR,
            min_raid_depth_spreads=dp.S01_MIN_RAID_DEPTH_SPREADS,
            acceptance_bars=dp.S01_ACCEPTANCE_BARS,
            displacement_atr=dp.S01_DISPLACEMENT_ATR,
            m5_displacement_atr=dp.S01_M5_DISPLACEMENT_ATR,
            entry_zone_atr=dp.S01_ENTRY_ZONE_ATR,
            sl_buffer_atr=dp.S01_SL_BUFFER_ATR,
            target_bins=dp.S01_TARGET_BINS,
            value_area_pct=dp.S01_VALUE_AREA_PCT,
            max_setup_bars=dp.S01_MAX_SETUP_BARS,
            max_m5_wait_bars=dp.S01_MAX_M5_WAIT_BARS,
        )


@dataclass(frozen=True)
class S01Reference:
    timestamp: Any
    high: float
    low: float
    liquidity_side: str  # BSL | SSL
    reference_id: str

    @property
    def direction(self) -> str:
        return "BEARISH" if self.liquidity_side == "BSL" else "BULLISH"

    @property
    def target(self) -> float:
        return self.low if self.liquidity_side == "BSL" else self.high

    def record(self) -> dict:
        return {
            "reference_timestamp": _iso(self.timestamp),
            "reference_high": self.high,
            "reference_low": self.low,
            "liquidity_side": self.liquidity_side,
            "reference_id": self.reference_id,
        }


@dataclass
class S01Setup:
    setup_id: str
    symbol: str
    reference: S01Reference
    state: str = REFERENCE_ARMED
    transitions: list[dict] = field(default_factory=list)
    last_rejection_reason: str = ""
    reference_armed_at: Any = None
    raid_start: Any = None
    raid_extreme: Optional[float] = None
    raid_extreme_time: Any = None
    raid_extreme_index: Optional[int] = None
    raid_depth_points: float = 0.0
    raid_depth_atr: float = 0.0
    raid_depth_spreads: float = 0.0
    reclaim_time: Any = None
    acceptance_closes: int = 0
    m15_mss: bool = False
    m15_mss_level: Optional[float] = None
    m15_mss_time: Any = None
    displacement_strength_atr: float = 0.0
    vp_start: Any = None
    vp_end: Any = None
    vp_start_index: Optional[int] = None
    vp_end_index: Optional[int] = None
    profile: Optional[VolumeProfile] = None
    vp_volume_source: str = "broker_tick_density"
    profile_frozen_at: Any = None
    selected_entry_model: str = ""  # POC | VAH | VAL
    selected_entry_level: Optional[float] = None
    selected_entry_time: Any = None
    poc_accepted: bool = False
    poc_retest_attempted: bool = False
    m5_reaction_time: Any = None
    m5_displacement_time: Any = None
    m5_mss: bool = False
    m5_fvg: Optional[dict] = None
    m5_ob: Optional[dict] = None
    m5_entry_model: str = ""
    m5_entry_zone: Optional[tuple[float, float]] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    structural_target: Optional[float] = None
    gross_r: Optional[float] = None
    estimated_cost_r: Optional[float] = None
    net_r: Optional[float] = None
    current_price: Optional[float] = None
    distance_to_poc: Optional[float] = None
    distance_to_vah: Optional[float] = None
    distance_to_val: Optional[float] = None
    atr14_m15: Optional[float] = None
    adx14_m15: Optional[float] = None
    spread_price: Optional[float] = None
    session: str = ""
    final_outcome: str = ""
    entry_emitted: bool = False
    terminal_at: Any = None

    def transition(self, state: str, *, at: Any = None, reason: str = "") -> None:
        if self.state == state:
            return
        self.transitions.append({
            "from": self.state, "to": state, "at": _iso(at),
            "reason": reason,
        })
        self.state = state
        if reason:
            self.last_rejection_reason = reason

    def record(self) -> dict:
        p = self.profile
        row = {
            "schema": "S01_CANDIDATE_V1",
            "strategy": S01_TRIGGER,
            "setup_id": self.setup_id,
            "symbol": self.symbol,
            "state": self.state,
            "transitions": list(self.transitions),
            "reference": self.reference.record(),
            "reference_armed_at": _iso(self.reference_armed_at),
            "raid_start": _iso(self.raid_start),
            "raid_extreme": self.raid_extreme,
            "raid_extreme_time": _iso(self.raid_extreme_time),
            "raid_depth_points": self.raid_depth_points,
            "raid_depth_atr": self.raid_depth_atr,
            "raid_depth_spreads": self.raid_depth_spreads,
            "reclaim_timestamp": _iso(self.reclaim_time),
            "acceptance_closes": self.acceptance_closes,
            "acceptance_rejection_state": (
                "REJECTED" if self.state not in {BREAKOUT_ACCEPTED}
                else "ACCEPTED"),
            "m15_mss": self.m15_mss,
            "m15_mss_level": self.m15_mss_level,
            "m15_mss_timestamp": _iso(self.m15_mss_time),
            "displacement_strength_atr": self.displacement_strength_atr,
            "vp_start": _iso(self.vp_start),
            "vp_end": _iso(self.vp_end),
            "vp_start_index": self.vp_start_index,
            "vp_end_index": self.vp_end_index,
            "vp_frozen": p is not None,
            "poc": p.poc if p else None,
            "vah": p.vah if p else None,
            "val": p.val if p else None,
            "vp_bin_size": p.bin_size if p else None,
            "vp_value_area_pct": (p.value_area_volume / p.total_volume
                                   if p and p.total_volume else None),
            "vp_volume_source": self.vp_volume_source,
            "selected_entry_model": self.selected_entry_model or None,
            "selected_entry_level": self.selected_entry_level,
            "selected_entry_timestamp": _iso(self.selected_entry_time),
            "poc_acceptance": self.poc_accepted,
            "m5_reaction_timestamp": _iso(self.m5_reaction_time),
            "m5_displacement_timestamp": _iso(self.m5_displacement_time),
            "m5_mss": self.m5_mss,
            "m5_fvg": self.m5_fvg,
            "m5_ob": self.m5_ob,
            "m5_entry_model": self.m5_entry_model or None,
            "m5_entry_zone": list(self.m5_entry_zone)
            if self.m5_entry_zone else None,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "structural_target": self.structural_target,
            "gross_r": self.gross_r,
            "estimated_cost_r": self.estimated_cost_r,
            "net_r": self.net_r,
            "current_price": self.current_price,
            "distance_to_poc": self.distance_to_poc,
            "distance_to_vah": self.distance_to_vah,
            "distance_to_val": self.distance_to_val,
            "atr14_m15": self.atr14_m15,
            "adx14_m15": self.adx14_m15,
            "spread_price": self.spread_price,
            "session": self.session,
            "final_outcome": self.final_outcome,
            "rejection_reason": self.last_rejection_reason,
            "terminal_at": _iso(self.terminal_at),
        }
        return _clean(row)


@dataclass(frozen=True)
class S01Result:
    detected: bool
    state: str
    setup_id: Optional[str]
    direction: str = "NONE"
    entry_price: float = 0.0
    stop_loss: float = 0.0
    target: float = 0.0
    selected_entry_model: str = ""
    rejection_reason: str = ""
    telemetry: Optional[dict] = None

    def record(self) -> dict:
        return {
            "strategy": S01_TRIGGER,
            "detected": self.detected,
            "state": self.state,
            "setup_id": self.setup_id,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "target": self.target,
            "selected_entry_model": self.selected_entry_model,
            "rejection_reason": self.rejection_reason,
            "telemetry": self.telemetry,
        }


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return pd.Timestamp(value).isoformat()
    except (TypeError, ValueError):
        return str(value)


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return _iso(value)
    return value


def _row_time(df: pd.DataFrame, index: int) -> Any:
    return df["time"].iloc[index] if "time" in df.columns else index


def _index_for_time(df: pd.DataFrame, stamp: Any,
                    fallback: Optional[int] = None) -> Optional[int]:
    """Resolve a stored event timestamp inside a rolling replay/live frame."""
    if stamp is None or "time" not in df.columns:
        return fallback
    target = pd.Timestamp(stamp)
    times = pd.to_datetime(df["time"], utc=True)
    if target.tzinfo is None:
        target = target.tz_localize("UTC")
    else:
        target = target.tz_convert("UTC")
    matches = np.where((times <= target).to_numpy())[0]
    return int(matches[-1]) if len(matches) else fallback


def _direction_hint(df: pd.DataFrame) -> str:
    if df is None or len(df) < 4:
        return "NONE"
    closes = df["close"].to_numpy(dtype=float)
    delta = closes[-1] - closes[-4]
    if delta > 0:
        return "BEARISH"  # delivery up into BSL, reversal candidate is short
    if delta < 0:
        return "BULLISH"  # delivery down into SSL, reversal candidate is long
    return "NONE"


def _reference_id(symbol: str, timestamp: Any, side: str,
                  high: float, low: float) -> str:
    raw = "|".join(map(str, (symbol.upper(), _iso(timestamp), side,
                               round(high, 8), round(low, 8))))
    return "S01R_" + sha256(raw.encode("utf-8")).hexdigest()[:20]


def select_reference_candle(df_d1_closed: pd.DataFrame,
                            df_m15_closed: pd.DataFrame,
                            symbol: str,
                            current_price: Optional[float] = None
                            ) -> Optional[S01Reference]:
    """Select the nearest untouched external D1 level before a raid.

    A D1 high is valid BSL only if no later completed D1 candle exceeded it;
    its paired low must also remain available as the structural target.  The
    SSL rule is the exact mirror.  Candidates already pierced by the available
    M15 history are excluded, which prevents arming a reference after its raid.
    """
    if (df_d1_closed is None or len(df_d1_closed) < 2 or
            df_m15_closed is None or len(df_m15_closed) < 4):
        return None
    required = {"high", "low", "close"}
    if not required.issubset(df_d1_closed.columns) or not required.issubset(df_m15_closed.columns):
        return None
    price = float(current_price if current_price is not None
                  else df_m15_closed["close"].iloc[-1])
    direction = _direction_hint(df_m15_closed)
    if direction == "NONE":
        return None

    d1 = df_d1_closed.reset_index(drop=True)
    m15 = df_m15_closed.reset_index(drop=True)
    candidates = []
    for i in range(len(d1)):
        high, low = float(d1["high"].iloc[i]), float(d1["low"].iloc[i])
        later = d1.iloc[i + 1:]
        # The reference candle's own intraday bars are allowed to contain its
        # high/low.  Only bars after that completed D1 candle can consume the
        # level.  Without this cutoff the reference would always disqualify
        # itself because its own M15 high equals its D1 high.
        if "time" in d1.columns and "time" in m15.columns:
            ref_end = pd.Timestamp(d1["time"].iloc[i]) + pd.Timedelta(days=1)
            post_reference = m15[pd.to_datetime(m15["time"], utc=True) >=
                                 (ref_end.tz_localize("UTC") if ref_end.tzinfo is None
                                  else ref_end.tz_convert("UTC"))]
        else:
            post_reference = m15
        if direction == "BEARISH":
            if high <= price or low >= price:
                continue
            if len(later) and (float(later["high"].max()) >= high or
                               float(later["low"].min()) <= low):
                continue
            if len(post_reference) and float(post_reference["high"].max()) >= high:
                continue
            if len(post_reference) and float(post_reference["low"].min()) <= low:
                continue
            side = "BSL"
            level = high
        else:
            if low >= price or high <= price:
                continue
            if len(later) and (float(later["low"].min()) <= low or
                               float(later["high"].max()) >= high):
                continue
            if len(post_reference) and float(post_reference["low"].min()) <= low:
                continue
            if len(post_reference) and float(post_reference["high"].max()) >= high:
                continue
            side = "SSL"
            level = low
        candidates.append((abs(level - price), -i, i, side, high, low))
    if not candidates:
        return None
    _, _, i, side, high, low = min(candidates)
    stamp = _row_time(d1, i)
    return S01Reference(stamp, high, low, side,
                        _reference_id(symbol, stamp, side, high, low))


def _touches_zone(bar, low: float, high: float) -> bool:
    return float(bar["high"]) >= low and float(bar["low"]) <= high


def _directional_mss(df: pd.DataFrame, start_idx: int, bearish: bool,
                     swing_lookback: int) -> tuple[Optional[float], Optional[int]]:
    """Find a causal M15/M5 structure break after ``start_idx``."""
    if df is None or len(df) < 2 * swing_lookback + 3:
        return None, None
    frame = df.reset_index(drop=True)
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    closes = frame["close"].to_numpy(dtype=float)
    pivot = start_idx + (int(np.argmax(highs[start_idx:])) if bearish
                         else int(np.argmin(lows[start_idx:])))
    if pivot >= len(frame) - 1:
        return None, None
    sh, sl = fractal_swings(frame.iloc[:pivot + 1], swing_lookback)
    candidates = sl if bearish else sh
    candidates = [x for x in candidates if x[0] >= start_idx and x[0] < pivot]
    if not candidates:
        return None, None
    level = candidates[-1][1]
    for j in range(pivot + 1, len(frame)):
        if (closes[j] < level) if bearish else (closes[j] > level):
            return float(level), j
    return None, None


class S01Engine:
    """One persistent S01 state machine for one symbol."""

    STATE_VERSION = 1

    def __init__(self, symbol: str, params: Optional[S01Params] = None,
                 state_path: Optional[str] = None):
        self.symbol = str(symbol).upper()
        self.params = params or S01Params()
        self.state_path = Path(state_path) if state_path else None
        self.setup: Optional[S01Setup] = None
        self.history: list[dict] = []
        self._last_m15_time = None
        self._last_m5_time = None
        self._terminal_seen = False
        self._load_state()

    @property
    def state(self) -> str:
        return self.setup.state if self.setup else IDLE

    def reset(self) -> None:
        self.setup = None
        self._last_m15_time = None
        self._last_m5_time = None
        self._terminal_seen = False
        self._save_state()

    def _save_state(self) -> None:
        if self.state_path is None:
            return
        # READY contains an executable quote and must never be resurrected.
        setup = (asdict(self.setup) if self.setup is not None
                 and self.setup.state not in TERMINAL_STATES | {READY} else None)
        payload = {"version": self.STATE_VERSION, "symbol": self.symbol,
                   "setup": _clean(setup),
                   "last_m15_time": _iso(self._last_m15_time),
                   "last_m5_time": _iso(self._last_m5_time)}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        envelope = {"payload": payload,
                    "checksum": sha256(canonical.encode("utf-8")).hexdigest()}
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
            if (payload.get("version") != self.STATE_VERSION
                    or payload.get("symbol") != self.symbol
                    or sha256(canonical.encode("utf-8")).hexdigest()
                    != envelope.get("checksum")):
                return
            raw = payload.get("setup")
            if not raw or raw.get("state") in TERMINAL_STATES | {READY}:
                return
            ref_raw = raw.pop("reference")
            profile_raw = raw.pop("profile", None)
            reference = S01Reference(**ref_raw)
            profile = VolumeProfile(**profile_raw) if profile_raw else None
            if raw.get("m5_entry_zone") is not None:
                raw["m5_entry_zone"] = tuple(raw["m5_entry_zone"])
            allowed = S01Setup.__dataclass_fields__.keys()
            self.setup = S01Setup(reference=reference, profile=profile,
                                  **{k: v for k, v in raw.items() if k in allowed})
            self._last_m15_time = payload.get("last_m15_time")
            self._last_m5_time = payload.get("last_m5_time")
        except (OSError, ValueError, TypeError, KeyError):
            self.setup = None

    def _arm(self, reference: S01Reference, at: Any) -> None:
        self.setup = S01Setup(
            setup_id=reference.reference_id, symbol=self.symbol,
            reference=reference, reference_armed_at=at,
            structural_target=reference.target,
            transitions=[{"from": IDLE, "to": REFERENCE_ARMED,
                          "at": _iso(at), "reason": "untouched D1 reference"}],
        )
        self._terminal_seen = False
        self._save_state()

    def _terminal(self, state: str, at: Any, reason: str) -> None:
        assert self.setup is not None
        self.setup.transition(state, at=at, reason=reason)
        self.setup.terminal_at = at
        self.history.append(self.setup.record())
        self._terminal_seen = True
        self._save_state()

    def _invalidated_by_market(self, df_m15: pd.DataFrame,
                               df_m5: pd.DataFrame) -> Optional[str]:
        s = self.setup
        if s is None:
            return None
        ref = s.reference
        recent_m15 = df_m15.iloc[-1]
        recent_m5 = df_m5.iloc[-1] if df_m5 is not None and len(df_m5) else None
        if ref.liquidity_side == "BSL":
            if s.raid_extreme is not None and float(recent_m15["high"]) > s.raid_extreme:
                return "new break above raid extreme"
            if s.profile and float(recent_m15["close"]) > s.profile.vah:
                return "acceptance above frozen VAH"
            if "time" in df_m15.columns and s.reference_armed_at is not None:
                armed = pd.Timestamp(s.reference_armed_at)
                times = pd.to_datetime(df_m15["time"], utc=True)
                if armed.tzinfo is None:
                    armed = armed.tz_localize("UTC")
                else:
                    armed = armed.tz_convert("UTC")
                after_arm = df_m15.loc[times > armed]
            else:
                after_arm = df_m15
            consumed = len(after_arm) > 0 and float(after_arm["low"].min()) <= ref.low
            if recent_m5 is not None:
                consumed = consumed or float(df_m5["low"].iloc[-1]) <= ref.low
        else:
            if s.raid_extreme is not None and float(recent_m15["low"]) < s.raid_extreme:
                return "new break below raid extreme"
            if s.profile and float(recent_m15["close"]) < s.profile.val:
                return "acceptance below frozen VAL"
            if "time" in df_m15.columns and s.reference_armed_at is not None:
                armed = pd.Timestamp(s.reference_armed_at)
                times = pd.to_datetime(df_m15["time"], utc=True)
                if armed.tzinfo is None:
                    armed = armed.tz_localize("UTC")
                else:
                    armed = armed.tz_convert("UTC")
                after_arm = df_m15.loc[times > armed]
            else:
                after_arm = df_m15
            consumed = len(after_arm) > 0 and float(after_arm["high"].max()) >= ref.high
            if recent_m5 is not None:
                consumed = consumed or float(df_m5["high"].iloc[-1]) >= ref.high
        if s.state in {MSS_CONFIRMED, IDENTIFY_RAID_LEG, BUILD_VP,
                       VP_FROZEN, WAIT_ENTRY_RETEST, WAIT_M5_CONFIRMATION,
                       READY} and consumed:
            return "opposite reference liquidity already consumed"
        return None

    def _record_raid(self, df: pd.DataFrame, idx: int, atr_value: float,
                     spread_price: float) -> None:
        s = self.setup
        assert s is not None
        bar = df.iloc[idx]
        bearish = s.reference.liquidity_side == "BSL"
        extreme = float(bar["high"] if bearish else bar["low"])
        if s.raid_extreme is None or (extreme > s.raid_extreme if bearish
                                      else extreme < s.raid_extreme):
            s.raid_extreme = extreme
            s.raid_extreme_index = idx
            s.raid_extreme_time = _row_time(df, idx)
        depth = (s.raid_extreme - (s.reference.high if bearish else s.reference.low)
                 if bearish else
                 (s.reference.low - s.raid_extreme))
        s.raid_depth_points = max(0.0, float(depth))
        s.raid_depth_atr = s.raid_depth_points / atr_value if atr_value else 0.0
        s.raid_depth_spreads = (s.raid_depth_points / spread_price
                                if spread_price > 0 else 0.0)
        if s.raid_start is None:
            s.raid_start = _row_time(df, idx)

    def _advance_m15(self, df: pd.DataFrame, spread_price: float,
                     min_stop_distance: float) -> None:
        s = self.setup
        assert s is not None
        if len(df) == 0:
            return
        idx = len(df) - 1
        bar = df.iloc[idx]
        now = _row_time(df, idx)
        atr_value = atr(df, self.params.m15_atr_period)
        if atr_value <= 0:
            return
        bearish = s.reference.liquidity_side == "BSL"
        level = s.reference.high if bearish else s.reference.low

        if s.state == REFERENCE_ARMED:
            pierced = float(bar["high"]) > level if bearish else float(bar["low"]) < level
            if pierced:
                depth = (float(bar["high"]) - level if bearish
                         else level - float(bar["low"]))
                if (depth >= self.params.min_raid_depth_atr * atr_value and
                        (spread_price <= 0 or depth >= self.params.min_raid_depth_spreads * spread_price)):
                    self._record_raid(df, idx, atr_value, spread_price)
                    if (float(bar["close"]) < level) if bearish else (float(bar["close"]) > level):
                        s.reclaim_time = now
                        s.transition(RAID_REJECTED, at=now,
                                    reason="completed M15 reclaim")
                        s.transition(WAIT_M15_MSS, at=now,
                                     reason="raid rejected; await M15 MSS")
                    else:
                        s.acceptance_closes = 1
                        s.transition(RAID_IN_PROGRESS, at=now,
                                     reason="external D1 liquidity pierced")
            return

        if s.state == RAID_IN_PROGRESS:
            self._record_raid(df, idx, atr_value, spread_price)
            reclaimed = ((float(bar["close"]) < level) if bearish
                         else (float(bar["close"]) > level))
            if reclaimed:
                s.reclaim_time = now
                s.transition(RAID_REJECTED, at=now,
                             reason="completed M15 reclaim")
                s.transition(WAIT_M15_MSS, at=now,
                             reason="raid rejected; await M15 MSS")
            else:
                s.acceptance_closes += 1
                if s.acceptance_closes >= self.params.acceptance_bars:
                    self._terminal(BREAKOUT_ACCEPTED, now,
                                   "sustained M15 acceptance beyond reference")
            return

        if s.state in {RAID_REJECTED, WAIT_M15_MSS}:
            raid_idx = _index_for_time(df, s.raid_extreme_time,
                                       s.raid_extreme_index) or 0
            mss_level, mss_idx = _directional_mss(
                df, raid_idx, bearish,
                self.params.swing_lookback)
            if mss_level is not None and mss_idx is not None:
                body = abs(float(df["close"].iloc[mss_idx]) -
                           float(df["open"].iloc[mss_idx]))
                strength = body / atr_value if atr_value else 0.0
                if strength >= self.params.displacement_atr:
                    s.m15_mss = True
                    s.m15_mss_level = mss_level
                    s.m15_mss_time = _row_time(df, mss_idx)
                    s.displacement_strength_atr = strength
                    s.transition(MSS_CONFIRMED, at=s.m15_mss_time,
                                 reason="causal M15 MSS plus displacement")
                    s.transition(IDENTIFY_RAID_LEG, at=now,
                                 reason="identify final delivery leg")
                    s.transition(BUILD_VP, at=now,
                                 reason="profile only VP_START through VP_END")

        if s.state == BUILD_VP:
            self._build_frozen_profile(df, now)
            if s.state == VP_FROZEN:
                s.transition(WAIT_ENTRY_RETEST, at=now,
                             reason="raid-leg POC/VAH/VAL frozen")

        if s.state in {VP_FROZEN, WAIT_ENTRY_RETEST, WAIT_M5_CONFIRMATION}:
            self._select_entry_model(df, now)

    def _build_frozen_profile(self, df: pd.DataFrame, now: Any) -> None:
        s = self.setup
        assert s is not None
        if s.raid_extreme_index is None:
            self._terminal(INVALIDATED, now, "raid extreme unavailable")
            return
        bearish = s.reference.liquidity_side == "BSL"
        sh, sl = fractal_swings(df, self.params.swing_lookback)
        pivots = sl if bearish else sh
        eligible = [x for x in pivots if x[0] < s.raid_extreme_index]
        if not eligible:
            s.last_rejection_reason = "waiting for causal VP_START swing confirmation"
            return
        start_idx, _ = eligible[-1]
        end_idx = _index_for_time(df, s.raid_extreme_time,
                                  s.raid_extreme_index)
        if end_idx is None:
            s.last_rejection_reason = "raid extreme is outside current closed frame"
            return
        if start_idx >= end_idx:
            s.last_rejection_reason = "invalid raid-leg span"
            return
        leg = df.iloc[start_idx:end_idx + 1].copy()
        volume_column = ("real_volume" if "real_volume" in leg.columns and
                          float(leg["real_volume"].sum()) > 0 else "tick_volume")
        profile = build_profile_auto(
            leg, target_bins=self.params.target_bins,
            va_pct=self.params.value_area_pct,
            volume_column=volume_column,
        )
        if profile is None or not profile.valid:
            s.last_rejection_reason = "raid-leg VP could not be built"
            return
        s.vp_start_index = start_idx
        s.vp_end_index = end_idx
        s.vp_start = _row_time(df, start_idx)
        s.vp_end = _row_time(df, end_idx)
        s.profile = profile
        s.vp_volume_source = ("real_volume" if volume_column == "real_volume"
                              else "broker_tick_density")
        s.profile_frozen_at = now
        if s.state == BUILD_VP:
            s.transition(VP_FROZEN, at=now,
                         reason="valid raid-leg volume profile frozen")

    def _select_entry_model(self, df: pd.DataFrame, now: Any) -> None:
        s = self.setup
        assert s is not None and s.profile is not None
        if s.selected_entry_model:
            return
        bearish = s.reference.liquidity_side == "BSL"
        p = s.profile
        bar = df.iloc[-1]
        close = float(bar["close"])
        zone = max(atr(df, self.params.m15_atr_period) * self.params.entry_zone_atr,
                   p.bin_size)
        if bearish:
            if close < p.poc:
                s.poc_accepted = True
            poc_retest = (s.poc_accepted and float(bar["high"]) >= p.poc - zone
                          and close < p.poc)
            if s.poc_accepted and float(bar["high"]) >= p.poc - zone:
                s.poc_retest_attempted = True
            vah_rejection = float(bar["high"]) >= p.vah - zone and close < p.vah
            if poc_retest:
                s.selected_entry_model = "POC"
                s.selected_entry_level = p.poc
            elif vah_rejection and not s.poc_retest_attempted:
                s.selected_entry_model = "VAH"
                s.selected_entry_level = p.vah
        else:
            if close > p.poc:
                s.poc_accepted = True
            poc_retest = (s.poc_accepted and float(bar["low"]) <= p.poc + zone
                          and close > p.poc)
            if s.poc_accepted and float(bar["low"]) <= p.poc + zone:
                s.poc_retest_attempted = True
            val_rejection = float(bar["low"]) <= p.val + zone and close > p.val
            if poc_retest:
                s.selected_entry_model = "POC"
                s.selected_entry_level = p.poc
            elif val_rejection and not s.poc_retest_attempted:
                s.selected_entry_model = "VAL"
                s.selected_entry_level = p.val
        if s.selected_entry_model:
            s.selected_entry_time = now
            s.transition(WAIT_M5_CONFIRMATION, at=now,
                         reason=f"first valid {s.selected_entry_model} retest")

    def _m5_confirmation(self, df: pd.DataFrame, current_price: float,
                         spread_price: float, min_stop_distance: float,
                         now: Any) -> Optional[S01Result]:
        s = self.setup
        assert s is not None
        if not s.selected_entry_model or s.selected_entry_time is None:
            return None
        if df is None or len(df) < 2 * self.params.m5_swing_lookback + 5:
            return None
        times = pd.to_datetime(df["time"], utc=True) if "time" in df.columns else None
        selected = pd.Timestamp(s.selected_entry_time)
        if selected.tzinfo is None:
            selected = selected.tz_localize("UTC")
        else:
            selected = selected.tz_convert("UTC")
        work = df.loc[times > selected].reset_index(drop=True) if times is not None else df.iloc[1:].reset_index(drop=True)
        if len(work) < 3:
            return None
        if len(work) > self.params.max_m5_wait_bars:
            self._terminal(EXPIRED, now, "M5 confirmation window expired")
            return None
        bearish = s.reference.liquidity_side == "BSL"
        level = float(s.selected_entry_level)
        a = atr(work, self.params.m5_atr_period)
        if a <= 0:
            return None
        zone = max(a * self.params.entry_zone_atr, 1e-9)
        reaction = None
        for i in range(len(work)):
            bar = work.iloc[i]
            if _touches_zone(bar, level - zone if bearish else level - zone,
                             level + zone) and ((float(bar["close"]) < level)
                                                if bearish else
                                                (float(bar["close"]) > level)):
                reaction = i
                break
        if reaction is None:
            return None
        if s.m5_reaction_time is None:
            s.m5_reaction_time = work["time"].iloc[reaction] if "time" in work.columns else now

        displacement = None
        mss_level = None
        for j in range(reaction + 1, len(work)):
            body = abs(float(work["close"].iloc[j]) - float(work["open"].iloc[j]))
            right_way = ((float(work["close"].iloc[j]) < float(work["open"].iloc[j]))
                         if bearish else
                         (float(work["close"].iloc[j]) > float(work["open"].iloc[j])))
            if not right_way or body < self.params.m5_displacement_atr * a:
                continue
            mss_level, _ = _directional_mss(work.iloc[:j + 1], reaction,
                                            bearish, self.params.m5_swing_lookback)
            if mss_level is None:
                continue
            if not ((float(work["close"].iloc[j]) < mss_level) if bearish
                    else (float(work["close"].iloc[j]) > mss_level)):
                continue
            displacement = j
            break
        if displacement is None:
            return None
        s.m5_mss = True
        s.m5_displacement_time = (work["time"].iloc[displacement]
                                  if "time" in work.columns else now)

        zone_data = None
        zone_formed_idx = None
        # Prefer the first fresh FVG made by the displacement.
        for j in range(displacement, len(work)):
            if j >= 2:
                if bearish and float(work["high"].iloc[j]) < float(work["low"].iloc[j - 2]):
                    zone_data = {"low": float(work["high"].iloc[j]),
                                 "high": float(work["low"].iloc[j - 2]),
                                 "formed_at": _iso(work["time"].iloc[j]) if "time" in work.columns else _iso(now)}
                    s.m5_fvg = zone_data
                    s.m5_entry_model = "FVG"
                    zone_formed_idx = j
                    break
                if (not bearish and
                        float(work["low"].iloc[j]) > float(work["high"].iloc[j - 2])):
                    zone_data = {"low": float(work["high"].iloc[j - 2]),
                                 "high": float(work["low"].iloc[j]),
                                 "formed_at": _iso(work["time"].iloc[j]) if "time" in work.columns else _iso(now)}
                    s.m5_fvg = zone_data
                    s.m5_entry_model = "FVG"
                    zone_formed_idx = j
                    break
        if zone_data is None and displacement > 0:
            prev = work.iloc[displacement - 1]
            opposite = ((float(prev["close"]) > float(prev["open"])) if bearish
                         else (float(prev["close"]) < float(prev["open"])))
            if opposite:
                zone_data = {"low": float(prev["low"]), "high": float(prev["high"]),
                             "formed_at": _iso(work["time"].iloc[displacement - 1]) if "time" in work.columns else _iso(now)}
                s.m5_ob = zone_data
                s.m5_entry_model = "OB"
                zone_formed_idx = displacement - 1
        if zone_data is None:
            return None
        s.m5_entry_zone = (float(zone_data["low"]), float(zone_data["high"]))
        # A zone cannot be retested before it exists.  FVG and OB formation
        # indices are intentionally independent from the displacement index.
        for k in range(int(zone_formed_idx) + 1, len(work)):
            bar = work.iloc[k]
            if not _touches_zone(bar, *s.m5_entry_zone):
                continue
            directional_close = ((float(bar["close"]) < float(bar["open"])) if bearish
                                 else (float(bar["close"]) > float(bar["open"])))
            if not directional_close:
                continue
            entry = float(current_price) if current_price > 0 else float(bar["close"])
            if not (s.m5_entry_zone[0] <= entry <= s.m5_entry_zone[1]):
                continue
            target = float(s.structural_target)
            buffer = max(a * self.params.sl_buffer_atr,
                         spread_price * 1.5, min_stop_distance)
            stop = float(s.raid_extreme) + buffer if bearish else float(s.raid_extreme) - buffer
            risk = abs(entry - stop)
            reward = (entry - target) if bearish else (target - entry)
            if risk <= 0 or reward <= 0:
                self._terminal(INVALIDATED, now, "structural target is not available beyond entry")
                return None
            cost = 2.0 * spread_price
            s.entry_price, s.stop_loss = entry, stop
            s.gross_r = reward / risk
            s.estimated_cost_r = cost / risk if risk else None
            s.net_r = (reward - cost) / (risk + cost)
            if not s.entry_emitted:
                s.entry_emitted = True
                s.transition(READY, at=(work["time"].iloc[k] if "time" in work.columns else now),
                             reason="M5 rejection, displacement, MSS and first fresh POI retest")
            return S01Result(True, s.state, s.setup_id, s.reference.direction,
                             entry, stop, target, s.selected_entry_model, "",
                             s.record())
        return None

    def step(self, df_m15_closed: pd.DataFrame, df_m5_closed: pd.DataFrame,
             df_d1_closed: pd.DataFrame, *, current_price: float = 0.0,
             spread_price: float = 0.0, min_stop_distance: float = 0.0,
             now: Any = None, session: str = "") -> S01Result:
        """Advance causally and return a signal only after the full contract."""
        if df_m15_closed is None or df_m15_closed.empty:
            return S01Result(False, self.state, self.setup.setup_id if self.setup else None,
                             rejection_reason="no M15 data",
                             telemetry=self.setup.record() if self.setup else None)
        df_m15 = df_m15_closed.reset_index(drop=True)
        df_m5 = (df_m5_closed.reset_index(drop=True)
                 if df_m5_closed is not None else pd.DataFrame())
        latest_m15 = _row_time(df_m15, len(df_m15) - 1)
        latest_m5 = _row_time(df_m5, len(df_m5) - 1) if len(df_m5) else None

        if self.setup is not None and self.setup.state in TERMINAL_STATES:
            terminal_ts = pd.Timestamp(self.setup.terminal_at) if self.setup.terminal_at is not None else None
            current_ts = pd.Timestamp(latest_m15)
            if terminal_ts is None or current_ts > terminal_ts:
                self.reset()

        if self.setup is None:
            reference = select_reference_candle(df_d1_closed, df_m15,
                                                self.symbol,
                                                current_price or None)
            if reference is not None:
                self._arm(reference, latest_m15)
            else:
                self._last_m15_time = latest_m15
                self._last_m5_time = latest_m5
                return S01Result(False, IDLE, None,
                                 rejection_reason="no untouched D1 reference",
                                 telemetry=None)

        s = self.setup
        assert s is not None
        tech = technical_snapshot(df_m15, self.params.m15_atr_period)
        s.current_price = float(current_price) if current_price > 0 else float(df_m15["close"].iloc[-1])
        s.spread_price = float(spread_price)
        s.atr14_m15 = tech.get("atr14_m15")
        s.adx14_m15 = tech.get("adx14_m15")
        if session:
            s.session = str(session)
        if s.profile is not None:
            s.distance_to_poc = abs(s.current_price - s.profile.poc)
            s.distance_to_vah = abs(s.current_price - s.profile.vah)
            s.distance_to_val = abs(s.current_price - s.profile.val)
        new_m15 = self._last_m15_time is None or pd.Timestamp(latest_m15) > pd.Timestamp(self._last_m15_time)
        new_m5 = self._last_m5_time is None or (latest_m5 is not None and pd.Timestamp(latest_m5) > pd.Timestamp(self._last_m5_time))

        if new_m15 and s.state not in TERMINAL_STATES:
            self._advance_m15(df_m15, spread_price, min_stop_distance)
            if s.state not in TERMINAL_STATES:
                reason = self._invalidated_by_market(df_m15, df_m5)
                if reason:
                    self._terminal(INVALIDATED, latest_m15, reason)
                elif s.raid_start is not None:
                    try:
                        if "time" in df_m15.columns and s.raid_extreme_time is not None:
                            age = int(max(0.0, (
                                pd.Timestamp(latest_m15) -
                                pd.Timestamp(s.raid_extreme_time)
                            ).total_seconds() / 900.0))
                        else:
                            raid_idx = int(s.raid_extreme_index or 0)
                            age = len(df_m15) - 1 - raid_idx
                        if age > self.params.max_setup_bars:
                            self._terminal(EXPIRED, latest_m15, "setup exceeded M15 lifetime")
                    except (TypeError, ValueError):
                        pass
        self._last_m15_time = latest_m15

        if new_m5 and s.state in {WAIT_M5_CONFIRMATION, READY} and not s.entry_emitted:
            result = self._m5_confirmation(df_m5, current_price, spread_price,
                                           min_stop_distance, now or latest_m5)
            if result is not None:
                self._last_m5_time = latest_m5
                self._save_state()
                return result
        self._last_m5_time = latest_m5
        self._save_state()
        return S01Result(False, s.state, s.setup_id, s.reference.direction,
                         rejection_reason=s.last_rejection_reason,
                         telemetry=s.record())

    def mark_executed(self, at: Any = None) -> None:
        if self.setup is not None and self.setup.state == READY:
            self.setup.transition(EXECUTED, at=at, reason="order accepted")
            self.setup.transition(COMPLETE, at=at, reason="setup consumed")
            self.setup.terminal_at = at
            self.history.append(self.setup.record())
            self._terminal_seen = True
            self._save_state()
