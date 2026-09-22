import json
import inspect
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scalper.regime_evidence import (
    CandidateEvidenceSnapshot, RegimeBarSnapshot, RegimeEvidenceRecorder, chronological_folds,
    integrity_report, summarize_admission, summarize_regimes)


def snap(bar, regime="MANIPULATION", trigger="SWEEP_REJECTION", gate="PASS"):
    return CandidateEvidenceSnapshot(
        timestamp_utc=f"2026-09-17T08:{bar:02d}:30+00:00",
        symbol="XAUUSD", decision_timeframe="M15",
        completed_bar_timestamp_utc=f"2026-09-17T08:{bar:02d}:00+00:00",
        candidate_id=f"c{bar}{trigger}", direction="BEARISH",
        trigger_type=trigger, ict_context=regime, context_score=.8,
        entry_price=4300.0, stop_loss=4310.0, tp1=4280.0, tp2=4270.0,
        atr_ratio=1.0, displacement_momentum=.5,
        structure_trend="BEARISH", manipulation_detected=True,
        manipulation_confidence=.8, liquidity_state="BSL",
        statistical_context="RANGING", autocorrelation=.1,
        efficiency_ratio=.2, volatility_ratio=1.0,
        previous_regime="UNKNOWN", bars_in_regime=1, transition=False,
        allowed_regimes=("MANIPULATION",), regime_gate=gate,
        veto_reason="" if gate == "PASS" else "TRANSITION unsuitable",
        stb_state="BEARISH", stb_confidence="HIGH",
        displacement_gate="PASS", session_state="LONDON_NY",
        source_bar_m5_utc=f"2026-09-17T08:{bar:02d}:00+00:00",
        source_bar_m15_utc=f"2026-09-17T08:{bar:02d}:00+00:00",
        source_bar_h1_utc="2026-09-17T08:00:00+00:00", source_bar_h4_utc=None)


def bar_snap(bar, regime="MANIPULATION"):
    return RegimeBarSnapshot(
        timestamp_utc=f"2026-09-17T08:{bar:02d}:30+00:00", symbol="XAUUSD",
        decision_timeframe="M15",
        completed_bar_timestamp_utc=f"2026-09-17T08:{bar:02d}:00+00:00",
        ict_context=regime, context_score=float("nan"), atr_ratio=float("inf"),
        displacement_momentum=.5, structure_trend="BEARISH",
        manipulation_detected=True, manipulation_confidence=.8,
        liquidity_state="BSL", statistical_context="RANGING",
        autocorrelation=.1, efficiency_ratio=.2, volatility_ratio=1.0,
        source_bar_m5_utc=f"2026-09-17T08:{bar:02d}:00+00:00",
        source_bar_m15_utc=f"2026-09-17T08:{bar:02d}:00+00:00",
        source_bar_h1_utc="2026-09-17T08:00:00+00:00", source_bar_h4_utc=None)


class TestRegimeEvidence(unittest.TestCase):
    def test_recorder_is_causal_and_dwell_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.jsonl"
            recorder = RegimeEvidenceRecorder(path)
            recorder.record_bar(bar_snap(0))
            recorder.record_bar(bar_snap(15))
            recorder.record_bar(bar_snap(30, "TRANSITION"))
            rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertTrue(all(row["completed_bars_only"] for row in rows))
        self.assertIsNone(rows[0]["context_score"])
        self.assertIsNone(rows[0]["atr_ratio"])
        report = summarize_regimes(rows)
        self.assertEqual(report["counts"], {"MANIPULATION": 2, "TRANSITION": 1})
        self.assertEqual(report["transitions"], {"MANIPULATION->TRANSITION": 1})

    def test_same_bar_candidates_are_retained_for_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.jsonl"
            recorder = RegimeEvidenceRecorder(path)
            recorder.record_bar(bar_snap(0))
            recorder.record(snap(0, "MANIPULATION", "SWEEP_REJECTION", "PASS"))
            recorder.record(snap(0, "MANIPULATION", "BOS_RETEST", "REJECT"))
            rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(len(rows), 3)
        matrix = summarize_admission(rows)
        self.assertEqual(matrix["MANIPULATION x SWEEP_REJECTION"]["passes"], 1)
        self.assertEqual(matrix["MANIPULATION x BOS_RETEST"]["rejects"], 1)

    def test_snapshot_cannot_claim_forming_bar(self):
        self.assertTrue(snap(0).record()["completed_bars_only"])
        self.assertIsNone(snap(0).source_bar_h4_utc)

    def test_observation_is_not_a_decision_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = RegimeEvidenceRecorder(Path(directory) / "evidence.jsonl")
            self.assertIsNone(recorder.record(snap(0)))
        source = inspect.getsource(RegimeEvidenceRecorder.record)
        self.assertNotIn("veto", source)

    def test_candidates_do_not_inflate_market_bar_counts_or_dwell(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.jsonl"
            recorder = RegimeEvidenceRecorder(path)
            recorder.record_bar(bar_snap(0))
            for trigger in ("SWEEP_REJECTION", "BOS_RETEST", "JUDAS"):
                recorder.record(snap(0, trigger=trigger))
            rows = [json.loads(line) for line in path.read_text().splitlines()]
        report = summarize_regimes(rows)
        self.assertEqual(report["observations"], 1)

    def test_ten_consecutive_bars_have_authoritative_dwell_ten(self):
        rows = [bar_snap(i).record() for i in range(10)]
        report = summarize_regimes(rows)
        self.assertEqual(report["dwell"]["MANIPULATION"]["median"], 10)
        self.assertEqual(report["dwell"]["MANIPULATION"]["mean"], 10)

    def test_offline_replay_is_restart_independent(self):
        rows = [bar_snap(i).record() for i in range(5)]
        rows += [bar_snap(i, "TRANSITION").record() for i in range(5, 10)]
        first = summarize_regimes(rows)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            replayed = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(first, summarize_regimes(replayed))

    def test_conflicting_duplicate_bar_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.jsonl"
            recorder = RegimeEvidenceRecorder(path)
            recorder.record_bar(bar_snap(0, "MANIPULATION"))
            recorder.record_bar(bar_snap(0, "TRANSITION"))
            rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(rows[-1]["record_type"], "REGIME_SNAPSHOT_CONFLICT")

    def test_folds_use_unique_timestamps(self):
        rows = [bar_snap(i).record() for i in range(10)]
        rows += [snap(0).record(), snap(0, trigger="JUDAS").record()]
        folds = chronological_folds(rows)
        sets = [{r["completed_bar_timestamp_utc"] for r in p} for p in folds.values()]
        self.assertEqual(sum(len(sets[i] & sets[j]) for i in range(3) for j in range(i + 1, 3)), 0)

    def test_integrity_detects_orphan_and_regime_mismatch(self):
        row = snap(0).record()
        result = integrity_report([row])
        self.assertEqual(result["orphan_candidate_records"], 1)
        result = integrity_report([bar_snap(0).record(), snap(0, "TRANSITION").record()])
        self.assertEqual(result["candidate_bar_regime_mismatches"], 1)


if __name__ == "__main__":
    unittest.main()
