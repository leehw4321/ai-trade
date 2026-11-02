import tempfile
import unittest
from unittest import mock

import live
import strategy as st


class Parse(unittest.TestCase):
    def test_orders(self):
        self.assertEqual(live.parse('매도 ONG 100%; 매수 BTC 20,000원'),
                         [('sell', 'KRW-ONG', 100.0), ('buy', 'KRW-BTC', 20000)])

    def test_nothing(self):
        self.assertEqual(live.parse('매도 없음; 매수 없음'), [])

    def test_conditional_is_rejected(self):
        with self.assertRaises(ValueError):
            live.parse('매도 없음; 매수 <C 상위3 1종목> min(30000원)원')

    def test_bad_format(self):
        with self.assertRaises(ValueError):
            live.parse('매수 BTC 조금')


class LatestPlan(unittest.TestCase):
    doc = ('| 43 | 추가 결정 사항 | 합의 | 현재 판단 문구 ‘실행 주문안: 매도 없음; 매수 없음’을 수용 |\n'
           '| 50 | 추가 결정 사항 | 제안 | 실행 주문안: 매수 BTC 30000원 |\n'
           '| 51 | 추가 결정 사항 | 합의 | 실행 주문안: 매수 ETH 20000원 |\n')

    def test_only_agreed_newest(self):
        self.assertEqual(live.latest_plan(self.doc), (51, '매수 ETH 20000원'))

    def test_quote_is_trimmed(self):
        self.assertEqual(live.latest_plan(self.doc.splitlines()[0]), (43, '매도 없음; 매수 없음'))


class Limits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lv = live.Live(self.tmp.name, {'enabled': True}, st.Params())
        self.markets = {'KRW-BTC': {'warning': False}, 'KRW-X': {'warning': True}}

    def tearDown(self):
        self.tmp.cleanup()

    def block(self, m, krw):
        with mock.patch('upbit.accounts', return_value=[{'currency': 'KRW', 'balance': '38810'}]), \
                mock.patch('upbit.orderbooks', return_value={m: {'ask': 100.0, 'bid': 99.9}}):
            return self.lv.buy_block(m, krw, self.markets)

    def test_limits(self):
        self.assertEqual(self.block('KRW-BTC', 30000), '')
        self.assertIn('1건 한도', self.block('KRW-BTC', 30001))
        self.assertIn('경보', self.block('KRW-X', 10000))
        self.lv.d['bought_krw'] = 90000
        self.assertIn('누적 매수 한도', self.block('KRW-BTC', 20000))

    def test_loss_stop_halts_buys(self):
        self.lv.d['realized'] = -15000
        self.lv.check_loss({})
        self.assertIn('중단', self.block('KRW-BTC', 10000))

    def test_same_plan_runs_once(self):
        doc = '| 60 | x | 합의 | 실행 주문안: 매수 BTC 10000원 |\n'
        with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False, encoding='utf-8') as f:
            f.write(doc)
        with mock.patch.object(live, 'DEBATE', live.Path(f.name)), mock.patch.object(live, 'EVIDENCE', live.Path(self.tmp.name) / 'e.md'), \
                mock.patch.object(self.lv, 'execute', return_value='ok') as ex:
            self.lv.poll(self.markets)
            self.lv.doc_mtime = 0
            self.lv.poll(self.markets)
        self.assertEqual(ex.call_count, 1)


