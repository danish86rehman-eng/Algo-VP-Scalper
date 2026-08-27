"""
Tests for the simulated Trade Guardian (ledger L-003).

The point of `scalper/exit_manager.py` is that the simulator makes the SAME
exit decisions as the live Guardian. Two classes of failure would make that
false while everything still ran:

  * **look-ahead** — the replay peeks at a bar the live process had not seen,
    which would make every managed backtest optimistic and unfalsifiable;
  * **drift** — the thresholds diverge from `TGAConfig`, so the simulated
    Guardian trails on a different ladder than the real one.

Both are asserted here rather than left to inspection, which is the standard
CLAUDE.md §13.4 already applies to `decision_params`.
"""
from __future__ import annotations

import logging
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scalper import decision_params as dp
from scalper.exit_manager import (
    SimulatedGuardian,
    _causal_frame,
    entry_atr_at,
)
from scalper.tga_engines import TGAConfig

logging.getLogger("TGA").setLevel(logging.CRITICAL)

T0 = datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc)


def frame(bars, start=T0):
    """Build an M5 frame from (open, high, low, close) tuples."""
    rows = []
    for i, (o, h, l, c) in enumerate(bars):
        rows.append({
            "time": pd.Timestamp(start + timedelta(minutes=5 * i)),
            "open": float(o), "high": float(h),
            "low": float(l), "close": float(c),
            "tick_volume": 100, "spread": 5,
        })
    return pd.DataFrame(rows)


def flat(n, price, start=T0):
    """`n` doji bars at `price` — inert warmup that cannot arm any stage."""
    return [(price, price, price, price)] * n


def eod_far():
    return T0 + timedelta(days=5)


class TestConfigParity(unittest.TestCase):
    """The ladder the simulator trails on must be the ladder the Guardian uses."""

    def test_tga_config_reads_decision_params(self):
        cfg = TGAConfig()
        self.assertEqual(cfg.breakeven_trigger_r, dp.TGA_BREAKEVEN_TRIGGER_R)
        self.assertEqual(cfg.stage2_trigger_r, dp.TGA_STAGE2_TRIGGER_R)
        self.assertEqual(cfg.stage3_trigger_r, dp.TGA_STAGE3_TRIGGER_R)
        self.assertEqual(cfg.stage2_trail_atr, dp.TGA_STAGE2_TRAIL_ATR)
        self.assertEqual(cfg.stage3_trail_atr, dp.TGA_STAGE3_TRAIL_ATR)
        self.assertEqual(cfg.structure_lock_atr, dp.TGA_STRUCTURE_LOCK_ATR)
        self.assertEqual(cfg.breakeven_min_close_candles,
                         dp.TGA_BREAKEVEN_MIN_CLOSE_CANDLES)
        self.assertEqual(cfg.no_progress_minutes, dp.TGA_NO_PROGRESS_MINUTES)
        self.assertEqual(cfg.no_progress_max_peak_r,
                         dp.TGA_NO_PROGRESS_MAX_PEAK_R)
        self.assertEqual(cfg.tp_extension_partial_pct,
                         dp.TGA_TP_EXTENSION_PARTIAL_PCT)

    def test_stage_ladder_is_the_documented_one(self):
        """CLAUDE.md §8 carried 0.5/1.0/2.0 for months while the code said
        1.0/1.5/2.5. Pin the code so the doc can be checked against it."""
        self.assertEqual(
            (dp.TGA_BREAKEVEN_TRIGGER_R, dp.TGA_STAGE2_TRIGGER_R,
             dp.TGA_STAGE3_TRIGGER_R), (1.0, 1.5, 2.5))

    def test_ships_off(self):
        self.assertFalse(dp.TGA_EXITS_IN_SIM)


