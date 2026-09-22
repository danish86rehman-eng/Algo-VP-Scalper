"""Tracked entry timing, fail-closed persistence and mirrored lifecycle tests."""
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import hashlib
import json

import pandas as pd

from scalper.tracked_pullback import Book, legacy_required
from scalper.trigger_engine import SATrigger, SATriggerEngine, MicroLiquidity
import backtest_scalper as sim


def fixture(sell=False):
    rows = [[102., 103., 101., 102.] for _ in range(29)]
    rows[20] = [102., 105., 101., 102.]
    rows[26] = [102.5, 103., 101.5, 102.]
    rows[27] = [102., 108., 102., 107.5]
    rows[28] = [107.5, 108., 104., 107.]
    m15 = pd.DataFrame(rows, columns=['open', 'high', 'low', 'close'])
    m15['time'] = pd.date_range('2026-09-01', periods=29, freq='15min', tz='UTC')
    broken = m15.time.iloc[28]
    formed = broken+pd.Timedelta(minutes=15)
    m5 = pd.DataFrame(dict(time=pd.date_range(broken, formed, freq='5min'),
                           open=107., high=108., low=106., close=107.))
    m5.loc[3, ['open', 'high', 'low', 'close']] = [103.3, 104.2, 103.25, 103.75]
    trigger = SATrigger(True, 'SWEEP_REJECTION', 'BULLISH', 107.5, 99., 125., 130., 'HIGH')
    if sell:
        for frame in (m15, m5):
            old = frame.copy()
            frame['open'], frame['close'] = 220-old.open, 220-old.close
            frame['high'], frame['low'] = 220-old.low, 220-old.high
        trigger.direction = 'BEARISH'
        trigger.entry_price, trigger.stop_loss, trigger.tp1, trigger.tp2 = 112.5, 121., 95., 90.
    return m15, m5, broken, formed, trigger


def seed(book, sell=False):
    m15, m5, broken, formed, trigger = fixture(sell)
    book.observe('XAUUSD', trigger, m15, broken)
    return m15, m5, broken, formed, trigger


