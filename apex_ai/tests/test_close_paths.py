"""
Regression tests for the forced-close paths (timeout, 23:00 EOD, midnight
reset) and the accounting that hangs off them.

Every test here pins a defect that was live in the 2026-08-23 audit build:

  * `_close_at_market` discarded the `order_send` result, so a rejected close
    was booked as a completed trade and the still-open position was deleted
    from `_open_trades` — invisible for the rest of the process lifetime.
  * P&L was read back before the closing deal had settled, so the entry deal's
    commission was booked as the trade's result.
  * `SADailyReset` called `mt5.Close(ticket)`, which is not a valid signature
    for the installed MetaTrader5 package, and then read `.retcode` off a bool.
  * `restore()` silently overrode the operator's `--pool` allocation.

These paths cannot be reached by the simulator, so unit coverage is the only
thing standing between a broker rejection and a phantom ledger entry.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import scalper_agent
from scalper_agent import ScalperAgent
from scalper.capital_pool import SACapitalPool
from scalper.trade_logger import SATradeLogger, SATradeRecord
from scalper import daily_reset as daily_reset_mod


# ── Fakes ─────────────────────────────────────────────────────────────────────

DONE = 10009          # mt5.TRADE_RETCODE_DONE
REJECT = 10018        # market closed


class FakeMT5:
    """Minimal stand-in for the MetaTrader5 module surface these paths touch."""

    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = DONE
    DEAL_ENTRY_IN = 0
    DEAL_ENTRY_OUT = 1

    def __init__(self, retcode=DONE, closes_position=True, deals=None):
        self.retcode = retcode
        self.closes_position = closes_position
        self.position_open = True
        self.sent_requests = []
        self._deals = deals if deals is not None else []

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=2000.0, ask=2000.5)

    def positions_get(self, ticket=None):
        if not self.position_open:
            return ()
        return (SimpleNamespace(ticket=ticket or 1, symbol="XAUUSD",
                                type=self.ORDER_TYPE_BUY, volume=0.10,
                                profit=-3.0, price_open=2000.0, sl=1990.0,
                                tp=2020.0, time=0, magic=88880),)

    def order_send(self, request):
        self.sent_requests.append(request)
        if self.retcode == DONE and self.closes_position:
            self.position_open = False
        return SimpleNamespace(retcode=self.retcode, comment="fake",
                               order=12345)

    def history_deals_get(self, *args, position=None, **kwargs):
        return tuple(self._deals)

    # Used only by the daily-reset fallback path; never reached when a closer
    # is injected. Present so an accidental call is visibly wrong.
    def Close(self, symbol, comment=None, ticket=None):  # noqa: N802
        raise AssertionError("daily reset must use the injected closer")


def deal(entry, profit=0.0, commission=0.0, swap=0.0):
    return SimpleNamespace(entry=entry, profit=profit, commission=commission,
                           swap=swap, position_id=1, time=0)


def bare_agent(**attrs) -> ScalperAgent:
    """
    A ScalperAgent with only the attributes the close paths read.

    Building a real one requires a live MT5 session; these methods are pure
    functions of `_open_trades` plus the broker responses, so isolating them
    keeps the regression fast and honest.
    """
    agent = ScalperAgent.__new__(ScalperAgent)
    agent._open_trades = {}
    agent.CLOSE_CONFIRM_ATTEMPTS = 2
    agent.CLOSE_CONFIRM_DELAY_S = 0.0
    agent.PNL_SETTLE_ATTEMPTS = 2
    agent.PNL_SETTLE_DELAY_S = 0.0
    for k, v in attrs.items():
        setattr(agent, k, v)
    return agent


# ── _close_at_market ──────────────────────────────────────────────────────────

class TestCloseAtMarket(unittest.TestCase):

    def test_rejected_close_returns_false(self):
        fake = FakeMT5(retcode=REJECT, closes_position=False)
        agent = bare_agent()
        with mock.patch.object(scalper_agent, "mt5", fake):
            ok = agent._close_at_market(1, "XAUUSD", 0, 0.10, reason="EOD_CLOSE")
        self.assertFalse(ok, "a rejected order_send must not report success")

    def test_done_but_position_survives_returns_false(self):
        """A DONE retcode is not proof the position is gone (partial fill)."""
        fake = FakeMT5(retcode=DONE, closes_position=False)
        agent = bare_agent()
        with mock.patch.object(scalper_agent, "mt5", fake):
            ok = agent._close_at_market(1, "XAUUSD", 0, 0.10)
        self.assertFalse(ok)

    def test_confirmed_close_returns_true(self):
        fake = FakeMT5()
        agent = bare_agent()
        with mock.patch.object(scalper_agent, "mt5", fake):
            ok = agent._close_at_market(1, "XAUUSD", 0, 0.10)
        self.assertTrue(ok)

    def test_missing_tick_does_not_claim_success(self):
        fake = FakeMT5()
        fake.symbol_info_tick = lambda symbol: None
        agent = bare_agent()
        with mock.patch.object(scalper_agent, "mt5", fake):
            ok = agent._close_at_market(1, "XAUUSD", 0, 0.10)
        self.assertFalse(ok)
        self.assertEqual(fake.sent_requests, [],
                         "no order should be sent without a price")

    def test_reason_reaches_the_broker_comment(self):
        """
        Exit profiles have to be distinguishable in deal history — every close
        used to be stamped SA_TIMEOUT regardless of why it fired, and an exit
        profile that cannot be measured cannot be eliminated (RESEARCH §13.3).
        """
        for reason, expected in (("TIMEOUT", "SA_TIMEOUT"),
                                 ("EOD_CLOSE", "SA_EOD_CLOSE"),
                                 ("EOD_RESET", "SA_EOD_RESET")):
            fake = FakeMT5()
            agent = bare_agent()
            with mock.patch.object(scalper_agent, "mt5", fake):
                agent._close_at_market(1, "XAUUSD", 0, 0.10, reason=reason)
            self.assertEqual(fake.sent_requests[0]["comment"], expected)


# ── _force_close_all ──────────────────────────────────────────────────────────

class TestForceCloseAll(unittest.TestCase):

    def setUp(self):
        self.now = datetime(2026, 8, 24, 23, 5, tzinfo=timezone.utc)
        self.booked = []

    def _agent(self):
        agent = bare_agent()
        agent._open_trades = {1: {"symbol": "XAUUSD", "direction": "BULLISH",
                                  "open_time": self.now}}
        agent._on_trade_closed = lambda t, outcome, pnl, when=None: \
            self.booked.append((t, outcome, pnl))
        return agent

    def test_failed_close_keeps_the_position_tracked_and_unbooked(self):
        """
        The core defect: a rejected close used to write a fabricated P&L into
        the ledger and drop the ticket, leaving a live position that no timeout
        and no EOD sweep would ever look at again.
        """
        fake = FakeMT5(retcode=REJECT, closes_position=False)
        agent = self._agent()
        with mock.patch.object(scalper_agent, "mt5", fake):
            still_open = agent._force_close_all(self.now, "EOD_CLOSE")

        self.assertEqual(still_open, 1)
        self.assertIn(1, agent._open_trades, "position must stay tracked")
        self.assertEqual(self.booked, [], "nothing may be booked for it")

    def test_confirmed_close_books_and_untracks(self):
        fake = FakeMT5(deals=[deal(FakeMT5.DEAL_ENTRY_IN, commission=-0.5),
                              deal(FakeMT5.DEAL_ENTRY_OUT, profit=-4.0,
                                   commission=-0.5, swap=-0.1)])
        agent = self._agent()
        with mock.patch.object(scalper_agent, "mt5", fake):
            still_open = agent._force_close_all(self.now, "EOD_CLOSE")

        self.assertEqual(still_open, 0)
        self.assertNotIn(1, agent._open_trades)
        self.assertEqual(len(self.booked), 1)
        ticket, outcome, pnl = self.booked[0]
        self.assertEqual(outcome, "EOD_CLOSE")
        self.assertAlmostEqual(pnl, -5.1, places=6,
                               msg="P&L must include commission and swap")


# ── _settled_pnl ──────────────────────────────────────────────────────────────

class TestSettledPnl(unittest.TestCase):

    def test_entry_only_history_returns_none(self):
        """
        The old read-back booked the entry commission as the trade's result:
        non-zero, so the `or pos.profit` fallback was bypassed, and a
        ~-$0.50 'loss' armed a cooldown and advanced the loss streak.
        """
        fake = FakeMT5(deals=[deal(FakeMT5.DEAL_ENTRY_IN, commission=-0.5)])
        agent = bare_agent()
        with mock.patch.object(scalper_agent, "mt5", fake):
            self.assertIsNone(agent._settled_pnl(1))

    def test_settled_history_sums_all_components(self):
        fake = FakeMT5(deals=[deal(FakeMT5.DEAL_ENTRY_IN, commission=-0.5),
                              deal(FakeMT5.DEAL_ENTRY_OUT, profit=12.0,
                                   commission=-0.5, swap=-0.25)])
        agent = bare_agent()
        with mock.patch.object(scalper_agent, "mt5", fake):
            self.assertAlmostEqual(agent._settled_pnl(1), 10.75, places=6)

    def test_settled_close_uses_broker_exit_timestamp(self):
        close_time = datetime(2026, 8, 25, 10, 3, 17, 456000,
                              tzinfo=timezone.utc)
        fake = FakeMT5(deals=[
            deal(FakeMT5.DEAL_ENTRY_IN, commission=-0.5),
            SimpleNamespace(entry=FakeMT5.DEAL_ENTRY_OUT, profit=12.0,
                             commission=-0.5, swap=-0.25, position_id=1,
                             time=int(close_time.timestamp()),
                             time_msc=int(close_time.timestamp() * 1000)),
        ])
        agent = bare_agent()
        agent._open_trades = {1: {"symbol": "XAUUSD", "direction": "BULLISH",
                                  "open_time": close_time - timedelta(minutes=3)}}
        booked = []
        agent._on_trade_closed = lambda t, outcome, pnl, when=None: \
            booked.append(when)
        observed_later = close_time + timedelta(seconds=20)
        with mock.patch.object(scalper_agent, "mt5", fake):
            self.assertTrue(agent._book_settled_close(
                1, agent._open_trades[1], observed_later, outcome="WIN_TP1"))

        self.assertEqual(booked, [close_time])

    def test_loss_settled_close_keeps_observation_timestamp(self):
        close_time = datetime(2026, 8, 25, 10, 3, 17, 456000,
                              tzinfo=timezone.utc)
        fake = FakeMT5(deals=[
            deal(FakeMT5.DEAL_ENTRY_IN, commission=-0.5),
            SimpleNamespace(entry=FakeMT5.DEAL_ENTRY_OUT, profit=-12.0,
                             commission=-0.5, swap=-0.25, position_id=1,
                             time=int(close_time.timestamp()),
                             time_msc=int(close_time.timestamp() * 1000)),
        ])
        agent = bare_agent()
        agent._open_trades = {1: {"symbol": "XAUUSD", "direction": "BULLISH",
                                  "open_time": close_time - timedelta(minutes=3)}}
        booked = []
        agent._on_trade_closed = lambda t, outcome, pnl, when=None: \
            booked.append(when)
        observed_later = close_time + timedelta(seconds=20)
        with mock.patch.object(scalper_agent, "mt5", fake):
            self.assertTrue(agent._book_settled_close(
                1, agent._open_trades[1], observed_later, outcome="LOSS"))

        self.assertEqual(booked, [observed_later])

    def test_no_deals_at_all_returns_none_not_zero(self):
        agent = bare_agent()
        with mock.patch.object(scalper_agent, "mt5", FakeMT5(deals=[])):
            self.assertIsNone(agent._get_closed_pnl(1))


# ── SADailyReset ──────────────────────────────────────────────────────────────

class TestDailyResetClose(unittest.TestCase):

    def setUp(self):
        # execute() appends to the daily journal. Redirect it — a test run must
        # never write rows into the operator's real P&L record.
        self._tmp = tempfile.TemporaryDirectory()
        self._journal = mock.patch.object(
            daily_reset_mod, "DAILY_JOURNAL_PATH",
            Path(self._tmp.name) / "sa_daily_journal.json")
        self._journal.start()

    def tearDown(self):
        self._journal.stop()
        self._tmp.cleanup()

    def test_uses_injected_closer_not_mt5_close(self):
        """
        `mt5.Close(ticket)` was never a valid call — the installed signature is
        Close(symbol, *, comment=None, ticket=None) and it returns a bool, so
        both the positional ticket and the `.retcode` read raised, were
        swallowed, and the midnight net never closed anything.
        """
        fake = FakeMT5()
        calls = []

        def closer(ticket, symbol, pos_type, volume, reason):
            calls.append((ticket, symbol, reason))
            return True

        pool = SACapitalPool(sa_pool_usd=1000.0, risk_pct_per_trade=0.03)
        reset = daily_reset_mod.SADailyReset()
        with mock.patch.object(daily_reset_mod, "mt5", fake):
            result = reset.execute(pool, fake, [1], closer=closer)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], "XAUUSD")
        self.assertEqual(calls[0][2], "EOD_RESET")
        self.assertEqual(result["positions_closed"], [1])

    def test_failed_close_is_not_reported_as_closed(self):
        fake = FakeMT5()
        pool = SACapitalPool(sa_pool_usd=1000.0, risk_pct_per_trade=0.03)
        reset = daily_reset_mod.SADailyReset()
        with mock.patch.object(daily_reset_mod, "mt5", fake):
            result = reset.execute(pool, fake, [1],
                                   closer=lambda *a, **k: False)
        self.assertEqual(result["positions_closed"], [],
                         "an unconfirmed close must never be reported closed")


# ── Unsettled-close booking ───────────────────────────────────────────────────

class TestUnsettledCloseBooking(unittest.TestCase):
    """
    A position that has vanished from the broker but whose *closing* deal has
    not yet landed in deal history.

    The agent used to book $0.00 for it and then derive the label from that
    fabrication, so the trade was recorded as a `LOSS`. The blast radius of one
    such booking, all of it verified in the code it calls:

      * `pool.register_close(0.0)` leaves the pool balance wrong — which sizes
        every later trade — and *resets* the consecutive-loss counter, because
        `0.0 < 0` is False.
      * `cooldown.record_trade_result(0.0)` arms the LOSS cooldown, because
        `0.0 > 0` is False, holding the agent to the next UTC hour on a trade
        that may well have won.
      * `SATradeLogger.log_close` only wrote into a record still marked `OPEN`,
        so that verdict could never be corrected.

    The rule pinned here is the one `_close_at_market` already enforces in the
    other direction: what the broker has not confirmed is not booked.
    """

    def setUp(self):
        self.now = datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)
        self.booked = []

    def _agent(self):
        agent = bare_agent()
        agent._open_trades = {1: {"symbol": "XAUUSD", "direction": "BULLISH",
                                  "open_time": self.now}}
        agent._on_trade_closed = (
            lambda t, outcome, pnl, when=None, settled=True:
            self.booked.append((t, outcome, pnl, settled)))
        return agent

    @staticmethod
    def _vanished(deals):
        fake = FakeMT5(deals=deals)
        fake.position_open = False
        return fake

    def test_unsettled_close_books_nothing_and_stays_tracked(self):
        """Entry leg only: the P&L is unknown, so there is nothing to book."""
        fake = self._vanished([deal(FakeMT5.DEAL_ENTRY_IN, commission=-0.5)])
        agent = self._agent()
        with mock.patch.object(scalper_agent, "mt5", fake):
            agent._monitor_open_trades()

        self.assertEqual(self.booked, [],
                         "an unsettled P&L must not be booked as $0.00")
        self.assertIn(1, agent._open_trades,
                      "the ticket must stay tracked so the next cycle retries")

    def test_retry_books_the_real_pnl_once_history_lands(self):
        """The deferred close is booked at its true value, with a WIN label."""
        fake = self._vanished([deal(FakeMT5.DEAL_ENTRY_IN, commission=-0.5)])
        agent = self._agent()
        with mock.patch.object(scalper_agent, "mt5", fake):
            agent._monitor_open_trades()
            self.assertEqual(self.booked, [])

            fake._deals.append(deal(FakeMT5.DEAL_ENTRY_OUT, profit=12.0,
                                    commission=-0.5, swap=-0.25))
            agent._monitor_open_trades()

        self.assertEqual(len(self.booked), 1)
        _ticket, outcome, pnl, settled = self.booked[0]
        self.assertEqual(outcome, "WIN_TP1",
                         "a winner deferred for one cycle is still a winner")
        self.assertAlmostEqual(pnl, 10.75, places=6)
        self.assertTrue(settled)
        self.assertNotIn(1, agent._open_trades)

    def test_give_up_is_labelled_unreconciled_not_loss(self):
        """
        The deferral is bounded — a ticket that never settles would hold a
        position slot forever. On give-up the trade is booked, but it is not
        handed a WIN/LOSS verdict there is no evidence for.
        """
        fake = self._vanished([deal(FakeMT5.DEAL_ENTRY_IN, commission=-0.5)])
        agent = self._agent()
        agent._open_trades[1]["settle_wait_since"] = (
            self.now
            - timedelta(minutes=ScalperAgent.PNL_RECONCILE_GRACE_MIN + 1))
        with mock.patch.object(scalper_agent, "mt5", fake):
            agent._monitor_open_trades()

        self.assertEqual(len(self.booked), 1)
        _ticket, outcome, _pnl, settled = self.booked[0]
        self.assertEqual(outcome, scalper_agent.OUTCOME_UNRECONCILED)
        self.assertFalse(settled, "the caller must know this P&L is fabricated")
        self.assertNotIn(1, agent._open_trades,
                         "give-up must release the position slot")

    def test_deferring_records_the_exit_profile_for_the_retry(self):
        """
        The exit profile must survive the wait. A TIMEOUT or EOD_CLOSE whose
        deal settles a cycle later comes back through the "position is gone"
        branch, which passes no outcome of its own — relabelling it WIN/LOSS
        there would silently merge that profile into the stop profile, and
        CLAUDE.md §13.3 requires each to stay separately measurable, because a
        profile that cannot be measured cannot be eliminated.
        """
        fake = self._vanished([deal(FakeMT5.DEAL_ENTRY_IN, commission=-0.5)])
        agent = self._agent()
        with mock.patch.object(scalper_agent, "mt5", fake):
            booked = agent._book_settled_close(1, agent._open_trades[1],
                                               self.now, outcome="TIMEOUT")

        self.assertFalse(booked)
        self.assertEqual(agent._open_trades[1].get("close_outcome"), "TIMEOUT")

    def test_a_deferred_eod_close_keeps_its_label_across_cycles(self):
        fake = self._vanished([deal(FakeMT5.DEAL_ENTRY_IN, commission=-0.5)])
        agent = self._agent()
        with mock.patch.object(scalper_agent, "mt5", fake):
            agent._force_close_all(self.now, "EOD_CLOSE")
            self.assertEqual(self.booked, [])

            # Next cycle: the deal has landed, and the ticket is picked up by
            # the ordinary monitor loop rather than by another EOD sweep.
            fake._deals.append(deal(FakeMT5.DEAL_ENTRY_OUT, profit=6.0,
                                    commission=-0.5, swap=0.0))
            agent._monitor_open_trades()

        self.assertEqual(len(self.booked), 1)
        self.assertEqual(self.booked[0][1], "EOD_CLOSE",
                         "the exit profile must survive the deferral")
        self.assertAlmostEqual(self.booked[0][2], 5.0, places=6)

    def test_forced_close_all_defers_an_unsettled_close(self):
        fake = self._vanished([deal(FakeMT5.DEAL_ENTRY_IN, commission=-0.5)])
        agent = self._agent()
        with mock.patch.object(scalper_agent, "mt5", fake):
            still_open = agent._force_close_all(self.now, "EOD_CLOSE")

        self.assertEqual(self.booked, [])
        self.assertEqual(still_open, 1)
        self.assertIn(1, agent._open_trades)

    def test_daily_reset_forces_a_verdict_rather_than_leaving_it_dangling(self):
        """
        There is no next cycle across the reset, so `force_settle` gives up
        immediately instead of carrying an unbooked trade into the new day.
        """
        fake = self._vanished([deal(FakeMT5.DEAL_ENTRY_IN, commission=-0.5)])
        agent = self._agent()
        with mock.patch.object(scalper_agent, "mt5", fake):
            still_open = agent._force_close_all(self.now, "EOD_RESET",
                                                force_settle=True)

        self.assertEqual(still_open, 0)
        self.assertEqual(len(self.booked), 1)
        self.assertEqual(self.booked[0][1], scalper_agent.OUTCOME_UNRECONCILED)


class TestUnreconciledIsQuarantined(unittest.TestCase):
    """
    An unreconciled close carries a fabricated $0.00. It is written to the
    trade log so an operator can find it, but it must not reach the two
    consumers that would turn it into a false finding: `postmortem.classify`
    has no branch for the label and falls through to the loss rules, inventing
    a failure mode, and the AdaptiveMemory journal feeds SEE's strategy trust.
    """

    def _agent(self):
        agent = bare_agent()
        agent.calls = []
        agent.pool = SimpleNamespace(
            register_close=lambda p: agent.calls.append(("pool", p)))
        agent.trade_log = SimpleNamespace(
            log_close=lambda t, r, p, s: agent.calls.append(("log", r, p)))
        agent.cooldown = SimpleNamespace(
            record_trade_result=lambda p, n: agent.calls.append(("cooldown", p)))
        agent.state_mach = SimpleNamespace(state=SimpleNamespace(value="ACTIVE"))
        agent._journal_closed_trade = lambda *a: agent.calls.append(("journal",))
        agent._queue_postmortem = lambda *a: agent.calls.append(("postmortem",))
        return agent

    def test_settled_close_reaches_every_consumer(self):
        agent = self._agent()
        agent._on_trade_closed(1, "WIN_TP1", 10.75)
        self.assertEqual([c[0] for c in agent.calls],
                         ["pool", "log", "cooldown", "journal", "postmortem"])

    def test_unreconciled_close_skips_forensics_and_journal(self):
        agent = self._agent()
        agent._on_trade_closed(1, scalper_agent.OUTCOME_UNRECONCILED, 0.0,
                               settled=False)
        kinds = [c[0] for c in agent.calls]
        self.assertIn("pool", kinds, "the position slot must still be released")
        self.assertIn("log", kinds, "it must be visible for reconciliation")
        self.assertNotIn("journal", kinds)
        self.assertNotIn("postmortem", kinds)


# ── Trade log correctability ──────────────────────────────────────────────────

class TestTradeLogClose(unittest.TestCase):
    """
    `log_close` matched only on `result == "OPEN"`, so a record booked once
    could never be corrected, and a close with no matching open record — every
    position adopted after a restart, which never calls `log_open` — was
    dropped with nothing but a warning.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "scalper_log.json"
        self.logger = SATradeLogger(str(self.path))

    def tearDown(self):
        self._tmp.cleanup()

    def _records(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _open_one(self, ticket=1):
        self.logger.log_open(SATradeRecord(
            instrument="XAUUSD", session_window="LONDON_OPEN",
            trigger_type="SWEEP_REJECTION", entry_price=4638.0,
            stop_loss=4647.0, tp1_target=4620.0, tp2_target=4612.0,
            position_size=0.01, sa_pool_risk_pct=3.0, ticket=ticket))

    def test_close_updates_the_open_record(self):
        self._open_one()
        self.logger.log_close(1, "WIN_TP1", 8.17, "ACTIVE")
        rec = self._records()[0]
        self.assertEqual(rec["result"], "WIN_TP1")
        self.assertAlmostEqual(rec["sa_pool_pnl"], 8.17)

    def test_a_booked_record_can_be_corrected(self):
        """
        The reconciliation path: 131 of 187 reconcilable records disagreed with
        the broker and not one of them could be repaired in place.
        """
        self._open_one()
        self.logger.log_close(1, "LOSS", 0.0, "ACTIVE")
        self.logger.log_close(1, "WIN_TP1", 8.17, "ACTIVE")

        records = self._records()
        self.assertEqual(len(records), 1, "a correction must not append")
        self.assertEqual(records[0]["result"], "WIN_TP1")
        self.assertAlmostEqual(records[0]["sa_pool_pnl"], 8.17)

    def test_close_without_an_open_record_is_recorded_not_dropped(self):
        """A position adopted after a restart has no `log_open` behind it."""
        self.logger.log_close(9876, "WIN_TP1", 12.50, "ACTIVE")
        records = self._records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["ticket"], 9876)
        self.assertEqual(records[0]["result"], "WIN_TP1")
        self.assertAlmostEqual(records[0]["sa_pool_pnl"], 12.50)

    def test_correction_targets_only_its_own_ticket(self):
        self._open_one(ticket=1)
        self._open_one(ticket=2)
        self.logger.log_close(1, "LOSS", -4.0, "ACTIVE")
        self.logger.log_close(2, "LOSS", -3.0, "ACTIVE")
        self.logger.log_close(1, "WIN_TP1", 9.0, "ACTIVE")

        by_ticket = {r["ticket"]: r for r in self._records()}
        self.assertAlmostEqual(by_ticket[1]["sa_pool_pnl"], 9.0)
        self.assertAlmostEqual(by_ticket[2]["sa_pool_pnl"], -3.0,
                               msg="the other ticket must be untouched")


