"""
Pins the simulator behaviour that makes a backtest evidence rather than a
plausible-looking number:

  * the exit scan resolves to the same four profiles the live agent produces
    (TP / SL / TIMEOUT / EOD), in both directions;
  * the 23:00 UTC trading-day boundary the daily reset uses;
  * the injected clocks on the two gates that previously read the wall clock,
    which is what let the simulator run a different gate from the live agent.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import pandas as pd

from backtest_scalper import (
    FILL_AT_NEXT_BAR_OPEN,
    TIMEOUT_CONFIRM_BARS,
    _entry_fill_price,
    _eod_cutoff,
    _schedule_exit,
    _trading_day,
)
from scalper.sa_crg import SACRG
from scalper.short_term_bias import ShortTermBiasFilter
from scalper.trigger_engine import SATrigger

UTC = timezone.utc


def _bars(start: datetime, rows):
    """rows = [(high, low, close), ...] on a 5-minute grid."""
    return pd.DataFrame({
        "time": [pd.Timestamp(start + timedelta(minutes=5 * i)) for i in range(len(rows))],
        "open": [r[2] for r in rows],
        "high": [r[0] for r in rows],
        "low": [r[1] for r in rows],
        "close": [r[2] for r in rows],
    })


def _long(entry=100.0, sl=99.0, tp1=102.0):
    return SATrigger(detected=True, trigger_type="SWEEP_REJECTION",
                     direction="BULLISH", entry_price=entry,
                     stop_loss=sl, tp1=tp1, tp2=103.0)


def _short(entry=100.0, sl=101.0, tp1=98.0):
    return SATrigger(detected=True, trigger_type="SWEEP_REJECTION",
                     direction="BEARISH", entry_price=entry,
                     stop_loss=sl, tp1=tp1, tp2=97.0)


class TestTradingDayBoundary(unittest.TestCase):
    def test_rolls_at_2300_utc_not_midnight(self):
        before = datetime(2026, 5, 20, 22, 59, tzinfo=UTC)
        after = datetime(2026, 5, 20, 23, 1, tzinfo=UTC)
        self.assertNotEqual(_trading_day(before), _trading_day(after))

    def test_midnight_stays_inside_the_same_trading_day(self):
        evening = datetime(2026, 5, 20, 23, 30, tzinfo=UTC)
        next_morning = datetime(2026, 5, 21, 6, 0, tzinfo=UTC)
        self.assertEqual(_trading_day(evening), _trading_day(next_morning))

    def test_eod_cutoff_is_the_next_2300(self):
        self.assertEqual(
            _eod_cutoff(datetime(2026, 5, 20, 9, 0, tzinfo=UTC)),
            datetime(2026, 5, 20, 23, 0, tzinfo=UTC))
        # Already past it -> tomorrow's boundary.
        self.assertEqual(
            _eod_cutoff(datetime(2026, 5, 20, 23, 30, tzinfo=UTC)),
            datetime(2026, 5, 21, 23, 0, tzinfo=UTC))


class TestExitProfiles(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 5, 20, 8, 0, tzinfo=UTC)

    def test_long_take_profit(self):
        df = _bars(self.start, [(100.2, 99.8, 100.0), (102.5, 100.0, 102.2)])
        price, result, reason, _, _outcome = _schedule_exit(df, 0, _long(), self.start)
        self.assertEqual((result, reason), ("WIN_TP1", "TP"))
        self.assertEqual(price, 102.0)

    def test_long_stop_loss(self):
        df = _bars(self.start, [(100.2, 99.8, 100.0), (100.1, 98.5, 98.7)])
        price, result, reason, _, _outcome = _schedule_exit(df, 0, _long(), self.start)
        self.assertEqual((result, reason), ("LOSS", "SL"))
        self.assertEqual(price, 99.0)

    def test_short_take_profit(self):
        df = _bars(self.start, [(100.2, 99.8, 100.0), (100.0, 97.5, 97.8)])
        _, result, reason, _, _outcome = _schedule_exit(df, 0, _short(), self.start)
        self.assertEqual((result, reason), ("WIN_TP1", "TP"))

    def test_short_stop_loss(self):
        df = _bars(self.start, [(100.2, 99.8, 100.0), (101.4, 100.1, 101.2)])
        _, result, reason, _, _outcome = _schedule_exit(df, 0, _short(), self.start)
        self.assertEqual((result, reason), ("LOSS", "SL"))

    def test_stop_wins_ties_within_a_bar(self):
        """A bar spanning both levels resolves to the stop — the conservative read."""
        df = _bars(self.start, [(100.2, 99.8, 100.0), (102.5, 98.5, 101.0)])
        _, result, reason, _, _outcome = _schedule_exit(df, 0, _long(), self.start)
        self.assertEqual((result, reason), ("LOSS", "SL"))

    def test_entry_bar_can_resolve_the_trade(self):
        """
        The scan starts at the entry bar, not after it. Skipping it made the
        first bar's range invisible and silently deferred stops by 5 minutes.
        """
        df = _bars(self.start, [(102.5, 99.9, 102.1), (100.0, 99.9, 100.0)])
        _, result, reason, _, _outcome = _schedule_exit(df, 0, _long(), self.start)
        self.assertEqual((result, reason), ("WIN_TP1", "TP"))

    def test_timeout_after_the_bar_budget(self):
        quiet = [(100.2, 99.8, 100.0)] * (TIMEOUT_CONFIRM_BARS + 5)
        df = _bars(self.start, quiet)
        _, result, reason, close_time, _outcome = _schedule_exit(df, 0, _long(), self.start)
        self.assertEqual((result, reason), ("TIMEOUT", "TIMEOUT"))
        self.assertEqual(
            close_time, self.start + timedelta(minutes=5 * TIMEOUT_CONFIRM_BARS))

    def test_eod_closes_before_the_timeout_would(self):
        """A trade opened near 23:00 flattens at 23:00, not 6h later."""
        start = datetime(2026, 5, 20, 22, 30, tzinfo=UTC)
        df = _bars(start, [(100.2, 99.8, 100.0)] * (TIMEOUT_CONFIRM_BARS + 5))
        _, result, reason, close_time, _outcome = _schedule_exit(df, 0, _long(), start)
        self.assertEqual((result, reason), ("EOD", "EOD"))
        self.assertGreaterEqual(close_time, datetime(2026, 5, 20, 23, 0, tzinfo=UTC))
        self.assertLess(close_time, datetime(2026, 5, 20, 23, 10, tzinfo=UTC))


class TestInjectedClocks(unittest.TestCase):
    """
    Both gates used to call datetime.now() internally. Replaying May bars
    against today's wall clock silently disabled them in the simulator.
    """

    def test_stb_accepts_a_replayed_timestamp(self):
        replayed = datetime(2026, 5, 20, 9, 0, tzinfo=UTC)
        self.assertEqual(ShortTermBiasFilter._as_utc(replayed), replayed)

    def test_stb_naive_timestamp_is_treated_as_utc(self):
        naive = datetime(2026, 5, 20, 9, 0)
        self.assertEqual(ShortTermBiasFilter._as_utc(naive),
                         datetime(2026, 5, 20, 9, 0, tzinfo=UTC))

    def test_stb_defaults_to_the_wall_clock(self):
        drift = abs((ShortTermBiasFilter._as_utc(None)
                     - datetime.now(UTC)).total_seconds())
        self.assertLess(drift, 5)

    def test_crg_pause_timer_runs_on_the_injected_clock(self):
        crg = SACRG()
        armed_at = datetime(2026, 5, 20, 9, 0, tzinfo=UTC)
        kwargs = dict(sa_daily_loss_usd=0.0, sa_max_daily_loss_usd=50.0,
                      sa_open_positions=0, current_spread_pips=1.0,
                      max_spread_pips=10.0, main_account_dd_pct=0.0)

        # Two consecutive losses arm the 15-minute pause at the replayed time.
        self.assertFalse(
            crg.check(sa_consecutive_losses=2, now=armed_at, **kwargs).approved)
        # Still inside the pause 10 minutes later.
        self.assertFalse(
            crg.check(sa_consecutive_losses=2,
                      now=armed_at + timedelta(minutes=10), **kwargs).approved)
        # Elapsed 20 minutes later — on the replayed clock, not the real one.
        self.assertTrue(
            crg.check(sa_consecutive_losses=2,
                      now=armed_at + timedelta(minutes=20), **kwargs).approved)




class TestEntryFillModel(unittest.TestCase):
    """
    Ledger L-013 stage S1. The simulator used to book P&L at the signal price
    while `_schedule_exit` already timed exits from the entry bar's open; these
    pin the fill to one event and pin the flag in both positions.
    """

    def setUp(self):
        self.start = datetime(2026, 5, 20, 8, 0, tzinfo=UTC)
        # Entry bar opens at 100.4 (close 100.1), i.e. away from the 100.0
        # signal price, so the two models are distinguishable.
        self.df = _bars(self.start, [(100.6, 99.9, 100.1), (101.0, 100.0, 100.8)])
        self.df.loc[0, "open"] = 100.4

    def test_long_fills_at_the_entry_bar_open(self):
        self.assertEqual(
            _entry_fill_price(self.df, 0, _long(entry=100.0), True), 100.4)

    def test_short_fills_at_the_entry_bar_open(self):
        self.assertEqual(
            _entry_fill_price(self.df, 0, _short(entry=100.0), True), 100.4)

    def test_long_disabled_reproduces_the_perfect_fill(self):
        self.assertEqual(
            _entry_fill_price(self.df, 0, _long(entry=100.0), False), 100.0)

    def test_short_disabled_reproduces_the_perfect_fill(self):
        self.assertEqual(
            _entry_fill_price(self.df, 0, _short(entry=100.0), False), 100.0)

    def test_reads_the_indexed_bar_not_the_first_one(self):
        self.assertEqual(
            _entry_fill_price(self.df, 1, _long(entry=100.0), True), 100.8)

    def test_slippage_sign_is_adverse_for_a_long_when_the_open_gaps_up(self):
        # A long filled above its signal price has paid up: worse entry, and
        # since SL/TP stay anchored the realized R must fall short of 2.0.
        fill = _entry_fill_price(self.df, 0, _long(entry=100.0), True)
        self.assertGreater(fill, 100.0)

    def test_slippage_sign_is_adverse_for_a_short_when_the_open_gaps_up(self):
        # The same bar is a *favourable* open for a short. The model is not a
        # penalty applied to every trade - it is whatever the bar did, which is
        # why it has to be measured rather than assumed.
        fill = _entry_fill_price(self.df, 0, _short(entry=100.0), True)
        self.assertGreater(fill, 100.0)

    def test_sl_and_tp_stay_anchored_to_the_signal_price(self):
        # L-012's remedy is re-anchoring these to the fill. S1 must NOT do it:
        # the live agent anchors to the signal price, and changing that is an
        # exit-side change blocked by L-003.
        trig = _long(entry=100.0, sl=99.0, tp1=102.0)
        _entry_fill_price(self.df, 0, trig, True)
        self.assertEqual(trig.stop_loss, 99.0)
        self.assertEqual(trig.tp1, 102.0)
        self.assertEqual(trig.entry_price, 100.0)

    def test_ships_enabled(self):
        # A fidelity fix has no correct "off" position; the flag exists only to
        # regenerate pre-S1 tables.
        self.assertTrue(FILL_AT_NEXT_BAR_OPEN)


if __name__ == "__main__":
    unittest.main()
