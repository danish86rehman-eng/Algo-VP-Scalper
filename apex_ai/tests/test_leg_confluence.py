"""
Tests for `VP_LEG_CONFLUENCE` (ledger L-015).

The operator's hypothesis is "quality over quantity": two completed H4 swing
legs supply the locations, an existing trigger supplies the entry. Three ways
that could be true in the report and false in the code:

  * **look-ahead** — a leg is built from a pivot the live agent could not have
    confirmed yet, which would make every backtest unfalsifiable;
  * **it silently admits everything** — a filter whose veto never fires reads
    as "harmless" in the numbers while testing nothing;
  * **it silently vetoes everything** — a filter that cannot build its legs and
    blocks on missing data measures warmup coverage, not location.

All three are asserted here.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scalper import decision_params as dp
from scalper import leg_confluence as lc

T0 = datetime(2026, 5, 1, tzinfo=timezone.utc)


def h4(bars):
    """Build a closed-H4 frame from (open, high, low, close[, volume])."""
    rows = []
    for i, bar in enumerate(bars):
        o, h, l, c = bar[:4]
        rows.append({
            "time": pd.Timestamp(T0 + timedelta(hours=4 * i)),
            "open": float(o), "high": float(h), "low": float(l),
            "close": float(c),
            "tick_volume": float(bar[4]) if len(bar) > 4 else 1000.0,
            "spread": 5,
        })
    return pd.DataFrame(rows)


def zigzag(turns=(4400.0, 4700.0, 4550.0, 4800.0, 4600.0), per_leg=10, pad=6):
    """Alternating turning points, ramped between, with pads at both ends.

    Two constraints the first draft of this fixture missed:

    * `StructureEngine.analyze` returns nothing until it has **4** alternating
      swings, so a single low->high->low zigzag yields no pivots at all;
    * a pivot confirms only once `lookback` bars have closed on BOTH sides, so
      the final turn needs a trailing pad or it is never confirmed.

    The newest three turns here are 4550 -> 4800 -> 4600, which is the
    low-high-low pair the operator drew.
    """
    first = turns[0]
    bars = [(first + 8, first + 12, first, first + 6, 600.0)] * pad
    for a, b in zip(turns[:-1], turns[1:]):
        ramp = np.linspace(a, b, per_leg)
        for k, (x, y) in enumerate(zip(ramp[:-1], ramp[1:])):
            # Volume, not price, carries the hump. Adding a price
            # consolidation mid-leg looked like the realistic choice and was
            # wrong: it grows extra pivots, `_pick_three` then selects two
            # 3-bar stubs, and the pair is refused. Weighting volume leaves
            # the price path — and therefore every pivot — untouched, while
            # still giving the profile an interior POC and real HVN/LVN bins.
            # Three humped bars, not one, and a wide margin. `_accumulate`
            # spreads a bar's volume across the bins its RANGE covers, so a
            # narrow bar concentrates while a wide one dilutes — a single
            # 4000-volume ramp bar lost the POC to a 600-volume 4-point turn
            # bar sitting in one bin. The hump has to beat concentration, not
            # just total volume.
            vol = 8000.0 if abs(k - per_leg // 2) <= 1 else 400.0
            bars.append((x, max(x, y) + 4, min(x, y) - 4, y, vol))
        # The turning bar itself: the extreme, with the close pulled back so
        # the ramp that follows does not immediately exceed it.
        step = 1 if b > a else -1
        bars.append((b - 6 * step, b + (4 if step > 0 else 0),
                     b - (0 if step > 0 else 4), b - 8 * step, 600.0))
    # The end pad is exactly `swing_lookback` bars: enough for the final turn
    # to confirm, too few for a pivot to form INSIDE the pad (that would need
    # `lookback` bars after it, which do not exist). A longer pad grows a
    # spurious sixth pivot and the newest leg comes out too short to qualify.
    last = turns[-1]
    bars += [(last + 4 * k, last + 4 * k + 10, last + 4 * k - 2,
              last + 4 * k + 6, 600.0) for k in range(1, 4)]
    return bars


def build(frame=None):
    return lc.build_leg_pair(
        frame if frame is not None else h4(zigzag()), "XAUUSD",
        swing_lookback=dp.LEG_CONF_SWING_LOOKBACK,
        min_leg_bars=dp.LEG_CONF_MIN_LEG_BARS,
        min_leg_atr=dp.LEG_CONF_MIN_LEG_ATR,
        atr_period=dp.LEG_CONF_ATR_PERIOD,
        target_bins=dp.LEG_CONF_TARGET_BINS,
        value_area_pct=dp.LEG_CONF_VALUE_AREA_PCT,
        node_stddev_mult=dp.LEG_CONF_NODE_STDDEV_MULT)


class TestLegConstruction(unittest.TestCase):

    def test_two_legs_are_found_and_alternate(self):
        pair = build()
        self.assertIsNotNone(pair, "no leg pair from a clean zigzag")
        self.assertNotEqual(pair.leg_a.direction, pair.leg_b.direction)
        self.assertEqual(pair.leg_a.end_index, pair.leg_b.start_index,
                         "legs must share the middle pivot")

    def test_legs_are_completed_not_open_ended(self):
        """The difference from L-014's anchored profile: a leg ends at its
        terminal pivot, not at the last closed bar. If it ran to the frame end
        the levels would move every bar and stop being levels."""
        pair = build()
        frame = h4(zigzag())
        self.assertLess(pair.leg_b.end_index, len(frame) - 1)

    def test_profiles_are_valid_and_ordered(self):
        pair = build()
        for leg in (pair.leg_a, pair.leg_b):
            self.assertTrue(leg.profile.valid)
            # <= is the real invariant: a uniform leg can put the POC on a
            # value-area edge. Interiority is asserted separately, on a
            # fixture built to have a volume hump.
            self.assertLessEqual(leg.profile.val, leg.profile.poc)
            self.assertLessEqual(leg.profile.poc, leg.profile.vah)
            self.assertLess(leg.profile.val, leg.profile.vah)

    def test_poc_is_interior_when_volume_is_humped(self):
        pair = build()
        for leg in (pair.leg_a, pair.leg_b):
            self.assertLess(leg.profile.val, leg.profile.poc)
            self.assertLess(leg.profile.poc, leg.profile.vah)

    def test_short_frame_yields_no_pair(self):
        self.assertIsNone(build(h4([(100, 101, 99, 100)] * 5)))

    def test_flat_frame_yields_no_pair(self):
        """No swing, no leg. Must return None rather than a degenerate
        profile whose 'levels' are an artefact of the binning."""
        self.assertIsNone(build(h4([(100, 100.5, 99.5, 100)] * 60)))


class TestNodes(unittest.TestCase):

    def test_nodes_are_found(self):
        pair = build()
        total = (len(pair.leg_a.hvn_prices) + len(pair.leg_a.lvn_prices)
                 + len(pair.leg_b.hvn_prices) + len(pair.leg_b.lvn_prices))
        self.assertGreater(total, 0, "no HVN/LVN classified on a real leg")

    def test_empty_bins_are_not_low_volume_nodes(self):
        """A bin the leg never visited is not an LVN. Counting untouched bins
        would label the whole periphery low-volume and make the veto fire on
        everything — a filter that blocks everything is not selective, it is
        broken."""
        pair = build()
        for leg in (pair.leg_a, pair.leg_b):
            for price in leg.lvn_prices:
                self.assertGreaterEqual(price, leg.profile.profile_low - 1e-9)
                self.assertLessEqual(price, leg.profile.profile_high + 1e-9)

    def test_hvn_and_lvn_are_disjoint(self):
        pair = build()
        for leg in (pair.leg_a, pair.leg_b):
            self.assertEqual(set(leg.hvn_prices) & set(leg.lvn_prices), set())


class TestCausality(unittest.TestCase):
    """What "causal" means here, stated precisely.

    The first draft of this class asserted that appending future bars leaves
    the pair unchanged. That is not causality and it is not even desirable:
    handing the builder a longer frame means asking it for a decision at a
    LATER time, and by then new pivots have genuinely confirmed. The test
    failed for the right reason and was wrong.

    The properties that actually matter are three:

      1. a terminal pivot is never reported before `lookback` bars have closed
         after it, so no level can be known before the live agent could know
         it;
      2. a COMPLETED leg is static — this is the whole difference from L-014's
         pivot->now profile, whose levels moved every bar;
      3. when new pivots do confirm, the pair re-anchors rather than going
         stale.
    """

    def test_terminal_pivot_is_never_at_the_frame_edge(self):
        frame = h4(zigzag())
        pair = build(frame)
        self.assertLessEqual(pair.leg_b.end_index,
                             len(frame) - dp.LEG_CONF_SWING_LOOKBACK - 1)

    def test_completed_legs_are_static(self):
        """Append fewer bars than `lookback`, so no new pivot CAN confirm.
        Every level must be bit-identical: a completed leg that drifted would
        be L-014's re-anchoring profile wearing a different name."""
        base = zigzag()
        a = build(h4(base))
        b = build(h4(base + [(4620, 4632, 4614, 4628, 500.0)] * 2))
        self.assertIsNotNone(b)
        self.assertEqual((a.leg_a.start_index, a.leg_a.end_index,
                          a.leg_b.start_index, a.leg_b.end_index),
                         (b.leg_a.start_index, b.leg_a.end_index,
                          b.leg_b.start_index, b.leg_b.end_index))
        for x, y in ((a.leg_a, b.leg_a), (a.leg_b, b.leg_b)):
            self.assertAlmostEqual(x.profile.poc, y.profile.poc, places=9)
            self.assertAlmostEqual(x.profile.vah, y.profile.vah, places=9)
            self.assertAlmostEqual(x.profile.val, y.profile.val, places=9)

    def test_verdict_is_static_while_the_legs_are(self):
        base = zigzag()
        a = build(h4(base))
        b = build(h4(base + [(4620, 4632, 4614, 4628, 500.0)] * 2))
        price = a.leg_a.profile.poc
        for mode in lc.MODES:
            va = lc.evaluate(price, a, mode, dp.LEG_CONF_ZONE_ATR,
                             dp.LEG_CONF_LVN_ATR)
            vb = lc.evaluate(price, b, mode, dp.LEG_CONF_ZONE_ATR,
                             dp.LEG_CONF_LVN_ATR)
            self.assertEqual((va.label, va.admit), (vb.label, vb.admit), mode)

    def test_new_pivots_re_anchor_the_pair(self):
        """The complement: once a genuinely new swing confirms, the filter
        must follow it. A pair frozen on stale levels would quietly stop
        being a location filter at all."""
        base = zigzag()
        a = build(h4(base))
        moved = build(h4(base + list(zigzag(turns=(4600.0, 5100.0, 4900.0),
                                            per_leg=10, pad=0))))
        self.assertIsNotNone(moved)
        self.assertNotEqual(a.leg_b.end_index, moved.leg_b.end_index)


