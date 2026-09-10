"""
Regression tests for TGA's TP-extension / partial-close stage.

Every test here pins a defect found on 2026-08-26:

  * The TP-extension block (the ONLY caller of `_partial_close_mt5`) became
    unreachable when TP1 moved 1.0R -> 2.0R.  `early_close_armed` flips at
    1.0R and the early-close branch `return`s out of `_process_position`
    before the TP-proximity check at step 6 is ever evaluated.  Every
    TP_EXTEND in the action log fired at peak 0.71R-0.94R against the old
    1R target; there have been zero since the migration.
  * `round(0.01 * 0.50, 2)` is `0.01`, and volume_min/step are both 0.01,
    so a "partial" close on a 0.01-lot position closed 100% of it.  The
    guard only tested `< volume_min`, never `>= rec.volume`.
  * `_partial_close_mt5` logged nothing on a rejected order and returned
    None on every path; the caller ignored it and wrote a TP_EXTEND action
    row regardless - a journal entry for a close that never happened.
  * The TP-extension write passed `p.sl`, the snapshot taken before the
    trailing stack moved the stop earlier in the same tick, reverting a
    trailed SL to its looser pre-trail value.

The live Guardian cannot be exercised by the simulator (it models no
trailing at all - see CLAUDE.md 13.10), so unit coverage is the only thing
standing between these paths and a silent live regression.
"""
from __future__ import annotations

import logging
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import trade_guardian_agent as tga


def setUpModule():
    """Contain TGA's logging so tests never write to the live journals.

    Importing `trade_guardian_agent` runs `logging.basicConfig` with a
    FileHandler on `logs/tga_log.txt` at the ROOT logger — and when another
    test module has already imported `scalper_agent`, basicConfig no-ops and
    TGA records propagate into `logs/scalper_agent.log` instead. Either way a
    unit test emits ticket-1 entries indistinguishable from live Guardian
    actions. CLAUDE.md 13.10: a fabricated journal row is worse than none.
    """
    tga.logger.handlers[:] = [logging.NullHandler()]
    tga.logger.propagate = False


DONE = 10009
REJECT = 10018

ENTRY = 2000.0
SL = 1990.0          # r_risk = 10.0
TP = 2020.0          # 2.0R  - current geometry
ATR = 4.0            # TP proximity band = 0.5 * ATR = 2.0


class FakeMT5:
    """Minimal stand-in for the MetaTrader5 surface _process_position touches."""

    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 2
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = DONE
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15

    def __init__(self, deal_retcode=DONE, sltp_retcode=DONE, volume_min=0.01):
        self.deal_retcode = deal_retcode
        self.sltp_retcode = sltp_retcode
        self.volume_min = volume_min
        self.sent = []

    def symbol_info(self, symbol):
        return SimpleNamespace(volume_min=self.volume_min, volume_step=0.01,
                               digits=2, point=0.01)

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=2019.0, ask=2019.2)

    def order_send(self, request):
        self.sent.append(request)
        rc = (self.deal_retcode if request.get("action") == self.TRADE_ACTION_DEAL
              else self.sltp_retcode)
        return SimpleNamespace(retcode=rc, comment="fake", order=1)

    # convenience views
    def deals(self):
        return [r for r in self.sent if r.get("action") == self.TRADE_ACTION_DEAL]

    def sltps(self):
        return [r for r in self.sent if r.get("action") == self.TRADE_ACTION_SLTP]


def _record(volume=0.10):
    return tga.TGAPositionRecord(
        ticket=1, symbol="XAUUSD", origin_agent="SA", direction="BUY",
        entry_price=ENTRY, original_sl=SL, original_tp=TP, volume=volume,
        entry_atr=ATR, entry_time=datetime.now(timezone.utc),
    )


def _agent(fake, *, sl_move=None, early_close=(False, ""), momentum=True,
           new_tp=2035.0):
    agent = tga.TradeGuardianAgent(tga.TGAConfig())
    agent.data = mock.Mock()
    agent.data.get_data.return_value = mock.Mock()
    agent.sl_engine = mock.Mock()
    agent.sl_engine.evaluate.return_value = (2, sl_move) if sl_move else (0, None)
    agent.early_engine = mock.Mock()
    agent.early_engine.check_triggers.return_value = early_close
    agent.tp_engine = mock.Mock()
    agent.tp_engine.check_momentum.return_value = momentum
    agent.tp_engine.get_next_structure_target.return_value = new_tp
    agent.actions = []
    agent._log_action = lambda rec, at, reason, *a, **k: agent.actions.append(at)
    return agent


def _pos(price, sl=SL, tp=TP):
    return SimpleNamespace(price_current=price, sl=sl, tp=tp)


