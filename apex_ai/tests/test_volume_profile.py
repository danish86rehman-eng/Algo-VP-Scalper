"""
Volume profile, regime classifier and the VP location gate.

The value-area assertions are built on hand-constructed histograms whose answer
can be worked out on paper, so a failure points at the arithmetic rather than at
market data having moved.
"""
import unittest

import numpy as np
import pandas as pd

from scalper import decision_params as DP
from scalper.regime_classifier import (
    REGIME_RANGING, REGIME_TRENDING_DOWN, REGIME_TRENDING_UP, REGIME_UNKNOWN,
    REGIME_VOLATILE, RegimeClassifier, autocorrelation, efficiency_ratio,
)
from scalper.volume_profile import (
    LOC_ABOVE_VAH, LOC_AT_POC, LOC_AT_VAH, LOC_AT_VAL, LOC_BELOW_VAL,
    LOC_LOWER_VALUE, LOC_UPPER_VALUE, _value_area, build_profile_auto,
    build_volume_profile, classify_location, is_poc_dead_zone,
)
from scalper.vp_gate import (
    MODE_ALWAYS, MODE_POC_ONLY, MODE_RANGING_ONLY, VolumeProfileGate,
)


def ou_range(n, centre=100.0, pull=0.05, sigma=0.35, seed=11):
    """
    Mean-reverting (Ornstein-Uhlenbeck) closes — a realistic balancing
    market. Deliberately NOT a smooth sine: a sine's returns are locally
    persistent, so it reports lag-1 autocorrelation around +0.97 and
    classifies as TRENDING even though it goes nowhere. Real ranges are
    noisy, and measured XAUUSD autocorrelation sits near +0.11.
    """
    rng = np.random.default_rng(seed)
    out = [centre]
    for _ in range(n - 1):
        out.append(out[-1] + (centre - out[-1]) * pull + rng.normal(0, sigma))
    return np.array(out)


def bars(rows):
    """rows: list of (high, low, volume) -> frame the profile builder accepts."""
    return pd.DataFrame([
        {"high": h, "low": l, "close": (h + l) / 2.0, "tick_volume": v}
        for h, l, v in rows
    ])


class ValueAreaAlgorithmTests(unittest.TestCase):
    """The greedy expansion transcribed from MQL5 art. 23169."""

    def test_expands_into_the_heavier_side(self):
        # POC at index 3. Above it: 5. Below it: 9. The heavier side is below,
        # so the first absorption must be downward.
        hist = np.array([1.0, 2.0, 9.0, 20.0, 5.0, 1.0])
        upper, lower, vol = _value_area(hist, poc_bin=3, va_threshold=25.0)
        self.assertEqual(lower, 2, "expansion should take the heavier lower bin")
        self.assertEqual(upper, 3)
        self.assertEqual(vol, 29.0)

    def test_ties_resolve_upward(self):
        # Equal neighbours. Art. 23169 uses `vol_above >= vol_below`, so the
        # upper bin wins. Reproduced deliberately, not corrected.
        hist = np.array([1.0, 7.0, 20.0, 7.0, 1.0])
        upper, lower, _ = _value_area(hist, poc_bin=2, va_threshold=26.0)
        self.assertEqual(upper, 3)
        self.assertEqual(lower, 2)

    def test_exhausted_side_does_not_stop_expansion(self):
        # POC is the top bin: the upper side is exhausted immediately. The
        # sentinel must let expansion continue downward rather than break.
        hist = np.array([3.0, 4.0, 10.0])
        upper, lower, vol = _value_area(hist, poc_bin=2, va_threshold=13.0)
        self.assertEqual(upper, 2)
        self.assertEqual(lower, 1)
        self.assertEqual(vol, 14.0)

    def test_both_sides_exhausted_breaks(self):
        hist = np.array([5.0])
        upper, lower, vol = _value_area(hist, poc_bin=0, va_threshold=99.0)
        self.assertEqual((upper, lower, vol), (0, 0, 5.0))


