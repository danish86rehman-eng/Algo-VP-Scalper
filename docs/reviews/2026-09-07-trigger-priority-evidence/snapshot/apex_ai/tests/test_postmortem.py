"""
Trade-forensics tests.

These pin the failure taxonomy. The whole self-improvement loop is built on
the claim that "stopped by a wick then the target printed" and "never went
onside" are distinguishable from each other; if that claim stops holding, the
remedy research downstream is being aimed at the wrong defect.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scalper import postmortem as PM
from scalper.postmortem import TradeContext, analyse, classify


T0 = datetime(2026, 8, 20, 7, 0, tzinfo=timezone.utc)


def bars(rows, start=T0, minutes=5):
    """rows = list of (high, low). Open/close are unused by the analyser."""
    return pd.DataFrame([
        {
            "time": start + timedelta(minutes=minutes * i),
            "open": (hi + lo) / 2,
            "high": hi,
            "low": lo,
            "close": (hi + lo) / 2,
        }
        for i, (hi, lo) in enumerate(rows)
    ])


def ctx(**kw):
    base = dict(
        ticket=1, symbol="XAUUSD", direction="BULLISH",
        entry=100.0, stop_loss=99.0, tp1=102.0,
        outcome="LOSS", pnl_usd=-30.0,
        open_time=T0 + timedelta(minutes=5),
        close_time=T0 + timedelta(minutes=20),
        risk_usd=30.0, sl_pips=100.0, spread_pips=4.0,
    )
    base.update(kw)
    return TradeContext(**base)


class TestClassifier(unittest.TestCase):
    """The pure decision table, independent of bar replay."""

    def test_win_with_shallow_drawdown_is_clean(self):
        self.assertEqual(
            classify("WIN_TP1", mfe_r=2.0, mae_r=0.2, tp1_after_exit=False),
            "WIN_CLEAN")

    def test_win_after_deep_drawdown_is_flagged_separately(self):
        self.assertEqual(
            classify("WIN_TP1", mfe_r=2.0, mae_r=0.9, tp1_after_exit=False),
            "WIN_SURVIVED_DD")

    def test_gave_back_winner_outranks_stop_geometry(self):
        # Reached 1.4R and still lost. Even if the target printed afterwards,
        # the defect is the exit, not the stop.
        self.assertEqual(
            classify("LOSS", mfe_r=1.4, mae_r=1.0, tp1_after_exit=True),
            "GAVE_BACK_WINNER")

    def test_stop_clip_requires_the_target_to_print_after(self):
        self.assertEqual(
            classify("LOSS", mfe_r=0.6, mae_r=1.1, tp1_after_exit=True),
            "STOP_TOO_TIGHT")
        # Same trade, target never printed -> not a stop-geometry problem.
        self.assertEqual(
            classify("LOSS", mfe_r=0.6, mae_r=1.1, tp1_after_exit=False),
            "LOSS_ORDINARY")

    def test_deep_adverse_excursion_is_not_a_clipped_stop(self):
        # Price ran 2R against us. Widening the stop would have cost more,
        # not less, so this must not be labelled STOP_TOO_TIGHT.
        self.assertEqual(
            classify("LOSS", mfe_r=0.5, mae_r=2.0, tp1_after_exit=True),
            "LOSS_ORDINARY")

    def test_never_onside_is_a_false_signal(self):
        self.assertEqual(
            classify("LOSS", mfe_r=0.05, mae_r=1.1, tp1_after_exit=False),
            "SIGNAL_FALSE")

    def test_timeout_near_target_blames_the_time_budget(self):
        self.assertEqual(
            classify("TIMEOUT", mfe_r=1.7, mae_r=0.3, tp1_after_exit=False),
            "TIMEOUT_NEAR_MISS")

    def test_timeout_without_movement_blames_the_window(self):
        self.assertEqual(
            classify("TIMEOUT", mfe_r=0.2, mae_r=0.3, tp1_after_exit=False),
            "TIMEOUT_STALLED")

    def test_two_sided_timeout_is_chop(self):
        self.assertEqual(
            classify("TIMEOUT", mfe_r=0.9, mae_r=0.8, tp1_after_exit=False),
            "TIMEOUT_CHOPPED")

    def test_every_label_is_documented(self):
        for label in ("WIN_CLEAN", "WIN_SURVIVED_DD", "STOP_TOO_TIGHT",
                      "SIGNAL_FALSE", "GAVE_BACK_WINNER", "LOSS_ORDINARY",
                      "TIMEOUT_NEAR_MISS", "TIMEOUT_STALLED",
                      "TIMEOUT_CHOPPED", "UNCLASSIFIED"):
            self.assertIn(label, PM.FAILURE_MODES)


class TestBarReplayLong(unittest.TestCase):

    def test_clipped_stop_then_target_prints(self):
        # Entry 100, SL 99, TP1 102. Price dips to 98.95 (stop taken), then
        # rallies through 102 inside the look-ahead window.
        df = bars([
            (100.2, 99.8),    # entry bar
            (100.1, 98.95),   # stop taken
            (100.5, 99.5),
            (101.0, 100.0),
            (102.4, 101.0),   # target prints, after the exit
        ])
        pm = analyse(ctx(close_time=T0 + timedelta(minutes=10)), df)
        self.assertEqual(pm.failure_mode, "STOP_TOO_TIGHT")
        self.assertTrue(pm.tp1_after_exit)
        self.assertAlmostEqual(pm.mae_r, 1.05, places=2)

    def test_lookahead_is_bounded(self):
        # Identical to the case above, but the target arrives beyond the
        # look-ahead budget. A move that landed a day later is not evidence
        # that the stop was too tight.
        df = bars([
            (100.2, 99.8),
            (100.1, 98.95),
            (100.5, 99.5),
            (101.0, 100.0),
            (102.4, 101.0),
        ])
        pm = analyse(ctx(close_time=T0 + timedelta(minutes=10)), df,
                     lookahead_bars=1)
        self.assertFalse(pm.tp1_after_exit)
        self.assertNotEqual(pm.failure_mode, "STOP_TOO_TIGHT")

    def test_signal_that_never_worked(self):
        df = bars([
            (100.05, 99.9),
            (100.02, 99.4),
            (99.9, 98.9),     # stop
            (99.5, 98.5),
        ])
        pm = analyse(ctx(close_time=T0 + timedelta(minutes=15)), df)
        self.assertEqual(pm.failure_mode, "SIGNAL_FALSE")
        self.assertLess(pm.mfe_r, PM.FALSE_SIGNAL_MFE_R)

    def test_winner_given_back(self):
        df = bars([
            (100.3, 99.9),
            (101.6, 100.2),   # +1.6R at best
            (100.5, 99.0),
            (100.0, 98.9),    # stopped
        ])
        pm = analyse(ctx(close_time=T0 + timedelta(minutes=20)), df)
        self.assertEqual(pm.failure_mode, "GAVE_BACK_WINNER")
        self.assertGreaterEqual(pm.mfe_r, PM.GAVE_BACK_MFE_R)

    def test_entry_bar_is_included_in_the_excursion(self):
        # The trade opens at 07:05 and the stop is taken on that same bar.
        # Slicing from 07:05 exclusive would lose it entirely.
        df = bars([
            (100.0, 99.95),
            (100.1, 98.90),   # the entry bar, and where the stop went
            (100.2, 99.7),
        ])
        pm = analyse(ctx(open_time=T0 + timedelta(minutes=5),
                         close_time=T0 + timedelta(minutes=5)), df)
        self.assertGreater(pm.mae_r, 1.0)


class TestBarReplayShort(unittest.TestCase):
    """Every measure must mirror. A long-only forensic is a silent blind spot
    — this repo has already shipped one long-only detector for months."""

    def test_short_excursions_mirror(self):
        # Short from 100, SL 101, TP1 98. Held bars must stay under 1R in
        # favour, or this is a given-back winner rather than a clipped stop.
        df = bars([
            (100.2, 99.8),
            (101.05, 99.9),   # stop taken at 101.05 -> MAE 1.05R
            (100.4, 99.6),    # best case 0.4R onside
            (99.0, 97.9),     # target prints after exit
        ])
        c = ctx(direction="BEARISH", entry=100.0, stop_loss=101.0, tp1=98.0,
                close_time=T0 + timedelta(minutes=10))
        pm = analyse(c, df)
        self.assertAlmostEqual(pm.mae_r, 1.05, places=2)
        self.assertTrue(pm.tp1_after_exit)
        self.assertEqual(pm.failure_mode, "STOP_TOO_TIGHT")

    def test_short_win_is_clean(self):
        df = bars([
            (100.1, 99.6),
            (99.7, 98.5),
            (98.6, 97.9),
        ])
        c = ctx(direction="BEARISH", entry=100.0, stop_loss=101.0, tp1=98.0,
                outcome="WIN_TP1", pnl_usd=60.0,
                close_time=T0 + timedelta(minutes=15))
        pm = analyse(c, df)
        self.assertEqual(pm.failure_mode, "WIN_CLEAN")
        self.assertGreater(pm.exit_r, 1.9)


class TestRobustness(unittest.TestCase):
    """A forensic pass must never be able to break the trading loop."""

    def test_missing_bars_yield_unclassified_not_an_exception(self):
        pm = analyse(ctx(), None)
        self.assertEqual(pm.failure_mode, "UNCLASSIFIED")

    def test_zero_risk_is_unclassified(self):
        pm = analyse(ctx(stop_loss=100.0), bars([(100.1, 99.9)]))
        self.assertEqual(pm.failure_mode, "UNCLASSIFIED")

    def test_bars_that_miss_the_window_are_unclassified(self):
        df = bars([(100.2, 99.8)], start=T0 - timedelta(days=3))
        pm = analyse(ctx(), df)
        self.assertEqual(pm.failure_mode, "UNCLASSIFIED")

    def test_malformed_frame_is_swallowed(self):
        df = pd.DataFrame([{"time": T0, "high": "not-a-number", "low": 1.0}])
        pm = analyse(ctx(), df)
        self.assertEqual(pm.failure_mode, "UNCLASSIFIED")

    def test_exit_r_is_zero_when_risk_usd_unknown(self):
        pm = analyse(ctx(risk_usd=0.0), bars([(100.2, 98.9), (100.1, 99.0)]))
        self.assertEqual(pm.exit_r, 0.0)

    def test_cost_heavy_flag_tracks_spread_to_stop(self):
        df = bars([(100.2, 99.8), (100.1, 98.9)])
        cheap = analyse(ctx(spread_pips=4.0, sl_pips=100.0), df)
        dear = analyse(ctx(spread_pips=15.0, sl_pips=100.0), df)
        self.assertFalse(cheap.cost_heavy)
        self.assertTrue(dear.cost_heavy)


class TestIncidentJournal(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "inc.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_and_reload_roundtrip(self):
        j = PM.IncidentJournal(str(self.path))
        df = bars([(100.2, 99.8), (100.1, 98.9)])
        j.record(analyse(ctx(ticket=11), df))
        j.record(analyse(ctx(ticket=12), df))
        rows = j.load()
        self.assertEqual([r["ticket"] for r in rows], [11, 12])

    def test_malformed_line_does_not_lose_the_rest(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text('{"ticket": 1}\nnot json\n{"ticket": 2}\n',
                             encoding="utf-8")
        rows = PM.IncidentJournal(str(self.path)).load()
        self.assertEqual([r["ticket"] for r in rows], [1, 2])

    def test_load_of_absent_file_is_empty(self):
        self.assertEqual(PM.IncidentJournal(str(self.path)).load(), [])


if __name__ == "__main__":
    unittest.main()
