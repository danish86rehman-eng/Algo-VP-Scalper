"""
Cooldown switch — behavioural tests.

The owner-specified contract:
    loss at 11:15 UTC -> blocked until 12:00 UTC
    loss at 11:55 UTC -> blocked until 12:00 UTC
    win               -> blocked for 5 minutes
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scalper.cooldown import SACooldown, LOSS_POLICY_FIXED_MINUTES


def utc(h, m, s=0):
    return datetime(2026, 8, 22, h, m, s, tzinfo=timezone.utc)


class TestLossCooldown(unittest.TestCase):
    def setUp(self):
        self.cd = SACooldown()

    def test_loss_at_1115_blocks_until_1200(self):
        self.cd.record_trade_result(-12.5, now=utc(11, 15))
        self.assertEqual(self.cd.active_until, utc(12, 0))
        self.assertFalse(self.cd.can_enter(utc(11, 16)))
        self.assertFalse(self.cd.can_enter(utc(11, 59, 59)))
        self.assertTrue(self.cd.can_enter(utc(12, 0)))

    def test_loss_at_1155_still_blocks_only_until_1200(self):
        self.cd.record_trade_result(-3.0, now=utc(11, 55))
        self.assertEqual(self.cd.active_until, utc(12, 0))
        self.assertEqual(self.cd.remaining_seconds(utc(11, 55)), 300)
        self.assertTrue(self.cd.can_enter(utc(12, 0, 1)))

    def test_loss_on_the_hour_blocks_a_full_hour(self):
        self.cd.record_trade_result(-1.0, now=utc(11, 0))
        self.assertEqual(self.cd.active_until, utc(12, 0))

    def test_breakeven_counts_as_loss(self):
        self.cd.record_trade_result(0.0, now=utc(11, 30))
        self.assertEqual(self.cd.active_until, utc(12, 0))

    def test_midnight_rollover(self):
        self.cd.record_trade_result(-5.0, now=utc(23, 40))
        self.assertEqual(
            self.cd.active_until,
            datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc),
        )

    def test_fixed_minutes_policy(self):
        cd = SACooldown(loss_policy=LOSS_POLICY_FIXED_MINUTES, loss_minutes=45)
        cd.record_trade_result(-2.0, now=utc(11, 15))
        self.assertEqual(cd.active_until, utc(12, 0))


class TestWinCooldown(unittest.TestCase):
    def test_win_blocks_five_minutes(self):
        cd = SACooldown()
        cd.record_trade_result(+8.0, now=utc(11, 15))
        self.assertEqual(cd.active_until, utc(11, 20))
        self.assertFalse(cd.can_enter(utc(11, 19, 59)))
        self.assertTrue(cd.can_enter(utc(11, 20)))

    def test_win_break_is_configurable(self):
        cd = SACooldown(win_minutes=12)
        cd.record_trade_result(+1.0, now=utc(11, 15))
        self.assertEqual(cd.active_until, utc(11, 27))

    def test_win_does_not_extend_to_the_hour(self):
        """A win late in the hour must NOT inherit the loss rule."""
        cd = SACooldown()
        cd.record_trade_result(+4.0, now=utc(11, 58))
        self.assertEqual(cd.active_until, utc(12, 3))


class TestSwitchAndLifecycle(unittest.TestCase):
    def test_disabled_never_blocks_but_still_records(self):
        cd = SACooldown(enabled=False)
        cd.record_trade_result(-10.0, now=utc(11, 15))
        self.assertTrue(cd.can_enter(utc(11, 16)))
        self.assertEqual(cd.active_until, utc(12, 0))  # state kept for re-enable

    def test_reset_clears(self):
        cd = SACooldown()
        cd.record_trade_result(-10.0, now=utc(11, 15))
        cd.reset()
        self.assertTrue(cd.can_enter(utc(11, 16)))

    def test_fresh_instance_allows_entry(self):
        self.assertTrue(SACooldown().can_enter(utc(11, 15)))

    def test_restore_reapplies_active_loss_pause(self):
        """A restart must not be an escape hatch from an active pause."""
        cd = SACooldown()
        cd.restore(last_close_time=utc(11, 50), last_pnl=-6.0, now=utc(11, 52))
        self.assertFalse(cd.can_enter(utc(11, 52)))
        self.assertEqual(cd.active_until, utc(12, 0))

    def test_restore_ignores_expired_pause(self):
        cd = SACooldown()
        cd.restore(last_close_time=utc(11, 50), last_pnl=-6.0, now=utc(13, 5))
        self.assertTrue(cd.can_enter(utc(13, 5)))

    def test_restore_handles_missing_history(self):
        cd = SACooldown()
        cd.restore(last_close_time=None, last_pnl=None, now=utc(11, 0))
        self.assertTrue(cd.can_enter(utc(11, 0)))

    def test_naive_datetime_is_treated_as_utc(self):
        cd = SACooldown()
        cd.record_trade_result(-1.0, now=datetime(2026, 8, 22, 11, 15))
        self.assertEqual(cd.active_until, utc(12, 0))

    def test_state_reports_remaining(self):
        cd = SACooldown()
        cd.record_trade_result(-1.0, now=utc(11, 30))
        state = cd.state(utc(11, 30))
        self.assertTrue(state.active)
        self.assertEqual(state.remaining_seconds, 1800)
        self.assertIn("LOSS", state.reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