class ProfileConstructionTests(unittest.TestCase):

    def test_poc_lands_on_the_heaviest_price(self):
        # 40 bars of noise around 100, then heavy volume parked at 105.
        rows = [(100.5, 99.5, 10) for _ in range(40)]
        rows += [(105.2, 104.8, 500) for _ in range(10)]
        prof = build_volume_profile(bars(rows), bin_size=0.5)
        self.assertIsNotNone(prof)
        self.assertTrue(prof.valid)
        self.assertGreaterEqual(prof.poc, 104.0)
        self.assertLessEqual(prof.poc, 105.5)

    def test_value_area_encloses_roughly_the_requested_fraction(self):
        rng = np.random.default_rng(7)
        centre = rng.normal(100.0, 1.0, 300)
        rows = [(c + 0.2, c - 0.2, 50) for c in centre]
        prof = build_volume_profile(bars(rows), bin_size=0.1, va_pct=0.70)
        self.assertIsNotNone(prof)
        # Greedy expansion overshoots by at most one bin, so it is at or just
        # above the target and never below it.
        self.assertGreaterEqual(prof.value_area_pct_actual, 0.70)
        self.assertLess(prof.value_area_pct_actual, 0.80)
        self.assertLess(prof.val, prof.poc)
        self.assertGreater(prof.vah, prof.poc)

    def test_zero_volume_bars_are_weighted_one_not_dropped(self):
        # Art. 23169: a feed reporting no volume must not build an empty
        # histogram. Every bar here reports zero.
        rows = [(100.0 + i * 0.1, 99.0 + i * 0.1, 0) for i in range(50)]
        prof = build_volume_profile(bars(rows), bin_size=0.1)
        self.assertIsNotNone(prof)
        self.assertAlmostEqual(prof.total_volume, 50.0, places=6)

    def test_flat_bar_deposits_into_one_bin(self):
        rows = [(100.0, 100.0, 5) for _ in range(10)]
        rows += [(101.0, 99.0, 1) for _ in range(10)]
        prof = build_volume_profile(bars(rows), bin_size=0.1)
        self.assertIsNotNone(prof)
        self.assertAlmostEqual(prof.poc, 100.0, places=1)

    def test_rejects_degenerate_and_out_of_range_inputs(self):
        self.assertIsNone(build_volume_profile(None, 0.5))
        self.assertIsNone(build_volume_profile(bars([(1, 1, 1)]), 0.5))
        # Flat series: high == low across the frame, no range to bin.
        self.assertIsNone(build_volume_profile(
            bars([(100.0, 100.0, 5)] * 20), bin_size=0.1))
        # Bin so coarse the histogram falls under MIN_BINS.
        self.assertIsNone(build_volume_profile(
            bars([(101.0, 99.0, 5)] * 20), bin_size=100.0))
        with self.assertRaises(ValueError):
            build_volume_profile(bars([(101, 99, 5)] * 20), 0.1, va_pct=1.5)

    def test_auto_bin_sizing_hits_the_target_row_count(self):
        rows = [(100.0 + i * 0.05, 99.0 + i * 0.05, 10) for i in range(100)]
        prof = build_profile_auto(bars(rows), target_bins=60)
        self.assertIsNotNone(prof)
        self.assertEqual(prof.bin_count, 61)  # ceil(range/bin) + 1