class TrackedPullbackTests(unittest.TestCase):
    def test_mirrored_sequence_without_trigger_redetection(self):
        for sell in (False, True):
            with self.subTest(sell=sell):
                book = Book()
                m15, m5, broken, formed, _ = seed(book, sell)
                s = next(iter(book.setups.values()))
                self.assertEqual(s.state, 'FORMING')
                self.assertFalse(book.select('XAUUSD', SATrigger(), 107., 107., broken).detected)
                now = formed+pd.Timedelta(minutes=5)
                price = 116.25 if sell else 103.75
                book.advance('XAUUSD', m15, m5, now, price, price)
                self.assertEqual(s.state, 'READY', s.record())
                picked = book.select('XAUUSD', SATrigger(), price, price, now)
                self.assertTrue(picked.detected)
                self.assertEqual(picked.stop_loss, 121. if sell else 99.)
                self.assertTrue(book.reserve(s.setup_id, price, now))
                self.assertEqual(s.state, 'ATTEMPTED')
                self.assertFalse(book.reserve(s.setup_id, price, now))

    def test_waits_for_candle_three_and_freezes_zone(self):
        book = Book()
        m15, m5, broken, formed, _ = seed(book)
        s = next(iter(book.setups.values()))
        book.advance('XAUUSD', m15, m5, broken+pd.Timedelta(minutes=10))
        self.assertEqual(s.state, 'FORMING')
        book.advance('XAUUSD', m15, m5, formed)
        self.assertEqual((s.state, s.zone_low, s.zone_high), ('WAIT_PULLBACK', 103., 104.))
        sid = s.setup_id
        m15.loc[28, 'low'] = 102.
        book.advance('XAUUSD', m15, m5, formed)
        self.assertEqual((sid, s.zone_low, s.zone_high), (s.setup_id, 103., 104.))

    def test_ob_fallback_only_immediately_preceding_candle(self):
        for opposite in (True, False):
            book = Book()
            m15, m5, broken, formed, trigger = fixture()
            if not opposite:
                m15.loc[26, ['open', 'close']] = [102., 102.5]
            m15.loc[28, 'low'] = 102.
            book.observe('XAUUSD', trigger, m15, broken)
            book.advance('XAUUSD', m15, m5, formed)
            s = next(iter(book.setups.values()))
            self.assertEqual(s.zone_kind if opposite else s.state, 'OB' if opposite else 'INVALIDATED')

    def test_rejection_edges_and_inconclusive_touch(self):
        cases = [([103.7,104.2,103.6,103.9], False), # shallow
                 ([103.3,104.2,103.2,103.3], False), # doji
                 ([103.7,104.2,103.2,103.6], False), # wrong body
                 ([103.3,104.2,103.2,103.5], False), # midpoint close equality
                 ([103.5,104.2,103.5,103.8], True)] # depth equality allowed
        for values, ready in cases:
            for sell in (False, True):
                with self.subTest(values=values, sell=sell):
                    book = Book()
                    m15, m5, _, formed, _ = seed(book, sell)
                    o,h,l,c = values
                    m5.loc[3, ['open','high','low','close']] = ([220-o,220-l,220-h,220-c] if sell else values)
                    book.advance('XAUUSD', m15, m5, formed+pd.Timedelta(minutes=5))
                    s = next(iter(book.setups.values()))
                    self.assertEqual(s.state, 'READY' if ready else 'WAIT_PULLBACK')

    def test_invalidation_overrides_rejection_and_no_ob_replacement(self):
        for field, value, reason in [('close',102.9,'DISTAL_CLOSE'),('low',98.,'STOP'),('high',126.,'TARGET')]:
            book = Book()
            m15,m5,_,formed,_ = seed(book)
            m5.loc[3, field] = value
            if field == 'close':
                m5.loc[3, 'low'] = 102.8
            book.advance('XAUUSD',m15,m5,formed+pd.Timedelta(minutes=5))
            s = next(iter(book.setups.values()))
            self.assertEqual((s.state,s.reason,s.zone_kind),('INVALIDATED',reason,'FVG'))

    def test_ready_retry_then_missed_not_rearmed(self):
        book = Book()
        m15,m5,_,formed,_ = seed(book)
        now = formed+pd.Timedelta(minutes=5)
        book.advance('XAUUSD',m15,m5,now)
        s = next(iter(book.setups.values()))
        self.assertFalse(s.quote_allowed(105.,now))
        self.assertTrue(s.quote_allowed(103.8,now+pd.Timedelta(seconds=30)))
        book.advance('XAUUSD',m15,m5,now+pd.Timedelta(seconds=60))
        self.assertEqual(s.state,'MISSED_ENTRY')
        book.advance('XAUUSD',m15,m5,now+pd.Timedelta(seconds=70))
        self.assertEqual(s.state,'MISSED_ENTRY')

    def test_ready_invalidation_on_quote(self):
        book = Book()
        m15,m5,_,formed,_ = seed(book)
        now = formed+pd.Timedelta(minutes=5)
        book.advance('XAUUSD',m15,m5,now)
        book.advance('XAUUSD',m15,m5,now+pd.Timedelta(seconds=30),98.,98.1)
        self.assertEqual(next(iter(book.setups.values())).state,'INVALIDATED')

    def test_ready_can_finish_across_setup_expiry(self):
        book = Book()
        m15,m5,_,formed,_ = seed(book)
        s = next(iter(book.setups.values()))
        now = formed+pd.Timedelta(minutes=5)
        s.expires_at = (now+pd.Timedelta(seconds=10)).isoformat()
        book.advance('XAUUSD',m15,m5,now)
        book.advance('XAUUSD',m15,m5,now+pd.Timedelta(seconds=30))
        self.assertEqual(s.state,'READY')
        self.assertTrue(book.reserve(s.setup_id,103.8,now+pd.Timedelta(seconds=30)))

    def test_expiry_boundary_blocks_promotion(self):
        book = Book()
        m15,m5,_,formed,_ = seed(book)
        s = next(iter(book.setups.values()))
        s.expires_at = (formed+pd.Timedelta(minutes=5)).isoformat()
        book.advance('XAUUSD',m15,m5,formed+pd.Timedelta(minutes=5))
        self.assertEqual(s.state,'EXPIRED')

    def test_missing_bar_blocks_entry(self):
        book = Book()
        m15,m5,_,formed,_ = seed(book)
        book.advance('XAUUSD',m15,m5.drop(index=1),formed+pd.Timedelta(minutes=5))
        s = next(iter(book.setups.values()))
        self.assertFalse(s.history_ready)
        self.assertFalse(book.select('XAUUSD',SATrigger(),103.8,103.8,formed+pd.Timedelta(minutes=5)).detected)

    def test_repeat_observations_are_not_unique_setups(self):
        book = Book()
        m15,_,broken,_,trigger = seed(book)
        book.observe('XAUUSD',trigger,m15,broken)
        self.assertEqual(len(book.setups),1)
        self.assertEqual(book.counts['UNIQUE_BREAKS'],1)
        self.assertEqual(book.counts['REPEATED_WAITING_OBSERVATIONS'],1)

    def test_restart_absolute_ready_timer_and_uncertain_reservation(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'book.json'
            book = Book(path)
            book.reconcile([],[])
            m15,m5,_,formed,_ = seed(book)
            now = formed+pd.Timedelta(minutes=5)
            book.advance('XAUUSD',m15,m5,now)
            recovered = Book(path)
            self.assertFalse(recovered.reconciled)
            recovered.reconcile([],[])
            recovered.advance('XAUUSD',m15,m5,now+pd.Timedelta(seconds=30))
            s = next(iter(recovered.setups.values()))
            self.assertTrue(recovered.reserve(s.setup_id,103.8,now+pd.Timedelta(seconds=30)))
            again = Book(path)
            again.reconcile([],[])
            self.assertEqual(next(iter(again.setups.values())).state,'ATTEMPTED')

    def test_restart_after_ready_window_does_not_resume_timer(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'book.json'
            book = Book(path)
            m15,m5,_,formed,_ = seed(book)
            now = formed+pd.Timedelta(minutes=5)
            book.advance('XAUUSD',m15,m5,now)
            book = Book(path)
            book.reconcile([],[])
            book.advance('XAUUSD',m15,m5,now+pd.Timedelta(seconds=65))
            self.assertEqual(next(iter(book.setups.values())).state,'MISSED_ENTRY')

    def test_failed_atomic_save_prevents_submission(self):
        with TemporaryDirectory() as directory:
            book = Book(Path(directory)/'book.json')
            book.reconcile([],[])
            m15,m5,_,formed,_ = seed(book)
            now = formed+pd.Timedelta(minutes=5)
            book.advance('XAUUSD',m15,m5,now)
            s = next(iter(book.setups.values()))
            with patch('scalper.tracked_pullback.os.replace',side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    book.reserve(s.setup_id,103.8,now)
            recovered = Book(book.path)
            recovered.reconcile([],[])
            self.assertEqual(next(iter(recovered.setups.values())).state,'ATTEMPTED')

    def test_missing_broker_history_blocks_reconciliation(self):
        book = Book()
        book.reconcile(None,[])
        self.assertFalse(book.reconciled)

    def test_native_contract_and_legacy_arms(self):
        book = Book()
        native = SATrigger(True,'HTF_CRT_SWEEP',htf_crt=object())
        self.assertIs(book.select('XAUUSD',native,1.,1.,pd.Timestamp('2026-09-01',tz='UTC')),native)
        self.assertFalse(legacy_required(True,True,native))
        self.assertTrue(legacy_required(True,False,native))

    def test_wick_only_break_never_seeds(self):
        book = Book()
        m15,_,broken,_,trigger = fixture()
        m15.loc[27,'close'] = 104.
        book.observe('XAUUSD',trigger,m15,broken)
        self.assertFalse(book.setups)

    def test_gap_inside_frozen_pivot_to_break_evidence_blocks_setup(self):
        book = Book()
        m15,_,broken,_,trigger = fixture()
        book.observe('XAUUSD',trigger,m15.drop(index=22),broken)
        self.assertFalse(book.setups)
        self.assertEqual(book.counts['BREAK_HISTORY_MISSING'],1)

    def test_gap_before_frozen_evidence_window_is_irrelevant(self):
        book = Book()
        m15,_,broken,_,trigger = fixture()
        book.observe('XAUUSD',trigger,m15.drop(index=10),broken)
        self.assertEqual(len(book.setups),1)

    def test_malformed_source_is_controlled_fail_closed(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'book.json'
            book = Book(path)
            seed(book)
            envelope = json.loads(path.read_text(encoding='utf-8'))
            payload = json.loads(envelope['payload'])
            del payload['setups'][0]['source']['stop_loss']
            encoded = json.dumps(payload,sort_keys=True,allow_nan=False)
            path.write_text(json.dumps(dict(payload=encoded,
                sha256=hashlib.sha256(encoded.encode()).hexdigest())),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'PULLBACK_STORE_MALFORMED'):
                Book(path)

    def test_stale_marker_cannot_overwrite_terminal_state(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'book.json'
            book = Book(path)
            seed(book)
            setup = next(iter(book.setups.values()))
            book.transition(setup,'INVALIDATED','TEST')
            book.save()
            path.with_name(setup.setup_id+'.attempt').write_text('reserved',encoding='utf-8')
            recovered = Book(path)
            recovered.reconcile([],[])
            self.assertEqual(next(iter(recovered.setups.values())).state,'INVALIDATED')

    def test_replay_pnl_uses_snapshot_without_late_mt5_call(self):
        info = SimpleNamespace(trade_tick_size=.001,trade_tick_value=.1)
        with patch.object(sim.mt5,'symbol_info',side_effect=AssertionError('late metadata call')):
            self.assertAlmostEqual(sim._order_pnl('XAUUSD','BULLISH',.1,4300.,4301.,info),10.)
            self.assertAlmostEqual(sim._order_pnl('XAUUSD','BEARISH',.1,4301.,4300.,info),10.)
        with self.assertRaises(RuntimeError):
            sim._order_pnl('XAUUSD','BULLISH',.1,4300.,4301.,SimpleNamespace(
                trade_tick_size=0.,trade_tick_value=.1))


if __name__ == '__main__':
    unittest.main()
