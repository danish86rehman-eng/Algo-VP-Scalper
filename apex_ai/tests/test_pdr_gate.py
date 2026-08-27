"""
Previous-day-range gate: both directions, all three modes, the abstention
cases, and the live/simulator parity the gate has to hold to be measurable.

The tests that matter most here are the ones asserting what the gate does
*not* veto. A veto layer that quietly vetoes more than its rule says is
indistinguishable from the rule working, right up until the fold test.
"""
import inspect
import unittest

import pandas as pd

import backtest_scalper as sim
from scalper import decision_params as DP
from scalper.pdr_gate import (
    MODE_LONG_PREMIUM, MODE_SHORT_DISCOUNT, MODE_SYMMETRIC, MODES, PDRGate,
)
from scalper_agent import ScalperAgent


def d1(high=110.0, low=100.0, extra_rows=3):
    """A daily frame whose LAST row is the previous day: PDL=low, PDH=high."""
    rows = [{"high": 999.0, "low": 0.5} for _ in range(extra_rows)]
    rows.append({"high": high, "low": low})
    return pd.DataFrame(rows)


# PDL=100, PDH=110 -> loc = (price - 100) / 10
DAILY = d1()


class LocationArithmeticTests(unittest.TestCase):

    def test_loc_is_fraction_of_previous_day_range(self):
        g = PDRGate(mode=MODE_SYMMETRIC)
        r = g.check("BULLISH", 105.0, DAILY)
        self.assertAlmostEqual(r.loc, 0.5)
        self.assertEqual(r.pdl, 100.0)
        self.assertEqual(r.pdh, 110.0)

    def test_only_the_last_closed_row_defines_the_levels(self):
        """Earlier rows are lead-in and must not widen the range."""
        r = PDRGate(mode=MODE_SYMMETRIC).check("BULLISH", 105.0, DAILY)
        self.assertEqual((r.pdl, r.pdh), (100.0, 110.0))


class ShortDiscountModeTests(unittest.TestCase):
    """The operator's literal hypothesis: do not sell the bottom of the range."""

    def setUp(self):
        self.gate = PDRGate(mode=MODE_SHORT_DISCOUNT)

    def test_short_in_discount_is_vetoed(self):
        r = self.gate.check("BEARISH", 102.0, DAILY)      # loc 0.20
        self.assertFalse(r.allow)
        self.assertIn("discount", r.reason)

    def test_short_exactly_at_the_boundary_is_vetoed(self):
        r = self.gate.check("BEARISH", 102.5, DAILY)      # loc 0.25
        self.assertFalse(r.allow)

    def test_short_above_the_boundary_is_allowed(self):
        r = self.gate.check("BEARISH", 103.0, DAILY)      # loc 0.30
        self.assertTrue(r.allow)

    def test_long_in_premium_is_NOT_vetoed_in_this_mode(self):
        r = self.gate.check("BULLISH", 109.0, DAILY)      # loc 0.90
        self.assertTrue(r.allow)

    def test_long_in_discount_is_allowed(self):
        self.assertTrue(self.gate.check("BULLISH", 101.0, DAILY).allow)


class LongPremiumModeTests(unittest.TestCase):
    """The attribution-backed cell: do not buy the top of the range."""

    def setUp(self):
        self.gate = PDRGate(mode=MODE_LONG_PREMIUM)

    def test_long_in_premium_is_vetoed(self):
        r = self.gate.check("BULLISH", 108.0, DAILY)      # loc 0.80
        self.assertFalse(r.allow)
        self.assertIn("premium", r.reason)

    def test_long_exactly_at_the_boundary_is_vetoed(self):
        self.assertFalse(self.gate.check("BULLISH", 107.5, DAILY).allow)

    def test_long_below_the_boundary_is_allowed(self):
        self.assertTrue(self.gate.check("BULLISH", 107.0, DAILY).allow)

    def test_short_in_discount_is_NOT_vetoed_in_this_mode(self):
        self.assertTrue(self.gate.check("BEARISH", 101.0, DAILY).allow)


class SymmetricModeTests(unittest.TestCase):

    def setUp(self):
        self.gate = PDRGate(mode=MODE_SYMMETRIC)

    def test_vetoes_both_sides(self):
        self.assertFalse(self.gate.check("BULLISH", 109.0, DAILY).allow)
        self.assertFalse(self.gate.check("BEARISH", 101.0, DAILY).allow)

    def test_allows_both_sides_in_the_middle(self):
        self.assertTrue(self.gate.check("BULLISH", 105.0, DAILY).allow)
        self.assertTrue(self.gate.check("BEARISH", 105.0, DAILY).allow)

    def test_long_in_discount_and_short_in_premium_are_the_wanted_trades(self):
        """The gate must never veto the direction the range argues *for*."""
        self.assertTrue(self.gate.check("BULLISH", 101.0, DAILY).allow)
        self.assertTrue(self.gate.check("BEARISH", 109.0, DAILY).allow)


