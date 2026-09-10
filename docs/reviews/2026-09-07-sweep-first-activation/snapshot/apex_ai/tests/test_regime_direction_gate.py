"""
Regime-direction gate: both directions, both modes, and the abstention cases.
"""
import inspect
import unittest

import numpy as np
import pandas as pd

import backtest_scalper as sim
from scalper import decision_params as DP
from scalper.regime_classifier import (
    REGIME_RANGING, REGIME_TRENDING_DOWN, REGIME_TRENDING_UP,
)
from scalper.regime_direction_gate import (
    MODE_COUNTER_TREND_LONGS, MODE_SYMMETRIC, RegimeDirectionGate,
)
from scalper_agent import ScalperAgent

UP = pd.DataFrame({"close": 100.0 + np.arange(400) * 0.5})
DOWN = pd.DataFrame({"close": 300.0 - np.arange(400) * 0.5})


def flat_range(n=400, seed=11):
    rng = np.random.default_rng(seed)
    out = [100.0]
    for _ in range(n - 1):
        out.append(out[-1] + (100.0 - out[-1]) * 0.05 + rng.normal(0, 0.35))
    return pd.DataFrame({"close": np.array(out)})


class SymmetricModeTests(unittest.TestCase):

    def setUp(self):
        self.gate = RegimeDirectionGate(mode=MODE_SYMMETRIC)

    def test_long_into_downtrend_is_vetoed(self):
        r = self.gate.check("BULLISH", DOWN)
        self.assertFalse(r.allow)
        self.assertEqual(r.regime, REGIME_TRENDING_DOWN)

    def test_short_into_uptrend_is_vetoed(self):
        r = self.gate.check("BEARISH", UP)
        self.assertFalse(r.allow)
        self.assertEqual(r.regime, REGIME_TRENDING_UP)

    def test_with_trend_entries_pass(self):
        self.assertTrue(self.gate.check("BEARISH", DOWN).allow)
        self.assertTrue(self.gate.check("BULLISH", UP).allow)

    def test_range_vetoes_nothing(self):
        rng = flat_range()
        for direction in ("BULLISH", "BEARISH"):
            r = self.gate.check(direction, rng)
            self.assertTrue(r.allow)
            self.assertEqual(r.regime, REGIME_RANGING)
            self.assertFalse(r.abstained, "a range is an opinion, not a pass")


class CounterTrendLongsModeTests(unittest.TestCase):
    """The literal L-006 observation: longs only."""

    def setUp(self):
        self.gate = RegimeDirectionGate(mode=MODE_COUNTER_TREND_LONGS)

    def test_long_into_downtrend_is_vetoed(self):
        self.assertFalse(self.gate.check("BULLISH", DOWN).allow)

    def test_short_into_uptrend_is_ALLOWED(self):
        # The asymmetry is the whole point of this mode existing.
        r = self.gate.check("BEARISH", UP)
        self.assertTrue(r.allow)
        self.assertIn("only vetoes longs", r.reason)


class AbstentionTests(unittest.TestCase):

    def test_missing_frame_abstains(self):
        g = RegimeDirectionGate(mode=MODE_SYMMETRIC)
        r = g.check("BULLISH", None)
        self.assertTrue(r.allow)
        self.assertTrue(r.abstained)

    def test_short_history_abstains_rather_than_blocking(self):
        g = RegimeDirectionGate(mode=MODE_SYMMETRIC)
        r = g.check("BULLISH", pd.DataFrame({"close": np.arange(30.0)}))
        self.assertTrue(r.allow)
        self.assertTrue(r.abstained)

    def test_unknown_direction_abstains(self):
        g = RegimeDirectionGate(mode=MODE_SYMMETRIC)
        self.assertTrue(g.check("SIDEWAYS", DOWN).abstained)

    def test_bad_mode_rejected(self):
        with self.assertRaises(ValueError):
            RegimeDirectionGate(mode="LONGS_ONLY")


class ParityTests(unittest.TestCase):

    def test_ships_disabled(self):
        self.assertFalse(DP.RD_GATE_ENABLED)

    def test_constants_are_one_definition(self):
        for name in ("RD_GATE_ENABLED", "RD_GATE_MODE", "RD_REGIME_BARS",
                     "RD_REGIME_TF"):
            with self.subTest(constant=name):
                self.assertIs(getattr(sim, name), getattr(DP, name))

    def test_gate_is_mirrored_into_both_decision_paths(self):
        live = inspect.getsource(ScalperAgent._scan_symbol)
        simsrc = inspect.getsource(sim.run_backtest)
        self.assertIn("rd_gate", live)
        self.assertIn("rd_gate", simsrc)
        self.assertIn("RD_GATE", live)
        self.assertIn("RD_GATE", simsrc)

    def test_regime_frame_uses_the_duration_aware_slice(self):
        simsrc = inspect.getsource(sim.run_backtest)
        block = simsrc[simsrc.index("if rd_gate_enabled:"):]
        block = block[:block.index("continue")]
        self.assertIn("_closed_tf(", block)


if __name__ == "__main__":
    unittest.main()
