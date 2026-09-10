"""Independent MT5 history and execution-telemetry worker.

The scalper sends small nonblocking UDP datagrams to localhost.  This process
owns all tick-history queries, calculations, persistence and horizon polling,
so telemetry cannot lengthen a trading scan or delay position registration.
"""
from __future__ import annotations

import json
import logging
import signal
import socket
import sys
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import SimpleNamespace

import MetaTrader5 as mt5
import pandas as pd

from scalper import execution_telemetry as ET


ROOT = Path(__file__).resolve().parent
logger = logging.getLogger("SA.TelemetryWorker")
_running = True


def _datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _namespace(value):
    return SimpleNamespace(**(value or {}))


def _rates_frame(symbol: str, timeframe: int, timeframe_minutes: int,
                 at: datetime, count: int) -> pd.DataFrame | None:
    rates = mt5.copy_rates_from(symbol, timeframe, at, count)
    if rates is None or len(rates) == 0:
        return None
    frame = pd.DataFrame(rates)
    if "time" in frame:
        frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
        # copy_rates_from may include the bar forming at `at`; telemetry uses
        # the same completed-bar information boundary as the strategy.
        frame = frame.loc[
            frame["time"] + timedelta(minutes=timeframe_minutes) <= at]
    return frame


def _enrich_context(symbol: str, decision_time: datetime,
                    context: dict) -> tuple[dict, pd.DataFrame | None]:
    context = dict(context or {})
    m15 = _rates_frame(symbol, mt5.TIMEFRAME_M15, 15, decision_time, 160)
    m5 = _rates_frame(symbol, mt5.TIMEFRAME_M5, 5, decision_time, 80)
    technical = ET.technical_snapshot(m15)
    context.update(technical)
    session_start = context.pop("session_start_utc", None)
    session_frame = None
    if m15 is not None and session_start:
        start = _datetime(session_start)
        session_frame = m15.loc[m15["time"] >= start]
    session_vwap = ET.bar_vwap(session_frame)
    context["session_vwap"] = session_vwap
    context["session_vwap_source"] = "BROKER_TICK_VOLUME_BAR_PROXY"
    atr = technical.get("atr14_m15")
    entry_price = context.pop("entry_price_for_context", None)
    context["distance_to_vwap_atr"] = (
        (float(entry_price) - session_vwap) / atr
        if entry_price is not None and session_vwap is not None and atr else None)
    return context, m5


def dispatch(recorder: ET.ExecutionTelemetry, message: dict) -> None:
    method = message.get("method")
    payload = message.get("payload") or {}
    if method == "record_candidate":
        recorder.record_candidate(
            payload["symbol"], payload["direction"], payload["trigger_type"],
            _namespace(payload.get("decision_tick")),
            _datetime(payload["decision_time"]), payload.get("context") or {})
        return
    if method == "record_entry":
        decision_time = _datetime(payload["decision_time"])
        context, m5 = _enrich_context(
            payload["symbol"], decision_time, payload.get("context") or {})
        recorder.record_entry(
            symbol=payload["symbol"], direction=payload["direction"],
            stop_price=float(payload["stop_price"]),
            decision_tick=_namespace(payload.get("decision_tick")),
            decision_time=decision_time,
            submit_time=_datetime(payload["submit_time"]),
            confirm_time=_datetime(payload["confirm_time"]),
            latency_ms=float(payload["latency_ms"]),
            request=payload.get("request") or {},
            result=_namespace(payload.get("result")), context=context,
            m5_frame=m5)
        return
    if method == "record_exit_submission":
        recorder.record_exit_submission(
            ticket=int(payload["ticket"]), symbol=payload["symbol"],
            direction=payload["direction"], exit_type=payload["exit_type"],
            reference_price=float(payload["reference_price"]),
            submit_time=_datetime(payload["submit_time"]),
            confirm_time=_datetime(payload["confirm_time"]),
            latency_ms=float(payload["latency_ms"]),
            request=payload.get("request") or {},
            result=_namespace(payload.get("result")))
        return
    if method == "record_settled_exit":
        recorder.record_settled_exit(
            ticket=int(payload["ticket"]), info=payload.get("info") or {},
            deals=[_namespace(deal) for deal in payload.get("deals") or []],
            outcome=payload["outcome"],
            observed_time=_datetime(payload["observed_time"]))


def _configure_logging() -> None:
    path = ROOT / "logs" / "execution_telemetry_worker.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=5_242_880, backupCount=3,
                                  encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)sZ [%(name)s] %(levelname)s: %(message)s",
        handlers=[handler, logging.StreamHandler(sys.stdout)], force=True)


def _stop(signum, frame) -> None:
    global _running
    _running = False


def main() -> int:
    global _running
    _configure_logging()
    if not mt5.initialize():
        logger.error("MT5 initialize failed: %s", mt5.last_error())
        return 2
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        server.bind((ET.TELEMETRY_HOST, ET.TELEMETRY_PORT))
    except OSError as exc:
        logger.error("telemetry UDP bind failed: %s", exc)
        mt5.shutdown()
        return 3
    server.settimeout(2.0)
    recorder = ET.ExecutionTelemetry(
        mt5, ROOT / "logs" / "sa_execution_telemetry.jsonl",
        ROOT / "logs" / "sa_execution_telemetry_pending.json")
    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _stop)
    logger.info("worker ready on udp://%s:%s", *server.getsockname())
    try:
        while _running:
            try:
                packet, _ = server.recvfrom(ET.MAX_DATAGRAM_BYTES)
                message = json.loads(packet.decode("utf-8"))
                if message.get("protocol") == "SA_TELEMETRY_UDP_V1":
                    dispatch(recorder, message)
            except socket.timeout:
                pass
            except Exception as exc:
                logger.warning("telemetry event failed: %s", exc)
            try:
                recorder.poll(datetime.now(timezone.utc))
            except Exception as exc:
                logger.warning("telemetry horizon poll failed: %s", exc)
    finally:
        server.close()
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
