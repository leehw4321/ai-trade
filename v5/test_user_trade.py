"""사용자 직접 주문(2026-11-28): 순위표 직접 매수·상세 창 직접 매도. 실제 _trade 경로로 체결 반영·보호·손실 집계 분리·기록을 본다."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import live
import manual_orders


class UserTradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name); (root / 'data').mkdir()
        (root / 'data/user_protected_assets.json').write_text('{"markets":[]}')
        self.w = live.Live(root / 'data/live', {'enabled': True, 'max_order_krw': 10**9, 'max_total_buy_krw': 10**9}, live.st.Params())
        self.markets = {'KRW-A': {'warning': False}, 'KRW-B': {'warning': False}}
        self.px = 100.0

    def exchange(self):
        """market_buy/market_sell → 즉시 전량 체결(가격 self.px, 수수료 0.05%)."""
        sent = []

        def buy(market, krw, identifier):
            sent.append(('bid', market, krw)); v = krw / self.px
            self._o = {'uuid': 'u%d' % len(sent), 'identifier': identifier, 'market': market, 'side': 'bid', 'state': 'done',
                       'executed_volume': str(v), 'trades': [{'volume': str(v), 'funds': str(krw)}], 'paid_fee': str(krw * .0005)}
            return dict(self._o)

        def sell(market, vol, identifier):
            sent.append(('ask', market, vol)); f = vol * self.px
            self._o = {'uuid': 'u%d' % len(sent), 'identifier': identifier, 'market': market, 'side': 'ask', 'state': 'done',
                       'executed_volume': str(vol), 'trades': [{'volume': str(vol), 'funds': str(f)}], 'paid_fee': str(f * .0005)}
            return dict(self._o)
        return sent, buy, sell

    def patches(self, sent_buy_sell):
        _, buy, sell = sent_buy_sell
        qty = lambda m: sum(p['qty'] for k, p in self.w.d['positions'].items() if k == m)
        return [patch.object(live.upbit, 'market_buy', side_effect=buy), patch.object(live.upbit, 'market_sell', side_effect=sell),
                patch.object(live.upbit, 'accounts', side_effect=lambda: [{'currency': 'KRW', 'balance': '1000000', 'locked': '0'},
                             {'currency': 'A', 'balance': str(qty('KRW-A')), 'locked': '0', 'avg_buy_price': '100'}]),
                patch.object(live.upbit, 'orderbooks', return_value={'KRW-A': {'ask': self.px, 'bid': self.px * .999}}),
                patch.object(live.upbit, 'tickers', side_effect=lambda ms: {m: {'trade_price': self.px} for m in ms}),
                patch.object(live.upbit, 'completed_candles', return_value=[]),
                patch.object(live.st, 'wilder_atr', return_value=2.0),
                patch.object(self.w, '_wait', side_effect=lambda o: dict(self._o)),
                patch.object(self.w, 'scan_manual_orders', side_effect=lambda m=None, risk=False, amount=None, allow_protected=False: amount)]

    def run_with(self, fn):
        ex = self.exchange(); ps = self.patches(ex)
        for p in ps: p.start()
        try: return fn(), ex[0]
        finally:
            for p in ps: p.stop()

    def today(self):
        return self.w.raw_loss({'KRW-A': self.px}) - self.w.d.get('risk_loss_offset', 0.)

    def test_user_buy_protects_logs_context_and_keeps_bot_loss_flat(self):
        before = self.today()
        (ok, msg), sent = self.run_with(lambda: self.w.user_buy('KRW-A', 30000, self.markets, {'rank': 4, 'rs': 0.08}))
        self.assertTrue(ok, msg); self.assertEqual(sent, [('bid', 'KRW-A', 30000)])
        pos = self.w.d['positions']['KRW-A']
        self.assertEqual(pos['user_trade']['context']['rank'], 4)
        self.assertIn('KRW-A', self.w.protected_markets())                # 봇 자동 매매 제외
        with self.assertRaises(live.ProtectedAssetError): self.w._trade('ask', 'KRW-A', 1, '봇 손절', risk=True)
        self.assertAlmostEqual(self.today(), before, places=6)              # 봇 손실 집계와 분리
        self.px = 90.0
        self.assertAlmostEqual(self.today(), before, places=6)              # 보호 중 시세 변동도 봇 손실 아님
        rows = [json.loads(l) for l in self.w.user_trade_file.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(rows[0]['event'], 'open'); self.assertEqual(rows[0]['context']['rs'], 0.08)

    def test_user_add_and_sell_on_own_position_then_close(self):
        self.run_with(lambda: self.w.user_buy('KRW-A', 30000, self.markets, {'rank': 2}))
        tid = self.w.d['positions']['KRW-A']['user_trade']['id']
        (ok, msg), _ = self.run_with(lambda: self.w.user_buy('KRW-A', 20000, self.markets, {'rank': 3}))
        self.assertTrue(ok, msg)
        self.assertEqual(self.w.d['positions']['KRW-A']['user_trade']['id'], tid)   # 추가 매수 뒤에도 같은 거래
        before = self.today()
        self.px = 110.0
        (ok, msg), sent = self.run_with(lambda: self.w.user_sell('KRW-A', {'KRW-A': self.px}, 50))
        self.assertTrue(ok, msg); self.assertEqual(sent[0][0], 'ask')
        self.assertIn('KRW-A', self.w.d['positions'])
        (ok, msg), _ = self.run_with(lambda: self.w.user_sell('KRW-A', {'KRW-A': self.px}, 100))
        self.assertTrue(ok, msg); self.assertNotIn('KRW-A', self.w.d['positions'])
        self.assertAlmostEqual(self.today(), before, places=6)              # 사용자 실현손익은 봇 손실에 안 들어감
        rows = [json.loads(l) for l in self.w.user_trade_file.read_text(encoding='utf-8').splitlines()]
        self.assertEqual([r['event'] for r in rows], ['open', 'add', 'sell', 'sell'])
        self.assertTrue(rows[-1]['closed']); self.assertEqual({r['id'] for r in rows}, {tid})
        self.assertGreater(sum(r['realized'] for r in rows if r['event'] == 'sell'), 0)

    def test_refusals(self):
        self.w.d['positions']['KRW-B'] = {'qty': 1.0, 'cost': 100.0, 'stop': 90.0, 'time': '2026-11-28T00:00:00+09:00'}
        (ok, msg), sent = self.run_with(lambda: self.w.user_buy('KRW-B', 30000, self.markets))
        self.assertFalse(ok); self.assertIn('봇이 관리 중', msg)
        (ok, msg), _ = self.run_with(lambda: self.w.user_sell('KRW-B', {'KRW-B': 100.0}))
        self.assertFalse(ok); self.assertIn('봇이 관리 중', msg)
        for krw in (4999, 10**6, 30000.5):
            (ok, msg), _ = self.run_with(lambda: self.w.user_buy('KRW-A', krw, self.markets))
            self.assertFalse(ok)
        self.w.protection_policy.decide('KRW-C', True)
        (ok, msg), _ = self.run_with(lambda: self.w.user_buy('KRW-C', 30000, {'KRW-C': {}}))
        self.assertFalse(ok); self.assertIn('장부 밖', msg)
        self.assertEqual(sent, [])
        self.w.d['positions']['KRW-A'] = {'qty': 300.0, 'cost': 30000.0, 'stop': 90.0, 'time': '2026-11-28T00:00:00+09:00'}
        self.w.protection_policy.decide('KRW-A', True)
        ok, msg = self.w.liquidate_position('KRW-A', {'KRW-A': 100.0}, pct=100, ord_type='limit', user=True)
        self.assertFalse(ok); self.assertIn('시장가', msg)

    def test_endpoints(self):
        srv = manual_orders.ManualOrderServer(8765, token='t', allowed_kinds={'user_buy', 'user_sell'})
        body = lambda **k: json.dumps(dict(token='t', market='KRW-A', confirm=True, **k)).encode()
        self.assertEqual(srv.check('127.0.0.1:8765', 'null', body(krw=30000), 'user_buy'), (200, ('KRW-A', 30000)))
        self.assertEqual(srv.check('127.0.0.1:8765', 'null', body(krw=10), 'user_buy')[0], 400)
        self.assertEqual(srv.check('127.0.0.1:8765', 'null', body(pct=50, ord='market'), 'user_sell')[0], 200)
        self.assertEqual(srv.check('127.0.0.1:8765', 'null', body(pct=33), 'user_sell')[0], 400)


class UserLimitBuyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name); (root / 'data').mkdir()
        (root / 'data/user_protected_assets.json').write_text('{"markets":[]}')
        self.w = live.Live(root / 'data/live', {'enabled': True, 'max_order_krw': 10**9, 'max_total_buy_krw': 10**9}, live.st.Params())
        self.markets = {'KRW-A': {'warning': False}}
        self.order = {'uuid': 'L1', 'state': 'wait', 'trades': [], 'paid_fee': '0'}
        self.ps = [patch.object(live.upbit, 'accounts', return_value=[{'currency': 'KRW', 'balance': '1000000', 'locked': '0'}]),
                   patch.object(live.upbit, 'orderbooks', return_value={'KRW-A': {'ask': 100.0, 'bid': 99.9}}),
                   patch.object(live.upbit, 'completed_candles', return_value=[]),
                   patch.object(live.st, 'wilder_atr', return_value=2.0),
                   patch.object(live.upbit, 'limit_buy_post_only', side_effect=lambda m, px, v, identifier: (self.order.update(identifier=identifier, market=m, side='bid'), dict(self.order))[1]),
                   patch.object(live.upbit, 'get_order', side_effect=lambda u: dict(self.order)),
                   patch.object(live.upbit, 'cancel_order', side_effect=lambda u: self.order.update(state='cancel')),
                   patch.object(live.upbit, 'tickers', side_effect=lambda ms: {m: {'trade_price': 99.0} for m in ms}),
                   patch.object(live.upbit, 'market_buy', side_effect=AssertionError('no market order')),
                   patch.object(self.w, '_wait', side_effect=lambda o: dict(self.order)),
                   patch.object(self.w, 'scan_manual_orders', side_effect=lambda m=None, risk=False, amount=None, allow_protected=False: amount)]
        for x in self.ps: x.start(); self.addCleanup(x.stop)

    def test_limit_waits_protected_then_fill_becomes_user_trade(self):
        ok, msg = self.w.user_limit_buy('KRW-A', 30000, 98.0, self.markets, {'rank': 2})
        self.assertTrue(ok, msg)
        self.assertIn('KRW-A', self.w.protected_markets())          # 걸 때부터 보호
        self.assertIn('KRW-A', self.w.d['resting_orders'])
        self.w.manage_resting({'KRW-A': 99.0}, still_ok=lambda m, r: '봇 조건 붕괴')   # 봇 조건으로 취소하지 않음
        self.assertIn('KRW-A', self.w.d['resting_orders'])
        self.order.update(state='done', executed_volume='300', trades=[{'volume': '300', 'funds': '29400'}], paid_fee='14.7')
        self.w.manage_resting({'KRW-A': 98.0})
        self.assertNotIn('KRW-A', self.w.d.get('resting_orders', {}))
        pos = self.w.d['positions']['KRW-A']
        self.assertEqual(pos['qty'], 300.0); self.assertEqual(pos['user_trade']['context']['limit_price'], 98.0)
        rows = [json.loads(l) for l in self.w.user_trade_file.read_text(encoding='utf-8').splitlines()]
        self.assertEqual((rows[0]['event'], rows[0]['order']), ('open', 'limit'))

    def test_no_waiting_time_by_default(self):
        import time, bot, strategy as st
        ok, msg = self.w.user_limit_buy('KRW-A', 30000, 98.0, self.markets)
        self.assertTrue(ok, msg); self.assertIn('취소할 때까지', msg)
        r = self.w.d['resting_orders']['KRW-A']['resting']
        self.assertGreater(r['deadline'] - time.time(), 365 * 86400)
        json.dumps(self.w.d, allow_nan=False)                      # 장부 지문 계산(allow_nan=False)과 호환
        self.w.manage_resting({'KRW-A': 99.0})
        self.assertIn('KRW-A', self.w.d['resting_orders'])
        html = bot.ranking_rows([{'market': 'KRW-A', 'rs': .05, 'rank': 1}], set(), {}, st.Params(), can_promote=True,
                                resting=self.w.resting_info())
        self.assertIn('취소 전까지 유지', html); self.assertNotIn('rest_extend', html); self.assertIn('user_rest_cancel', html)

    def test_expiry_without_fill_cancels_and_unprotects_no_market_fallback(self):
        ok, _ = self.w.user_limit_buy('KRW-A', 30000, 98.0, self.markets)
        self.assertTrue(ok)
        self.w.d['resting_orders']['KRW-A']['resting']['deadline'] = 0
        self.w.manage_resting({'KRW-A': 99.0})
        self.assertNotIn('KRW-A', self.w.d.get('resting_orders', {}))
        self.assertNotIn('KRW-A', self.w.d['positions'])
        self.assertNotIn('KRW-A', self.w.protected_markets())       # 하나도 안 사졌으면 보호 되돌림

    def test_price_validation_and_cancel(self):
        for bad in (98.05, 60.0, -1, float('nan')):
            ok, msg = self.w.user_limit_buy('KRW-A', 30000, bad, self.markets); self.assertFalse(ok, bad)
        ok, _ = self.w.user_limit_buy('KRW-A', 30000, 97.0, self.markets); self.assertTrue(ok)
        ok, msg = self.w.user_limit_buy('KRW-A', 30000, 96.0, self.markets); self.assertFalse(ok); self.assertIn('진행 중', msg)
        ok, _ = self.w.cancel_user_rest('KRW-A'); self.assertTrue(ok)
        self.assertEqual(self.w.d['resting_orders']['KRW-A']['resting']['deadline'], 0)


class UserLimitSellTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name); (root / 'data').mkdir()
        (root / 'data/user_protected_assets.json').write_text('{"markets":[]}')
        self.w = live.Live(root / 'data/live', {'enabled': True, 'max_order_krw': 10**9, 'max_total_buy_krw': 10**9}, live.st.Params())
        self.w.d['positions']['KRW-A'] = {'qty': 1000.0, 'cost': 100000.0, 'stop': 90.0, 'time': '2026-11-28T00:00:00+09:00',
                                          'user_trade': {'id': 'U1', 'opened': 'x', 'context': {}}}
        self.w.protection_policy.decide('KRW-A', True)
        self.order = {'uuid': 'S1', 'state': 'wait', 'trades': [], 'paid_fee': '0'}
        self.ps = [patch.object(live.upbit, 'accounts', return_value=[{'currency': 'A', 'balance': '1000', 'locked': '0'}]),
                   patch.object(live.upbit, 'orderbooks', return_value={'KRW-A': {'ask': 100.1, 'bid': 100.0}}),
                   patch.object(live.upbit, 'limit_sell_post_only', side_effect=lambda m, px, v, identifier: (self.order.update(identifier=identifier, market=m, side='ask', price=px, volume=v), dict(self.order))[1]),
                   patch.object(live.upbit, 'get_order', side_effect=lambda u: dict(self.order)),
                   patch.object(live.upbit, 'cancel_order', side_effect=lambda u: self.order.update(state='cancel')),
                   patch.object(live.upbit, 'tickers', side_effect=lambda ms: {m: {'trade_price': 85.0} for m in ms}),
                   patch.object(live.upbit, 'market_sell', side_effect=AssertionError('no market order')),
                   patch.object(self.w, '_wait', side_effect=lambda o: dict(self.order)),
                   patch.object(self.w, 'scan_manual_orders', side_effect=lambda m=None, risk=False, amount=None, allow_protected=False: amount)]
        for x in self.ps: x.start(); self.addCleanup(x.stop)

    def today(self, px):
        return self.w.raw_loss({'KRW-A': px}) - self.w.d.get('risk_loss_offset', 0.)

    def test_protected_limit_sell_ignores_bot_stop_and_keeps_bot_loss_flat(self):
        ok, msg = self.w.user_limit_sell('KRW-A', 100, 110.0, {'KRW-A': 100.0})
        self.assertTrue(ok, msg); self.assertEqual(self.order['price'], 110.0)
        self.w.manage_resting({'KRW-A': 85.0})                      # 봇 손절선(90) 아래여도 사용자 주문은 그대로
        self.assertIn('KRW-A', self.w.d['resting_orders'])
        before = self.today(110.0)
        self.order.update(state='done', executed_volume='1000', trades=[{'volume': '1000', 'funds': '110000'}], paid_fee='55')
        self.w.manage_resting({'KRW-A': 110.0})
        self.assertNotIn('KRW-A', self.w.d['positions'])
        self.assertAlmostEqual(self.today(110.0), before, places=6)   # 사용자 실현손익은 봇 손실 밖
        rows = [json.loads(l) for l in self.w.user_trade_file.read_text(encoding='utf-8').splitlines()]
        self.assertEqual((rows[-1]['event'], rows[-1]['id'], rows[-1]['closed']), ('sell', 'U1', True))
        self.assertAlmostEqual(rows[-1]['realized'], 110000 - 55 - 100000)

    def test_expiry_cancels_without_market_and_validation(self):
        for bad in (110.05, 400.0, 0):
            ok, _ = self.w.user_limit_sell('KRW-A', 100, bad, {}); self.assertFalse(ok, bad)
        ok, _ = self.w.user_limit_sell('KRW-A', 50, 105.0, {}); self.assertTrue(ok)
        self.assertAlmostEqual(self.order['volume'], 500.0)
        ok, msg = self.w.user_limit_sell('KRW-A', 50, 106.0, {}); self.assertFalse(ok); self.assertIn('진행 중', msg)
        ok, _ = self.w.cancel_user_rest('KRW-A'); self.assertTrue(ok)
        self.w.manage_resting({'KRW-A': 100.0})
        self.assertNotIn('KRW-A', self.w.d.get('resting_orders', {}))
        self.assertEqual(self.w.d['positions']['KRW-A']['qty'], 1000.0)

    def test_custom_quantity(self):
        ok, msg = self.w.user_limit_sell('KRW-A', 0, 105.0, {}, qty=300.0)
        self.assertTrue(ok, msg); self.assertAlmostEqual(self.order['volume'], 300.0)
        self.w.d['resting_orders'].clear(); self.order = {'uuid': 'S2', 'state': 'wait', 'trades': [], 'paid_fee': '0'}
        ok, _ = self.w.user_limit_sell('KRW-A', 0, 105.0, {}, qty=980.0)      # 남는 20개(2,100원) < 5,000원 → 전량
        self.assertTrue(ok); self.assertAlmostEqual(self.order['volume'], 1000.0)
        self.w.d['resting_orders'].clear()
        for q in (10.0, None, -1, float('inf')):
            ok, _ = self.w.user_limit_sell('KRW-A', 0, 105.0, {}, qty=q); self.assertFalse(ok, q)
        self.assertEqual(live.Live._sell_part(1000.0, 100.0, 50), (500.0, ''))
        self.assertEqual(live.Live._sell_part(1000.0, 100.0, 0, 5000.0)[0], 1000.0)
        srv = manual_orders.ManualOrderServer(8765, token='t', allowed_kinds={'user_limit_sell', 'liquidate'})
        b = lambda **k: json.dumps(dict(token='t', market='KRW-A', confirm=True, **k)).encode()
        self.assertEqual(srv.check('127.0.0.1:8765', 'null', b(pct=0, qty=12.5, price=105.0), 'user_limit_sell')[0], 200)
        self.assertEqual(srv.check('127.0.0.1:8765', 'null', b(pct=0, price=105.0), 'user_limit_sell')[0], 400)
        self.assertEqual(srv.check('127.0.0.1:8765', 'null', b(pct=0, qty=3), 'liquidate')[0], 200)

    def test_popup_tick_table_matches_exchange(self):
        import re, position_popup, upbit
        tk = position_popup.MODAL.split('var TK=')[1].split(';')[0]
        tk = re.sub(r'(?<![0-9])[.]', '0.', tk)
        self.assertEqual(json.loads(tk), [list(map(float, t)) for t in upbit.TICKS])


class RankButtonTests(unittest.TestCase):
    def test_rank_rows_have_hover_buy_button_only_with_server(self):
        import bot, strategy as st
        rk = [{'market': 'KRW-A', 'rs': .05, 'rank': 1, 'close': 100.0}]
        self.assertIn('ub-btn', bot.ranking_rows(rk, set(), {}, st.Params(), can_promote=True))
        self.assertNotIn('ub-btn', bot.ranking_rows(rk, set(), {}, st.Params(), can_promote=False))
        self.assertNotIn('ub-btn', bot.ranking_rows(rk, set(), {'KRW-A': {'trade_blocked': True, 'warning_reasons': ['유의']}}, st.Params(), can_promote=True))
        js = bot.user_buy_script({'port': 1, 'token': 't', 'buy_max': 100000})
        self.assertIn("'/user_buy'", js); self.assertIn("'/user_limit_buy'", js); self.assertIn('ub-up', js)
        # 2026-11-28 사고: 창 자체 style의 display:flex가 hidden을 이겨 페이지를 열 때마다 빈 주문 창이 화면을 덮었다
        import re
        tag = re.search(r'<div id=ub-modal[^>]*>', js).group(0)
        self.assertNotIn('display', tag); self.assertIn('#ub-modal[hidden]{display:none!important}', js)


if __name__ == '__main__':
    unittest.main()
