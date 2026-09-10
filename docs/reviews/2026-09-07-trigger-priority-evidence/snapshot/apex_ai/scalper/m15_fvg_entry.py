"""L-018: executable M15 FVG touch, with TP before broken structure.

Only closed bars establish the gap and its history. The current executable
quote supplies the entry; a historical candle's low is never used as a fill.
"""
from dataclasses import asdict, dataclass
from typing import Optional

import numpy as np
import pandas as pd

from scalper import decision_params as DP
from scalper.reclaim_fvg import _closed, broken_levels


@dataclass(frozen=True)
class FVGEntry:
    allow: bool
    reason: str
    direction: str = ""
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    fvg_low: float = 0.0
    fvg_high: float = 0.0
    formed_at: Optional[str] = None
    displacement_at: Optional[str] = None
    target_level: float = 0.0
    target_buffer: float = 0.0

    def record(self):
        return asdict(self)


def entry_quote_allowed(plan: FVGEntry, price: float) -> bool:
    if plan is None or not plan.allow or not np.isfinite(price):
        return False
    side = 1 if plan.direction == "BULLISH" else -1
    risk, reward = side*(price-plan.stop), side*(plan.target-price)
    return (plan.fvg_low <= price <= plan.fvg_high and risk > 0
            and reward >= DP.M15_FVG_MIN_R*risk
            and side*price <= side*plan.entry)  # no adverse chase after sizing


def evaluate_fvg_entry(direction, df_m15, df_m5, quote, now,
                       last_close=None) -> FVGEntry:
    if direction not in ("BULLISH", "BEARISH") or not np.isfinite(quote):
        return FVGEntry(False, "M15_FVG_INVALID_QUOTE")
    now = pd.Timestamp(now)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    m15 = _closed(df_m15, now, DP.TRIGGER_TF_MINUTES, DP.TRIGGER_BARS)
    m5 = _closed(df_m5, now, DP.CONFIRM_TF_MINUTES, DP.CONFIRM_BARS)
    if m15 is None or m5 is None or len(m15) < DP.RECLAIM_ATR_PERIOD+3:
        return FVGEntry(False, "M15_FVG_DATA_UNAVAILABLE", direction)
    side = 1 if direction == "BULLISH" else -1
    o, c = side*m15.open.to_numpy(), side*m15.close.to_numpy()
    h = side*(m15.high if side == 1 else m15.low).to_numpy()
    l = side*(m15.low if side == 1 else m15.high).to_numpy()
    price = side*quote
    previous = np.r_[c[0], c[:-1]]
    tr = np.maximum(h-l, np.maximum(abs(h-previous), abs(l-previous)))
    atr = pd.Series(tr).rolling(DP.RECLAIM_ATR_PERIOD).mean().shift(1).to_numpy()
    barriers = broken_levels(l, c, atr, price)
    duration = pd.Timedelta(minutes=DP.TRIGGER_TF_MINUTES)
    reason = "M15_FVG_WAIT_PRICE"
    # Newest qualified overlapping gap has priority. A nearer structural
    # obstacle is never skipped to manufacture enough reward.
    for j in range(len(m15)-2, DP.RECLAIM_ATR_PERIOD-1, -1):
        formed = m15.time.iloc[j+1]+duration
        if now-formed >= DP.RECLAIM_MAX_FVG_BARS*duration:
            break
        if (m15.time.iloc[j]-m15.time.iloc[j-1] != duration
                or m15.time.iloc[j+1]-m15.time.iloc[j] != duration):
            continue
        body, span = c[j]-o[j], h[j]-l[j]
        if not (np.isfinite(atr[j]) and atr[j] > 0 and span > 0
                and body >= DP.RECLAIM_BODY_ATR*atr[j]
                and body/span >= DP.RECLAIM_BODY_FRACTION
                and (c[j]-l[j])/span >= DP.RECLAIM_CLOSE_LOCATION
                and l[j+1]-h[j-1] >= DP.RECLAIM_MIN_FVG_ATR*atr[j]):
            continue
        low, high = float(h[j-1]), float(l[j+1])
        if not low <= price <= high:
            continue
        after = m5[m5.time >= formed]
        if (m5.time.iloc[0] > formed
                or (not after.empty and (after.time.iloc[0] != formed
                    or (after.time.diff().iloc[1:] != pd.Timedelta(minutes=DP.CONFIRM_TF_MINUTES)).any()))):
            return FVGEntry(False, "M15_FVG_HISTORY_MISSING", direction)
        distal = side*(after.low if side == 1 else after.high)
        # Full mitigation, even by wick, consumes this gap. Partial visits
        # remain eligible until expiry; they are not fresh gap formations.
        if (distal <= low).any() or (l[j+2:] <= low).any():
            reason = "M15_FVG_CONSUMED"
            continue
        if last_close is not None:
            closed = pd.Timestamp(last_close)
            closed = closed.tz_localize("UTC") if closed.tzinfo is None else closed.tz_convert("UTC")
            departure = after[after.time >= closed]
            if formed <= closed and not (side*departure.close > high).any():
                return FVGEntry(False, "M15_FVG_WAIT_FRESH_RETURN", direction)
        if not barriers:
            return FVGEntry(False, "M15_FVG_NO_STRUCTURAL_TARGET", direction)
        level, _, buffer = min(barriers, key=lambda item: item[0])
        stop, target = low-DP.M15_FVG_STOP_ATR*atr[j], level-buffer
        context = dict(direction=direction, entry=quote, stop=side*stop,
                       target=side*target, fvg_low=min(side*low, side*high),
                       fvg_high=max(side*low, side*high), formed_at=formed.isoformat(),
                       displacement_at=m15.time.iloc[j].isoformat(),
                       target_level=side*level, target_buffer=buffer)
        if price-stop <= 0 or target-price < DP.M15_FVG_MIN_R*(price-stop):
            return FVGEntry(False, "M15_FVG_TARGET_R_TOO_SMALL", **context)
        return FVGEntry(True, "M15_FVG_READY", **context)
    return FVGEntry(False, reason, direction)


def find_fvg_entry(df_m15, df_m5, bid, ask, now, last_closes=None, symbol=""):
    """One shared selection call for live quotes and replay observations."""
    last_closes = last_closes or {}
    plans = [evaluate_fvg_entry(direction, df_m15, df_m5, price, now,
                               last_closes.get((symbol, direction)))
             for direction, price in (("BULLISH", ask), ("BEARISH", bid))]
    ready = [p for p in plans if p.allow]
    if ready:
        return max(ready, key=lambda p: p.formed_at)
    # Keep a structural rejection in preference to the other side's no-touch.
    return next((p for p in plans if p.reason != "M15_FVG_WAIT_PRICE"), plans[0])
