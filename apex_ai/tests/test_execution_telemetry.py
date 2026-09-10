"""Contract tests for immutable scalper execution telemetry."""
from __future__ import annotations

import json
import inspect
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import numpy as np

from scalper.execution_telemetry import (
    BANNED_DECISION_FIELDS,
    DatagramTelemetryClient,
    ExecutionTelemetry,
    entry_shortfall,
    exit_shortfall,
    normalized_markout,
    tick_native_snapshot,
)


T0 = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def tick(when: datetime, bid: float, ask: float):
    return SimpleNamespace(time_msc=int(when.timestamp() * 1000),
                           bid=bid, ask=ask)


class FakeMT5:
    COPY_TICKS_INFO = 1
    DEAL_ENTRY_OUT = 1
    TRADE_RETCODE_DONE = 10009
    DEAL_REASON_SL = 4
    DEAL_REASON_TP = 5

    def __init__(self, ticks=(), deals=()):
        self.ticks = list(ticks)
        self.deals = list(deals)

    def copy_ticks_range(self, symbol, start, end, flags):
        lo, hi = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        return [row for row in self.ticks if lo <= row.time_msc <= hi]

    def symbol_info(self, symbol):
        return SimpleNamespace(point=0.01, trade_exemode=2)

    def account_info(self):
        return SimpleNamespace(company="TEST_BROKER", trade_mode=0)

    def history_deals_get(self, *args, ticket=None, **kwargs):
        if ticket is None:
            return tuple(self.deals)
        return tuple(deal for deal in self.deals
                     if getattr(deal, "ticket", None) == ticket)


class FakeSocket:
    def __init__(self, *args):
        self.blocking = None
        self.sent = []

    def setblocking(self, value):
        self.blocking = value

    def sendto(self, packet, address):
        self.sent.append((packet, address))
        return len(packet)


class TestPureExecutionMath(unittest.TestCase):

    def test_shortfall_sign_is_positive_when_execution_is_worse(self):
        self.assertAlmostEqual(entry_shortfall("BULLISH", 100, 100.2, 100.5), .3)
        self.assertAlmostEqual(entry_shortfall("BEARISH", 100, 100.2, 99.7), .3)
        self.assertAlmostEqual(exit_shortfall("BULLISH", 99, 98.7), .3)
        self.assertAlmostEqual(exit_shortfall("BEARISH", 101, 101.4), .4)

    def test_markout_is_direction_normalized(self):
        self.assertEqual(normalized_markout("BULLISH", 100, 101), 1)
        self.assertEqual(normalized_markout("BEARISH", 100, 99), 1)
        self.assertEqual(normalized_markout("BEARISH", 100, 101), -1)

    def test_snapshot_uses_bid_ask_ticks(self):
        rows = []
        for minute in range(-100, 0):
            spread = .20 if minute < -5 else .40
            rows.append(tick(T0 + timedelta(minutes=minute), 2000,
                             2000 + spread))
        snapshot = tick_native_snapshot(
            rows, tick(T0, 2000, 2000.50), T0, .01, 5.0, 1.25)
        self.assertEqual(snapshot["spread_source"], "BROKER_BID_ASK_TICKS")
        self.assertAlmostEqual(snapshot["spread_price"], .50)
        self.assertAlmostEqual(snapshot["spread_points"], 50)
        self.assertAlmostEqual(snapshot["spread_to_stop"], .10)
        self.assertEqual(snapshot["quote_activity_label"], "BROKER_ACTIVITY_PROXY")
        self.assertEqual(snapshot["broker_impact_label"], "BROKER_IMPACT_PROXY")
        self.assertEqual(snapshot["m5_range_atr"], 1.25)

    def test_snapshot_accepts_mt5_numpy_structured_ticks(self):
        rows = np.array([
            (int((T0 - timedelta(seconds=2)).timestamp() * 1000), 100., 100.2),
            (int((T0 - timedelta(seconds=1)).timestamp() * 1000), 100., 100.4),
        ], dtype=[("time_msc", "i8"), ("bid", "f8"), ("ask", "f8")])
        snapshot = tick_native_snapshot(
            rows, tick(T0, 100, 100.3), T0, .01, 1.0)
        self.assertAlmostEqual(snapshot["pre60_spread_median"], .3)


