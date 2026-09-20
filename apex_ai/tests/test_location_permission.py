"""Focused tests for the directional VP/SR permission contract."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scalper.location_permission import (  # noqa: E402
    ALLOW_LONG, ALLOW_SHORT, BLOCK, CONTEXT_ONLY, LocationPermissionConfig,
    build_location_permission, evaluate_sweep_reaction,
)
from scalper.candidate_funnel import CandidateFunnelRecorder  # noqa: E402
from scalper.market_location import (  # noqa: E402
    AT_POC, AT_VAL, AT_VAH, AT_MAJOR_RESISTANCE, AT_MAJOR_SUPPORT,
    MID_RANGE_NO_LOCATION, MarketLocationSnapshot, SRZone,
)
from scalper.trigger_engine import MicroLiquidity, SATrigger, SATriggerEngine  # noqa: E402


T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def frame(rows, step=15):
    return pd.DataFrame([
        {"time": T0 + timedelta(minutes=step * i), **row}
        for i, row in enumerate(rows)
    ])


def support_snapshot(price=100.4):
    zone = SRZone(
        zone_id="SR-SUPPORT-A", price=100.0, zone_low=99.5, zone_high=100.5,
        type="SUPPORT", timeframe="H4", created_at=None, last_touch=None,
        touch_count=4, rejection_count=2, strength_score=0.9,
        source="H4_STRUCTURAL_HL", active=True,
        distance_from_price=0.0 if 99.5 <= price <= 100.5 else abs(price - 100.0),
    )
    return MarketLocationSnapshot(
        symbol="XAUUSD", as_of=(T0 + timedelta(hours=1)).isoformat(),
        current_price=price, atr=1.0, location_type=AT_MAJOR_SUPPORT,
        vp_state="INSIDE_VALUE", sr_state="AT_SUPPORT",
        nearest_support=100.0, distance_to_support_atr=abs(price - 100.0),
        active_profile_id="VP-A", active_profile_timeframe="M15",
        zones=(zone,), reasons=("H4_SUPPORT",),
    )


def resistance_snapshot(price=99.6):
    zone = SRZone(
        zone_id="SR-RESISTANCE-A", price=100.0, zone_low=99.5, zone_high=100.5,
        type="RESISTANCE", timeframe="H4", created_at=None, last_touch=None,
        touch_count=4, rejection_count=2, strength_score=0.9,
        source="H4_STRUCTURAL_LH", active=True,
        distance_from_price=0.0 if 99.5 <= price <= 100.5 else abs(price - 100.0),
    )
    return MarketLocationSnapshot(
        symbol="XAUUSD", as_of=(T0 + timedelta(hours=1)).isoformat(),
        current_price=price, atr=1.0, location_type=AT_MAJOR_RESISTANCE,
        vp_state="INSIDE_VALUE", sr_state="AT_RESISTANCE",
        nearest_resistance=100.0, distance_to_resistance_atr=abs(price - 100.0),
        active_profile_id="VP-A", active_profile_timeframe="M15",
        zones=(zone,), reasons=("H4_RESISTANCE",),
    )


def value_snapshot(kind, price, event="NO_VALUE_EVENT"):
    return MarketLocationSnapshot(
        symbol="XAUUSD", as_of=(T0 + timedelta(hours=1)).isoformat(),
        current_price=price, atr=1.0, location_type=kind,
        vp_state=kind, sr_state="UNKNOWN_LOCATION",
        nearest_poc=100.0, nearest_vah=101.0, nearest_val=99.0,
        distance_to_poc_atr=abs(price - 100.0),
        distance_to_vah_atr=abs(price - 101.0),
        distance_to_val_atr=abs(price - 99.0),
        active_profile_id="VP-A", active_profile_timeframe="M15",
        value_area_event=event,
    )


def sweep_trigger_frame(bullish=True):
    if bullish:
        rows = [
            {"open": 101.0, "high": 101.2, "low": 100.6, "close": 100.8},
            {"open": 100.6, "high": 100.8, "low": 100.1, "close": 100.3},
            {"open": 100.3, "high": 100.6, "low": 99.8, "close": 100.0},
            {"open": 99.2, "high": 101.0, "low": 98.8, "close": 100.4},
        ]
    else:
        rows = [
            {"open": 99.0, "high": 99.4, "low": 98.8, "close": 99.2},
            {"open": 99.4, "high": 99.9, "low": 99.2, "close": 99.7},
            {"open": 99.7, "high": 100.2, "low": 99.5, "close": 100.0},
            {"open": 100.8, "high": 101.2, "low": 99.0, "close": 99.6},
        ]
    return frame(rows, step=15)


def confirm_frame(bullish=True):
    closes = [99.0, 99.1, 99.2, 99.3, 101.8] if bullish else [101.0, 100.9, 100.8, 100.7, 98.2]
    rows = []
    for i, close in enumerate(closes):
        rows.append({
            "open": close - 0.2 if bullish else close + 0.2,
            "high": close + 0.25,
            "low": close - 0.25,
            "close": close,
        })
    result = frame(rows, step=5)
    result["time"] = [T0 + timedelta(minutes=45 + 5 * i)
                       for i in range(len(result))]
    if bullish:
        result.loc[result.index[-1], ["open", "high", "low", "close"]] = [
            99.3, 102.05, 99.05, 101.8]
    else:
        result.loc[result.index[-1], ["open", "high", "low", "close"]] = [
            100.7, 100.95, 97.95, 98.2]
    return result


class PermissionTests(unittest.TestCase):
    def test_mid_range_sweep_is_blocked(self):
        snapshot = MarketLocationSnapshot(
            symbol="XAUUSD", as_of=T0.isoformat(), current_price=100.0,
            atr=1.0, location_type=MID_RANGE_NO_LOCATION,
            vp_state="INSIDE_VALUE", sr_state="UNKNOWN_LOCATION",
        )
        result = build_location_permission(
            snapshot, direction="BULLISH", trigger_type="SWEEP_REJECTION",
            swept_level=99.9)
        self.assertEqual(result.permission, BLOCK)
        self.assertEqual(result.reason, "MID_RANGE")

    def test_support_ssl_sweep_requires_and_can_pass_m5_confirmation(self):
        base = build_location_permission(
            support_snapshot(), direction="BULLISH",
            trigger_type="SWEEP_REJECTION", swept_level=100.0)
        result = evaluate_sweep_reaction(
            base, confirm_frame(True), sweep_time=T0 + timedelta(minutes=45),
            config=LocationPermissionConfig(m5_displacement_atr=0.5))
        self.assertEqual(result.permission, ALLOW_LONG)
        self.assertIn(result.confirmation_state,
                      {"DISPLACEMENT_CONFIRMED", "MSS_CONFIRMED", "MSS_AND_DISPLACEMENT"})

    def test_resistance_bsl_sweep_can_pass_m5_confirmation(self):
        base = build_location_permission(
            resistance_snapshot(), direction="BEARISH",
            trigger_type="SWEEP_REJECTION", swept_level=100.0)
        result = evaluate_sweep_reaction(
            base, confirm_frame(False), sweep_time=T0 + timedelta(minutes=45),
            config=LocationPermissionConfig(m5_displacement_atr=0.5))
        self.assertEqual(result.permission, ALLOW_SHORT)

    def test_val_and_vah_are_directional_locations(self):
        long_result = build_location_permission(
            value_snapshot(AT_VAL, 99.1, "VAL_REJECTION"), direction="BULLISH",
            trigger_type="SWEEP_REJECTION", swept_level=99.0)
        short_result = build_location_permission(
            value_snapshot(AT_VAH, 100.9, "VAH_REJECTION"), direction="BEARISH",
            trigger_type="SWEEP_REJECTION", swept_level=101.0)
        self.assertEqual(long_result.permission, ALLOW_LONG)
        self.assertEqual(short_result.permission, ALLOW_SHORT)

    def test_naked_value_edge_touch_is_not_permission(self):
        result = build_location_permission(
            value_snapshot(AT_VAL, 99.1), direction="BULLISH",
            trigger_type="SWEEP_REJECTION", swept_level=99.0)
        self.assertEqual(result.permission, BLOCK)

    def test_atr_proximity_boundary_is_inclusive(self):
        on_boundary = build_location_permission(
            support_snapshot(100.85), direction="BULLISH",
            trigger_type="SWEEP_REJECTION", swept_level=100.35,
            config=LocationPermissionConfig(proximity_atr=0.35))
        outside = build_location_permission(
            support_snapshot(100.851), direction="BULLISH",
            trigger_type="SWEEP_REJECTION", swept_level=100.351,
            config=LocationPermissionConfig(proximity_atr=0.35))
        self.assertEqual(on_boundary.permission, ALLOW_LONG)
        self.assertEqual(outside.permission, BLOCK)

    def test_poc_alone_is_context_only(self):
        result = build_location_permission(
            value_snapshot(AT_POC, 100.0), direction="BULLISH",
            trigger_type="SWEEP_REJECTION", swept_level=100.0)
        self.assertEqual(result.permission, CONTEXT_ONLY)
        self.assertEqual(result.reason, "POC_ONLY_NO_STRUCTURE")

    def test_reaction_without_confirmation_is_blocked(self):
        base = build_location_permission(
            support_snapshot(), direction="BULLISH",
            trigger_type="SWEEP_REJECTION", swept_level=100.0)
        weak = confirm_frame(True)
        weak.loc[weak.index[-1], ["open", "high", "low", "close"]] = [99.1, 99.9, 98.9, 99.8]
        result = evaluate_sweep_reaction(
            base, weak, sweep_time=T0 + timedelta(minutes=45),
            config=LocationPermissionConfig(m5_displacement_atr=2.0))
        self.assertEqual(result.permission, BLOCK)
        self.assertEqual(result.reason, "NO_RECLAIM")

    def test_acceptance_beyond_support_invalidates_long(self):
        base = build_location_permission(
            support_snapshot(), direction="BULLISH",
            trigger_type="SWEEP_REJECTION", swept_level=100.0)
        invalid = confirm_frame(True)
        invalid.loc[invalid.index[-2:], ["open", "high", "low", "close"]] = [
            [98.5, 99.0, 98.0, 98.4], [98.2, 98.6, 97.8, 98.0]]
        result = evaluate_sweep_reaction(
            base, invalid, sweep_time=T0 + timedelta(minutes=45))
        self.assertEqual(result.permission, BLOCK)
        self.assertEqual(result.reason, "VAL_ACCEPTANCE_BLOCK_LONG")

    def test_swept_level_too_far_from_location_is_blocked(self):
        result = build_location_permission(
            support_snapshot(), direction="BULLISH",
            trigger_type="SWEEP_REJECTION", swept_level=98.0)
        self.assertEqual(result.permission, BLOCK)
        self.assertEqual(result.reason, "TOO_FAR_FROM_LOCATION")

    def test_nearby_replacement_cannot_rescue_frozen_location(self):
        first = build_location_permission(
            support_snapshot(), direction="BULLISH",
            trigger_type="SWEEP_REJECTION", swept_level=100.0)
        later_snapshot = support_snapshot()
        later_snapshot = replace(
            later_snapshot,
            zones=(replace(later_snapshot.zones[0], zone_id="SR-SUPPORT-B"),))
        later = build_location_permission(
            later_snapshot, direction="BULLISH", frozen_location=first.frozen,
            trigger_type="SWEEP_REJECTION", swept_level=100.0)
        self.assertEqual(first.frozen.location_id, "SR-SUPPORT-A")
        self.assertEqual(first.frozen.location_id, later.frozen.location_id)
        self.assertEqual(first.frozen.zone_low, 99.5)
        self.assertEqual(later.permission, BLOCK)
        self.assertEqual(later.reason, "LOCATION_INVALIDATED")
        self.assertIn("FROZEN_LOCATION_REPLACED", later.reasons)

    def test_same_frozen_location_remains_eligible(self):
        snapshot = support_snapshot()
        first = build_location_permission(
            snapshot, direction="BULLISH",
            trigger_type="SWEEP_REJECTION", swept_level=100.0)
        later = build_location_permission(
            snapshot, direction="BULLISH", frozen_location=first.frozen,
            trigger_type="SWEEP_REJECTION", swept_level=100.0)
        self.assertEqual(later.permission, ALLOW_LONG)
        self.assertEqual(later.frozen.location_id, first.frozen.location_id)


class TriggerGateTests(unittest.TestCase):
    def test_priority_cannot_bypass_blocked_location(self):
        trigger_frame = sweep_trigger_frame(True)
        confirm = confirm_frame(True)
        engine = SATriggerEngine(enabled_triggers=["SWEEP_REJECTION", "BOS_RETEST"])
        observed = []
        sweep = SATrigger(
            detected=True, trigger_type="SWEEP_REJECTION", direction="BULLISH",
            entry_price=100.4, stop_loss=98.5, tp1=104.2, tp2=106.1,
            swept_level=100.0, sweep_time=T0 + timedelta(minutes=45))
        bos = SATrigger(
            detected=True, trigger_type="BOS_RETEST", direction="BULLISH",
            entry_price=100.4, stop_loss=98.5, tp1=104.2, tp2=106.1)
        mid = MarketLocationSnapshot(
            symbol="XAUUSD", as_of=(T0 + timedelta(hours=1)).isoformat(),
            current_price=100.4, atr=1.0, location_type=MID_RANGE_NO_LOCATION,
            vp_state="INSIDE_VALUE", sr_state="UNKNOWN_LOCATION")
        with patch.object(engine, "_check_sweep_rejection", return_value=sweep), \
                patch.object(engine, "_check_bos_retest", return_value=bos):
            result = engine.step2_trigger(
                trigger_frame, confirm, MicroLiquidity(current_price=100.4),
                "XAUUSD", market_location=mid,
                market_location_mode="ACTIVE",
                candidate_observer=observed.append)
        self.assertTrue(result.detected)
        self.assertEqual(result.trigger_type, "BOS_RETEST")
        self.assertEqual(len(observed), 2)
        self.assertEqual(observed[0].location_permission.permission, BLOCK)
        self.assertIsNone(observed[1].location_permission)

    def test_missing_runtime_location_fails_closed(self):
        engine = SATriggerEngine(enabled_triggers=["SWEEP_REJECTION"])
        candidate = SATrigger(
            detected=True, trigger_type="SWEEP_REJECTION", direction="BULLISH",
            entry_price=100.4, stop_loss=98.5, tp1=104.2, tp2=106.1,
            swept_level=100.0, sweep_time=T0)
        observed = []
        with patch.object(engine, "_check_sweep_rejection", return_value=candidate):
            result = engine.step2_trigger(
                sweep_trigger_frame(True), confirm_frame(True),
                MicroLiquidity(current_price=100.4), "XAUUSD",
                market_location=None, market_location_required=True,
                candidate_observer=observed.append)
        self.assertFalse(result.detected)
        self.assertEqual(observed[0].location_permission.rejection_code,
                         "SWEEP_REJECTION_BLOCKED_NO_LOCATION")

    def test_candidate_funnel_records_location_block_reason(self):
        trigger = SATrigger(
            detected=True, trigger_type="SWEEP_REJECTION", direction="BULLISH",
            swept_level=100.0,
            location_permission=build_location_permission(
                None, direction="BULLISH", trigger_type="SWEEP_REJECTION",
                swept_level=100.0))
        recorder = CandidateFunnelRecorder(enabled=True)
        cid = recorder.observe_trigger(trigger, "XAUUSD", T0, T0)
        recorder.record_location_result(cid, trigger, T0, T0)
        location_events = [event for event in recorder.events
                           if event.get("candidate_id") == cid
                           and event.get("stage") == "LOCATION_CHECKED"]
        self.assertEqual(location_events[-1]["status"], "FAIL")
        self.assertEqual(location_events[-1]["reason"],
                         "SWEEP_REJECTION_BLOCKED_NO_LOCATION")

    def test_live_and_replay_permission_records_match(self):
        trigger_frame = sweep_trigger_frame(True)
        confirm = confirm_frame(True)
        snapshot = support_snapshot()
        kwargs = dict(
            df_m5=trigger_frame, df_m1=confirm,
            liq=MicroLiquidity(current_price=100.4, equal_lows=[100.0]),
            symbol="XAUUSD", market_location=snapshot,
            market_location_mode="ACTIVE",
        )
        first = SATriggerEngine(enabled_triggers=["SWEEP_REJECTION"]).step2_trigger(**kwargs)
        second = SATriggerEngine(enabled_triggers=["SWEEP_REJECTION"]).step2_trigger(**kwargs)
        self.assertTrue(first.detected)
        self.assertEqual(first.location_permission.record(),
                         second.location_permission.record())


if __name__ == "__main__":
    unittest.main()
