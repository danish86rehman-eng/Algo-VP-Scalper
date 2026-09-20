"""Top-down W1/H4 anchored-profile tests."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scalper.location_permission import build_location_permission  # noqa: E402
from scalper.market_location import (  # noqa: E402
    AT_VAL,
    MarketLocationEngine,
    MarketLocationSnapshot,
    MarketLocationConfig,
    ProfileRecord,
    PROFILE_ACTIVE,
    VP_SR_SUPPORT_CONFLUENCE,
    SRZone,
    SUPPORT,
    _build_vp_confluences,
)
from scalper.volume_profile import VolumeProfile  # noqa: E402


UTC = timezone.utc


def _ramp(start: float, end: float, count: int) -> list[float]:
    return [start + (end - start) * i / (count - 1)
            for i in range(count)]


def _bars(values: list[float], step_minutes: int, start: datetime) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "time": start + timedelta(minutes=step_minutes * i),
            "open": value - 1.0,
            "high": value + 2.0,
            "low": value - 2.0,
            "close": value,
            "tick_volume": 1000.0 + (500.0 if 12 <= i <= 38 else 0.0),
            "real_volume": 0,
        }
        for i, value in enumerate(values)
    ])


def _w1_frame() -> pd.DataFrame:
    values = ([4300.0] * 6
              + _ramp(4300.0, 3900.0, 16)
              + _ramp(3900.0, 5000.0, 30)
              + _ramp(5000.0, 4500.0, 12)
              + [4500.0, 4520.0, 4490.0, 4510.0]
              + _ramp(4510.0, 4580.0, 18)
              + [4580.0 + i * 2.0 for i in range(55)])
    return _bars(values, 10080, datetime(2024, 1, 7, tzinfo=UTC))


def _h4_frame() -> pd.DataFrame:
    values = ([4400.0] * 5
              + _ramp(4400.0, 4200.0, 12)
              + _ramp(4200.0, 4600.0, 20)
              + _ramp(4600.0, 4350.0, 12)
              + _ramp(4350.0, 4500.0, 18)
              + [4500.0, 4520.0, 4490.0, 4510.0]
              + _ramp(4510.0, 4550.0, 18))
    return _bars(values, 240, datetime(2026, 5, 1, tzinfo=UTC))


def _m15_frame() -> pd.DataFrame:
    return _bars([4500.0 + (i % 3) for i in range(140)],
                 15, datetime(2026, 9, 1, tzinfo=UTC))


def _snapshot(engine=None, *, w1=None, h4=None, m15=None, as_of=None):
    w1 = w1 if w1 is not None else _w1_frame()
    h4 = h4 if h4 is not None else _h4_frame()
    m15 = m15 if m15 is not None else _m15_frame()
    as_of = as_of or w1.time.iloc[-1] + pd.Timedelta(weeks=1)
    return (engine or MarketLocationEngine()).snapshot(
        symbol="XAUUSD", current_price=float(h4.close.iloc[-1]),
        df_w1=w1, df_h4=h4, df_m15=m15, as_of=as_of)


class TopDownProfileTests(unittest.TestCase):
    def test_w1_and_h4_are_independent_and_simultaneous(self):
        snapshot = _snapshot()
        self.assertEqual(snapshot.active_profile_timeframe, "H4")
        self.assertIsNotNone(snapshot.w1_profile_id)
        self.assertIsNotNone(snapshot.h4_profile_id)
        self.assertTrue(any(p.timeframe == "W1" and p.status == PROFILE_ACTIVE
                            for p in snapshot.profiles))
        self.assertTrue(any(p.timeframe == "H4" and p.status == PROFILE_ACTIVE
                            for p in snapshot.profiles))

    def test_raw_poc_vah_val_levels_are_not_averaged(self):
        snapshot = _snapshot()
        self.assertEqual(snapshot.w1_poc,
                         next(p.poc for p in snapshot.profiles
                              if p.timeframe == "W1" and p.status == PROFILE_ACTIVE))
        self.assertEqual(snapshot.h4_poc,
                         next(p.poc for p in snapshot.profiles
                              if p.timeframe == "H4" and p.status == PROFILE_ACTIVE))
        self.assertIsNotNone(snapshot.w1_vah)
        self.assertIsNotNone(snapshot.w1_val)
        self.assertIsNotNone(snapshot.h4_vah)
        self.assertIsNotNone(snapshot.h4_val)

    def test_small_new_swings_do_not_reanchor_the_weekly_profile(self):
        w1 = _w1_frame()
        h4 = _h4_frame()
        first_as_of = w1.time.iloc[-1] + pd.Timedelta(weeks=1)
        engine = MarketLocationEngine()
        first = _snapshot(engine, w1=w1, h4=h4, as_of=first_as_of)
        small = w1.copy()
        start = small.time.iloc[-1] + pd.Timedelta(weeks=1)
        extra = _bars([small.close.iloc[-1] + i for i in range(1, 6)],
                       10080, start)
        second_frame = pd.concat([small, extra], ignore_index=True)
        second = _snapshot(engine, w1=second_frame, h4=h4,
                           as_of=second_frame.time.iloc[-1] + pd.Timedelta(weeks=1))
        self.assertEqual(first.w1_profile_id, second.w1_profile_id)
        self.assertEqual(first.w1_anchor_start, second.w1_anchor_start)
        self.assertEqual(first.w1_anchor_end, second.w1_anchor_end)

    def test_h4_can_replace_without_rebuilding_w1(self):
        w1 = _w1_frame()
        h4 = _h4_frame()
        engine = MarketLocationEngine()
        extra_values = ([h4.close.iloc[-1]]
                        + _ramp(float(h4.close.iloc[-1]), 4900.0, 14)
                        + _ramp(4900.0, 4300.0, 16)
                        + [4300.0, 4290.0, 4310.0, 4300.0])
        extra = _bars(extra_values, 240,
                      h4.time.iloc[-1] + pd.Timedelta(hours=4))
        second_h4 = pd.concat([h4, extra], ignore_index=True)
        w1_stable = w1.loc[w1["time"] < pd.Timestamp("2026-04-01", tz="UTC")].copy()
        first_as_of = h4.time.iloc[-1] + pd.Timedelta(hours=4)
        second_as_of = second_h4.time.iloc[-1] + pd.Timedelta(hours=4)
        first = _snapshot(engine, w1=w1_stable, h4=h4, as_of=first_as_of)
        second = _snapshot(engine, w1=w1_stable, h4=second_h4, as_of=second_as_of)
        self.assertEqual(first.w1_profile_id, second.w1_profile_id)
        self.assertEqual(first.w1_anchor_start, second.w1_anchor_start)
        self.assertEqual(first.w1_anchor_end, second.w1_anchor_end)
        self.assertNotEqual(first.h4_profile_id, second.h4_profile_id)

    def test_anchor_confirmation_is_causal_and_future_bars_are_ignored(self):
        w1 = _w1_frame()
        h4 = _h4_frame()
        as_of = w1.time.iloc[-1] + pd.Timedelta(weeks=1)
        base = _snapshot(w1=w1, h4=h4, as_of=as_of)
        future = _bars([7000.0, 7000.0, 7000.0], 10080,
                       w1.time.iloc[-1] + pd.Timedelta(weeks=1))
        with_future = pd.concat([w1, future], ignore_index=True)
        replayed = _snapshot(w1=with_future, h4=h4, as_of=as_of)
        self.assertEqual(base.w1_profile_id, replayed.w1_profile_id)
        active_w1 = next(p for p in base.profiles
                         if p.timeframe == "W1" and p.status == PROFILE_ACTIVE)
        self.assertLessEqual(pd.Timestamp(active_w1.confirmation_time),
                             pd.Timestamp(as_of))
        self.assertLessEqual(pd.Timestamp(active_w1.anchor_end_time),
                             pd.Timestamp(active_w1.confirmation_time))

    def test_runtime_and_replay_have_identical_anchor_selection(self):
        left = _snapshot(engine=MarketLocationEngine())
        right = _snapshot(engine=MarketLocationEngine())
        self.assertEqual((left.w1_profile_id, left.h4_profile_id),
                         (right.w1_profile_id, right.h4_profile_id))
        self.assertEqual((left.w1_anchor_start, left.w1_anchor_end,
                          left.h4_anchor_start, left.h4_anchor_end),
                         (right.w1_anchor_start, right.w1_anchor_end,
                          right.h4_anchor_start, right.h4_anchor_end))

    def test_restart_reconstructs_identical_profile_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            state = str(Path(directory) / "location-state.json")
            first = _snapshot(engine=MarketLocationEngine(state_path=state))
            restarted = _snapshot(engine=MarketLocationEngine(state_path=state))
        self.assertEqual((first.w1_profile_id, first.h4_profile_id),
                         (restarted.w1_profile_id, restarted.h4_profile_id))

    def test_structural_vp_confluence_retains_components(self):
        snapshot = _snapshot()
        # The synthetic expansion produces VP/SR joins even when W1 and H4
        # auctions are distinct; components must remain explicit in telemetry.
        self.assertTrue(snapshot.vp_confluences)
        self.assertTrue(any("STRUCTURAL" in component
                            for c in snapshot.vp_confluences
                            for component in c.components))

    def test_multi_timeframe_vp_confluence_keeps_both_raw_components(self):
        def profile(profile_id, timeframe, poc, vah, val):
            raw = VolumeProfile(
                poc=poc, vah=vah, val=val, profile_high=120.0,
                profile_low=80.0, bin_size=1.0, bin_count=40,
                total_volume=1000.0, value_area_volume=700.0, bars_used=40)
            return ProfileRecord(
                profile_id=profile_id, status=PROFILE_ACTIVE,
                timeframe=timeframe, profile=raw, profile_start=None,
                profile_end=None, profile_high=120.0, profile_low=80.0,
                direction="UP", total_volume=1000.0,
                data_quality="TICK_VOLUME", source="TEST")

        confluences = _build_vp_confluences(
            profile("w1", "W1", 100.0, 110.0, 90.0),
            profile("h4", "H4", 100.5, 110.5, 90.5),
            (), price=100.25, atr_value=2.0,
            config=MarketLocationConfig(cluster_atr=0.35))
        self.assertTrue(any(c.kind == "MULTI_TF_VP_CONFLUENCE"
                            and c.components == ("W1_POC", "H4_POC")
                            for c in confluences))

    def test_sweep_location_can_identify_the_associated_vp_area(self):
        raw = VolumeProfile(
            poc=105.0, vah=110.0, val=100.0, profile_high=120.0,
            profile_low=95.0, bin_size=1.0, bin_count=25,
            total_volume=1000.0, value_area_volume=700.0, bars_used=30)
        zone = SRZone(
            zone_id="SR-support", price=100.0, zone_low=99.0, zone_high=101.0,
            type=SUPPORT, timeframe="H4", created_at=None, last_touch=None,
            touch_count=2, rejection_count=1, strength_score=0.8,
            source="H4_STRUCTURAL_SUPPORT", active=True,
            distance_from_price=0.0)
        snapshot = MarketLocationSnapshot(
            symbol="XAUUSD", as_of="2026-09-19T00:00:00+00:00",
            current_price=100.0, atr=2.0, location_type=VP_SR_SUPPORT_CONFLUENCE,
            vp_state=AT_VAL, sr_state="AT_SUPPORT", nearest_support=100.0,
            nearest_val=100.0, distance_to_support_atr=0.0,
            distance_to_val_atr=0.0, active_profile_id="H4-test",
            active_profile_timeframe="H4", zones=(zone,), top_down_available=True)
        permission = build_location_permission(
            snapshot, direction="BULLISH", trigger_type="SWEEP_REJECTION",
            swept_level=100.0)
        self.assertIn("AT_VAL", permission.reasons)
        self.assertEqual(permission.frozen.location_kind, SUPPORT)


if __name__ == "__main__":
    unittest.main()