class LocationTests(unittest.TestCase):

    def setUp(self):
        rows = [(100.0 + i * 0.05, 99.0 + i * 0.05, 10) for i in range(100)]
        self.prof = build_profile_auto(bars(rows), target_bins=60)
        self.width = self.prof.value_area_width

    def test_poc_dead_zone_beats_every_other_label(self):
        # The rule that would have vetoed the 2026-08-25 16:45 long.
        at_poc = self.prof.poc + 0.001
        self.assertEqual(
            classify_location(at_poc, self.prof, 0.10 * self.width,
                              0.10 * self.width),
            LOC_AT_POC)
        self.assertTrue(is_poc_dead_zone(at_poc, self.prof, 0.10 * self.width))

    def test_poc_band_wins_when_it_overlaps_an_edge_zone(self):
        # A band wide enough to swallow the whole value area must still report
        # AT_POC at the edges — the dead zone cannot be skipped by a tight
        # profile, which is when it matters most.
        huge = self.width
        self.assertEqual(
            classify_location(self.prof.vah, self.prof, 0.10 * self.width, huge),
            LOC_AT_POC)

    def test_edges_and_outsides(self):
        tol = 0.02 * self.width
        band = 0.02 * self.width
        self.assertEqual(
            classify_location(self.prof.vah, self.prof, tol, band), LOC_AT_VAH)
        self.assertEqual(
            classify_location(self.prof.val, self.prof, tol, band), LOC_AT_VAL)
        self.assertEqual(
            classify_location(self.prof.vah + 10 * tol, self.prof, tol, band),
            LOC_ABOVE_VAH)
        self.assertEqual(
            classify_location(self.prof.val - 10 * tol, self.prof, tol, band),
            LOC_BELOW_VAL)

    def test_halves_of_value(self):
        tol = 0.01 * self.width
        band = 0.01 * self.width
        upper = self.prof.poc + 0.25 * self.width
        lower = self.prof.poc - 0.25 * self.width
        self.assertEqual(classify_location(upper, self.prof, tol, band),
                         LOC_UPPER_VALUE)
        self.assertEqual(classify_location(lower, self.prof, tol, band),
                         LOC_LOWER_VALUE)


class RegimeClassifierTests(unittest.TestCase):

    def test_efficiency_ratio_bounds(self):
        straight = np.arange(100, dtype=float)
        self.assertAlmostEqual(efficiency_ratio(straight), 1.0, places=6)
        # Even length: ends where it started, so displacement is exactly 0.
        closed_loop = np.array([100.0, 101.0] * 50 + [100.0])
        self.assertAlmostEqual(efficiency_ratio(closed_loop), 0.0, places=9)
        # Odd length ends one step out, so ER is 1/(n-1), not 0.
        self.assertLess(efficiency_ratio(np.array([100.0, 101.0] * 50)), 0.02)

    def test_autocorrelation_of_flat_series_is_zero_not_nan(self):
        self.assertEqual(autocorrelation(np.zeros(50)), 0.0)

    def test_clean_uptrend_classifies_trending_up(self):
        closes = 100.0 + np.arange(200) * 0.5
        df = pd.DataFrame({"close": closes})
        read = RegimeClassifier().classify(df)
        self.assertEqual(read.regime, REGIME_TRENDING_UP)

    def test_clean_downtrend_classifies_trending_down(self):
        closes = 200.0 - np.arange(200) * 0.5
        df = pd.DataFrame({"close": closes})
        read = RegimeClassifier().classify(df)
        self.assertEqual(read.regime, REGIME_TRENDING_DOWN)

    def test_noisy_mean_reverting_range_classifies_ranging(self):
        read = RegimeClassifier().classify(
            pd.DataFrame({"close": ou_range(300)}))
        self.assertEqual(read.regime, REGIME_RANGING)

    def test_smooth_sine_reads_as_trending_under_ANY(self):
        """
        Recorded because it is surprising and load-bearing: a smooth
        oscillation has locally persistent returns, so autocorrelation
        calls it a trend while the efficiency ratio calls it a range.
        Under combine="ANY" the trend reading wins.
        """
        closes = 100.0 + np.sin(np.arange(400) / 5.0) * 2.0
        df = pd.DataFrame({"close": closes})
        read = RegimeClassifier(combine="ANY").classify(df)
        self.assertGreater(read.autocorr, 0.9)
        self.assertLess(read.efficiency_ratio, 0.2)
        self.assertTrue(read.is_trending)
        # BOTH requires the two statistics to agree, so the same series
        # reads as a range.
        self.assertEqual(
            RegimeClassifier(combine="BOTH").classify(df).regime,
            REGIME_RANGING)

    def test_combine_mode_is_validated(self):
        with self.assertRaises(ValueError):
            RegimeClassifier(combine="EITHER")

    def test_volatility_spike_takes_precedence(self):
        rng = np.random.default_rng(3)
        quiet = 100.0 + np.cumsum(rng.normal(0, 0.01, 200))
        loud = quiet[-1] + np.cumsum(rng.normal(0, 2.0, 60))
        df = pd.DataFrame({"close": np.concatenate([quiet, loud])})
        read = RegimeClassifier(lookback=200).classify(df)
        self.assertEqual(read.regime, REGIME_VOLATILE)

    def test_short_history_is_unknown_not_ranging(self):
        df = pd.DataFrame({"close": np.arange(30, dtype=float)})
        self.assertEqual(RegimeClassifier(lookback=100).classify(df).regime,
                         REGIME_UNKNOWN)

    def test_lookback_floor_is_enforced(self):
        with self.assertRaises(ValueError):
            RegimeClassifier(lookback=10)


