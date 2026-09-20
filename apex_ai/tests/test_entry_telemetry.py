import inspect
import unittest
from decimal import Decimal
from types import SimpleNamespace

from scalper.execution_telemetry import (
    ENTRY_LIFECYCLE_STAGES,
    ENTRY_TERMINAL_REASONS,
    EntryLifecycleTelemetry,
    EntryTelemetryIdentityError,
    build_entry_observation,
    build_setup_identity,
)
from scalper.trigger_engine import SATrigger


T0 = "2026-09-17T08:00:00+00:00"
T1 = "2026-09-17T08:15:00+00:00"
T2 = "2026-09-17T08:30:00+00:00"


def identity_kwargs(**overrides):
    values = dict(
        symbol="XAUUSD",
        direction="BULLISH",
        location_type="BROKEN_LEVEL",
        location_identity={"level": 4324.76, "broken_at": T0},
        location_time=T0,
        reclaim_time=T1,
        fvg_formation_time=T2,
        fvg_identity={"low": 4325.10, "high": 4326.20},
        location_price=4324.76,
    )
    values.update(overrides)
    return values


class TestSetupIdentity(unittest.TestCase):
    def test_same_causal_fields_are_stable_across_scan_and_process_context(self):
        first = build_setup_identity(**identity_kwargs())
        second = build_setup_identity(**identity_kwargs())
        self.assertEqual(first.setup_id, second.setup_id)
        self.assertEqual(first.payload, second.payload)

    def test_utc_equivalent_timestamps_and_price_forms_match(self):
        first = build_setup_identity(**identity_kwargs(
            location_time="2026-09-17T08:00:00+00:00",
            reclaim_time="2026-09-17T08:15:00+00:00",
            fvg_formation_time="2026-09-17T08:30:00+00:00",
            location_price=4324.7600))
        second = build_setup_identity(**identity_kwargs(
            location_time="2026-09-17T13:00:00+05:00",
            reclaim_time="2026-09-17T13:15:00+05:00",
            fvg_formation_time="2026-09-17T13:30:00+05:00",
            location_identity={"level": Decimal("4324.760"),
                               "broken_at": "2026-09-17T13:00:00+05:00"},
            location_price=Decimal("4324.760")))
        self.assertEqual(first.setup_id, second.setup_id)

    def test_causal_identity_changes_change_id(self):
        base = build_setup_identity(**identity_kwargs()).setup_id
        for change in (
            {"direction": "BEARISH"},
            {"location_type": "M15_FVG"},
            {"location_identity": {"level": 4325.00, "broken_at": T0}},
            {"reclaim_time": "2026-09-17T08:20:00+00:00"},
            {"fvg_identity": {"low": 4325.20, "high": 4326.20}},
        ):
            with self.subTest(change=change):
                self.assertNotEqual(base,
                                    build_setup_identity(**identity_kwargs(**change)).setup_id)

    def test_missing_causal_fields_fail_closed(self):
        for field in ("location_type", "location_identity", "location_time",
                      "reclaim_time", "fvg_formation_time", "fvg_identity"):
            values = identity_kwargs(**{field: None})
            with self.subTest(field=field):
                with self.assertRaises(EntryTelemetryIdentityError):
                    build_setup_identity(**values)


class Evidence:
    def __init__(self, **values):
        self.values = values

    def record(self):
        return dict(self.values)


def trigger_fixture():
    return SATrigger(
        detected=True,
        trigger_type="SWEEP_REJECTION",
        direction="BULLISH",
        entry_price=4325.50,
        stop_loss=4315.50,
        tp1=4345.50,
        tp2=4355.50,
        confidence="HIGH",
        matched_triggers=["SWEEP_REJECTION", "BOS_RETEST"],
        reclaim=Evidence(
            level=4324.76,
            broken_at=T0,
            reclaimed_at=T1,
            fvg_formed_at=T2,
            fvg_low=4325.10,
            fvg_high=4326.20,
        ),
    )


