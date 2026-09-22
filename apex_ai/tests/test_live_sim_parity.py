"""
Live/simulator parity.

Invariant #2 says a backtest must run the live decision path. It was previously
enforced by comments asking each editor to keep two copies of every constant in
step, and four of them had drifted:

  * the consultation timeframe - live classified regime on M5, the simulator on
    M15, so Gate 1's whitelist was being applied to different classifications;
  * the trigger window - live 150 bars, simulator the entire history to date;
  * the confirmation window - live 150 bars, simulator 200;
  * the forming bar - live read it, the simulator could not.

These tests fail if any of them comes back. They deliberately assert *identity*
with `scalper.decision_params` rather than against literal numbers: the point is
that exactly one definition exists, not that it currently holds some value.
"""
from __future__ import annotations

import inspect
import unittest
from datetime import datetime, timezone

import MetaTrader5 as mt5
import pandas as pd

import backtest_scalper as sim
from scalper import decision_params as DP
from scalper import sa_consultant
from scalper.sa_crg import SACRG
from scalper.vp_gate import VolumeProfileGate
from scalper_agent import ScalperAgent


class TestSharedConstants(unittest.TestCase):
    """Both processes must read each value from decision_params, not restate it."""

    def test_trigger_and_confirm_timeframes(self):
        self.assertIs(ScalperAgent.TF_TRIGGER, DP.TF_TRIGGER)
        self.assertIs(ScalperAgent.TF_CONFIRM, DP.TF_CONFIRM)

    def test_decision_windows(self):
        self.assertIs(ScalperAgent.TRIGGER_BARS, DP.TRIGGER_BARS)
        self.assertIs(ScalperAgent.CONFIRM_BARS, DP.CONFIRM_BARS)
        self.assertIs(sim.TRIGGER_BARS, DP.TRIGGER_BARS)
        self.assertIs(sim.CONFIRM_BARS, DP.CONFIRM_BARS)

    def test_vp_gate_constants_are_one_definition(self):
        """
        The VP gate is the newest gate and therefore the likeliest to drift.
        Identity, not equality: exactly one definition must exist.
        """
        for name in ("VP_PROFILE_BARS", "VP_PROFILE_FETCH_BARS",
                     "VP_TARGET_BINS", "VP_VALUE_AREA_PCT",
                     "VP_POC_BAND_FRAC", "VP_EDGE_TOLERANCE_FRAC",
                     "VP_GATE_ENABLED", "VP_GATE_MODE",
                     "VP_REGIME_BARS", "VP_REGIME_LOOKBACK",
                     "VP_REGIME_SMOOTHING", "VP_REGIME_TREND_THRESHOLD",
                     "VP_REGIME_VOL_THRESHOLD", "VP_REGIME_ER_THRESHOLD"):
            with self.subTest(constant=name):
                self.assertIs(getattr(sim, name), getattr(DP, name),
                              f"{name} must come from decision_params")

    def test_vp_gate_is_built_identically_in_both_processes(self):
        """
        Same construction arguments on both sides. A gate built with a
        different profile window or POC band in the simulator would measure a
        different strategy from the one that runs live - invariant #2.
        """
        agent = ScalperAgent.__new__(ScalperAgent)
        gate_live = VolumeProfileGate(
            mode=DP.VP_GATE_MODE, profile_bars=DP.VP_PROFILE_BARS,
            target_bins=DP.VP_TARGET_BINS, value_area_pct=DP.VP_VALUE_AREA_PCT,
            poc_band_frac=DP.VP_POC_BAND_FRAC,
            edge_tolerance_frac=DP.VP_EDGE_TOLERANCE_FRAC)
        src = inspect.getsource(ScalperAgent.__init__)
        for token in ("VP_PROFILE_BARS", "VP_TARGET_BINS",
                      "VP_VALUE_AREA_PCT", "VP_EDGE_TOLERANCE_FRAC"):
            self.assertIn(f"DP.{token}", src,
                          f"live gate must construct from DP.{token}")
        sim_src = inspect.getsource(sim.run_backtest)
        for token in ("VP_PROFILE_BARS", "VP_TARGET_BINS",
                      "VP_VALUE_AREA_PCT", "VP_EDGE_TOLERANCE_FRAC"):
            self.assertIn(token, sim_src,
                          f"simulator gate must construct from {token}")
        self.assertEqual(gate_live.mode, DP.VP_GATE_MODE)

    def test_vplr_constants_are_one_definition(self):
        """
        Same discipline as the VP gate, for the newest trigger. Identity, not
        equality — a simulator that restated any of these would be measuring a
        different trigger from the one that runs live.
        """
        for name in ("VPLR_ENABLED", "VPLR_PROFILE_FETCH_BARS",
                     "VPLR_SESSION_OVERRIDE_ENABLED",
                     "VPLR_SESSION_WINDOW_UTC"):
            with self.subTest(constant=name):
                self.assertIs(getattr(sim, name), getattr(DP, name),
                              f"{name} must come from decision_params")

    def test_vplr_params_are_built_from_decision_params_in_both_paths(self):
        """
        Both processes must construct the parameter bundle the same way, and
        every field must trace back to a DP constant rather than a literal.
        """
        from scalper.vp_liquidity_trigger import VPLRParams
        live_src = inspect.getsource(ScalperAgent.__init__)
        sim_src = inspect.getsource(sim.run_backtest)
        self.assertIn("VPLRParams.from_decision_params", live_src)
        self.assertIn("VPLRParams.from_decision_params", sim_src)

        built = VPLRParams.from_decision_params(DP)
        for field, value in built.__dict__.items():
            with self.subTest(field=field):
                self.assertTrue(
                    any(value is getattr(DP, n) or value == getattr(DP, n)
                        for n in dir(DP) if n.startswith("VPLR_")),
                    f"{field} does not trace to a VPLR_* constant")

    def test_vplr_is_mirrored_into_the_simulator(self):
        """Both decision paths must evaluate the trigger, or neither."""
        live_src = inspect.getsource(ScalperAgent._scan_symbol)
        sim_src = inspect.getsource(sim.run_backtest)
        for token in ("vplr_ctx", "vplr_context"):
            self.assertIn(token, live_src, f"live path missing {token}")
            self.assertIn(token, sim_src, f"simulator missing {token}")

    def test_vplr_session_override_is_mirrored(self):
        """
        The VP-only Asia allowance must exist on both sides. Live expresses it
        as SAState.VP_ONLY; the simulator as `vp_only_bar`. Both must consult
        `vp_window_open`, or one process would trade hours the other does not.
        """
        sim_src = inspect.getsource(sim.run_backtest)
        loop_src = inspect.getsource(ScalperAgent.run)
        self.assertIn("vp_window_open", sim_src)
        self.assertIn("vp_window_open", loop_src)
        self.assertIn("VP_ONLY", loop_src)
        self.assertIn("VP_LIQUIDITY_REACTION", sim_src)

    def test_vplr_h4_and_d1_frames_use_closed_tf_in_the_simulator(self):
        """
        L-007. Every H4/D1 frame this trigger touches must be sliced with
        `_closed_tf`, which requires the bar to have genuinely closed — not
        `_closed`, which only checks the opening stamp and would hand over a
        forming H4 bar for the anchored profile.
        """
        sim_src = inspect.getsource(sim.run_backtest)
        anchor_call = sim_src.split("vplr_context(")[1].split(")")[0]
        self.assertIn("_closed_tf", anchor_call,
                      "anchored H4 frame must use _closed_tf")
        self.assertIn("240", anchor_call, "H4 slice must pass tf_minutes=240")

    def test_disabled_trigger_leaves_the_legacy_trigger_set_untouched(self):
        """
        The baseline-reproduction guarantee, asserted rather than inspected:
        with the switch off the enabled set is exactly the five that existed
        before, and the VP allowance cannot open.
        """
        from scalper.trigger_engine import resolve_enabled_triggers
        self.assertEqual(
            set(resolve_enabled_triggers(None, False, crt_enabled=False)),
        {"SWEEP_REJECTION", "FVG_FILL", "BOS_RETEST", "JUDAS",
             "VALUE_AREA_FADE"})
        self.assertTrue(DP.VPLR_ENABLED)

    def test_vp_gate_is_mirrored_into_the_simulator(self):
        """Both decision paths must contain the gate, or neither."""
        live_src = inspect.getsource(ScalperAgent._scan_symbol)
        sim_src = inspect.getsource(sim.run_backtest)
        self.assertIn("vp_gate", live_src)
        self.assertIn("vp_gate", sim_src)
        self.assertIn("VP_GATE", live_src)
        self.assertIn("VP_GATE", sim_src)

    def test_profile_frame_excludes_the_forming_bar(self):
        """
        The simulator must slice the H4 profile frame with the duration-aware
        helper. `_closed` compares a bar's OPENING stamp against now, which on
        H4 hands over a bar with up to four hours still to run - and a profile
        built over a forming bar repaints.
        """
        sim_src = inspect.getsource(sim.run_backtest)
        vp_block = sim_src[sim_src.index("if vp_gate_enabled:"):]
        vp_block = vp_block[:vp_block.index("continue")]
        self.assertIn("_closed_tf(", vp_block)
        self.assertNotIn("_closed(frames", vp_block)

    def test_closed_tf_waits_for_the_bar_to_finish(self):
        idx = pd.to_datetime(
            ["2026-08-25T08:00Z", "2026-08-25T12:00Z", "2026-08-25T16:00Z"])
        df = pd.DataFrame({"time": idx, "close": [1.0, 2.0, 3.0]})
        now = datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc)
        got = sim._closed_tf(df, now, bars=10, tf_minutes=240)
        # 16:00 covers [16:00, 20:00) and has NOT closed at 18:00.
        self.assertEqual(len(got), 2)
        self.assertEqual(got["time"].iloc[-1], pd.Timestamp("2026-08-25T12:00Z"))

    def test_regime_whitelist_is_one_object(self):
        self.assertIs(ScalperAgent.TRIGGER_REGIME_WHITELIST,
                      DP.TRIGGER_REGIME_WHITELIST)
        self.assertIs(sim.TRIGGER_REGIME_WHITELIST,
                      DP.TRIGGER_REGIME_WHITELIST)

    def test_thin_liquidity_windows(self):
        self.assertIs(ScalperAgent.THIN_LIQUIDITY_HOURS_UTC,
                      DP.THIN_LIQUIDITY_HOURS_UTC)
        self.assertIs(sim.THIN_LIQUIDITY_HOURS_UTC,
                      DP.THIN_LIQUIDITY_HOURS_UTC)
        self.assertIs(ScalperAgent.THIN_LIQ_REQUIRED_CONFIDENCE,
                      DP.THIN_LIQ_REQUIRED_CONFIDENCE)
        self.assertIs(sim.THIN_LIQ_REQUIRED_CONFIDENCE,
                      DP.THIN_LIQ_REQUIRED_CONFIDENCE)

    def test_timeout_budget_is_derived_not_typed_twice(self):
        """
        The simulator walks the confirmation frame, the live agent counts
        trigger bars. One must be computed from the other; typing both is how
        they diverge.
        """
        self.assertIs(sim.TIMEOUT_CONFIRM_BARS, DP.TIMEOUT_CONFIRM_BARS)
        self.assertEqual(
            DP.TIMEOUT_CONFIRM_BARS,
            ScalperAgent.TIMEOUT_BARS * DP.TRIGGER_TF_MINUTES // DP.CONFIRM_TF_MINUTES,
        )

    def test_position_cap_matches_the_risk_governor(self):
        """
        SACRG carries its own ceiling. If MAX_OPEN_POSITIONS were raised past
        it, the simulator would model concurrency the live governor forbids.
        """
        self.assertIn("sa_open_positions < 2", inspect.getsource(SACRG))
        self.assertEqual(DP.MAX_OPEN_POSITIONS, 2)


