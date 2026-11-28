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


class RankButtonTests(unittest.TestCase):
    def test_rank_rows_have_hover_buy_button_only_with_server(self):
        import bot, strategy as st
        rk = [{'market': 'KRW-A', 'rs': .05, 'rank': 1, 'close': 100.0}]
        self.assertIn('ub-btn', bot.ranking_rows(rk, set(), {}, st.Params(), can_promote=True))
        self.assertNotIn('ub-btn', bot.ranking_rows(rk, set(), {}, st.Params(), can_promote=False))
        self.assertNotIn('ub-btn', bot.ranking_rows(rk, set(), {'KRW-A': {'trade_blocked': True, 'warning_reasons': ['유의']}}, st.Params(), can_promote=True))
        js = bot.user_buy_script({'port': 1, 'token': 't', 'buy_max': 100000})
        self.assertIn("'/user_buy'", js); self.assertIn('confirm(', js); self.assertIn('prompt(', js)


if __name__ == '__main__':
    unittest.main()
