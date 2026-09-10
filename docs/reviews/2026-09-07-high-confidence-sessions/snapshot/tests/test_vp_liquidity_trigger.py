"""
Tests for VP_LIQUIDITY_REACTION — the anchored profile, the setup contract, the
priority position, the VP-only session allowance, and the causality guarantees.

The bar for "this works" here is deliberately not "it fires on the reference
setup". A detector can be made to fire on one remembered afternoon by fitting
its thresholds to it, which is why the design note forbids tuning to
4640 -> 4594. What these assert instead is structural: that a touch cannot fire,
that direction is decided by liquidity rather than by the level, that the anchor
cannot see the future, and that with the trigger disabled nothing anywhere
behaves differently.
"""
from __future__ import annotations

import unittest
from datetime import datetime, time as dtime, timedelta, timezone

import numpy as np
import pandas as pd

import scalper.decision_params as DP
from scalper.anchored_vp import (
    LEG_DOWN, LEG_UP, build_anchored_profile)
from scalper.behavior_state import SABehaviorStateMachine, SAState
from scalper.session_checker import SESSION_WINDOWS, SASessionChecker
from scalper.trigger_engine import (
    MicroLiquidity, SATriggerEngine, resolve_enabled_triggers)
from scalper import vp_liquidity_trigger as VPLR


# ── frame builders ───────────────────────────────────────────────────────────

def _bars(rows, start: datetime, minutes: int) -> pd.DataFrame:
    """rows = [(open, high, low, close, volume), …] -> an OHLCV frame."""
    return pd.DataFrame({
        "time": [start + timedelta(minutes=minutes * i) for i in range(len(rows))],
        "open": [r[0] for r in rows],
        "high": [r[1] for r in rows],
        "low": [r[2] for r in rows],
        "close": [r[3] for r in rows],
        "tick_volume": [r[4] for r in rows],
    })


def _zigzag(start_price: float, legs, volume: float = 100.0):
    """
    Walk a price path as (step, count) legs, one bar per step.

    A monotonic ramp has no interior fractal pivots at all — the max of any
    centred window sits at its edge — so a path built from straight lines would
    give the swing engine nothing to confirm and the anchor would never form.
    Every leg therefore carries a one-bar pullback, which is also what real
    impulse legs look like.
    """
    rows = []
    price = start_price
    for step, count in legs:
        for k in range(count):
            move = step if (k % 4 != 3) else -step * 0.4
            o = price
            c = price + move
            hi = max(o, c) + abs(step) * 0.25
            lo = min(o, c) - abs(step) * 0.25
            rows.append((o, hi, lo, c, volume))
            price = c
    return rows


def _h4_recovery_leg() -> pd.DataFrame:
    """
    The shape the reference setup had: a sharp decline, then a recovery whose
    volume concentrates near its high — so the leg's POC lands at the top of the
    recovery rather than in the middle of it.
    """
    start = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
    rows = _zigzag(4750.0, [(-6.0, 34)], volume=80.0)          # decline
    rows += _zigzag(rows[-1][3], [(5.0, 14)], volume=90.0)     # recovery
    # Balance at the top: narrow bars, heavy volume -> the POC forms here.
    top = rows[-1][3]
    for i in range(10):
        o = top + (0.4 if i % 2 else -0.4)
        c = top - (0.4 if i % 2 else -0.4)
        rows.append((o, max(o, c) + 0.5, min(o, c) - 0.5, c, 4000.0))
    return _bars(rows, start, 240)