class AbstentionTests(unittest.TestCase):
    """Outside the range, and on unusable input, the gate must not guess."""

    def setUp(self):
        self.gate = PDRGate(mode=MODE_SYMMETRIC)

    def test_above_pdh_abstains_rather_than_vetoing_a_long(self):
        r = self.gate.check("BULLISH", 115.0, DAILY)      # loc 1.5
        self.assertTrue(r.allow)
        self.assertTrue(r.abstained)
        self.assertIn("above PDH", r.reason)

    def test_below_pdl_abstains_rather_than_vetoing_a_short(self):
        r = self.gate.check("BEARISH", 95.0, DAILY)       # loc -0.5
        self.assertTrue(r.allow)
        self.assertTrue(r.abstained)
        self.assertIn("below PDL", r.reason)

    def test_no_frame_abstains(self):
        for frame in (None, pd.DataFrame()):
            r = self.gate.check("BULLISH", 105.0, frame)
            self.assertTrue(r.allow)
            self.assertTrue(r.abstained)

    def test_zero_range_day_abstains(self):
        r = self.gate.check("BULLISH", 105.0, d1(high=100.0, low=100.0))
        self.assertTrue(r.allow)
        self.assertTrue(r.abstained)

    def test_unknown_direction_abstains(self):
        r = self.gate.check("NONE", 105.0, DAILY)
        self.assertTrue(r.allow)
        self.assertTrue(r.abstained)

    def test_missing_price_abstains(self):
        self.assertTrue(self.gate.check("BULLISH", 0.0, DAILY).abstained)


class ConstructionTests(unittest.TestCase):

    def test_bad_mode_rejected(self):
        with self.assertRaises(ValueError):
            PDRGate(mode="TREND")

    def test_inverted_fractions_rejected(self):
        with self.assertRaises(ValueError):
            PDRGate(mode=MODE_SYMMETRIC, discount_frac=0.8, premium_frac=0.2)

    def test_all_declared_modes_construct(self):
        for m in MODES:
            self.assertIsInstance(PDRGate(mode=m), PDRGate)


class LiveSimParityTests(unittest.TestCase):
    """
    Invariant #2: a gate added to `_scan_symbol` is mirrored into the
    simulator in the same change, reading the same constants from
    `decision_params` rather than restating them.
    """

    def test_both_processes_expose_the_gate(self):
        self.assertIn("pdr_gate_enabled",
                      inspect.signature(ScalperAgent.__init__).parameters)
        self.assertIn("pdr_gate_enabled",
                      inspect.signature(sim.run_backtest).parameters)
        self.assertIn("pdr_gate_mode",
                      inspect.signature(ScalperAgent.__init__).parameters)
        self.assertIn("pdr_gate_mode",
                      inspect.signature(sim.run_backtest).parameters)

    def test_defaults_come_from_decision_params(self):
        for fn in (ScalperAgent.__init__, sim.run_backtest):
            p = inspect.signature(fn).parameters
            self.assertIs(p["pdr_gate_enabled"].default, DP.PDR_GATE_ENABLED)
            self.assertIs(p["pdr_gate_mode"].default, DP.PDR_GATE_MODE)

    def test_simulator_imports_the_shared_constants(self):
        self.assertIs(sim.PDR_BARS, DP.PDR_BARS)
        self.assertIs(sim.PDR_DISCOUNT_FRAC, DP.PDR_DISCOUNT_FRAC)
        self.assertIs(sim.PDR_PREMIUM_FRAC, DP.PDR_PREMIUM_FRAC)

    def test_simulator_reads_the_daily_frame_as_closed_bars(self):
        """
        `_closed_tf(..., 1440)`, never `_closed`. A daily bar stamped today
        00:00 has not closed until tomorrow 00:00, so `_closed` would hand the
        gate today's forming bar and PDH/PDL would repaint intraday.
        """
        src = inspect.getsource(sim.run_backtest)
        head = src.split("if pdr_gate_enabled:", 1)[1][:600]
        self.assertIn("_closed_tf(frames.get('D1')", head)
        self.assertIn("1440", head)

    def test_gate_ships_disabled(self):
        """§13.5: nothing promotes without folds. L-011 is still open."""
        self.assertFalse(DP.PDR_GATE_ENABLED)


class ObservationOnlyTests(unittest.TestCase):
    """§13.10: the gate decides admission; it must not touch trade geometry."""

    def test_result_carries_no_geometry_field(self):
        r = PDRGate(mode=MODE_SYMMETRIC).check("BULLISH", 105.0, DAILY)
        for banned in ("sl", "tp", "tp1", "tp2", "volume", "risk", "lot"):
            self.assertFalse(
                hasattr(r, banned),
                f"PDRResult must not carry {banned!r} — it is a veto layer")


if __name__ == "__main__":
    unittest.main()
