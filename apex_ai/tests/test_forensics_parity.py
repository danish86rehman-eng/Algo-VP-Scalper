"""
Forensics parity and non-interference.

Two properties are pinned here, both of them load-bearing.

1. The live agent and the simulator classify a trade identically, because they
   call the SAME function rather than two copies of one idea. This is the same
   failure invariant #2 was written for: four decision values had already
   drifted while a comment asked editors to keep them in step.

2. The forensics layer cannot change what the strategy does. It observes
   closed trades and counts vetoes; it must never be able to veto anything
   itself. If a future edit gives it decision authority, that edit is a
   strategy change wearing telemetry's clothes and has to be mirrored and
   fold-tested like any other gate.
"""
import inspect
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scalper import postmortem as PM


T0 = datetime(2026, 8, 20, 7, 0, tzinfo=timezone.utc)


def _bars(rows, start=T0, minutes=5):
    return pd.DataFrame([
        {"time": start + timedelta(minutes=minutes * i),
         "open": (hi + lo) / 2, "high": hi, "low": lo, "close": (hi + lo) / 2}
        for i, (hi, lo) in enumerate(rows)
    ])


class TestSingleSourceOfTruth(unittest.TestCase):

    def test_backtester_imports_the_live_forensic_module(self):
        # Not a re-implementation. The simulator must call the same analyse().
        src = (Path(__file__).resolve().parents[1] / "backtest_scalper.py"
               ).read_text(encoding="utf-8")
        self.assertIn("from scalper import postmortem as PM", src)
        self.assertIn("PM.analyse(", src)

    def test_agent_imports_the_live_forensic_module(self):
        src = (Path(__file__).resolve().parents[1] / "scalper_agent.py"
               ).read_text(encoding="utf-8")
        self.assertIn("from scalper import postmortem as PMORTEM", src)
        self.assertIn("PMORTEM.analyse(", src)

    def test_thresholds_have_exactly_one_definition(self):
        # A second copy of any of these silently gives the two processes
        # different definitions of the same failure mode.
        for name in ("WIN_CLEAN_MAE_R", "GAVE_BACK_MFE_R", "STOP_CLIP_MAE_R",
                     "FALSE_SIGNAL_MFE_R", "TIMEOUT_NEAR_MISS_MFE_R",
                     "TIMEOUT_STALL_R"):
            self.assertTrue(hasattr(PM, name), f"{name} missing")
        root = Path(__file__).resolve().parents[1]
        for path in (root / "scalper_agent.py", root / "backtest_scalper.py",
                     root / "analyze_incidents.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("GAVE_BACK_MFE_R =", text,
                             f"{path.name} redefines a forensic threshold")
            self.assertNotIn("FALSE_SIGNAL_MFE_R =", text,
                             f"{path.name} redefines a forensic threshold")

    def test_identical_trade_classifies_identically_either_side(self):
        # The same context and bars must produce the same label regardless of
        # which process built the TradeContext.
        df = _bars([(100.2, 99.8), (100.1, 98.95), (100.5, 99.5),
                    (101.0, 100.0), (102.4, 101.0)])
        common = dict(
            ticket=1, symbol="XAUUSD", direction="BULLISH",
            entry=100.0, stop_loss=99.0, tp1=102.0, outcome="LOSS",
            pnl_usd=-30.0, open_time=T0 + timedelta(minutes=5),
            close_time=T0 + timedelta(minutes=10),
        )
        live = PM.analyse(PM.TradeContext(regime="MANIPULATION",
                                          session="LONDON_OPEN", **common), df)
        sim = PM.analyse(PM.TradeContext(regime="SIM",
                                         session="LONDON_OPEN", **common), df)
        self.assertEqual(live.failure_mode, sim.failure_mode)
        self.assertEqual(live.mfe_r, sim.mfe_r)
        self.assertEqual(live.mae_r, sim.mae_r)
        self.assertEqual(live.tp1_after_exit, sim.tp1_after_exit)


class TestNoDecisionAuthority(unittest.TestCase):

    def test_analyse_returns_no_permission_field(self):
        # A field named allow/blocked/veto would mean something downstream
        # could start reading this as a gate.
        df = _bars([(100.2, 99.8), (100.1, 98.9)])
        pm = PM.analyse(PM.TradeContext(
            ticket=1, symbol="XAUUSD", direction="BULLISH", entry=100.0,
            stop_loss=99.0, tp1=102.0, outcome="LOSS", pnl_usd=-30.0,
            open_time=T0 + timedelta(minutes=5),
            close_time=T0 + timedelta(minutes=10)), df)
        for banned in ("allow", "blocked", "veto", "approved", "skip"):
            self.assertNotIn(banned, pm.to_record(),
                             f"postmortem exposes a decision-shaped field "
                             f"'{banned}' — it must stay observational")

    def test_scan_symbol_never_branches_on_telemetry(self):
        # The rejection log is written to, never read from, inside the scan
        # path. `if self.rejects...` would make a counter into a filter.
        src = (Path(__file__).resolve().parents[1] / "scalper_agent.py"
               ).read_text(encoding="utf-8")
        self.assertNotIn("if self.rejects", src)
        self.assertNotIn("if self.incidents", src)
        self.assertNotIn("self.rejects.counts_for", src)

    def test_reject_log_record_returns_nothing(self):
        from scalper.reject_log import RejectionLog
        # `from __future__ import annotations` makes annotations strings, so
        # the declared return reads as "None" rather than the None object.
        sig = inspect.signature(RejectionLog.record)
        self.assertIn(sig.return_annotation, (None, "None"),
                      "record() must be annotated as returning None — a "
                      "return value invites a caller to branch on it")
        log = RejectionLog(str(Path(__file__).with_name("_parity_tmp.json")))
        try:
            self.assertIsNone(log.record("CRG", "XAUUSD"))
        finally:
            Path(__file__).with_name("_parity_tmp.json").unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
