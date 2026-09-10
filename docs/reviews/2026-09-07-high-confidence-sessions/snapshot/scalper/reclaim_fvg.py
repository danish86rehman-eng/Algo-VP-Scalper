"""L-017: broken level -> displacement reclaim -> confirmed FVG -> return.

One closed-bar evaluator for live and replay. Reconstructing the bounded
150-bar structural context avoids an in-memory level reset on restart.
BUY and SELL use the same arithmetic after reflecting SELL prices.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

from scalper import decision_params as DP


@dataclass(frozen=True)
class ReclaimDecision:
    allow: bool
    reason: str
    active: bool = False
    level: Optional[float] = None
    zone_buffer: float = 0.0
    broken_at: Optional[str] = None
    reclaimed_at: Optional[str] = None
    fvg_low: Optional[float] = None
    fvg_high: Optional[float] = None
    fvg_formed_at: Optional[str] = None
    retest_at: Optional[str] = None
    direction: str = ""

    def record(self) -> dict:
        return asdict(self)


def entry_in_reclaim_zone(decision: Optional[ReclaimDecision], price: float) -> bool:
    """Final quote guard: a completed return never authorizes chasing away."""
    if decision is None or not decision.allow or not np.isfinite(price):
        return False
    if not decision.active:
        return True
    side = 1 if decision.direction == "BULLISH" else -1
    return (decision.fvg_low <= price <= decision.fvg_high
            and side * price >= side * decision.level + decision.zone_buffer)


def close_barriers(deals, magic: int) -> dict:
    """Origin-position attribution includes exits written by the Guardian.

    A partial exit also advances the barrier. Open-position dedup prevents
    re-entry until final settlement, whose later timestamp supersedes it.
    """
    origins = {d.position_id: d for d in deals
               if d.entry == mt5.DEAL_ENTRY_IN and d.magic == magic}
    result = {}
    for deal in deals:
        origin = origins.get(deal.position_id)
        if origin is None or deal.entry not in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY):
            continue
        direction = "BULLISH" if origin.type == mt5.DEAL_TYPE_BUY else "BEARISH"
        key = (origin.symbol, direction)
        at = datetime.fromtimestamp(deal.time, tz=timezone.utc)
        result[key] = max(result.get(key, at), at)
    return result


def _closed(frame, now: pd.Timestamp, minutes: int, count: int):
    if frame is None or not {"time", "open", "high", "low", "close"}.issubset(frame.columns):
        return None
    data = frame.copy()
    data["time"] = pd.to_datetime(data["time"], utc=True, errors="coerce")
    if data.time.isna().any() or not data.time.is_monotonic_increasing or data.time.duplicated().any():
        return None
    data = data[data.time + pd.Timedelta(minutes=minutes) <= now].tail(count).reset_index(drop=True)
    prices = data[["open", "high", "low", "close"]]
    if data.empty or not np.isfinite(prices.to_numpy()).all():
        return None
    if ((data.high < prices.max(axis=1)) | (data.low > prices.min(axis=1))).any():
        return None
    # Never let a stale terminal keep approving the last known setup.
    if now - (data.time.iloc[-1] + pd.Timedelta(minutes=minutes)) >= pd.Timedelta(minutes=minutes):
        return None
    return data


def evaluate_reclaim(direction: str, df_m15, df_m5, entry: float,
                     stop: float, target: float, now: datetime,
                     not_before: Optional[datetime] = None) -> ReclaimDecision:
    """Veto an existing candidate unless its broken-level sequence is complete.

    Levels must be confirmed BEFORE their break. Only levels inside the
    candidate's stop-to-TP1 corridor apply: nearest obstacle ahead of entry,
    otherwise nearest previously broken level behind it. No new trigger,
    stop, target or risk budget is manufactured by this gate.
    """
    if direction not in ("BULLISH", "BEARISH"):
        return ReclaimDecision(False, "RECLAIM_INVALID_DIRECTION")
    now = pd.Timestamp(now)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    m15 = _closed(df_m15, now, DP.TRIGGER_TF_MINUTES, DP.TRIGGER_BARS)
    m5 = _closed(df_m5, now, DP.CONFIRM_TF_MINUTES, DP.CONFIRM_BARS)
    n = DP.RECLAIM_SWING_BARS
    if m15 is None or m5 is None or len(m15) < DP.RECLAIM_ATR_PERIOD + 2*n + 2:
        return ReclaimDecision(False, "RECLAIM_DATA_UNAVAILABLE")
    side = 1 if direction == "BULLISH" else -1
    price, sl, tp = side * entry, side * stop, side * target
    if not np.isfinite([price, sl, tp]).all() or not sl < price < tp:
        return ReclaimDecision(False, "RECLAIM_INVALID_GEOMETRY")

    def reflected(df):
        o, c = side * df.open.to_numpy(), side * df.close.to_numpy()
        h = side * (df.high if side == 1 else df.low).to_numpy()
        l = side * (df.low if side == 1 else df.high).to_numpy()
        return o, h, l, c

    o, h, l, c = reflected(m15)
    previous = np.r_[c[0], c[:-1]]
    tr = np.maximum(h-l, np.maximum(abs(h-previous), abs(l-previous)))
    # ATR at each event excludes that event's own candle.
    atr = pd.Series(tr).rolling(DP.RECLAIM_ATR_PERIOD).mean().shift(1).to_numpy()
    levels = broken_levels(l, c, atr, sl, tp)
    if not levels:
        return ReclaimDecision(True, "RECLAIM_NO_BROKEN_LEVEL")
    ahead = [item for item in levels if item[0]+item[2] > price]
    level, broken, buffer = min(ahead, key=lambda x: x[0]) if ahead else max(levels, key=lambda x: x[0])
    context = dict(active=True, level=side*level, zone_buffer=buffer,
                   broken_at=m15.time.iloc[broken].isoformat(), direction=direction)

    def wait(reason, **extra):
        return ReclaimDecision(False, reason, **(context | extra))

    if price < level+buffer or c[-1] < level+buffer:
        return wait("RECLAIM_WAIT_LEVEL")
    barrier = pd.Timestamp(not_before) if not_before is not None else None
    if barrier is not None:
        barrier = barrier.tz_localize("UTC") if barrier.tzinfo is None else barrier.tz_convert("UTC")
    strong = []
    for j in range(max(broken+1, 1), len(m15)):
        closed_at = m15.time.iloc[j] + pd.Timedelta(minutes=DP.TRIGGER_TF_MINUTES)
        if barrier is not None and closed_at <= barrier:
            continue
        body, span = c[j]-o[j], h[j]-l[j]
        if (span > 0 and np.isfinite(atr[j]) and atr[j] > 0
                and body >= DP.RECLAIM_BODY_ATR*atr[j]
                and body/span >= DP.RECLAIM_BODY_FRACTION
                and (c[j]-l[j])/span >= DP.RECLAIM_CLOSE_LOCATION
                and l[j] <= level+buffer and c[j] > level+buffer):
            strong.append(j)
    if not strong:
        return wait("RECLAIM_WAIT_DISPLACEMENT")
    bar_duration = pd.Timedelta(minutes=DP.TRIGGER_TF_MINUTES)
    gaps = [j for j in strong if j+1 < len(m15)
            and m15.time.iloc[j]-m15.time.iloc[j-1] == bar_duration
            and m15.time.iloc[j+1]-m15.time.iloc[j] == bar_duration
            and l[j+1]-h[j-1] >= DP.RECLAIM_MIN_FVG_ATR*atr[j]
            and c[j+1] > level+buffer]
    if not gaps:
        return wait("RECLAIM_WAIT_FVG")
    j = gaps[-1]
    low, high = float(h[j-1]), float(l[j+1])
    formed = m15.time.iloc[j+1] + pd.Timedelta(minutes=DP.TRIGGER_TF_MINUTES)
    context.update(reclaimed_at=m15.time.iloc[j].isoformat(),
                   fvg_low=min(side*low, side*high), fvg_high=max(side*low, side*high),
                   fvg_formed_at=formed.isoformat())
    if now-formed >= pd.Timedelta(minutes=DP.RECLAIM_MAX_FVG_BARS*DP.TRIGGER_TF_MINUTES):
        return wait("RECLAIM_FVG_EXPIRED")
    if m5.time.iloc[0] > formed:
        return wait("RECLAIM_FVG_HISTORY_MISSING")
    if (c[j+2:] < level-buffer).any():
        return wait("RECLAIM_LEVEL_LOST")
    after = m5[m5.time >= formed].reset_index(drop=True)
    if after.empty:
        return wait("RECLAIM_WAIT_RETURN")
    if (after.time.iloc[0] != formed
            or (after.time.diff().iloc[1:] != pd.Timedelta(minutes=DP.CONFIRM_TF_MINUTES)).any()):
        # An absent candle could conceal an earlier touch or invalidation.
        return wait("RECLAIM_FVG_HISTORY_MISSING")
    ro, rh, rl, rc = reflected(after)
    if (rc < low).any() or (rc < level-buffer).any():
        return wait("RECLAIM_FVG_INVALIDATED")
    touches = np.flatnonzero((rl <= high) & (rh >= low))
    if not len(touches):
        return wait("RECLAIM_WAIT_RETURN")
    k = int(touches[0])
    if k != len(after)-1:
        return wait("RECLAIM_RETURN_STALE")
    # First return must close directionally inside the gap and above the zone.
    if not (rc[k] > ro[k] and low <= rc[k] <= high and rc[k] >= level+buffer):
        return wait("RECLAIM_RETURN_UNCONFIRMED")
    context["retest_at"] = after.time.iloc[k].isoformat()
    decision = ReclaimDecision(True, "RECLAIM_READY", **context)
    if not entry_in_reclaim_zone(decision, entry):
        return wait("RECLAIM_ENTRY_OUTSIDE_FVG")
    return decision


def broken_levels(l, c, atr, sl=-np.inf, tp=np.inf):
    """Confirmed then broken supports in reflected price space.

    Shared with M15 FVG target placement: trade up TO the first resistance,
    whereas L-017 requires reclaim when the proposed path crosses it.
    """
    n = DP.RECLAIM_SWING_BARS
    levels = []
    for pivot in range(n, len(l)-n):
        level = float(l[pivot])
        if not sl < level < tp:
            continue
        if not (level < min(l[pivot-n:pivot]) and level <= min(l[pivot+1:pivot+n+1])):
            continue
        broken, buffer = None, 0.0
        for j in range(pivot+n+1, len(l)):
            if not np.isfinite(atr[j]) or atr[j] <= 0:
                continue
            if broken is None:
                proposed = DP.RECLAIM_ZONE_ATR * atr[j]
                if c[j] < level-proposed:
                    broken, buffer = j, float(proposed)
            elif c[j] < level-buffer and c[j-1] >= level-buffer:
                broken = j  # a failed reclaim starts the sequence again
        if broken is not None:
            levels.append((level, broken, buffer))
    return levels
