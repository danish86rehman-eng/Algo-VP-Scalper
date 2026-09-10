"""M15 FVG location, structural TP, causal data and Guardian target cap."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from core.constants import MAGIC_SCALPER
from scalper import decision_params as DP
from scalper.m15_fvg_entry import evaluate_fvg_entry, entry_quote_allowed, find_fvg_entry
from scalper.reclaim_fvg import evaluate_reclaim
from scalper.trigger_engine import SATrigger, SATriggerEngine, MicroLiquidity


def fixture():
    rows = [[112., 113., 111., 112.] for _ in range(29)]
    rows[18] = [112., 113., 110., 112.]
    rows[25] = [112., 113., 98., 99.]
    rows[26] = [99., 99.5, 98.5, 99.]
    rows[27] = [99.2, 104.5, 99., 104.]
    rows[28] = [104., 105., 103.5, 104.5]
    m15 = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    m15["time"] = pd.date_range("2026-09-01", periods=len(rows), freq="15min", tz="UTC")
    formed = m15.time.iloc[-1]+pd.Timedelta(minutes=15)
    m5 = pd.DataFrame({"time": pd.date_range(m15.time.iloc[0], formed, freq="5min"),
                       "open": 104., "high": 105., "low": 103.7, "close": 104.5})
    return m15, m5, formed+pd.Timedelta(minutes=5)


def mirror(frame):
    out = frame.copy()
    out["open"], out["close"] = 220-frame.open, 220-frame.close
    out["high"], out["low"] = 220-frame.low, 220-frame.high
    return out


class M15FVGTests(unittest.TestCase):
    def setUp(self):
        self.m15, self.m5, self.now = fixture()

    def check(self, quote=102., last_close=None):
        return evaluate_fvg_entry("BULLISH", self.m15, self.m5, quote, self.now, last_close)

    def test_both_directions_enter_zone_and_target_before_structure(self):
        buy = self.check()
        self.assertTrue(buy.allow, buy)
        self.assertEqual((buy.fvg_low, buy.fvg_high), (99.5, 103.5))
        self.assertLess(buy.target, buy.target_level)
        self.assertGreaterEqual(buy.target-buy.entry, 2*(buy.entry-buy.stop))
        sell = evaluate_fvg_entry("BEARISH", mirror(self.m15), mirror(self.m5),
                                  118., self.now)
        self.assertTrue(sell.allow, sell)
        self.assertAlmostEqual(sell.target, 220-buy.target)
        self.assertGreater(sell.target, sell.target_level)

    def test_fvg_target_does_not_require_breakout_through_resistance(self):
        p = self.check()
        gate = evaluate_reclaim(p.direction, self.m15, self.m5, p.entry,
                                p.stop, p.target, self.now)
        self.assertTrue(gate.allow, gate)
        self.assertEqual(gate.reason, "RECLAIM_NO_BROKEN_LEVEL")
        through = evaluate_reclaim(p.direction, self.m15, self.m5, p.entry,
                                   p.stop, 115., self.now)
        self.assertFalse(through.allow)

    def test_price_outside_zone_does_not_create_fvg_entry(self):
        self.assertFalse(self.check(104.).allow)
        self.assertFalse(self.check(99.).allow)

    def test_weak_displacement_does_not_create_zone(self):
        self.m15.loc[27, "open"] = 103.8
        self.assertFalse(self.check().allow)

    def test_third_candle_must_have_closed(self):
        self.now -= pd.Timedelta(minutes=6)
        self.assertFalse(self.check().allow)

    def test_fully_mitigated_gap_is_consumed_even_by_a_wick(self):
        self.m5.loc[len(self.m5)-1, "low"] = 99.5
        self.assertEqual(self.check().reason, "M15_FVG_CONSUMED")

    def test_partial_visit_does_not_consume_gap(self):
        self.m5.loc[len(self.m5)-1, "low"] = 102.
        self.assertTrue(self.check().allow)

    def test_same_zone_needs_departure_after_prior_close(self):
        close = self.now-pd.Timedelta(minutes=2)
        self.assertEqual(self.check(last_close=close).reason, "M15_FVG_WAIT_FRESH_RETURN")
        next_bar = self.m5.iloc[-1:].copy()
        next_bar["time"] += pd.Timedelta(minutes=5)
        self.m5 = pd.concat([self.m5, next_bar], ignore_index=True)
        self.now += pd.Timedelta(minutes=5)
        self.assertTrue(self.check(last_close=close).allow)

    def test_missing_bar_history_fails_closed(self):
        next_bar = self.m5.iloc[-1:].copy()
        next_bar["time"] += pd.Timedelta(minutes=5)
        self.m5 = pd.concat([self.m5.iloc[:-1], next_bar], ignore_index=True)
        self.now += pd.Timedelta(minutes=5)
        self.assertEqual(self.check().reason, "M15_FVG_HISTORY_MISSING")

    def test_no_structural_target_no_trade(self):
        self.m15.loc[18, "low"] = 111.
        self.assertEqual(self.check().reason, "M15_FVG_NO_STRUCTURAL_TARGET")

    def test_nearest_obstacle_cannot_be_skipped_to_improve_rr(self):
        with patch("scalper.m15_fvg_entry.broken_levels", return_value=[(105., 25, .2), (120., 25, .2)]):
            self.assertEqual(self.check().reason, "M15_FVG_TARGET_R_TOO_SMALL")

    def test_target_under_2r_is_rejected_without_tightening_stop(self):
        good = self.check()
        high = self.check(103.5)
        self.assertEqual(high.reason, "M15_FVG_TARGET_R_TOO_SMALL")
        self.assertEqual(high.stop, good.stop)
        self.assertEqual(high.target, good.target)

    def test_quote_guard_rejects_chasing_adverse_move_and_invalid_price(self):
        p = self.check()
        self.assertTrue(entry_quote_allowed(p, p.entry))
        self.assertTrue(entry_quote_allowed(p, p.entry-.1))
        self.assertFalse(entry_quote_allowed(p, p.entry+.01))
        self.assertFalse(entry_quote_allowed(p, 99.))
        self.assertFalse(entry_quote_allowed(p, float("nan")))
        self.assertFalse(entry_quote_allowed(replace(p, allow=False), p.entry))

    def test_future_bars_do_not_change_entry(self):
        before = self.check()
        for name in ("m15", "m5"):
            df = getattr(self, name)
            row = df.iloc[-1:].copy()
            row["time"] = self.now+pd.Timedelta(hours=1)
            row.loc[:, ["open", "high", "low", "close"]] = [100., 300., 1., 200.]
            setattr(self, name, pd.concat([df, row], ignore_index=True))
        self.assertEqual(before, self.check())

    def test_m15_plan_precedes_sweep_but_respects_trigger_whitelist(self):
        p = self.check()
        sweep = SATrigger(detected=True, trigger_type="SWEEP_REJECTION", direction="BULLISH")
        for enabled, expected in [(["FVG_FILL", "SWEEP_REJECTION"], "FVG_FILL"),
                                  (["SWEEP_REJECTION"], "SWEEP_REJECTION")]:
            engine = SATriggerEngine(enabled_triggers=enabled)
            with patch.object(engine, "_check_sweep_rejection", return_value=sweep), \
                 patch.object(engine, "_check_fvg_fill", return_value=SATrigger()):
                result = engine.step2_trigger(self.m15, self.m5, MicroLiquidity(), "XAUUSD",
                                               m15_fvg_entry=p)
            self.assertEqual(result.trigger_type, expected)
            if result.fvg_entry:
                self.assertEqual(result.tp1, p.target)
                self.assertEqual(result.tp2, p.target)

    def test_denied_m15_plan_cannot_fall_back_to_old_m5_fvg(self):
        engine = SATriggerEngine(enabled_triggers=["FVG_FILL"])
        denied = replace(self.check(), allow=False)
        with patch.object(engine, "_check_fvg_fill") as old_detector:
            result = engine.step2_trigger(self.m15, self.m5, MicroLiquidity(), "XAUUSD",
                                           m15_fvg_entry=denied)
        self.assertFalse(result.detected)
        old_detector.assert_not_called()


class BrokerAndExecutionTests(unittest.TestCase):
    def test_4300_is_a_conditional_plan_not_a_claimed_broker_fill(self):
        base = Path(__file__).resolve().parents[2]/"docs/reviews/2026-09-02-support-retest-evidence"
        m15, m5 = pd.read_csv(base/"XAUUSD_M15.csv"), pd.read_csv(base/"XAUUSD_M5.csv")
        p = evaluate_fvg_entry("BULLISH", m15, m5, 4300., pd.Timestamp("2026-09-02T04:20Z"))
        self.assertTrue(p.allow, p)
        self.assertAlmostEqual(p.fvg_low, 4294.004)
        self.assertAlmostEqual(p.fvg_high, 4302.272)
        self.assertLess(p.target, 4322.777)
        actual_quote = evaluate_fvg_entry("BULLISH", m15, m5, 4301.357,
                                          pd.Timestamp("2026-09-02T04:25Z"))
        self.assertEqual(actual_quote.reason, "M15_FVG_TARGET_R_TOO_SMALL")

    def test_live_and_backtest_share_entry_evaluator(self):
        import scalper_agent, backtest_scalper
        self.assertIs(scalper_agent.find_fvg_entry, backtest_scalper.find_fvg_entry)
        self.assertIs(scalper_agent.entry_quote_allowed, backtest_scalper.entry_quote_allowed)

    def test_invalid_final_quote_never_submits_an_order(self):
        import scalper_agent
        m15, m5, now = fixture()
        plan = evaluate_fvg_entry("BULLISH", m15, m5, 102., now)
        agent = scalper_agent.ScalperAgent.__new__(scalper_agent.ScalperAgent)
        trigger = SATrigger(direction="BULLISH", fvg_entry=plan)
        with patch.object(scalper_agent.mt5, "symbol_info_tick", return_value=SimpleNamespace(ask=104., bid=103.9)), \
             patch.object(scalper_agent.mt5, "symbol_info", return_value=SimpleNamespace(point=.01)), \
             patch.object(scalper_agent.logger, "warning"), \
             patch.object(scalper_agent.mt5, "order_send") as send:
            self.assertIsNone(agent._execute_trade("XAUUSD", trigger, .01))
            send.assert_not_called()

    def test_guardian_restores_structural_target_from_broker_comment(self):
        import trade_guardian_agent as tga
        agent = tga.TradeGuardianAgent.__new__(tga.TradeGuardianAgent)
        agent.registry = {}
        agent.data = SimpleNamespace(get_historical_atr=lambda *args: 4.)
        position = SimpleNamespace(ticket=1, symbol="XAUUSD", magic=MAGIC_SCALPER,
                                   time=1788280000, type=tga.mt5.ORDER_TYPE_BUY,
                                   price_open=4300., sl=4290., tp=4324., volume=.02,
                                   comment=DP.M15_FVG_ORDER_COMMENT)
        with patch.object(tga.mt5, "positions_get", return_value=[position]), \
             patch.object(tga.logger, "info"):
            agent._sync_registry()
        self.assertTrue(agent.registry[1].tp_extend_blocked)
        self.assertEqual(agent.registry[1].original_tp, 4324.)

    def test_final_cost_gate_blocks_a_new_wide_spread(self):
        import scalper_agent
        m15, m5, now = fixture()
        plan = evaluate_fvg_entry("BULLISH", m15, m5, 102., now)
        agent = scalper_agent.ScalperAgent.__new__(scalper_agent.ScalperAgent)
        agent.trigger_eng = SATriggerEngine()
        trigger = SATrigger(detected=True, direction="BULLISH", fvg_entry=plan,
                            entry_price=plan.entry, stop_loss=plan.stop, tp1=plan.target)
        with patch.object(scalper_agent.mt5, "symbol_info_tick", return_value=SimpleNamespace(ask=102., bid=101.8)), \
             patch.object(scalper_agent.mt5, "symbol_info", return_value=SimpleNamespace(point=.001)), \
             patch.object(scalper_agent.logger, "warning"), \
             patch.object(scalper_agent.mt5, "order_send") as send:
            self.assertIsNone(agent._execute_trade("XAUUSD", trigger, .01))
            send.assert_not_called()

    def test_simulated_guardian_keeps_structural_target(self):
        from scalper.exit_manager import SimulatedGuardian
        rows = [(100., 102., 98., 100.)]*30+[(100., 119.5, 100., 119.), (119., 121., 119., 120.)]
        df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
        df["time"] = pd.date_range("2026-09-01", periods=len(df), freq="5min", tz="UTC")
        guardian = SimulatedGuardian()
        with patch.object(guardian.tp_engine, "check_momentum") as momentum, \
             patch.object(guardian.early_engine, "check_triggers", return_value=(False, "")):
            out = guardian.run(df, 30, "BULLISH", 100., 90., 120., df.time.iloc[30],
                               .02, 31, df.time.iloc[-1]+pd.Timedelta(hours=1),
                               tp_extend_blocked=True)
        momentum.assert_not_called()
        self.assertFalse(out.tp_extended)
        self.assertEqual(out.exit_reason, "TP")
        self.assertEqual(out.exit_price, 120.)


if __name__ == "__main__":
    unittest.main()
