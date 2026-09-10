"""L-019: prior D1/W1/MN1 liquidity raid -> M15 reclaim -> FVG return.

Pure decision path shared by live and replay. HTF candles supply frozen ranges,
never the current candle's final OHLC. Entries use executable quotes, not lows.
"""
from dataclasses import asdict, dataclass, replace
from hashlib import sha256

import numpy as np
import pandas as pd

from scalper import decision_params as DP
from scalper.reclaim_fvg import _closed, broken_levels
from scalper.m15_fvg_entry import entry_quote_allowed
from scalper.crt_confluence import assess_confluence, confirmation_current, validate_mode


@dataclass(frozen=True)
class CRTPlan:
    allow: bool = False
    reason: str = "CRT_WATCH"
    timeframe: str = ""
    direction: str = ""
    anchor_at: str = ""
    range_low: float = 0.
    range_high: float = 0.
    swept_level: float = 0.
    level_buffer: float = 0.
    raid_at: str = ""
    raid_extreme: float = 0.
    reclaimed_at: str = ""
    formed_at: str = ""
    setup_id: str = ""
    entry: float = 0.
    stop: float = 0.
    target: float = 0.
    target_level: float = 0.
    target_buffer: float = 0.
    fvg_low: float = 0.
    fvg_high: float = 0.
    confluence: dict | None = None

    def record(self):
        return asdict(self)


def period_end(start, timeframe):
    """Keep the broker's week origin; use calendar months, never 30 days."""
    start = pd.Timestamp(start)
    if timeframe == "MN1":
        return start + pd.offsets.MonthBegin(1)
    return start + pd.Timedelta(days=7 if timeframe == "W1" else 1)


def closed_ranges(frame, timeframe, now):
    if frame is None or frame.empty:
        return None
    try:
        f = frame[["time", "open", "high", "low", "close"]].copy()
        f["time"] = pd.to_datetime(f.time, utc=True)
        if f.time.isna().any() or not f.time.is_monotonic_increasing or f.time.duplicated().any():
            return None
        # At most one opened-but-forming HTF candle. Match the bounded live
        # fetch before doing calendar arithmetic on a long replay history.
        f = f[f.time <= now].tail(DP.CRT_RANGE_BARS+1)
        f = f[[period_end(t, timeframe) <= now for t in f.time]]
        a = f[["open", "high", "low", "close"]].to_numpy(dtype=float)
        if (not np.isfinite(a).all() or (f.high <= f.low).any()
                or (f.high < f[["open", "close"]].max(axis=1)).any()
                or (f.low > f[["open", "close"]].min(axis=1)).any()):
            return None
        return f.tail(DP.CRT_RANGE_BARS).reset_index(drop=True)
    except (KeyError, ValueError, TypeError):
        return None


def used_setups(deals, magic):
    """Broker entry comments survive Guardian closes and process restarts."""
    return {d.comment[len(DP.CRT_ORDER_PREFIX):] for d in deals
            if d.magic == magic and d.entry == 0
            and getattr(d, "comment", "").startswith(DP.CRT_ORDER_PREFIX)}


def crt_quote_allowed(plan, price, now=None):
    if not entry_quote_allowed(plan, price) or not confirmation_current(plan, now):
        return False
    side = 1 if plan.direction == "BULLISH" else -1
    return (side*(price-plan.swept_level) >= plan.level_buffer
            and side*(plan.target-price) >= DP.CRT_MIN_R*side*(price-plan.stop))


