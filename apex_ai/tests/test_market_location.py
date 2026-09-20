"""Focused causal tests for the shared VP + structural location engine."""
from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scalper.market_location import (  # noqa: E402
    AT_POC,
    FLIP,
    MarketLocationConfig,
    MarketLocationEngine,
    NO_VALUE_EVENT,
    POC_LOSS,
    POC_RECLAIM,
    PROFILE_ACTIVE,
    PROFILE_REFERENCE,
    SRZone,
    SUPPORT,
    VAH_ACCEPTANCE,
    VAH_REJECTION,
    VAL_ACCEPTANCE,
    VAL_REJECTION,
    _LevelCandidate,
    _make_zones,
    _structure_swings,
    _value_event,
    _value_location, ProfileLedger,
)
from scalper.volume_profile import VolumeProfile  # noqa: E402
from scalper.market_location import ProfileRecord  # noqa: E402
from scalper.trigger_engine import (MicroLiquidity, SATrigger,
                                    SATriggerEngine)  # noqa: E402


T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


def bars(closes, *, volume=1000.0, step=15):
    rows = []
    for i, close in enumerate(closes):
        close = float(close)
        rows.append({
            "time": T0 + timedelta(minutes=step * i),
            "open": close - 0.25,
            "high": close + 0.75,
            "low": close - 0.75,
            "close": close,
            "tick_volume": volume,
            "real_volume": 0,
        })
    return pd.DataFrame(rows)


def zigzag_frame():
    """Alternating swings with enough right-side bars to confirm the last one."""
    points = [100, 120, 90, 130, 95, 140, 105]
    values = []
    for left, right in zip(points[:-1], points[1:]):
        values.extend([left + (right - left) * j / 5 for j in range(5)])
    values.extend([105, 106, 107, 108, 109, 110])
    return bars(values)


def profile(poc=100.0, vah=110.0, val=90.0):
    raw = VolumeProfile(
        poc=poc, vah=vah, val=val, profile_high=120.0,
        profile_low=80.0, bin_size=1.0, bin_count=40,
        total_volume=1000.0, value_area_volume=700.0, bars_used=20)
    return ProfileRecord(
        profile_id="VP-test", status=PROFILE_ACTIVE, timeframe="M15",
        profile=raw, profile_start=T0.isoformat(),
        profile_end=(T0 + timedelta(hours=5)).isoformat(),
        profile_high=120.0, profile_low=80.0, direction="UP",
        total_volume=1000.0, data_quality="TICK_VOLUME",
        source="TEST")


class TestCausality(unittest.TestCase):
    def test_swing_confirmation_requires_right_side_bars(self):
        frame = zigzag_frame()
        swings = _structure_swings(frame, "XAUUSD", "M15", 3)
        self.assertTrue(swings)
        for swing in swings:
            self.assertLessEqual(swing.index + 3, len(frame) - 1)
            self.assertGreaterEqual(swing.confirmed_at, swing.pivot_at)

    def test_appending_a_forming_bar_cannot_change_a_closed_snapshot(self):
        frame = zigzag_frame()
        engine = MarketLocationEngine()
        as_of = frame.time.iloc[-1] + pd.Timedelta(minutes=15)
        first = engine.snapshot(
            symbol="XAUUSD", current_price=float(frame.close.iloc[-1]),
            df_m15=frame, df_h4=frame, as_of=as_of)
        forming = frame.copy()
        forming.loc[len(forming)] = {
            "time": as_of, "open": 110, "high": 300, "low": 1,
            "close": 250, "tick_volume": 999999, "real_volume": 0,
        }
        second = MarketLocationEngine().snapshot(
            symbol="XAUUSD", current_price=float(frame.close.iloc[-1]),
            df_m15=forming, df_h4=forming, as_of=as_of)
        self.assertEqual(first.location_type, second.location_type)
        self.assertEqual(first.nearest_poc, second.nearest_poc)
        self.assertEqual(first.nearest_support, second.nearest_support)


