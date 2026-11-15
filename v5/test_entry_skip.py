"""2026-11-15 SANC: 손절 ATR 자료가 없는 신규 상장 종목이 주문안 전체를 멈춰 뒤 종목(PIEVERSE·UNI)까지 못 산 문제."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import live
import shadow
import strategy as st


def bars(n):
    return [{'t': f'{i}', 'o': 100, 'h': 101, 'l': 99, 'c': 100} for i in range(n)]


class SkipOneKeepOthers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.lv = live.Live(Path(self.tmp.name) / 'live', {'enabled': True, 'max_order_krw': 10 ** 9, 'max_total_buy_krw': 10 ** 9}, st.Params())

    def test_atr_skip_does_not_abort_following_buys(self):
        calls = []

        def trade(side, m, krw, reason, **kw):
            calls.append(m)
            if m == 'KRW-SANC':
                raise live.EntrySkip('손절 ATR 자료 부족·무효: 매수 보류')
            return {'state': 'done'}

        mk = {m: {'warning': False} for m in ('KRW-SANC', 'KRW-PIEVERSE')}
        with patch.object(self.lv, 'buy_block', return_value=None), patch.object(self.lv, 'resting_candidates', return_value=set()), \
                patch.object(self.lv, '_trade', side_effect=trade), patch.object(self.lv, '_fill', return_value=(1.0, 62000, 31)), \
                patch.object(live.upbit, 'accounts', return_value=[]), patch.object(live.upbit, 'tickers', return_value={}):
            self.lv.d['positions']['KRW-PIEVERSE'] = {'stop': 1.0}
            r = self.lv.execute(('봇 주문안 t', '매도 없음; 매수 SANC 62000원; 매수 PIEVERSE 62000원'), mk)
        self.assertEqual(calls, ['KRW-SANC', 'KRW-PIEVERSE'])
        self.assertIn('SANC 매수 안 함: 손절 ATR 자료 부족', r)
        self.assertIn('PIEVERSE 매수 62,031원', r)

    def test_entry_skip_is_still_a_value_error(self):
        self.assertTrue(issubclass(live.EntrySkip, ValueError))


class CandidateFilter(unittest.TestCase):
    def test_young_market_removed_and_ok_cached(self):
        ranked = [{'market': 'KRW-SANC'}, {'market': 'KRW-MEGA'}, {'market': 'KRW-X'}, {'market': 'KRW-FAR'}]
        got = []

        def fetch(m, tf):
            got.append((m, tf)); return bars(13 if m == 'KRW-SANC' else 99)
        cache = set()
        with patch.object(shadow.time, 'sleep'):
            out = shadow.atr_ready({'KRW-SANC', 'KRW-MEGA', 'KRW-FAR'}, ranked, 3, 240, cache, fetch)
            self.assertEqual(out, {'KRW-MEGA', 'KRW-FAR'})                  # 범위 밖 FAR는 손대지 않음
            self.assertEqual(cache, {'KRW-MEGA'})
            got.clear()
            shadow.atr_ready({'KRW-SANC', 'KRW-MEGA'}, ranked, 3, 240, cache, fetch)
            self.assertEqual(got, [('KRW-SANC', 240)])                       # 이미 확인된 MEGA는 다시 조회 안 함


if __name__ == '__main__':
    unittest.main()
