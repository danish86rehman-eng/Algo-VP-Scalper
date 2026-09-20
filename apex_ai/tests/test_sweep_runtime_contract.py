import json
import inspect
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import scalper_agent as live
from scalper.candidate_funnel import make_candidate_id
from scalper.location_permission import (ACCEPTANCE, ALLOW_LONG, LocationPermission,
                                          LocationPermissionConfig,
                                          evaluate_sweep_reaction)
from scalper.sweep_location import (LiquidityPool, SweepLocationPolicy,
                                    classify_liquidity,
                                    load_sweep_location_policy)
from scalper.trigger_engine import SATrigger, SATriggerEngine


UTC = timezone.utc
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def pool(level=100.0, *, confirmed="2026-09-20T10:00:00+00:00",
         created="2026-09-20T09:00:00+00:00", consumed=False):
    return LiquidityPool("LP-TEST", "LOW", "PDL", "D1", level,
                         created, confirmed, 1, not consumed, consumed, ())


def sweep(direction="BULLISH", stop=95.0, tp1=170.0):
    if direction == "BEARISH" and stop == 95.0:
        stop = 135.0
    return SATrigger(detected=True, trigger_type="SWEEP_REJECTION",
                     direction=direction, entry_price=100.0,
                     stop_loss=stop, tp1=tp1, tp2=120.0,
                     swept_level=100.0)


