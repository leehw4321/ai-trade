import datetime as dt
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import urllib.error
from unittest.mock import Mock, patch

import bot
import live
import strategy as st
import upbit


class Rejection(unittest.TestCase):
    def test_definitive_exchange_rejection_is_typed(self):
        error = urllib.error.HTTPError('https://example.invalid', 400, 'bad request', {},
            io.BytesIO(b'{"error":{"name":"insufficient_funds_bid"}}'))
        self.addCleanup(error.close)
        with patch.object(upbit, 'ORDER_GUARD', None), patch.object(upbit, '_auth', return_value={}), \
                patch.object(upbit.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaises(upbit.OrderRejected) as caught:
                upbit.market_buy('KRW-A', 10000, identifier='only-once')
        self.assertEqual(caught.exception.name, 'insufficient_funds_bid')

    def test_duplicate_identifier_is_not_treated_as_definite_nonacceptance(self):
        error = urllib.error.HTTPError('https://example.invalid', 400, 'bad request', {},
            io.BytesIO(b'{"error":{"name":"duplicated_identifier"}}'))
        self.addCleanup(error.close)
        with patch.object(upbit, 'ORDER_GUARD', None), patch.object(upbit, '_auth', return_value={}), \
                patch.object(upbit.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                upbit.market_buy('KRW-A', 10000, identifier='only-once')
        self.assertNotIsInstance(caught.exception, upbit.OrderRejected)

    def test_server_and_rate_limit_errors_remain_uncertain(self):
        for status in (429, 500):
            with self.subTest(status=status):
                error = urllib.error.HTTPError('https://example.invalid', status, 'temporary', {},
                    io.BytesIO(b'{"error":{"name":"temporary_failure"}}'))
                self.addCleanup(error.close)
                with patch.object(upbit, 'ORDER_GUARD', None), patch.object(upbit, '_auth', return_value={}), \
                        patch.object(upbit.urllib.request, 'urlopen', side_effect=error):
                    with self.assertRaises(RuntimeError) as caught:
                        upbit.market_buy('KRW-A', 10000, identifier='only-once')
                self.assertNotIsInstance(caught.exception, upbit.OrderRejected)


class Reliability(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lv = live.Live(Path(self.tmp.name) / 'live', {'enabled': True, 'auto_proposal': True}, st.Params())
        self.lv.d['positions'] = {'KRW-A': {'qty': 100., 'cost': 10000., 'stop': 90., 'time': 't'}}

    def tearDown(self):
        self.tmp.cleanup()

    def pending(self, market='KRW-A'):
        return {'identifier': 'uncertain-' + market, 'market': market, 'side': 'bid',
                'reason': 'plan', 'time': 't', 'atr': 1.}

    def test_definite_rejection_releases_intent_but_does_not_replay(self):
        with patch.object(upbit, 'market_sell', side_effect=upbit.OrderRejected(400, 'insufficient_funds_ask', 'reject')) as post:
            with self.assertRaises(upbit.OrderRejected):
                self.lv._trade('ask', 'KRW-A', 100, 'exit')
        self.assertEqual(post.call_count, 1)
        self.assertIsNone(self.lv.d.get('pending_order'))
        self.assertIsNone(json.loads(self.lv.file.read_text(encoding="utf-8"))['pending_order'])
        self.assertEqual(self.lv.d['positions']['KRW-A']['qty'], 100)

    def test_nan_atr_rejects_before_intent_or_order_transmission(self):
        with patch.object(upbit, 'completed_candles', return_value=[]), \
                patch.object(st, 'wilder_atr', return_value=float('nan')), \
                patch.object(upbit, 'market_buy') as post:
            with self.assertRaisesRegex(ValueError, 'ATR'):
                self.lv._trade('bid', 'KRW-B', 10000, 'buy')
        post.assert_not_called()
        self.assertFalse(self.lv.d.get('pending_order'))
        self.assertFalse(self.lv.file.exists())

    def test_invalid_risk_prices_do_not_trigger_exchange_calls(self):
        with patch.object(upbit, 'accounts') as account, patch.object(upbit, 'market_sell') as post:
            for value in (float('nan'), float('inf'), 0., -10., True):
                self.lv.stop_check({'KRW-A': value})
        account.assert_not_called()
        post.assert_not_called()

    def test_timeout_remains_durable_and_blocks_all_new_buys(self):
        with patch.object(upbit, 'market_sell', side_effect=TimeoutError) as post:
            with self.assertRaises(TimeoutError):
                self.lv._trade('ask', 'KRW-A', 100, 'exit')
            with self.assertRaisesRegex(RuntimeError, '미확정'):
                self.lv._trade('bid', 'KRW-B', 10000, 'buy')
        self.assertEqual(post.call_count, 1)
        self.assertTrue(json.loads(self.lv.file.read_text(encoding="utf-8"))['pending_order']['identifier'])

    def test_unresolved_primary_does_not_block_other_market_stop(self):
        self.lv.d['pending_order'] = self.pending()
        self.lv.d['positions']['KRW-B'] = {'qty': 100., 'cost': 10000., 'stop': 90., 'time': 't'}
        terminal = {'uuid': 'risk-id', 'market': 'KRW-B', 'side': 'ask', 'state': 'done',
                    'paid_fee': '4', 'executed_volume': '100', 'trades': [{'volume': '100', 'funds': '8000'}]}
        balances = [{'currency': 'A', 'balance': '100'}, {'currency': 'B', 'balance': '100'}]
        with patch.object(upbit, 'get_order_by_identifier', side_effect=LookupError('not yet visible')), \
                patch.object(upbit, 'accounts', return_value=balances), \
                patch.object(upbit, 'market_sell', return_value=terminal) as post, \
                patch.object(self.lv, '_wait', return_value=terminal), patch.object(self.lv, 'share'):
            self.lv.stop_check({'KRW-A': 80., 'KRW-B': 80.})
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[:2], ('KRW-B', 100.))
        self.assertIn('KRW-A', self.lv.d['positions'])
        self.assertNotIn('KRW-B', self.lv.d['positions'])
        self.assertTrue(self.lv.d['pending_order'])
        self.assertFalse(self.lv.d['risk_pending_orders'])

    def test_uncertain_risk_order_is_not_reposted(self):
        self.lv.d['pending_order'] = self.pending('KRW-OTHER')
        with patch.object(upbit, 'get_order_by_identifier', side_effect=LookupError('pending')), \
                patch.object(upbit, 'accounts', return_value=[{'currency': 'A', 'balance': '100'}]), \
                patch.object(upbit, 'market_sell', side_effect=TimeoutError) as post:
            self.lv.stop_check({'KRW-A': 80.})
            self.lv.stop_check({'KRW-A': 80.})
        self.assertEqual(post.call_count, 1)
        self.assertIn('KRW-A', self.lv.d['risk_pending_orders'])
        saved = json.loads(self.lv.file.read_text(encoding="utf-8"))
        self.assertTrue(saved['risk_pending_orders']['KRW-A']['identifier'])

    def test_risk_partial_cancel_preserves_remaining_cost_and_quantity(self):
        self.lv.d['risk_pending_orders'] = {'KRW-A': {**self.pending(), 'side': 'ask'}}
        partial = {'state': 'wait', 'paid_fee': '2', 'trades': [{'volume': '40', 'funds': '3200'}]}
        self.lv._apply_pending(partial, 'KRW-A')
        self.lv._apply_pending(partial, 'KRW-A')
        self.assertEqual(self.lv.d['positions']['KRW-A']['qty'], 60.)
        partial['state'] = 'cancel'
        self.lv._apply_pending(partial, 'KRW-A')
        self.assertEqual(self.lv.d['positions']['KRW-A']['cost'], 6000.)
        self.assertAlmostEqual(self.lv.d['realized'], -802.)
        self.assertFalse(self.lv.d['risk_pending_orders'])

    def test_missing_mark_freezes_loss_evaluation_and_new_entries(self):
        self.lv.d['risk_baseline_unrealized'] = 30000.
        self.assertIsNone(self.lv.check_loss({}))
        self.assertEqual(self.lv.d['halted'], '')
        with patch.object(upbit, 'accounts') as lookup:
            self.assertIn('가격 미확인', self.lv.buy_block('KRW-B', 10000, {'KRW-B': {}}))
        lookup.assert_not_called()
        self.lv.d['risk_baseline_unrealized'] = self.lv.unrealized({'KRW-A': 100.})
        self.assertAlmostEqual(self.lv.check_loss({'KRW-A': 100.}), 0.)
        self.assertNotIn('valuation_incomplete', self.lv.d)

    def test_cumulative_cap_reserves_fee(self):
        self.lv.d['bought_krw'] = 70000.
        self.assertIn('누적 매수 한도', self.lv.buy_block('KRW-A', 30000, {'KRW-A': {}}))

    def test_terminal_missing_details_does_not_mutate_before_validation(self):
        self.lv.d['pending_order'] = {**self.pending(), 'side': 'ask'}
        bad = {'state': 'done', 'executed_volume': '100', 'paid_fee': '2',
               'trades': [{'volume': '40', 'funds': '3200'}]}
        with self.assertRaisesRegex(ValueError, '상세 체결'):
            self.lv._apply_pending(bad)
        self.assertEqual(self.lv.d['positions']['KRW-A']['qty'], 100.)
        self.assertEqual(self.lv.d['realized'], 0.)
        self.assertNotIn('applied_volume', self.lv.d['pending_order'])

    def test_receiver_rejects_relabelled_old_source_signal(self):
        now = 3000.
        self.lv.d['account_adoption'] = {'epoch': 2000.}
        prop = {'time': dt.datetime.fromtimestamp(now - 1, dt.timezone.utc).isoformat(),
                'text': '실행 주문안: 매수 A 10000원', 'signal_tf_minutes': 15,
                'signal_closed_at': 1800., 'evaluated_at': 2990.}
        target = self.lv.folder.parent / 'proposal.json'
        target.write_text(json.dumps(prop), encoding='utf-8')
        with patch.object(live.time, 'time', return_value=now), patch.object(self.lv, 'execute') as execute:
            self.lv.poll_proposal({})
        execute.assert_not_called()
        self.assertFalse(self.lv.d['executed'])
        prop['signal_closed_at'] = 2700.
        target.write_text(json.dumps(prop), encoding='utf-8')
        with patch.object(live.time, 'time', return_value=now), \
                patch.object(self.lv, 'execute', return_value='ok') as execute, patch.object(self.lv, 'share'):
            self.lv.poll_proposal({})
        execute.assert_called_once()


class MonitorOrder(unittest.TestCase):
    def test_risk_checks_precede_optional_account_display(self):
        calls = []
        class EndLoop(BaseException):
            pass
        runner = bot.Bot.__new__(bot.Bot)
        runner.p = st.Params()
        runner.ledgers = {'P10': SimpleNamespace(ready=True, d={'positions': {}})}
        runner.live = SimpleNamespace(cfg={'enabled': True}, d={'positions': {'KRW-A': {}}},
            trail_check=lambda: None, check_resume_request=lambda prices: None, stop_check=lambda prices: calls.append('live-risk'), check_loss=lambda prices: calls.append('loss'))
        runner.align_timeframe = Mock()
        runner.log = Mock()
        runner.stop_check = lambda ledger, prices: calls.append('paper-risk')
        def display(close):
            calls.append('display')
            raise EndLoop()
        runner.refresh_display = display
        with patch.object(bot.subprocess, 'Popen'), patch.object(bot, 'STOP', SimpleNamespace(exists=lambda: False)), \
                patch.object(bot, 'STOP_ASCII', SimpleNamespace(exists=lambda: False)), \
                patch.object(bot.time, 'time', return_value=100.), \
                patch.object(upbit, 'tickers', return_value={'KRW-A': {'trade_price': 100.}}):
            with self.assertRaises(EndLoop):
                runner.run()
        self.assertEqual(calls, ['live-risk', 'loss', 'paper-risk', 'display'])


if __name__ == '__main__':
    unittest.main()
