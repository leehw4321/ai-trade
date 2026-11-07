import json
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

import live
import manual_orders as mo
import strategy as st
from session_guard import SessionLost


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class LiquidatePosition(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lv = live.Live(self.tmp.name, {'enabled': True}, st.Params())
        self.lv.d['positions']['KRW-X'] = {'qty': 10.0, 'cost': 9000.0, 'stop': 800.0, 'time': '2026-11-03T08:00:00+09:00'}
        self.accounts = [{'currency': 'X', 'balance': '10'}]

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, prices=None, trade=None, market='KRW-X'):
        prices = {'KRW-X': 1000.0} if prices is None else prices
        trade = trade or mock.Mock(side_effect=self.fake_trade)
        with mock.patch.object(self.lv, '_trade', trade), mock.patch('upbit.accounts', return_value=self.accounts):
            return self.lv.liquidate_position(market, prices), trade

    def fake_trade(self, side, market, amount, reason, risk=False):
        del self.lv.d['positions'][market]
        return {'state': 'done', 'trades': [{'volume': str(amount), 'funds': str(amount * 1000)}], 'paid_fee': '5'}

    def test_sells_whole_position_through_the_risk_path(self):
        (ok, msg), trade = self.run_it()
        self.assertTrue(ok, msg)
        trade.assert_called_once_with('ask', 'KRW-X', 10.0, '수동 강제 청산', risk=True)
        self.assertIn('9,995원', msg)

    def test_only_sells_what_the_exchange_really_holds(self):
        self.accounts = [{'currency': 'X', 'balance': '4'}]
        (ok, _), trade = self.run_it(prices={'KRW-X': 2000.0})
        self.assertTrue(ok)
        self.assertEqual(trade.call_args[0][2], 4.0)

    def test_rejections_never_place_an_order(self):
        cases = {
            'unmanaged': dict(market='KRW-NOPE', prices={'KRW-NOPE': 1000.0}),
            'no price': dict(prices={}),
            'bad price': dict(prices={'KRW-X': float('nan')}),
            'dust': dict(prices={'KRW-X': 100.0}),  # 10개 x 100원 = 1,000원 < 5,000원
        }
        for name, kw in cases.items():
            with self.subTest(name):
                (ok, msg), trade = self.run_it(**kw)
                self.assertFalse(ok)
                self.assertTrue(msg)
                trade.assert_not_called()

    def test_disabled_live_and_uncertain_order_block(self):
        with mock.patch.object(self.lv, 'market_has_uncertain_order', return_value=True):
            (ok, msg), trade = self.run_it()
        self.assertFalse(ok)
        self.assertIn('미확정', msg)
        trade.assert_not_called()
        self.lv.cfg['enabled'] = False
        (ok, _), trade = self.run_it()
        self.assertFalse(ok)
        trade.assert_not_called()

    def test_failure_is_reported_and_recorded_not_raised(self):
        (ok, msg), _ = self.run_it(trade=mock.Mock(side_effect=RuntimeError('거부')))
        self.assertFalse(ok)
        self.assertIn('청산 실패', msg)
        self.assertIn('KRW-X', self.lv.d['positions'])

    def test_lost_execution_right_propagates(self):
        with self.assertRaises(SessionLost):
            self.run_it(trade=mock.Mock(side_effect=SessionLost('다른 PC')))

    def test_unfilled_order_keeps_the_position(self):
        unfilled = mock.Mock(return_value={'state': 'cancel', 'trades': [], 'paid_fee': '0'})
        (ok, msg), _ = self.run_it(trade=unfilled)
        self.assertFalse(ok)
        self.assertIn('미체결', msg)


