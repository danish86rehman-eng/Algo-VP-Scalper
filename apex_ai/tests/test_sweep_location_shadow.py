import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from scalper.sweep_location import (
    SweepLocationContext, build_sweep_location_context)
from scalper.trigger_engine import MicroLiquidity, SATrigger, SATriggerEngine
from scalper.short_term_bias import SessionLiquidityTracker


def frame(values, start="2026-09-18 00:00:00", minutes=15):
    idx = pd.date_range(start, periods=len(values), freq=f"{minutes}min", tz="UTC")
    return pd.DataFrame({
        "time": idx, "open": values, "high": [v + 1 for v in values],
        "low": [v - 1 for v in values], "close": values,
        "tick_volume": [100] * len(values), "volume": [100] * len(values),
    })


def trigger(level, direction="BULLISH"):
    return SATrigger(detected=True, trigger_type="SWEEP_REJECTION",
                     direction=direction, swept_level=level,
                     entry_price=level, stop_loss=level - 2)


class SweepLocationShadowTests(unittest.TestCase):
    def setUp(self):
        self.m15 = frame([100 + i * .1 for i in range(40)])
        self.now = self.m15.time.iloc[-1].to_pydatetime()

    def build(self, level, direction="BULLISH", d1=None, liq=None, fvg=None, vp=None):
        liq = liq or MicroLiquidity(current_price=level,
                                     equal_lows=[level] if direction == "BULLISH" else [],
                                     equal_highs=[level] if direction == "BEARISH" else [])
        return build_sweep_location_context(
            symbol="XAUUSD", trigger=trigger(level, direction),
            df_trigger=self.m15, liquidity=liq, now=self.now,
            df_d1=d1, m15_fvg=fvg, vp_profile=vp)

    def test_pdh_and_pdl_proximity_are_causal(self):
        d1 = frame([90], start="2026-09-17 00:00:00", minutes=1440)
        d1.loc[0, ["high", "low"]] = [110, 90]
        self.assertEqual(self.build(110, "BEARISH", d1=d1).liquidity_type, "PDH")
        self.assertEqual(self.build(90, "BULLISH", d1=d1).liquidity_type, "PDL")

    def test_anonymous_local_is_explicitly_unknown(self):
        result = self.build(108, liq=MicroLiquidity(current_price=108))
        self.assertEqual(result.liquidity_type, "UNKNOWN_LOCAL")
        self.assertEqual(result.source_timeframe, "M15")

    def test_equal_liquidity_and_atr_distance(self):
        values = [100 + ((i % 4) - 2) * .5 for i in range(80)]
        highs = [v + .5 for v in values]
        lows = [v - .5 for v in values]
        equal_frame = pd.DataFrame({
            "time": pd.date_range("2026-09-18", periods=80, freq="1min", tz="UTC"),
            "open": values, "high": highs, "low": lows, "close": values,
        })
        result = build_sweep_location_context(
            symbol="XAUUSD", trigger=trigger(98.5), df_trigger=equal_frame,
            liquidity=MicroLiquidity(current_price=98.5, equal_lows=[98.5]),
            now=equal_frame.time.iloc[-1].to_pydatetime())
        self.assertEqual(result.liquidity_type, "M15_EQUAL_LOW")
        self.assertGreaterEqual(result.touch_count, 2)
        self.assertGreaterEqual(len(result.source_swing_ids), 2)
        self.assertEqual(result.distance_price, 0.0)
        self.assertEqual(result.distance_atr, 0.0)

    def test_session_identity_is_recorded_without_gate(self):
        tracker = SessionLiquidityTracker()
        result = self.build(101.0, liq=MicroLiquidity(current_price=101.0))
        result = build_sweep_location_context(
            symbol="XAUUSD", trigger=trigger(101.0), df_trigger=self.m15,
            liquidity=MicroLiquidity(current_price=101.0), now=self.now,
            session_tracker=tracker)
        self.assertIn(result.liquidity_type, {"UNKNOWN_LOCAL", "TOKYO_LOW", "TOKYO_HIGH"})

    def test_fvg_and_vp_are_observation_only(self):
        fvg = SimpleNamespace(fvg_low=103.0, fvg_high=105.0,
                              reason="M15_FVG_READY")
        vp = SimpleNamespace(valid=True, poc=102.0, vah=106.0, val=98.0)
        result = self.build(104, fvg=fvg, vp=vp)
        self.assertEqual(result.fvg_timeframe, "M15")
        self.assertIsNotNone(result.fvg_distance_atr)
        self.assertEqual(result.vp_reference_type, "POC")
        self.assertEqual(result.premium_discount, "PREMIUM")

    def test_trigger_winner_and_geometry_remain_unchanged(self):
        # The current branch intentionally excludes SWEEP_REJECTION from the
        # constructor's default whitelist; this test exercises the explicit
        # enabled path without changing that production default.
        engine = SATriggerEngine()
        engine.enabled_triggers = {"SWEEP_REJECTION", "BOS_RETEST"}
        liq = MicroLiquidity(current_price=105, equal_lows=[100], equal_highs=[110])
        m15 = self.m15.copy()
        m15.loc[m15.index[-2], "low"] = 99
        m15.loc[m15.index[-1], ["open", "close"]] = [100, 101]
        winner = engine.step2_trigger(m15, m15, liq, "XAUUSD")
        self.assertEqual(winner.trigger_type, "SWEEP_REJECTION")
        self.assertEqual(winner.confidence, "HIGH")
        self.assertEqual(winner.stop_loss, float(m15["low"].iloc[-4:].min()) - engine._atr(m15) * .2)

    def test_context_identity_deduplicates_repeated_observations(self):
        from scalper.candidate_funnel import CandidateFunnelRecorder
        recorder = CandidateFunnelRecorder()
        t = trigger(104)
        t.location_context = SweepLocationContext(104)
        first = recorder.observe_trigger(t, "XAUUSD", self.now, self.now)
        second = recorder.observe_trigger(t, "XAUUSD", self.now, self.now)
        self.assertEqual(first, second)
        self.assertEqual(recorder.summary()["candidates"], 1)


if __name__ == "__main__":
    unittest.main()