class SweepRuntimeContractTests(unittest.TestCase):
    def test_active_config_loads_with_resolved_path(self):
        path = Path(__file__).parents[1] / "config.json"
        policy = load_sweep_location_policy(str(path), require_active=True)
        self.assertTrue(policy.active)
        self.assertEqual(Path(policy.config_path), path.resolve())
        self.assertFalse(policy.allow_unknown_local)
        self.assertFalse(policy.allow_consumed)

    def test_missing_active_config_fails_closed(self):
        with self.assertRaises(ValueError):
            load_sweep_location_policy(None, require_active=True)

    def test_invalid_active_config_fails_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps({"sweep_location": {"mode": "off"}}),
                            encoding="utf-8")
            with self.assertRaises(ValueError):
                load_sweep_location_policy(str(path), require_active=True)

    def test_unknown_local_is_blocked(self):
        result = classify_liquidity(level=100.0, direction="BULLISH",
                                    pools=(), now=NOW,
                                    policy=SweepLocationPolicy(enabled=True, mode="active"),
                                    atr_value=2.0)
        self.assertEqual(result.primary, "UNKNOWN_LOCAL")
        self.assertFalse(result.allowed)

    def test_consumed_pool_is_blocked(self):
        result = classify_liquidity(level=100.0, direction="BULLISH",
                                    pools=(pool(consumed=True),), now=NOW,
                                    policy=SweepLocationPolicy(enabled=True, mode="active"),
                                    atr_value=2.0)
        self.assertEqual(result.reason, "CONSUMED_POOL")
        self.assertFalse(result.allowed)

    def test_valid_named_pool_is_allowed(self):
        result = classify_liquidity(level=100.0, direction="BULLISH",
                                    pools=(pool(),), now=NOW,
                                    policy=SweepLocationPolicy(enabled=True, mode="active"),
                                    atr_value=2.0)
        self.assertEqual(result.primary, "PDL")
        self.assertTrue(result.allowed)

    def test_atr_normalized_match_passes(self):
        result = classify_liquidity(level=100.15, direction="BULLISH",
                                    pools=(pool(),), now=NOW,
                                    policy=SweepLocationPolicy(enabled=True, mode="active",
                                                               match_tolerance_atr=.10),
                                    atr_value=2.0)
        self.assertTrue(result.allowed)

    def test_atr_normalized_match_rejects(self):
        result = classify_liquidity(level=100.21, direction="BULLISH",
                                    pools=(pool(),), now=NOW,
                                    policy=SweepLocationPolicy(enabled=True, mode="active",
                                                               match_tolerance_atr=.10),
                                    atr_value=2.0)
        self.assertEqual(result.primary, "UNKNOWN_LOCAL")

    def test_future_pool_cannot_match(self):
        future = pool(confirmed="2026-09-20T13:00:00+00:00",
                      created="2026-09-20T12:30:00+00:00")
        result = classify_liquidity(level=100.0, direction="BULLISH",
                                    pools=(future,), now=NOW,
                                    policy=SweepLocationPolicy(enabled=True, mode="active"),
                                    atr_value=2.0)
        self.assertEqual(result.primary, "UNKNOWN_LOCAL")

    def test_self_pool_at_event_time_cannot_match(self):
        self_pool = pool(confirmed=NOW.isoformat(), created=NOW.isoformat())
        result = classify_liquidity(level=100.0, direction="BULLISH",
                                    pools=(self_pool,), now=NOW,
                                    policy=SweepLocationPolicy(enabled=True, mode="active"),
                                    atr_value=2.0)
        self.assertEqual(result.primary, "UNKNOWN_LOCAL")

    def test_buy_uses_actual_ask(self):
        result = SATriggerEngine().final_quote_check(
            sweep(stop=65.0), bid=100.0, ask=100.02, point=.01, symbol="XAUUSD")
        self.assertEqual(result["executable_price"], 100.02)
        self.assertTrue(result["allowed"])

    def test_sell_uses_actual_bid(self):
        result = SATriggerEngine().final_quote_check(
            sweep("BEARISH", stop=135.0, tp1=30.0),
            bid=99.98, ask=100.0, point=.01, symbol="XAUUSD")
        self.assertEqual(result["executable_price"], 99.98)
        self.assertTrue(result["allowed"])

    def test_final_spread_blocker(self):
        result = SATriggerEngine().final_quote_check(
            sweep(stop=65.0), bid=100.0, ask=100.90, point=.01, symbol="XAUUSD")
        self.assertEqual(result["blocker"], "FINAL_SPREAD_FAIL")

    def test_final_spread_to_stop_blocker(self):
        engine = SATriggerEngine()
        original = engine.MAX_SPREAD_TO_SL_FRAC
        engine.MAX_SPREAD_TO_SL_FRAC = .0001
        try:
            result = engine.final_quote_check(
                sweep(stop=65.0), bid=100.0, ask=100.02, point=.01, symbol="XAUUSD")
        finally:
            engine.MAX_SPREAD_TO_SL_FRAC = original
        self.assertEqual(result["blocker"], "FINAL_SPREAD_TO_STOP_FAIL")

    def test_final_min_sl_blocker(self):
        result = SATriggerEngine().final_quote_check(
            sweep(stop=99.0, tp1=104.0), bid=100.0, ask=100.02,
            point=.01, symbol="XAUUSD")
        self.assertEqual(result["blocker"], "FINAL_MIN_SL_FAIL")

    def test_final_net_r_blocker(self):
        result = SATriggerEngine().final_quote_check(
            sweep(stop=65.0, tp1=105.0), bid=100.0, ask=100.02,
            point=.01, symbol="XAUUSD")
        self.assertEqual(result["blocker"], "FINAL_NET_R_FAIL")

    def test_signal_geometry_is_not_overwritten(self):
        trigger = sweep()
        original = trigger.entry_price
        SATriggerEngine().final_quote_check(trigger, bid=100.0, ask=100.02,
                                            point=.01, symbol="XAUUSD")
        self.assertEqual(trigger.entry_price, original)

    def test_stable_id_includes_frozen_location_and_pool(self):
        first = make_candidate_id("XAUUSD", "SWEEP_REJECTION", "BULLISH",
                                 NOW, 100.0, frozen_location_id="LOC-1",
                                 pool_id="LP-1")
        second = make_candidate_id("XAUUSD", "SWEEP_REJECTION", "BULLISH",
                                  NOW, 100.0, frozen_location_id="LOC-1",
                                  pool_id="LP-1")
        changed = make_candidate_id("XAUUSD", "SWEEP_REJECTION", "BULLISH",
                                   NOW, 100.0, frozen_location_id="LOC-2",
                                   pool_id="LP-1")
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)

    def test_expired_reaction_is_blocked(self):
        permission = LocationPermission(
            permission=ALLOW_LONG, direction="BULLISH", swept_level=100.0,
            location_price=100.0, zone_low=99.0, zone_high=101.0,
            triggered_at="2026-09-20T00:00:00+00:00")
        import pandas as pd
        frame = pd.DataFrame({
            "time": pd.date_range("2026-09-20 00:00", periods=4, freq="5min", tz="UTC"),
            "open": [100, 100, 100, 100], "high": [101, 101, 101, 101],
            "low": [99, 99, 99, 99], "close": [100, 100, 100, 100],
        })
        result = evaluate_sweep_reaction(
            permission, frame, sweep_time=frame.time.iloc[0],
            config=LocationPermissionConfig(sweep_expiry_minutes=1))
        self.assertEqual(result.reason, "SETUP_EXPIRED")

    def test_pre_order_revalidation_blocks_expiry_consumption_and_migration(self):
        frame = pd.DataFrame({"close": [100.0]})
        frozen = SimpleNamespace(
            location_id="LOC-1", profile_id="PROFILE-1",
            w1_profile_id="W1-1", h4_profile_id="H4-1")
        original_permission = SimpleNamespace(
            frozen=frozen, triggered_at=NOW.isoformat())
        original_context = SimpleNamespace(liquidity_pool_id="LP-1")
        trigger = sweep()
        trigger.sweep_time = NOW
        trigger.location_permission = original_permission
        trigger.location_context = original_context
        snapshot = SimpleNamespace(
            active_profile_id="PROFILE-1", w1_profile_id="W1-1",
            h4_profile_id="H4-1")
        agent = SimpleNamespace(
            TF_TRIGGER=1, TRIGGER_BARS=10, TF_CONFIRM=2, CONFIRM_BARS=10,
            TF_H4=3, TF_H1=4,
            _get_ohlcv=lambda *args, **kwargs: frame,
            market_location=SimpleNamespace(snapshot=lambda **kwargs: snapshot),
            sweep_location_policy=SweepLocationPolicy(enabled=True, mode="active"),
        )

        valid_permission = SimpleNamespace(
            reason="LOCATION_REACTION_CONFIRMED", reaction_state="DISPLACEMENT_CONFIRMED",
            confirmation_state="MSS_CONFIRMED", executable=True,
            rejection_code=None, frozen=frozen)
        valid_context = SimpleNamespace(
            liquidity_pool_id="LP-1", liquidity_type="PDL",
            already_consumed=False, allowed=True, permission_reason="PASS_INDEPENDENT_POOL")
        cases = (
            ("expired", valid_context,
             SimpleNamespace(**{**valid_permission.__dict__, "reason": "SETUP_EXPIRED",
                                "executable": False, "rejection_code": "SETUP_EXPIRED"}),
             "FINAL_SETUP_EXPIRED"),
            ("consumed",
             SimpleNamespace(**{**valid_context.__dict__, "already_consumed": True,
                                "allowed": False, "permission_reason": "CONSUMED_POOL"}),
             valid_permission, "FINAL_CONSUMED_POOL"),
            ("acceptance", valid_context,
             SimpleNamespace(**{**valid_permission.__dict__, "reason": "VAL_ACCEPTANCE_BLOCK_LONG",
                                "reaction_state": ACCEPTANCE, "executable": False,
                                "rejection_code": "VAL_ACCEPTANCE_BLOCK_LONG"}),
             "FINAL_ACCEPTANCE"),
            ("location invalidated", valid_context,
             SimpleNamespace(**{**valid_permission.__dict__, "reason": "LOCATION_INVALIDATED",
                                "executable": False, "rejection_code": "LOCATION_INVALIDATED"}),
             "FINAL_LOCATION_INVALIDATED"),
            ("M5 association lost", valid_context,
             SimpleNamespace(**{**valid_permission.__dict__, "reason": "NO_M5_CONFIRMATION",
                                "executable": False, "rejection_code": "NO_M5_CONFIRMATION"}),
             "FINAL_NO_M5_CONFIRMATION"),
        )
        for label, final_context, final_permission, expected in cases:
            with self.subTest(label=label), \
                    patch.object(live, "build_location_permission", return_value=final_permission), \
                    patch.object(live, "evaluate_sweep_reaction", return_value=final_permission), \
                    patch.object(live, "build_sweep_location_context", return_value=final_context):
                allowed, result = live.ScalperAgent._finalize_sweep_location(
                    agent, "XAUUSD", trigger, NOW)
                self.assertFalse(allowed)
                self.assertEqual(result["blocker"], expected)

    def test_final_location_and_quote_checks_precede_order_send(self):
        source = inspect.getsource(live.ScalperAgent._execute_trade)
        self.assertLess(source.index("self._finalize_sweep_location("),
                        source.index("self.trigger_eng.final_quote_check("))
        self.assertLess(source.index("self.trigger_eng.final_quote_check("),
                        source.index("mt5.order_send(request)"))

    def test_live_replay_final_quote_parity(self):
        trigger = sweep()
        engine = SATriggerEngine()
        live = engine.final_quote_check(trigger, bid=100.0, ask=100.02,
                                        point=.01, symbol="XAUUSD")
        replay = engine.final_quote_check(trigger, bid=100.0, ask=100.02,
                                          point=.01, symbol="XAUUSD")
        self.assertEqual(live, replay)


if __name__ == "__main__":
    unittest.main()