class TestSharedConsultation(unittest.TestCase):
    """Gate 1 and Gate 2 must be one implementation, not two."""

    def test_simulator_imports_the_live_analysis(self):
        self.assertIs(sim.consult_analyse, sa_consultant.analyse)

    def test_simulator_imports_the_live_tp2_override(self):
        self.assertIs(sim.apply_lia_override, sa_consultant.apply_lia_override)

    def test_no_reimplemented_regime_classification_remains(self):
        """
        The old duplicate called regime_engine.analyze directly and carried its
        own copy of the whitelist literal. Both are gone; the gate function now
        delegates.
        """
        src = inspect.getsource(sim._apply_v1_council_gates)
        self.assertIn("consult_analyse", src)
        self.assertNotIn("regime_engine.analyze", src)
        self.assertNotIn('"SWEEP_REJECTION":', src)

    def test_consultation_runs_on_the_trigger_frame(self):
        """
        The live consultant hard-coded TIMEFRAME_M5 after the stack moved to
        M15/M5, which is what made its regime disagree with the simulator's.
        """
        src = inspect.getsource(sa_consultant.SAConsultant._run_consultation)
        self.assertIn("DP.TF_TRIGGER", src)
        self.assertNotIn("TIMEFRAME_M5", src)


class TestClosedBarsOnly(unittest.TestCase):
    """
    Every decision frame excludes the forming bar, in both processes. A frame
    that includes it cannot be reproduced: the values that permitted the entry
    keep moving until the bar closes.
    """

    def test_live_agent_reads_from_position_one(self):
        src = inspect.getsource(ScalperAgent._get_ohlcv)
        self.assertIn("start = 1 if closed_only else 0", src)
        self.assertIn("closed_only: bool = True", src)

    def test_consultant_reads_from_position_one(self):
        src = inspect.getsource(sa_consultant.SAConsultant._fetch)
        self.assertIn("copy_rates_from_pos(symbol, timeframe, 1, bars)", src)

    def test_simulator_slice_is_strictly_before_now(self):
        df = pd.DataFrame({
            "time": pd.date_range("2026-05-01", periods=10, freq="h", tz="UTC"),
            "high": range(10), "low": range(10),
        })
        now = df["time"].iloc[5].to_pydatetime()
        out = sim._closed(df, now, bars=100)
        self.assertTrue((out["time"] < pd.Timestamp(now)).all())
        self.assertEqual(len(out), 5)