# ── Pool allocation ───────────────────────────────────────────────────────────

class TestPoolRestoreModes(unittest.TestCase):

    def test_fresh_mode_holds_the_requested_allocation(self):
        """
        `--pool 1000` restored to $423.77 in the audit run, sizing at 42% of the
        requested risk unit. Position sizing is a signal-admission filter
        (CLAUDE.md §13.9) — the run measured a different sample than the one
        that was asked for.
        """
        pool = SACapitalPool(sa_pool_usd=1000.0, risk_pct_per_trade=0.03)
        pool.restore(daily_pnl=-20.0, cumulative_pnl=-576.23,
                     apply_to_pool=False)
        self.assertAlmostEqual(pool.current_pool, 1000.0)
        self.assertAlmostEqual(pool.risk_per_trade_usd, 30.0)
        self.assertAlmostEqual(pool.daily_pnl, -20.0,
                               msg="daily counters still gate the loss limit")

    def test_resume_mode_keeps_compounding(self):
        pool = SACapitalPool(sa_pool_usd=1000.0, risk_pct_per_trade=0.03)
        pool.restore(daily_pnl=-20.0, cumulative_pnl=-576.23)
        self.assertAlmostEqual(pool.current_pool, 423.77)


if __name__ == "__main__":
    unittest.main()
