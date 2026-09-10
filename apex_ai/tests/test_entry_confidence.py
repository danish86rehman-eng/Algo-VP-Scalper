"""Pre-2026-09-07 confidence policy and live/replay parity."""
import inspect
import itertools
import unittest

from scalper import decision_params as DP
import scalper_agent as live
import backtest_scalper as replay


class EntryConfidenceTests(unittest.TestCase):
    def test_stb_admitted_setups_have_no_additional_confidence_filter(self):
        ratings = ("HIGH", "MEDIUM", "LOW", "UNKNOWN", "", None)
        for trigger, stb in itertools.product(ratings, repeat=2):
            with self.subTest(trigger=trigger, stb=stb):
                self.assertTrue(DP.entry_confidence_allowed(trigger, stb))

    def test_gate_precedes_consultation_in_both_paths(self):
        for fn, downstream in ((live.ScalperAgent._scan_symbol, "self.consultant.consult("),
                               (replay.run_backtest, "trigger, why = _apply_v1_council_gates(")):
            source = inspect.getsource(fn)
            gate = source.index("if not DP.entry_confidence_allowed(trigger.confidence, stb.confidence):")
            self.assertLess(gate, source.index(downstream))
            self.assertIn('"CONFIDENCE"', source[gate:source.index(downstream)])