class TestSpreadIsMeasuredNotAssumed(unittest.TestCase):
    def test_per_bar_spread_reaches_every_consumer(self):
        src = inspect.getsource(sim.run_backtest)
        self.assertIn("bar_spread", src)
        # The scalar must no longer reach the validator, the governor or costs.
        self.assertIn("trigger, bar_spread, symbol", src)
        self.assertIn("current_spread_pips   = bar_spread", src)
        self.assertIn("_execution_cost(symbol, volume, bar_spread", src)

    def test_points_convert_to_pips(self):
        out = sim._spread_series(pd.DataFrame({"spread": [50.0, 30.0]}), 2.5)
        self.assertEqual(list(out), [5.0, 3.0])

    def test_zero_spread_bars_fall_back_to_the_floor(self):
        """
        Some symbols report the field zeroed. Pricing those bars at zero cost
        would flatter the run, so the supplied floor is substituted.
        """
        out = sim._spread_series(pd.DataFrame({"spread": [50.0, 0.0, 30.0]}), 2.5)
        self.assertEqual(list(out), [5.0, 2.5, 3.0])


class TestEMABandParity(unittest.TestCase):
    def test_both_processes_share_the_band_settings(self):
        self.assertIs(sim.EMA_BAND_BARS, DP.EMA_BAND_BARS)
        self.assertIs(sim.EMA_BAND_TF, DP.EMA_BAND_TF)
        self.assertIs(sim.EMA_BAND_PERIOD, DP.EMA_BAND_PERIOD)

    def test_band_is_h1_and_period_18(self):
        """The operator specified 18-period EMAs on H1; pin it."""
        self.assertEqual(DP.EMA_BAND_PERIOD, 18)
        self.assertEqual(DP.EMA_BAND_TF, mt5.TIMEFRAME_H1)

    def test_seeding_window_is_long_enough_to_converge(self):
        self.assertGreaterEqual(DP.EMA_BAND_BARS, DP.EMA_BAND_PERIOD * 5)

    def test_gate_sits_before_the_bias_gate_in_both(self):
        """
        Ordering changes which counter absorbs a rejection. Both chains put the
        band immediately after detection and before the short-term-bias gate.
        """
        sim_src = inspect.getsource(sim.run_backtest)
        self.assertLess(sim_src.index("ema_filter.check"),
                        sim_src.index("stb_filter.check"))
        live_src = inspect.getsource(ScalperAgent._scan_symbol)
        self.assertLess(live_src.index("self.ema_filter.check"),
                        live_src.index("self.stb_filter.check"))