class TestClassification(unittest.TestCase):

    def test_price_at_a_leg_poc_is_at_least_at_level(self):
        pair = build()
        v = lc.classify(pair.leg_a.profile.poc, pair, dp.LEG_CONF_ZONE_ATR,
                        dp.LEG_CONF_LVN_ATR)
        self.assertIn(v.label, (lc.LOC_AT_LEVEL, lc.LOC_CONFLUENCE))
        self.assertTrue(any(n.endswith("POC") for n in v.matched_levels))

    def test_far_away_price_matches_nothing(self):
        pair = build()
        v = lc.classify(pair.leg_a.profile.vah + 50 * pair.atr_h4, pair,
                        dp.LEG_CONF_ZONE_ATR, dp.LEG_CONF_LVN_ATR)
        self.assertIn(v.label, (lc.LOC_NO_LEVEL, lc.LOC_IN_LVN))
        self.assertEqual(v.matched_levels, [])

    def test_confluence_requires_both_legs(self):
        pair = build()
        # A tolerance wide enough to touch both legs everywhere must produce
        # CONFLUENCE; the label is not decorative.
        v = lc.classify(pair.leg_a.profile.poc, pair, zone_atr=1000.0,
                        lvn_atr=0.0)
        self.assertEqual(v.label, lc.LOC_CONFLUENCE)


