"""
Rejection-telemetry tests.

The point of this file is CLAUDE.md 13.9: a run's LOT_FLOOR count must be a
readable number, not a grep of a rotating log, or comparisons across risk
levels silently compare two different samples.
"""
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scalper.reject_log import RejectionLog, STAGES, MAX_REASON_SAMPLES


DAY1 = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)
DAY2 = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


class TestRejectionLog(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "rej.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _log(self, **kw):
        return RejectionLog(str(self.path), **kw)

    def test_counts_accumulate_per_stage(self):
        log = self._log()
        for _ in range(3):
            log.record("LOT_FLOOR", "XAUUSD", now=DAY1)
        log.record("STB", "XAUUSD", now=DAY1)
        counts = log.counts_for("2026-08-24", "XAUUSD")
        self.assertEqual(counts["LOT_FLOOR"], 3)
        self.assertEqual(counts["STB"], 1)

    def test_symbols_are_counted_separately_and_summed(self):
        log = self._log()
        log.record("CRG", "XAUUSD", now=DAY1)
        log.record("CRG", "USOIL", now=DAY1)
        log.record("CRG", "USOIL", now=DAY1)
        self.assertEqual(log.counts_for("2026-08-24", "USOIL")["CRG"], 2)
        self.assertEqual(log.counts_for("2026-08-24")["CRG"], 3)

    def test_unknown_stage_is_bucketed_not_dropped(self):
        log = self._log()
        log.record("NOT_A_REAL_GATE", "XAUUSD", now=DAY1)
        self.assertEqual(log.counts_for("2026-08-24")["UNKNOWN"], 1)

    def test_days_are_separate_buckets(self):
        log = self._log()
        log.record("LOT_FLOOR", "XAUUSD", now=DAY1)
        log.record("LOT_FLOOR", "XAUUSD", now=DAY2)
        self.assertEqual(log.counts_for("2026-08-24")["LOT_FLOOR"], 1)
        self.assertEqual(log.counts_for("2026-08-25")["LOT_FLOOR"], 1)
        self.assertEqual(log.totals()["LOT_FLOOR"], 2)

    def test_date_roll_forces_a_write(self):
        # A day's totals must survive a restart even if the batch was short of
        # the flush threshold when midnight passed.
        log = self._log(flush_every=1000)
        log.record("STB", "XAUUSD", now=DAY1)
        log.record("STB", "XAUUSD", now=DAY2)   # roll seals day 1
        reloaded = self._log()
        self.assertEqual(reloaded.counts_for("2026-08-24")["STB"], 1)

    def test_batching_defers_writes_until_the_threshold(self):
        log = self._log(flush_every=3)
        log.record("CRG", "XAUUSD", now=DAY1)
        log.record("CRG", "XAUUSD", now=DAY1)
        self.assertEqual(self._log().counts_for("2026-08-24"), {})
        log.record("CRG", "XAUUSD", now=DAY1)   # third trips the flush
        self.assertEqual(self._log().counts_for("2026-08-24")["CRG"], 3)

    def test_explicit_flush_persists(self):
        log = self._log(flush_every=1000)
        log.record("THIN_LIQ", "XAGUSD", now=DAY1)
        log.flush()
        self.assertEqual(
            self._log().counts_for("2026-08-24", "XAGUSD")["THIN_LIQ"], 1)

    def test_reason_samples_are_bounded(self):
        log = self._log()
        for i in range(MAX_REASON_SAMPLES + 5):
            log.record("STEP3_VALIDATE", "XAUUSD", reason=f"reason {i}",
                       now=DAY1)
        log.flush()
        data = self._log()._data
        samples = data["2026-08-24"]["XAUUSD"]["reasons"]["STEP3_VALIDATE"]
        self.assertEqual(len(samples), MAX_REASON_SAMPLES)

    def test_duplicate_reasons_are_not_stored_twice(self):
        log = self._log()
        for _ in range(4):
            log.record("CRG", "XAUUSD", reason="daily loss limit", now=DAY1)
        log.flush()
        samples = self._log()._data["2026-08-24"]["XAUUSD"]["reasons"]["CRG"]
        self.assertEqual(samples, ["daily loss limit"])
        # ...but the count still reflects every occurrence.
        self.assertEqual(self._log().counts_for("2026-08-24")["CRG"], 4)

    def test_summary_line_ranks_the_busiest_gate_first(self):
        log = self._log()
        log.record("STB", "XAUUSD", now=DAY1)
        for _ in range(5):
            log.record("LOT_FLOOR", "XAUUSD", now=DAY1)
        line = log.summary_line("2026-08-24")
        self.assertIn("LOT_FLOOR=5", line)
        self.assertLess(line.index("LOT_FLOOR"), line.index("STB"))

    def test_summary_line_when_nothing_recorded(self):
        self.assertIn("none recorded", self._log().summary_line("2026-01-01"))

    def test_corrupt_file_does_not_raise(self):
        self.path.write_text("{ not json", encoding="utf-8")
        log = self._log()
        log.record("CRG", "XAUUSD", now=DAY1)
        log.flush()
        self.assertEqual(log.counts_for("2026-08-24")["CRG"], 1)

    def test_every_gate_in_scan_symbol_has_a_stage(self):
        # A gate without a stage name records as UNKNOWN, which hides which
        # filter is doing the damage. Keep this list in step with the chain.
        for expected in ("SESSION_IDLE", "COOLDOWN", "DEDUP", "STB",
                         "THIN_LIQ", "GATE1_REGIME", "GATE2_DISPLACEMENT",
                         "STEP3_VALIDATE", "CRG", "LOT_FLOOR"):
            self.assertIn(expected, STAGES)


if __name__ == "__main__":
    unittest.main()
