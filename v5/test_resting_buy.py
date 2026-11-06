"""호가 1칸이 큰 종목의 지정가 매수(사용자 지시 2026-11-06, study_limit_entry): 매수 1호가 post_only → 15분 → 시장가 전환."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot
import live
import strategy as st
import upbit


class Ticks(unittest.TestCase):
    def test_tick_table_and_price_string(self):
        self.assertEqual(upbit.tick_size(146), 1)
        self.assertEqual(upbit.tick_size(99.9), .1)
        self.assertEqual(upbit.tick_size(2_000_000), 1000)
        self.assertEqual(upbit.price_str(146.0), '146')
        self.assertEqual(upbit.price_str(3.487), '3.48')


class Resting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.lv = live.Live(Path(self.tmp.name) / 'live', {'enabled': True, 'resting_buy_enabled': True,
                                                             'max_order_krw': 10 ** 9, 'max_total_buy_krw': 10 ** 9}, st.Params())
        self.now = 1_000_000.0
        c = patch.object(live.time, 'time', side_effect=lambda: self.now); c.start(); self.addCleanup(c.stop)
        for name, value in (('entry_chase_block', None), ('is_protected', False), ('scan_manual_orders', None)):
            q = patch.object(self.lv, name, return_value=value); q.start(); self.addCleanup(q.stop)
        q = patch.object(live.upbit, 'completed_candles', return_value=[]); q.start(); self.addCleanup(q.stop)
        q = patch.object(live.upbit, 'tickers', side_effect=RuntimeError('no network')); q.start(); self.addCleanup(q.stop)
        q = patch.object(live.st, 'wilder_atr', return_value=2.0); q.start(); self.addCleanup(q.stop)
        self.book = {'KRW-CAP': {'ask': 147.0, 'bid': 146.0}, 'KRW-BIG': {'ask': 3000.0, 'bid': 2999.0}}
        q = patch.object(live.upbit, 'orderbooks', side_effect=lambda ms: {m: self.book[m] for m in ms}); q.start(); self.addCleanup(q.stop)
        self.order = {'uuid': 'u1', 'identifier': None, 'state': 'wait', 'trades': [], 'paid_fee': '0'}
        q = patch.object(live.upbit, 'limit_buy_post_only', side_effect=self._post); q.start(); self.addCleanup(q.stop)
        q = patch.object(live.upbit, 'get_order', side_effect=lambda u: dict(self.order)); q.start(); self.addCleanup(q.stop)
        self.cancelled = []
        q = patch.object(live.upbit, 'cancel_order', side_effect=self._cancel); q.start(); self.addCleanup(q.stop)
        self.market_buys = []
        q = patch.object(self.lv, '_trade', side_effect=lambda side, m, krw, reason, **k: self.market_buys.append((m, krw)) or
                         {'trades': [{'volume': '1', 'funds': str(krw)}], 'paid_fee': '0', 'state': 'done'}); q.start(); self.addCleanup(q.stop)

    def _post(self, m, price, volume, identifier=None):
        self.sent = (m, price, volume)
        self.order['identifier'] = identifier
        return {'uuid': 'u1'}

    def _cancel(self, u):
        self.cancelled.append(u); self.order['state'] = 'cancel'; return {}

    def _fill(self, vol, funds):
        self.order['trades'] = [{'volume': str(vol), 'funds': str(funds)}]
        self.order['paid_fee'] = str(funds * 0.0005)

    def place(self):
        msg = self.lv._rest_buy('KRW-CAP', 80000, '봇 주문안 t', {'market': 'KRW-CAP', 'anchor_close': 130.0})
        self.assertIn('지정가 매수 대기 146', msg)
        return msg

    def test_candidate_only_big_tick_and_only_when_enabled(self):
        self.assertEqual(self.lv.resting_candidates(['KRW-BIG', 'KRW-CAP']), {'KRW-CAP'})   # 1칸 0.68% vs 0.03%
        self.lv.cfg['resting_buy_enabled'] = False
        self.assertEqual(self.lv.resting_candidates(['KRW-CAP']), set())

    def test_places_post_only_one_tick_below_ask_and_returns_without_waiting(self):
        self.place()
        self.assertEqual(self.sent[:2], ('KRW-CAP', 146.0))
        self.assertFalse(self.lv.has_uncertain_orders())             # 다른 종목 매수·손절을 막지 않는다
        self.assertTrue(self.lv.market_has_uncertain_order('KRW-CAP'))
        self.assertEqual(self.lv.resting_info()['KRW-CAP']['price'], 146.0)

    def test_partial_fill_goes_into_the_ledger_while_waiting(self):
        self.place()
        self._fill(200, 29200)
        self.lv.manage_resting({})
        self.assertAlmostEqual(self.lv.d['positions']['KRW-CAP']['qty'], 200)
        self.assertEqual(self.cancelled, [])

    def test_deadline_cancels_and_buys_the_rest_at_market_if_price_did_not_run(self):
        self.place(); self._fill(200, 29200)
        self.now += 901
        self.lv.manage_resting({})
        self.assertEqual(self.cancelled, ['u1'])
        self.assertEqual(len(self.market_buys), 1)
        self.assertAlmostEqual(self.market_buys[0][1], int(80000 - 29200 - 14.6))
        self.assertEqual(self.lv.resting_info(), {})

    def test_deadline_skips_market_buy_when_price_ran_more_than_one_tick(self):
        self.place()
        self.book['KRW-CAP'] = {'ask': 149.0, 'bid': 148.0}
        self.now += 901
        self.lv.manage_resting({})
        self.assertEqual(self.market_buys, [])
        self.assertFalse(self.lv.market_has_uncertain_order('KRW-CAP'))

    def test_buy_now_button_forces_market_even_if_price_ran(self):
        self.place()
        self.book['KRW-CAP'] = {'ask': 149.0, 'bid': 148.0}
        self.assertTrue(self.lv.request_resting_now('KRW-CAP')[0])
        self.lv.manage_resting({})
        self.assertEqual([m for m, _ in self.market_buys], ['KRW-CAP'])

    def test_near_stop_cancels_without_market_buy(self):
        self.place(); self._fill(200, 29200)
        self.lv.manage_resting({})
        pos = self.lv.d['positions']['KRW-CAP']
        self.lv.manage_resting({'KRW-CAP': self.lv.stop_limit(pos)})
        self.assertEqual(self.cancelled, ['u1'])
        self.assertEqual(self.market_buys, [])

    def test_two_markets_rest_at_the_same_time(self):
        self.place()
        self.book['KRW-SOPH'] = {'ask': 3.49, 'bid': 3.48}
        msg = self.lv._rest_buy('KRW-SOPH', 80000, '봇 주문안 t', {'market': 'KRW-SOPH'})
        self.assertIn('지정가 매수 대기 3.48', msg)
        self.assertEqual(set(self.lv.resting_info()), {'KRW-CAP', 'KRW-SOPH'})

    def test_cancel_without_market_buy_when_18h_gain_turns_non_positive(self):
        self.place()
        self.lv.manage_resting({'KRW-CAP': 130.0})
        self.assertEqual(self.cancelled, ['u1']); self.assertEqual(self.market_buys, [])

    def test_cancel_when_loss_halt_or_new_judgment_says_no(self):
        self.place()
        self.lv.manage_resting({'KRW-CAP': 146.0}, lambda m, r: None)
        self.assertEqual(self.cancelled, [])
        self.lv.manage_resting({'KRW-CAP': 146.0}, lambda m, r: '순위 7위 밖')
        self.assertEqual(self.cancelled, ['u1']); self.assertEqual(self.market_buys, [])
        self.order.update(state='wait'); self.cancelled.clear()
        self.place(); self.lv.d['halted'] = True
        self.lv.manage_resting({'KRW-CAP': 146.0})
        self.assertEqual(self.cancelled, ['u1']); self.assertEqual(self.market_buys, [])

    def test_post_only_rejected_immediately_falls_back_to_market(self):
        self.place()
        self.order['state'] = 'cancel'                     # 호가가 움직여 즉시 체결될 가격이 되면 거래소가 취소한다
        self.lv.manage_resting({})
        self.assertEqual([m for m, _ in self.market_buys], ['KRW-CAP'])


class StillOk(unittest.TestCase):
    def test_new_15m_judgment_rechecks_rank_gain_cap_regime_warning(self):
        from types import SimpleNamespace
        p = st.Params(entry_scan=7, regime_filter=True)
        b = SimpleNamespace(p=p, live=SimpleNamespace(d={}), rs_all={'KRW-CAP': (3, .1)},
                            entry_view={'end': 2000, 'entry_ok': {'KRW-CAP'}, 'regime': .002})
        ok = lambda: bot.Bot.resting_still_ok(b, 'KRW-CAP', {'placed': 1000})
        self.assertIsNone(ok())
        self.assertIsNone(bot.Bot.resting_still_ok(b, 'KRW-CAP', {'placed': 3000}))   # 걸기 전 판단이면 보지 않는다
        b.rs_all['KRW-CAP'] = (9, .1); self.assertIn('순위', ok())
        b.rs_all['KRW-CAP'] = (3, -.01); self.assertIn('상승률', ok())
        b.rs_all['KRW-CAP'] = (3, .2); self.assertIn('과열', ok())
        b.rs_all['KRW-CAP'] = (3, .1); b.entry_view['regime'] = -.001; self.assertIn('장세', ok())
        b.entry_view['regime'] = .002; b.entry_view['entry_ok'] = set(); self.assertIn('경보', ok())


class Display(unittest.TestCase):
    def test_ranking_row_shows_resting_badge_countdown_and_button(self):
        p = st.Params(entry_scan=7, entry_want=4)
        rows = bot.ranking_rows([{'market': 'KRW-CAP', 'rs': .1, 'rank': 3}], set(), {}, p, can_promote=True,
                                resting={'KRW-CAP': {'market': 'KRW-CAP', 'price': 146.0, 'deadline': 1_000_900}})
        self.assertIn('지정가 매수 중 146원', rows)
        self.assertIn("data-dl='1000900'", rows)
        self.assertIn('data-act=buy_now', rows)
        self.assertIn('rest-cd', bot.promote_script(None))


if __name__ == '__main__':
    unittest.main()
