import csv
import inspect
import io
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pandas as pd

from scalper import session_sweep as SS
from scalper.trigger_engine import SATriggerEngine, SATrigger, MicroLiquidity, resolve_enabled_triggers


def fixture(bearish=False):
    times = pd.date_range("2026-09-10T05:30Z", periods=32, freq="5min")
    df = pd.DataFrame(dict(time=times, open=102., high=103., low=101., close=101.7))
    df.loc[24, "high"] = 104.
    df.loc[30, ["open", "high", "low", "close"]] = [101., 102., 98., 101.]
    df.loc[31, ["open", "high", "low", "close"]] = [101., 106., 100.8, 105.5]
    end = pd.Timestamp("2026-09-10T02:00Z")
    checked = pd.Timestamp("2026-09-10T07:55Z")
    levels = [SS.Level("asia", "ASIA", "SSL", 100., end, checked),
              SS.Level("asia", "ASIA", "BSL", 130., end, checked)]
    if bearish:
        df[["open", "high", "low", "close"]] = pd.DataFrame({
            "open": 200-df.open, "high": 200-df.low,
            "low": 200-df.high, "close": 200-df.close})
        levels = [replace(p, side="BSL" if p.side == "SSL" else "SSL", price=200-p.price)
                  for p in levels]
    now = times[-1]+pd.Timedelta(minutes=5)
    return levels, df, float(df.close.iloc[-1]), now


