import tempfile
from pathlib import Path
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
        self.assertEqual(lv.stop_limit(pos), max(pos['stop'], pos.get('stop0', pos['stop'])))  # 올라간 손절가도 실시간 평가(사용자 지시 11-04)
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


class AdoptedStopRebase(unittest.TestCase):
    def test_far_stop_is_raised_once_to_cap_and_never_lowered(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        lv = live.Live(tmp.name, {'enabled': True}, st.Params())
        lv.d['positions']['KRW-FAR'] = {'qty': 1.0, 'cost': 100000.0, 'stop': 65000.0, 'time': 't'}
        lv.d['positions']['KRW-NEAR'] = {'qty': 1.0, 'cost': 100000.0, 'stop': 97000.0, 'time': 't'}
        lv.rebase_adopted_stops({'KRW-FAR': 100000.0, 'KRW-NEAR': 100000.0})
        self.assertAlmostEqual(lv.d['positions']['KRW-FAR']['stop'], 92000.0)
        self.assertEqual(lv.d['positions']['KRW-NEAR']['stop'], 97000.0)
        lv.rebase_adopted_stops({'KRW-FAR': 50000.0})              # 한 번만 적용: 가격이 폭락해도 다시 당기지 않음
        self.assertAlmostEqual(lv.d['positions']['KRW-FAR']['stop'], 92000.0)


class AdoptedTrail(unittest.TestCase):
    def make(self, on):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        lv = live.Live(tmp.name, {'enabled': True, 'trail_enabled': True, 'trail_adopted': on}, st.Params())
        lv.d['positions']['KRW-A'] = {'qty': 300.0, 'cost': 30000.0, 'stop': 80.0, 'time': '2026-11-02T21:12:57+09:00'}
        return lv

    @staticmethod
    def bars(*closes):
        import datetime as dt
        out = []
        for i, c in enumerate(closes):
            start = dt.datetime(2026, 11, 2, 12, 0, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=15 * i)
            out.append({'t': start.isoformat(), 'o': c, 'h': c * 1.01, 'l': c * 0.99, 'c': c})
        return out

    def test_off_by_default_leaves_adopted_alone(self):
        lv = self.make(False)
        with mock.patch('upbit.completed_candles', return_value=self.bars(*([100.0] * 40))):
            lv.trail_check()
        self.assertNotIn('entry', lv.d['positions']['KRW-A'])

    def test_adopted_gets_entry_from_account_average_and_trails_from_1r(self):
        lv = self.make(True)
        base = [100.0 + (i % 3) for i in range(40)]
        with mock.patch('upbit.completed_candles', return_value=self.bars(*base)):
            self.assertTrue(lv.adopt_trail_init())
        pos = lv.d['positions']['KRW-A']
        self.assertEqual(pos['entry'], 100.0)                       # 30000/300 = 거래소 평균 매수가
        self.assertTrue(pos['trail'] and pos['adopted_trail'])
        self.assertEqual(pos['stop0'], 80.0)                        # 초기 손절은 기존 값 유지
        self.assertGreater(pos['atr'], 0)
        # 1R(=3×ATR) 이상 올랐다가 고점−2×ATR 아래로 확정 종가가 내려오면 청산 신호
        p = st.Params(trail_enabled=True, trail_start_r=1.0)
        top = pos['entry'] + 1.5 * 3 * pos['atr']
        self.assertIsNone(st.ratchet('KRW-A', pos, top, p))
        self.assertEqual(st.ratchet('KRW-A', pos, top - 2.1 * pos['atr'], p) is not None, True)


class PartialProfit(TrailLive):
    def trade_log(self, lv, closes, **cfg):
        lv.cfg.update(partial_enabled=True, **cfg)
        sold = []

        def fake(side, market, amount, reason, risk=False):
            sold.append((market, reason, amount))
            pos = lv.d['positions'][market]
            pos['qty'] -= amount
            return {'state': 'done', 'trades': [{'volume': str(amount), 'funds': str(amount * 106000)}], 'paid_fee': '0'}

        with mock.patch.object(lv, '_trade', side_effect=fake), mock.patch.object(lv, 'recover_all_pending', return_value=True), \
                mock.patch('upbit.accounts', return_value=[{'currency': 'X', 'balance': '1'}]):
            for minute, close in closes:
                with mock.patch('upbit.completed_candles', return_value=[self.bar(minute, close)]):
                    lv.trail_check()
        return sold

    def test_partial_sells_once_at_2r_not_before(self):
        lv = self.make(True)
        lv.d['positions']['KRW-X']['qty'] = 1.0
        lv.d['positions']['KRW-X']['cost'] = 100000.0
        sold = self.trade_log(lv, [(1, 104000), (2, 107000), (3, 108000)])   # 1R=3,000 → 2R=106,000
        self.assertEqual([(m, r) for m, r, _ in sold], [('KRW-X', '부분 익절')])  # 104,000(1.33R) 전엔 안 팔고, 한 번만
        self.assertAlmostEqual(sold[0][2], 0.3333, places=3)
        self.assertTrue(lv.d['positions']['KRW-X']['part_done'])
        self.assertIn('KRW-OLD', lv.d['positions'])                                    # 인수 보유분은 건드리지 않음

    def test_partial_off_by_default(self):
        lv = self.make(True)
        lv.cfg['partial_enabled'] = False
        with mock.patch.object(lv, '_trade') as t, mock.patch.object(lv, 'recover_all_pending', return_value=True), \
                mock.patch('upbit.accounts', return_value=[{'currency': 'X', 'balance': '1'}]), \
                mock.patch('upbit.completed_candles', return_value=[self.bar(1, 107000)]):
            lv.trail_check()
        t.assert_not_called()


class LossStopPercent(unittest.TestCase):
    def make(self, pct):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        lv = live.Live(tmp.name, {'enabled': True, 'loss_stop_krw': 15000, 'loss_stop_pct': pct}, st.Params())
        lv.d['positions']['KRW-A'] = {'qty': 1.0, 'cost': 100000.0, 'stop': 1.0, 'time': 't'}
        return lv

    def test_amount_mode_unchanged_when_pct_zero(self):
        lv = self.make(0.0)
        self.assertEqual(lv.loss_limit({}), 15000.0)
        lv.d['realized'] = -15000
        lv.check_loss({'KRW-A': 100000 / (1 - 0.0005)})
        self.assertIn('중단선 15,000원', lv.d['halted'])

    def test_pct_uses_base_equity_once_and_halts_at_limit(self):
        lv = self.make(0.01)
        with mock.patch.object(lv, 'equity_total', return_value=2_000_000.0) as eq:
            self.assertEqual(lv.loss_limit({}), 20000.0)
            self.assertEqual(lv.loss_limit({}), 20000.0)       # 두 번째는 저장된 기준 사용
            eq.assert_called_once()
        self.assertEqual(lv.d['risk_base_equity'], 2_000_000.0)
        lv.d['realized'] = -19000.0
        px = {'KRW-A': 100000 / (1 - 0.0005)}
        lv.check_loss(px)
        self.assertEqual(lv.d['halted'], '')                       # 19,000 < 20,000
        lv.d['realized'] = -20500.0
        lv.check_loss(px)
        self.assertIn('총자산의 1.00%', lv.d['halted'])

    def test_falls_back_to_amount_when_equity_unavailable(self):
        lv = self.make(0.01)
        with mock.patch.object(lv, 'equity_total', return_value=None):
            self.assertEqual(lv.loss_limit({}), 15000.0)
        self.assertNotIn('risk_base_equity', lv.d)


class LastSellRecord(unittest.TestCase):
    def test_full_close_records_last_sell_with_profit_flag(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        lv = live.Live(tmp.name, {'enabled': True}, st.Params())
        lv.d['positions']['KRW-A'] = {'qty': 1.0, 'cost': 100000.0, 'stop': 1.0, 'time': 't'}
        pending = {'identifier': 'i', 'side': 'ask', 'market': 'KRW-A', 'amount': 1.0, 'reason': '추적 손절', 'time': 't', 'uuid': 'u'}
        lv.d['pending_order'] = pending
        order = {'identifier': 'i', 'side': 'ask', 'state': 'done', 'executed_volume': '1', 'uuid': 'u',
                 'trades': [{'volume': '1', 'funds': '110000'}], 'paid_fee': '50'}
        lv._apply_pending(order, None)
        ls = lv.d['last_sell']['KRW-A']
        self.assertTrue(ls['profit'])
        self.assertAlmostEqual(ls['price'], 110000.0)
        self.assertIn('추적', ls['reason'])


class EquityTotal(unittest.TestCase):
    def test_ignores_assets_without_krw_market(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        lv = live.Live(tmp.name, {'enabled': True}, st.Params())
        rows = [{'currency': 'KRW', 'balance': '1000', 'locked': '0'}, {'currency': 'AAA', 'balance': '2', 'locked': '1'},
                {'currency': 'DEAD', 'balance': '9', 'locked': '0'}]
        with mock.patch('upbit.accounts', return_value=rows), mock.patch('upbit.krw_markets', return_value={'KRW-AAA': {}}), \
                mock.patch('upbit.tickers', return_value={'KRW-AAA': {'trade_price': 100.0}}) as t:
            self.assertAlmostEqual(lv.equity_total(), 1000 + 3 * 100.0)   # DEAD는 시세 조회 대상에서 제외
        self.assertEqual(t.call_args.args[0], ['KRW-AAA'])


class ManualResume(unittest.TestCase):
    def test_resume_clears_halt_and_restarts_loss_count(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            lv = live.Live(Path(t), {'loss_stop_krw': 15000}, st.Params())
            lv.d['realized'] = -16000
            self.assertGreaterEqual(lv.check_loss({}), 15000)
            self.assertTrue(lv.d['halted'])
            (Path(t) / 'RESUME_BUY').write_text('테스트', encoding='utf-8')
            lv.check_resume_request({})
            self.assertFalse(lv.d['halted'])
            self.assertFalse((Path(t) / 'RESUME_BUY').exists())
            self.assertEqual(lv.check_loss({}), 0)            # 과거 손실은 기준으로 이동
            lv.d['realized'] -= 15000                          # 재개 후 새로 15,000원 잃으면 다시 중단
            lv.check_loss({})
            self.assertTrue(lv.d['halted'])


class DailyLoss(unittest.TestCase):
    def test_new_day_restarts_count_and_resumes(self):
        with tempfile.TemporaryDirectory() as t:
            lv = live.Live(Path(t), {'loss_stop_krw': 15000, 'loss_daily': True}, st.Params())
            lv.d['risk_day'] = '2000-01-01'; lv.d['realized'] = -16000
            self.assertEqual(lv.check_loss({}), 0)             # 새 날: 과거 손실은 기준으로 이동
            self.assertFalse(lv.d['halted'])
            lv.d['realized'] -= 15000                           # 같은 날 15,000원 더 잃으면 중단
            lv.check_loss({})
            self.assertTrue(lv.d['halted'])
            day = lv.d['risk_day']
            lv.check_loss({})
            self.assertTrue(lv.d['halted'] and lv.d['risk_day'] == day)   # 같은 날에는 풀리지 않음
            lv.d['risk_day'] = '2000-01-01'
            lv.check_loss({})
            self.assertFalse(lv.d['halted'])                    # 다음 날 자동 재개