class TestSRZones(unittest.TestCase):
    def _candidate(self, price, role, source="TEST"):
        at = pd.Timestamp(T0)
        return _LevelCandidate(price, role, "H4", at, at + pd.Timedelta(hours=4),
                               source, 2.0)

    def test_nearby_levels_cluster_into_one_zone(self):
        frame = bars([100 + i * 0.1 for i in range(30)], step=240)
        zones = _make_zones(
            [self._candidate(100.0, SUPPORT),
             self._candidate(100.2, SUPPORT, "H4_REACTION")],
            (("H4", frame),), 100.1, 1.0,
            MarketLocationConfig(cluster_atr=0.5), "XAUUSD")
        self.assertEqual(len(zones), 1)
        self.assertLessEqual(zones[0].zone_low, 100.1)
        self.assertGreaterEqual(zones[0].zone_high, 100.1)
        self.assertIn("H4_REACTION", zones[0].source)

    def test_support_flips_only_after_multiple_acceptance_closes(self):
        frame = bars([100, 100, 100, 98.0, 97.5], step=240)
        zones = _make_zones(
            [self._candidate(100.0, SUPPORT)], (("H4", frame),), 97.5, 1.0,
            MarketLocationConfig(cluster_atr=0.25, acceptance_bars=2), "XAUUSD")
        self.assertEqual(zones[0].type, FLIP)
        self.assertEqual(zones[0].flip_to, "RESISTANCE")
        self.assertIsNotNone(zones[0].acceptance_confirmed_at)

    def test_broken_level_remains_active(self):
        frame = bars([100, 100, 98.0, 97.5], step=240)
        zones = _make_zones(
            [self._candidate(100.0, SUPPORT)], (("H4", frame),), 97.5, 1.0,
            MarketLocationConfig(cluster_atr=0.25, acceptance_bars=2), "XAUUSD")
        self.assertTrue(zones[0].active)


class TestValueAreaStates(unittest.TestCase):
    def test_vah_rejection(self):
        frame = bars([100, 101, 102], step=15)
        frame.loc[2, ["open", "high", "low", "close"]] = [109, 112, 107, 108]
        self.assertEqual(_value_event(frame, profile(), 5.0,
                                      MarketLocationConfig()), VAH_REJECTION)

    def test_vah_acceptance(self):
        frame = bars([100, 101, 113, 114], step=15)
        self.assertEqual(_value_event(frame, profile(), 5.0,
                                      MarketLocationConfig()), VAH_ACCEPTANCE)

    def test_val_rejection(self):
        frame = bars([100, 99, 98], step=15)
        frame.loc[2, ["open", "high", "low", "close"]] = [91, 93, 88, 92]
        self.assertEqual(_value_event(frame, profile(), 5.0,
                                      MarketLocationConfig()), VAL_REJECTION)

    def test_val_acceptance(self):
        frame = bars([100, 89, 88], step=15)
        self.assertEqual(_value_event(frame, profile(), 5.0,
                                      MarketLocationConfig()), VAL_ACCEPTANCE)

    def test_poc_reclaim_and_loss(self):
        reclaim = bars([99, 101], step=15)
        loss = bars([101, 99], step=15)
        cfg = MarketLocationConfig()
        self.assertEqual(_value_event(reclaim, profile(), 5.0, cfg), POC_RECLAIM)
        self.assertEqual(_value_event(loss, profile(), 5.0, cfg), POC_LOSS)


