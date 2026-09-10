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


class TestGateTelemetryIsObservationOnly(unittest.TestCase):
    """
    The Stage 0 telemetry fields carry gate state into the incident record.
    They exist to be RECORDED, never to be CONSULTED. If the scan path ever
    reads one, that is a gate wearing telemetry's clothes and has to be
    mirrored and fold-tested like any other gate. See CLAUDE.md 13.10.
    """

    def test_derived_telemetry_never_reaches_the_decision_path(self):
        src = (Path(__file__).resolve().parents[1] / "scalper_agent.py"
               ).read_text(encoding="utf-8")
        scan = src[src.index("def _scan_symbol"):src.index("def _execute_trade")]

        # `htf_alignment` is DERIVED in postmortem for post-hoc analysis only.
        # It has no business existing anywhere near a decision.
        self.assertNotIn("htf_alignment", scan)

        # The recorded fields may be WRITTEN in the scan path -- that is the
        # registration site, and writing is the whole point. What must never
        # happen is a BRANCH on one. Anything else is a gate wearing
        # telemetry's clothes. See CLAUDE.md 13.10.
        telemetry = ("stb_confidence", "short_term_bias", "htf_trend",
                     "recent_sweep", "config_era", "CONFIG_ERA")
        for line in scan.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if not any(name in stripped for name in telemetry):
                continue
            # A dict-literal write (`"config_era": DP.CONFIG_ERA,`) or a
            # keyword argument is fine; a conditional is not.
            self.assertFalse(
                stripped.startswith(("if ", "elif ", "while ", "assert ")),
                f"telemetry must not be branched on: {stripped!r}")
            for op in (" == ", " != ", " in ", " not in ", " < ", " > "):
                self.assertNotIn(
                    op, stripped,
                    f"telemetry must not be compared: {stripped!r}")

    def test_htf_alignment_has_exactly_one_definition(self):
        self.assertEqual(PM.htf_alignment("BULLISH", "BEARISH"), "OPPOSE")
        self.assertEqual(PM.htf_alignment("BEARISH", "BULLISH"), "OPPOSE")
        self.assertEqual(PM.htf_alignment("BULLISH", "BULLISH"), "AGREE")
        self.assertEqual(PM.htf_alignment("BEARISH", "BEARISH"), "AGREE")
        self.assertEqual(PM.htf_alignment("BULLISH", "RANGING"), "NEUTRAL")
        self.assertEqual(PM.htf_alignment("BULLISH", "UNKNOWN"), "UNKNOWN")
        self.assertEqual(PM.htf_alignment("UNKNOWN", "BULLISH"), "UNKNOWN")

    def test_telemetry_round_trips_into_the_record(self):
        ctx = PM.TradeContext(
            ticket=1, symbol="XAUUSD", direction="BULLISH",
            entry=100.0, stop_loss=99.0, tp1=102.0, outcome="LOSS",
            pnl_usd=-10.0, open_time=T0, close_time=T0 + timedelta(minutes=30),
            risk_usd=10.0,
            stb_confidence="LOW", short_term_bias="NEUTRAL",
            htf_trend="BEARISH", config_era="test-era",
        )
        rec = PM.analyse(ctx, _bars([(100.5, 98.5)] * 12), bar_minutes=5,
                         lookahead_bars=4).to_record()
        self.assertEqual(rec["stb_confidence"], "LOW")
        self.assertEqual(rec["htf_trend"], "BEARISH")
        self.assertEqual(rec["config_era"], "test-era")
        # BULLISH trade under a BEARISH htf read.
        self.assertEqual(rec["htf_alignment"], "OPPOSE")

    def test_unclassified_path_also_carries_telemetry(self):
        # The bars-unavailable path is the one least exercised in testing and
        # most exercised in production edge cases.
        ctx = PM.TradeContext(
            ticket=2, symbol="XAUUSD", direction="BEARISH",
            entry=100.0, stop_loss=101.0, tp1=98.0, outcome="LOSS",
            pnl_usd=-10.0, open_time=T0, close_time=T0 + timedelta(minutes=30),
            risk_usd=10.0,
            stb_confidence="HIGH", htf_trend="BEARISH", config_era="test-era",
        )
        rec = PM.analyse(ctx, None, bar_minutes=5, lookahead_bars=4).to_record()
        self.assertEqual(rec["stb_confidence"], "HIGH")
        self.assertEqual(rec["config_era"], "test-era")
        self.assertEqual(rec["htf_alignment"], "AGREE")


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

    def test_simulator_populates_gate_telemetry_from_the_gate(self):
        # The fields existed on SimTrade for a day while nothing assigned them,
        # so every sim row read UNKNOWN and the two books were not poolable on
        # the one axis they were added to answer. A substring check would pass
        # on the dataclass defaults, so this asserts the constructor keyword is
        # present AND that its value comes off the `stb` gate result rather
        # than a literal.
        import ast
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "backtest_scalper.py").read_text(
            encoding="utf-8"))
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "SimTrade"]
        self.assertEqual(len(calls), 1, "expected one SimTrade construction")
        kwargs = {k.arg: k.value for k in calls[0].keywords if k.arg}
        for field in ("stb_confidence", "short_term_bias", "htf_trend",
                      "recent_sweep"):
            self.assertIn(field, kwargs, f"simulator drops {field}")
            names = {n.value.id for n in ast.walk(kwargs[field])
                     if isinstance(n, ast.Attribute)
                     and isinstance(n.value, ast.Name)}
            self.assertIn("stb", names,
                          f"{field} is not sourced from the STB gate result")

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
