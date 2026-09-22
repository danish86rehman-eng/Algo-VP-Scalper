"""Completed-session sweep/reclaim/MSS reversal. No MT5 or order API.

BSL/SSL are chart-level proxies, not observations of actual broker stops.
The thresholds are a frozen research specification, not a fitted edge.
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
WINDOWS = {"ASIA": (time(19), time(22)), "LONDON": (time(2), time(5)),
           "NEW_YORK": (time(8, 20), time(11))}
EXPECTED_LOGIN = 40280210
EXPECTED_SERVER = "Exness-MT5Trial2"
DEFAULT_VAULT = r"D:\OneDrive - Orient Petroleum\Personal\Obsidian\AI-Trading-Strategies"
MAX_LEDGER_AGE = timedelta(minutes=45)
EVENT_LIFE = timedelta(minutes=60)
QUOTE_LIFE = timedelta(seconds=60)
ORDER_PREFIX = "SA_SS_"


@dataclass(frozen=True)
class Level:
    record_id: str
    session: str
    side: str
    price: float
    end: pd.Timestamp
    checked_at: pd.Timestamp
    swept_at: pd.Timestamp | None = None  # first breached M1 bar OPEN

    @property
    def setup_id(self):
        return hashlib.sha256(f"{self.record_id}:{self.side}".encode()).hexdigest()[:20]


@dataclass(frozen=True)
class Plan:
    allow: bool = False
    reason: str = "SESSION_SWEEP_WAIT"
    setup_id: str = ""
    session: str = ""
    side: str = ""
    direction: str = ""
    swept_level: float = 0.
    raid_at: str = ""
    reclaim_at: str = ""
    mss_at: str = ""
    pivot_at: str = ""
    pivot_known_at: str = ""
    pivot: float = 0.
    atr: float = 0.
    entry: float = 0.
    stop: float = 0.
    target: float = 0.
    target_level: float = 0.
    target_id: str = ""
    target_buffer: float = 0.
    quote_deadline: str = ""
    snapshot_at: str = ""

    def record(self):
        return asdict(self)


def utc(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise ValueError("SESSION_SWEEP_TIMESTAMP_NOT_UTC")
    return stamp.tz_convert("UTC")


def load_levels(vault, now, login, server):
    """Fail closed on stale, mixed-generation or wrong-account research data."""
    now = utc(now)
    if login != EXPECTED_LOGIN or server != EXPECTED_SERVER:
        raise ValueError("SESSION_SWEEP_ACCOUNT_MISMATCH")
    root = Path(vault)
    md = root / "journal/market/session-liquidity-ledger.md"
    before = md.read_text(encoding="utf-8-sig")
    payload = (root / "data/session-liquidity.csv").read_text(encoding="utf-8-sig")
    if before != md.read_text(encoding="utf-8-sig"):
        raise ValueError("SESSION_SWEEP_SNAPSHOT_CHANGED")
    def field(label):
        match = re.search(re.escape(label) + r": `([^`]+)`", before)
        if not match:
            raise ValueError("SESSION_SWEEP_METADATA_MISSING")
        return match.group(1)
    if int(field("MT5 account")) != login or field("MT5 server") != server:
        raise ValueError("SESSION_SWEEP_LEDGER_ACCOUNT_MISMATCH")
    updated = utc(field("Last updated"))
    if not timedelta(0) <= now - updated <= MAX_LEDGER_AGE:
        raise ValueError("SESSION_SWEEP_LEDGER_STALE")
    reader = csv.DictReader(io.StringIO(payload))
    levels, seen = [], set()
    for row in reader:
        name = row["session"]
        if name not in WINDOWS or row["symbol"] != "XAUUSD" or None in row:
            raise ValueError("SESSION_SWEEP_BAD_ROW")
        start, end, checked = map(utc, (row["start_utc"], row["end_utc"], row["updated_at_utc"]))
        date = datetime.fromisoformat(row["session_date_ny"]).date()
        expected_start, expected_end = (pd.Timestamp(datetime.combine(date, t, NY)).tz_convert("UTC")
                                        for t in WINDOWS[name])
        rid = row["record_id"]
        if (rid != f"XAUUSD:{name}:{start.isoformat()}" or rid in seen
                or start != expected_start or end != expected_end or end > checked
                or checked > updated or int(row["bars"]) != int((end-start).total_seconds()/60)):
            raise ValueError("SESSION_SWEEP_BAD_ID_WINDOW_OR_COVERAGE")
        seen.add(rid)
        hi, lo = float(row["high"]), float(row["low"])
        if not all(math.isfinite(v) and v > 0 for v in (hi, lo)) or hi <= lo:
            raise ValueError("SESSION_SWEEP_BAD_PRICE")
        for side, key, price in (("BSL", "high", hi), ("SSL", "low", lo)):
            status, swept = row[key+"_status"], row[key+"_swept_at_utc"]
            if status not in ("FRESH", "SWEPT") or bool(swept) != (status == "SWEPT"):
                raise ValueError("SESSION_SWEEP_BAD_STATE")
            swept = utc(swept) if swept else None
            if swept is not None and not end <= swept <= checked:
                raise ValueError("SESSION_SWEEP_BAD_SWEEP_TIME")
            if status == "FRESH" and checked != updated:
                raise ValueError("SESSION_SWEEP_BOUNDARY_STALE")
            levels.append(Level(rid, name, side, price, end, checked, swept))
    if not levels or max(p.checked_at for p in levels) != updated:
        raise ValueError("SESSION_SWEEP_EMPTY_OR_MIXED_SNAPSHOT")
    return levels


def closed_frame(frame, now, minutes=5):
    if frame is None or frame.empty:
        raise ValueError("SESSION_SWEEP_NO_BARS")
    df = frame.copy()
    df["time"] = [utc(v) for v in df["time"]]
    df = df[df.time + pd.Timedelta(minutes=minutes) <= utc(now)].reset_index(drop=True)
    if df.empty or not df.time.is_monotonic_increasing or df.time.duplicated().any():
        raise ValueError("SESSION_SWEEP_BAD_BAR_TIME")
    prices = df[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if (not np.isfinite(prices).all() or (prices <= 0).any()
            or (df.high < df[["open", "low", "close"]].max(axis=1)).any()
            or (df.low > df[["open", "high", "close"]].min(axis=1)).any()):
        raise ValueError("SESSION_SWEEP_BAD_OHLC")
    return df


def reconstruct_levels(m1):
    """Replay index from M1, never from today's CSV. as_of hides future events.

    Full-session coverage is mandatory. Precomputed extrema only become
    available at session end; first-sweep timestamps are masked at each clock.
    """
    if m1 is None or m1.empty:
        return []
    finish = utc(m1.time.iloc[-1]) + pd.Timedelta(minutes=1)
    df = closed_frame(m1, finish, 1)
    local_dates = df.time.dt.tz_convert(NY).dt.date
    result = []
    for date in sorted(set(local_dates)):
        for name, clocks in WINDOWS.items():
            start, end = (pd.Timestamp(datetime.combine(date, t, NY)).tz_convert("UTC") for t in clocks)
            owned = df[(df.time >= start) & (df.time < end)]
            expected = pd.date_range(start, end, freq="min", inclusive="left")
            if not pd.DatetimeIndex(owned.time).equals(expected):
                continue
            later = df[df.time >= end]
            for side, price in (("BSL", float(owned.high.max())), ("SSL", float(owned.low.min()))):
                taken = later[later.high > price] if side == "BSL" else later[later.low < price]
                first = None if taken.empty else taken.time.iloc[0]
                result.append(Level(f"XAUUSD:{name}:{start.isoformat()}", name, side,
                                    price, end, finish, first))
    return result


def as_of(index, now):
    from dataclasses import replace
    now = utc(now)
    return [replace(p, checked_at=now,
                    swept_at=p.swept_at if p.swept_at is not None
                    and p.swept_at + pd.Timedelta(minutes=1) <= now else None)
            for p in index if p.end <= now]


def _first_raid(level, df, now):
    if level.swept_at is not None:
        return level.swept_at.floor("5min")
    # The vault proved freshness up to checked_at; complete the intervening
    # history locally. Include its partial M5 bucket, now fully closed.
    start = max(level.end, level.checked_at.floor("5min"))
    if start < df.time.iloc[0] and level.checked_at < now:
        raise ValueError("SESSION_SWEEP_SNAPSHOT_GAP")
    recent = df[df.time >= start]
    taken = recent[recent.high > level.price] if level.side == "BSL" else recent[recent.low < level.price]
    return None if taken.empty else taken.time.iloc[0]


def quote_allowed(plan, price, now):
    if not plan.allow or not math.isfinite(price) or price <= 0:
        return False
    if not utc(plan.mss_at) <= utc(now) <= utc(plan.quote_deadline):
        return False
    sign = 1 if plan.direction == "BULLISH" else -1
    risk, reward = sign*(price-plan.stop), sign*(plan.target-price)
    return (sign*(price-plan.swept_level) > 0 and risk > 0 and reward >= 2*risk
            and abs(price-plan.entry) <= .10*plan.atr)


def evaluate(levels, m5, bid, ask, now, used=frozenset(), tick_size=.001):
    """Same pure detector in live and replay; only a new M5 close can signal."""
    now = utc(now)
    if not (all(math.isfinite(v) and v > 0 for v in (bid, ask, tick_size)) and ask >= bid):
        return Plan(reason="SESSION_SWEEP_NO_QUOTE")
    try:
        df = closed_frame(m5, now)
        age = now - (df.time.iloc[-1]+pd.Timedelta(minutes=5))
        if len(df) < 25 or age >= pd.Timedelta(minutes=5):
            return Plan(reason="SESSION_SWEEP_BARS_STALE")
        if age > QUOTE_LIFE:
            return Plan(reason="SESSION_SWEEP_WAIT_NEXT_M5_CLOSE")
        if any(p.checked_at > now or now-p.checked_at > MAX_LEDGER_AGE
               for p in levels if p.swept_at is None):
            return Plan(reason="SESSION_SWEEP_BOUNDARY_STALE")
        raids = {p.setup_id: _first_raid(p, df, now) for p in levels if p.end <= now}
    except (ValueError, KeyError, TypeError) as exc:
        return Plan(reason=str(exc))
    plans = []
    reason = "SESSION_SWEEP_WAIT"
    for level in levels:
        raid = raids.get(level.setup_id)
        if level.setup_id in used or raid is None or now-raid > EVENT_LIFE or raid < level.end:
            continue
        indexes = df.index[df.time == raid]
        if len(indexes) != 1 or indexes[0] < 20:
            reason = "SESSION_SWEEP_RAID_HISTORY_GAP"
            continue
        i = int(indexes[0])
        hist = df.iloc[:i]
        # No hidden missing candle between the frozen ATR/pivot and decision.
        if not (df.time.iloc[i-20:].diff().dropna() == pd.Timedelta(minutes=5)).all():
            reason = "SESSION_SWEEP_BAR_GAP"
            continue
        tr = pd.concat([hist.high-hist.low, (hist.high-hist.close.shift()).abs(),
                        (hist.low-hist.close.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.iloc[-14:].mean())
        sign = 1 if level.side == "SSL" else -1
        direction = "BULLISH" if sign == 1 else "BEARISH"
        raidbar = df.iloc[i]
        opposite = [p for p in levels if p.record_id == level.record_id and p.side != level.side]
        if any(raidbar.high > p.price if p.side == "BSL" else raidbar.low < p.price for p in opposite):
            reason = "SESSION_SWEEP_TWO_SIDED_RAID"
            continue
        depth = level.price-raidbar.low if sign == 1 else raidbar.high-level.price
        if atr <= 0 or depth < .25*atr:
            reason = "SESSION_SWEEP_SHALLOW_RAID"
            continue
        key = "high" if sign == 1 else "low"
        pivots = []
        for k in range(max(2, i-20), i-2):
            v = float(df[key].iloc[k])
            neighbors = df[key].iloc[[k-2, k-1, k+1, k+2]]
            is_pivot = (v > neighbors).all() if sign == 1 else (v < neighbors).all()
            if is_pivot:
                # A pivot already broken before the raid is not an MSS anchor.
                if not (sign*(df.close.iloc[k+3:i]-v) > 0).any():
                    pivots.append((k, v))
        if not pivots:
            reason = "SESSION_SWEEP_NO_FROZEN_PIVOT"
            continue
        k, pivot = pivots[-1]
        outside, reclaimed, confirmation, cancelled = 0, None, None, False
        extreme = float(raidbar.low if sign == 1 else raidbar.high)
        for j in range(i, len(df)):
            bar = df.iloc[j]
            outside = outside+1 if sign*(bar.close-level.price) < 0 else 0
            if outside >= 2:
                reason, cancelled = "SESSION_SWEEP_ACCEPTED_OUTSIDE", True
                break
            extreme = min(extreme, float(bar.low)) if sign == 1 else max(extreme, float(bar.high))
            if sign*(bar.close-level.price) > 0 and reclaimed is None:
                reclaimed = bar.time + pd.Timedelta(minutes=5)
            body, span = sign*(bar.close-bar.open), bar.high-bar.low
            location = (bar.close-bar.low if sign == 1 else bar.high-bar.close) / span if span > 0 else 0
            if (j > i and confirmation is None and reclaimed is not None
                    and sign*(bar.close-level.price) > 0 and sign*(bar.close-pivot) > 0
                    and sign*(df.close.iloc[j-1]-pivot) <= 0
                    and body >= .8*atr and span > 0 and body/span >= .6 and location >= .75):
                confirmation = j
        if cancelled:
            continue
        if confirmation is None:
            reason = "SESSION_SWEEP_WAIT_RECLAIM_MSS"
            continue
        if confirmation != len(df)-1:
            reason = "SESSION_SWEEP_CONFIRMATION_EXPIRED"
            continue
        spread = ask-bid
        buffer = max(.10*atr, 2*spread, 2*tick_size)
        entry = ask if sign == 1 else bid
        stop = extreme-sign*buffer
        targets = [p for p in levels if p.side != level.side and p.end <= now
                   and raids.get(p.setup_id) is None and sign*(p.price-entry) > 0]
        if not targets:
            reason = "SESSION_SWEEP_NO_FRESH_TARGET"
            continue
        target = min(targets, key=lambda p: abs(p.price-entry))
        target_price = target.price-sign*buffer
        risk, reward = sign*(entry-stop), sign*(target_price-entry)
        if risk <= 0 or reward < 2*risk or (reward-2*spread)/(risk+2*spread) < 1.5:
            reason = "SESSION_SWEEP_TARGET_TOO_CLOSE"
            continue
        stamp = df.time.iloc[confirmation] + pd.Timedelta(minutes=5)
        plan = Plan(True, "SESSION_SWEEP_READY", level.setup_id, level.session, level.side,
                    direction, level.price, raid.isoformat(), reclaimed.isoformat(), stamp.isoformat(),
                    df.time.iloc[k].isoformat(), (df.time.iloc[k]+pd.Timedelta(minutes=15)).isoformat(),
                    pivot, atr, entry, stop, target_price, target.price, target.setup_id, buffer,
                    min(stamp+QUOTE_LIFE, raid+EVENT_LIFE).isoformat(), level.checked_at.isoformat())
        if quote_allowed(plan, entry, now):
            plans.append(plan)
    if len({p.direction for p in plans}) > 1:
        return Plan(reason="SESSION_SWEEP_CONFLICT")
    return sorted(plans, key=lambda p: (-utc(p.raid_at).value, p.setup_id))[0] if plans else Plan(reason=reason)


def reserve_attempt(directory, setup_id):
    """One order attempt per boundary, durable before order_send (incl. timeout).

    O_EXCL prevents retries after uncertain submission and survives restarts.
    A failed reservation blocks submission. Never deletes earlier attempts.
    """
    if not re.fullmatch(r"[a-f0-9]{20}", setup_id):
        raise ValueError("SESSION_SWEEP_BAD_SETUP_ID")
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    try:
        with (path / (setup_id+".attempt")).open("x", encoding="utf-8") as handle:
            handle.write(datetime.now(UTC).isoformat())
        return True
    except FileExistsError:
        return False