class VPGateTests(unittest.TestCase):
    """
    Both directions and the invalidation case, per the apex-ops checklist.
    """

    def setUp(self):
        # A balanced, range-bound frame: the profile is meaningful and the
        # regime classifier reports RANGING.
        mid = ou_range(400)
        self.h4 = pd.DataFrame({
            "high": mid + 0.4, "low": mid - 0.4,
            "close": mid, "tick_volume": 100.0,
        })
        self.regime_df = pd.DataFrame({"close": mid})
        self.gate = VolumeProfileGate(
            mode=MODE_RANGING_ONLY, profile_bars=200, target_bins=60,
            value_area_pct=0.70, poc_band_frac=0.10,
            edge_tolerance_frac=0.10)
        self.prof = self.gate.build_profile(self.h4)

    def test_long_at_val_is_allowed(self):
        r = self.gate.check("BULLISH", self.prof.val, self.h4, self.regime_df)
        self.assertTrue(r.allow)
        self.assertFalse(r.abstained)
        self.assertEqual(r.location, LOC_AT_VAL)

    def test_short_at_vah_is_allowed(self):
        r = self.gate.check("BEARISH", self.prof.vah, self.h4, self.regime_df)
        self.assertTrue(r.allow)
        self.assertEqual(r.location, LOC_AT_VAH)

    def test_long_at_vah_is_vetoed(self):
        r = self.gate.check("BULLISH", self.prof.vah, self.h4, self.regime_df)
        self.assertFalse(r.allow)

    def test_short_at_val_is_vetoed(self):
        r = self.gate.check("BEARISH", self.prof.val, self.h4, self.regime_df)
        self.assertFalse(r.allow)

    def test_both_directions_vetoed_at_poc(self):
        for direction in ("BULLISH", "BEARISH"):
            r = self.gate.check(direction, self.prof.poc, self.h4,
                                self.regime_df)
            self.assertFalse(r.allow, f"{direction} should be vetoed at POC")
            self.assertTrue(r.at_poc)

    def test_poc_only_mode_allows_a_wrong_side_entry(self):
        gate = VolumeProfileGate(
            mode=MODE_POC_ONLY, profile_bars=200, target_bins=60,
            value_area_pct=0.70, poc_band_frac=0.10, edge_tolerance_frac=0.10)
        # A long at VAH is wrong-side, but POC_ONLY enforces only the dead zone.
        self.assertTrue(gate.check("BULLISH", self.prof.vah, self.h4).allow)
        self.assertFalse(gate.check("BULLISH", self.prof.poc, self.h4).allow)

    def test_ranging_only_abstains_in_a_trend(self):
        trend = pd.DataFrame({"close": 100.0 + np.arange(400) * 0.5})
        r = self.gate.check("BULLISH", self.prof.vah, self.h4, trend)
        self.assertTrue(r.allow)
        self.assertTrue(r.abstained, "gate must abstain, not silently pass")
        self.assertEqual(r.regime, REGIME_TRENDING_UP)

    def test_always_mode_applies_in_a_trend(self):
        gate = VolumeProfileGate(
            mode=MODE_ALWAYS, profile_bars=200, target_bins=60,
            value_area_pct=0.70, poc_band_frac=0.10, edge_tolerance_frac=0.10)
        trend = pd.DataFrame({"close": 100.0 + np.arange(400) * 0.5})
        r = gate.check("BULLISH", self.prof.vah, self.h4, trend)
        self.assertFalse(r.allow)

    def test_missing_profile_abstains_rather_than_blocking(self):
        r = self.gate.check("BULLISH", 100.0, pd.DataFrame(), self.regime_df)
        self.assertTrue(r.allow)
        self.assertTrue(r.abstained)

    def test_missing_regime_frame_abstains_in_ranging_only(self):
        r = self.gate.check("BULLISH", self.prof.val, self.h4, None)
        self.assertTrue(r.allow)
        self.assertTrue(r.abstained)

    def test_unknown_mode_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            VolumeProfileGate(mode="SOMETIMES", profile_bars=10,
                              target_bins=10, value_area_pct=0.7,
                              poc_band_frac=0.1, edge_tolerance_frac=0.1)


