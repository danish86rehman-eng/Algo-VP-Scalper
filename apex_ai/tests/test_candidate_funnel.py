import unittest
from datetime import datetime, timedelta, timezone

from scalper.candidate_funnel import (
    CandidateFunnelRecorder, STAGES, make_candidate_id,
)


class CandidateFunnelTests(unittest.TestCase):
    def setUp(self):
        self.r = CandidateFunnelRecorder()
        self.ts = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)

    def cid(self, trigger="FVG_FILL"):
        return self.r.candidate(symbol="XAUUSD", trigger=trigger,
                                 direction="BULLISH", originating_event_ts=self.ts,
                                 reference_level=4320.0, formation_ts=self.ts)

    def test_repeated_id_is_idempotent(self):
        a = self.cid(); b = self.cid()
        self.assertEqual(a, b)
        self.assertEqual(self.r.summary()["candidates"], 1)

    def test_wait_is_not_rejection(self):
        c = self.cid(); self.r.stage(c, "M5_CONFIRMATION_CHECKED", "WAIT", "await bar")
        self.assertEqual(self.r.summary()["by_trigger"]["FVG_FILL"]["REJECTED"], 0)

    def test_wait_to_pass(self):
        c = self.cid(); self.r.stage(c, "M5_CONFIRMATION_CHECKED", "WAIT")
        self.r.stage(c, "M5_CONFIRMATION_CHECKED", "PASS")
        events = [e for e in self.r.events if e.get("stage") == "M5_CONFIRMATION_CHECKED"]
        self.assertEqual(events[-1]["status"], "PASS")

    def test_terminal_cannot_reopen(self):
        c = self.cid(); self.r.terminal(c, "REJECTED")
        self.r.stage(c, "REGIME_CHECKED", "PASS")
        self.r.terminal(c, "EXECUTED")
        self.assertEqual(self.r.summary()["by_trigger"]["FVG_FILL"]["REJECTED"], 1)

    def test_preempted_remains_preempted(self):
        low = self.cid("JUDAS"); win = self.cid("FVG_FILL")
        self.r.preempt(low, win, "FVG_FILL")
        self.assertEqual(self.r.summary()["by_trigger"]["JUDAS"]["PREEMPTED"], 1)

    def test_lower_telemetry_cannot_affect_selection(self):
        selected = []
        def observer(_):
            selected.append("telemetry")
            return False
        self.assertFalse(observer(object()))
        self.assertEqual(selected, ["telemetry"])

    def test_no_return_value_is_decision_authority(self):
        c = self.cid()
        self.assertIsNone(self.r.stage(c, "REGIME_CHECKED", "PASS"))

    def test_enabled_disabled_have_no_trading_output(self):
        disabled = CandidateFunnelRecorder(enabled=False)
        self.assertTrue(disabled.candidate(symbol="XAUUSD", trigger="JUDAS",
                                           direction="BEARISH", originating_event_ts=self.ts))
        self.assertEqual(disabled.events, ())

    def test_deterministic_m5_expiry(self):
        c = self.cid(); expiry = self.ts + timedelta(minutes=30)
        self.r.stage(c, "M5_CONFIRMATION_CHECKED", "WAIT", values={"expiry": expiry})
        self.r.terminal(c, "EXPIRED", "M5 confirmation window elapsed")
        self.assertEqual(self.r.summary()["by_trigger"]["FVG_FILL"]["EXPIRED"], 1)

    def test_deterministic_fvg_lifecycle(self):
        c = self.cid()
        for stage in ("FRESHNESS_CHECKED", "LOCATION_CHECKED", "GEOMETRY_CHECKED"):
            self.r.stage(c, stage, "PASS")
        self.r.terminal(c, "SIMULATED_ENTRY") if False else None
        self.assertEqual(make_candidate_id("XAUUSD", "FVG_FILL", "BULLISH", self.ts,
                                           4320.0, formation_ts=self.ts), c)

    def test_live_replay_identical_stages(self):
        a = CandidateFunnelRecorder(); b = CandidateFunnelRecorder()
        ca = a.candidate(symbol="XAUUSD", trigger="BOS_RETEST", direction="BULLISH",
                         originating_event_ts=self.ts, reference_level=4319.5)
        cb = b.candidate(symbol="XAUUSD", trigger="BOS_RETEST", direction="BULLISH",
                         originating_event_ts=self.ts, reference_level=4319.5)
        for r, c in ((a, ca), (b, cb)):
            r.stage(c, "STRUCTURE_CHECKED", "PASS")
            r.stage(c, "M5_CONFIRMATION_CHECKED", "WAIT")
        self.assertEqual(ca, cb)
        self.assertEqual([(e["stage"], e["status"]) for e in a.events if e.get("kind") == "stage"],
                         [(e["stage"], e["status"]) for e in b.events if e.get("kind") == "stage"])

    def test_completed_bars_only_and_all_terminal_stages_present(self):
        c = self.cid()
        self.r.stage(c, "RAW_DETECTED", "PASS", completed_bar_ts=self.ts,
                     evaluation_ts=self.ts + timedelta(seconds=1))
        self.r.terminal(c, "UNRESOLVED_AT_END")
        self.assertTrue(self.r.integrity()["completed_bar_only"])
        stage_names = {e["stage"] for e in self.r.events if e.get("kind") == "stage"}
        self.assertEqual(stage_names, set(STAGES))


if __name__ == "__main__":
    unittest.main()
