"""Calendar-causal CRT trigger, both directions, execution and recovery."""
from dataclasses import replace
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from core.constants import MAGIC_SCALPER
from scalper import decision_params as DP
from scalper.htf_crt import (watch_crt, select_crt, period_end, closed_ranges,
                             used_setups, entry_quote_allowed, crt_quote_allowed, CRTPlan)
from scalper.trigger_engine import SATrigger, SATriggerEngine, MicroLiquidity, resolve_enabled_triggers


def fixture():
    m15 = pd.DataFrame({"time": pd.date_range(end="2026-09-02T07:00Z", periods=150, freq="15min"),
                        "open": 103., "high": 104., "low": 102., "close": 103.})
    m15.loc[146, ["open", "high", "low", "close"]] = [103., 104., 96., 98.]
    m15.loc[147, ["open", "high", "low", "close"]] = [98., 99., 97., 98.]
    m15.loc[148, ["open", "high", "low", "close"]] = [98., 109., 97.5, 108.]
    m15.loc[149, ["open", "high", "low", "close"]] = [108., 110., 106., 108.]
    formed = m15.time.iloc[-1]+pd.Timedelta(minutes=15)
    m5 = pd.DataFrame({"time": pd.date_range(end=formed, periods=150, freq="5min"),
                       "open": 107., "high": 108., "low": 105.5, "close": 107.})
    frames = {tf: pd.DataFrame({"time": [pd.Timestamp(t)], "open": [150.],
                                "high": [160.], "low": [100.], "close": [103.]})
              for tf, t in [("D1", "2026-09-01T00:00Z"), ("W1", "2026-08-23T00:00Z"),
                             ("MN1", "2026-08-01T00:00Z")]}
    return m15, m5, frames, formed+pd.Timedelta(minutes=5)


def mirror(df):
    f = df.copy()
    f["open"], f["close"] = 260-df.open, 260-df.close
    f["high"], f["low"] = 260-df.low, 260-df.high
    return f


