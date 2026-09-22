"""Opt-in tracked M15 break / frozen POI / M5 rejection contract.

No broker calls. Live and replay use this same book; live supplies a durable
path and reconciled order/deal comments. FORMING is an internal break record,
not an entry permission: zone selection waits for candle three to close.
"""
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
import hashlib
import json
import os
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

from scalper import decision_params as DP
from scalper.reclaim_fvg import _closed
from scalper.trigger_engine import SATrigger

PENDING = {"FORMING", "WAIT_PULLBACK", "READY"}
PRIORITY = ("S01_REFERENCE_CANDLE_RAID_VP", "SESSION_SWEEP", "SWEEP_REJECTION", "HTF_CRT_SWEEP",
            "VP_LIQUIDITY_REACTION", "FVG_FILL", "BOS_RETEST", "JUDAS", "VALUE_AREA_FADE")


def utc(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("PULLBACK_UTC_REQUIRED")
    return stamp.tz_convert("UTC")


def native(trigger):
    return (getattr(trigger, "s01", None) is not None or
            any(getattr(trigger, name, None) is not None
                for name in ("htf_crt", "fvg_entry", "session_sweep")))


def legacy_required(enabled, tracked, trigger):
    # Preserve the old A/B contract exactly when the new model is off.
    return enabled and not tracked and trigger.session_sweep is None


@dataclass
class Setup:
    setup_id: str
    symbol: str
    direction: str
    pivot_at: str
    pivot: float
    break_at: str
    formed_at: str
    expires_at: str
    atr: float
    candle1_high: float
    candle1_low: float
    ob_valid: bool
    source: dict
    state: str = "FORMING"
    zone_low: float = 0.0
    zone_high: float = 0.0
    zone_kind: str = ""
    confirmed_at: str = ""
    cursor: str = ""
    reason: str = "BREAK_CONFIRMED"
    history_ready: bool = False

    @property
    def sign(self):
        return 1 if self.direction == "BULLISH" else -1

    def record(self):
        return asdict(self)

    def comment(self):
        # Keep the original Guardian family prefix; append reconciliation ID.
        return f"SA_{self.source['trigger_type'][:4]}_{self.setup_id}"

    def quote_allowed(self, price, now):
        if (self.state != "READY" or not self.history_ready
                or not np.isfinite(price) or not self.confirmed_at
                or not utc(self.confirmed_at) <= utc(now) < utc(self.confirmed_at)+pd.Timedelta(seconds=60)):
            return False
        low, high = self.zone_low, self.zone_high
        low -= DP.RECLAIM_ZONE_ATR*self.atr if self.sign < 0 else 0
        high += DP.RECLAIM_ZONE_ATR*self.atr if self.sign > 0 else 0
        return (low <= price <= high
                and self.sign*(price-self.source['stop_loss']) > 0
                and self.sign*(self.source['tp1']-price) > 0)

    def trigger(self, price):
        source = dict(self.source)
        if source.get('vplr') is not None:
            from scalper.vp_liquidity_trigger import VPLRSignal
            source['vplr'] = dict(source['vplr'])
            for key in ('mss_time', 'raid_time', 'anchor_time', 'anchor_extreme_time'):
                if source['vplr'].get(key):
                    source['vplr'][key] = utc(source['vplr'][key])
            source['vplr'] = VPLRSignal(**source['vplr'])
        return replace(SATrigger(**source), entry_price=price, pullback=self)


class Book:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.setups = {}
        self.seen = set()
        self.counts = Counter()
        self.reconciled = path is None
        if self.path and self.path.exists():
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
            payload = envelope['payload']
            if hashlib.sha256(payload.encode()).hexdigest() != envelope['sha256']:
                raise ValueError("PULLBACK_STORE_CHECKSUM")
            data = json.loads(payload)
            if data['version'] != 1:
                raise ValueError("PULLBACK_STORE_VERSION")
            self.seen = set(data['seen'])
            self.counts.update(data['counts'])
            for row in data['setups']:
                try:
                    setup = Setup(**row)
                    values = [setup.atr, setup.pivot, setup.zone_low, setup.zone_high,
                              setup.candle1_low, setup.candle1_high,
                              setup.source['entry_price'], setup.source['stop_loss'], setup.source['tp1']]
                    valid = (setup.direction in ('BULLISH', 'BEARISH')
                             and setup.state in PENDING | {'ATTEMPTED', 'INVALIDATED', 'EXPIRED', 'MISSED_ENTRY'}
                             and np.isfinite(values).all() and setup.atr > 0
                             and setup.candle1_low <= setup.candle1_high
                             and (setup.state == 'FORMING' or setup.zone_low <= setup.zone_high))
                except (KeyError, TypeError, ValueError):
                    valid = False
                if not valid:
                    raise ValueError("PULLBACK_STORE_MALFORMED")
                for name in ('break_at', 'formed_at', 'expires_at', 'cursor'):
                    utc(getattr(setup, name))
                setup.history_ready = False
                self.setups[setup.setup_id] = setup

    def save(self):
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(dict(version=1, seen=sorted(self.seen), counts=dict(self.counts),
                                  setups=[s.record() for s in self.setups.values()]),
                             sort_keys=True, allow_nan=False)
        envelope = dict(payload=payload, sha256=hashlib.sha256(payload.encode()).hexdigest())
        fd, temporary = tempfile.mkstemp(prefix=self.path.name, suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(envelope, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def transition(self, setup, state, reason):
        setup.state, setup.reason = state, reason
        self.counts[f'{state}:{reason}'] += 1

    def expire(self, setup, now):
        if setup.state == 'READY' and now >= utc(setup.confirmed_at)+pd.Timedelta(seconds=60):
            self.transition(setup, 'MISSED_ENTRY', 'READY_WINDOW_ELAPSED')
        elif setup.state in ('FORMING', 'WAIT_PULLBACK') and now >= utc(setup.expires_at):
            self.transition(setup, 'EXPIRED', 'SIX_HOURS')

    def reconcile(self, orders, deals):
        self.reconciled = False
        if orders is None or deals is None:
            return
        comments = {getattr(row, 'comment', '') for row in (*orders, *deals)}
        for setup in self.setups.values():
            marker = self.path and self.path.with_name(setup.setup_id+'.attempt')
            if setup.state in PENDING and (setup.comment() in comments or (marker and marker.exists())):
                self.transition(setup, 'ATTEMPTED', 'RECONCILED_OR_RESERVED')
        self.save()
        self.reconciled = True

    def observe(self, symbol, trigger, m15, now):
        """Capture only a newly closed displacement, never rediscover old legs."""
        if not trigger.detected or native(trigger):
            return
        frame = _closed(m15, utc(now), 15, DP.TRIGGER_BARS)
        n = DP.RECLAIM_SWING_BARS
        if frame is None or len(frame) < DP.RECLAIM_ATR_PERIOD+2*n+2:
            self.counts['BREAK_DATA_UNAVAILABLE'] += 1
            return
        if trigger.direction not in ('BULLISH', 'BEARISH'):
            return
        sign = 1 if trigger.direction == 'BULLISH' else -1
        o, c = sign*frame.open.to_numpy(), sign*frame.close.to_numpy()
        h = sign*(frame.high if sign > 0 else frame.low).to_numpy()
        l = sign*(frame.low if sign > 0 else frame.high).to_numpy()
        j = len(frame)-1
        previous = np.r_[c[0], c[:-1]]
        tr = np.maximum(h-l, np.maximum(abs(h-previous), abs(l-previous)))
        atr = float(np.mean(tr[j-DP.RECLAIM_ATR_PERIOD:j]))
        pivots = [k for k in range(n, j-n) if h[k] > max(h[k-n:k])
                  and h[k] >= max(h[k+1:k+n+1])]
        if not pivots or atr <= 0:
            self.counts['NO_CONFIRMED_SWING'] += 1
            return
        k = pivots[-1]
        evidence_start = min(k-n, j-DP.RECLAIM_ATR_PERIOD)
        if (frame.time.diff().iloc[evidence_start+1:j+1] != pd.Timedelta(minutes=15)).any():
            self.counts['BREAK_HISTORY_MISSING'] += 1
            return
        level = h[k]
        span, body = h[j]-l[j], c[j]-o[j]
        if not (span > 0 and body >= DP.RECLAIM_BODY_ATR*atr
                and body/span >= DP.RECLAIM_BODY_FRACTION
                and (c[j]-l[j])/span >= DP.RECLAIM_CLOSE_LOCATION
                and c[j-1] <= level+DP.RECLAIM_ZONE_ATR*atr
                and c[j] > level+DP.RECLAIM_ZONE_ATR*atr):
            self.counts['NO_DISPLACED_BREAK'] += 1
            return
        broken = frame.time.iloc[j]+pd.Timedelta(minutes=15)
        key = '|'.join((symbol, trigger.direction, frame.time.iloc[k].isoformat(), broken.isoformat()))
        if key in self.seen:
            self.counts['REPEATED_WAITING_OBSERVATIONS'] += 1
            return
        self.seen.add(key)  # Suppressed breaks cannot resurrect later.
        if any(s.symbol == symbol and s.direction == trigger.direction and s.state in PENDING
               for s in self.setups.values()):
            self.counts['SUPPRESSED_UNIQUE_BREAKS'] += 1
            self.save()
            return
        if not sign*trigger.stop_loss < sign*trigger.entry_price < sign*trigger.tp1:
            self.counts['INVALID_SOURCE_GEOMETRY'] += 1
            self.save()
            return
        source = asdict(trigger)
        source['pullback'] = None
        if source.get('vplr'):
            for name, value in source['vplr'].items():
                if isinstance(value, pd.Timestamp):
                    source['vplr'][name] = value.isoformat()
        sid = hashlib.sha256(key.encode()).hexdigest()[:20]
        self.setups[sid] = Setup(sid, symbol, trigger.direction, frame.time.iloc[k].isoformat(),
            float(sign*level), broken.isoformat(), (broken+pd.Timedelta(minutes=15)).isoformat(),
            (broken+pd.Timedelta(hours=6)).isoformat(), atr,
            float(frame.high.iloc[j-1]), float(frame.low.iloc[j-1]), bool(c[j-1] < o[j-1]),
            source, cursor=broken.isoformat())
        self.counts['UNIQUE_BREAKS'] += 1
        self.save()

    def filter_candidate(self, symbol, trigger, m15, now):
        self.observe(symbol, trigger, m15, now)
        return trigger if native(trigger) else None

    def advance(self, symbol, m15, m5, now, bid=None, ask=None):
        now = utc(now)
        if not any(s.symbol == symbol and s.state in PENDING for s in self.setups.values()):
            return
        small = _closed(m5, now, 5, DP.CONFIRM_BARS)
        large = _closed(m15, now, 15, DP.TRIGGER_BARS)
        for s in list(self.setups.values()):
            if s.symbol != symbol or s.state not in PENDING:
                continue
            s.history_ready = False
            if small is None or large is None:
                s.reason = 'DATA_UNAVAILABLE'
                self.expire(s, now)
                continue
            if s.state == 'FORMING' and now >= utc(s.formed_at):
                third = large[large.time == utc(s.break_at)]
                if third.empty:
                    s.reason = 'FORMATION_HISTORY_MISSING'
                    self.expire(s, now)
                    continue
                third = third.iloc[0]
                low, high = ((s.candle1_high, float(third.low)) if s.sign > 0
                             else (float(third.high), s.candle1_low))
                if high-low >= DP.RECLAIM_MIN_FVG_ATR*s.atr:
                    s.zone_low, s.zone_high, s.zone_kind = low, high, 'FVG'
                elif s.ob_valid:
                    s.zone_low, s.zone_high, s.zone_kind = s.candle1_low, s.candle1_high, 'OB'
                else:
                    self.transition(s, 'INVALIDATED', 'NO_FVG_OR_IMMEDIATE_OB')
                    continue
                old_id = s.setup_id
                s.setup_id = hashlib.sha256(
                    f'{old_id}|{s.zone_kind}|{s.zone_low}|{s.zone_high}'.encode()).hexdigest()[:20]
                del self.setups[old_id]
                self.setups[s.setup_id] = s
                self.transition(s, 'WAIT_PULLBACK', 'ZONE_FROZEN')
                self.counts['UNIQUE_SETUPS'] += 1
            after = small[small.time >= utc(s.cursor)]
            expected = pd.date_range(utc(s.cursor), now.floor('5min')-pd.Timedelta(minutes=5), freq='5min')
            if list(after.time) != list(expected):
                s.reason = 'INTERVENING_HISTORY_MISSING'
                self.expire(s, now)
                continue
            for row in after.itertuples():
                stamp = row.time+pd.Timedelta(minutes=5)
                stop, target = s.source['stop_loss'], s.source['tp1']
                stop_hit = row.low <= stop if s.sign > 0 else row.high >= stop
                target_hit = row.high >= target if s.sign > 0 else row.low <= target
                distal = (row.close < s.zone_low if s.sign > 0 else row.close > s.zone_high)
                zone_available = row.time >= utc(s.formed_at)
                if stop_hit or target_hit or (zone_available and s.zone_kind and distal):
                    self.transition(s, 'INVALIDATED', 'STOP' if stop_hit else 'TARGET' if target_hit else 'DISTAL_CLOSE')
                    break
                if s.state != 'READY' and stamp >= utc(s.expires_at):
                    self.transition(s, 'EXPIRED', 'SIX_HOURS')
                    break
                if s.state == 'READY' and stamp >= utc(s.confirmed_at)+pd.Timedelta(seconds=60):
                    self.transition(s, 'MISSED_ENTRY', 'READY_WINDOW_ELAPSED')
                    break
                midpoint = (s.zone_low+s.zone_high)/2
                touch = row.low <= s.zone_high and row.high >= s.zone_low
                deep = row.low <= midpoint if s.sign > 0 else row.high >= midpoint
                if s.state == 'WAIT_PULLBACK' and zone_available and touch:
                    if deep and s.sign*(row.close-row.open) > 0 and s.sign*(row.close-midpoint) > 0:
                        s.confirmed_at = stamp.isoformat()
                        self.transition(s, 'READY', 'M5_REJECTION')
                    else:
                        self.counts['INCONCLUSIVE_TOUCHES'] += 1
                s.cursor = stamp.isoformat()
            s.history_ready = s.state in PENDING
            # Live quote invalidation takes precedence over timer expiry.
            if s.state in PENDING and bid is not None and ask is not None:
                if np.isfinite([bid, ask]).all() and 0 < bid <= ask:
                    q = bid if s.sign > 0 else ask
                    if s.sign*(q-s.source['stop_loss']) <= 0 or s.sign*(q-s.source['tp1']) >= 0:
                        self.transition(s, 'INVALIDATED', 'QUOTE_STOP_OR_TARGET')
            self.expire(s, now)
        self.save()

    def select(self, symbol, original, bid, ask, now, allowed=None):
        candidates = [original] if original.detected and native(original) else []
        if self.reconciled:
            for s in self.setups.values():
                q = ask if s.sign > 0 else bid
                if (s.symbol == symbol and (allowed is None or s.source['trigger_type'] in allowed)
                        and s.quote_allowed(q, now)):
                    candidates.append(s.trigger(q))
        return min(candidates, key=lambda t: PRIORITY.index(t.trigger_type)) if candidates else SATrigger()

    def reserve(self, setup_id, price, now):
        s = self.setups[setup_id]
        if not self.reconciled or not s.quote_allowed(price, now):
            return False
        if self.path:
            marker = self.path.with_name(setup_id+'.attempt')
            try:
                with marker.open('x', encoding='utf-8') as handle:
                    handle.write(utc(now).isoformat())
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError:
                self.transition(s, 'ATTEMPTED', 'EXISTING_RESERVATION')
                self.save()
                return False
        self.transition(s, 'ATTEMPTED', 'DURABLE_SUBMISSION_RESERVATION')
        self.save()  # Failure prevents order_send; marker still consumes attempt.
        return True
