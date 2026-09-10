"""
L-016 — wick-ratio qualification on the sweep candle.

The detector admits a sweep on two conditions (an extreme in the last 4 bars
pierced the level, and the last bar closes back through it) with no requirement
that the piercing candle rejected anything. These tests pin the added
qualification in BOTH directions, its invalidation case, and — most importantly
— that the filter DISABLED reproduces the pre-existing admission exactly, which
is what makes the stored baselines still comparable.
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scalper import decision_params as DP
from scalper.trigger_engine import (MicroLiquidity, SATriggerEngine,
                                    _wick_ratio)


def _bar(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c, "tick_volume": 100}


def _frame(bars):
    return pd.DataFrame([_bar(*b) for b in bars])


#: Four bars where bar[1] pierces an SSL at 100.0 with a LONG lower wick
#: (range 4.0, lower wick 3.0 -> ratio 0.75) and the last bar closes back above.
_BULL_DEEP_WICK = [
    (101.0, 101.5, 100.5, 101.0),
    (101.0, 101.0,  97.0, 100.0),   # sweeping candle: body 100..101, low 97
    (100.2, 100.8, 100.1, 100.6),
    (100.6, 101.4, 100.5, 101.2),   # bullish close back above the level
]

#: Same shape, but the sweeping candle is a shallow tag: range 4.0, lower wick
#: 0.2 -> ratio 0.05. This is the trade the remedy is meant to remove.
_BULL_SHALLOW_WICK = [
    (101.0, 101.5, 100.5, 101.0),
    (97.2,  101.0,  97.0, 100.6),   # body opens at the low: almost no wick
    (100.2, 100.8, 100.1, 100.6),
    (100.6, 101.4, 100.5, 101.2),
]

#: Bearish mirror: bar[1] pierces a BSL at 100.0 with a long UPPER wick.
_BEAR_DEEP_WICK = [
    ( 99.0,  99.5,  98.5,  99.0),
    (100.0, 103.0,  99.0,  99.5),   # sweeping candle: body 99..100, high 103
    ( 99.8,  99.9,  99.2,  99.4),
    ( 99.4,  99.5,  98.6,  98.8),   # bearish close back below the level
]

#: Bearish shallow tag: upper wick 0.2 of a 4.0 range -> ratio 0.05.
_BEAR_SHALLOW_WICK = [
    ( 99.0,  99.5,  98.5,  99.0),
    ( 99.2, 103.0,  99.0, 102.8),   # body closes at the high: almost no wick
    ( 99.8,  99.9,  99.2,  99.4),
    ( 99.4,  99.5,  98.6,  98.8),
]


def _bull_liq():
    return MicroLiquidity(equal_lows=[100.0], current_price=101.2)


def _bear_liq():
    return MicroLiquidity(equal_highs=[100.0], current_price=98.8)


class TestWickRatioArithmetic(unittest.TestCase):
    """`(min(open,close) - low) / range` long, `(high - max(o,c)) / range` short."""

    def test_bullish_uses_the_lower_wick(self):
        candle = _bar(101.0, 101.0, 97.0, 100.0)   # range 4.0, lower wick 3.0
        self.assertAlmostEqual(_wick_ratio(candle, "BULLISH"), 0.75)

    def test_bearish_uses_the_upper_wick(self):
        candle = _bar(100.0, 103.0, 99.0, 99.5)    # range 4.0, upper wick 3.0
        self.assertAlmostEqual(_wick_ratio(candle, "BEARISH"), 0.75)

    def test_zero_range_candle_scores_zero_not_a_crash(self):
        candle = _bar(100.0, 100.0, 100.0, 100.0)
        self.assertEqual(_wick_ratio(candle, "BULLISH"), 0.0)
        self.assertEqual(_wick_ratio(candle, "BEARISH"), 0.0)

    def test_the_two_directions_are_not_the_same_measurement(self):
        candle = _bar(101.0, 101.0, 97.0, 100.0)
        self.assertNotAlmostEqual(_wick_ratio(candle, "BULLISH"),
                                  _wick_ratio(candle, "BEARISH"))


class TestFilterDisabled(unittest.TestCase):
    """Default OFF must reproduce the pre-existing admission, both directions."""

    def setUp(self):
        self.te = SATriggerEngine(sweep_wick_filter=False)

    def test_shipping_default_is_off(self):
        self.assertFalse(DP.SWEEP_WICK_FILTER_ENABLED)
        self.assertFalse(SATriggerEngine().sweep_wick_filter)

    def test_shallow_bullish_sweep_still_admitted(self):
        trig = self.te._check_sweep_rejection(
            _frame(_BULL_SHALLOW_WICK), _bull_liq(), "XAUUSD")
        self.assertTrue(trig.detected)
        self.assertEqual(trig.direction, "BULLISH")

    def test_shallow_bearish_sweep_still_admitted(self):
        trig = self.te._check_sweep_rejection(
            _frame(_BEAR_SHALLOW_WICK), _bear_liq(), "XAUUSD")
        self.assertTrue(trig.detected)
        self.assertEqual(trig.direction, "BEARISH")

    def test_ratio_is_stamped_even_when_nothing_branches_on_it(self):
        """Attribution needs the ratio on baseline runs (L-016 'must report')."""
        trig = self.te._check_sweep_rejection(
            _frame(_BULL_SHALLOW_WICK), _bull_liq(), "XAUUSD")
        self.assertIsNotNone(trig.wick_ratio)
        self.assertAlmostEqual(trig.wick_ratio, 0.05)


class TestFilterEnabled(unittest.TestCase):
    def setUp(self):
        self.te = SATriggerEngine(sweep_wick_filter=True, sweep_wick_ratio=0.45)

    def test_deep_bullish_wick_admitted(self):
        trig = self.te._check_sweep_rejection(
            _frame(_BULL_DEEP_WICK), _bull_liq(), "XAUUSD")
        self.assertTrue(trig.detected)
        self.assertEqual(trig.direction, "BULLISH")
        self.assertGreaterEqual(trig.wick_ratio, 0.45)

    def test_deep_bearish_wick_admitted(self):
        trig = self.te._check_sweep_rejection(
            _frame(_BEAR_DEEP_WICK), _bear_liq(), "XAUUSD")
        self.assertTrue(trig.detected)
        self.assertEqual(trig.direction, "BEARISH")
        self.assertGreaterEqual(trig.wick_ratio, 0.45)

    def test_shallow_bullish_tag_rejected(self):
        trig = self.te._check_sweep_rejection(
            _frame(_BULL_SHALLOW_WICK), _bull_liq(), "XAUUSD")
        self.assertFalse(trig.detected)

    def test_shallow_bearish_tag_rejected(self):
        trig = self.te._check_sweep_rejection(
            _frame(_BEAR_SHALLOW_WICK), _bear_liq(), "XAUUSD")
        self.assertFalse(trig.detected)

    def test_rejected_setup_still_reports_its_ratio(self):
        trig = self.te._check_sweep_rejection(
            _frame(_BEAR_SHALLOW_WICK), _bear_liq(), "XAUUSD")
        self.assertFalse(trig.detected)
        self.assertAlmostEqual(trig.wick_ratio, 0.05)

    def test_threshold_is_swept_not_hardcoded(self):
        """0.35 / 0.45 / 0.55 must be able to disagree, or the sweep is a no-op."""
        frame, liq = _frame(_BULL_DEEP_WICK), _bull_liq()
        loose = SATriggerEngine(sweep_wick_filter=True, sweep_wick_ratio=0.70)
        tight = SATriggerEngine(sweep_wick_filter=True, sweep_wick_ratio=0.80)
        self.assertTrue(loose._check_sweep_rejection(frame, liq, "X").detected)
        self.assertFalse(tight._check_sweep_rejection(frame, liq, "X").detected)


class TestBothProcessesTakeTheFlag(unittest.TestCase):
    """Invariant #2: a gate the simulator cannot see measures another strategy."""

    def test_agent_and_backtester_both_pass_it_through(self):
        root = Path(__file__).resolve().parents[1]
        agent = (root / "scalper_agent.py").read_text(encoding="utf-8")
        sim = (root / "backtest_scalper.py").read_text(encoding="utf-8")
        for src, name in ((agent, "scalper_agent.py"),
                          (sim, "backtest_scalper.py")):
            self.assertIn("sweep_wick_filter=", src, f"{name} must pass the flag")
            self.assertIn("--sweep-wick-filter", src, f"{name} must expose it")


if __name__ == "__main__":
    unittest.main()