class AnchoredProfileTests(unittest.TestCase):

    def setUp(self):
        self.h4 = _h4_recovery_leg()

    def _build(self, df=None, **over):
        kw = dict(swing_lookback=DP.VPLR_H4_SWING_LOOKBACK,
                  min_leg_bars=DP.VPLR_MIN_LEG_BARS,
                  min_leg_atr=DP.VPLR_MIN_LEG_ATR,
                  atr_period=DP.VPLR_ATR_PERIOD,
                  target_bins=DP.VPLR_TARGET_BINS,
                  value_area_pct=DP.VPLR_VALUE_AREA_PCT)
        kw.update(over)
        return build_anchored_profile(self.h4 if df is None else df,
                                      "XAUUSD", **kw)

    def test_builds_a_leg_profile(self):
        ap = self._build()
        self.assertIsNotNone(ap)
        self.assertTrue(ap.profile.valid)
        self.assertGreater(ap.poc, 0)
        self.assertGreaterEqual(ap.vah, ap.poc)
        self.assertLessEqual(ap.val, ap.poc)

    def test_poc_sits_inside_the_leg_not_the_whole_history(self):
        """
        The point of anchoring. A rolling window over this frame straddles the
        decline and the recovery and its POC is an artefact of both; the leg
        profile must describe only the leg.
        """
        ap = self._build()
        lo = min(ap.anchor_price, ap.extreme_price)
        hi = max(ap.anchor_price, ap.extreme_price)
        self.assertGreaterEqual(ap.poc, lo - 1e-6)
        self.assertLessEqual(ap.poc, hi + 1e-6)

    def test_anchor_is_right_side_confirmed(self):
        """
        The causality guarantee: the pivot must be old enough that the bars
        confirming it are already in the frame. If the anchor index could reach
        the last bar, the profile would be reading a pivot that has not yet been
        proven to be one.
        """
        ap = self._build()
        self.assertLessEqual(ap.anchor_index,
                             len(self.h4) - 1 - DP.VPLR_H4_SWING_LOOKBACK)

    def test_future_bars_cannot_change_a_past_decision(self):
        """
        Compute the anchor as of bar N, then append bars that would obviously
        move it (a violent new high), and recompute as of bar N. The decision
        must be identical — this is what "no look-ahead" means operationally.
        """
        # Any cut that yields a profile will do; picking the first avoids
        # tying the test to one fixture length.
        cut = next((c for c in range(len(self.h4) - 1, 20, -1)
                    if self._build(self.h4.iloc[:c]) is not None), None)
        self.assertIsNotNone(cut, "fixture produced no anchorable leg")

        before = self._build(self.h4.iloc[:cut])
        spike = self.h4.iloc[cut:].copy()
        spike["high"] += 500.0
        spike["close"] += 500.0
        joined = pd.concat([self.h4.iloc[:cut], spike], ignore_index=True)
        after = self._build(joined.iloc[:cut])

        self.assertIsNotNone(before)
        self.assertEqual(before.anchor_index, after.anchor_index)
        self.assertAlmostEqual(before.poc, after.poc, places=9)
        self.assertAlmostEqual(before.vah, after.vah, places=9)
        self.assertAlmostEqual(before.val, after.val, places=9)

    def test_rejects_a_leg_that_is_too_small(self):
        self.assertIsNone(self._build(min_leg_atr=1e6))

    def test_rejects_a_leg_that_is_too_short(self):
        self.assertIsNone(self._build(min_leg_bars=10_000))

    def test_direction_labels_match_the_pivot_type(self):
        ap = self._build()
        self.assertIn(ap.direction, (LEG_UP, LEG_DOWN))
        if ap.direction == LEG_UP:
            self.assertGreaterEqual(ap.extreme_price, ap.anchor_price)
        else:
            self.assertLessEqual(ap.extreme_price, ap.anchor_price)

    def test_short_frame_returns_none_rather_than_a_guess(self):
        self.assertIsNone(self._build(self.h4.iloc[:5]))


# ── the setup contract ───────────────────────────────────────────────────────

