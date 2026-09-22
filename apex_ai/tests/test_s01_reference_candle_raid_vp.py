"""Causal S01 reference/raid state-machine tests."""
import unittest
from types import SimpleNamespace

import numpy as np
import pandas as pd

from scalper.s01_reference_candle_raid_vp import (
    BREAKOUT_ACCEPTED,
    RAID_IN_PROGRESS,
    RAID_REJECTED,
    REFERENCE_ARMED,
    S01Engine,
    S01Reference,
    S01Setup,
    VP_FROZEN,
    select_reference_candle,
)
from scalper.trigger_engine import MicroLiquidity, SATrigger, SATriggerEngine
from scalper.trigger_engine import resolve_enabled_triggers
from scalper.volume_profile import VolumeProfile


def _m15(closes, start="2026-01-03 00:00:00"):
    rows = []
    for i, close in enumerate(closes):
        close = float(close)
        rows.append({
            "time": pd.Timestamp(start) + pd.Timedelta(minutes=15 * i),
            "open": close - 0.2,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "tick_volume": 100,
        })
    return pd.DataFrame(rows)


def _d1():
    return pd.DataFrame([
        {"time": pd.Timestamp("2026-01-01"), "high": 108.0, "low": 92.0,
         "close": 100.0},
        {"time": pd.Timestamp("2026-01-02"), "high": 110.0, "low": 90.0,
         "close": 100.0},
    ])


class S01ReferenceTests(unittest.TestCase):
    def test_reference_is_selected_before_raid_and_ignores_own_candle(self):
        frame = _m15(np.linspace(100, 105, 20))
        ref = select_reference_candle(_d1(), frame, "XAUUSD", 105.0)
        self.assertIsNotNone(ref)
        self.assertEqual(ref.liquidity_side, "BSL")
        self.assertEqual(ref.high, 110.0)
        self.assertEqual(ref.low, 90.0)
        self.assertTrue(ref.reference_id.startswith("S01R_"))

    def test_reclaim_moves_to_rejected_chain(self):
        base = _m15(np.linspace(100, 105, 20))
        engine = S01Engine("XAUUSD")
        first = engine.step(base, base.iloc[-20:].copy(), _d1(),
                            current_price=105.0, spread_price=0.1)
        self.assertEqual(first.state, REFERENCE_ARMED)

        raid = pd.concat([base, _m15([109.0], "2026-01-03 05:00:00")],
                          ignore_index=True)
        raid.loc[len(raid) - 1, ["high", "close"]] = [112.0, 109.0]
        result = engine.step(raid, raid.iloc[-20:].copy(), _d1(),
                             current_price=109.0, spread_price=0.1)
        self.assertEqual(result.state, "WAIT_M15_MSS")
        states = [x["to"] for x in engine.setup.transitions]
        self.assertIn(RAID_REJECTED, states)

    def test_sustained_acceptance_cancels_reversal(self):
        base = _m15(np.linspace(100, 105, 20))
        engine = S01Engine("XAUUSD")
        engine.step(base, base.iloc[-20:].copy(), _d1(),
                    current_price=105.0, spread_price=0.1)
        first = _m15([111.0], "2026-01-03 05:00:00")
        first.loc[0, "high"] = 112.0
        frame = pd.concat([base, first], ignore_index=True)
        result = engine.step(frame, frame.iloc[-20:].copy(), _d1(),
                             current_price=111.0, spread_price=0.1)
        self.assertEqual(result.state, RAID_IN_PROGRESS)

        second = _m15([111.5], "2026-01-03 05:15:00")
        second.loc[0, "high"] = 112.5
        frame = pd.concat([frame, second], ignore_index=True)
        result = engine.step(frame, frame.iloc[-20:].copy(), _d1(),
                             current_price=111.5, spread_price=0.1)
        self.assertEqual(result.state, BREAKOUT_ACCEPTED)
        self.assertFalse(result.detected)


class S01PriorityTests(unittest.TestCase):
    def test_s01_is_opt_in_and_shared_switch_adds_it(self):
        self.assertNotIn("S01_REFERENCE_CANDLE_RAID_VP",
                         set(resolve_enabled_triggers(None, False,
                                                       crt_enabled=False)))
        self.assertIn("S01_REFERENCE_CANDLE_RAID_VP",
                      set(resolve_enabled_triggers(None, False,
                                                   crt_enabled=False,
                                                   s01_enabled=True)))

    def test_s01_wins_over_stateless_sweep_when_enabled(self):
        engine = SATriggerEngine(
            enabled_triggers={"S01_REFERENCE_CANDLE_RAID_VP", "SWEEP_REJECTION"})
        sweep = SATrigger(detected=True, trigger_type="SWEEP_REJECTION",
                          direction="BEARISH")
        s01 = SimpleNamespace(
            detected=True, direction="BEARISH", entry_price=100.0,
            stop_loss=105.0, target=90.0,
            selected_entry_model="POC", telemetry={"reference": {
                "reference_high": 110.0, "reference_low": 90.0}},
        )
        engine._check_sweep_rejection = lambda *args: sweep
        result = engine.step2_trigger(
            pd.DataFrame(), pd.DataFrame(), MicroLiquidity(), "XAUUSD",
            s01_result=s01)
        self.assertEqual(result.trigger_type, "S01_REFERENCE_CANDLE_RAID_VP")
        self.assertEqual(result.matched_triggers,
                         ["S01_REFERENCE_CANDLE_RAID_VP", "SWEEP_REJECTION"])

    def test_entry_hierarchy_prefers_poc_then_allows_edge_fallback(self):
        reference = S01Reference(pd.Timestamp("2026-01-02"), 110.0, 90.0,
                                 "BSL", "S01R_TEST")
        profile = VolumeProfile(
            poc=100.0, vah=102.0, val=98.0, profile_high=103.0,
            profile_low=97.0, bin_size=1.0, bin_count=7,
            total_volume=100.0, value_area_volume=70.0, bars_used=10)

        primary = S01Engine("XAUUSD")
        primary.setup = S01Setup("S01R_TEST", "XAUUSD", reference,
                                 state=VP_FROZEN, profile=profile)
        primary._select_entry_model(_m15([99.0]), pd.Timestamp("2026-01-03"))
        self.assertEqual(primary.setup.selected_entry_model, "POC")

        secondary = S01Engine("XAUUSD")
        secondary.setup = S01Setup("S01R_TEST", "XAUUSD", reference,
                                   state=VP_FROZEN, profile=profile)
        edge = _m15([101.5])
        edge.loc[0, "high"] = 102.2
        secondary._select_entry_model(edge, pd.Timestamp("2026-01-03"))
        self.assertEqual(secondary.setup.selected_entry_model, "VAH")


if __name__ == "__main__":
    unittest.main()