class CRTTests(unittest.TestCase):
    def setUp(self):
        self.m15, self.m5, self.frames, self.now = fixture()

    def plans(self, quote=103., **kwargs):
        return watch_crt(self.m15, self.m5, self.frames, quote, quote, self.now, **kwargs)

    def plan(self, **kwargs):
        return select_crt(self.plans(**kwargs))

    def test_daily_weekly_monthly_both_sides_are_real_triggers(self):
        for mirrored in [False, True]:
            plans = (self.plans() if not mirrored else watch_crt(
                mirror(self.m15), mirror(self.m5), {k: mirror(v) for k, v in self.frames.items()},
                157., 157., self.now))
            ready = [p for p in plans if p.allow]
            self.assertEqual([p.timeframe for p in ready], ["MN1", "W1", "D1"], plans)
            for p in ready:
                self.assertEqual(p.direction, "BEARISH" if mirrored else "BULLISH")
                self.assertTrue(entry_quote_allowed(p, p.entry))
                engine = SATriggerEngine(enabled_triggers=["HTF_CRT_SWEEP"])
                t = engine.step2_trigger(self.m15, self.m5, MicroLiquidity(), "XAUUSD", htf_crt=p)
                self.assertTrue(t.detected)
                self.assertEqual(t.tp1, p.target)
                self.assertEqual(t.tp2, t.tp1)

    def test_previous_anchor_colour_is_not_a_direction_gate(self):
        self.assertTrue(self.plan().allow)  # Red anchor can have an SSL reversal.

    def test_priority_above_vp_and_sweep_and_respects_whitelist(self):
        engine = SATriggerEngine(enabled_triggers=["HTF_CRT_SWEEP", "VP_LIQUIDITY_REACTION", "SWEEP_REJECTION"])
        with patch.object(engine, "_check_vp_liquidity_reaction", return_value=SATrigger(detected=True, trigger_type="VP_LIQUIDITY_REACTION")), \
             patch.object(engine, "_check_sweep_rejection", return_value=SATrigger(detected=True, trigger_type="SWEEP_REJECTION")):
            t = engine.step2_trigger(self.m15, self.m5, MicroLiquidity(), "XAUUSD", htf_crt=self.plan(), vplr_ctx=object())
        self.assertEqual(t.matched_triggers, ["HTF_CRT_SWEEP", "VP_LIQUIDITY_REACTION", "SWEEP_REJECTION"])
        engine = SATriggerEngine(enabled_triggers=["BOS_RETEST"])
        with patch.object(engine, "_check_bos_retest", return_value=SATrigger()):
            self.assertFalse(engine.step2_trigger(self.m15, self.m5, MicroLiquidity(), "XAUUSD", htf_crt=self.plan()).detected)
        self.assertNotIn("HTF_CRT_SWEEP", resolve_enabled_triggers(None, False, False))
        with self.assertRaises(ValueError):
            resolve_enabled_triggers(["HTF_CRT_SWEEP"], False, False)

    def test_wick_only_or_weak_reclaim_never_enters(self):
        self.m15.loc[148, "open"] = 107.
        self.assertFalse(self.plan().allow)
        self.assertTrue(any(p.reason == "CRT_SWEPT_WAIT_DISPLACEMENT" for p in self.plans()))

    def test_no_fvg_no_trade(self):
        self.m15.loc[149, "low"] = 98.
        self.assertFalse(self.plan().allow)

    def test_displacement_may_start_inside_reclaimed_range(self):
        self.m15.loc[148, "open"] = 100.5
        self.assertTrue(self.plan().allow)

    def test_entry_expires_with_fresh_ltf_history(self):
        self.now += pd.Timedelta(hours=6)
        for name, minutes in [("m15", 15), ("m5", 5)]:
            f = getattr(self, name)
            times = pd.date_range(f.time.iloc[-1]+pd.Timedelta(minutes=minutes),
                                  self.now-pd.Timedelta(minutes=minutes), freq=f"{minutes}min")
            more = pd.DataFrame({"time": times, "open": 108., "high": 109., "low": 107., "close": 108.})
            setattr(self, name, pd.concat([f, more], ignore_index=True))
        self.assertFalse(self.plan().allow)
        self.assertTrue(any(p.reason == "CRT_EXPIRED" for p in self.plans()))

    def test_no_entry_at_extreme_or_chased_quote(self):
        self.assertFalse(self.plan(quote=96.).allow)
        self.assertFalse(self.plan(quote=107.).allow)
        self.assertFalse(entry_quote_allowed(self.plan(), 103.01))
        self.assertFalse(crt_quote_allowed(self.plan(), 99.9))  # In gap, below reclaimed level.

    def test_third_bar_must_close_and_return_must_be_later(self):
        self.now -= pd.Timedelta(minutes=6)
        self.assertFalse(self.plan().allow)

    def test_far_edge_mitigation_or_lost_level_invalidates(self):
        self.m5.loc[149, "low"] = 99.
        self.assertFalse(self.plan().allow)
        self.assertTrue(any(p.reason == "CRT_INVALIDATED" for p in self.plans()))

    def test_both_range_edges_swept_abstains(self):
        self.m15.loc[146, "high"] = 161.
        self.assertFalse(self.plan().allow)

    def test_missing_confirmation_history_denies(self):
        self.m5 = self.m5.iloc[:-1]
        self.assertFalse(self.plan().allow)

    def test_stop_beyond_raid_and_target_not_stretched_for_rr(self):
        p = self.plan()
        self.assertLess(p.stop, p.raid_extreme)
        with patch("scalper.htf_crt.broken_levels", return_value=[(106., 1, .2), (140., 1, .2)]):
            bad = next(p for p in self.plans() if p.direction == "BULLISH")
        self.assertEqual(bad.reason, "CRT_TARGET_R_TOO_SMALL")
        self.assertEqual(bad.stop, p.stop)
        self.assertEqual(bad.target, 105.8)

    def test_setup_consumed_from_broker_and_close_blocks_other_frames(self):
        ids = {p.setup_id for p in self.plans() if p.allow}
        self.assertFalse(self.plan(used=ids).allow)
        self.assertFalse(self.plan(last_closes={("XAUUSD", "BULLISH"): self.now}, symbol="XAUUSD").allow)
        p = self.plan()
        deals = [NS(magic=MAGIC_SCALPER, entry=0, comment=DP.CRT_ORDER_PREFIX+p.setup_id),
                 NS(magic=0, entry=1, comment=DP.CRT_ORDER_PREFIX+"other")]
        self.assertEqual(used_setups(deals, MAGIC_SCALPER), {p.setup_id})
        self.assertNotEqual(self.plan(symbol="XAUUSD").setup_id,
                            self.plan(symbol="XAGUSD").setup_id)

    def test_fresh_gap_survives_calendar_rollover_without_using_new_anchor(self):
        for tf, anchor, last in [("D1", "2026-09-01", "2026-09-02T23:45Z"),
                                  ("W1", "2026-08-23", "2026-09-05T23:45Z"),
                                  ("MN1", "2026-08-01", "2026-09-30T23:45Z")]:
            m15, m5, frames, now = fixture()
            delta = pd.Timestamp(last)-m15.time.iloc[-1]
            m15.time += delta
            m5.time += delta
            now += delta
            f = frames[tf]
            current = f.copy()
            current.time = [period_end(f.time.iloc[0], tf)]
            frames[tf] = pd.concat([f, current], ignore_index=True)
            plans = watch_crt(m15, m5, frames, 103., 103., now)
            p = next(p for p in plans if p.timeframe == tf and p.direction == "BULLISH")
            self.assertTrue(p.allow, p)
            self.assertEqual(p.anchor_at, pd.Timestamp(anchor, tz="UTC").isoformat())

    def test_ready_directions_conflict_abstains(self):
        p = self.plan()
        self.assertEqual(select_crt([p, replace(p, direction="BEARISH")]).reason, "CRT_DIRECTION_CONFLICT")

    def test_future_htf_ohlc_and_ltf_bars_cannot_change_decision(self):
        expected = self.plans()
        for tf, frame in self.frames.items():
            future = frame.copy()
            future.time = [period_end(frame.time.iloc[0], tf)]
            future.high, future.low = 900., 1.
            self.frames[tf] = pd.concat([frame, future], ignore_index=True)
        for name in ("m15", "m5"):
            df = getattr(self, name)
            future = df.iloc[-1:].copy()
            future.time = self.now+pd.Timedelta(hours=1)
            future.high, future.low = 900., 1.
            setattr(self, name, pd.concat([df, future], ignore_index=True))
        self.assertEqual(expected, self.plans())

    def test_month_calendar_and_broker_sunday_week(self):
        for start, end in [("2026-02-01", "2026-03-01"), ("2024-02-01", "2024-03-01"),
                           ("2026-12-01", "2027-01-01")]:
            self.assertEqual(period_end(pd.Timestamp(start, tz="UTC"), "MN1"), pd.Timestamp(end, tz="UTC"))
        self.assertEqual(period_end(pd.Timestamp("2026-08-23", tz="UTC"), "W1"), pd.Timestamp("2026-08-30", tz="UTC"))

    def test_future_anchor_is_not_previous_period(self):
        for tf, frame in self.frames.items():
            frame.time = [period_end(frame.time.iloc[0], tf)]
        self.assertFalse(self.plan().allow)

    def test_missing_or_corrupt_htf_is_explicit(self):
        self.frames["MN1"] = None
        self.frames["W1"].loc[0, "low"] = 200.
        plans = self.plans()
        self.assertEqual(sum(p.reason == "CRT_HTF_DATA_UNAVAILABLE" for p in plans), 4)
        self.assertEqual(select_crt(plans).timeframe, "D1")

    def test_stale_daily_range_is_not_reported_as_current_watch(self):
        self.frames["D1"].time -= pd.Timedelta(days=2)
        self.assertEqual([p.reason for p in self.plans() if p.timeframe == "D1"],
                         ["CRT_HTF_DATA_STALE", "CRT_HTF_DATA_STALE"])