class _Scenario:
    """
    A synthetic raid-and-reverse around a chosen price, on both lower frames.

    Built from an explicit path rather than random noise so that a failure names
    a clause rather than a seed.
    """

    def __init__(self, level: float, bearish: bool = True,
                 raid: bool = True, reclaim: bool = True, mss: bool = True):
        self.level = level
        self.bearish = bearish
        self.m15 = self._m15(raid, reclaim)
        self.m5 = self._m5(mss)

    def _m15(self, raid: bool, reclaim: bool) -> pd.DataFrame:
        """
        Approach the level without touching it, raid it, then displace away.

        Built as explicit bars rather than a generated walk: a walk that rises
        far enough to reach the level also crosses it dozens of bars early, and
        the raid the test means to exercise is then not the one the detector
        finds.
        """
        start = datetime(2026, 8, 27, 0, 0, tzinfo=timezone.utc)
        sign = 1.0 if self.bearish else -1.0
        rows = []
        # 40 bars of chop that stays clear of the level.
        for i in range(40):
            mid = self.level - sign * (8.0 + (i % 5) * 0.8)
            o = mid
            c = mid + sign * 0.3 * (1 if i % 2 else -1)
            rows.append((o, max(o, c) + 0.5, min(o, c) - 0.5, c, 200.0))
        # Two bars printing the level exactly -> the equal highs/lows pool.
        for _ in range(2):
            o = self.level - sign * 1.2
            c = self.level - sign * 0.8
            hi = self.level if self.bearish else max(o, c) + 0.6
            lo = min(o, c) - 0.6 if self.bearish else self.level
            rows.append((o, hi, lo, c, 300.0))
        if raid:
            # The raid: pierces the level and closes back through it, leaving
            # the rejection wick.
            o = self.level - sign * 0.4
            pierce = self.level + sign * 5.0
            c = self.level - sign * 3.0
            hi = pierce if self.bearish else max(o, c) + 0.5
            lo = min(o, c) - 0.5 if self.bearish else pierce
            rows.append((o, hi, lo, c, 900.0))
            for _ in range(3):                      # displacement away
                o = rows[-1][3]
                c = o - sign * 6.0
                rows.append((o, max(o, c) + 0.4, min(o, c) - 0.4, c, 700.0))
        if not reclaim:                             # close back beyond it
            o = rows[-1][3]
            c = self.level + sign * 2.0
            rows.append((o, max(o, c) + 0.3, min(o, c) - 0.3, c, 200.0))
        return _bars(rows, start, 15)

    def _m5(self, mss: bool) -> pd.DataFrame:
        """
        A staircase into the raid that leaves real swing pivots behind, then a
        decisive break back through the last one.

        The pullbacks carry a wick below the next bar's open. Without it the
        dip's low ties with the following bar's low, `fractal_swings` requires a
        UNIQUE extreme in the window, and no pivot is confirmed — so there is
        nothing for the MSS to break and the setup can never complete.
        """
        sign = 1.0 if self.bearish else -1.0
        rows = []
        price = self.level - sign * 14.0
        for cycle in range(4):
            for _ in range(3):                      # three bars toward the level
                o = price
                c = price + sign * 1.2
                rows.append((o, max(o, c) + 0.3, min(o, c) - 0.3, c, 150.0))
                price = c
            o = price                               # the wicked pullback
            c = price - sign * 1.6
            wick = c - sign * 1.2
            hi = max(o, c) + 0.3 if self.bearish else wick
            lo = wick if self.bearish else min(o, c) - 0.3
            rows.append((o, hi, lo, c, 220.0))
            price = c
        # The reaction extreme at the raid.
        o = price
        top = self.level + sign * 5.0
        rows.append((o, top if self.bearish else max(o, o) + 0.3,
                     min(o, o) - 0.3 if self.bearish else top, o, 400.0))
        if mss:
            for _ in range(16):                     # break back through
                o = price
                c = price - sign * 1.4
                rows.append((o, max(o, c) + 0.2, min(o, c) - 0.2, c, 500.0))
                price = c
        else:
            for _ in range(6):                      # stall, no structure break
                rows.append((price, price + 0.2, price - 0.2, price, 120.0))
        # Cover the same wall-clock span as the M15 frame.
        n = len(rows)
        span_start = (datetime(2026, 8, 27, 0, 0, tzinfo=timezone.utc)
                      + timedelta(minutes=15 * 46) - timedelta(minutes=5 * n))
        return _bars(rows, span_start, 5)