class TestRecorder(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        ticks = [tick(T0 + timedelta(seconds=second), 100 + second / 100,
                      100.2 + second / 100)
                 for second in range(-6300, 3661)]
        self.deal = SimpleNamespace(ticket=77, order=88, position_id=99,
                                    entry=0, price=100.30, volume=.10,
                                    time_msc=int((T0 + timedelta(milliseconds=120)).timestamp() * 1000))
        self.mt5 = FakeMT5(ticks, [self.deal])
        self.log = root / "telemetry.jsonl"
        self.recorder = ExecutionTelemetry(self.mt5, self.log,
                                           root / "pending.json")

    def tearDown(self):
        self.temp.cleanup()

    def records(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_entry_records_provenance_shortfall_and_schedules_followups(self):
        result = SimpleNamespace(retcode=10009, retcode_external=0,
                                 request_id=12, order=88, deal=77,
                                 price=100.31, volume=.10)
        request = {"price": 100.20, "volume": .10, "type_filling": 1}
        self.recorder.record_entry(
            symbol="XAUUSD", direction="BULLISH", stop_price=99.20,
            decision_tick=tick(T0, 100, 100.20), decision_time=T0,
            submit_time=T0 + timedelta(milliseconds=10),
            confirm_time=T0 + timedelta(milliseconds=100), latency_ms=90,
            request=request, result=result,
            context={"telemetry_authority": "OBSERVATION_ONLY"},
            m5_frame=pd.DataFrame())
        record = self.records()[0]
        self.assertEqual(record["fill_time_source"], "BROKER_DEAL")
        self.assertEqual(record["deal_ticket"], 77)
        self.assertAlmostEqual(record["entry_shortfall_price"], .10)
        self.assertAlmostEqual(record["entry_shortfall_points"], 10)
        self.assertAlmostEqual(record["entry_shortfall_R"], .10)
        self.assertEqual({item["kind"] for item in self.recorder.pending},
                         {"MARKOUT", "SPREAD_RECOVERY"})

    def test_poll_writes_all_markouts_and_recovery_curve(self):
        self.recorder.pending = [
            {"kind": "MARKOUT", "symbol": "XAUUSD", "direction": "BULLISH",
             "fill_price": 100.2, "fill_msc": int(T0.timestamp() * 1000)},
            {"kind": "SPREAD_RECOVERY", "symbol": "XAUUSD",
             "anchor_msc": int(T0.timestamp() * 1000),
             "anchor_source": "ENTRY_DECISION_PROXY"},
        ]
        self.recorder.poll(T0 + timedelta(minutes=61))
        records = self.records()
        markout = next(row for row in records if row["event"] == "POST_FILL_MARKOUT")
        recovery = next(row for row in records if row["event"] == "SPREAD_RECOVERY")
        for seconds in (1, 5, 15, 30, 60):
            self.assertIn(f"mid_markout_{seconds}s", markout)
        for minutes in (1, 2, 5, 10, 20, 30, 60):
            self.assertIn(f"spread_ratio_t+{minutes}m", recovery)
        self.assertIn("minutes_to_1.25x_baseline", recovery)
        self.assertIn("recovery_censored", recovery)

    def test_exit_settlement_uses_broker_deal(self):
        exit_deal = SimpleNamespace(ticket=90, order=91, entry=1,
                                    price=98.80, time_msc=int(T0.timestamp() * 1000),
                                    reason=4)
        self.recorder.record_settled_exit(
            ticket=99,
            info={"symbol": "XAUUSD", "direction": "BULLISH",
                  "entry": 100.2, "sl": 99.0, "tp1": 102,
                  "exit_telemetry": {"exit_type": "TIMEOUT",
                                     "reference_price": 99.0,
                                     "latency_ms": 25}},
            deals=[exit_deal], outcome="TIMEOUT", observed_time=T0)
        record = self.records()[0]
        self.assertAlmostEqual(record["exit_shortfall_price"], .20)
        self.assertAlmostEqual(record["exit_shortfall_points"], 20)
        self.assertEqual(record["exit_latency_ms"], 25)

    def test_records_expose_no_decision_shaped_fields(self):
        self.recorder.record_candidate(
            "XAUUSD", "BULLISH", "FVG_FILL", tick(T0, 100, 100.2), T0,
            {"telemetry_authority": "OBSERVATION_ONLY"})
        self.assertFalse(BANNED_DECISION_FIELDS.intersection(self.records()[0]))
        self.assertIsNone(self.recorder.record_candidate(
            "XAUUSD", "BULLISH", "FVG_FILL", tick(T0, 100, 100.2), T0,
            {"telemetry_authority": "OBSERVATION_ONLY"}))

    def test_live_scan_never_branches_on_execution_telemetry(self):
        import scalper_agent
        source = inspect.getsource(scalper_agent.ScalperAgent._scan_symbol)
        for line in source.splitlines():
            stripped = line.strip()
            if "execution_telemetry" in stripped:
                self.assertFalse(stripped.startswith(("if ", "elif ", "while ")))

    def test_tick_enrichment_is_after_order_submission(self):
        import scalper_agent
        source = inspect.getsource(scalper_agent.ScalperAgent._execute_trade)
        send_at = source.index("result = mt5.order_send(request)")
        context_at = source.index("self._execution_observation_context")
        candidate_at = source.index("self.execution_telemetry.record_candidate")
        entry_at = source.index("self.execution_telemetry.record_entry")
        self.assertLess(send_at, context_at)
        self.assertLess(send_at, candidate_at)
        self.assertLess(send_at, entry_at)


class TestTradingProcessIsolation(unittest.TestCase):

    def test_client_is_nonblocking_and_emits_only_a_datagram(self):
        fake = FakeSocket()
        client = DatagramTelemetryClient(socket_factory=lambda *args: fake)
        self.assertFalse(fake.blocking)
        result = client.record_entry(
            symbol="XAUUSD", direction="BULLISH", stop_price=99,
            decision_tick=tick(T0, 100, 100.2), decision_time=T0,
            submit_time=T0, confirm_time=T0, latency_ms=3,
            request={"volume": .1}, result=SimpleNamespace(retcode=10009),
            context={"telemetry_authority": "OBSERVATION_ONLY"})
        self.assertIsNone(result)
        self.assertEqual(len(fake.sent), 1)
        message = json.loads(fake.sent[0][0].decode("utf-8"))
        self.assertEqual(message["method"], "record_entry")
        self.assertEqual(message["payload"]["symbol"], "XAUUSD")

    def test_scalper_has_no_history_or_horizon_work(self):
        import scalper_agent
        execution = inspect.getsource(scalper_agent.ScalperAgent._execute_trade)
        run = inspect.getsource(scalper_agent.ScalperAgent.run)
        self.assertNotIn("copy_ticks_range", execution)
        self.assertNotIn("execution_telemetry.poll", run)

    def test_watchdog_supervises_independent_worker(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "scalper_watchdog.py").read_text(encoding="utf-8")
        self.assertIn("execution_telemetry_worker.py", source)
        self.assertIn('ChildSupervisor("Telemetry"', source)


if __name__ == "__main__":
    unittest.main()
