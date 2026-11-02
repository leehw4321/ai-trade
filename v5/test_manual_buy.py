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


class AddToPosition(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lv = live.Live(self.tmp.name, {'enabled': True, 'max_order_krw': 1_000_000_000,
                                            'max_total_buy_krw': 1_000_000_000}, st.Params())
        self.lv.d['positions']['KRW-X'] = {'qty': 10.0, 'cost': 9000.0, 'stop': 800.0,
                                           'time': '2026-11-03T08:00:00+09:00'}
        self.markets = {'KRW-X': {'warning': False}}

    def tearDown(self):
        self.tmp.cleanup()

    def fake_trade(self, side, market, amount, reason, risk=False):
        self.lv.d['positions'][market]['cost'] += amount
        return {'state': 'done', 'trades': [{'volume': '5', 'funds': str(amount)}], 'paid_fee': '10'}

    def add(self, krw=30000, market='KRW-X', block='', trade=None):
        trade = trade or mock.Mock(side_effect=self.fake_trade)
        with mock.patch.object(self.lv, 'buy_block', return_value=block) as bb, \
                mock.patch.object(self.lv, '_trade', trade):
            return self.lv.add_to_position(market, krw, self.markets), trade, bb

    def test_buys_through_the_same_checks_as_automatic_buys(self):
        (ok, msg), trade, bb = self.add()
        self.assertTrue(ok, msg)
        bb.assert_called_once_with('KRW-X', 30000, self.markets)
        trade.assert_called_once_with('bid', 'KRW-X', 30000, '수동 추가 매수')
        self.assertIn('30,010원', msg)

    def test_rejections_never_place_an_order(self):
        cases = {
            'new coin': dict(market='KRW-NEW'),
            'too small': dict(krw=4999),
            'float amount': dict(krw=30000.5),
            'text amount': dict(krw='30000'),
            'above manual cap': dict(krw=100001),
            'blocked by buy_block': dict(block='손실 중단선 도달로 신규 매수 중단 상태'),
        }
        for name, kw in cases.items():
            with self.subTest(name):
                (ok, msg), trade, _ = self.add(**kw)
                self.assertFalse(ok)
                self.assertTrue(msg)
                trade.assert_not_called()

    def test_loss_halt_really_blocks_a_manual_add(self):
        self.lv.d['halted'] = '2026-11-03T05:59:38+09:00 손실 중단'
        with mock.patch.object(self.lv, '_trade') as trade:
            ok, msg = self.lv.add_to_position('KRW-X', 30000, self.markets)
        self.assertFalse(ok)
        self.assertIn('중단', msg)
        trade.assert_not_called()

    def test_cap_never_exceeds_the_order_limit(self):
        self.lv.cfg.update(max_order_krw=50000, manual_buy_max_krw=100000)
        (ok, msg), trade, _ = self.add(krw=60000)
        self.assertFalse(ok)
        self.assertIn('50,000', msg)
        trade.assert_not_called()

    def test_live_off_failure_and_lost_lease(self):
        self.lv.cfg['enabled'] = False
        self.assertFalse(self.add()[0][0])
        self.lv.cfg['enabled'] = True
        (ok, msg), _, _ = self.add(trade=mock.Mock(side_effect=RuntimeError('거부')))
        self.assertFalse(ok)
        self.assertIn('매수 실패', msg)
        with self.assertRaises(SessionLost):
            self.add(trade=mock.Mock(side_effect=SessionLost('다른 PC')))

    def test_unfilled_buy_is_reported(self):
        unfilled = mock.Mock(return_value={'state': 'cancel', 'trades': [], 'paid_fee': '0'})
        (ok, msg), _, _ = self.add(trade=unfilled)
        self.assertFalse(ok)
        self.assertIn('미체결', msg)


class BuyThroughServer(unittest.TestCase):
    def setUp(self):
        self.port = free_port()
        self.srv = mo.ManualOrderServer(self.port, token='tok-123')
        self.assertTrue(self.srv.start())

    def tearDown(self):
        self.srv.stop()

    def post(self, doc):
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}/buy', data=json.dumps(doc).encode(),
                                     method='POST', headers={'Origin': 'null'})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_amount_must_be_an_integer_in_range(self):
        base = {'token': 'tok-123', 'market': 'KRW-ABC', 'confirm': True}
        for bad in (None, 'x', 4999, 100_000_001, 30000.5, True, -5000):
            self.assertEqual(self.post({**base, 'krw': bad})[0], 400, bad)
        self.assertEqual(self.post({**base, 'token': 'no', 'krw': 30000})[0], 403)
        self.assertEqual(self.post({**base, 'confirm': False, 'krw': 30000})[0], 400)
        self.assertEqual(self.srv.drain(), [])

    def test_confirmed_buy_reaches_the_bot_with_its_amount(self):
        got = []

        def bot():
            for _ in range(200):
                for req in self.srv.drain():
                    got.append((req.kind, req.market, req.krw))
                    req.finish(True, '매수 완료')
                    return
                threading.Event().wait(0.02)
        t = threading.Thread(target=bot)
        t.start()
        code, doc = self.post({'token': 'tok-123', 'market': 'KRW-ABC', 'confirm': True, 'krw': 30000})
        t.join()
        self.assertEqual((code, doc), (200, {'ok': True, 'message': '매수 완료'}))
        self.assertEqual(got, [('buy', 'KRW-ABC', 30000)])

    def test_repeat_guard_is_per_kind(self):
        def bot():
            for _ in range(2):
                for _ in range(200):
                    reqs = self.srv.drain()
                    for r in reqs:
                        r.finish(True, 'ok')
                    if reqs:
                        break
                    threading.Event().wait(0.02)
        t = threading.Thread(target=bot)
        t.start()
        self.assertEqual(self.srv.submit('KRW-ABC', 'buy', 30000)[0], 200)
        self.assertEqual(self.srv.submit('KRW-ABC', 'buy', 30000)[0], 429)       # 같은 종류는 30초 거부
        self.assertEqual(self.srv.submit('KRW-ABC', 'liquidate')[0], 200)         # 다른 종류는 별개
        t.join()


if __name__ == '__main__':
    unittest.main()