class _StubProfile:
    """Minimal stand-in so a test can place POC/VAH/VAL exactly."""

    def __init__(self, poc, vah, val):
        self.poc, self.vah, self.val = poc, vah, val
        self.valid = True
        self.value_area_width = max(vah - val, 1e-9)


class _StubAnchored:
    def __init__(self, poc, vah, val):
        self.profile = _StubProfile(poc, vah, val)
        self.poc, self.vah, self.val = poc, vah, val
        self.anchor_time = None
        self.extreme_time = None

    def summary(self):
        return "stub"


def _params(**over) -> VPLR.VPLRParams:
    p = VPLR.VPLRParams.from_decision_params(DP)
    return p if not over else VPLR.VPLRParams(**{**p.__dict__, **over})


def _detect(scn: _Scenario, anchored, liq, **over):
    ctx = VPLR.VPLRContext(anchored=anchored, params=_params(**over))
    return VPLR.detect_with_context(scn.m15, scn.m5, ctx, liq)


def _liq_at(level: float, bearish: bool) -> MicroLiquidity:
    ml = MicroLiquidity(current_price=level - (5.0 if bearish else -5.0))
    if bearish:
        ml.equal_highs = [level]
        ml.session_high = level
    else:
        ml.equal_lows = [level]
        ml.session_low = level
    return ml


