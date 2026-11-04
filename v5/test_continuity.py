import tempfile
import unittest
from unittest import mock

import continuity as ct
import live
import strategy as st

T = '2026-11-04T10:00:00+09:00'


def row(t, side, market, vol, funds, fee=0.0, ident='v5-x', state='done'):
    return {'created_at': t, 'side': side, 'market': market, 'executed_volume': str(vol), 'executed_funds': str(funds),
            'paid_fee': str(fee), 'identifier': ident, 'state': state}


class Replay(unittest.TestCase):
    def test_profit_and_loss_flags_follow_average_cost_with_fees(self):
        rows = [row('2026-11-04T09:00:00+09:00', 'bid', 'KRW-A', 10, 1000, 0.5),
                row('2026-11-04T10:00:00+09:00', 'ask', 'KRW-A', 10, 1100, 0.55),
                row('2026-11-04T09:00:00+09:00', 'bid', 'KRW-B', 10, 1000, 0.5),
                row('2026-11-04T10:00:00+09:00', 'ask', 'KRW-B', 10, 900, 0.45)]
        r = ct.replay_bot_orders(rows)
        self.assertTrue(r['KRW-A']['last_sell']['profit'])
        self.assertFalse(r['KRW-B']['last_sell']['profit'])
        self.assertAlmostEqual(r['KRW-A']['last_sell']['price'], 110.0)
        self.assertEqual(r['KRW-A']['qty'], 0.0)

    def test_order_of_rows_does_not_matter_only_time(self):
        rows = [row('2026-11-04T10:00:00+09:00', 'ask', 'KRW-A', 10, 1100), row('2026-11-04T09:00:00+09:00', 'bid', 'KRW-A', 10, 1000)]
        self.assertTrue(ct.replay_bot_orders(rows)['KRW-A']['last_sell']['profit'])

    def test_sell_without_known_cost_is_not_assumed_profitable(self):
        r = ct.replay_bot_orders([row('2026-11-04T10:00:00+09:00', 'ask', 'KRW-A', 10, 1100)])
        self.assertFalse(r['KRW-A']['last_sell']['profit'])
        self.assertFalse(r['KRW-A']['known_basis'])

    def test_non_bot_fills_and_open_orders_are_flagged(self):
        rows = [row('2026-11-04T09:00:00+09:00', 'bid', 'KRW-A', 10, 1000), row('2026-11-04T09:30:00+09:00', 'bid', 'KRW-A', 1, 100, ident='user-1'),
                row('2026-11-04T09:00:00+09:00', 'bid', 'KRW-B', 10, 1000), row('2026-11-04T09:10:00+09:00', 'bid', 'KRW-B', 0, 0, state='wait')]
        r = ct.replay_bot_orders(rows)
        self.assertTrue(r['KRW-A']['mixed'])
        self.assertTrue(r['KRW-B']['open'])

    def test_garbage_rows_are_ignored(self):
        rows = [{'market': 'KRW-A', 'executed_volume': 'x'}, {'market': ''}, {'market': 'KRW-A', 'executed_volume': '1', 'executed_funds': 'nan', 'identifier': 'v5-a', 'side': 'bid', 'created_at': T}]
        r = ct.replay_bot_orders(rows)
        self.assertFalse(r['KRW-A']['known_basis'])


class Restorable(unittest.TestCase):
    NOW = 1_794_000_000.0

    def entry(self, age, profit=True):
        return {'last_sell': {'time': self.NOW - age, 'price': 253.0, 'profit': profit}}

    def test_recent_newer_sells_only(self):
        self.assertEqual(ct.restorable_last_sell(self.entry(3600), None, self.NOW)['price'], 253.0)
        self.assertIsNone(ct.restorable_last_sell(self.entry(8 * 86400), None, self.NOW))
        self.assertIsNone(ct.restorable_last_sell(self.entry(3600), {'time': self.NOW - 60}, self.NOW))  # 장부가 더 새롭다
        self.assertIsNone(ct.restorable_last_sell({'last_sell': {'time': self.NOW + 3600, 'price': 1, 'profit': True}}, None, self.NOW))
        self.assertIsNone(ct.restorable_last_sell(None, None, self.NOW))

    def test_bot_basis_requires_full_proof(self):
        good = {'qty': 100.0, 'cost': 4310.0, 'mixed': False, 'open': False, 'known_basis': True, 'last_sell': None, 'first_side': 'bid'}
        self.assertAlmostEqual(ct.bot_basis(good, 100.0), 43.1)
        self.assertAlmostEqual(ct.bot_basis(good, 100.05), 43.1)  # 0.1% 이내
        for bad in (dict(good, mixed=True), dict(good, open=True), dict(good, known_basis=False), dict(good, qty=60.0),
                    dict(good, first_side='ask'), dict(good, first_side=None)):
            self.assertIsNone(ct.bot_basis(bad, 100.0))
        self.assertIsNone(ct.bot_basis(good, 120.0))
        self.assertIsNone(ct.bot_basis(None, 100.0))


