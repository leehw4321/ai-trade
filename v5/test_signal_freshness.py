"""Signal provenance checks use mocked data only; never touch the live account or ledgers."""
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import shadow
import strategy as st


class SignalFreshness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.end = 18000
        self.now = self.end + shadow.SETTLE + 30
        self.worker = shadow.Shadow.__new__(shadow.Shadow)
        self.worker.base = st.Params(tf=15)
        self.worker.cfg = {}
        self.worker.markets = {'KRW-A': {'warning': False}}
        self.worker.vars, self.worker.ledgers = {}, {}
        self.worker.base_ranked = [{'market': 'KRW-A', 'rank': 1, 'rs': .1,
            'bar': shadow.utc(self.end - 900).isoformat(), 'close': 100., 'atr': 1.}]
        self.worker.rs_out, self.worker.entry_ok = {}, {'KRW-A'}
        self.worker.prop = None
        self.worker.signal_closed_at = self.end
        self.worker.signal_tf_minutes = 15
        self.worker.evaluated_at = self.end + shadow.SETTLE + 1

    def test_current_signal_metadata_is_written_without_relabelling_source(self):
        with patch.object(shadow, 'DATA', self.root), patch('shadow.time.time', return_value=self.now), \
                patch('shadow.upbit.accounts', return_value=[{'currency': 'KRW', 'balance': '20000'}]), \
                patch('shadow.now_iso', return_value=shadow.utc(self.now).isoformat()):
            self.worker.make_proposal()
        proposal = json.loads((self.root / 'proposal.json').read_text(encoding='utf-8'))
        self.assertEqual(proposal['signal_closed_at'], self.end)
        self.assertEqual(proposal['signal_tf_minutes'], 15)
        self.assertEqual(proposal['evaluated_at'], self.end + shadow.SETTLE + 1)

    def test_stale_cache_cannot_gain_fresh_generation_timestamp_or_read_account(self):
        self.worker.signal_closed_at -= 900
        self.worker.evaluated_at -= 900
        with patch('shadow.time.time', return_value=self.now), patch('shadow.upbit.accounts') as accounts:
            with self.assertRaisesRegex(ValueError, '최신'):
                self.worker.make_proposal()
        accounts.assert_not_called()
        self.assertFalse((self.root / 'proposal.json').exists())

    def test_missing_nonfinite_future_and_mismatched_metadata_are_rejected(self):
        for field, value in [('signal_closed_at', None), ('signal_closed_at', float('nan')),
                             ('signal_closed_at', float('inf')), ('signal_closed_at', self.end + 900),
                             ('signal_closed_at', True), ('signal_tf_minutes', 60),
                             ('evaluated_at', self.now + 1), ('evaluated_at', self.end - 1)]:
            with self.subTest(field=field, value=value):
                original = getattr(self.worker, field)
                setattr(self.worker, field, value)
                with self.assertRaises(ValueError): self.worker.proposal_signal(self.now)
                setattr(self.worker, field, original)

    def test_settle_boundary_switches_expected_source_after_twenty_seconds(self):
        next_end = self.end + 900
        self.assertEqual(self.worker.proposal_signal(next_end + shadow.SETTLE - .01)['signal_closed_at'], self.end)
        with self.assertRaises(ValueError):
            self.worker.proposal_signal(next_end + shadow.SETTLE)

    def test_boundary_crossed_during_account_lookup_does_not_overwrite_existing_plan(self):
        existing = '{"preserve": "older record"}'
        (self.root / 'proposal.json').write_text(existing, encoding='utf-8')
        with patch.object(shadow, 'DATA', self.root), \
                patch('shadow.time.time', side_effect=[self.now, self.end + 920]), \
                patch('shadow.upbit.accounts', return_value=[]):
            with self.assertRaisesRegex(ValueError, '최신'):
                self.worker.make_proposal()
        self.assertEqual((self.root / 'proposal.json').read_text(encoding='utf-8'), existing)

    def test_failed_decide_invalidates_old_cache_provenance(self):
        with patch('shadow.upbit.krw_markets', side_effect=TimeoutError('offline')):
            with self.assertRaises(TimeoutError): self.worker.decide(self.end + 900)
        self.assertIsNone(self.worker.signal_closed_at)
        self.assertIsNone(self.worker.evaluated_at)
        with self.assertRaises(ValueError): self.worker.proposal_signal(self.now)

    def test_successful_decide_commits_provenance_only_when_all_work_finishes(self):
        b = [{'t': shadow.utc(self.end - 900).isoformat(), 'o': 100., 'h': 101., 'l': 99., 'c': 100.}]
        with patch('shadow.upbit.krw_markets', return_value={'KRW-A': {'warning': False}}), \
                patch('shadow.upbit.tickers', return_value={'KRW-A': {'acc_trade_price_24h': 1., 'trade_price': 100.}}), \
                patch('shadow.upbit.accounts', return_value=[]), \
                patch('shadow.upbit.completed_candles', return_value=b), \
                patch('shadow.st.rank', return_value=self.worker.base_ranked), \
                patch('shadow.time.sleep'), patch('shadow.time.time', return_value=self.now):
            self.worker.decide(self.end)
        self.assertEqual(self.worker.proposal_signal(self.now), {
            'signal_closed_at': self.end, 'signal_tf_minutes': 15, 'evaluated_at': self.now})

    def test_ranked_bar_must_match_source_close(self):
        self.worker.base_ranked[0]['bar'] = shadow.utc(self.end - 1800).isoformat()
        with self.assertRaisesRegex(ValueError, '불일치'):
            self.worker.proposal_signal(self.now)


if __name__ == '__main__':
    unittest.main()
