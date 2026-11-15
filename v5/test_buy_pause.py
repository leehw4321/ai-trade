"""매수 쉬기·다시 시작 버튼과 가상 매수 기록(사용자 지시 2026-11-15)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import buy_pause as bp
import drop_watch
import live
import manual_orders as mo
import strategy as st
from test_drop_watch import bars_from


class PauseState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_toggle(self):
        self.assertFalse(bp.is_paused(self.d))
        self.assertTrue(bp.set_paused(self.d, True, now=100)[0])
        self.assertTrue(bp.is_paused(self.d))
        self.assertFalse(bp.set_paused(self.d, True, now=200)[0])       # 이미 쉬는 중
        self.assertTrue(bp.set_paused(self.d, False, now=300)[0])
        s = bp.load(self.d)
        self.assertFalse(s['paused']); self.assertEqual([x['action'] for x in s['log']], ['쉬기', '다시 시작'])

    def test_virtual_dedupe_and_slot_cap(self):
        self.assertEqual(bp.record_virtual(self.d, [('KRW-A', 70000, 10.0), ('KRW-A', 70000, 10.0), ('KRW-B', 70000, 0)], now=0), 1)
        self.assertEqual(bp.record_virtual(self.d, [('KRW-A', 70000, 11.0)], now=3600), 0)    # 6시간 안 같은 종목
        self.assertEqual(bp.record_virtual(self.d, [('KRW-B', 1, 1.0), ('KRW-C', 1, 1.0), ('KRW-D', 1, 1.0)], now=3600), 2)  # 동시 3개
        self.assertEqual(bp.record_virtual(self.d, [('KRW-A', 70000, 9.0)], now=7 * 3600), 1)  # 창이 끝나면 다시

    def test_resolve_render_and_resume_signal(self):
        bp.set_paused(self.d, True, now=0)
        bp.record_virtual(self.d, [('KRW-A', 100000, 100.0)], now=0)
        n = bp.resolve(self.d, lambda m: bars_from([(100, 100, 96, 97)] + [(97, 97, 97, 97)] * 30), 0.035, 0.03, 0.3, now=8 * 3600)
        self.assertEqual(n, 1)
        v = bp.load(self.d)['virtual'][0]
        self.assertEqual(v['why'], '손절'); self.assertAlmostEqual(v['ret'], -0.035 - drop_watch.FEE2)
        act = {'port': 1, 'token': 't'}
        html = bp.render(self.d, act, [], now=8 * 3600)
        self.assertIn('매수 쉬는 중', html); self.assertIn('/resume_buy', html); self.assertIn('쉰 효과 +3,600원', html)
        trips = [{'close': k, 'pnl': 1000} for k in range(19)]
        self.assertIn('다시 살 신호', bp.render(self.d, act, trips, now=8 * 3600))      # 실거래 19건 +19,000 + 가상 −3,600
        bp.set_paused(self.d, False, now=9 * 3600)
        html = bp.render(self.d, act, [], now=9 * 3600)
        self.assertIn('/pause_buy', html); self.assertIn('지난 휴식 가상 매수 누계 1건', html)
        self.assertIn('실행권이 있는 PC', bp.render(self.d, None, [], now=9 * 3600))


class Requests(unittest.TestCase):
    def test_pause_resume_need_token_and_confirm(self):
        s = mo.ManualOrderServer(8765, token='t', allowed_kinds={'pause_buy', 'resume_buy'})
        h = '127.0.0.1:8765'
        for k in ('pause_buy', 'resume_buy'):
            self.assertEqual(mo.KINDS['/' + k], k)
            self.assertEqual(s.check(h, 'null', json.dumps({'token': 't', 'confirm': True}).encode(), k), (200, (None, None)))
            self.assertEqual(s.check(h, 'null', json.dumps({'token': 'x', 'confirm': True}).encode(), k)[0], 403)
            self.assertEqual(s.check(h, 'null', json.dumps({'token': 't'}).encode(), k)[0], 400)


class LiveGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.lv = live.Live(self.d / 'live', {'enabled': True, 'max_order_krw': 10 ** 9, 'max_total_buy_krw': 10 ** 9}, st.Params())
        for method in ('accounts', 'orderbooks', 'market_buy', 'market_sell'):
            p = patch.object(live.upbit, method, side_effect=AssertionError('unexpected external call'))
            p.start(); self.addCleanup(p.stop)

    def test_paused_automatic_buy_is_virtual_only(self):
        bp.set_paused(self.d, True)
        with patch.object(live.upbit, 'tickers', return_value={'KRW-MPLX': {'trade_price': 50.0}}):
            r = self.lv.execute(('봇 주문안 test', '매수 MPLX 94000원'), {'KRW-MPLX': {'warning': False}}, automatic=True)
        self.assertIn('매수 쉬는 중', r)
        v = bp.load(self.d)['virtual']
        self.assertEqual((v[0]['m'], v[0]['krw'], v[0]['px']), ('KRW-MPLX', 94000, 50.0))

    def test_not_paused_goes_to_normal_path(self):
        with patch.object(live.upbit, 'tickers', side_effect=AssertionError('x')), self.assertRaises(AssertionError):
            self.lv.execute(('봇 주문안 test', '매수 MPLX 94000원'), {'KRW-MPLX': {'warning': False}}, automatic=True)
        self.assertEqual(bp.load(self.d)['virtual'], [])

    def test_resting_buy_cancelled_while_paused(self):
        bp.set_paused(self.d, True)
        self.assertEqual(self.lv.resting_broken('KRW-A', {}, None), '사용자 매수 쉬기')


if __name__ == '__main__':
    unittest.main()