class LiveRestore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = {'enabled': True, 'restore_continuity': True, 'max_order_krw': 10 ** 9, 'max_total_buy_krw': 10 ** 9}
        self.lv = live.Live(self.tmp.name, cfg, st.Params(stop_max_pct=0.045))
        self.lv.d['positions']['KRW-BMT'] = {'qty': 100.0, 'cost': 4310.0, 'stop': 34.39, 'inherited': True, 'time': T}
        self.lv.d['positions']['KRW-USR'] = {'qty': 5.0, 'cost': 500.0, 'stop': 80.0, 'inherited': True, 'time': T}
        self.rows = [row('2026-11-04T07:00:00+09:00', 'bid', 'KRW-BMT', 100, 4300, 10),
                     row('2026-11-04T09:00:00+09:00', 'bid', 'KRW-LSK', 10, 2470, 1), row('2026-11-04T11:49:00+09:00', 'ask', 'KRW-LSK', 10, 2530, 1)]

    def test_restores_last_sell_for_sold_markets_and_basis_for_bot_holdings(self):
        now_epoch = __import__('datetime').datetime.fromisoformat('2026-11-04T17:10:00+09:00').timestamp()
        with mock.patch('live.time.time', return_value=now_epoch):
            done = self.lv.restore_continuity(self.rows)
        self.assertEqual(done['last_sell'], ['KRW-LSK'])
        self.assertEqual(self.lv.d['last_sell']['KRW-LSK']['price'], 253.0)
        self.assertTrue(self.lv.d['last_sell']['KRW-LSK']['profit'])
        self.assertAlmostEqual(self.lv.d['positions']['KRW-BMT']['bot_basis'], 43.1)
        self.assertNotIn('bot_basis', self.lv.d['positions']['KRW-USR'])  # 이력이 없으면 복원하지 않는다

    def test_disabled_flag_and_history_failure_change_nothing(self):
        self.lv.cfg['restore_continuity'] = False
        self.assertEqual(self.lv.restore_continuity(self.rows), {})
        self.lv.cfg['restore_continuity'] = True
        with mock.patch.object(live.manual_protection.HistoryClient, 'orders', side_effect=OSError('offline')):
            self.assertEqual(self.lv.restore_continuity(), {})
        self.assertFalse(self.lv.d.get('last_sell'))
        self.assertNotIn('bot_basis', self.lv.d['positions']['KRW-BMT'])

    def test_sell_first_in_the_window_means_older_holdings_and_no_basis(self):
        rows = [row('2026-11-04T08:00:00+09:00', 'ask', 'KRW-BMT', 30, 1300), row('2026-11-04T09:00:00+09:00', 'bid', 'KRW-BMT', 130, 5600)]
        r = ct.replay_bot_orders(rows)
        self.assertEqual(r['KRW-BMT']['first_side'], 'ask')
        self.assertIsNone(ct.bot_basis(r['KRW-BMT'], 100.0))

    def test_markets_with_open_or_pending_orders_are_skipped_for_last_sell(self):
        rows = self.rows + [row('2026-11-04T11:00:00+09:00', 'bid', 'KRW-LSK', 0, 0, state='wait')]
        self.assertEqual(self.lv.restore_continuity(rows)['last_sell'], [])
        self.lv.d.pop('last_sell', None)
        self.lv.d['pending_order'] = {'market': 'KRW-LSK', 'side': 'ask'}
        self.assertEqual(self.lv.restore_continuity(self.rows)['last_sell'], [])

    def test_protected_market_is_not_touched(self):
        with mock.patch.object(self.lv, 'is_protected', side_effect=lambda m: m == 'KRW-BMT'):
            self.lv.restore_continuity(self.rows)
        self.assertNotIn('bot_basis', self.lv.d['positions']['KRW-BMT'])

    def test_rebase_uses_the_original_average_cost_for_proven_bot_holdings(self):
        self.lv.restore_continuity(self.rows)
        self.lv.rebase_adopted_stops({'KRW-BMT': 40.8, 'KRW-USR': 90.0})
        self.assertAlmostEqual(self.lv.d['positions']['KRW-BMT']['stop'], 43.1 * 0.955)  # 41.16, 인수 시점 현재가 기준 40.0이 아님
        self.assertAlmostEqual(self.lv.d['positions']['KRW-USR']['stop'], 90.0 * 0.955)  # 입증 안 된 보유는 기존 규칙(현재가 기준)


class RFrom15m(unittest.TestCase):
    def test_ratchet_uses_atr_r_when_present(self):
        p = st.Params(trail_enabled=True, k=3.0, trail_lock_r=1.0, trail_start_r=2.0, trail_m=2.0, giveback=0.5)
        # 4시간 ATR 12.6(R=37.8)이면 +7% 이익은 1R 미만이라 잠금이 없다
        old = {'entry': 197.0, 'atr': 12.6, 'stop': 188.1, 'stop0': 188.1}
        st.ratchet('KRW-X', old, 211.0, p)
        self.assertEqual(old['stop'], 188.1)
        # 15분 ATR 4.2(R=12.6)이면 같은 +7%는 1R 이상이라 본전 잠금이 걸린다
        new = {'entry': 197.0, 'atr': 12.6, 'atr_r': 4.2, 'stop': 188.1, 'stop0': 188.1}
        st.ratchet('KRW-X', new, 211.0, p)
        self.assertGreater(new['stop'], 197.0)

    def test_without_atr_r_behaviour_is_unchanged(self):
        p = st.Params(trail_enabled=True)
        a = {'entry': 100.0, 'atr': 2.0, 'stop': 90.0, 'stop0': 90.0}
        b = dict(a)
        self.assertEqual(st.ratchet('KRW-X', a, 130.0, p), st.ratchet('KRW-X', dict(b, atr_r=None), 130.0, p))


if __name__ == '__main__':
    unittest.main()
