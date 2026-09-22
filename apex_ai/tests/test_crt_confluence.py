"""Causal confluence labels, strict enforcement and measured-cost plumbing."""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import inspect
import unittest

import pandas as pd

from scalper import decision_params as DP
from scalper.htf_crt import watch_crt, select_crt, crt_quote_allowed, CRTPlan
from test_htf_crt import fixture, mirror


def confirmed_fixture():
    m15, m5, frames, now = fixture()
    m15.loc[140, "high"] = 106.
    m5.loc[149, ["open", "high", "low", "close"]] = [102., 105., 101.5, 103.]
    return m15, m5, frames, now


class ConfluenceTests(unittest.TestCase):
    def setUp(self):
        self.m15, self.m5, self.frames, self.now = confirmed_fixture()

    def plans(self, mode="OBSERVE"):
        return watch_crt(self.m15, self.m5, self.frames, 103., 103., self.now, confluence_mode=mode)

    def test_both_directions_pass_mss_and_completed_retest(self):
        for reflected in (False, True):
            m15, m5, frames = (mirror(self.m15), mirror(self.m5),
                {k: mirror(v) for k, v in self.frames.items()}) if reflected else (self.m15, self.m5, self.frames)
            q = 157. if reflected else 103.
            p = select_crt(watch_crt(m15, m5, frames, q, q, self.now, confluence_mode="MSS_RETEST"))
            self.assertTrue(p.allow)
            self.assertTrue(p.confluence["mss"])
            self.assertTrue(p.confluence["retest"])
            self.assertLessEqual(pd.Timestamp(p.confluence["swing_known_at"]), pd.Timestamp(p.raid_at))
            self.assertTrue(crt_quote_allowed(p, q, self.now))
            self.assertFalse(crt_quote_allowed(p, q, self.now+pd.Timedelta(minutes=5)))
            self.assertFalse(crt_quote_allowed(p, q))  # Strict final check needs a clock.

    def test_observation_does_not_filter_missing_swing(self):
        self.m15.loc[140, "high"] = 104.
        self.assertTrue(select_crt(self.plans()).allow)
        self.assertFalse(select_crt(self.plans("MSS")).allow)
        self.assertIn("CRT_WAIT_MSS", [p.reason for p in self.plans("MSS")])

    def test_pivot_confirmed_only_after_raid_is_not_known_at_raid(self):
        self.m15.loc[140, "high"] = 104.
        self.m15.loc[144, "high"] = 106.
        self.assertFalse(select_crt(self.plans("MSS")).allow)

    def test_wick_past_opposing_swing_does_not_count_as_mss(self):
        self.m15.loc[140, "high"] = 109.
        self.m15.loc[148, "high"] = 111.
        p = next(p for p in self.plans("MSS") if p.direction == "BULLISH")
        self.assertTrue(p.confluence["baseline_ready"])
        self.assertFalse(p.allow)

    def test_close_must_clear_buffer_not_just_swing(self):
        self.m15.loc[140, "high"] = 107.95
        self.assertFalse(select_crt(self.plans("MSS")).allow)

    def test_quote_inside_gap_without_directional_return_is_not_a_retest(self):
        self.m5.loc[149, "open"] = 104.
        self.assertTrue(select_crt(self.plans("MSS")).allow)
        self.assertFalse(select_crt(self.plans("MSS_RETEST")).allow)
        self.assertIn("CRT_WAIT_RETEST_CONFIRMATION", [p.reason for p in self.plans("MSS_RETEST")])

    def test_return_close_outside_gap_or_reclaimed_side_fails(self):
        for close in (100.1, 106.5):
            self.m5.loc[149, ["open", "high", "low", "close"]] = [99.5, 108., 99.2, close]
            self.assertFalse(select_crt(self.plans("MSS_RETEST")).allow)

    def test_forming_and_future_candles_cannot_change_labels(self):
        expected = [p.record() for p in self.plans("MSS_RETEST")]
        for attr, minutes in (("m15", 15), ("m5", 5)):
            frame = getattr(self, attr)
            extra = pd.DataFrame({"time": [self.now], "open": [90.], "high": [999.], "low": [1.], "close": [900.]})
            setattr(self, attr, pd.concat([frame, extra], ignore_index=True))
        self.assertEqual(expected, [p.record() for p in self.plans("MSS_RETEST")])

    def test_old_return_does_not_stay_armed(self):
        self.m5 = pd.concat([self.m5, pd.DataFrame({"time": [self.now], "open": [103.],
                            "high": [105.], "low": [102.], "close": [103.]})], ignore_index=True)
        self.now += pd.Timedelta(minutes=5)
        self.assertTrue(select_crt(self.plans("MSS")).allow)
        self.assertFalse(select_crt(self.plans("MSS_RETEST")).allow)

    def test_unknown_mode_is_not_silently_observed(self):
        with self.assertRaises(ValueError):
            self.plans("MISSPELLED")

    def test_strict_confirmation_expires_before_live_order_submission(self):
        import scalper_agent as live
        from scalper.trigger_engine import SATrigger
        p = select_crt(self.plans("MSS_RETEST"))
        a = live.ScalperAgent.__new__(live.ScalperAgent)
        with patch.object(live.mt5, "symbol_info_tick", return_value=NS(bid=102.95, ask=103.)), \
             patch.object(live.mt5, "symbol_info", return_value=NS(point=.001)), \
             patch.object(live, "datetime") as clock, patch.object(live.mt5, "order_send") as send:
            clock.now.return_value = self.now+pd.Timedelta(minutes=5)
            self.assertIsNone(a._execute_trade("XAUUSD", SATrigger(direction="BULLISH", htf_crt=p), .01))
        send.assert_not_called()

    def test_failed_watch_is_retried_without_waiting_fifteen_minutes(self):
        import scalper_agent as live
        a = live.ScalperAgent.__new__(live.ScalperAgent)
        a._crt_watch_bar, a._get_ohlcv = {}, Mock()
        a._crt_plans = Mock(side_effect=[[CRTPlan(reason="CRT_LTF_DATA_UNAVAILABLE")], [CRTPlan()]])
        a._watch_htf_crt("XAUUSD", self.now)
        self.assertEqual(a._crt_watch_bar, {})
        a._watch_htf_crt("XAUUSD", self.now)
        self.assertIn("XAUUSD", a._crt_watch_bar)

    def test_mode_defaults_and_evaluator_match_live_sim(self):
        import scalper_agent as live, backtest_scalper as sim
        self.assertIs(live.watch_crt, sim.watch_crt)
        for func in (live.ScalperAgent.__init__, sim.run_backtest):
            self.assertEqual(inspect.signature(func).parameters["crt_confluence_mode"].default, "OBSERVE")