class TestCausality(unittest.TestCase):
    """No decision may depend on a bar the live Guardian had not seen."""

    def test_causal_frame_never_contains_a_future_bar(self):
        df = frame([(1, 2, 0, 1), (10, 20, 5, 15), (99, 99, 99, 99)])
        f = _causal_frame(df, 1)
        # The synthetic forming row is built from bar 1's close, never bar 2.
        self.assertEqual(len(f), 3)
        self.assertEqual(float(f.iloc[-2]["close"]), 15.0)
        self.assertEqual(float(f.iloc[-1]["high"]), 15.0)
        self.assertNotIn(99.0, f["high"].tolist())

    def test_appending_future_bars_does_not_change_the_exit(self):
        base = flat(30, 2000.0) + [
            (2000, 2000, 1994, 1996),
            (1996, 1997, 1990, 1992),
            (1992, 1993, 1986, 1988),
            (1988, 1989, 1984, 1985),
        ]
        g = SimulatedGuardian()
        common = dict(entry_idx=30, direction="BEARISH", entry_price=2000.0,
                      stop_loss=2010.0, tp1=1980.0, entry_time=T0,
                      volume=0.02, eod=eod_far())

        short = frame(base)
        a = g.run(df_m5=short, max_idx=len(short) - 1, **common)

        # Same bars, then a violent future that would change every decision
        # if any of them could see it.
        long = frame(base + [(1985, 2400, 1985, 2400)] * 10)
        b = g.run(df_m5=long, max_idx=len(base) - 1, **common)

        self.assertEqual(a.exit_reason, b.exit_reason)
        self.assertAlmostEqual(a.exit_price, b.exit_price, places=6)
        self.assertEqual(a.sl_stage, b.sl_stage)
        self.assertAlmostEqual(a.peak_r, b.peak_r, places=6)

    def test_entry_atr_uses_only_bars_up_to_entry(self):
        bars = flat(25, 100.0) + [(100, 140, 60, 100)] * 5
        df = frame(bars)
        self.assertAlmostEqual(entry_atr_at(df, 24), entry_atr_at(
            frame(bars[:25]), 24), places=9)


class TestStopIsStillHonoured(unittest.TestCase):
    """Management must not lose the original exits."""

    def test_original_stop_still_fills(self):
        bars = flat(30, 2000.0) + [(2000, 2012, 1999, 2011)]
        g = SimulatedGuardian()
        out = g.run(df_m5=frame(bars), entry_idx=30, direction="BEARISH",
                    entry_price=2000.0, stop_loss=2010.0, tp1=1980.0,
                    entry_time=T0, volume=0.02, max_idx=len(bars) - 1,
                    eod=eod_far())
        self.assertEqual(out.exit_reason, "SL")
        self.assertEqual(out.exit_price, 2010.0)
        self.assertEqual(out.result, "LOSS")

    def test_target_still_fills(self):
        bars = flat(30, 2000.0) + [(2000, 2000, 1978, 1979)]
        g = SimulatedGuardian()
        out = g.run(df_m5=frame(bars), entry_idx=30, direction="BEARISH",
                    entry_price=2000.0, stop_loss=2010.0, tp1=1980.0,
                    entry_time=T0, volume=0.02, max_idx=len(bars) - 1,
                    eod=eod_far())
        self.assertEqual(out.exit_reason, "TP")
        self.assertEqual(out.exit_price, 1980.0)

    def test_stop_wins_the_intrabar_tie(self):
        """Unchanged from the unmanaged walk: a bar spanning both resolves to
        the stop, so the two paths differ in management, not in tie-break."""
        bars = flat(30, 2000.0) + [(2000, 2011, 1979, 1990)]
        g = SimulatedGuardian()
        out = g.run(df_m5=frame(bars), entry_idx=30, direction="BEARISH",
                    entry_price=2000.0, stop_loss=2010.0, tp1=1980.0,
                    entry_time=T0, volume=0.02, max_idx=len(bars) - 1,
                    eod=eod_far())
        self.assertEqual(out.exit_reason, "SL")


class TestBreakeven(unittest.TestCase):

    def _run(self, tail, **kw):
        bars = flat(30, 2000.0) + tail
        g = SimulatedGuardian()
        args = dict(entry_idx=30, direction="BEARISH", entry_price=2000.0,
                    stop_loss=2010.0, tp1=1980.0, entry_time=T0, volume=0.02,
                    eod=eod_far())
        args.update(kw)
        return g.run(df_m5=frame(bars), max_idx=len(bars) - 1, **args)

    def test_breakeven_arms_after_1r_with_confirmed_closes(self):
        # Two closed bars below entry, then a retrace back through entry.
        out = self._run([
            (2000, 2000, 1989, 1990),   # peak 1.0R, close holds below entry
            (1990, 1992, 1988, 1989),   # second confirming close
            (1989, 1991, 1988, 1990),   # BE now live at 2000
            (1990, 2005, 1989, 2004),   # retrace hits BE, not the 2010 stop
        ])
        self.assertEqual(out.exit_reason, "BREAKEVEN_SL")
        self.assertEqual(out.exit_price, 2000.0)
        self.assertGreaterEqual(out.sl_stage, 1)

    def test_breakeven_deferred_on_a_wick(self):
        """A +1R wick whose close does not hold must NOT move the stop.

        This is the exact failure the live threshold change was made for; if
        the simulator skipped the structure confirmation it would book
        breakevens the account never got."""
        out = self._run([
            (2000, 2001, 1989, 2000.5),   # wick to 1.1R, closes ABOVE entry
            (2000.5, 2011, 2000, 2010.5),  # straight to the original stop
        ])
        self.assertEqual(out.exit_reason, "SL")
        self.assertEqual(out.exit_price, 2010.0)