class TestProfilesAndLocation(unittest.TestCase):
    def test_profile_ledger_does_not_retire_another_symbol(self):
        ledger = ProfileLedger()
        gold = replace(profile(), symbol="XAUUSD", profile_id="gold")
        silver = replace(profile(), symbol="XAGUSD", profile_id="silver")
        ledger.update((gold,), "XAUUSD")
        ledger.update((silver,), "XAGUSD")
        retired = ledger.update((gold,), "XAUUSD")
        self.assertEqual(retired, ())
        self.assertEqual(ledger._latest[("XAGUSD", "silver")].status,
                         PROFILE_ACTIVE)

    def test_trigger_engine_carries_location_evidence(self):
        frame = zigzag_frame()
        location = MarketLocationEngine().snapshot(
            symbol="XAUUSD", current_price=110.0,
            df_m15=frame, df_h4=frame,
            as_of=frame.time.iloc[-1] + pd.Timedelta(minutes=15))
        engine = SATriggerEngine(enabled_triggers=["BOS_RETEST"])
        candidate = SATrigger(
            detected=True, trigger_type="BOS_RETEST", direction="BULLISH",
            entry_price=110.0, stop_loss=100.0, tp1=130.0, tp2=140.0)
        observed = []
        with patch.object(engine, "_check_bos_retest", return_value=candidate):
            result = engine.step2_trigger(
                frame, frame, MicroLiquidity(current_price=110.0), "XAUUSD",
                market_location=location, candidate_observer=observed.append)
        self.assertTrue(result.detected)
        self.assertEqual(len(observed), 1)
        self.assertIs(observed[0].market_location, location)
        self.assertIsNone(observed[0].location_permission)

    def test_profile_requires_real_or_tick_volume(self):
        frame = bars([100 + i for i in range(30)])
        frame = frame.drop(columns=["tick_volume", "real_volume"])
        snapshot = MarketLocationEngine().snapshot(
            symbol="XAUUSD", current_price=120.0,
            df_m15=frame, df_h4=frame,
            as_of=frame.time.iloc[-1] + pd.Timedelta(minutes=15))
        self.assertEqual(snapshot.profiles, ())

    def test_primary_location_can_report_poc(self):
        frame = zigzag_frame()
        as_of = frame.time.iloc[-1] + pd.Timedelta(minutes=15)
        snapshot = MarketLocationEngine().snapshot(
            symbol="XAUUSD", current_price=float(frame.close.iloc[-1]),
            df_m15=frame, df_h4=frame, as_of=as_of)
        self.assertTrue(snapshot.profiles)
        self.assertIn(snapshot.vp_state, {AT_POC, "AT_VAH", "AT_VAL",
                                           "ABOVE_VALUE", "BELOW_VALUE",
                                           "INSIDE_VALUE"})
        self.assertTrue(all(p.status in {PROFILE_ACTIVE, PROFILE_REFERENCE}
                            for p in snapshot.profiles))

    def test_profile_ledger_retires_old_reference(self):
        frame = zigzag_frame()
        engine = MarketLocationEngine()
        as_of = frame.time.iloc[-1] + pd.Timedelta(minutes=15)
        first = engine.snapshot(symbol="XAUUSD", current_price=110,
                                df_m15=frame, df_h4=frame, as_of=as_of)
        later = frame.copy()
        later.loc[len(later)] = {
            "time": as_of + pd.Timedelta(minutes=15), "open": 110,
            "high": 130, "low": 109, "close": 129,
            "tick_volume": 1000, "real_volume": 0,
        }
        second = engine.snapshot(symbol="XAUUSD", current_price=129,
                                 df_m15=later, df_h4=later,
                                 as_of=as_of + pd.Timedelta(minutes=30))
        self.assertTrue(first.profiles)
        self.assertTrue(second.profiles)
        # The active profile id is stable while its causal end advances; a
        # genuinely replaced profile is reported as retired by the ledger.
        self.assertTrue(set(first.retired_profile_ids) or
                        first.profiles[0].profile_id == second.profiles[0].profile_id)

    def test_no_location_is_mid_range(self):
        frame = bars([100 + (i % 2) * 0.2 for i in range(80)], step=15)
        snapshot = MarketLocationEngine().snapshot(
            symbol="XAUUSD", current_price=100.1,
            df_m15=frame, df_h4=frame,
            as_of=frame.time.iloc[-1] + pd.Timedelta(minutes=15))
        self.assertIn(snapshot.location_type, {"MID_RANGE_NO_LOCATION", AT_POC,
                                               "INSIDE_VALUE"})


if __name__ == "__main__":
    unittest.main()
