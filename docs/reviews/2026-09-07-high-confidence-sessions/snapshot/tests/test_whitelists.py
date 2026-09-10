"""
Tests for the trigger and session whitelists.

Both are gates. They exist in `scalper_agent.py` and `backtest_scalper.py`
identically (invariant #2), and the point of each is to let one concept be
measured in isolation — so the thing that must be pinned is that disabling a
detector actually stops it firing, and that a typo fails loudly instead of
silently disabling everything.
"""
import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from scalper.session_checker import DEFAULT_ENABLED_SESSIONS, SASessionChecker
from scalper.trigger_engine import SATriggerEngine


def _bars(n=60, start=2000.0, step=0.5):
    """Deterministic ramp with a sweep-shaped wick near the end."""
    idx = pd.date_range("2026-06-01", periods=n, freq="15min", tz="UTC")
    close = start + np.arange(n) * step
    df = pd.DataFrame({
        "time": idx,
        "open": close - 0.2,
        "high": close + 0.4,
        "low": close - 0.4,
        "close": close,
        "tick_volume": 100,
    })
    return df


class TestTriggerWhitelist(unittest.TestCase):
    def test_default_enables_every_trigger(self):
        te = SATriggerEngine()
        self.assertEqual(te.enabled_triggers, set(SATriggerEngine.ALL_TRIGGERS))

    def test_single_trigger_selection(self):
        te = SATriggerEngine(enabled_triggers=["BOS_RETEST"])
        self.assertEqual(te.enabled_triggers, {"BOS_RETEST"})

    def test_names_are_case_insensitive_and_stripped(self):
        te = SATriggerEngine(enabled_triggers=[" bos_retest ", "judas"])
        self.assertEqual(te.enabled_triggers, {"BOS_RETEST", "JUDAS"})

    def test_unknown_trigger_raises(self):
        with self.assertRaises(ValueError):
            SATriggerEngine(enabled_triggers=["SWEEP_REJECTON"])

    def test_empty_selection_raises(self):
        with self.assertRaises(ValueError):
            SATriggerEngine(enabled_triggers=[])

    def test_disabled_detector_cannot_fire(self):
        """
        The invalidation case: with SWEEP_REJECTION disabled, step2 must never
        return it, whatever the data does. This is the whole reason the flag
        exists — SWEEP_REJECTION sits first in the priority order and took 196
        of 198 trades, masking everything behind it.
        """
        df = _bars()
        te_all = SATriggerEngine()
        te_one = SATriggerEngine(enabled_triggers=["BOS_RETEST"])
        liq_all = te_all.step1_liquidity(df, "XAUUSD")
        liq_one = te_one.step1_liquidity(df, "XAUUSD")
        got_all = te_all.step2_trigger(df, df, liq_all, "XAUUSD", float(df["open"].iloc[0]))
        got_one = te_one.step2_trigger(df, df, liq_one, "XAUUSD", float(df["open"].iloc[0]))
        self.assertNotEqual(got_one.trigger_type, "SWEEP_REJECTION")
        self.assertNotEqual(got_one.trigger_type, "JUDAS")
        self.assertIn(got_all.trigger_type, set(SATriggerEngine.ALL_TRIGGERS) | {"NONE"})


class TestSessionWhitelist(unittest.TestCase):
    def test_default_enables_only_selected_windows(self):
        sc = SASessionChecker()
        self.assertEqual(sc.enabled_sessions, set(DEFAULT_ENABLED_SESSIONS))

    def test_excluded_default_windows_are_idle(self):
        for hour, minute in ((6, 45), (7, 15), (16, 45)):
            with self.subTest(hour=hour, minute=minute):
                moment = datetime(2026, 6, 1, hour, minute,
                                  tzinfo=timezone.utc)
                self.assertEqual(
                    SASessionChecker().get_state(moment).window_name, "IDLE")

    def test_excluded_window_can_be_enabled_explicitly(self):
        moment = datetime(2026, 6, 1, 7, 15, tzinfo=timezone.utc)
        state = SASessionChecker(enabled_sessions=["LONDON_OPEN"]).get_state(moment)
        self.assertTrue(state.in_window)
        self.assertEqual(state.window_name, "LONDON_OPEN")

    def test_unknown_session_raises(self):
        with self.assertRaises(ValueError):
            SASessionChecker(enabled_sessions=["LONDON"])

    def test_empty_selection_raises(self):
        with self.assertRaises(ValueError):
            SASessionChecker(enabled_sessions=[])

    def test_disabled_window_is_idle(self):
        """07:15 UTC is inside LONDON_OPEN; with London off it must be IDLE."""
        moment = datetime(2026, 6, 1, 7, 15, tzinfo=timezone.utc)
        self.assertEqual(SASessionChecker(
            enabled_sessions=["LONDON_OPEN"]).get_state(moment).window_name,
            "LONDON_OPEN")
        off = SASessionChecker(enabled_sessions=["TOKYO_OPEN"])
        state = off.get_state(moment)
        self.assertFalse(state.in_window)
        self.assertEqual(state.window_name, "IDLE")

    def test_enabled_window_still_matches(self):
        moment = datetime(2026, 6, 1, 1, 0, tzinfo=timezone.utc)
        off = SASessionChecker(enabled_sessions=["TOKYO_OPEN"])
        state = off.get_state(moment)
        self.assertTrue(state.in_window)
        self.assertEqual(state.window_name, "TOKYO_OPEN")


if __name__ == "__main__":
    unittest.main()