def _evaluate(timeframe, direction, ranges, m15, m5, quote, now, used, last_close, symbol):
    base = CRTPlan(timeframe=timeframe, direction=direction)
    if ranges is None or ranges.empty:
        return replace(base, reason="CRT_HTF_DATA_UNAVAILABLE")
    if now >= period_end(period_end(ranges.time.iloc[-1], timeframe), timeframe):
        return replace(base, reason="CRT_HTF_DATA_STALE")
    side = 1 if direction == "BULLISH" else -1
    o, c = side*m15.open.to_numpy(), side*m15.close.to_numpy()
    h = side*(m15.high if side == 1 else m15.low).to_numpy()
    l = side*(m15.low if side == 1 else m15.high).to_numpy()
    prev = np.r_[c[0], c[:-1]]
    tr = np.maximum(h-l, np.maximum(abs(h-prev), abs(l-prev)))
    atr = pd.Series(tr).rolling(DP.RECLAIM_ATR_PERIOD).mean().shift(1).to_numpy()
    bar = pd.Timedelta(minutes=15)
    times = list(m15.time)
    result = base
    # The last two anchors cover a day/week/month rollover. The time condition
    # below associates each raid with the range actually known at that time.
    for row in ranges.tail(2).itertuples():
        start, end = period_end(row.time, timeframe), period_end(period_end(row.time, timeframe), timeframe)
        level, opposite = ((row.low, row.high) if side == 1 else (-row.high, -row.low))
        p = replace(base, anchor_at=row.time.isoformat(), range_low=row.low,
                    range_high=row.high, swept_level=side*level)
        key = f"{symbol}:{timeframe}:{row.time.isoformat()}:{direction}"
        p = replace(p, setup_id=sha256(key.encode()).hexdigest()[:16])
        raid, extreme, buffer, reclaim = None, None, None, None
        for i in range(DP.RECLAIM_ATR_PERIOD, len(m15)):
            t = times[i]
            if raid is None:
                if not start <= t < end:
                    continue
                if not (np.isfinite(atr[i]) and atr[i] > 0):
                    continue
                # Crossing from inside distinguishes a raid from a market
                # already accepted outside the old range at data-window start.
                if c[i-1] >= level and l[i] < level-DP.CRT_LEVEL_ATR*atr[i]:
                    raid, extreme, buffer = i, float(l[i]), DP.CRT_LEVEL_ATR*atr[i]
                    p = replace(p, raid_at=t.isoformat(), raid_extreme=side*extreme, level_buffer=buffer,
                                reason="CRT_SWEPT_WAIT_DISPLACEMENT")
                else:
                    continue
            if i > raid and t-times[i-1] != bar:
                p = replace(p, reason="CRT_HISTORY_MISSING")
                break
            if h[i] >= opposite:  # Both sides raided / range delivery exhausted.
                p = replace(p, reason="CRT_OPPOSITE_EDGE_REACHED")
                break
            if reclaim is None:
                extreme = min(extreme, float(l[i]))
                p = replace(p, raid_extreme=side*extreme)
                span, body = h[i]-l[i], c[i]-o[i]
                # A close-back-inside can precede the strong candle; its open
                # need not still be outside the range. No anchor-colour gate.
                if (span > 0 and c[i] > level+buffer and c[i] < opposite
                        and body >= DP.RECLAIM_BODY_ATR*atr[i]
                        and body/span >= DP.RECLAIM_BODY_FRACTION
                        and (c[i]-l[i])/span >= DP.RECLAIM_CLOSE_LOCATION):
                    reclaim = i
                    p = replace(p, reclaimed_at=(t+bar).isoformat(), reason="CRT_WAIT_FVG")
                continue
            # The three-candle gap must be created by that reclaim candle.
            j = reclaim
            if i != j+1:
                break
            formed = t+bar
            low, high = float(h[j-1]), float(l[i])
            if (times[j]-times[j-1] != bar
                    or high-low < DP.RECLAIM_MIN_FVG_ATR*atr[j]):
                p = replace(p, reason="CRT_RECLAIM_NO_FVG")
                reclaim = None
                continue
            p = replace(p, formed_at=formed.isoformat(),
                        fvg_low=min(side*low, side*high), fvg_high=max(side*low, side*high))
            if now-formed >= DP.CRT_MAX_ENTRY_BARS*bar:
                p = replace(p, reason="CRT_EXPIRED")
                break
            after = m5[m5.time >= formed]
            if (after.empty or after.time.iloc[0] != formed
                    or (after.time.diff().iloc[1:] != pd.Timedelta(minutes=5)).any()):
                p = replace(p, reason="CRT_WAIT_RETURN_HISTORY")
                break
            distal = side*(after.low if side == 1 else after.high)
            if ((distal <= low).any() or (side*after.close < level-buffer).any()
                    or (side*(after.high if side == 1 else after.low) >= opposite).any()):
                p = replace(p, reason="CRT_INVALIDATED")
                break
            if p.setup_id in used or (last_close is not None and pd.Timestamp(p.raid_at) <= pd.Timestamp(last_close)):
                p = replace(p, reason="CRT_SETUP_USED")
                break
            price = side*quote
            p = replace(p, entry=quote, reason="CRT_WAIT_FVG_RETURN")
            if not (np.isfinite(price) and low <= price <= high and price >= level+buffer):
                break
            # Midpoint first, then opposite edge only after midpoint was
            # already surpassed. Never skip a nearer broken structural level
            # merely to manufacture 2R.
            midpoint = (level+opposite)/2
            target_level = midpoint if midpoint > price else opposite
            obstacles = broken_levels(l, c, atr, price)
            target_buffer = buffer
            if obstacles:
                near, _, near_buffer = min(obstacles, key=lambda x: x[0])
                if near < target_level:
                    target_level, target_buffer = near, near_buffer
            stop = extreme-DP.CRT_STOP_ATR*atr[j]
            target = target_level-target_buffer
            p = replace(p, stop=side*stop, target=side*target,
                        target_level=side*target_level, target_buffer=target_buffer)
            if price-stop <= 0 or target-price < DP.CRT_MIN_R*(price-stop):
                p = replace(p, reason="CRT_TARGET_R_TOO_SMALL")
                break
            p = replace(p, allow=True, reason="CRT_READY")
            break
        if p.raid_at or not result.raid_at:
            result = p
    return result


def watch_crt(df_m15, df_m5, frames, bid, ask, now, used=(), last_closes=None, symbol="",
              confluence_mode=DP.CRT_CONFLUENCE_MODE):
    """Six explicit watch states; calendar-causal source selection in both paths."""
    validate_mode(confluence_mode)
    now = pd.Timestamp(now)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    m15 = _closed(df_m15, now, 15, DP.TRIGGER_BARS)
    m5 = _closed(df_m5, now, 5, DP.CONFIRM_BARS)
    if m15 is None or m5 is None or len(m15) < DP.RECLAIM_ATR_PERIOD+3:
        return [CRTPlan(reason="CRT_LTF_DATA_UNAVAILABLE")]
    last_closes = last_closes or {}
    plans = []
    for tf in DP.CRT_TIMEFRAMES:
        ranges = closed_ranges(frames.get(tf), tf, now)
        for direction, quote in (("BULLISH", ask), ("BEARISH", bid)):
            plan = _evaluate(tf, direction, ranges, m15, m5, quote, now,
                             used, last_closes.get((symbol, direction)), symbol)
            plans.append(assess_confluence(plan, m15, m5, now, confluence_mode))
    return plans


def select_crt(plans):
    """Conflicting ready directions abstain; otherwise MN1 > W1 > D1."""
    ready = [p for p in plans if p.allow]
    if len({p.direction for p in ready}) > 1:
        return CRTPlan(reason="CRT_DIRECTION_CONFLICT")
    return ready[0] if ready else CRTPlan(reason="CRT_NOT_READY")
