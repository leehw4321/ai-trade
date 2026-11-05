"""과열 상한 예외(사용자 지시 2026-11-05): 직접 지정 15~20%, 이익 청산 뒤 재진입 1회, 과열 보유는 순위 매도 제외."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import chase_override as co
import live
import shadow
import strategy as st

CAPS = {'max_order_krw': 10 ** 9, 'max_total_buy_krw': 10 ** 9, 'max_sell_krw_per_plan': 10 ** 9}


class Rules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.p = st.Params()

    def test_promote_only_between_cap_and_20_percent_and_expires(self):
        self.assertFalse(co.promote(self.dir, 'KRW-A', 0.14, self.p, now=1000)[0])
        self.assertFalse(co.promote(self.dir, 'KRW-A', 0.20, self.p, now=1000)[0])
        self.assertTrue(co.promote(self.dir, 'KRW-A', 0.15, self.p, now=1000)[0])
        self.assertIn('KRW-A', co.promoted(self.dir, now=1000 + 23 * 3600))
        self.assertNotIn('KRW-A', co.promoted(self.dir, now=1000 + 24 * 3600))
        self.assertTrue(co.unpromote(self.dir, 'KRW-A')[0])
        self.assertEqual(co.promoted(self.dir, now=1000), {})

    def test_disabled_cap_means_no_exception(self):
        p = st.Params(chase_rs_cap=0.0)
        self.assertFalse(co.promote(self.dir, 'KRW-A', 0.17, p, now=1000)[0])
        self.assertEqual(co.cap_for('KRW-A', p, {'KRW-A': 1e12}, None), 0.0)

    def test_reentry_once_after_profit_within_24h(self):
        now = 100000
        last = {'time': now - 3600, 'profit': True}
        self.assertTrue(co.reentry_ok(last, now))
        self.assertFalse(co.reentry_ok({**last, 'profit': False}, now))
        self.assertFalse(co.reentry_ok({**last, 'from_reentry': True}, now))
        self.assertFalse(co.reentry_ok({**last, 'time': now - 24 * 3600}, now))
        self.assertEqual(co.cap_for('KRW-A', self.p, {}, last, now), 0.20)
        self.assertEqual(co.cap_for('KRW-A', self.p, {}, None, now), 0.15)

    def test_hot_held_is_sticky_while_held_and_cleared_after_sale(self):
        self.assertEqual(co.update_hot_held(self.dir, {'KRW-A', 'KRW-B'}, {'KRW-A': 0.16, 'KRW-B': 0.05}, self.p), {'KRW-A'})
        self.assertEqual(co.update_hot_held(self.dir, {'KRW-A', 'KRW-B'}, {'KRW-A': 0.02, 'KRW-B': 0.05}, self.p), {'KRW-A'})
        self.assertEqual(co.update_hot_held(self.dir, {'KRW-B'}, {'KRW-B': 0.05}, self.p), set())

    def test_bad_files_fall_back_to_the_15_percent_rule(self):
        (self.dir / co.PROMOTED).write_text('{broken', encoding='utf-8')
        (self.dir / co.HOT_HELD).write_text('[1, 2]', encoding='utf-8')
        self.assertEqual(co.promoted(self.dir), {})
        self.assertEqual(co.hot_held(self.dir), set())


class Proposal(unittest.TestCase):
    def setUp(self):
        for method in ('accounts', 'tickers', 'orderbooks', 'market_buy', 'market_sell'):
            block = patch.object(shadow.upbit, method, side_effect=AssertionError('unexpected external call'))
            block.start()
            self.addCleanup(block.stop)
        self.p = st.Params(M=9, entry_want=3, entry_scan=7, coin_cap=1., total_cap=1.)

    def plan(self, ranked, holdings=None, **kw):
        return shadow.proposal(holdings or {}, ranked, {}, {r['market'] for r in ranked}, self.p, CAPS, 990_000, 0, **kw)

    def test_hot_holding_is_not_sold_when_rank_drops(self):
        ranked = [{'market': f'KRW-C{i}', 'rank': i, 'rs': .05} for i in range(1, 12)]
        text, _ = self.plan(ranked, {'KRW-C11': 80000})
        self.assertIn('매도 C11', text)
        text, reasons = self.plan(ranked, {'KRW-C11': 80000}, hot={'KRW-C11'})
        self.assertNotIn('매도 C11', text)
        self.assertTrue(any('과열 보유' in r for r in reasons))

    def test_rank_exit_off_keeps_every_holding_for_stop_and_profit_guard(self):
        ranked = [{'market': f'KRW-C{i}', 'rank': i, 'rs': .05} for i in range(1, 12)] + [{'market': 'KRW-NEG', 'rank': 12, 'rs': -.03}]
        holdings = {'KRW-C11': 80000, 'KRW-NEG': 80000, 'KRW-OUT': 80000}   # 순위 밖·상대강도 음수·상위 30 밖
        text, _ = self.plan(ranked, holdings)
        self.assertIn('매도 C11', text)
        self.p.rank_exit = False
        text, reasons = self.plan(ranked, holdings)
        self.assertIn('매도 없음', text)
        self.assertEqual(sum('순위 매도 안 함' in r for r in reasons), 3)

    def test_protected_holdings_do_not_eat_the_bot_investment_room(self):
        # 실제 11-05 12시 계좌: 보호 157만 + ETH 8만 + 현금 30만. 순위 매도를 끄면 보호 종목이 투입액에 남아 매수 여력이 0이 되던 결함
        self.p.rank_exit = False
        self.p.total_cap, self.p.size_by_risk = 0.5, False
        ranked = [{'market': 'KRW-A', 'rank': 1, 'rs': .05}]
        holdings = {'KRW-BTC': 529000, 'KRW-XRP': 524000, 'KRW-DOGE': 516000, 'KRW-ETH': 80000}
        text, reasons = shadow.proposal(holdings, ranked, {}, {'KRW-A'}, self.p, CAPS, 302000, 0)
        self.assertIn('매수 없음', text)
        text, reasons = shadow.proposal(holdings, ranked, {}, {'KRW-A'}, self.p, CAPS, 302000, 0, protected={'KRW-BTC', 'KRW-XRP', 'KRW-DOGE'})
        self.assertIn('매수 A', text)
        self.assertIn('매도 없음', text)

    def test_four_slots_and_stuck_marking_after_rank_exit_off(self):
        self.p.rank_exit, self.p.slots, self.p.entry_want = False, 4, 4
        ranked = [{'market': f'KRW-C{i}', 'rank': i, 'rs': .05} for i in range(1, 12)]
        holdings = {'KRW-C11': 70000, 'KRW-C10': 90000, 'KRW-BTC': 500000}
        costs = {'KRW-C11': 80000, 'KRW-C10': 80000}
        stuck = {}
        text, reasons = self.plan(ranked, holdings, protected={'KRW-BTC'}, costs=costs, stuck_out=stuck)
        self.assertEqual({m: s['state'] for m, s in stuck.items()}, {'KRW-C11': '손실 중', 'KRW-C10': '이익 중'})
        self.assertEqual(text.count('매수 '), 2)                 # 봇 보유 2(보호 BTC 제외) → 4종목까지 2개 더
        self.assertTrue(any('봇 보유 2/4종목 (묶임 2)' in r for r in reasons))
        text, _ = self.plan(ranked, {f'KRW-C{i}': 80000 for i in range(8, 12)})
        self.assertIn('매수 없음', text)                          # 4/4면 신규 매수 없음

    def test_promoted_17_percent_is_bought_but_21_percent_is_not(self):
        ranked = [{'market': 'KRW-A', 'rank': 1, 'rs': .17}, {'market': 'KRW-B', 'rank': 2, 'rs': .21}]
        text, _ = self.plan(ranked)
        self.assertIn('매수 없음', text)
        text, _ = self.plan(ranked, promoted={'KRW-A': time.time() + 3600, 'KRW-B': time.time() + 3600})
        self.assertIn('매수 A', text)
        self.assertNotIn('매수 B', text)

    def test_profit_exit_reentry_skips_wait_and_dip_once(self):
        ranked = [{'market': 'KRW-A', 'rank': 1, 'rs': .18, 'close': 110., 'atr': 1.}]
        fresh = {'KRW-A': {'time': time.time() - 600, 'price': 100., 'profit': True}}
        text, _ = self.plan(ranked, last_sells=fresh)
        self.assertIn('매수 A', text)
        text, _ = self.plan(ranked, last_sells={'KRW-A': {**fresh['KRW-A'], 'from_reentry': True}})
        self.assertIn('매수 없음', text)
        text, _ = self.plan(ranked, last_sells={'KRW-A': {**fresh['KRW-A'], 'profit': False}})
        self.assertIn('매수 없음', text)


class Receiver(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.worker = live.Live(self.data / 'live', {'enabled': True}, st.Params())
        self.end, self.now = 18000, 18050
        clock = patch.object(live.time, 'time', return_value=self.now)
        clock.start(); self.addCleanup(clock.stop)
        self.ctx = {'market': 'KRW-A', 'signal_close': 117., 'anchor_close': 100., 'rs': .17,
                    'signal_closed_at': self.end, 'signal_tf_minutes': 15, 'evaluated_at': 18025}

    def test_receiver_allows_promoted_up_to_20_percent_from_its_own_file(self):
        self.assertIn('과열', self.worker.entry_chase_block('KRW-A', self.ctx, 117.5))
        co.promote(self.data, 'KRW-A', .17, self.worker.p, now=self.now)
        self.assertIsNone(self.worker.entry_chase_block('KRW-A', self.ctx, 117.5))
        self.assertIn('과열', self.worker.entry_chase_block('KRW-A', self.ctx, 120.))

    def test_receiver_allows_profit_reentry_only_once(self):
        self.worker.d['last_sell'] = {'KRW-A': {'time': self.now - 600, 'price': 100., 'profit': True}}
        self.assertIsNone(self.worker.entry_chase_block('KRW-A', self.ctx, 117.5))
        self.worker.d['last_sell']['KRW-A']['from_reentry'] = True
        self.assertIn('과열', self.worker.entry_chase_block('KRW-A', self.ctx, 117.5))


if __name__ == '__main__':
    unittest.main()