class TestWarmupCoversTheLongestLookback(unittest.TestCase):
    def test_lead_in_seeds_every_gate(self):
        """
        The lead-in must cover the slowest frame, or the opening weeks of a run
        are suppressed by insufficient-history rejections rather than measured.
        H4 bias needs H4_BARS bars; at 6 bars a trading day that is the binding
        constraint.
        """
        h4_trading_days = DP.H4_BARS / 6.0
        calendar_days = h4_trading_days * 7.0 / 5.0
        self.assertGreaterEqual(sim.WARMUP_LEAD_DAYS, calendar_days)


if __name__ == "__main__":
    unittest.main()


class TestBandDefaultAgrees(unittest.TestCase):
    """
    Both CLIs must take the band's default from decision_params. The simulator
    briefly used `--no-ema-band` as a store_true flag, which forced the band ON
    whenever the flag was absent and silently ignored EMA_BAND_ENABLED=False —
    reintroducing exactly the class of divergence this file exists to prevent.
    """

    def _default(self, parser_owner_src: str) -> None:
        self.assertIn("BooleanOptionalAction", parser_owner_src)
        self.assertNotIn('"--no-ema-band", action="store_true"', parser_owner_src)

    def test_simulator_cli_default_comes_from_decision_params(self):
        import backtest_scalper
        src = inspect.getsource(backtest_scalper)
        self._default(src)
        self.assertIn("default=EMA_BAND_ENABLED", src)

    def test_live_cli_default_comes_from_decision_params(self):
        import scalper_agent
        src = inspect.getsource(scalper_agent)
        self._default(src)
        self.assertIn("default=DP.EMA_BAND_ENABLED", src)

    def test_run_backtest_signature_default_matches(self):
        self.assertEqual(
            inspect.signature(sim.run_backtest).parameters["ema_band_enabled"].default,
            DP.EMA_BAND_ENABLED,
        )