class Server(unittest.TestCase):
    def setUp(self):
        self.port = free_port()
        self.srv = mo.ManualOrderServer(self.port, token='tok-123')
        self.assertTrue(self.srv.start())

    def tearDown(self):
        self.srv.stop()

    def post(self, doc, origin='null', host=None, raw=None):
        body = raw if raw is not None else json.dumps(doc).encode()
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}/liquidate', data=body, method='POST',
                                     headers={'Content-Type': 'text/plain'})
        if origin is not None:
            req.add_header('Origin', origin)
        if host:
            req.add_header('Host', host)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def good(self, **kw):
        return {'token': 'tok-123', 'market': 'KRW-ABC', 'confirm': True, **kw}

    def bot(self, ok=True, message='done'):
        def work():
            for _ in range(200):
                for req in self.srv.drain():
                    req.finish(ok, message)
                    return
                threading.Event().wait(0.02)
        t = threading.Thread(target=work)
        t.start()
        return t

    def test_confirmed_request_is_executed_by_the_bot_loop_and_answered(self):
        t = self.bot(True, '청산 완료')
        code, doc = self.post(self.good())
        t.join()
        self.assertEqual((code, doc['ok'], doc['message']), (200, True, '청산 완료'))

    def test_bot_refusal_is_passed_back(self):
        t = self.bot(False, '미확정 주문')
        code, doc = self.post(self.good())
        t.join()
        self.assertEqual((code, doc['ok']), (200, False))

    def test_wrong_or_missing_token_is_rejected_and_nothing_is_queued(self):
        for doc in (self.good(token='nope'), {'market': 'KRW-ABC', 'confirm': True}):
            self.assertEqual(self.post(doc)[0], 403)
        self.assertEqual(self.srv.drain(), [])

    def test_foreign_origin_and_host_are_rejected(self):
        self.assertEqual(self.post(self.good(), origin='https://evil.example')[0], 403)
        self.assertEqual(self.post(self.good(), host='evil.example')[0], 403)
        self.assertEqual(self.srv.drain(), [])

    def test_unconfirmed_or_malformed_requests_are_rejected(self):
        self.assertEqual(self.post(self.good(confirm=False))[0], 400)
        self.assertEqual(self.post(self.good(confirm='true'))[0], 400)
        for market in ('BTC', 'KRW-', 'krw-abc', 'KRW-ABC;DROP', 'USDT-BTC', 5, None):
            self.assertEqual(self.post(self.good(market=market))[0], 400, market)
        self.assertEqual(self.post(None, raw=b'not json')[0], 400)
        self.assertEqual(self.post(None, raw=b'x' * 5000)[0], 413)
        self.assertEqual(self.srv.drain(), [])

    def test_same_market_cannot_be_requested_twice_within_30_seconds(self):
        t = self.bot()
        self.assertEqual(self.post(self.good())[0], 200)
        t.join()
        self.assertEqual(self.post(self.good())[0], 429)
        self.assertEqual(self.srv.drain(), [])

    def test_only_loopback_liquidate_path_exists(self):
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}/other', data=b'{}', method='POST')
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 404)
        self.assertEqual(self.srv.httpd.server_address[0], '127.0.0.1')

    def test_port_in_use_returns_false_without_raising(self):
        other = mo.ManualOrderServer(self.port)
        self.assertFalse(other.start())


if __name__ == '__main__':
    unittest.main()