class RegressionOfTheMeasuredLossTests(unittest.TestCase):
    """
    The specific trade this gate was built for.

    XAUUSD 2026-08-25 16:45:25 UTC, BUY at 4642.26, stopped at 4634.49 for
    -$23.29, with the 42-bar H4 POC at 4641.33 and a value area 248.43 wide.
    """

    ENTRY = 4642.26
    POC = 4641.33
    VAH = 4691.02
    VAL = 4442.59

    def test_entry_sits_inside_any_sane_poc_band(self):
        width = self.VAH - self.VAL
        distance = abs(self.ENTRY - self.POC)
        self.assertAlmostEqual(distance, 0.93, places=2)
        # The configured default must catch it by a wide margin.
        self.assertLess(distance, DP.VP_POC_BAND_FRAC * width)

    def test_the_two_winning_shorts_were_also_at_the_poc(self):
        # Recorded so nobody later claims this gate would have kept them.
        width = self.VAH - self.VAL
        band = DP.VP_POC_BAND_FRAC * width
        # (entry, POC of the profile as it stood at that moment)
        for entry, poc in ((4642.94, 4647.55), (4638.78, 4641.33)):
            self.assertLess(
                abs(entry - poc), band,
                f"winning short at {entry} was inside the POC band too")


class DecisionParamsAreSaneTests(unittest.TestCase):

    def test_gate_ships_disabled(self):
        # §13.5: nothing is promoted without three consistent folds.
        self.assertFalse(DP.VP_GATE_ENABLED)

    def test_fractions_are_in_range(self):
        self.assertTrue(0.0 < DP.VP_POC_BAND_FRAC < 0.5)
        self.assertTrue(0.0 < DP.VP_EDGE_TOLERANCE_FRAC < 0.5)
        self.assertTrue(0.0 < DP.VP_VALUE_AREA_PCT < 1.0)

    def test_profile_window_fits_inside_the_fetch(self):
        self.assertLessEqual(DP.VP_PROFILE_BARS, DP.VP_PROFILE_FETCH_BARS)

    def test_mode_and_backend_are_valid(self):
        from scalper.vp_gate import MODES
        self.assertIn(DP.VP_GATE_MODE, MODES)
        self.assertIn(DP.VP_REGIME_BACKEND, DP.VP_REGIME_BACKENDS)


if __name__ == "__main__":
    unittest.main()