class SessionSweepTests(unittest.TestCase):
    def test_mirrored_reversal_and_fixed_pivot_chronology(self):
        for bearish in (False, True):
            levels, df, quote, now = fixture(bearish)
            p = SS.evaluate(levels, df, quote, quote+.02, now)
            self.assertTrue(p.allow, p.reason)
            self.assertEqual(p.direction, "BEARISH" if bearish else "BULLISH")
            self.assertEqual(p.pivot, 96. if bearish else 104.)
            self.assertLess(SS.utc(p.pivot_known_at), SS.utc(p.raid_at))
            self.assertLess(SS.utc(p.raid_at), SS.utc(p.mss_at))
            self.assertGreaterEqual(abs(p.target-p.entry)/abs(p.entry-p.stop), 2)
            self.assertTrue(SS.quote_allowed(p, p.entry, now))

    def test_touch_is_not_sweep(self):
        levels, df, q, now = fixture()
        df.loc[30, "low"] = 100.
        self.assertFalse(SS.evaluate(levels, df, q, q, now).allow)

    def test_reclaim_without_mss_or_displacement_does_not_enter(self):
        levels, df, q, now = fixture()
        df.loc[31, ["open", "high", "close"]] = [101., 103., 102.]
        self.assertFalse(SS.evaluate(levels, df, 102., 102., now).allow)

    def test_two_outside_closes_cancel_both_directions(self):
        for bearish in (False, True):
            levels, df, q, now = fixture(bearish)
            if bearish:
                df.loc[30, ["open", "high", "low", "close"]] = [101., 103., 100.5, 102.]
                df.loc[31, ["open", "high", "low", "close"]] = [102., 104., 101., 103.]
            else:
                df.loc[30, ["open", "high", "low", "close"]] = [99., 99.5, 97., 98.]
                df.loc[31, ["open", "high", "low", "close"]] = [98., 99., 96., 97.]
            p = SS.evaluate(levels, df, float(df.close.iloc[-1]), float(df.close.iloc[-1]), now)
            self.assertFalse(p.allow)
            self.assertEqual(p.reason, "SESSION_SWEEP_ACCEPTED_OUTSIDE")

    def test_existing_first_sweep_is_not_rearmed(self):
        levels, df, q, now = fixture()
        levels[0] = replace(levels[0], swept_at=pd.Timestamp("2026-09-10T04:00Z"))
        self.assertFalse(SS.evaluate(levels, df, q, q, now).allow)

    def test_fresh_snapshot_and_recent_swept_snapshot_same_plan(self):
        levels, df, q, now = fixture()
        first = SS.evaluate(levels, df, q, q, now)
        levels[0] = replace(levels[0], swept_at=pd.Timestamp("2026-09-10T08:01Z"), checked_at=now)
        second = SS.evaluate(levels, df, q, q, now)
        self.assertTrue(second.allow, second.reason)
        self.assertEqual(first.setup_id, second.setup_id)
        self.assertEqual(first.stop, second.stop)

    def test_forming_future_bar_and_duplicate_bar(self):
        levels, df, q, now = fixture()
        forming = pd.DataFrame([dict(time=now, open=q, high=199., low=1., close=q)])
        before = SS.evaluate(levels, df, q, q, now)
        after = SS.evaluate(levels, pd.concat([df, forming]), q, q, now)
        self.assertEqual(before, after)
        self.assertFalse(SS.evaluate(levels, pd.concat([df, df.tail(1)]), q, q, now).allow)

    def test_gap_stale_data_and_unknown_price_fail_closed(self):
        levels, df, q, now = fixture()
        for bad in (df.drop(index=29), df.assign(close=float("nan"))):
            self.assertFalse(SS.evaluate(levels, bad, q, q, now).allow)
        self.assertFalse(SS.evaluate(levels, df, q, q, now+pd.Timedelta(minutes=2)).allow)
        stale = [replace(p, checked_at=now-pd.Timedelta(minutes=46)) for p in levels]
        self.assertFalse(SS.evaluate(stale, df, q, q, now).allow)
        self.assertFalse(SS.evaluate(levels, df, float("nan"), q, now).allow)

    def test_no_target_cherry_picking(self):
        levels, df, q, now = fixture()
        levels.append(replace(levels[1], record_id="near", price=110.))
        p = SS.evaluate(levels, df, q, q, now)
        self.assertFalse(p.allow)
        self.assertEqual(p.reason, "SESSION_SWEEP_TARGET_TOO_CLOSE")

    def test_target_taken_locally_excluded_and_no_target_rejected(self):
        levels, df, q, now = fixture()
        levels[1] = replace(levels[1], swept_at=pd.Timestamp("2026-09-10T07:00Z"))
        self.assertFalse(SS.evaluate(levels, df, q, q, now).allow)

    def test_quote_chase_expiry_and_used_event(self):
        levels, df, q, now = fixture()
        p = SS.evaluate(levels, df, q, q, now)
        self.assertFalse(SS.quote_allowed(p, q+1., now))
        self.assertFalse(SS.quote_allowed(p, q, now+pd.Timedelta(seconds=61)))
        self.assertFalse(SS.evaluate(levels, df, q, q, now, {p.setup_id}).allow)
        self.assertFalse(SS.quote_allowed(p, 99., now))

    def test_durable_one_attempt_per_boundary(self):
        levels, _, _, _ = fixture()
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(SS.reserve_attempt(folder, levels[0].setup_id))
            self.assertFalse(SS.reserve_attempt(folder, levels[0].setup_id))
            self.assertTrue(SS.reserve_attempt(folder, levels[1].setup_id))

    def test_switch_off_preserves_default_and_explicit_switch_required(self):
        self.assertNotIn("SESSION_SWEEP", resolve_enabled_triggers(None, False))
        with self.assertRaises(ValueError):
            resolve_enabled_triggers(["SESSION_SWEEP"], False)
        self.assertEqual(resolve_enabled_triggers(["SESSION_SWEEP"], False, False, True), ["SESSION_SWEEP"])
        levels, df, q, now = fixture()
        plan = SS.evaluate(levels, df, q, q, now)
        engine = SATriggerEngine(enabled_triggers=["SESSION_SWEEP", "SWEEP_REJECTION"])
        with patch.object(engine, "_check_sweep_rejection", return_value=SATrigger(True, "SWEEP_REJECTION")):
            p = engine.step2_trigger(df, df, MicroLiquidity(), "XAUUSD", session_sweep=plan)
        self.assertEqual(p.matched_triggers, ["SESSION_SWEEP", "SWEEP_REJECTION"])

    def test_shared_live_replay_api_and_off_defaults(self):
        import scalper_agent as live
        import backtest_scalper as sim
        self.assertIs(live.SS.evaluate, sim.SS.evaluate)
        for method in (live.ScalperAgent.__init__, sim.run_backtest):
            self.assertFalse(inspect.signature(method).parameters["session_sweep_enabled"].default)

    def test_replay_snapshots_symbol_metadata_before_event_loop(self):
        import backtest_scalper as sim
        source = inspect.getsource(sim.run_backtest)
        prefetch, event_loop = source.split("for now, symbol, i in events:", 1)
        self.assertEqual(prefetch.count("mt5.symbol_info(symbol)"), 1)
        self.assertNotIn("mt5.symbol_info(symbol)", event_loop)
        self.assertIn("info = symbol_info_by_symbol[symbol]", event_loop)

    def test_named_session_trigger_bypasses_unrelated_reclaim_fvg_gate(self):
        live = Path(inspect.getfile(__import__("scalper_agent"))).read_text(encoding="utf-8")
        replay = Path(inspect.getfile(__import__("backtest_scalper"))).read_text(encoding="utf-8")
        self.assertIn("self.reclaim_fvg_enabled and trigger.session_sweep is None", live)
        self.assertIn("reclaim_fvg_enabled and trigger.session_sweep is None", replay)

    def test_execution_account_quote_cost_and_reservation_guards(self):
        import scalper_agent as live
        levels, df, q, now = fixture()
        plan = SS.evaluate(levels, df, q, q, now)
        current = pd.Timestamp.now(tz="UTC")
        plan = replace(plan, mss_at=current.isoformat(),
                       quote_deadline=(current+pd.Timedelta(seconds=60)).isoformat())
        trig = SATrigger(True, "SESSION_SWEEP", "BULLISH", plan.entry, plan.stop,
                         plan.target, plan.target, session_sweep=plan)
        agent = live.ScalperAgent.__new__(live.ScalperAgent)
        agent.trigger_eng = SATriggerEngine()
        agent._calculate_lots = MagicMock(return_value=.01)
        agent.reclaim_fvg_enabled = False
        fake = MagicMock()
        fake.symbol_info.return_value = SimpleNamespace(point=.001, digits=3)
        fake.symbol_info_tick.return_value = SimpleNamespace(bid=q, ask=q, time=current.timestamp())
        fake.account_info.return_value = SimpleNamespace(login=1, server=SS.EXPECTED_SERVER)
        agent.execution_telemetry = MagicMock()
        fake.order_send.return_value = None  # uncertain submission must never retry
        with tempfile.TemporaryDirectory() as folder, patch.object(live, "mt5", fake):
            agent._session_sweep_attempt_dir = Path(folder)
            self.assertIsNone(agent._execute_trade("XAUUSD", trig, .01))
            fake.order_send.assert_not_called()
            fake.account_info.return_value.login = SS.EXPECTED_LOGIN
            fake.symbol_info_tick.return_value.ask = q+1
            self.assertIsNone(agent._execute_trade("XAUUSD", trig, .01))
            fake.order_send.assert_not_called()
            fake.symbol_info_tick.return_value.ask = q
            with patch.object(agent.trigger_eng, "step3_validate", return_value=(False, "cost")):
                self.assertIsNone(agent._execute_trade("XAUUSD", trig, .01))
            fake.order_send.assert_not_called()
            self.assertIsNone(agent._execute_trade("XAUUSD", trig, .01))
            self.assertEqual(fake.order_send.call_count, 1)
            self.assertIsNone(agent._execute_trade("XAUUSD", trig, .01))
            self.assertEqual(fake.order_send.call_count, 1)
            self.assertEqual(fake.order_send.call_args.args[0]["comment"], SS.ORDER_PREFIX+plan.setup_id)