class TPExtensionReachability(unittest.TestCase):
    """The 2R migration silently disabled the whole partial-close feature."""

    def test_tp_extension_runs_when_price_is_in_the_band_with_momentum(self):
        fake = FakeMT5()
        agent = _agent(fake, early_close=(True, "Trigger 1: rejection wick"))
        rec = _record()
        with mock.patch.object(tga, "mt5", fake):
            # 2019 -> 1.9R, dist_to_tp = 1.0 < 0.5*ATR = 2.0
            agent._process_position(rec, _pos(2019.0))

        self.assertTrue(rec.tp_extended,
                        "TP extension never ran: early close pre-empted it")
        self.assertEqual(len(fake.deals()), 1,
                         "no partial close was sent")
        self.assertNotIn("EARLY_CLOSE", agent.actions,
                         "early close fired on a trade running into TP")
        self.assertIn("TP_EXTEND", agent.actions)

    def test_early_close_still_fires_outside_the_tp_band(self):
        """The suppression must be narrow - a stalling trade still gets killed."""
        fake = FakeMT5()
        agent = _agent(fake, early_close=(True, "Trigger 1: rejection wick"))
        rec = _record()
        with mock.patch.object(tga, "mt5", fake):
            # 2012 -> 1.2R, dist_to_tp = 8.0 >> 2.0
            agent._process_position(rec, _pos(2012.0))

        self.assertIn("EARLY_CLOSE", agent.actions)
        self.assertFalse(rec.tp_extended)

    def test_early_close_fires_in_the_band_without_momentum(self):
        """No momentum = not running into TP = early close keeps priority."""
        fake = FakeMT5()
        agent = _agent(fake, early_close=(True, "Trigger 2: momentum loss"),
                       momentum=False)
        rec = _record()
        with mock.patch.object(tga, "mt5", fake):
            agent._process_position(rec, _pos(2019.0))

        self.assertIn("EARLY_CLOSE", agent.actions)
        partials = [r for r in fake.deals() if r["comment"] == "TGA_PARTIAL_TP1"]
        self.assertEqual(partials, [], "banked a partial on a stalling trade")


class PartialCloseVolume(unittest.TestCase):

    def test_partial_close_never_closes_the_entire_position(self):
        """0.01 lots cannot be halved: round(0.005, 2) == 0.01 == the lot."""
        fake = FakeMT5()
        agent = _agent(fake)
        rec = _record(volume=0.01)
        with mock.patch.object(tga, "mt5", fake):
            agent._process_position(rec, _pos(2019.0))

        for req in fake.deals():
            self.assertLess(req["volume"], 0.01,
                            "TGA closed 100% of the position and called it partial")
        self.assertEqual(fake.deals(), [],
                         "an unsplittable position must not be partially closed")

    def test_partial_close_halves_a_splittable_position(self):
        fake = FakeMT5()
        agent = _agent(fake)
        rec = _record(volume=0.10)
        with mock.patch.object(tga, "mt5", fake):
            agent._process_position(rec, _pos(2019.0))

        self.assertEqual(len(fake.deals()), 1)
        self.assertAlmostEqual(fake.deals()[0]["volume"], 0.05, places=2)
        self.assertAlmostEqual(rec.volume, 0.05, places=2)


class PartialCloseFailureIsNotLoggedAsSuccess(unittest.TestCase):

    def test_rejected_partial_close_does_not_write_a_tp_extend_row(self):
        fake = FakeMT5(deal_retcode=REJECT)
        agent = _agent(fake)
        rec = _record(volume=0.10)
        with mock.patch.object(tga, "mt5", fake):
            agent._process_position(rec, _pos(2019.0))

        self.assertFalse(rec.tp_extended,
                         "tp_extended latched on a rejected partial close")
        self.assertNotIn("TP_EXTEND", agent.actions,
                         "journal claims a partial close the broker rejected")
        self.assertAlmostEqual(rec.volume, 0.10, places=2,
                               msg="record shrank on a rejected close")

    def test_partial_close_reports_success(self):
        fake = FakeMT5()
        agent = _agent(fake)
        rec = _record(volume=0.10)
        with mock.patch.object(tga, "mt5", fake):
            ok = agent._partial_close_mt5(rec, 0.50)
        self.assertIs(ok, True)

    def test_partial_close_reports_rejection(self):
        fake = FakeMT5(deal_retcode=REJECT)
        agent = _agent(fake)
        rec = _record(volume=0.10)
        with mock.patch.object(tga, "mt5", fake):
            ok = agent._partial_close_mt5(rec, 0.50)
        self.assertIs(ok, False)


class TPExtensionPreservesTrailedSL(unittest.TestCase):

    def test_extension_does_not_revert_a_stop_trailed_this_tick(self):
        fake = FakeMT5()
        agent = _agent(fake, sl_move=2005.0)
        rec = _record(volume=0.10)
        with mock.patch.object(tga, "mt5", fake):
            agent._process_position(rec, _pos(2019.0))

        sltps = fake.sltps()
        self.assertGreaterEqual(len(sltps), 2, "expected a trail write and a TP write")
        self.assertAlmostEqual(sltps[-1]["sl"], 2005.0, places=2,
                               msg="TP extension reverted the trailed SL to its "
                                   "pre-trail value")


if __name__ == "__main__":
    unittest.main()