class TestGuardianExitParity(unittest.TestCase):
    """L-003: the simulated Guardian must BE the live Guardian.

    The gap this closes was not a missing feature — it was the simulator
    modelling a static stop while the account trailed, closed early and
    extended targets. Re-opening it would not raise; it would quietly make
    every exit-side result describe a strategy nobody runs. So the coupling is
    asserted structurally rather than trusted.
    """

    def test_both_processes_import_the_same_engine_classes(self):
        """Not 'equivalent implementations' — the same objects.

        CLAUDE.md §13.4 already learned this the expensive way: four constants
        drifted while a comment asked editors to keep them in step. An `is`
        check cannot drift.
        """
        import trade_guardian_agent as tga
        from scalper import exit_manager, tga_engines

        self.assertIs(tga.SLEngine, tga_engines.SLEngine)
        self.assertIs(tga.EarlyCloseEngine, tga_engines.EarlyCloseEngine)
        self.assertIs(tga.TPEngine, tga_engines.TPEngine)
        self.assertIs(tga.TGAConfig, tga_engines.TGAConfig)

        g = exit_manager.SimulatedGuardian()
        self.assertIsInstance(g.sl_engine, tga.SLEngine)
        self.assertIsInstance(g.early_engine, tga.EarlyCloseEngine)
        self.assertIsInstance(g.tp_engine, tga.TPEngine)

    def test_guardian_thresholds_come_from_decision_params(self):
        from scalper.tga_engines import TGAConfig

        cfg = TGAConfig()
        for field, const in [
            ("breakeven_trigger_r", "TGA_BREAKEVEN_TRIGGER_R"),
            ("stage2_trigger_r", "TGA_STAGE2_TRIGGER_R"),
            ("stage3_trigger_r", "TGA_STAGE3_TRIGGER_R"),
            ("stage2_trail_atr", "TGA_STAGE2_TRAIL_ATR"),
            ("stage3_trail_atr", "TGA_STAGE3_TRAIL_ATR"),
            ("structure_lock_atr", "TGA_STRUCTURE_LOCK_ATR"),
            ("breakeven_min_close_candles", "TGA_BREAKEVEN_MIN_CLOSE_CANDLES"),
            ("early_close_min_profit_r", "TGA_EARLY_CLOSE_MIN_PROFIT_R"),
            ("tp_extension_partial_pct", "TGA_TP_EXTENSION_PARTIAL_PCT"),
            ("no_progress_minutes", "TGA_NO_PROGRESS_MINUTES"),
            ("no_progress_max_peak_r", "TGA_NO_PROGRESS_MAX_PEAK_R"),
        ]:
            self.assertEqual(getattr(cfg, field), getattr(DP, const),
                             f"{field} drifted from DP.{const}")

    def test_tga_engines_module_stays_mt5_free(self):
        """It is imported by unit tests and by the simulator; pulling MT5 or
        the Guardian's logging config in would break both."""
        import inspect as _i

        from scalper import tga_engines
        src = _i.getsource(tga_engines)
        self.assertNotIn("import MetaTrader5", src)
        self.assertNotIn("basicConfig", src)

    def test_simulator_default_comes_from_decision_params(self):
        self.assertEqual(
            inspect.signature(sim.run_backtest).parameters["tga_exits"].default,
            DP.TGA_EXITS_IN_SIM,
        )

    def test_ships_off_so_stored_baselines_stay_comparable(self):
        self.assertFalse(DP.TGA_EXITS_IN_SIM)