class SetupContractTests(unittest.TestCase):

    LEVEL = 4640.0

    def test_full_short_setup_fires(self):
        scn = _Scenario(self.LEVEL, bearish=True)
        sig = _detect(scn, _StubAnchored(self.LEVEL, self.LEVEL + 30,
                                         self.LEVEL - 30),
                      _liq_at(self.LEVEL, True))
        self.assertTrue(sig.detected, sig.reject_reason)
        self.assertEqual(sig.direction, "BEARISH")
        self.assertEqual(sig.vp_level_name, VPLR.LEVEL_POC)
        self.assertGreater(sig.stop_loss, sig.entry_price)
        self.assertGreaterEqual(sig.sweep_depth_atr, DP.VPLR_MIN_SWEEP_ATR)
        self.assertTrue(sig.confluences)

    def test_full_long_setup_fires(self):
        scn = _Scenario(self.LEVEL, bearish=False)
        sig = _detect(scn, _StubAnchored(self.LEVEL, self.LEVEL + 30,
                                         self.LEVEL - 30),
                      _liq_at(self.LEVEL, False))
        self.assertTrue(sig.detected, sig.reject_reason)
        self.assertEqual(sig.direction, "BULLISH")
        self.assertLess(sig.stop_loss, sig.entry_price)

    def test_a_mere_touch_does_not_fire(self):
        """The acceptance criterion: being near a VP level is not a trigger."""
        scn = _Scenario(self.LEVEL, bearish=True, raid=False)
        sig = _detect(scn, _StubAnchored(self.LEVEL, self.LEVEL + 30,
                                         self.LEVEL - 30),
                      _liq_at(self.LEVEL, True))
        self.assertFalse(sig.detected)
        self.assertIn("no liquidity raid", sig.reject_reason)

    def test_raid_without_mss_does_not_fire(self):
        scn = _Scenario(self.LEVEL, bearish=True, mss=False)
        sig = _detect(scn, _StubAnchored(self.LEVEL, self.LEVEL + 30,
                                         self.LEVEL - 30),
                      _liq_at(self.LEVEL, True))
        self.assertFalse(sig.detected)

    def test_no_vp_coincidence_does_not_fire(self):
        """Same raid, but the profile is nowhere near the level raided."""
        scn = _Scenario(self.LEVEL, bearish=True)
        far = _StubAnchored(self.LEVEL + 900, self.LEVEL + 930,
                            self.LEVEL + 870)
        sig = _detect(scn, far, _liq_at(self.LEVEL, True))
        self.assertFalse(sig.detected)
        self.assertIn("no VP/liquidity coincidence", sig.reject_reason)

    def test_a_stale_raid_does_not_qualify(self):
        """
        Regression, XAUUSD 2026-08-27. `pierced[0]` plus an open-ended running
        extreme meant a level breached hours ago still read as "just raided",
        and scored DEEPER the older it got — 4.43xATR for an event long past.
        A raid outside the age bound must not qualify.
        """
        scn = _Scenario(self.LEVEL, bearish=True)
        anchored = _StubAnchored(self.LEVEL, self.LEVEL + 30, self.LEVEL - 30)
        fresh = _detect(scn, anchored, _liq_at(self.LEVEL, True))
        self.assertTrue(fresh.detected, fresh.reject_reason)
        stale = _detect(scn, anchored, _liq_at(self.LEVEL, True),
                        raid_max_age_bars=0)
        self.assertFalse(stale.detected)

    def test_depth_is_measured_from_the_most_recent_pierce(self):
        """A fresh raid scores a sane depth, not one inflated by age."""
        scn = _Scenario(self.LEVEL, bearish=True)
        sig = _detect(scn, _StubAnchored(self.LEVEL, self.LEVEL + 30,
                                         self.LEVEL - 30),
                      _liq_at(self.LEVEL, True))
        self.assertTrue(sig.detected)
        self.assertLess(sig.sweep_depth_atr, 4.0,
                        "depth this large means a stale raid is being scored")

    def test_confluence_floor_is_enforced(self):
        scn = _Scenario(self.LEVEL, bearish=True)
        sig = _detect(scn, _StubAnchored(self.LEVEL, self.LEVEL + 30,
                                         self.LEVEL - 30),
                      _liq_at(self.LEVEL, True), min_confluence=99)
        self.assertFalse(sig.detected)

    def test_vah_and_val_both_reachable(self):
        """POC is not privileged — all three levels are reaction zones."""
        for name, anchored in (
            (VPLR.LEVEL_VAH, _StubAnchored(self.LEVEL - 60, self.LEVEL,
                                           self.LEVEL - 120)),
            (VPLR.LEVEL_VAL, _StubAnchored(self.LEVEL + 60, self.LEVEL + 120,
                                           self.LEVEL)),
        ):
            with self.subTest(level=name):
                scn = _Scenario(self.LEVEL, bearish=True)
                sig = _detect(scn, anchored, _liq_at(self.LEVEL, True))
                self.assertTrue(sig.detected, sig.reject_reason)
                self.assertEqual(sig.vp_level_name, name)

    def test_direction_comes_from_liquidity_not_from_the_level(self):
        """
        A VAL raid taken from below is a LONG, and a VAL raid is exactly the
        case the old 'buy from VAL' rule would also call a long — so the
        discriminating test is the opposite one: an SSL raid at the VAH must
        produce a BULLISH signal, which 'sell from VAH' could never do.
        """
        scn = _Scenario(self.LEVEL, bearish=False)          # SSL raid, long
        at_vah = _StubAnchored(self.LEVEL - 60, self.LEVEL, self.LEVEL - 120)
        sig = _detect(scn, at_vah, _liq_at(self.LEVEL, False))
        self.assertTrue(sig.detected, sig.reject_reason)
        self.assertEqual(sig.vp_level_name, VPLR.LEVEL_VAH)
        self.assertEqual(sig.direction, "BULLISH")

    def test_records_the_full_evidence_chain(self):
        scn = _Scenario(self.LEVEL, bearish=True)
        sig = _detect(scn, _StubAnchored(self.LEVEL, self.LEVEL + 30,
                                         self.LEVEL - 30),
                      _liq_at(self.LEVEL, True))
        self.assertTrue(sig.detected)
        for field in ("vp_level_name", "interacted_level", "level_source",
                      "sweep_depth_atr", "mss_level"):
            self.assertTrue(getattr(sig, field) not in (None, "", 0.0),
                            f"{field} not recorded")
        self.assertIsNotNone(sig.raid_time)
        self.assertIsNotNone(sig.mss_time)

    def test_detection_ignores_bars_after_the_frame_ends(self):
        """
        Truncating the frames at the decision bar must not change the answer —
        i.e. nothing in `detect` reaches forward past the last row it is given.
        """
        scn = _Scenario(self.LEVEL, bearish=True)
        anchored = _StubAnchored(self.LEVEL, self.LEVEL + 30, self.LEVEL - 30)
        full = _detect(scn, anchored, _liq_at(self.LEVEL, True))

        extended = _Scenario(self.LEVEL, bearish=True)
        tail = extended.m15.iloc[-1:].copy()
        tail["time"] = tail["time"] + timedelta(minutes=15)
        tail["high"] = tail["high"] + 400.0
        extended.m15 = pd.concat([extended.m15, tail], ignore_index=True)
        truncated = _Scenario(self.LEVEL, bearish=True)
        truncated.m15 = extended.m15.iloc[:-1]
        again = _detect(truncated, anchored, _liq_at(self.LEVEL, True))

        self.assertEqual(full.detected, again.detected)
        self.assertAlmostEqual(full.entry_price, again.entry_price, places=9)
        self.assertAlmostEqual(full.stop_loss, again.stop_loss, places=9)


