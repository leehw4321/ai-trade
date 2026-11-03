"""D-51 합의 명세 테스트 (네트워크 없음)."""
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import exit_study as X
import strategy as st

P = replace(st.Params(), trail_enabled=True, stop_max_pct=0.045, k=3.0)


def lot(E=100.0, atr=5.0, kind='main'):
    S0 = st.cap_stop(E, E - P.k * atr, P)      # 3×5=15 → −4.5%로 당김: S0=95.5
    return {'qty': 30.0, 'qty0': 30.0, 'E': E, 'S0': S0, 'stop': S0, 'atr': atr, 'peak': E, 'time': '2000-01-01T00:00:00+09:00',
            'spend': 3000.0, 'kind': kind}


class RiskUnit(unittest.TestCase):
    def test_r0_is_initial_stop_distance_and_g0_uses_atr(self):
        l = lot()
        self.assertAlmostEqual(X.risk_unit(l, 'r0', P.k), 4.5)
        self.assertAlmostEqual(X.risk_unit(l, 'atr', P.k), 15.0)

    def test_non_positive_r0_falls_back(self):
        l = dict(lot(), S0=101.0)
        self.assertAlmostEqual(X.risk_unit(l, 'r0', P.k), 15.0)

    def test_r0_not_recomputed_after_stop_raise(self):
        l = lot()
        X.protect(l, 105.0, X.risk_unit(l, 'r0', P.k), P, 'KRW-X', 1.0)
        self.assertGreater(l['stop'], l['S0'])
        self.assertAlmostEqual(X.risk_unit(l, 'r0', P.k), 4.5)


class Protect(unittest.TestCase):
    def test_partial_at_1r0_for_g2_but_not_for_g1(self):
        self.assertEqual(X.protect(lot(), 104.6, 4.5, P, 'KRW-X', 1.0)[0], 'partial')
        self.assertIsNone(X.protect(lot(), 104.6, 4.5, P, 'KRW-X', 2.0)[0])
        self.assertEqual(X.protect(lot(), 109.1, 4.5, P, 'KRW-X', 2.0)[0], 'partial')

    def test_g0_protection_late_with_wide_atr(self):
        l = lot()
        X.protect(l, 110.0, 15.0, P, 'KRW-X', 2.0)
        self.assertEqual(l['stop'], l['S0'])            # +10%여도 1R(15) 미만: 본전 잠금 없음

    def test_full_exit_has_priority_over_partial(self):
        l = lot()
        X.protect(l, 110.0, 4.5, P, 'KRW-X', 1.0)       # 고점 110, g=2.2 → 추적 시작
        act, why = X.protect(l, 104.0, 4.5, P, 'KRW-X', 1.0)
        self.assertEqual((act, why), ('exit', '수익 반납'))

    def test_stop_never_lowered(self):
        l = lot()
        X.protect(l, 105.0, 4.5, P, 'KRW-X', 0)
        s = l['stop']
        X.protect(l, 102.0, 4.5, P, 'KRW-X', 0)
        self.assertGreaterEqual(l['stop'], s)


class Rebuy(unittest.TestCase):
    r = {'market': 'KRW-X', 'rank': 2, 'rs': 0.05, 'atr': 1.0, 'close': 100.0}

    def check(self, hours, ask, r=None, cash=1e6):
        right = {'t': 0.0, 'px': 110.0, 'budget': 20000.0}
        return X.rebuy_ok(right, hours * 3600, ask, r if r is not None else self.r, 0, 1e6, 0, cash, P)

    def test_window_price_and_signal(self):
        self.assertEqual(self.check(1.9, 100)[0], '대기 중')
        self.assertEqual(self.check(3.1, 100)[0], '기한 만료')
        self.assertIn('97%', self.check(2.5, 107)[0])
        self.assertIn('유지 순위', self.check(2.5, 100, dict(self.r, rank=6))[0])
        self.assertIn('유지 순위', self.check(2.5, 100, dict(self.r, rs=-0.01))[0])

    def test_allowed_and_budget_capped(self):
        why, amt = self.check(2.5, 100)
        self.assertIsNone(why)
        self.assertEqual(amt, 20000.0)                    # 지출 ≤ 익절 순수령액

    def test_atr_pullback_required(self):
        r = dict(self.r, atr=20.0, close=100.0)            # ATR 20% → 110×0.8=88 이하여야
        self.assertIn('눌려야', self.check(2.5, 100, r)[0])


class BookFlow(unittest.TestCase):
    def test_bundle_counts_partial_rebuy_and_rotation_once(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(X, 'OUT', Path(d)):
            bk = X.Book('G3', X.GROUPS['G3_재매수'])
            Pos = bk.buy('KRW-X', 100000, 100.0, 5.0, P)
            main = Pos['main']
            bk.sell_qty('KRW-X', main, main['qty0'] / 3, 106.0, P, '부분 익절')
            lot2 = bk.open_lot('KRW-X', 300.0, 102.0, 5.0, P, 'rebuy')
            Pos['lot'] = lot2
            Pos['spent'] += lot2['spend']
            bk.close_all('KRW-X', 104.0, P, '순환')
            b = X.rows(Path(d) / 'G3' / 'bundles.jsonl')
            self.assertEqual(len(b), 1)                    # 부분 익절·재매수·순환이 한 묶음
            self.assertNotIn('KRW-X', bk.d['pos'])
            self.assertAlmostEqual(bk.d['cash'], X.CAPITAL + b[0]['pnl'], places=1)


if __name__ == '__main__':
    unittest.main()