class TestNoProgress(unittest.TestCase):

    def test_no_progress_kills_a_dead_trade(self):
        """§13.10: 36 of 115 live losses closed without price reaching the
        original stop, median MAE 0.54R. A breakeven stop cannot do that; this
        path can, and it had no simulator counterpart at all."""
        # 13 bars * 5 min = 65 min > 60, peak stays under 0.3R.
        tail = [(2000, 2002, 1999, 2001)] * 13
        bars = flat(30, 2000.0) + tail
        g = SimulatedGuardian()
        out = g.run(df_m5=frame(bars), entry_idx=30, direction="BEARISH",
                    entry_price=2000.0, stop_loss=2010.0, tp1=1980.0,
                    entry_time=T0 + timedelta(minutes=150),
                    volume=0.02, max_idx=len(bars) - 1, eod=eod_far())
        self.assertEqual(out.exit_reason, "NO_PROGRESS")
        self.assertTrue(out.no_progress_closed)
        self.assertLessEqual(out.peak_r, dp.TGA_NO_PROGRESS_MAX_PEAK_R)

    def test_no_progress_spares_a_trade_that_moved(self):
        tail = [(2000, 2000, 1994, 1995)] + [(1995, 1996, 1994, 1995)] * 13
        bars = flat(30, 2000.0) + tail
        g = SimulatedGuardian()
        out = g.run(df_m5=frame(bars), entry_idx=30, direction="BEARISH",
                    entry_price=2000.0, stop_loss=2010.0, tp1=1980.0,
                    entry_time=T0 + timedelta(minutes=150),
                    volume=0.02, max_idx=len(bars) - 1, eod=eod_far())
        self.assertNotEqual(out.exit_reason, "NO_PROGRESS")


