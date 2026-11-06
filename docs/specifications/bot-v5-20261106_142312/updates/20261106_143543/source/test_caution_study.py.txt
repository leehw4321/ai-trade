"""D-59 주의 종목 그림자 비교 테스트 (네트워크 없음)."""
import datetime as dt
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import caution_study as C
import exit_study as X
import strategy as st

P = replace(st.Params(), trail_enabled=True, stop_max_pct=0.045, k=3.0, entry_risk=0.00184, size_by_risk=True,
            entry_want=3, entry_scan=5, M=5, chase_rs_cap=0.15, universe=3, L=4)


class Allowed(unittest.TestCase):
    def test_only_price_and_volume_caution(self):
        ok = lambda types, inv=False: C.caution_allowed({'warning': True, 'investment_warning': inv, 'caution_types': types})
        self.assertTrue(ok(['TRADING_VOLUME_SOARING']))
        self.assertTrue(ok(['PRICE_FLUCTUATIONS', 'TRADING_VOLUME_SOARING']))
        self.assertFalse(ok(['TRADING_VOLUME_SOARING', 'DEPOSIT_AMOUNT_SOARING']))   # 입금량 급등 섞임
        self.assertFalse(ok(['GLOBAL_PRICE_DIFFERENCES']))
        self.assertFalse(ok(['TRADING_VOLUME_SOARING'], inv=True))                    # 투자유의
        self.assertFalse(C.caution_allowed({'warning': True, 'investment_warning': False, 'caution_types': 'x'}))
        self.assertFalse(C.caution_allowed(None))                                     # 조회 실패
        self.assertFalse(ok([]))

    def test_rise_1h(self):
        b = [{'c': c} for c in (10, 11, 12, 13, 14)]
        self.assertTrue(C.rise_1h(b))
        self.assertFalse(C.rise_1h(b[:4]))
        self.assertFalse(C.rise_1h([{'c': c} for c in (14, 13, 12, 11, 14)]))


def bars(end, closes):
    start = end - len(closes) * 900
    return [{'t': dt.datetime.fromtimestamp(start + i * 900, dt.timezone.utc).isoformat(), 'o': c, 'h': c * 1.01,
             'l': c * 0.99, 'c': c} for i, c in enumerate(closes)]


class Decide(unittest.TestCase):
    def run_decide(self, warn_types, spread_bid=99.8, open_caution=False):
        end = (int(time.time()) // 900) * 900
        markets = {'KRW-A': {'warning': True, 'investment_warning': False, 'caution_types': warn_types},
                   'KRW-B': {'warning': False, 'investment_warning': False, 'caution_types': []},
                   'KRW-C': {'warning': False, 'investment_warning': False, 'caution_types': []}}
        closes = {'KRW-A': [100] * 16 + [104, 106, 108, 110, 112],    # 강한 상승, 1시간 상승
                  'KRW-B': [100] * 16 + [100, 100, 100, 100, 100.5],
                  'KRW-C': [100] * 21, 'KRW-Z': [10] * 21}
        tick = {m: {'acc_trade_price_24h': 1e9 - i, 'trade_price': closes[m][-1]} for i, m in enumerate(markets)}
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(C, 'load_params', return_value=(P, {'live': {}})), \
                    mock.patch.object(X, 'load_params', return_value=(P, {'live': {}})):
                s = C.CautionStudy(Path(d))
            if open_caution:
                bk = s.books['C1_주의허용']
                Pz = bk.buy('KRW-Z', 50000, 10.0, 0.3, s.base)
                bk.d['caution_bundles'] = [Pz['bundle']]
            for bk in s.books.values():
                bk.d['last_decision'] = end - 900
            with mock.patch('upbit.krw_markets', return_value=markets), mock.patch('upbit.tickers', return_value=tick), \
                    mock.patch('upbit.completed_candles', side_effect=lambda m, tf, n: bars(end, closes[m])), \
                    mock.patch('upbit.orderbooks', side_effect=lambda ms: {m: {'ask': 100.0, 'bid': spread_bid if m == 'KRW-A' else 99.9} for m in ms}), \
                    mock.patch.object(s, 'atr4', return_value=1.0), mock.patch('time.sleep'):
                s.decide(end)
            return {n: {m: P2['spent'] for m, P2 in b.d['pos'].items()} for n, b in s.books.items()}, s

    def test_c1_buys_allowed_caution_at_half_and_c0_does_not(self):
        pos, s = self.run_decide(['TRADING_VOLUME_SOARING'])
        self.assertNotIn('KRW-A', pos['C0_경보차단'])
        self.assertIn('KRW-A', pos['C1_주의허용'])
        self.assertLess(pos['C1_주의허용']['KRW-A'], pos['C1_주의허용']['KRW-B'])   # 절반 금액
        t = [x for x in s.books['C1_주의허용'].d['trades'] if x.get('caution')]
        self.assertAlmostEqual(t[0]['spread'], 0.002)

    def test_excluded_types_wide_spread_and_open_limit(self):
        self.assertNotIn('KRW-A', self.run_decide(['DEPOSIT_AMOUNT_SOARING'])[0]['C1_주의허용'])
        self.assertNotIn('KRW-A', self.run_decide(['TRADING_VOLUME_SOARING'], spread_bid=99.5)[0]['C1_주의허용'])
        self.assertNotIn('KRW-A', self.run_decide(['TRADING_VOLUME_SOARING'], open_caution=True)[0]['C1_주의허용'])


class Halt(unittest.TestCase):
    def test_pilot_loss_counts_realized_and_open(self):
        with tempfile.TemporaryDirectory() as d:
            bk = X.Book('C1', C.GROUPS['C1_주의허용'], Path(d))
            bk.p = P
            Pa = bk.buy('KRW-A', 100000, 100.0, 1.0, P)
            bk.d['caution_bundles'] = [Pa['bundle']]
            self.assertLess(C.pilot_pnl(bk, {'KRW-A': 89.0}), C.PILOT_LOSS)   # 약 −11% 평가손
            bk.close_all('KRW-A', 95.0, P, '손절')
            self.assertLess(C.pilot_pnl(bk, {}), 0)


if __name__ == '__main__':
    unittest.main()