class TestEntryObservation(unittest.TestCase):
    def test_observation_has_schema_identity_and_not_evaluated_status(self):
        trigger = trigger_fixture()
        observation = build_entry_observation(
            symbol="XAUUSD", trigger=trigger,
            legacy_decision="ALLOWED", legacy_reason=None)
        self.assertEqual(observation["schema"], "SA_ENTRY_TELEMETRY_V1")
        self.assertEqual(observation["schema_version"], 1)
        self.assertEqual(observation["staged_status"], "NOT_EVALUATED")
        self.assertTrue(observation["setup_id"].startswith("ES1_"))
        self.assertEqual(observation["selected_trigger"], "SWEEP_REJECTION")
        self.assertEqual(observation["matched_triggers"],
                         ["SWEEP_REJECTION", "BOS_RETEST"])

    def test_future_or_forming_values_do_not_change_completed_evidence(self):
        before = build_entry_observation(symbol="XAUUSD", trigger=trigger_fixture())
        changed = trigger_fixture()
        changed.reclaim.values.update({"forming_high": 9999.0,
                                       "future_close": 1.0})
        after = build_entry_observation(symbol="XAUUSD", trigger=changed)
        self.assertEqual(before["setup_id"], after["setup_id"])
        self.assertEqual(before["identity"], after["identity"])

    def test_baseline_trigger_fixture_is_not_mutated(self):
        trigger = trigger_fixture()
        before = {
            "detected": trigger.detected,
            "trigger_type": trigger.trigger_type,
            "direction": trigger.direction,
            "entry_price": trigger.entry_price,
            "stop_loss": trigger.stop_loss,
            "tp1": trigger.tp1,
            "matched_triggers": list(trigger.matched_triggers),
            "reclaim": trigger.reclaim.record(),
        }
        observation = build_entry_observation(
            symbol="XAUUSD", trigger=trigger,
            legacy_decision="ALLOWED", legacy_reason="VALID")
        self.assertEqual(before, {
            "detected": trigger.detected,
            "trigger_type": trigger.trigger_type,
            "direction": trigger.direction,
            "entry_price": trigger.entry_price,
            "stop_loss": trigger.stop_loss,
            "tp1": trigger.tp1,
            "matched_triggers": list(trigger.matched_triggers),
            "reclaim": trigger.reclaim.record(),
        })
        self.assertEqual(observation["legacy_decision"], "ALLOWED")
        self.assertEqual(observation["legacy_reason"], "VALID")
        self.assertEqual(trigger.trigger_type, "SWEEP_REJECTION")
        self.assertEqual(trigger.direction, "BULLISH")
        self.assertEqual(trigger.entry_price, 4325.50)
        self.assertEqual(trigger.stop_loss, 4315.50)
        self.assertEqual(trigger.tp1, 4345.50)

    def test_live_and_replay_import_the_same_observation_builder(self):
        import backtest_scalper
        import scalper_agent
        self.assertIs(backtest_scalper.build_entry_observation,
                      scalper_agent.EXEC_TEL.build_entry_observation)
        live = scalper_agent.EXEC_TEL.build_entry_observation(
            symbol="XAUUSD", trigger=trigger_fixture())
        replay = backtest_scalper.build_entry_observation(
            symbol="XAUUSD", trigger=trigger_fixture())
        self.assertEqual(live, replay)

    def test_telemetry_is_not_an_execution_authority(self):
        import scalper_agent
        from scalper.trigger_engine import SATriggerEngine
        self.assertNotIn("EntryLifecycleTelemetry",
                         inspect.getsource(SATriggerEngine))
        execute_source = inspect.getsource(scalper_agent.ScalperAgent._execute_trade)
        self.assertNotIn("entry_setup_telemetry", execute_source)
        self.assertNotIn("EntryLifecycleTelemetry", execute_source)
        self.assertNotIn("build_setup_identity", execute_source)


class TestEntryLifecycle(unittest.TestCase):
    def test_valid_progression_is_deterministic(self):
        lifecycle = EntryLifecycleTelemetry("ES1_fixture")
        for stage in ENTRY_LIFECYCLE_STAGES[1:]:
            lifecycle.advance(stage, "2026-09-17T08:00:00+00:00")
        self.assertEqual(lifecycle.current_stage, "CONSUMED")
        self.assertIsNone(lifecycle.terminal_reason)
        self.assertEqual(lifecycle.record()["staged_status"], "NOT_EVALUATED")

    def test_invalid_transition_and_terminal_reuse_are_rejected(self):
        lifecycle = EntryLifecycleTelemetry("ES1_fixture")
        with self.assertRaises(ValueError):
            lifecycle.advance("READY")
        lifecycle.terminate("STALE", "2026-09-17T08:00:00+00:00")
        with self.assertRaises(ValueError):
            lifecycle.advance("BREAK_OR_RAID_OBSERVED")
        with self.assertRaises(ValueError):
            lifecycle.terminate("REUSED")

    def test_consumed_setup_cannot_be_reused(self):
        lifecycle = EntryLifecycleTelemetry("ES1_fixture")
        for stage in ENTRY_LIFECYCLE_STAGES[1:]:
            lifecycle.advance(stage)
        with self.assertRaises(ValueError):
            lifecycle.advance("READY")
        with self.assertRaises(ValueError):
            lifecycle.terminate("REUSED")


if __name__ == "__main__":
    unittest.main()