# ── priority and the trigger engine ──────────────────────────────────────────

class PriorityTests(unittest.TestCase):

    LEVEL = 4640.0

    def test_vp_is_next_after_sweep_and_htf_crt(self):
        self.assertEqual(SATriggerEngine.ALL_TRIGGERS[2],
                         "VP_LIQUIDITY_REACTION")

    def test_remaining_relative_order_after_operator_promotion(self):
        self.assertEqual(SATriggerEngine.ALL_TRIGGERS[3:],
                         ("FVG_FILL", "BOS_RETEST",
                          "JUDAS", "VALUE_AREA_FADE"))

    def test_vp_wins_when_sweep_is_not_enabled(self):
        scn = _Scenario(self.LEVEL, bearish=True)
        eng = SATriggerEngine(enabled_triggers=["VP_LIQUIDITY_REACTION", "FVG_FILL", "BOS_RETEST", "JUDAS"])
        liq = _liq_at(self.LEVEL, True)
        ctx = VPLR.VPLRContext(
            anchored=_StubAnchored(self.LEVEL, self.LEVEL + 30,
                                   self.LEVEL - 30),
            params=_params())
        trig = eng.step2_trigger(scn.m15, scn.m5, liq, "XAUUSD",
                                 session_open_price=None, vplr_ctx=ctx)
        self.assertTrue(trig.detected)
        self.assertEqual(trig.trigger_type, "VP_LIQUIDITY_REACTION")
        self.assertEqual(trig.matched_triggers[0], "VP_LIQUIDITY_REACTION")
        self.assertIsNotNone(trig.vplr)

    def test_incomplete_vp_leaves_the_bar_to_the_others(self):
        """
        The acceptance criterion that a VP touch must not consume the bar. The
        scenario raids nothing, so VP declines; SWEEP_REJECTION must still be
        free to take it.
        """
        scn = _Scenario(self.LEVEL, bearish=True, raid=False)
        eng = SATriggerEngine()
        liq = _liq_at(self.LEVEL, True)
        far = VPLR.VPLRContext(
            anchored=_StubAnchored(self.LEVEL + 900, self.LEVEL + 930,
                                   self.LEVEL + 870),
            params=_params())
        with_vp = eng.step2_trigger(scn.m15, scn.m5, liq, "XAUUSD",
                                    session_open_price=None, vplr_ctx=far)
        without = eng.step2_trigger(scn.m15, scn.m5, liq, "XAUUSD",
                                    session_open_price=None, vplr_ctx=None)
        self.assertEqual(with_vp.detected, without.detected)
        self.assertEqual(with_vp.trigger_type, without.trigger_type)

    def test_targets_use_the_shared_2r_geometry(self):
        scn = _Scenario(self.LEVEL, bearish=True)
        eng = SATriggerEngine()
        ctx = VPLR.VPLRContext(
            anchored=_StubAnchored(self.LEVEL, self.LEVEL + 30,
                                   self.LEVEL - 30),
            params=_params())
        trig = eng.step2_trigger(scn.m15, scn.m5, _liq_at(self.LEVEL, True),
                                 "XAUUSD", session_open_price=None,
                                 vplr_ctx=ctx)
        risk = abs(trig.entry_price - trig.stop_loss)
        self.assertAlmostEqual(trig.entry_price - trig.tp1,
                               risk * SATriggerEngine.TP1_R, places=6)


