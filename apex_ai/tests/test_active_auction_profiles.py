"""Regression tests for current-auction Volume Profile architecture."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scalper.market_location as ml  # noqa: E402
from scalper.location_permission import build_location_permission  # noqa: E402
from scalper.volume_profile import VolumeProfile  # noqa: E402


UTC = timezone.utc
T0 = datetime(2026, 1, 4, tzinfo=UTC)


def _bar_frame(values, minutes, start=T0, volume=1000.0):
    return pd.DataFrame([{
        "time": start + timedelta(minutes=minutes * i),
        "open": float(value) - 0.2,
        "high": float(value) + 1.0,
        "low": float(value) - 1.0,
        "close": float(value),
        "tick_volume": float(volume),
        "real_volume": 0,
    } for i, value in enumerate(values)])


def _zigzag():
    points = [100, 130, 90, 140, 80, 125, 85, 120]
    values = []
    for left, right in zip(points[:-1], points[1:]):
        values.extend(left + (right - left) * i / 6 for i in range(6))
    values.extend([118, 117, 116, 115, 114, 113])
    return _bar_frame(values, 10080)


def _swing(index, price, role, point_type, minutes=240):
    at = pd.Timestamp(T0 + timedelta(minutes=minutes * index))
    return ml._Swing(index, float(price), role, point_type, at,
                     at + pd.Timedelta(minutes=minutes * 3))


class CurrentAuctionSelectionTests(unittest.TestCase):
    def test_discovery_runs_before_historical_scoring(self):
        frame = _zigzag()
        order = []
        discover = ml._discover_active_auctions
        historical = ml._rank_structural_legs

        def discover_first(*args, **kwargs):
            order.append("discover")
            return discover(*args, **kwargs)

        def score_later(*args, **kwargs):
            order.append("historical-score")
            return historical(*args, **kwargs)

        with patch.object(ml, "_discover_active_auctions",
                          side_effect=discover_first), patch.object(
                              ml, "_rank_structural_legs",
                              side_effect=score_later):
            ml._profiles_for_frame(
                frame, "XAUUSD", "W1", 2, ml.MarketLocationConfig(),
                current_price=114.0)
        self.assertTrue(order)
        self.assertEqual(order[0], "discover")
        self.assertIn("historical-score", order[1:])

    def test_high_scoring_historical_leg_cannot_become_active(self):
        frame = _zigzag()
        profiles = ml._profiles_for_frame(
            frame, "XAUUSD", "W1", 2, ml.MarketLocationConfig(),
            current_price=114.0)
        active = next(p for p in profiles if p.status == ml.PROFILE_ACTIVE)
        references = [p for p in profiles if p.status == ml.PROFILE_REFERENCE]
        self.assertIn("ACTIVE_CURRENT_AUCTION", active.source)
        self.assertTrue(references)
        self.assertTrue(any(p.selection_score > active.selection_score
                            for p in references))

    def test_active_and_reference_are_separate_and_reference_is_context_only(self):
        frame = _zigzag()
        snapshot = ml.MarketLocationEngine().snapshot(
            symbol="XAUUSD", current_price=114.0,
            df_w1=frame, df_h4=frame,
            df_m15=_bar_frame([113, 114] * 100, 15),
            as_of=frame.time.iloc[-1] + pd.Timedelta(weeks=1))
        active_ids = {snapshot.w1_profile_id, snapshot.h4_profile_id}
        reference_ids = {p.profile_id for p in snapshot.profiles
                         if p.status == ml.PROFILE_REFERENCE}
        self.assertTrue(active_ids - {None})
        self.assertTrue(reference_ids)
        self.assertTrue((active_ids - {None}).isdisjoint(reference_ids))
        self.assertIn(snapshot.w1_reference_profile_id, reference_ids)
        self.assertIn(snapshot.h4_reference_profile_id, reference_ids)
        permission = build_location_permission(
            snapshot, direction="BULLISH", trigger_type="SWEEP_REJECTION",
            swept_level=114.0)
        if permission.profile_id is not None:
            self.assertIn(permission.profile_id, active_ids)
            self.assertNotIn(permission.profile_id, reference_ids)

    def test_sweep_rejection_permission_binds_active_profile_not_reference(self):
        def record(profile_id, status, poc, vah, val):
            raw = VolumeProfile(
                poc=poc, vah=vah, val=val, profile_high=vah + 5,
                profile_low=val - 5, bin_size=1.0, bin_count=30,
                total_volume=1000.0, value_area_volume=705.0, bars_used=40)
            return ml.ProfileRecord(
                profile_id=profile_id, status=status, timeframe="H4",
                profile=raw, profile_start=None, profile_end=None,
                profile_high=raw.profile_high, profile_low=raw.profile_low,
                direction="UP", total_volume=raw.total_volume,
                data_quality="TICK_VOLUME", source="TEST")

        active = record("active-h4", ml.PROFILE_ACTIVE, 105, 110, 100)
        reference = record("reference-h4", ml.PROFILE_REFERENCE, 55, 60, 50)
        snapshot = ml.MarketLocationSnapshot(
            symbol="XAUUSD", as_of="2026-09-18T21:00:00+00:00",
            current_price=100.0, atr=2.0, location_type=ml.AT_VAL,
            vp_state=ml.AT_VAL, value_area_event=ml.VAL_REJECTION,
            nearest_poc=105.0, nearest_vah=110.0, nearest_val=100.0,
            distance_to_poc_atr=2.5, distance_to_vah_atr=5.0,
            distance_to_val_atr=0.0, active_profile_id=active.profile_id,
            active_profile_timeframe="H4", h4_profile_id=active.profile_id,
            profiles=(active, reference), top_down_available=True)
        permission = build_location_permission(
            snapshot, direction="BULLISH", trigger_type="SWEEP_REJECTION",
            swept_level=100.0)
        self.assertEqual(permission.profile_id, active.profile_id)
        self.assertNotEqual(permission.profile_id, reference.profile_id)
        self.assertTrue(permission.executable)

    def test_latest_bos_event_and_price_membership_select_current_range(self):
        frame = _bar_frame([100 + i * 0.2 for i in range(30)], 240)
        swings = [
            _swing(2, 130, ml.RESISTANCE, "HH"),
            _swing(6, 90, ml.SUPPORT, "LL"),
            _swing(10, 120, ml.RESISTANCE, "LH"),
            _swing(14, 80, ml.SUPPORT, "LL"),
            _swing(20, 115, ml.RESISTANCE, "LH"),
        ]
        candidates = ml._rank_active_auctions(
            ml._discover_active_auctions(
                frame, swings, 100.0,
                ml.MarketLocationConfig(min_anchor_leg_atr=0.1)))
        chosen = candidates[0]
        self.assertTrue(chosen.contains_current_price)
        self.assertEqual(chosen.leg.left.index, 14)
        self.assertEqual(chosen.leg.right.index, 20)
        self.assertIn("LATEST_LL", chosen.structural_event)

    def test_active_profile_stays_frozen_without_replacement_event(self):
        frame = _zigzag()
        initial = ml._profiles_for_frame(
            frame, "XAUUSD", "W1", 2, ml.MarketLocationConfig(),
            current_price=114.0)
        active = next(p for p in initial if p.status == ml.PROFILE_ACTIVE)
        with patch.object(ml, "_discover_active_auctions", return_value=[]):
            held = ml._profiles_for_frame(
                frame, "XAUUSD", "W1", 2, ml.MarketLocationConfig(),
                current_price=500.0, prior=active,
                prior_as_of=pd.Timestamp("2026-09-18T21:00:00Z"))
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0].profile_id, active.profile_id)
        self.assertEqual(held[0].replacement_reason, "HOLD_CURRENT_AUCTION")

    def test_same_candle_dual_pivot_resolves_to_one_role(self):
        frame = _bar_frame([100.0] * 25, 240)
        frame.loc[10, ["open", "high", "low", "close"]] = [100, 200, 50, 160]
        for index in range(11, 17):
            frame.loc[index, ["open", "high", "low", "close"]] = [
                160, 180 + index, 150, 175 + index]
        swings = ml._structure_swings(frame, "XAUUSD", "H4", 3)
        same_index = [s for s in swings if s.index == 10]
        self.assertEqual(len(same_index), 1)
        self.assertEqual(same_index[0].role, ml.SUPPORT)


class DetailedProfileTests(unittest.TestCase):
    def _h4_native_and_m5(self):
        native = _bar_frame([120, 118, 105, 114, 110], 240, volume=48.0)
        native.loc[2, "low"] = 95.0
        detail_rows = []
        for h4_index in range(5):
            for offset in range(48):
                at = T0 + timedelta(hours=4 * h4_index, minutes=5 * offset)
                center = 100.0 + h4_index * 2 + (offset % 5) * 0.2
                detail_rows.append({
                    "time": at, "open": center, "high": center + 0.4,
                    "low": center - 0.4, "close": center + 0.1,
                    "tick_volume": 1.0, "real_volume": 0,
                })
        detail = pd.DataFrame(detail_rows)
        detail.loc[2 * 48 + 5, "low"] = 95.0
        future = pd.DataFrame([{
            "time": T0 + timedelta(hours=20), "open": 100, "high": 500,
            "low": 1, "close": 400, "tick_volume": 999999.0,
            "real_volume": 0,
        }])
        return native, pd.concat((detail, future), ignore_index=True)

    def test_h4_uses_complete_m5_and_clips_future_volume(self):
        native, m5 = self._h4_native_and_m5()
        leg = ml._LegCandidate(
            _swing(0, 120, ml.RESISTANCE, "HH"),
            _swing(4, 110, ml.SUPPORT, "HL"), "DOWN", 1.0, 3.0, 5, 0.8,
            "CURRENT_AUCTION")
        record = ml._profile_record(
            native, "H4", leg, ml.PROFILE_ACTIVE, "TEST",
            ml.MarketLocationConfig(), "XAUUSD",
            detail_frames={"M5": m5})
        self.assertEqual(record.source_timeframe, "M5")
        self.assertEqual(record.volume_source, "tick_volume")
        self.assertEqual(record.row_count, 48)
        self.assertAlmostEqual(record.total_volume, 240.0, places=6)
        self.assertLess(record.profile_high, 500.0)
        self.assertGreaterEqual(record.actual_value_area_percentage, 70.0)
        self.assertLess(record.actual_value_area_percentage, 80.0)
        repeated = ml._profile_record(
            native, "H4", leg, ml.PROFILE_ACTIVE, "TEST",
            ml.MarketLocationConfig(), "XAUUSD",
            detail_frames={"M5": m5})
        self.assertEqual((record.row_count, record.profile.bin_size,
                          record.poc, record.vah, record.val),
                         (repeated.row_count, repeated.profile.bin_size,
                          repeated.poc, repeated.vah, repeated.val))

    def test_anchor_endpoint_and_profile_extreme_are_distinct(self):
        native, m5 = self._h4_native_and_m5()
        leg = ml._LegCandidate(
            _swing(0, 120, ml.RESISTANCE, "HH"),
            _swing(4, 110, ml.SUPPORT, "HL"), "DOWN", 1.0, 3.0, 5, 0.8,
            "CURRENT_AUCTION")
        record = ml._profile_record(
            native, "H4", leg, ml.PROFILE_ACTIVE, "TEST",
            ml.MarketLocationConfig(), "XAUUSD",
            detail_frames={"M5": m5})
        self.assertEqual(record.anchor_end_price, 110.0)
        self.assertEqual(record.anchor_low, 110.0)
        self.assertEqual(record.profile_low, 95.0)

    def test_incomplete_m5_falls_back_to_m15(self):
        native, m5 = self._h4_native_and_m5()
        m15 = m5.iloc[:-1:3].copy()
        # Aggregate three one-volume M5 rows into each M15 row.
        m15["tick_volume"] = 3.0
        leg = ml._LegCandidate(
            _swing(0, 120, ml.RESISTANCE, "HH"),
            _swing(4, 110, ml.SUPPORT, "HL"), "DOWN", 1.0, 3.0, 5, 0.8,
            "CURRENT_AUCTION")
        record = ml._profile_record(
            native, "H4", leg, ml.PROFILE_ACTIVE, "TEST",
            ml.MarketLocationConfig(), "XAUUSD",
            detail_frames={"M5": m5.iloc[:20], "M15": m15})
        self.assertEqual(record.source_timeframe, "M15")
        self.assertAlmostEqual(record.total_volume, 240.0, places=6)

    def test_w1_prefers_complete_m15_detail(self):
        native = _bar_frame([90, 100, 95, 110, 105], 10080, volume=480.0)
        rows = []
        for week in range(5):
            week_start = T0 + timedelta(weeks=week)
            for weekday in range(5):
                for quarter in range(96):
                    at = week_start + timedelta(days=weekday,
                                                minutes=15 * quarter)
                    center = 95 + week * 2 + (quarter % 7) * 0.1
                    rows.append({
                        "time": at, "open": center, "high": center + 0.3,
                        "low": center - 0.3, "close": center,
                        "tick_volume": 1.0, "real_volume": 0,
                    })
        m15 = pd.DataFrame(rows)
        leg = ml._LegCandidate(
            _swing(0, 89, ml.SUPPORT, "LL", 10080),
            _swing(4, 111, ml.RESISTANCE, "HH", 10080),
            "UP", 1.0, 3.0, 5, 0.8, "CURRENT_AUCTION")
        record = ml._profile_record(
            native, "W1", leg, ml.PROFILE_ACTIVE, "TEST",
            ml.MarketLocationConfig(), "XAUUSD",
            detail_frames={"M15": m15})
        self.assertEqual(record.source_timeframe, "M15")
        self.assertAlmostEqual(record.total_volume, 2400.0, places=6)

    def test_restart_reconstruction_keeps_profile_ids_and_levels(self):
        frame = _zigzag()
        m15 = _bar_frame([113, 114] * 100, 15)
        as_of = frame.time.iloc[-1] + pd.Timedelta(weeks=1)
        with tempfile.TemporaryDirectory() as directory:
            state = str(Path(directory) / "state.json")
            first = ml.MarketLocationEngine(state_path=state).snapshot(
                symbol="XAUUSD", current_price=114.0, df_w1=frame,
                df_h4=frame, df_m15=m15, as_of=as_of)
            second = ml.MarketLocationEngine(state_path=state).snapshot(
                symbol="XAUUSD", current_price=114.0, df_w1=frame,
                df_h4=frame, df_m15=m15, as_of=as_of)
        self.assertEqual(first.w1_profile_id, second.w1_profile_id)
        self.assertEqual(first.h4_profile_id, second.h4_profile_id)
        self.assertEqual((first.w1_poc, first.w1_vah, first.w1_val),
                         (second.w1_poc, second.w1_vah, second.w1_val))
        self.assertEqual((first.h4_poc, first.h4_vah, first.h4_val),
                         (second.h4_poc, second.h4_vah, second.h4_val))

    def test_runtime_and_replay_inputs_produce_identical_profiles(self):
        frame = _zigzag()
        m15 = _bar_frame([113, 114] * 100, 15)
        as_of = frame.time.iloc[-1] + pd.Timedelta(weeks=1)
        kwargs = dict(symbol="XAUUSD", current_price=114.0,
                      df_w1=frame.copy(), df_h4=frame.copy(),
                      df_m15=m15.copy(), as_of=as_of)
        runtime = ml.MarketLocationEngine().snapshot(**kwargs)
        replay = ml.MarketLocationEngine().snapshot(**kwargs)
        self.assertEqual(runtime.w1_profile_id, replay.w1_profile_id)
        self.assertEqual(runtime.h4_profile_id, replay.h4_profile_id)
        self.assertEqual((runtime.w1_poc, runtime.w1_vah, runtime.w1_val,
                          runtime.h4_poc, runtime.h4_vah, runtime.h4_val),
                         (replay.w1_poc, replay.w1_vah, replay.w1_val,
                          replay.h4_poc, replay.h4_vah, replay.h4_val))


if __name__ == "__main__":
    unittest.main()
