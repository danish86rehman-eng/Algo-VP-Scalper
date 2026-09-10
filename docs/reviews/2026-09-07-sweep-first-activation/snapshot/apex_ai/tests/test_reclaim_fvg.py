"""L-017: causal sequence, both directions, broker replay and final quote guard."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from core.constants import MAGIC_SCALPER, MAGIC_TGA
from scalper import decision_params as DP
from scalper.reclaim_fvg import (evaluate_reclaim, entry_in_reclaim_zone,
                                close_barriers, ReclaimDecision)


def fixture():
    rows = [[103., 104., 102., 103.] for _ in range(29)]
    rows[18] = [103., 104., 100., 103.]  # known support, confirmed at bar 21
    rows[25] = [102., 103., 98., 99.]    # support broken by close
    rows[26] = [99., 99.5, 98.5, 99.]
    rows[27] = [99.2, 104.5, 99., 104.]  # displacement reclaim
    rows[28] = [104., 105., 103.5, 104.5]  # FVG 99.5..103.5 now exists
    m15 = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    m15["time"] = pd.date_range("2026-09-01", periods=len(rows), freq="15min", tz="UTC")
    formed = m15.time.iloc[-1] + pd.Timedelta(minutes=15)
    times = pd.date_range(m15.time.iloc[0], formed, freq="5min")
    m5 = pd.DataFrame({"time": times, "open": 104., "high": 105., "low": 103.7, "close": 104.5})
    m5.loc[len(m5)-1, ["open", "high", "low", "close"]] = [101.5, 103., 101., 102.]
    return m15, m5, formed + pd.Timedelta(minutes=5)


def mirror(frame):
    result = frame.copy()
    result["open"] = 200-frame.open
    result["close"] = 200-frame.close
    result["high"] = 200-frame.low
    result["low"] = 200-frame.high
    return result


class ReclaimSequenceTests(unittest.TestCase):
    def setUp(self):
        self.m15, self.m5, self.now = fixture()

    def check(self, price=102., after=None):
        return evaluate_reclaim("BULLISH", self.m15, self.m5, price, 97., 112., self.now, after)

    def test_complete_sequence_allows_both_directions(self):
        buy = self.check()
        self.assertTrue(buy.allow, buy)
        self.assertEqual(buy.reason, "RECLAIM_READY")
        self.assertEqual((buy.level, buy.fvg_low, buy.fvg_high), (100., 99.5, 103.5))
        sell = evaluate_reclaim("BEARISH", mirror(self.m15), mirror(self.m5),
                                98., 103., 88., self.now)
        self.assertTrue(sell.allow, sell)
        self.assertEqual((sell.level, sell.fvg_low, sell.fvg_high), (100., 96.5, 100.5))

    def test_broken_support_blocks_long(self):
        self.assertEqual(self.check(99.).reason, "RECLAIM_WAIT_LEVEL")

    def test_broken_resistance_blocks_short(self):
        result = evaluate_reclaim("BEARISH", mirror(self.m15), mirror(self.m5),
                                  101., 103., 88., self.now)
        self.assertEqual(result.reason, "RECLAIM_WAIT_LEVEL")

    def test_green_close_without_displacement_is_insufficient(self):
        self.m15.loc[27, ["open", "close"]] = [101., 101.2]
        self.assertEqual(self.check().reason, "RECLAIM_WAIT_DISPLACEMENT")

    def test_large_wick_is_not_displacement(self):
        self.m15.loc[27, ["open", "close"]] = [99.2, 100.5]
        self.assertEqual(self.check().reason, "RECLAIM_WAIT_DISPLACEMENT")

    def test_displacement_without_fvg_waits(self):
        self.m15.loc[28, "low"] = 99.4
        self.assertEqual(self.check().reason, "RECLAIM_WAIT_FVG")

    def test_forming_third_candle_cannot_confirm_fvg(self):
        self.now -= pd.Timedelta(minutes=6)
        self.assertEqual(self.check().reason, "RECLAIM_WAIT_FVG")

    def test_formation_bar_cannot_be_its_own_return(self):
        self.now -= pd.Timedelta(minutes=5)
        self.assertEqual(self.check().reason, "RECLAIM_WAIT_RETURN")

    def test_price_must_return_after_fvg_confirmation(self):
        self.m5.loc[len(self.m5)-1, ["open", "high", "low", "close"]] = [104., 105., 103.7, 104.5]
        self.assertEqual(self.check().reason, "RECLAIM_WAIT_RETURN")

    def test_first_return_requires_directional_confirmation(self):
        self.m5.loc[len(self.m5)-1, "open"] = 102.5
        self.assertEqual(self.check().reason, "RECLAIM_RETURN_UNCONFIRMED")

    def test_invalidated_gap_cannot_be_reused(self):
        self.m5.loc[len(self.m5)-1, ["low", "close"]] = [98., 99.]
        self.assertEqual(self.check().reason, "RECLAIM_FVG_INVALIDATED")

    def test_old_return_does_not_remain_an_entry_signal(self):
        bar = self.m5.iloc[-1].copy()
        bar["time"] += pd.Timedelta(minutes=5)
        self.m5 = pd.concat([self.m5, bar.to_frame().T], ignore_index=True)
        self.m5[["open", "high", "low", "close"]] = self.m5[["open", "high", "low", "close"]].astype(float)
        self.now += pd.Timedelta(minutes=5)
        self.assertEqual(self.check().reason, "RECLAIM_RETURN_STALE")

    def test_prior_close_requires_new_reclaim(self):
        barrier = self.m15.time.iloc[27] + pd.Timedelta(minutes=16)
        self.assertEqual(self.check(after=barrier).reason, "RECLAIM_WAIT_DISPLACEMENT")

    def test_entry_cannot_chase_above_fvg(self):
        self.assertEqual(self.check(104.).reason, "RECLAIM_ENTRY_OUTSIDE_FVG")
        ready = self.check()
        self.assertTrue(entry_in_reclaim_zone(ready, 102.))
        self.assertFalse(entry_in_reclaim_zone(ready, 104.))
        self.assertFalse(entry_in_reclaim_zone(ready, 99.6))  # gap, but below reclaimed zone

    def test_wick_only_break_is_not_a_role_flip(self):
        self.m15.loc[25, "close"] = 101.
        self.m15.loc[26, ["open", "high", "low", "close"]] = [101., 102., 100.5, 101.]
        self.assertEqual(self.check().reason, "RECLAIM_NO_BROKEN_LEVEL")

    def test_missing_and_stale_data_fail_closed(self):
        self.assertFalse(evaluate_reclaim("BULLISH", self.m15, None, 102., 97., 112., self.now).allow)
        self.now += pd.Timedelta(minutes=5)
        self.assertEqual(self.check().reason, "RECLAIM_DATA_UNAVAILABLE")

    def test_missing_middle_candle_cannot_manufacture_an_fvg(self):
        self.m15.loc[:26, "time"] -= pd.Timedelta(minutes=15)
        self.assertEqual(self.check().reason, "RECLAIM_WAIT_FVG")

    def test_missing_return_history_cannot_hide_an_earlier_touch(self):
        current_m15 = self.m15.iloc[-1:].copy()
        current_m15["time"] += pd.Timedelta(minutes=15)
        self.m15 = pd.concat([self.m15, current_m15], ignore_index=True)
        last = self.m5.iloc[-1:].copy()
        last["time"] += pd.Timedelta(minutes=10)
        self.m5 = pd.concat([self.m5, last], ignore_index=True)
        self.now += pd.Timedelta(minutes=10)
        self.assertEqual(self.check().reason, "RECLAIM_FVG_HISTORY_MISSING")

    def test_expired_fvg_cannot_approve_an_entry(self):
        with patch.object(DP, "RECLAIM_MAX_FVG_BARS", 0):
            self.assertEqual(self.check().reason, "RECLAIM_FVG_EXPIRED")

    def test_missing_or_denied_permission_fails_final_quote_check(self):
        self.assertFalse(entry_in_reclaim_zone(None, 102.))
        self.assertFalse(entry_in_reclaim_zone(ReclaimDecision(False, "RECLAIM_DATA_UNAVAILABLE"), 102.))
        no_obstacle = ReclaimDecision(True, "RECLAIM_NO_BROKEN_LEVEL")
        self.assertTrue(entry_in_reclaim_zone(no_obstacle, 102.))
        self.assertFalse(entry_in_reclaim_zone(no_obstacle, float("nan")))

    def test_future_data_does_not_change_decision(self):
        before = self.check()
        for name, minutes in (("m15", 15), ("m5", 5)):
            frame = getattr(self, name)
            future = frame.iloc[-1:].copy()
            future["time"] = self.now+pd.Timedelta(minutes=minutes)
            future.loc[:, ["open", "high", "low", "close"]] = [500., 900., 1., 600.]
            setattr(self, name, pd.concat([frame, future], ignore_index=True))
        self.assertEqual(before, self.check())

    def test_malformed_bars_fail_closed(self):
        self.m15.loc[27, "high"] = 90.
        self.assertEqual(self.check().reason, "RECLAIM_DATA_UNAVAILABLE")

    def test_unconfirmed_pivot_does_not_create_known_support(self):
        # The break occurs inside the pivot's right-hand confirmation window;
        # therefore this alleged support was never known before the break.
        self.m15.loc[18, "low"] = 102.
        self.m15.loc[24, "low"] = 100.
        self.assertEqual(self.check().reason, "RECLAIM_NO_BROKEN_LEVEL")


class BrokerBarrierTests(unittest.TestCase):
    def test_guardian_exit_restores_original_direction(self):
        import MetaTrader5 as mt5
        origin = SimpleNamespace(position_id=10, entry=mt5.DEAL_ENTRY_IN, magic=MAGIC_SCALPER,
                                 type=mt5.DEAL_TYPE_BUY, symbol="XAUUSD", time=100)
        close = SimpleNamespace(position_id=10, entry=mt5.DEAL_ENTRY_OUT, magic=MAGIC_TGA,
                                type=mt5.DEAL_TYPE_SELL, symbol="XAUUSD", time=200)
        unrelated = SimpleNamespace(position_id=11, entry=mt5.DEAL_ENTRY_OUT, magic=MAGIC_TGA,
                                    type=mt5.DEAL_TYPE_SELL, symbol="XAUUSD", time=300)
        barriers = close_barriers([close, unrelated, origin], MAGIC_SCALPER)
        self.assertEqual(list(barriers), [("XAUUSD", "BULLISH")])
        self.assertEqual(barriers[("XAUUSD", "BULLISH")].timestamp(), 200)


class IncidentAndIntegrationTests(unittest.TestCase):
    def test_live_and_backtest_import_the_same_evaluator(self):
        import scalper_agent
        import backtest_scalper
        self.assertIs(scalper_agent.evaluate_reclaim, backtest_scalper.evaluate_reclaim)
        self.assertIs(scalper_agent.entry_in_reclaim_zone, backtest_scalper.entry_in_reclaim_zone)
        self.assertTrue(DP.RECLAIM_FVG_ENABLED)

    def test_broker_replay_blocks_yesterday_and_both_today_entries(self):
        root = Path(__file__).resolve().parents[2] / "docs/reviews/2026-09-02-support-retest-evidence"
        m15 = pd.read_csv(root/"XAUUSD_M15.csv")
        m5 = pd.read_csv(root/"XAUUSD_M5.csv")
        cases = [("2026-09-01T07:15:36Z", 4433.071, 4426.099, 4447.318),
                 ("2026-09-02T08:00:20Z", 4324.048, 4317.331, 4335.133),
                 ("2026-09-02T08:21:08Z", 4325.538, 4318.853, 4338.651)]
        for at, entry, stop, target in cases:
            with self.subTest(at=at):
                result = evaluate_reclaim("BULLISH", m15, m5, entry, stop, target, pd.Timestamp(at))
                self.assertFalse(result.allow)
                self.assertEqual(result.reason, "RECLAIM_WAIT_LEVEL")

    def test_final_quote_outside_gap_never_sends_order(self):
        import scalper_agent
        from scalper.trigger_engine import SATrigger
        m15, m5, now = fixture()
        permission = evaluate_reclaim("BULLISH", m15, m5, 102., 97., 112., now)
        agent = scalper_agent.ScalperAgent.__new__(scalper_agent.ScalperAgent)
        agent.reclaim_fvg_enabled = True
        trigger = SATrigger(direction="BULLISH", reclaim=permission)
        with patch.object(scalper_agent.mt5, "symbol_info_tick", return_value=SimpleNamespace(ask=104., bid=103.9)), \
             patch.object(scalper_agent.mt5, "symbol_info", return_value=SimpleNamespace(point=.01)), \
             patch.object(scalper_agent.mt5, "order_send") as send:
            self.assertIsNone(agent._execute_trade("XAUUSD", trigger, .01))
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
