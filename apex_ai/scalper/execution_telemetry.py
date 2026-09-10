"""Immutable, observation-only execution telemetry for the scalper.

This module records broker quote and execution evidence.  It deliberately
exposes no allow/reject/resize/exit decision and no caller consumes its output.
All public recording methods return ``None``.
"""
from __future__ import annotations

import json
import math
import socket
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd


MARKOUT_SECONDS = (1, 5, 15, 30, 60)
RECOVERY_MINUTES = (1, 2, 5, 10, 20, 30, 60)
BANNED_DECISION_FIELDS = {"allow", "blocked", "veto", "approved", "skip"}
TELEMETRY_HOST = "127.0.0.1"
TELEMETRY_PORT = 55557
MAX_DATAGRAM_BYTES = 60_000


class NullExecutionTelemetry:
    """Fail-open recorder used when telemetry storage cannot initialize."""

    def record_candidate(self, *args, **kwargs) -> None:
        return None

    def record_entry(self, *args, **kwargs) -> None:
        return None

    def record_exit_submission(self, *args, **kwargs) -> None:
        return None

    def record_settled_exit(self, *args, **kwargs) -> None:
        return None

    def poll(self, *args, **kwargs) -> None:
        return None


def _wire_object(value: Any) -> Any:
    """Convert the small MT5 objects used by the client into JSON values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    if isinstance(value, dict):
        return {str(key): _wire_object(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire_object(item) for item in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return str(value)


def _fields(obj: Any, names: Iterable[str]) -> dict:
    return {name: _wire_object(_get(obj, name)) for name in names}


class DatagramTelemetryClient:
    """Nonblocking, best-effort emitter used by the trading process.

    The client performs no MT5 history query and no file write.  Local UDP is
    intentionally lossy: if the independent worker is unavailable or its
    receive buffer is full, trading continues and telemetry may be missing.
    """

    def __init__(self, host: str = TELEMETRY_HOST, port: int = TELEMETRY_PORT,
                 socket_factory=socket.socket):
        self.address = (host, int(port))
        self.socket = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setblocking(False)

    def _send(self, method: str, payload: dict) -> None:
        try:
            packet = json.dumps(
                {"protocol": "SA_TELEMETRY_UDP_V1", "method": method,
                 "payload": _wire_object(payload)},
                separators=(",", ":"), allow_nan=False).encode("utf-8")
            if len(packet) <= MAX_DATAGRAM_BYTES:
                self.socket.sendto(packet, self.address)
        except (OSError, TypeError, ValueError):
            pass
        return None

    def record_candidate(self, symbol: str, direction: str, trigger_type: str,
                         decision_tick: Any, decision_time: datetime,
                         context: dict) -> None:
        self._send("record_candidate", {
            "symbol": symbol, "direction": direction,
            "trigger_type": trigger_type,
            "decision_tick": _fields(decision_tick, ("bid", "ask", "time_msc")),
            "decision_time": decision_time, "context": context,
        })

    def record_entry(self, *, symbol: str, direction: str, stop_price: float,
                     decision_tick: Any, decision_time: datetime,
                     submit_time: datetime, confirm_time: datetime,
                     latency_ms: float, request: dict, result: Any,
                     context: dict, m5_frame: Any = None) -> None:
        self._send("record_entry", {
            "symbol": symbol, "direction": direction,
            "stop_price": stop_price,
            "decision_tick": _fields(decision_tick, ("bid", "ask", "time_msc")),
            "decision_time": decision_time, "submit_time": submit_time,
            "confirm_time": confirm_time, "latency_ms": latency_ms,
            "request": request,
            "result": _fields(result, (
                "retcode", "retcode_external", "request_id", "order", "deal",
                "price", "volume", "bid", "ask", "comment", "time_msc")),
            "context": context,
        })

    def record_exit_submission(self, *, ticket: int, symbol: str,
                               direction: str, exit_type: str,
                               reference_price: float, submit_time: datetime,
                               confirm_time: datetime, latency_ms: float,
                               request: dict, result: Any) -> None:
        self._send("record_exit_submission", {
            "ticket": ticket, "symbol": symbol, "direction": direction,
            "exit_type": exit_type, "reference_price": reference_price,
            "submit_time": submit_time, "confirm_time": confirm_time,
            "latency_ms": latency_ms, "request": request,
            "result": _fields(result, (
                "retcode", "retcode_external", "request_id", "order", "deal",
                "price", "volume", "comment", "time_msc")),
        })

    def record_settled_exit(self, *, ticket: int, info: dict,
                            deals: Iterable[Any], outcome: str,
                            observed_time: datetime) -> None:
        deal_fields = ("entry", "price", "volume", "time", "time_msc",
                       "ticket", "order", "position_id", "reason")
        self._send("record_settled_exit", {
            "ticket": ticket,
            "info": {key: _wire_object(info.get(key)) for key in (
                "symbol", "direction", "entry", "sl", "tp1",
                "exit_telemetry")},
            "deals": [_fields(deal, deal_fields) for deal in deals],
            "outcome": outcome, "observed_time": observed_time,
        })

    def poll(self, *args, **kwargs) -> None:
        return None


def _utc(value: Optional[datetime] = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _msc(value: datetime) -> int:
    return int(_utc(value).timestamp() * 1000)


def _iso_from_msc(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    dtype = getattr(obj, "dtype", None)
    names = getattr(dtype, "names", None)
    if names and name in names:
        value = obj[name]
        return value.item() if hasattr(value, "item") else value
    return getattr(obj, name, default)


def _float(value: Any) -> Optional[float]:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _percentile(values: list[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _tick_rows(ticks: Optional[Iterable[Any]]) -> list[dict]:
    rows = []
    if ticks is None:
        return rows
    for tick in ticks:
        bid = _float(_get(tick, "bid"))
        ask = _float(_get(tick, "ask"))
        stamp = _get(tick, "time_msc")
        if stamp is None:
            seconds = _float(_get(tick, "time"))
            stamp = int(seconds * 1000) if seconds is not None else None
        try:
            stamp = int(stamp)
        except (TypeError, ValueError):
            stamp = None
        if bid is None or ask is None or stamp is None or bid <= 0 or ask < bid:
            continue
        rows.append({"time_msc": stamp, "bid": bid, "ask": ask,
                     "mid": (bid + ask) / 2, "spread": ask - bid})
    rows.sort(key=lambda row: row["time_msc"])
    return rows


def _first_at_or_after(rows: list[dict], target_msc: int) -> Optional[dict]:
    return next((row for row in rows if row["time_msc"] >= target_msc), None)


def _median_spread(rows: list[dict], start_msc: int,
                   end_msc: int) -> Optional[float]:
    values = [row["spread"] for row in rows
              if start_msc <= row["time_msc"] < end_msc]
    return statistics.median(values) if values else None


def tick_native_snapshot(ticks: Optional[Iterable[Any]], decision_tick: Any,
                         decision_time: datetime, point: float,
                         stop_distance: float,
                         m5_range_atr: Optional[float] = None) -> dict:
    """Calculate arrival spread and activity from actual Bid/Ask ticks."""
    now_msc = _msc(decision_time)
    rows = _tick_rows(ticks)
    bid = _float(_get(decision_tick, "bid"))
    ask = _float(_get(decision_tick, "ask"))
    spread = ask - bid if bid is not None and ask is not None and ask >= bid else None
    pre60 = [row["spread"] for row in rows
             if now_msc - 3_600_000 <= row["time_msc"] < now_msc]
    baseline = statistics.median(pre60) if pre60 else None
    p90 = _percentile(pre60, .90)
    recent = [row for row in rows
              if now_msc - 300_000 <= row["time_msc"] < now_msc]
    prior_counts = []
    for bucket in range(1, 21):
        end = now_msc - bucket * 300_000
        start = end - 300_000
        prior_counts.append(sum(start <= row["time_msc"] < end for row in rows))
    count_median = statistics.median(prior_counts) if prior_counts else None
    abs_return = None
    if len(recent) >= 2 and recent[0]["mid"] > 0 and recent[-1]["mid"] > 0:
        abs_return = abs(math.log(recent[-1]["mid"] / recent[0]["mid"]))
    return {
        "spread_source": "BROKER_BID_ASK_TICKS",
        "spread_price": spread,
        "spread_points": spread / point if spread is not None and point > 0 else None,
        "spread_to_stop": spread / stop_distance
                          if spread is not None and stop_distance > 0 else None,
        "spread_vs_pre60_median": spread / baseline
                                  if spread is not None and baseline else None,
        "spread_vs_pre60_p90": spread / p90
                               if spread is not None and p90 else None,
        "pre60_spread_median": baseline,
        "pre60_spread_p90": p90,
        "quote_activity_label": "BROKER_ACTIVITY_PROXY",
        "quote_tick_count_5m": len(recent),
        "quote_tick_count_vs_pre20_median": len(recent) / count_median
                                             if count_median else None,
        "broker_impact_label": "BROKER_IMPACT_PROXY",
        "abs_log_mid_return_5m": abs_return,
        "abs_log_return_per_quote_tick_5m": abs_return / len(recent)
                                              if abs_return is not None and recent else None,
        "m5_range_atr": m5_range_atr,
    }


def technical_snapshot(frame: Any, period: int = 14) -> dict:
    """ATR/ADX/DIs and last-bar displacement from completed bars."""
    empty = {"atr14_m15": None, "adx14_m15": None, "plus_di": None,
             "minus_di": None, "displacement_atr": None}
    if frame is None or len(frame) < period + 2:
        return empty
    try:
        high = pd.to_numeric(frame["high"], errors="coerce")
        low = pd.to_numeric(frame["low"], errors="coerce")
        close = pd.to_numeric(frame["close"], errors="coerce")
        opened = pd.to_numeric(frame["open"], errors="coerce")
        tr = pd.concat(((high - low), (high - close.shift()).abs(),
                        (low - close.shift()).abs()), axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / period, adjust=False,
                     min_periods=period).mean()
        up = high.diff()
        down = -low.diff()
        plus_dm = up.where((up > down) & (up > 0), 0.0)
        minus_dm = down.where((down > up) & (down > 0), 0.0)
        plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False,
                                    min_periods=period).mean() / atr
        minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False,
                                      min_periods=period).mean() / atr
        denom = (plus_di + minus_di).replace(0, float("nan"))
        dx = 100 * (plus_di - minus_di).abs() / denom
        adx = dx.ewm(alpha=1 / period, adjust=False,
                     min_periods=period).mean()
        last_atr = _float(atr.iloc[-1])
        return {
            "atr14_m15": last_atr,
            "adx14_m15": _float(adx.iloc[-1]),
            "plus_di": _float(plus_di.iloc[-1]),
            "minus_di": _float(minus_di.iloc[-1]),
            "displacement_atr": abs(float(close.iloc[-1]) - float(opened.iloc[-1])) / last_atr
                                if last_atr else None,
        }
    except (KeyError, TypeError, ValueError, IndexError):
        return empty


def range_over_atr(frame: Any, period: int = 14) -> Optional[float]:
    if frame is None or len(frame) < period + 1:
        return None
    try:
        high = pd.to_numeric(frame["high"], errors="coerce")
        low = pd.to_numeric(frame["low"], errors="coerce")
        close = pd.to_numeric(frame["close"], errors="coerce")
        tr = pd.concat(((high - low), (high - close.shift()).abs(),
                        (low - close.shift()).abs()), axis=1).max(axis=1)
        atr = _float(tr.ewm(alpha=1 / period, adjust=False,
                            min_periods=period).mean().iloc[-1])
        return float(high.iloc[-1] - low.iloc[-1]) / atr if atr else None
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def bar_vwap(frame: Any) -> Optional[float]:
    """Broker tick-volume VWAP proxy from completed bars."""
    if frame is None or len(frame) == 0:
        return None
    try:
        volume_name = "tick_volume" if "tick_volume" in frame.columns else "volume"
        volume = pd.to_numeric(frame[volume_name], errors="coerce").fillna(0)
        typical = (pd.to_numeric(frame["high"], errors="coerce")
                   + pd.to_numeric(frame["low"], errors="coerce")
                   + pd.to_numeric(frame["close"], errors="coerce")) / 3
        total = float(volume.sum())
        return float((typical * volume).sum() / total) if total > 0 else None
    except (KeyError, TypeError, ValueError):
        return None


def entry_shortfall(direction: str, decision_bid: float, decision_ask: float,
                    fill_price: float) -> float:
    return (fill_price - decision_ask if direction == "BULLISH"
            else decision_bid - fill_price)


def exit_shortfall(direction: str, reference_price: float,
                   fill_price: float) -> float:
    return (reference_price - fill_price if direction == "BULLISH"
            else fill_price - reference_price)


def normalized_markout(direction: str, fill_price: float,
                       future_mid: float) -> float:
    return (future_mid - fill_price if direction == "BULLISH"
            else fill_price - future_mid)


class ExecutionTelemetry:
    """Append-only evidence recorder. Records are never read by strategy code."""

    def __init__(self, mt5_api: Any, log_path: str | Path,
                 pending_path: str | Path):
        self.mt5 = mt5_api
        self.log_path = Path(log_path)
        self.pending_path = Path(pending_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.pending_path.parent.mkdir(parents=True, exist_ok=True)
        self.pending = self._load_pending()

    def _load_pending(self) -> list[dict]:
        try:
            value = json.loads(self.pending_path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except (OSError, ValueError):
            return []

    def _save_pending(self) -> None:
        temporary = self.pending_path.with_suffix(self.pending_path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.pending, separators=(",", ":")),
                             encoding="utf-8")
        temporary.replace(self.pending_path)

    def _write(self, record: dict) -> None:
        if BANNED_DECISION_FIELDS.intersection(record):
            raise ValueError("execution telemetry cannot expose decision fields")
        envelope = {"schema": "SA_EXECUTION_TELEMETRY_V1",
                    "recorded_at_utc": _utc().isoformat(), **record}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(envelope, default=str,
                                    separators=(",", ":")) + "\n")

    def _provenance(self) -> dict:
        try:
            account = self.mt5.account_info()
        except Exception:
            account = None
        return {
            "broker": (_get(account, "company") or _get(account, "server")
                       or "UNKNOWN"),
            "account_mode": _get(account, "trade_mode", "UNKNOWN"),
            "source_clock": "UTC",
        }

    def record_candidate(self, symbol: str, direction: str, trigger_type: str,
                         decision_tick: Any, decision_time: datetime,
                         context: dict) -> None:
        self._write({
            "event": "CANDIDATE_OBSERVED", "symbol": symbol,
            "direction": direction, "trigger_type": trigger_type,
            "decision_time_utc": _utc(decision_time).isoformat(),
            "decision_time_msc": _msc(decision_time),
            "decision_bid": _float(_get(decision_tick, "bid")),
            "decision_ask": _float(_get(decision_tick, "ask")),
            "decision_mid": ((_float(_get(decision_tick, "bid"))
                              + _float(_get(decision_tick, "ask"))) / 2
                             if None not in (_float(_get(decision_tick, "bid")),
                                             _float(_get(decision_tick, "ask")))
                             else None),
            "dom_status": "UNKNOWN", **self._provenance(), **context,
        })

    def record_entry(self, *, symbol: str, direction: str, stop_price: float,
                     decision_tick: Any, decision_time: datetime,
                     submit_time: datetime, confirm_time: datetime,
                     latency_ms: float, request: dict, result: Any,
                     context: dict, m5_frame: Any = None) -> None:
        point = _float(_get(self.mt5.symbol_info(symbol), "point")) or 0.0
        from_time = _utc(decision_time) - timedelta(minutes=105)
        flags = getattr(self.mt5, "COPY_TICKS_INFO",
                        getattr(self.mt5, "COPY_TICKS_ALL", 0))
        ticks = self.mt5.copy_ticks_range(symbol, from_time,
                                          _utc(decision_time), flags)
        decision_bid = _float(_get(decision_tick, "bid"))
        decision_ask = _float(_get(decision_tick, "ask"))
        deal = None
        deal_ticket = _get(result, "deal")
        if deal_ticket:
            try:
                found = self.mt5.history_deals_get(ticket=deal_ticket)
                deal = found[0] if found else None
            except Exception:
                deal = None
        done_code = getattr(self.mt5, "TRADE_RETCODE_DONE", 10009)
        completed = _get(result, "retcode") == done_code
        result_price = (_float(_get(deal, "price"))
                        or (_float(_get(result, "price")) if completed else None))
        requested_price = _float(request.get("price"))
        fill_price = result_price
        risk_price = abs((decision_ask if direction == "BULLISH" else decision_bid)
                         - stop_price) if decision_bid is not None and decision_ask is not None else 0.0
        shortfall = entry_shortfall(direction, decision_bid, decision_ask, fill_price) \
            if None not in (decision_bid, decision_ask, fill_price) else None
        spread = (decision_ask - decision_bid
                  if decision_bid is not None and decision_ask is not None else None)
        snapshot = tick_native_snapshot(
            ticks, decision_tick, decision_time, point, risk_price,
            range_over_atr(m5_frame))
        context = dict(context)
        if (context.get("raid_depth_spreads") is None
                and context.get("raid_depth_atr") is not None
                and context.get("atr14_m15") is not None and spread):
            context["raid_depth_spreads"] = (
                context["raid_depth_atr"] * context["atr14_m15"] / spread)
        fill_msc = _get(deal, "time_msc") or _get(result, "time_msc")
        try:
            fill_msc = int(fill_msc)
        except (TypeError, ValueError):
            fill_msc = _msc(confirm_time)
        record = {
            "event": "ENTRY_EXECUTION", "symbol": symbol,
            "direction": direction,
            "decision_time_utc": _utc(decision_time).isoformat(),
            "decision_time_msc": _msc(decision_time),
            "submit_time_utc": _utc(submit_time).isoformat(),
            "submit_time_msc": _msc(submit_time),
            "fill_time_utc": _iso_from_msc(fill_msc),
            "fill_time_msc": fill_msc,
            "fill_time_source": ("BROKER_DEAL" if _get(deal, "time_msc")
                                  else "BROKER_RESULT" if _get(result, "time_msc")
                                  else "CONFIRMATION_CLOCK_FALLBACK"),
            "execution_latency_ms": latency_ms,
            "request_id": _get(result, "request_id"),
            "retcode": _get(result, "retcode"),
            "retcode_external": _get(result, "retcode_external"),
            "order_ticket": _get(result, "order"),
            "deal_ticket": deal_ticket,
            "requested_volume": _float(request.get("volume")),
            "filled_volume": _float(_get(deal, "volume")) or _float(_get(result, "volume")),
            "execution_mode": _get(self.mt5.symbol_info(symbol),
                                   "trade_exemode", "UNKNOWN"),
            "filling_mode": request.get("type_filling", "UNKNOWN"),
            "decision_bid": decision_bid, "decision_ask": decision_ask,
            "requested_price": requested_price, "fill_price": fill_price,
            "entry_shortfall_price": shortfall,
            "entry_shortfall_points": shortfall / point if shortfall is not None and point > 0 else None,
            "entry_shortfall_spread_units": shortfall / spread if shortfall is not None and spread else None,
            "entry_shortfall_R": shortfall / risk_price if shortfall is not None and risk_price else None,
            "dom_status": "UNKNOWN", **self._provenance(), **snapshot, **context,
        }
        self._write(record)
        if fill_price is not None:
            self.pending.append({"kind": "MARKOUT", "symbol": symbol,
                                 "direction": direction, "fill_price": fill_price,
                                 "fill_msc": fill_msc})
        anchor_msc = context.get("raid_time_msc") or _msc(decision_time)
        self.pending.append({"kind": "SPREAD_RECOVERY", "symbol": symbol,
                             "anchor_msc": int(anchor_msc),
                             "anchor_source": ("RAID_TIME" if context.get("raid_time_msc")
                                               else "ENTRY_DECISION_PROXY")})
        self._save_pending()

    def record_exit_submission(self, *, ticket: int, symbol: str,
                               direction: str, exit_type: str,
                               reference_price: float, submit_time: datetime,
                               confirm_time: datetime, latency_ms: float,
                               request: dict, result: Any) -> None:
        self._write({
            "event": "EXIT_SUBMISSION", "position_ticket": ticket,
            "symbol": symbol, "direction": direction, "exit_type": exit_type,
            "exit_reference_price": reference_price,
            "submit_time_utc": _utc(submit_time).isoformat(),
            "submit_time_msc": _msc(submit_time),
            "broker_confirmation_time_utc": _utc(confirm_time).isoformat(),
            "exit_latency_ms": latency_ms,
            "request_id": _get(result, "request_id"),
            "retcode": _get(result, "retcode"),
            "retcode_external": _get(result, "retcode_external"),
            "order_ticket": _get(result, "order"),
            "deal_ticket": _get(result, "deal"),
            "requested_volume": _float(request.get("volume")),
            "filled_volume": _float(_get(result, "volume")),
            "filling_mode": request.get("type_filling", "UNKNOWN"),
        })

    def record_settled_exit(self, *, ticket: int, info: dict,
                            deals: Iterable[Any], outcome: str,
                            observed_time: datetime) -> None:
        entry_out = getattr(self.mt5, "DEAL_ENTRY_OUT", 1)
        exits = [deal for deal in deals if _get(deal, "entry") == entry_out]
        if not exits:
            return
        deal = max(exits, key=lambda item: int(_get(item, "time_msc", 0) or 0))
        fill = _float(_get(deal, "price"))
        direction = info.get("direction", "UNKNOWN")
        submitted = info.get("exit_telemetry", {})
        exit_type = submitted.get("exit_type") or outcome or "BROKER_EXIT"
        reference = _float(submitted.get("reference_price"))
        deal_reason = _get(deal, "reason")
        if deal_reason == getattr(self.mt5, "DEAL_REASON_SL", object()):
            exit_type = "STOP_LOSS"
            reference = _float(info.get("sl"))
        elif deal_reason == getattr(self.mt5, "DEAL_REASON_TP", object()):
            exit_type = "TAKE_PROFIT"
            reference = _float(info.get("tp1"))
        elif reference is None and "LOSS" in str(outcome).upper():
            reference = _float(info.get("sl"))
            exit_type = "STOP_LOSS"
        if reference is None and "TP" in str(outcome).upper():
            reference = _float(info.get("tp1"))
            exit_type = "TAKE_PROFIT"
        shortfall = exit_shortfall(direction, reference, fill) \
            if None not in (reference, fill) else None
        point = _float(_get(self.mt5.symbol_info(info.get("symbol")), "point")) or 0.0
        risk_price = abs(float(info.get("entry", 0)) - float(info.get("sl", 0)))
        self._write({
            "event": "EXIT_SETTLED", "position_ticket": ticket,
            "symbol": info.get("symbol"), "direction": direction,
            "exit_type": exit_type, "outcome": outcome,
            "exit_reference_price": reference, "exit_fill_price": fill,
            "exit_shortfall_price": shortfall,
            "exit_shortfall_points": shortfall / point if shortfall is not None and point > 0 else None,
            "exit_shortfall_R": shortfall / risk_price if shortfall is not None and risk_price else None,
            "exit_latency_ms": submitted.get("latency_ms"),
            "exit_fill_time_msc": _get(deal, "time_msc"),
            "exit_fill_time_utc": _iso_from_msc(_get(deal, "time_msc")),
            "deal_ticket": _get(deal, "ticket"),
            "order_ticket": _get(deal, "order"),
            "deal_reason": deal_reason,
            "observed_time_utc": _utc(observed_time).isoformat(),
        })

    def poll(self, now: Optional[datetime] = None) -> None:
        """Backfill due markouts and spread recovery without blocking execution."""
        now = _utc(now)
        now_msc = _msc(now)
        remaining = []
        for item in self.pending:
            due_msc = (item["fill_msc"] + 60_000
                       if item.get("kind") == "MARKOUT"
                       else item.get("anchor_msc", 0) + 3_600_000)
            if now_msc < due_msc:
                remaining.append(item)
                continue
            try:
                if item["kind"] == "MARKOUT":
                    self._complete_markout(item)
                else:
                    self._complete_recovery(item)
            except Exception as exc:
                self._write({"event": "TELEMETRY_BACKFILL_ERROR",
                             "kind": item.get("kind"), "symbol": item.get("symbol"),
                             "error": str(exc)})
        self.pending = remaining
        self._save_pending()

    def _ticks(self, symbol: str, start_msc: int, end_msc: int) -> list[dict]:
        flags = getattr(self.mt5, "COPY_TICKS_INFO",
                        getattr(self.mt5, "COPY_TICKS_ALL", 0))
        raw = self.mt5.copy_ticks_range(
            symbol, datetime.fromtimestamp(start_msc / 1000, timezone.utc),
            datetime.fromtimestamp(end_msc / 1000, timezone.utc), flags)
        return _tick_rows(raw)

    def _complete_markout(self, item: dict) -> None:
        rows = self._ticks(item["symbol"], item["fill_msc"],
                           item["fill_msc"] + 65_000)
        record = {"event": "POST_FILL_MARKOUT", "symbol": item["symbol"],
                  "direction": item["direction"],
                  "fill_time_msc": item["fill_msc"],
                  "fill_price": item["fill_price"],
                  "markout_price_unit": "PRICE"}
        missing = []
        for seconds in MARKOUT_SECONDS:
            row = _first_at_or_after(rows, item["fill_msc"] + seconds * 1000)
            key = f"mid_markout_{seconds}s"
            record[key] = (normalized_markout(item["direction"], item["fill_price"],
                                               row["mid"]) if row else None)
            if row is None:
                missing.append(seconds)
        record["missing_horizons_seconds"] = missing
        self._write(record)

    def _complete_recovery(self, item: dict) -> None:
        anchor = item["anchor_msc"]
        rows = self._ticks(item["symbol"], anchor - 3_600_000,
                           anchor + 3_660_000)
        baseline = _median_spread(rows, anchor - 3_600_000, anchor)
        record = {"event": "SPREAD_RECOVERY", "symbol": item["symbol"],
                  "anchor_time_msc": anchor,
                  "anchor_time_utc": _iso_from_msc(anchor),
                  "anchor_source": item["anchor_source"],
                  "baseline_source": "PRE60_BROKER_BID_ASK_TICKS",
                  "pre60_spread_median": baseline}
        for minutes in RECOVERY_MINUTES:
            target = anchor + minutes * 60_000
            row = _first_at_or_after(rows, target)
            record[f"spread_ratio_t+{minutes}m"] = (
                row["spread"] / baseline if row and baseline else None)
        first_125 = first_100 = None
        if baseline:
            for minute in range(1, 61):
                value = _median_spread(rows, anchor + (minute - 1) * 60_000,
                                       anchor + minute * 60_000)
                if value is None:
                    continue
                if first_125 is None and value <= baseline * 1.25:
                    first_125 = minute
                if first_100 is None and value <= baseline:
                    first_100 = minute
        record["minutes_to_1.25x_baseline"] = first_125
        record["minutes_to_1.00x_baseline"] = first_100
        record["recovery_censored"] = baseline is None or first_100 is None
        self._write(record)
