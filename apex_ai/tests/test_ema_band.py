"""
The H1 EMA(18) high/low band.

Pins the rule the operator specified: longs only above both bands, shorts only
below both, nothing inside — plus the arithmetic of the EMA itself, which has
to match MetaTrader's `iMA` or the numbers printed here will disagree with the
numbers on the chart.
"""
from __future__ import annotations

import unittest

import pandas as pd

from scalper.decision_params import EMA_BAND_PERIOD
from scalper.ema_filter import EMABandFilter, ema_mt5


def _frame(bars):
    """bars = [(high, low), ...] -> a minimal H1 frame."""
    return pd.DataFrame({
        "time": pd.date_range("2026-05-01", periods=len(bars), freq="h", tz="UTC"),
        "open": [(h + l) / 2 for h, l in bars],
        "high": [h for h, _ in bars],
        "low": [l for _, l in bars],
        "close": [(h + l) / 2 for h, l in bars],
    })


def _flat(n=60, high=100.0, low=98.0):
    return _frame([(high, low)] * n)


class TestEMAArithmetic(unittest.TestCase):
    def test_constant_series_returns_the_constant(self):
        self.assertAlmostEqual(ema_mt5([5.0] * 50, 18), 5.0, places=10)

    def test_seeded_from_the_simple_average(self):
        """
        With exactly `period` values there is no recursion left to run, so the
        result must be the plain average — MetaTrader's seeding rule.
        """
        vals = [float(i) for i in range(1, 19)]      # 1..18, mean 9.5
        self.assertAlmostEqual(ema_mt5(vals, 18), 9.5, places=10)

    def test_matches_the_recursive_definition(self):
        vals = [float(i) for i in range(1, 41)]
        alpha = 2.0 / (18 + 1)
        expected = sum(vals[:18]) / 18.0
        for v in vals[18:]:
            expected = v * alpha + expected * (1 - alpha)
        self.assertAlmostEqual(ema_mt5(vals, 18), expected, places=10)

    def test_insufficient_history_is_none_not_zero(self):
        """A short series must abstain; returning 0.0 would read as 'price is above'."""
        self.assertIsNone(ema_mt5([1.0] * 17, 18))

    def test_seed_choice_washes_out(self):
        """
        MetaTrader seeds from an SMA. After a long run-up any seed converges to
        the same value, which is what lets this implementation claim parity
        with the terminal.
        """
        vals = [100.0 + (i % 7) for i in range(200)]
        alpha = 2.0 / (EMA_BAND_PERIOD + 1)
        rogue = 0.0
        for v in vals:
            rogue = v * alpha + rogue * (1 - alpha)
        self.assertAlmostEqual(ema_mt5(vals, EMA_BAND_PERIOD), rogue, places=5)


class TestBandDirection(unittest.TestCase):
    def setUp(self):
        self.f = EMABandFilter()
        self.df = _flat()          # band settles at high=100, low=98

    def test_above_both_permits_long_only(self):
        self.assertTrue(self.f.check("BULLISH", 101.0, self.df).allow)
        self.assertFalse(self.f.check("BEARISH", 101.0, self.df).allow)

    def test_below_both_permits_short_only(self):
        self.assertTrue(self.f.check("BEARISH", 97.0, self.df).allow)
        self.assertFalse(self.f.check("BULLISH", 97.0, self.df).allow)

    def test_inside_the_band_permits_nothing(self):
        for direction in ("BULLISH", "BEARISH"):
            res = self.f.check(direction, 99.0, self.df)
            self.assertFalse(res.allow)
            self.assertEqual(res.position, "INSIDE")
            self.assertEqual(res.permitted_direction, "NEUTRAL")

    def test_touching_a_band_is_inside_not_through(self):
        """Strict comparison: price exactly on a band has not cleared it."""
        self.assertEqual(self.f.check("BULLISH", 100.0, self.df).position, "INSIDE")
        self.assertEqual(self.f.check("BEARISH", 98.0, self.df).position, "INSIDE")

    def test_ema_high_sits_above_ema_low(self):
        res = self.f.check("BULLISH", 101.0, self.df)
        self.assertGreater(res.ema_high, res.ema_low)

    def test_reported_band_matches_the_data(self):
        res = self.f.check("BULLISH", 101.0, self.df)
        self.assertAlmostEqual(res.ema_high, 100.0, places=6)
        self.assertAlmostEqual(res.ema_low, 98.0, places=6)


class TestBandRefusesToGuess(unittest.TestCase):
    """Every failure path must block, never fall through permissively."""

    def setUp(self):
        self.f = EMABandFilter()

    def test_short_history_blocks(self):
        res = self.f.check("BULLISH", 101.0, _flat(n=EMA_BAND_PERIOD - 1))
        self.assertFalse(res.allow)
        self.assertIn("Insufficient", res.reason)

    def test_missing_frame_blocks(self):
        self.assertFalse(self.f.check("BULLISH", 101.0, None).allow)

    def test_nonpositive_price_blocks(self):
        self.assertFalse(self.f.check("BULLISH", 0.0, _flat()).allow)

    def test_unknown_direction_blocks(self):
        """
        A direction the band does not recognise can never equal the permitted
        one, so it is refused rather than waved through.
        """
        self.assertFalse(self.f.check("SIDEWAYS", 101.0, _flat()).allow)


class TestTrendingSeries(unittest.TestCase):
    def test_rising_market_permits_longs(self):
        df = _frame([(100.0 + i, 98.0 + i) for i in range(60)])
        price = 200.0                                  # far above the band
        res = EMABandFilter().check("BULLISH", price, df)
        self.assertTrue(res.allow)
        self.assertEqual(res.position, "ABOVE")

    def test_falling_market_vetoes_longs(self):
        df = _frame([(200.0 - i, 198.0 - i) for i in range(60)])
        res = EMABandFilter().check("BULLISH", 100.0, df)
        self.assertFalse(res.allow)
        self.assertEqual(res.permitted_direction, "BEARISH")


if __name__ == "__main__":
    unittest.main()