class LedgerTests(unittest.TestCase):
    def files(self, root):
        now = pd.Timestamp("2026-09-10T09:17Z")
        row = dict(record_id="XAUUSD:LONDON:2026-09-10T06:00:00+00:00", symbol="XAUUSD",
                   session="LONDON", session_date_ny="2026-09-10", start_utc="2026-09-10T06:00:00+00:00",
                   end_utc="2026-09-10T09:00:00+00:00", high="130", low="100", high_status="FRESH",
                   low_status="SWEPT", high_swept_at_utc="", low_swept_at_utc="2026-09-10T09:02:00+00:00",
                   bars="180", updated_at_utc=now.isoformat())
        (root/"data").mkdir()
        (root/"journal/market").mkdir(parents=True)
        (root/"journal/market/session-liquidity-ledger.md").write_text(
            f"Last updated: `{now.isoformat()}`\nMT5 account: `172783529`\nMT5 server: `Exness-MT5Real2`\n", encoding="utf-8")
        self.write_rows(root, [row])
        return now, row

    def write_rows(self, root, rows):
        with (root/"data/session-liquidity.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, list(rows[0]))
            w.writeheader()
            w.writerows(rows)

    def test_correct_identity_stale_and_account_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            now, _ = self.files(root)
            self.assertEqual(len(SS.load_levels(root, now, SS.EXPECTED_LOGIN, SS.EXPECTED_SERVER)), 2)
            for stamp, login, server in ((now, 1, SS.EXPECTED_SERVER), (now, SS.EXPECTED_LOGIN, "demo"),
                                          (now+pd.Timedelta(minutes=46), SS.EXPECTED_LOGIN, SS.EXPECTED_SERVER)):
                with self.assertRaises(ValueError):
                    SS.load_levels(root, stamp, login, server)

    def test_duplicate_malformed_nan_partial_and_mixed_generation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            now, row = self.files(root)
            variants = [[row, row], [dict(row, high="nan")], [dict(row, bars="179")],
                        [dict(row, updated_at_utc="2026-09-10T09:16:00+00:00")],
                        [dict(row, high_swept_at_utc=now.isoformat())],
                        [dict(row, start_utc="2026-09-10T06:00:00")]]
            for rows in variants:
                self.write_rows(root, rows)
                with self.assertRaises(ValueError):
                    SS.load_levels(root, now, SS.EXPECTED_LOGIN, SS.EXPECTED_SERVER)

    def test_replay_dst_complete_windows_and_future_prefix_invariance(self):
        for start in ("2026-01-05T13:20Z", "2026-07-06T12:20Z"):
            t = pd.date_range(start, periods=170, freq="min")
            df = pd.DataFrame(dict(time=t, open=105., high=110., low=100., close=106.))
            df.loc[165, "high"] = 120.
            index = SS.reconstruct_levels(df)
            self.assertEqual(len(index), 2)
            self.assertEqual(index[0].session, "NEW_YORK")
            self.assertFalse(SS.as_of(index, t[159]))
            cutoff = t[162]
            full = SS.as_of(index, cutoff)
            prefix = SS.as_of(SS.reconstruct_levels(df[df.time < cutoff]), cutoff)
            self.assertEqual(full, prefix)
            self.assertIsNone(full[0].swept_at)
            self.assertIsNotNone(SS.as_of(index, t[-1])[0].swept_at)
            self.assertEqual(SS.reconstruct_levels(df.drop(index=20)), [])


if __name__ == "__main__":
    unittest.main()
