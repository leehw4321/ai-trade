"""Offline executable-ask enforcement of the existing cap; no live files or APIs."""
import copy
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

STAGED = Path(__file__).resolve().parent
sys.path.insert(0, 'C:/Users/User/Desktop/ai-trade/v5')


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, STAGED / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


live = module('staged_live_context_review', 'live.py')
shadow = module('staged_shadow_context_review', 'shadow.py')


class EntryContext(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worker = live.Live(self.root / 'live', {'enabled': True}, live.st.Params())
        self.markets = {'KRW-A': {'warning': False}}
        self.end, self.now = 18000, 18050
        self.clock = patch.object(live.time, 'time', return_value=self.now)
        self.clock.start(); self.addCleanup(self.clock.stop)
        self.context = {'market': 'KRW-A', 'signal_close': 110., 'anchor_close': 100., 'rs': .1,
                        'signal_closed_at': self.end, 'signal_tf_minutes': 15, 'evaluated_at': 18025}
        for name in ('accounts', 'tickers', 'orderbooks', 'completed_candles', 'market_buy', 'market_sell'):
            block = patch.object(live.upbit, name, side_effect=AssertionError('unexpected external call: ' + name))
            block.start(); self.addCleanup(block.stop)

    def test_existing_cap_is_enforced_at_actual_ask_including_exact_boundary(self):
        boundary = self.context['anchor_close'] * (1 + self.worker.p.chase_rs_cap)
        self.assertIsNone(self.worker.entry_chase_block('KRW-A', self.context, 111.))
        self.assertIsNone(self.worker.entry_chase_block('KRW-A', self.context, math.nextafter(boundary, 0)))
        self.assertIn('과열', self.worker.entry_chase_block('KRW-A', self.context, boundary))
        self.assertIn('과열', self.worker.entry_chase_block('KRW-A', self.context, 120.))
        self.assertEqual(self.worker.p.chase_rs_cap, .15)

    def test_receiver_cap_wins_over_any_sender_claim(self):
        context = {**self.context, 'chase_rs_cap': .99}
        self.assertIn('과열', self.worker.entry_chase_block('KRW-A', context, 120.))
        self.worker.p.chase_rs_cap = .12
        self.assertIn('과열', self.worker.entry_chase_block('KRW-A', context, 113.))

    def test_disabled_cap_still_requires_valid_context_for_automatic_buy(self):
        self.worker.p.chase_rs_cap = 0.
        self.assertIsNone(self.worker.entry_chase_block('KRW-A', self.context, 130.))
        self.assertIn('출처', self.worker.buy_block('KRW-A', 10000, self.markets,
                                                   entry_context=None, validate_entry=True))

    def test_missing_stale_future_nonfinite_and_mismatched_context_fail_closed(self):
        self.assertIn('출처', self.worker.entry_chase_block('KRW-A', None))
        mutations = [('market', 'KRW-B'), ('signal_close', float('nan')),
                     ('signal_close', True), ('anchor_close', 0.), ('anchor_close', float('inf')),
                     ('rs', float('nan')), ('rs', -.1), ('rs', .2),
                     ('signal_closed_at', self.end - 900), ('signal_closed_at', self.end + 900),
                     ('signal_closed_at', True), ('signal_tf_minutes', True),
                     ('signal_tf_minutes', 60), ('evaluated_at', self.now + 1),
                     ('evaluated_at', self.end), ('evaluated_at', float('nan'))]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                self.assertIsNotNone(self.worker.entry_chase_block('KRW-A', {**self.context, key: value}, 111.))
        for ask in (None, True, 0., -1., float('nan'), float('inf')):
            with self.subTest(ask=ask):
                self.assertIsNotNone(self.worker.entry_chase_block('KRW-A', self.context, ask))

    def test_final_check_after_atr_blocks_new_price_spike_before_journal_or_post(self):
        with patch.object(live.upbit, 'accounts', return_value=[{'currency': 'KRW', 'balance': '20000'}]), \
             patch.object(live.upbit, 'orderbooks', side_effect=[{'KRW-A': {'ask': 111., 'bid': 110.9}},
                                                               {'KRW-A': {'ask': 120., 'bid': 119.9}}]), \
             patch.object(live.upbit, 'completed_candles', return_value=[]), \
             patch.object(live.st, 'wilder_atr', return_value=1.), \
             patch.object(live.upbit, 'market_buy') as buy, patch.object(self.worker, 'save') as save:
            self.assertEqual(self.worker.buy_block('KRW-A', 10000, self.markets,
                                                   entry_context=self.context, validate_entry=True), '')
            with self.assertRaisesRegex(ValueError, '과열'):
                self.worker._trade('bid', 'KRW-A', 10000, 'auto',
                                   entry_context=self.context, validate_entry=True)
        buy.assert_not_called(); save.assert_not_called()
        self.assertFalse(self.worker.d.get('pending_order'))
        self.assertEqual(self.worker.d['positions'], {})

    def test_boundary_crossed_during_atr_lookup_blocks_submission(self):
        def atr_fetch(*args):
            live.time.time.return_value = self.end + 920
            return []
        with patch.object(live.upbit, 'completed_candles', side_effect=atr_fetch), \
             patch.object(live.st, 'wilder_atr', return_value=1.), \
             patch.object(live.upbit, 'orderbooks', return_value={'KRW-A': {'ask': 111., 'bid': 110.9}}), \
             patch.object(live.upbit, 'market_buy') as buy:
            with self.assertRaisesRegex(ValueError, '최신'):
                self.worker._trade('bid', 'KRW-A', 10000, 'auto',
                                   entry_context=self.context, validate_entry=True)
        buy.assert_not_called()
        self.assertFalse(self.worker.d.get('pending_order'))

    def test_valid_pre_submit_context_still_reaches_existing_trade_accounting(self):
        order = {'uuid': 'test-only', 'state': 'done', 'executed_volume': '100', 'paid_fee': '5.5',
                 'trades': [{'volume': '100', 'funds': '11000'}]}
        with patch.object(live.upbit, 'completed_candles', return_value=[]), \
             patch.object(live.st, 'wilder_atr', return_value=1.), \
             patch.object(live.upbit, 'orderbooks', return_value={'KRW-A': {'ask': 110., 'bid': 109.9}}), \
             patch.object(live.upbit, 'market_buy', return_value=order) as buy, \
             patch.object(self.worker, '_wait', return_value=order):
            self.worker._trade('bid', 'KRW-A', 11000, 'auto',
                               entry_context=self.context, validate_entry=True)
        self.assertEqual(buy.call_count, 1)
        self.assertEqual(self.worker.d['positions']['KRW-A']['qty'], 100)
        self.assertIsNone(self.worker.d['pending_order'])

    def test_auto_execute_without_context_never_falls_back_to_manual_path(self):
        with patch.object(live.upbit, 'accounts', return_value=[{'currency': 'KRW', 'balance': '20000'}]), \
             patch.object(live.upbit, 'tickers', return_value={'KRW-A': {'trade_price': 110.}}), \
             patch.object(self.worker, '_trade') as trade:
            result = self.worker.execute(('p', '매수 A 10000원'), self.markets, automatic=True)
        self.assertIn('출처', result); trade.assert_not_called()

    def test_manual_without_context_explicitly_skips_when_cap_enabled(self):
        with patch.object(live.upbit, 'accounts', return_value=[{'currency': 'KRW', 'balance': '20000'}]), \
             patch.object(live.upbit, 'tickers', return_value={'KRW-A': {'trade_price': 110.}}), \
             patch.object(self.worker, '_trade') as trade:
            result = self.worker.execute(('p', '매수 A 10000원'), self.markets)
        self.assertIn('출처', result); trade.assert_not_called()

    def test_loss_reentry_after_six_hours_does_not_require_atr_dip(self):
        self.worker.d['last_sell'] = {'KRW-A': {'time': self.now - 6 * 3600 - 1,
                                               'price': 100., 'profit': False}}
        with patch.object(live.upbit, 'accounts', return_value=[{'currency': 'KRW', 'balance': '20000'}]), \
             patch.object(live.upbit, 'orderbooks', return_value={'KRW-A': {'ask': 111., 'bid': 110.9}}), \
             patch.object(live.upbit, 'completed_candles') as candles:
            self.assertEqual(self.worker.buy_block('KRW-A', 10000, self.markets), '')
        candles.assert_not_called()

    def test_future_or_nonfinite_last_sale_blocks_before_account_access(self):
        for when in (self.now + 1, float('nan'), True):
            self.worker.d['last_sell'] = {'KRW-A': {'time': when, 'price': 100., 'profit': False}}
            why = self.worker.buy_block('KRW-A', 10000, self.markets)
            self.assertIn('기록', why)

    def test_sender_emits_same_source_anchor_instead_of_current_account_price(self):
        worker = shadow.Shadow.__new__(shadow.Shadow)
        worker.base, worker.cfg, worker.markets = live.st.Params(), {}, {}
        worker.base_ranked = [{'market': 'KRW-A', 'rank': 1, 'close': 110., 'rs': .1,
                              'bar': shadow.utc(self.end - 900).isoformat()}]
        worker.signal_closed_at, worker.signal_tf_minutes = self.end, 15
        worker.evaluated_at = self.end + 25
        worker.entry_ok, worker.rs_out, worker.prop = {'KRW-A'}, {}, None
        with patch.object(shadow, 'DATA', self.root), \
             patch.object(shadow.upbit, 'accounts', return_value=[{'currency': 'KRW', 'balance': '20000'}]):
            worker.make_proposal()
        proposal = json.loads((self.root / 'proposal.json').read_text(encoding='utf-8'))
        source = proposal['entry_context']['KRW-A']
        self.assertEqual(source['signal_close'], 110.)
        self.assertAlmostEqual(source['anchor_close'], 100.)
        self.assertEqual(source['signal_closed_at'], self.end)


if __name__ == '__main__':
    unittest.main()
