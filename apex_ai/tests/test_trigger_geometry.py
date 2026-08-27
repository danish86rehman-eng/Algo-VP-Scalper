"""
Trigger geometry and entry-validation tests.

These lock in the two changes that decide whether the scalper can be
profitable at all: the target multiple, and the cost floor beneath it.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scalper.trigger_engine import SATriggerEngine


class _Trigger:
    """Minimal stand-in for SATrigger with only the fields step3 reads."""
    def __init__(self, entry, sl, tp1):
        self.detected = True
        self.entry_price = entry
        self.stop_loss = sl
        self.tp1 = tp1


class TestTargetGeometry(unittest.TestCase):
    def setUp(self):
        self.te = SATriggerEngine()

    def test_defaults_are_2r_and_3r(self):
        self.assertEqual(self.te.tp1_r, 2.0)
        self.assertEqual(self.te.tp2_r, 3.0)

    def test_bullish_targets(self):
        tp1, tp2 = self.te._targets("BULLISH", 100.0, 99.0)
        self.assertAlmostEqual(tp1, 102.0)
        self.assertAlmostEqual(tp2, 103.0)

    def test_bearish_targets_mirror(self):
        tp1, tp2 = self.te._targets("BEARISH", 100.0, 101.0)
        self.assertAlmostEqual(tp1, 98.0)
        self.assertAlmostEqual(tp2, 97.0)

    def test_zero_risk_yields_no_targets(self):
        self.assertEqual(self.te._targets("BULLISH", 100.0, 100.0), (0.0, 0.0))

    def test_targets_are_configurable(self):
        te = SATriggerEngine(tp1_r=1.5, tp2_r=4.0)
        tp1, tp2 = te._targets("BULLISH", 100.0, 98.0)
        self.assertAlmostEqual(tp1, 103.0)
        self.assertAlmostEqual(tp2, 108.0)


class TestNetRCostGate(unittest.TestCase):
    """
    A 1R target cannot survive round-turn spread at a sub-50% win rate.
    The gate must reject that geometry and accept 2R.
    """
    def setUp(self):
        self.te = SATriggerEngine()

    def test_one_r_target_is_rejected(self):
        ok, why = self.te.step3_validate(
            _Trigger(100.0, 99.0, 101.0), spread_pips=5.0,
            symbol="XAUUSD", sl_pips=400)
        self.assertFalse(ok)
        self.assertIn("Net R", why)

    def test_two_r_target_passes(self):
        ok, why = self.te.step3_validate(
            _Trigger(100.0, 99.0, 102.0), spread_pips=5.0,
            symbol="XAUUSD", sl_pips=400)
        self.assertTrue(ok, why)

    def test_sl_floor_still_precedes_cost_gate(self):
        ok, why = self.te.step3_validate(
            _Trigger(100.0, 99.0, 102.0), spread_pips=2.0,
            symbol="XAUUSD", sl_pips=100)
        self.assertFalse(ok)
        self.assertIn("institutional floor", why)

    def test_absolute_spread_cap_rejects_outliers(self):
        ok, why = self.te.step3_validate(
            _Trigger(100.0, 99.0, 102.0), spread_pips=99.0,
            symbol="XAUUSD", sl_pips=400)
        self.assertFalse(ok)
        self.assertIn("absolute cap", why)

    def test_undetected_trigger_rejected(self):
        t = _Trigger(100.0, 99.0, 102.0)
        t.detected = False
        ok, _ = self.te.step3_validate(t, 1.0, "XAUUSD", 400)
        self.assertFalse(ok)


class TestEqualLevelClustering(unittest.TestCase):
    """`_find_equals` used to emit one level per matching PAIR and ignored
    both its `current_price` and `side` arguments."""

    def setUp(self):
        self.te = SATriggerEngine(equal_hl_tolerance_pct=0.001)

    def test_three_equal_highs_produce_one_level(self):
        levels = self.te._find_equals(
            [100.00, 100.02, 100.01, 95.0], current_price=99.0, side="high")
        self.assertEqual(len(levels), 1)
        self.assertAlmostEqual(levels[0], 100.01, places=2)

    def test_levels_below_price_are_dropped_for_highs(self):
        levels = self.te._find_equals(
            [100.00, 100.02], current_price=105.0, side="high")
        self.assertEqual(levels, [])

    def test_levels_above_price_are_dropped_for_lows(self):
        levels = self.te._find_equals(
            [100.00, 100.02], current_price=95.0, side="low")
        self.assertEqual(levels, [])

    def test_isolated_extreme_is_not_a_pool(self):
        levels = self.te._find_equals(
            [100.0, 90.0, 80.0], current_price=70.0, side="high")
        self.assertEqual(levels, [])

    def test_empty_input(self):
        self.assertEqual(self.te._find_equals([], 100.0, "high"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
