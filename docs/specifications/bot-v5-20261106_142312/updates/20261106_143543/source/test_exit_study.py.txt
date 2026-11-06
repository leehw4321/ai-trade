"""D-51 합의 명세와 ChatGPT 구현 검토(07) 반영 테스트 (네트워크 없음)."""
import json
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest import mock

import exit_study as X
import strategy as st

P = replace(st.Params(), trail_enabled=True, stop_max_pct=0.045, k=3.0, entry_risk=0.00225, size_by_risk=True)


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
        self.assertAlmostEqual(X.risk_unit(dict(lot(), S0=101.0), 'r0', P.k), 15.0)

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
        self.assertEqual(l['stop'], l['S0'])

    def test_full_exit_has_priority_over_partial(self):
        l = lot()
        X.protect(l, 110.0, 4.5, P, 'KRW-X', 1.0)
        self.assertEqual(X.protect(l, 104.0, 4.5, P, 'KRW-X', 1.0), ('exit', '수익 반납'))

    def test_stop_never_lowered(self):
        l = lot()
        X.protect(l, 105.0, 4.5, P, 'KRW-X', 0)
        s = l['stop']
        X.protect(l, 102.0, 4.5, P, 'KRW-X', 0)
        self.assertGreaterEqual(l['stop'], s)


class Rebuy(unittest.TestCase):
    r = {'market': 'KRW-X', 'rank': 2, 'rs': 0.05, 'atr': 1.0, 'close': 100.0}

    def check(self, hours, ask, r=None, entry_ok=None, equity=1e6, budget=20000.0):
        right = {'t': 0.0, 'px': 110.0, 'budget': budget}
        return X.rebuy_ok(right, hours * 3600, ask, r if r is not None else self.r,
                          entry_ok if entry_ok is not None else {'KRW-X'}, 0, equity, 0, 1e9, P)

    def test_window_price_and_signal(self):
        self.assertEqual(self.check(1.9, 100)[0], '대기 중')
        self.assertEqual(self.check(3.1, 100)[0], '기한 만료')
        self.assertIn('97%', self.check(2.5, 107)[0])
        self.assertIn('유지 순위', self.check(2.5, 100, dict(self.r, rank=6))[0])
        self.assertIn('유지 순위', self.check(2.5, 100, dict(self.r, rs=-0.01))[0])

    def test_all_new_buy_limits_apply(self):
        self.assertIn('신규 매수 불가', self.check(2.5, 100, entry_ok=set())[0])      # 경보·자료 없음
        self.assertIn('과열', self.check(2.5, 100, dict(self.r, rs=0.2))[0])         # 과열 15%
        self.assertIn('눌려야', self.check(2.5, 100, dict(self.r, atr=20.0))[0])     # ATR 눌림
        why, amt = self.check(2.5, 100, equity=200000, budget=50000)
        self.assertIsNone(why)
        self.assertAlmostEqual(amt, 0.00225 * 200000 / 0.045)                       # 위험 기준 금액 1만 원

    def test_allowed_and_budget_capped(self):
        why, amt = self.check(2.5, 100)
        self.assertIsNone(why)
        self.assertEqual(amt, 20000.0)


class BookFlow(unittest.TestCase):
    def test_bundle_counts_partial_rebuy_and_rotation_once_and_records_are_atomic(self):
        with tempfile.TemporaryDirectory() as d:
            bk = X.Book('G3', X.GROUPS['G3_재매수'], Path(d))
            Pos = bk.buy('KRW-X', 100000, 100.0, 5.0, P)
            main = Pos['main']
            bk.sell_qty('KRW-X', main, main['qty0'] / 3, 106.0, P, '부분 익절')
            lot2 = bk.open_lot('KRW-X', 300.0, 102.0, 5.0, P, 'rebuy')
            Pos['lot'] = lot2
            Pos['spent'] += lot2['spend']
            bk.close_all('KRW-X', 104.0, P, '순환')
            bk.save()
            saved = json.loads((Path(d) / 'G3' / 'state.json').read_text(encoding='utf-8'))
            self.assertEqual(len(saved['bundles']), 1)
            self.assertEqual(len(saved['trades']), 4)       # 매수·부분 익절·순환 2건이 장부와 같은 파일
            self.assertAlmostEqual(bk.d['cash'], X.CAPITAL + saved['bundles'][0]['pnl'], places=1)

    def test_summary_dedupes_bundles_and_skips_incomplete_equity(self):
        state = {'bundles': [{'bundle': 'a', 'pnl': 10, 'pnl_pct': 1}, {'bundle': 'a', 'pnl': 10, 'pnl_pct': 1}],
                 'equity': [{'equity': 100}, {'equity': 50, 'complete': False}, {'equity': 90}], 'pos': {}}
        s = X.summarize(state, 100)
        self.assertEqual(s['n'], 1)
        self.assertAlmostEqual(s['mdd'], 0.1)
        self.assertEqual(s['skipped'], 1)


class Frozen(unittest.TestCase):
    def test_restart_uses_frozen_params_not_current_config(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(X, 'load_params', return_value=(P, {'live': {}})):
                a = X.Study(Path(d))
            changed = replace(P, M=8, k=2.0)
            with mock.patch.object(X, 'load_params', return_value=(changed, {'live': {}})):
                b = X.Study(Path(d))
            self.assertEqual((b.base.M, b.base.k), (a.base.M, a.base.k))


class Replay(unittest.TestCase):
    def test_missed_minute_bars_are_replayed_in_order(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(X, 'load_params', return_value=(P, {'live': {}})):
                s = X.Study(Path(d))
            s.books = {'G2_R0_1R0익절': s.books['G2_R0_1R0익절']}
            bk = s.books['G2_R0_1R0익절']
            Pos = bk.buy('KRW-X', 100000, 100.0, 5.0, P)
            Pos['main']['time'] = '2000-01-01T00:00:00+09:00'
            Pos['last_bar'] = '2000-01-01T00:00:00+00:00'
            bars = [{'t': f'2000-01-01T00:0{i}:00+00:00', 'o': c, 'h': c, 'l': c, 'c': c} for i, c in enumerate([101, 104.5, 109.5, 103], 1)]
            with mock.patch('upbit.completed_candles', return_value=bars), \
                    mock.patch('upbit.orderbooks', return_value={'KRW-X': {'ask': 103.1, 'bid': 103.0}}), \
                    mock.patch('time.sleep'):
                s.minute_bars()
            kinds = [t['reason'] for t in bk.d['trades'] if t['side'] == '매도']
            self.assertEqual(kinds[0], '부분 익절')                 # 104.5 봉에서 1R0 익절
            self.assertNotIn('KRW-X', bk.d['pos'])                 # 109.5 고점 뒤 103: 반납 50% 이상 청산
            self.assertEqual(Pos['last_bar'], bars[-1]['t'])


if __name__ == '__main__':
    unittest.main()
