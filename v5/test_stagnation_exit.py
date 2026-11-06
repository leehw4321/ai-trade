"""정체 탈출 실제 적용(사용자 승인 2026-11-06): 4시간 뒤 최고수익 1% 미만·본전 아래였다가 본전 지정가 위로 오면 시장가 매도."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import exit_watch as ew
import live
import strategy as st

H = 3600
OPEN = '2026-11-06T00:00:00+00:00'
B = ew._ts(OPEN)


def pos(qty=100., cost=14700.):
    return {'KRW-CAP': {'qty': qty, 'cost': cost, 'time': OPEN}}


class WatchLive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.w = ew.Watch(Path(self.tmp.name))
        self.calls = []
        self.ok = True

    def sell(self, m, w, px):
        self.calls.append((m, w['T'], px)); return self.ok

    def step(self, t, px, **k):
        self.w.update(pos(), {'KRW-CAP': px}, {'KRW-CAP': (3, 1.0)}, now=B + t, live_sell=self.sell, live_hours=4, **k)

    def arm(self):
        self.step(4 * H + 1, 142.0)          # 4시간 뒤 최고 +0% · 본전(147.07) 아래 → armed, 한계 148
        self.assertEqual(self.w.s['KRW-CAP|%s|4' % OPEN]['state'], 'armed')

    def test_sells_only_when_price_clears_limit_plus_tick(self):
        self.arm()
        self.step(4 * H + 100, 148.0)        # 한계 148 도달만으로는 아님(한 호가 위 필요)
        self.assertEqual(self.calls, [])
        self.step(4 * H + 200, 149.0)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.w.s['KRW-CAP|%s|4' % OPEN]['state'], 'filled')
        self.assertTrue(self.w.s['KRW-CAP|%s|4' % OPEN]['live'])

    def test_failed_sell_stays_armed_and_retries(self):
        self.arm(); self.ok = False
        self.step(4 * H + 200, 150.0); self.step(4 * H + 210, 150.0)
        self.assertEqual(self.w.s['KRW-CAP|%s|4' % OPEN]['state'], 'armed')
        self.assertEqual(len(self.calls), 2)
        self.ok = True
        self.step(4 * H + 220, 150.0)
        self.assertEqual(self.w.s['KRW-CAP|%s|4' % OPEN]['state'], 'filled')

    def test_no_live_sell_after_two_hour_window(self):
        self.arm()
        self.step(6 * H + 100, 150.0, )      # 유효 2시간(4h+1s → 6h+1s) 지난 뒤
        self.assertEqual(self.calls, [])

    def test_two_hour_watch_stays_virtual(self):
        self.step(2 * H + 1, 142.0)
        self.step(2 * H + 50, 150.0)
        self.assertEqual([c for c in self.calls if c[1] == 2], [])
        self.assertEqual(self.w.s['KRW-CAP|%s|2' % OPEN]['state'], 'filled')

    def test_strong_position_not_armed(self):
        self.w.update(pos(), {'KRW-CAP': 149.0}, {}, now=B + 4 * H + 1, live_sell=self.sell, live_hours=4)
        self.assertEqual(self.w.s['KRW-CAP|%s|4' % OPEN]['state'], 'pass')


class LiveSell(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.lv = live.Live(Path(self.tmp.name) / 'live', {'enabled': True, 'stagnation_exit_enabled': True,
                                                             'max_order_krw': 10 ** 9, 'max_total_buy_krw': 10 ** 9}, st.Params())
        self.lv.d['positions'] = pos()
        self.sold = []
        for name, value in (('is_protected', False),):
            q = patch.object(self.lv, name, return_value=value); q.start(); self.addCleanup(q.stop)
        q = patch.object(live.upbit, 'accounts', return_value=[{'currency': 'CAP', 'balance': '100'}]); q.start(); self.addCleanup(q.stop)
        q = patch.object(self.lv, '_trade', side_effect=self._trade); q.start(); self.addCleanup(q.stop)
        q = patch.object(self.lv, 'share'); q.start(); self.addCleanup(q.stop)
        self.w = {'T': 4, 'opened': OPEN, 'limit': 148, 'entry': 147.0}

    def _trade(self, side, m, vol, reason, **k):
        self.sold.append((side, m, vol, reason, k))
        return {'trades': [{'volume': str(vol), 'funds': str(vol * 149)}], 'paid_fee': '70', 'state': 'done'}

    def test_sells_full_balance_through_risk_path(self):
        self.assertTrue(self.lv.stagnation_sell('KRW-CAP', self.w, 149.0))
        self.assertEqual(self.sold[0][:4], ('ask', 'KRW-CAP', 100., '정체 탈출 4시간'))
        self.assertTrue(self.sold[0][4]['risk'])

    def test_refuses_when_disabled_other_position_or_uncertain(self):
        self.lv.cfg['stagnation_exit_enabled'] = False
        self.assertFalse(self.lv.stagnation_sell('KRW-CAP', self.w, 149.0))
        self.lv.cfg['stagnation_exit_enabled'] = True
        self.assertFalse(self.lv.stagnation_sell('KRW-CAP', dict(self.w, opened='other'), 149.0))   # 다른(새로 산) 포지션
        self.lv.d['risk_pending_orders'] = {'KRW-CAP': {}}
        self.assertFalse(self.lv.stagnation_sell('KRW-CAP', self.w, 149.0))
        self.assertEqual(self.sold, [])

    def test_refuses_protected_and_tiny(self):
        with patch.object(self.lv, 'is_protected', return_value=True):
            self.assertFalse(self.lv.stagnation_sell('KRW-CAP', self.w, 149.0))
        with patch.object(live.upbit, 'accounts', return_value=[{'currency': 'CAP', 'balance': '10'}]):
            self.assertFalse(self.lv.stagnation_sell('KRW-CAP', self.w, 149.0))   # 평가 1,490원 < 5,000원
        self.assertEqual(self.sold, [])


if __name__ == '__main__':
    unittest.main()