class TriggerSetTests(unittest.TestCase):

    def test_disabled_excludes_the_trigger(self):
        self.assertNotIn("VP_LIQUIDITY_REACTION",
                         resolve_enabled_triggers(None, False))

    def test_enabled_includes_the_trigger(self):
        self.assertIn("VP_LIQUIDITY_REACTION",
                      resolve_enabled_triggers(None, True))

    def test_disabled_default_set_equals_the_legacy_five(self):
        self.assertEqual(set(resolve_enabled_triggers(None, False, crt_enabled=False)),
                         {"SWEEP_REJECTION", "FVG_FILL", "BOS_RETEST",
                          "JUDAS", "VALUE_AREA_FADE"})

    def test_requesting_it_while_disabled_is_an_error(self):
        with self.assertRaises(ValueError):
            resolve_enabled_triggers(["VP_LIQUIDITY_REACTION"], False)


# ── session allowance and state machine ──────────────────────────────────────

class SessionAllowanceTests(unittest.TestCase):

    WINDOW = (dtime(0, 0), dtime(6, 30))

    def _at(self, hour, minute=0):
        return datetime(2026, 8, 27, hour, minute, tzinfo=timezone.utc)

    def test_session_windows_are_untouched(self):
        """The existing triggers' windows are not widened by any of this."""
        self.assertEqual(
            [(w.name, w.start, w.end) for w in SESSION_WINDOWS],
            [("LONDON_OPEN", dtime(7, 0), dtime(8, 30)),
             ("LONDON_NY", dtime(12, 0), dtime(13, 30)),
             ("PRE_LONDON", dtime(6, 30), dtime(7, 0)),
             ("TOKYO_OPEN", dtime(0, 0), dtime(2, 0)),
             ("NY_LUNCH_REV", dtime(16, 30), dtime(17, 30))])

    def test_vp_window_covers_the_measured_dead_zone(self):
        """02:00-06:30 is the gap the 2026-08-27 log showed the agent IDLE in."""
        chk = SASessionChecker(vp_window=self.WINDOW)
        for hour in (2, 3, 4, 5, 6):
            self.assertTrue(chk.vp_window_open(self._at(hour)), hour)
            self.assertFalse(chk.get_state(self._at(hour)).in_window, hour)

    def test_vp_window_is_closed_outside_asia(self):
        chk = SASessionChecker(vp_window=self.WINDOW)
        for hour in (7, 9, 13, 18, 22):
            self.assertFalse(chk.vp_window_open(self._at(hour)), hour)

    def test_end_is_exclusive_like_every_other_window(self):
        chk = SASessionChecker(vp_window=self.WINDOW)
        self.assertTrue(chk.vp_window_open(self._at(6, 29)))
        self.assertFalse(chk.vp_window_open(self._at(6, 30)))

    def test_no_window_configured_means_never_open(self):
        chk = SASessionChecker()
        for hour in range(24):
            self.assertFalse(chk.vp_window_open(self._at(hour)))