class TestTPExtension(unittest.TestCase):
    """The extension must stay reachable, must not fill what a broker would
    reject, and must keep winning the race against early close.

    The warmup here has real range on purpose. A flat warmup gives ATR 0, the
    `0.5 * entry_atr` proximity band collapses to nothing and the extension can
    never fire — a test written that way passes while asserting nothing, which
    is how the first draft of this class was wrong.
    """

    #: Alternating 4-wide bars, so ATR(14) == 4.0 and the TP band is +/- 2.0.
    WARMUP = [(1998, 2002, 1998, 2002), (2002, 2002, 1998, 1998)] * 15

    #: Three closed bars in-direction with small wicks and range >= 0.8*ATR,
    #: the last closing 1.5 inside the 1980 target's 2.0-wide band.
    #: Peak stays UNDER 1R until the final approach, so early close arms on
    #: the same bar that enters the target band. Arming earlier would let a
    #: forced trigger fire before the suppression is ever consulted, which is
    #: how the first draft of `test_running_into_tp_suppresses_early_close`
    #: passed for the wrong reason.
    RUN = [
        (2000, 2000, 1996, 1996.2),      # 0.4R
        (1996.2, 1996.2, 1991, 1991.3),  # 0.9R - still unarmed
        (1991.3, 1991.3, 1981, 1981.4),  # 1.9R, and inside the +/-2.0 band
        (1981.4, 1982, 1971, 1972),      # runs to the extended target
    ]

    def _run(self, volume, volume_min=0.01, guardian=None):
        bars = self.WARMUP + self.RUN
        g = guardian or SimulatedGuardian(volume_min=volume_min,
                                          volume_step=0.01)
        g.volume_min, g.volume_step = volume_min, 0.01
        return g.run(df_m5=frame(bars), entry_idx=len(self.WARMUP),
                     direction="BEARISH", entry_price=2000.0,
                     stop_loss=2010.0, tp1=1980.0, entry_time=T0,
                     volume=volume, max_idx=len(bars) - 1, eod=eod_far())

    def test_warmup_gives_a_real_atr(self):
        """Guard the guard: if this drifts to 0 the class stops testing."""
        df = frame(self.WARMUP + self.RUN)
        # `entry_atr_at` includes the entry bar itself, mirroring the live
        # `copy_rates_from(symbol, M5, entry_time, 20)`.
        self.assertAlmostEqual(entry_atr_at(df, len(self.WARMUP)), 4.0,
                               places=6)
        self.assertAlmostEqual(0.5 * entry_atr_at(df, len(self.WARMUP)), 2.0,
                               places=6)

    def test_extension_fires_and_banks_a_partial(self):
        out = self._run(0.02)
        self.assertTrue(out.tp_extended)
        self.assertEqual(len(out.legs), 2)
        self.assertEqual(out.legs[0].reason, "TP_EXTEND_PARTIAL")
        self.assertAlmostEqual(out.legs[0].volume, 0.01, places=6)
        self.assertAlmostEqual(sum(l.volume for l in out.legs), 0.02,
                               places=6)

    def test_extended_target_is_beyond_the_original(self):
        out = self._run(0.02)
        self.assertLess(out.final_tp, 1980.0)

    def test_single_lot_cannot_split_so_tp_is_not_extended(self):
        """0.01 lots cannot be halved; live sets `tp_extend_blocked` rather
        than pushing the target out with nothing banked."""
        out = self._run(0.01)
        self.assertFalse(out.tp_extended)
        self.assertEqual(len(out.legs), 1)

    def test_running_into_tp_suppresses_early_close(self):
        """CLAUDE.md §8: at 2R geometry the proximity band sits ABOVE the 1R
        arming threshold, so without this suppression early close always wins
        and the extension stage is dead — 13 TP_EXTENDs in May, zero in
        August. Force every early-close trigger on and require the extension
        to still happen."""
        g = SimulatedGuardian(volume_min=0.01, volume_step=0.01)
        g.early_engine.check_triggers = (
            lambda rec, df, price: (True, "forced"))
        out = self._run(0.02, guardian=g)
        self.assertTrue(out.tp_extended,
                        "early close pre-empted the extension")

    def test_early_close_still_wins_away_from_the_target(self):
        """The suppression is narrow: it must not disable early close for
        trades that are not running into their target."""
        bars = self.WARMUP + [
            (2000, 2000, 1988, 1989),      # peak 1.2R, arms early close
            (1989, 1990, 1988, 1989),      # nowhere near the 1980 band
        ]
        g = SimulatedGuardian(volume_min=0.01, volume_step=0.01)
        g.early_engine.check_triggers = (
            lambda rec, df, price: (True, "forced"))
        out = g.run(df_m5=frame(bars), entry_idx=len(self.WARMUP),
                    direction="BEARISH", entry_price=2000.0,
                    stop_loss=2010.0, tp1=1980.0, entry_time=T0,
                    volume=0.02, max_idx=len(bars) - 1, eod=eod_far())
        self.assertTrue(out.early_closed)
        self.assertEqual(out.exit_reason, "EARLY_CLOSE")


class TestEarlyClose(unittest.TestCase):

    def test_early_close_needs_1r_first(self):
        """Armed only after peak >= 1R — a reversal before that is the stop's
        business, not the early-close engine's."""
        bars = flat(30, 2000.0) + [
            (2000, 2000, 1997, 1998),        # 0.2R only
            (1998, 2003, 1997, 2002.5),      # sharp reversal candle
            (2002.5, 2011, 2002, 2010.5),
        ]
        g = SimulatedGuardian()
        out = g.run(df_m5=frame(bars), entry_idx=30, direction="BEARISH",
                    entry_price=2000.0, stop_loss=2010.0, tp1=1980.0,
                    entry_time=T0, volume=0.02, max_idx=len(bars) - 1,
                    eod=eod_far())
        self.assertFalse(out.early_closed)


class TestUnmanagedPathUnchanged(unittest.TestCase):
    """`--tga-exits` off must leave the historic walk exactly as it was."""

    def test_schedule_exit_without_guardian_returns_none_outcome(self):
        import backtest_scalper as bt
        from scalper.trigger_engine import SATrigger

        bars = flat(5, 2000.0) + [(2000, 2012, 1999, 2011)]
        trig = SATrigger()
        trig.direction = "BEARISH"
        trig.entry_price = 2000.0
        trig.stop_loss = 2010.0
        trig.tp1 = 1980.0
        price, result, reason, when, outcome = bt._schedule_exit(
            frame(bars), 5, trig, T0)
        self.assertIsNone(outcome)
        self.assertEqual(reason, "SL")
        self.assertEqual(price, 2010.0)


if __name__ == "__main__":
    unittest.main()