class Delegated(unittest.TestCase):
    def test_bot_proposal_runs_once_per_proposal(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            root = live.Path(d)
            lv = live.Live(root / 'live', {'enabled': True, 'auto_proposal': True}, st.Params())
            (root / 'proposal.json').write_text(json.dumps({'time': '2026-11-02T13:00:00+09:00', 'text': '실행 주문안: 매수 BTC 10000원'}), encoding='utf-8')
            with mock.patch.object(live, 'DEBATE', root / 'none.md'), mock.patch.object(live, 'EVIDENCE', root / 'e.md'),                     mock.patch.object(lv, 'execute', return_value='ok') as ex:
                lv.poll({})
                lv.poll({})
            self.assertEqual(ex.call_count, 1)
            self.assertEqual(ex.call_args[0][0][1], '매수 BTC 10000원')


class Limits2(unittest.TestCase):
    def test_parse_and_throttle(self):
        w = live.parse_limits('1건 100000원; 누적 1,000,000원; 손실중단 10000원')
        self.assertEqual(w, {'max_order_krw': 100000, 'max_total_buy_krw': 1000000, 'loss_stop_krw': 10000})
        cur = {'max_order_krw': 30000, 'max_total_buy_krw': 100000, 'loss_stop_krw': 15000}
        self.assertEqual(live.throttle(cur, w, 1_900_000), {'max_order_krw': 60000, 'max_total_buy_krw': 200000, 'loss_stop_krw': 10000})
        self.assertEqual(live.throttle({'max_total_buy_krw': 1_500_000}, {'max_total_buy_krw': 5_000_000}, 1_900_000)['max_total_buy_krw'], 1_900_000)

    def test_only_agreed_limits(self):
        doc = '| 70 | x | 제안 | 실행 한도: 1건 90000원 |\n| 71 | x | 합의 | 실행 한도: 1건 60000원 |\n'
        self.assertEqual(live.latest_limits(doc), (71, '1건 60000원'))


if __name__ == '__main__':
    unittest.main()


class TrailLive(unittest.TestCase):
    """EXIT-T1 실거래 연결: 기본 꺼짐, 새 포지션만 대상, 확정 1분봉 종가로 평가, 상향 손절가는 실시간 평가 제외."""

    def make(self, trail):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        lv = live.Live(self.tmp.name, {'enabled': True, 'trail_enabled': trail}, st.Params())
        t0 = '2026-11-02T21:00:00+09:00'
        lv.d['positions']['KRW-X'] = {'qty': 1.0, 'cost': 100000.0, 'stop': 97000.0, 'time': t0,
                                      'entry': 100000.0, 'atr': 1000.0, 'stop0': 97000.0, 'trail': trail}
        lv.d['positions']['KRW-OLD'] = {'qty': 1.0, 'cost': 100000.0, 'stop': 50000.0, 'time': t0}  # 인수 보유분: entry 없음
        return lv

    @staticmethod
    def bar(minute, close):
        import datetime as dt
        return {'t': dt.datetime(2026, 11, 2, 12, minute, tzinfo=dt.timezone.utc).isoformat(),
                'o': close, 'h': close, 'l': close, 'c': close}

    def run_trail(self, lv, closes):
        sold = []

        def fake_trade(side, market, amount, reason, risk=False):
            sold.append((market, reason))
            lv.d['positions'].pop(market, None)
            return {'state': 'done', 'trades': [{'volume': str(amount), 'funds': '104000'}], 'paid_fee': '0'}

        with mock.patch.object(lv, '_trade', side_effect=fake_trade),                 mock.patch.object(lv, 'recover_all_pending', return_value=True),                 mock.patch('upbit.accounts', return_value=[{'currency': 'X', 'balance': '1'}]):
            for minute, close in closes:
                with mock.patch('upbit.completed_candles', return_value=[self.bar(minute, close)]):
                    lv.trail_check()
        return sold

    def test_default_off_never_sells_on_trail(self):
        lv = self.make(False)
        self.assertEqual(self.run_trail(lv, [(1, 110000), (2, 104000)]), [])
        self.assertIn('KRW-X', lv.d['positions'])

    def test_on_exits_new_position_only(self):
        lv = self.make(True)
        sold = self.run_trail(lv, [(1, 110000), (2, 110000), (3, 104000)])
        self.assertEqual([m for m, _ in sold], ['KRW-X'])      # 인수 보유분 KRW-OLD는 건드리지 않음
        self.assertTrue(sold[0][1] in ('추적 손절', '수익 반납'))
        self.assertIn('KRW-OLD', lv.d['positions'])

    def test_raised_stop_is_not_checked_by_live_price(self):
        lv = self.make(True)
        self.run_trail(lv, [(1, 110000), (2, 110000)])               # 손절가 상향(108), 아직 보유
        pos = lv.d['positions']['KRW-X']
        self.assertGreater(pos['stop'], 100000)
        self.assertEqual(lv.stop_limit(pos), 97000.0)             # 실시간 평가는 초기 고정 손절만
        self.assertEqual(lv.stop_limit(lv.d['positions']['KRW-OLD']), 50000.0)

    def test_new_buy_fixes_entry_and_addon_disables_trail(self):
        lv = self.make(True)
        lv.d['positions'].clear()
        pending = {'identifier': 'i', 'side': 'bid', 'market': 'KRW-N', 'amount': 10000, 'reason': 'p', 'uuid': 'u',
                   'time': '2026-11-02T21:00:00+09:00', 'atr': 1.0}
        order = {'identifier': 'i', 'side': 'bid', 'state': 'done', 'executed_volume': '100',
                 'trades': [{'volume': '100', 'funds': '10000'}], 'paid_fee': '5', 'uuid': 'u'}
        lv.d['pending_order'] = pending
        lv._apply_pending(order, None)
        pos = lv.d['positions']['KRW-N']
        self.assertEqual((pos['entry'], pos['atr'], pos['trail']), (100.0, 1.0, True))
        self.assertAlmostEqual(pos['stop0'], 97.0)
        pending2 = {k: v for k, v in pending.items() if not k.startswith('applied_')} | {'identifier': 'j', 'uuid': 'v'}
        order2 = {**order, 'identifier': 'j', 'uuid': 'v'}
        lv.d['pending_order'] = pending2
        lv._apply_pending(order2, None)
        self.assertFalse(lv.d['positions']['KRW-N']['trail'])  # 추가 매수 lot은 제외(안건 27 미합의)
        self.assertEqual(lv.d['positions']['KRW-N']['entry'], 100.0)