class CostTests(unittest.TestCase):
    # Isolate confluence/cost behavior from the separate HIGH-only entry gate.
    @patch.object(DP, "entry_confidence_allowed", return_value=True)
    def test_full_replay_enforces_modes_and_costs_with_shared_detector(self, confidence_gate):
        import backtest_scalper as sim
        m15, m5, frames, now = confirmed_fixture()
        # Hold non-CRT analysis/exit engines constant; the detector, mode,
        # priority, spread/risk controls, sizing and booking are exercised.
        m5.loc[149, "open"] = 104.  # Return is bearish: A1 passes, A2 fails.
        for name, frame in (("M15", m15), ("M5", m5)):
            quote = pd.DataFrame({"time": [now.floor("15min") if name == "M15" else now],
                                  "open": [103.], "high": [104.], "low": [102.], "close": [103.]})
            frames[name] = pd.concat([frame, quote], ignore_index=True)
            frames[name]["tick_volume"], frames[name]["spread"] = 100, 5
        codes = {DP.TF_TRIGGER: frames["M15"], DP.TF_CONFIRM: frames["M5"], DP.TF_HTF: frames["M15"],
                 DP.TF_H4: frames["M15"], **{code: frames[tf] for tf, code in DP.CRT_TIMEFRAMES.items()}}
        for mode, count in (("OBSERVE", 1), ("MSS", 1), ("MSS_RETEST", 0)):
            with self.subTest(mode=mode), \
                 patch.object(sim, "_fetch", side_effect=lambda s, tf, a, b: codes[tf]), \
                 patch.object(sim.mt5, "symbol_select", return_value=True), \
                 patch.object(sim.mt5, "symbol_info", return_value=NS(point=.001, trade_tick_size=.001,
                     trade_tick_value=.1, volume_min=.01, volume_max=1., volume_step=.01, trade_contract_size=100.)), \
                 patch.object(sim.mt5, "order_send", side_effect=AssertionError("No orders in replay")), \
                 patch.object(sim.ShortTermBiasFilter, "check", return_value=NS(allow=True, confidence="HIGH",
                     reason="fixture", short_term_bias="BULLISH", htf_trend="BULLISH", recent_sweep=None)), \
                 patch.object(sim, "_apply_v1_council_gates", side_effect=lambda s, t, *args, **kwargs: (t, "")), \
                 patch.object(sim, "_order_pnl", return_value=1.), \
                 patch.object(sim, "_schedule_exit", return_value=(103., "TIMEOUT", "fixture", now, None)):
                r = sim.run_backtest(["XAUUSD"], now, now, 1000., .03, 1, 6., True, False,
                    enabled_triggers=["HTF_CRT_SWEEP"], tga_exits=False, m15_fvg_entry_enabled=False,
                    crt_confluence_mode=mode, round_trip_cost_price=.35, commission_per_lot=11.,
                    watch_inactive_crt=False, enabled_sessions=["LONDON_OPEN"])
            self.assertEqual(len(r["trades"]), count, r["rejections"])
            if count:
                t = r["trades"][0]
                self.assertEqual(t["htf_crt"]["confluence"]["mode"], mode)
                self.assertAlmostEqual(t["pnl"], round(1-.35*100*t["volume"], 2))
                self.assertAlmostEqual(r["summary"]["ending_balance"], 1000+t["pnl"])

    def test_total_cost_replaces_spread_and_commission(self):
        import backtest_scalper as sim
        with patch.object(sim.mt5, "symbol_info", return_value=NS(trade_contract_size=100.)):
            self.assertAlmostEqual(sim._execution_cost("XAUUSD", .1, 6., 1., 11., .35), 3.5)
            self.assertAlmostEqual(sim._execution_cost("XAUUSD", .1, 6., 1., 11.), 1.7)
            for symbol, cost in (("USOIL", .35), ("XAUUSD", -1.), ("XAUUSD", float("nan"))):
                with self.assertRaises(ValueError):
                    sim._execution_cost(symbol, .1, 6., 1., 11., cost)


if __name__ == "__main__":
    unittest.main()