class TestModes(unittest.TestCase):

    def test_no_opinion_always_admits(self):
        """A filter that cannot build its legs has not judged the trade.
        Vetoing on missing data would measure warmup coverage, not location."""
        for mode in lc.MODES:
            v = lc.evaluate(100.0, None, mode, dp.LEG_CONF_ZONE_ATR,
                            dp.LEG_CONF_LVN_ATR)
            self.assertEqual(v.label, lc.LOC_NO_OPINION)
            self.assertTrue(v.admit, mode)

    def test_modes_are_strictly_ordered_in_strictness(self):
        """CONFLUENCE_ONLY admits a subset of AT_LEVEL, which admits a subset
        of LVN_VETO. If that ordering broke, the three arms would not be
        measuring what their names say."""
        for label in (lc.LOC_CONFLUENCE, lc.LOC_AT_LEVEL, lc.LOC_IN_LVN,
                      lc.LOC_NO_LEVEL):
            v = lc.ConfluenceVerdict(label=label)
            strict = lc.admits(v, lc.MODE_CONFLUENCE_ONLY)
            mid = lc.admits(v, lc.MODE_AT_LEVEL)
            loose = lc.admits(v, lc.MODE_LVN_VETO)
            self.assertLessEqual(int(strict), int(mid), label)
            self.assertLessEqual(int(mid), int(loose), label)

    def test_each_mode_both_admits_and_vetoes_something(self):
        """Guard against a filter that is silently a no-op or silently a
        total block — either would look tidy in a results table."""
        labels = (lc.LOC_CONFLUENCE, lc.LOC_AT_LEVEL, lc.LOC_IN_LVN,
                  lc.LOC_NO_LEVEL)
        for mode in lc.MODES:
            got = {lc.admits(lc.ConfluenceVerdict(label=x), mode)
                   for x in labels}
            self.assertEqual(got, {True, False}, mode)

    def test_lvn_veto_blocks_only_the_lvn_label(self):
        for label in (lc.LOC_CONFLUENCE, lc.LOC_AT_LEVEL, lc.LOC_NO_LEVEL):
            self.assertTrue(lc.admits(lc.ConfluenceVerdict(label=label),
                                      lc.MODE_LVN_VETO))
        self.assertFalse(lc.admits(lc.ConfluenceVerdict(label=lc.LOC_IN_LVN),
                                   lc.MODE_LVN_VETO))

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            lc.admits(lc.ConfluenceVerdict(label=lc.LOC_AT_LEVEL), "NOPE")


class TestShipsOff(unittest.TestCase):

    def test_disabled_by_default(self):
        self.assertFalse(dp.LEG_CONF_ENABLED)

    def test_default_mode_is_valid(self):
        self.assertIn(dp.LEG_CONF_MODE, lc.MODES)

    def test_node_threshold_matches_the_cited_source(self):
        """MQL5 CodeBase 76264 default. §13.7: design evidence only — that
        article publishes no win rate, PF or sample size."""
        self.assertEqual(dp.LEG_CONF_NODE_STDDEV_MULT, 1.0)


if __name__ == "__main__":
    unittest.main()
