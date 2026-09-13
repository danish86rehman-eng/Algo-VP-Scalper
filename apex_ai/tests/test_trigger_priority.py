"""Operator-approved Sweep-first precedence, shared by live and replay."""
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch
import inspect
import unittest

import pandas as pd
import backtest_scalper as sim
import scalper_agent as live
from scalper import decision_params as DP
from scalper.trigger_engine import MicroLiquidity, SATrigger, SATriggerEngine


class TriggerPriorityTests(unittest.TestCase):
    ORDER = ("SWEEP_REJECTION", "HTF_CRT_SWEEP", "FVG_FILL", "BOS_RETEST", "JUDAS")

    def test_priority_and_fallback_both_directions(self):
        for direction in ("BULLISH", "BEARISH"):
            plan = SimpleNamespace(allow=True, direction=direction, entry=100.,
                stop=90. if direction == "BULLISH" else 110.,
                target=120. if direction == "BULLISH" else 80.,
                target_level=120., swept_level=95., timeframe="D1", fvg_low=99., fvg_high=101.)
            for offset in range(len(self.ORDER)):
                order = self.ORDER[offset:]
                with self.subTest(direction=direction, expected=order[0]), ExitStack() as stack:
                    engine = SATriggerEngine(enabled_triggers=reversed(order))
                    for name, method in (("SWEEP_REJECTION", "_check_sweep_rejection"),
                                         ("BOS_RETEST", "_check_bos_retest"),
                                         ("JUDAS", "_check_judas_swing")):
                        stack.enter_context(patch.object(engine, method, return_value=SATrigger(
                            detected=True, trigger_type=name, direction=direction)))
                    vp = stack.enter_context(patch.object(engine, "_check_vp_liquidity_reaction"))
                    va = stack.enter_context(patch.object(engine, "_check_value_area_fade"))
                    legacy = stack.enter_context(patch.object(engine, "_check_fvg_fill"))
                    got = engine.step2_trigger(pd.DataFrame(), pd.DataFrame(), MicroLiquidity(),
                        "XAUUSD", session_open_price=100., htf_crt=plan, m15_fvg_entry=plan)
                    self.assertEqual(got.trigger_type, order[0])
                    self.assertEqual(got.direction, direction)
                    self.assertEqual(got.matched_triggers, list(order))
                    vp.assert_not_called()
                    va.assert_not_called()
                    legacy.assert_not_called()

    def test_shared_engine_and_disabled_volume_profile_defaults(self):
        self.assertIs(live.SATriggerEngine, SATriggerEngine)
        self.assertIs(sim.SATriggerEngine, SATriggerEngine)
        self.assertFalse(DP.VPLR_ENABLED)
        self.assertFalse(DP.VA_FADE_ENABLED)
        for fn in (live.ScalperAgent.__init__, sim.run_backtest):
            params = inspect.signature(fn).parameters
            self.assertFalse(params["vplr_enabled"].default)
            self.assertFalse(params["va_fade_enabled"].default)
        self.assertEqual(tuple(t for t in SATriggerEngine.ALL_TRIGGERS
                               if t not in {"VP_LIQUIDITY_REACTION", "VALUE_AREA_FADE", "SESSION_SWEEP"}), self.ORDER)