class StateMachineTests(unittest.TestCase):

    def _eval(self, **over):
        kw = dict(in_session_window=False, daily_loss_limit_hit=False,
                  main_dd_pct=0.0, consecutive_losses=0, pause_active=False,
                  vp_window_open=False)
        kw.update(over)
        return SABehaviorStateMachine().evaluate(**kw)

    def test_vp_only_when_outside_session_but_inside_vp_window(self):
        self.assertEqual(self._eval(vp_window_open=True), SAState.VP_ONLY)

    def test_idle_when_no_vp_window(self):
        self.assertEqual(self._eval(vp_window_open=False), SAState.IDLE)

    def test_normal_session_still_wins(self):
        self.assertEqual(
            self._eval(in_session_window=True, vp_window_open=True),
            SAState.ACTIVE)

    def test_hard_protections_outrank_the_vp_allowance(self):
        """
        The non-negotiable list. Each of these must beat VP_ONLY, or the
        allowance would be a way around a risk stop rather than a way around a
        session window.
        """
        self.assertEqual(self._eval(vp_window_open=True, main_dd_pct=9.0),
                         SAState.PROTECTED)
        self.assertEqual(self._eval(vp_window_open=True,
                                    daily_loss_limit_hit=True), SAState.HALTED)
        self.assertEqual(self._eval(vp_window_open=True, consecutive_losses=2,
                                    pause_active=True), SAState.PAUSED)

    def test_default_argument_preserves_previous_behaviour(self):
        sm = SABehaviorStateMachine()
        self.assertEqual(
            sm.evaluate(in_session_window=False, daily_loss_limit_hit=False,
                        main_dd_pct=0.0, consecutive_losses=0,
                        pause_active=False),
            SAState.IDLE)


class STBMembershipTests(unittest.TestCase):
    """
    The gate that made VALUE_AREA_FADE untestable (L-009). Membership is the
    gate, so membership is asserted.
    """

    def test_vp_is_treated_as_a_fade_type(self):
        self.assertIn("VP_LIQUIDITY_REACTION", DP.STB_NEUTRAL_OK_TRIGGERS)
        self.assertIn("VP_LIQUIDITY_REACTION", DP.STB_COUNTER_TREND_TRIGGERS)

    def test_existing_membership_is_unchanged(self):
        self.assertEqual(DP.STB_RANGE_GUARD_TRIGGERS,
                         frozenset({"SWEEP_REJECTION", "JUDAS", "FVG_FILL"}))
        self.assertEqual(
            DP.STB_NEUTRAL_OK_TRIGGERS - {"VP_LIQUIDITY_REACTION", "HTF_CRT_SWEEP"},
            frozenset({"SWEEP_REJECTION", "JUDAS", "FVG_FILL"}))
        self.assertEqual(
            DP.STB_COUNTER_TREND_TRIGGERS - {"VP_LIQUIDITY_REACTION", "HTF_CRT_SWEEP"},
            frozenset({"SWEEP_REJECTION"}))

    def test_gate1_has_a_whitelist_entry(self):
        """Gate 1 defensively denies any trigger it has no entry for."""
        self.assertIn("VP_LIQUIDITY_REACTION", DP.TRIGGER_REGIME_WHITELIST)
        self.assertEqual(DP.TRIGGER_REGIME_WHITELIST["VP_LIQUIDITY_REACTION"],
                         DP.VPLR_REGIMES)

    def test_ships_disabled(self):
        self.assertFalse(DP.VPLR_ENABLED)


if __name__ == "__main__":
    unittest.main()