class PartialAndLimitSell(LiquidatePosition):
    """수동 매도(2026-11-07): 25/50/75/전량 × 시장가/지정가(매도호가)."""

    def fake_trade(self, side, market, amount, reason, risk=False):
        pos = self.lv.d['positions'][market]
        pos['qty'] -= amount
        return {'state': 'done', 'trades': [{'volume': str(amount), 'funds': str(amount * 1000)}], 'paid_fee': '5'}

    def sell(self, pct, ord_type='market', price=3000.0):
        trade = mock.Mock(side_effect=self.fake_trade)
        with mock.patch.object(self.lv, '_trade', trade), mock.patch('upbit.accounts', return_value=self.accounts):
            return self.lv.liquidate_position('KRW-X', {'KRW-X': price}, pct=pct, ord_type=ord_type), trade

    def test_partial_market_sells_the_requested_share_and_keeps_the_rest(self):
        (ok, msg), trade = self.sell(25)
        self.assertTrue(ok, msg)
        self.assertAlmostEqual(trade.call_args[0][2], 2.5)
        self.assertEqual(trade.call_args[0][3], '수동 매도 25%')
        self.assertTrue(trade.call_args[1]['risk'])
        self.assertAlmostEqual(self.lv.d['positions']['KRW-X']['qty'], 7.5)

    def test_each_allowed_share(self):
        for pct, vol in ((50, 5.0), (75, 7.5)):
            self.setUp()
            (ok, _), trade = self.sell(pct)
            self.assertTrue(ok); self.assertAlmostEqual(trade.call_args[0][2], vol)

    def test_remainder_below_minimum_becomes_full_sale(self):
        (ok, _), trade = self.sell(75, price=600.0)   # 7.5개=4,500원<5,000 → 25% 남는 1,500원 → 전량이 아니라 7.5개 자체가 최소 미달
        self.assertFalse(ok); trade.assert_not_called()
        self.setUp()
        (ok, _), trade = self.sell(75, price=700.0)   # 7.5개=5,250원 OK, 남는 2.5개=1,750원<5,000 → 전량
        self.assertTrue(ok); self.assertEqual(trade.call_args[0][2], 10.0)

    def test_share_below_minimum_is_rejected_without_order(self):
        (ok, msg), trade = self.sell(25, price=1000.0 / 5)   # 2.5개 x 200원 = 500원
        self.assertFalse(ok); trade.assert_not_called(); self.assertIn('5,000원', msg)

    def test_bad_share_or_order_type_is_rejected(self):
        for pct, o in ((30, 'market'), (50, 'stop')):
            (ok, _), trade = self.sell(pct, o)
            self.assertFalse(ok); trade.assert_not_called()

    def test_limit_sell_rests_at_best_ask_and_does_not_touch_market(self):
        trade = mock.Mock()
        with mock.patch.object(self.lv, '_trade', trade), mock.patch('upbit.accounts', return_value=self.accounts), \
                mock.patch('upbit.orderbooks', return_value={'KRW-X': {'ask': 1005.0, 'bid': 1000.0}}), \
                mock.patch('upbit.tick_size', return_value=5.0), \
                mock.patch('upbit.limit_sell_post_only', return_value={'uuid': 'u9'}) as lim:
            ok, msg = self.lv.liquidate_position('KRW-X', {'KRW-X': 1000.0}, pct=50, ord_type='limit')
        self.assertTrue(ok, msg)
        trade.assert_not_called()
        self.assertEqual(lim.call_args[0][:3], ('KRW-X', 1005.0, 5.0))
        rest = self.lv.d['resting_orders']['KRW-X']
        self.assertTrue(rest['resting']['manual']); self.assertEqual(rest['side'], 'ask')

    def test_manual_limit_expiry_cancels_without_market_fallback(self):
        import time
        self.lv.d['resting_orders'] = {'KRW-X': {'identifier': 'v5-m', 'uuid': 'u9', 'side': 'ask', 'market': 'KRW-X', 'amount': 5.0,
            'reason': '수동 매도 50% 지정가', 'time': 't', 'atr': None, 'atr_r': None,
            'resting': {'price': 1005.0, 'tick': 5.0, 'volume': 5.0, 'deadline': time.time() - 1, 'placed': time.time() - 100, 'manual': True}}}
        order = {'uuid': 'u9', 'state': 'wait', 'market': 'KRW-X', 'side': 'ask', 'identifier': 'v5-m', 'trades': [], 'executed_volume': '0'}
        trade = mock.Mock()
        with mock.patch('upbit.get_order', return_value=order), mock.patch.object(self.lv, '_wait', return_value=dict(order, state='cancel')), \
                mock.patch('upbit.cancel_order') as cancel, mock.patch.object(self.lv, '_trade', trade):
            self.lv.manage_resting({'KRW-X': 1000.0})
        cancel.assert_called_once(); trade.assert_not_called()
        self.assertNotIn('KRW-X', self.lv.d['resting_orders'])
        self.assertEqual(self.lv.d['positions']['KRW-X']['qty'], 10.0)

    def test_manual_limit_near_stop_still_falls_back_to_market(self):
        import time
        self.lv.d['positions']['KRW-X']['stop'] = 999.0
        self.lv.d['resting_orders'] = {'KRW-X': {'identifier': 'v5-m', 'uuid': 'u9', 'side': 'ask', 'market': 'KRW-X', 'amount': 5.0,
            'reason': '수동 매도 50% 지정가', 'time': 't', 'atr': None, 'atr_r': None,
            'resting': {'price': 1005.0, 'tick': 5.0, 'volume': 5.0, 'deadline': time.time() + 900, 'placed': time.time(), 'manual': True}}}
        order = {'uuid': 'u9', 'state': 'wait', 'market': 'KRW-X', 'side': 'ask', 'identifier': 'v5-m', 'trades': [], 'executed_volume': '0'}
        trade = mock.Mock(side_effect=self.fake_trade)
        with mock.patch('upbit.get_order', return_value=order), mock.patch.object(self.lv, '_wait', return_value=dict(order, state='cancel')), \
                mock.patch('upbit.cancel_order'), mock.patch.object(self.lv, '_trade', trade), mock.patch('upbit.accounts', return_value=self.accounts):
            self.lv.manage_resting({'KRW-X': 1000.0})
        trade.assert_called_once()


class ManualSellValidation(unittest.TestCase):
    def setUp(self):
        self.srv = mo.ManualOrderServer(port=8765, token='tok')

    def body(self, **kw):
        return json.dumps({'token': 'tok', 'market': 'KRW-ABC', 'confirm': True, **kw}).encode()

    def check(self, **kw):
        return self.srv.check('127.0.0.1:8765', None, self.body(**kw), 'liquidate')[0]

    def test_defaults_and_allowed_values(self):
        self.assertEqual(self.check(), 200)
        for pct in (25, 50, 75, 100):
            for o in ('market', 'limit'):
                self.assertEqual(self.check(pct=pct, ord=o), 200)

    def test_rejects_other_values(self):
        for kw in (dict(pct=30), dict(pct='50'), dict(pct=True), dict(pct=0), dict(ord='stop'), dict(ord=1)):
            self.assertEqual(self.check(**kw), 400, kw)