class CRTIntegrationTests(unittest.TestCase):
    def test_backtest_emits_crt_trade_with_shared_metadata(self):
        import backtest_scalper as sim
        m15, m5, frames, now = fixture()
        # Supply one current quote after a completed M5 bar, with exit plumbing
        # stubbed. Detector, priority, cost and risk paths remain real.
        m15 = pd.concat([m15, pd.DataFrame({"time": [now.floor("15min")], "open": [103.],
                                            "high": [108.], "low": [102.], "close": [104.]})], ignore_index=True)
        m5 = pd.concat([m5, pd.DataFrame({"time": [now], "open": [103.], "high": [104.],
                                         "low": [102.], "close": [103.]})], ignore_index=True)
        for f in [m15, m5]:
            f["tick_volume"], f["spread"] = 100, 5
        codes = {DP.TF_TRIGGER: m15, DP.TF_CONFIRM: m5, DP.TF_HTF: m15, DP.TF_H4: m15,
                 **{code: frames[tf] for tf, code in DP.CRT_TIMEFRAMES.items()}}
        with patch.object(sim, "_fetch", side_effect=lambda s, tf, a, b: codes[tf]), \
             patch.object(sim.mt5, "symbol_select", return_value=True), \
             patch.object(sim.mt5, "symbol_info", return_value=NS(point=.001, trade_tick_size=.001,
                 trade_tick_value=.1, volume_min=.01, volume_max=1., volume_step=.01)), \
             patch.object(sim.mt5, "order_send", side_effect=AssertionError("No orders in backtest")), \
             patch.object(sim.ShortTermBiasFilter, "check", return_value=NS(allow=True, confidence="HIGH",
                 reason="fixture", short_term_bias="BULLISH", htf_trend="BULLISH", recent_sweep=None)), \
             patch.object(sim, "_apply_v1_council_gates", side_effect=lambda s, t, *args: (t, "")), \
             patch.object(sim, "_schedule_exit", return_value=(103., "TIMEOUT", "fixture", now, None)):
            r = sim.run_backtest(["XAUUSD"], now, now, 1000., .03, 1, 2.5, True, False,
                 htf_crt_enabled=True, enabled_triggers=["HTF_CRT_SWEEP"], tga_exits=False,
                 m15_fvg_entry_enabled=False, reclaim_fvg_enabled=True,
                 enabled_sessions=["LONDON_OPEN"])
        self.assertEqual(len(r["trades"]), 1, r["rejections"])
        self.assertEqual(r["trades"][0]["htf_crt"]["timeframe"], "MN1")

    def test_scan_reaches_execution_for_all_frames_and_directions(self):
        import scalper_agent as live
        from scalper.sa_crg import SACRG
        from scalper.session_checker import SASessionChecker
        m15, m5, frames, now = fixture()
        for mirrored in (False, True):
            for tf in DP.CRT_TIMEFRAMES:
                a = live.ScalperAgent.__new__(live.ScalperAgent)
                for flag in ("va_fade_enabled", "vp_gate_enabled", "leg_conf_enabled", "vplr_enabled",
                             "m15_fvg_entry_enabled", "ema_band_enabled", "pdr_gate_enabled", "rd_gate_enabled"):
                    setattr(a, flag, False)
                a.htf_crt_enabled, a.reclaim_fvg_enabled, a._reclaim_history_ready = True, True, True
                a._crt_used, a._reclaim_last_closes, a._open_trades, a._session_open_prices = set(), {}, {}, {}
                a.cooldown, a.rejects = NS(can_enter=lambda t: True), Mock()
                a.trigger_eng = SATriggerEngine(enabled_triggers=["HTF_CRT_SWEEP"])
                frame15, frame5 = (mirror(m15), mirror(m5)) if mirrored else (m15, m5)
                a._get_ohlcv = lambda symbol, code, bars=100: frame5 if code == DP.TF_CONFIRM else frame15
                a._crt_frames = lambda symbol, t: {tf: mirror(frames[tf]) if mirrored else frames[tf]}
                direction, quote = ("BEARISH", 157.) if mirrored else ("BULLISH", 103.)
                # Other engines have their own suites. Hold their outputs fixed
                # to test actual CRT -> reclaim/cost/risk -> broker wiring.
                a.stb_filter = NS(check=lambda **kw: NS(allow=True, confidence="HIGH", short_term_bias=direction,
                                                      htf_trend=direction, recent_sweep=None, reason="fixture"))
                a.consultant = NS(consult=lambda **kw: NS(success=True, stale_data=False, latency_ms=0.,
                                                        regime="MANIPULATION", displacement_confirmed=True,
                                                        lia_tp2_override=999.))
                a.news_guard = NS(active_blackout=lambda **kw: None)
                a.crg, a.session = SACRG(), SASessionChecker()
                a.pool = NS(daily_pnl=0., max_daily_loss=100., open_positions=0, consecutive_losses=0,
                            risk_per_trade_usd=30., register_open=Mock())
                a._calculate_lots, a._log_trade, a.dry_run = Mock(return_value=.01), Mock(), False
                tick = NS(bid=quote if mirrored else quote-.05, ask=quote+.05 if mirrored else quote)
                with patch.object(live.mt5, "symbol_info_tick", return_value=tick), \
                     patch.object(live.mt5, "symbol_info", return_value=NS(point=.001, digits=3)), \
                     patch.object(live.mt5, "order_send", return_value=NS(retcode=live.mt5.TRADE_RETCODE_DONE, order=7)) as send, \
                     patch.object(live.logger, "info"), patch.object(live.logger, "warning"):
                    a._scan_symbol("XAUUSD", a.session.get_state(now), 0., now)
                self.assertEqual(send.call_count, 1, (tf, direction, a.rejects.mock_calls))
                self.assertEqual(a._open_trades[7]["htf_crt"]["timeframe"], tf)
                self.assertNotEqual(a._open_trades[7]["tp2"], 999.)

    def test_shared_live_backtest_evaluator(self):
        import scalper_agent as live, backtest_scalper as sim
        self.assertIs(live.watch_crt, sim.watch_crt)
        self.assertIs(live.select_crt, sim.select_crt)

    def test_final_quote_execution_comment_recovery_and_guardian_cap(self):
        import scalper_agent as live, trade_guardian_agent as tga
        m15, m5, frames, now = fixture()
        for direction, quote, mirrored in [("BULLISH", 103., False), ("BEARISH", 157., True)]:
            plans = watch_crt(mirror(m15) if mirrored else m15, mirror(m5) if mirrored else m5,
                              {k: mirror(v) for k, v in frames.items()} if mirrored else frames,
                              quote, quote, now)
            for p in (p for p in plans if p.allow):
                engine = SATriggerEngine(enabled_triggers=["HTF_CRT_SWEEP"])
                trig = engine.step2_trigger(m15, m5, MicroLiquidity(), "XAUUSD", htf_crt=p)
                agent = live.ScalperAgent.__new__(live.ScalperAgent)
                agent.trigger_eng, agent._crt_used = engine, set()
                agent.reclaim_fvg_enabled = False
                tick = NS(bid=quote if mirrored else quote-.05, ask=quote+.05 if mirrored else quote)
                with patch.object(live.mt5, "symbol_info_tick", return_value=tick), \
                     patch.object(live.mt5, "symbol_info", return_value=NS(point=.001, digits=3)), \
                     patch.object(live.mt5, "order_send", return_value=NS(retcode=live.mt5.TRADE_RETCODE_DONE, order=7)) as send, \
                     patch.object(live.logger, "info"), patch.object(live.logger, "warning"):
                    self.assertEqual(agent._execute_trade("XAUUSD", trig, .01), 7)
                req = send.call_args.args[0]
                self.assertEqual(req["comment"], DP.CRT_ORDER_PREFIX+p.setup_id)
                self.assertIn(p.setup_id, agent._crt_used)
                guardian = tga.TradeGuardianAgent.__new__(tga.TradeGuardianAgent)
                guardian.registry = {}
                guardian.data = NS(get_historical_atr=lambda *args: 4.)
                pos = NS(ticket=7, symbol="XAUUSD", magic=MAGIC_SCALPER, time=int(now.timestamp()),
                          type=req["type"], price_open=quote, sl=req["sl"], tp=req["tp"], volume=.01, comment=req["comment"])
                with patch.object(tga.mt5, "positions_get", return_value=[pos]), patch.object(tga.logger, "info"):
                    guardian._sync_registry()
                self.assertTrue(guardian.registry[7].tp_extend_blocked)

    def test_bad_final_quote_never_sends(self):
        import scalper_agent as live
        m15, m5, frames, now = fixture()
        p = select_crt(watch_crt(m15, m5, frames, 103., 103., now))
        agent = live.ScalperAgent.__new__(live.ScalperAgent)
        with patch.object(live.mt5, "symbol_info_tick", return_value=NS(bid=109., ask=110.)), \
             patch.object(live.mt5, "symbol_info", return_value=NS(point=.001)), \
             patch.object(live.mt5, "order_send") as send, patch.object(live.logger, "warning"):
            self.assertIsNone(agent._execute_trade("XAUUSD", SATrigger(direction="BULLISH", htf_crt=p), .01))
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
