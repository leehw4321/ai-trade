import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import rank_defer as rd
import shadow
import strategy as st

CAPS = {'max_order_krw': 300000, 'max_total_buy_krw': 10**9, 'max_sell_krw_per_plan': 10**9}
T0 = 1_794_000_000.0
OPEN = '2026-11-10T08:00:00+09:00'


class Generator(unittest.TestCase):
    """주문안 생성(정시)에서 손실 중 순위 매도를 미루고, 4시간 뒤 만료되면 판다."""

    def setUp(self):
        for method in ('accounts', 'tickers', 'orderbooks', 'market_buy', 'market_sell'):
            block = patch.object(shadow.upbit, method, side_effect=AssertionError('unexpected external call'))
            block.start(); self.addCleanup(block.stop)
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.p = st.Params(M=9, entry_want=3, entry_scan=7, coin_cap=1., total_cap=1.)
        self.ranked = [{'market': f'KRW-C{i}', 'rank': i, 'rs': .05} for i in range(1, 12)]
        self.state = {}

    def plan(self, holdings, pos, now, ranked=None):
        ranked = ranked or self.ranked
        defer = {'state': self.state, 'pos': pos, 'now': now, 'hours': 4.0}
        text, reasons = shadow.proposal(holdings, ranked, {}, {r['market'] for r in ranked}, self.p, CAPS, 500_000, 0, defer=defer)
        sold = {'KRW-' + x for x in re.findall(r'매도 ([A-Z0-9]+) ', text)}
        rd.finish(self.d, self.state, set(holdings), sold, now)
        return text, reasons

    def events(self):
        return [json.loads(x)['event'] for x in (self.d / 'rank_defer.jsonl').read_text(encoding='utf-8').splitlines()]

    def test_loss_is_deferred_then_sold_after_four_hours(self):
        pos = {'KRW-C11': {'qty': 1000.0, 'cost': 100_000.0, 'time': OPEN}}
        text, reasons = self.plan({'KRW-C11': 98_000}, pos, T0)
        self.assertNotIn('매도 C11', text)
        self.assertTrue(any('손실 중 순위 매도 유예' in r for r in reasons))
        self.assertIn('KRW-C11', rd.load_state(self.d))
        for h in (1, 2, 3):
            text, _ = self.plan({'KRW-C11': 98_000}, pos, T0 + h * 3600 + 60)
            self.assertNotIn('매도 C11', text)
        text, reasons = self.plan({'KRW-C11': 98_000}, pos, T0 + 4 * 3600 + 60)
        self.assertIn('매도 C11', text)
        self.assertTrue(any('유예 4시간 만료' in r for r in reasons))
        self.assertEqual(rd.load_state(self.d), {})
        self.assertEqual(self.events(), ['유예 시작', '유예 만료 매도'])

    def test_profit_is_sold_immediately(self):
        pos = {'KRW-C11': {'qty': 1000.0, 'cost': 100_000.0, 'time': OPEN}}
        text, _ = self.plan({'KRW-C11': 101_000}, pos, T0)
        self.assertIn('매도 C11', text)
        self.assertEqual(rd.load_state(self.d), {})

    def test_unknown_cost_is_not_deferred(self):
        text, _ = self.plan({'KRW-C11': 98_000}, {}, T0)
        self.assertIn('매도 C11', text)

    def test_back_in_rank_releases_deferral(self):
        pos = {'KRW-C11': {'qty': 1000.0, 'cost': 100_000.0, 'time': OPEN}}
        self.plan({'KRW-C11': 98_000}, pos, T0)
        ranked = [{'market': 'KRW-C11', 'rank': 1, 'rs': .08}] + [{'market': f'KRW-C{i}', 'rank': i + 1, 'rs': .05} for i in range(1, 11)]
        text, _ = self.plan({'KRW-C11': 98_000}, pos, T0 + 3600, ranked)
        self.assertNotIn('매도 C11', text)
        self.assertEqual(rd.load_state(self.d), {})
        self.assertEqual(self.events(), ['유예 시작', '순위 복귀'])

    def test_deferred_holding_still_counts_as_invested(self):
        self.p.total_cap, self.p.size_by_risk = 0.5, False
        pos = {'KRW-C11': {'qty': 1000.0, 'cost': 600_000.0, 'time': OPEN}}
        text, _ = self.plan({'KRW-C11': 500_000}, pos, T0)
        self.assertNotIn('매도 C11', text)
        self.assertIn('매수 없음', text)   # 유예 중 보유가 총투입 50%를 채워 새로 사지 않는다


class BotWatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        rd.save_state(self.d, {'KRW-A': {'m': 'KRW-A', 'opened': OPEN, 'started': T0, 'value': 98_000, 'cost': 100_000.0, 'qty': 1000.0,
                                         'immediate_pnl': 98_000 * (1 - rd.FEE) - 100_000, 'hours': 4.0}})
        self.pos = {'KRW-A': {'qty': 1000.0, 'cost': 100_000.0, 'time': OPEN}}
        self.w = rd.Watch(self.d); self.calls = []

    def sell(self, m, w, px):
        self.calls.append(px)
        w['fill_funds'], w['fill_fee'], w['fill_vol'] = 1000 * px, 1000 * px * rd.FEE, 1000.0
        return True

    def test_sells_at_breakeven_and_banner_shows_effect(self):
        self.w.update(self.pos, {'KRW-A': 99.0}, live_sell=self.sell, now=T0 + 60)
        self.assertEqual(self.calls, [])
        be = 100 / (1 - rd.FEE)
        self.w.update(self.pos, {'KRW-A': be * 1.0011}, live_sell=self.sell, now=T0 + 120)
        self.assertEqual(len(self.calls), 1)
        s = self.w.summary()
        self.assertEqual((s['n'], s['hows']), (1, {'본전 매도': 1}))
        self.assertGreater(s['pnl_now'] - s['pnl_old'], 1900)
        html = rd.render(self.w, 4)
        self.assertIn('손실 유예 실험 중', html); self.assertIn('바로 팔았다면', html); self.assertIn('효과 +', html)

    def test_failed_sell_retries_after_a_minute(self):
        be = 100 / (1 - rd.FEE)
        for s in range(0, 60, 3):
            self.w.update(self.pos, {'KRW-A': be * 1.01}, live_sell=lambda *a: False, now=T0 + s)
        self.assertEqual(self.w.r['KRW-A|' + OPEN]['state'], 'deferred')
        n = sum(1 for x in (self.d / 'rank_defer.jsonl').read_text(encoding='utf-8').splitlines() if '보류' in x)
        self.assertEqual(n, 1)

    def test_closed_by_stop_records_actual_from_last_sell(self):
        self.w.update(self.pos, {'KRW-A': 97.0}, live_sell=self.sell, now=T0 + 60)
        self.w.update({}, {}, last_sell={'KRW-A': {'time': T0 + 100, 'price': 95.5, 'reason': '손절'}}, live_sell=self.sell, now=T0 + 120)
        r = self.w.r['KRW-A|' + OPEN]
        self.assertEqual((r['state'], r['how']), ('done', '손절'))
        self.assertLess(self.w.summary()['pnl_now'], self.w.summary()['pnl_old'])

    def test_returned_to_rank_follows_until_close(self):
        self.w.update(self.pos, {'KRW-A': 99.0}, live_sell=self.sell, now=T0 + 60)
        rd.save_state(self.d, {})
        self.w.update(self.pos, {'KRW-A': 200.0}, live_sell=self.sell, now=T0 + 120)   # 유예가 풀린 뒤엔 본전 매도를 하지 않는다
        self.assertEqual(self.calls, [])
        self.assertEqual(self.w.r['KRW-A|' + OPEN]['state'], 'returned')
        self.w.update({}, {}, last_sell={'KRW-A': {'time': T0 + 200, 'price': 103.0, 'reason': '이익보호'}}, now=T0 + 240)
        self.assertEqual(self.w.r['KRW-A|' + OPEN]['how'], '이익보호')

    def test_off_means_no_sell(self):
        be = 100 / (1 - rd.FEE)
        self.w.update(self.pos, {'KRW-A': be * 1.05}, live_sell=None, now=T0 + 60)
        self.assertEqual(self.w.r['KRW-A|' + OPEN]['state'], 'deferred')


if __name__ == '__main__':
    unittest.main()
