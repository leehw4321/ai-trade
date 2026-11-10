"""2026-11-11 사용자 지시: 바닥가 지정가 손절·매수 전 호가 두께·위험 청산 빠른 직접 주문 확인(모두 기본 꺼짐)."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from asset_protection import Policy, ProtectionError
from manual_protection import Detector
import live
import strategy as st
import upbit
from test_auto_protection import FakeClient, order


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.lv = live.Live(Path(self.tmp.name) / 'live', {'enabled': True, 'auto_proposal': True}, st.Params())
        self.lv.d['positions'] = {'KRW-A': {'qty': 100., 'cost': 10000., 'stop': 90., 'time': 't'}}
        self.share = patch.object(self.lv, 'share').start(); self.addCleanup(patch.stopall)


class DefaultsOff(Base):
    def test_all_new_switches_default_off(self):
        for k in ('stop_floor_enabled', 'min_bid_depth_enabled', 'stop_fast_manual_check'):
            self.assertIs(live.DEFAULTS[k], False)

    def test_default_stop_still_uses_market_sell(self):
        done = {'uuid': 'u', 'market': 'KRW-A', 'side': 'ask', 'state': 'done', 'paid_fee': '4',
                'executed_volume': '100', 'trades': [{'volume': '100', 'funds': '8000'}]}
        with patch.object(upbit, 'accounts', return_value=[{'currency': 'A', 'balance': '100'}]), \
                patch.object(upbit, 'market_sell', return_value=done) as ms, \
                patch.object(upbit, 'limit_sell') as ls, patch.object(self.lv, '_wait', return_value=done):
            self.lv.stop_check({'KRW-A': 80.})
        ms.assert_called_once(); ls.assert_not_called()


class StopFloor(Base):
    def setUp(self):
        super().setUp()
        self.lv.cfg.update(stop_floor_enabled=True, stop_floor_pct=0.005, stop_floor_wait_seconds=15)

    def test_stop_places_limit_at_floor_below_trigger_and_rests(self):
        with patch.object(upbit, 'accounts', return_value=[{'currency': 'A', 'balance': '100'}]), \
                patch.object(upbit, 'limit_sell', return_value={'uuid': 'L1', 'state': 'wait'}) as ls, \
                patch.object(upbit, 'market_sell') as ms:
            self.lv.stop_check({'KRW-A': 80.})
        ms.assert_not_called()
        self.assertEqual(ls.call_args.args[:3], ('KRW-A', 79.6, 100.))   # 80 × 0.995, 호가 0.1 단위 내림
        rest = self.lv.d['resting_orders']['KRW-A']
        self.assertTrue(rest['resting']['stop_floor']); self.assertEqual(rest['uuid'], 'L1')
        self.assertEqual(rest['reason'], '손절')
        # 대기 중에는 같은 종목 손절을 다시 내지 않는다
        with patch.object(upbit, 'accounts', return_value=[{'currency': 'A', 'balance': '0', 'locked': '100'}]), \
                patch.object(upbit, 'limit_sell') as ls2, patch.object(upbit, 'market_sell') as ms2:
            self.lv.stop_check({'KRW-A': 70.})
        ls2.assert_not_called(); ms2.assert_not_called()

    def test_rejected_floor_order_falls_back_to_market(self):
        done = {'uuid': 'u', 'market': 'KRW-A', 'side': 'ask', 'state': 'done', 'paid_fee': '4',
                'executed_volume': '100', 'trades': [{'volume': '100', 'funds': '8000'}]}
        with patch.object(upbit, 'accounts', return_value=[{'currency': 'A', 'balance': '100'}]), \
                patch.object(upbit, 'limit_sell', side_effect=upbit.OrderRejected(400, 'x', 'reject')), \
                patch.object(upbit, 'market_sell', return_value=done) as ms, patch.object(self.lv, '_wait', return_value=done):
            self.lv.stop_check({'KRW-A': 80.})
        ms.assert_called_once()
        self.assertNotIn('KRW-A', self.lv.d['positions'])
        self.assertFalse(self.lv.d.get('resting_orders'))

    def _rest(self, deadline):
        self.lv.d['resting_orders'] = {'KRW-A': {
            'identifier': 'v5-x', 'uuid': 'L1', 'side': 'ask', 'market': 'KRW-A', 'amount': 100., 'reason': '손절',
            'time': 't', 'atr': None, 'atr_r': None,
            'resting': {'price': 79.6, 'tick': .1, 'volume': 100., 'deadline': deadline, 'placed': 0,
                        'manual': False, 'stop_floor': True, 'trigger_px': 80.}}}

    def test_waiting_floor_order_is_not_cancelled_for_being_below_stop(self):
        self._rest(deadline=10**12)
        wait = {'uuid': 'L1', 'market': 'KRW-A', 'side': 'ask', 'state': 'wait', 'executed_volume': '0', 'trades': []}
        with patch.object(upbit, 'get_order', return_value=wait), patch.object(upbit, 'cancel_order') as cancel:
            self.lv.manage_resting({'KRW-A': 70.})   # 손절선(90)보다 한참 아래여도 기한 전이면 기다린다
        cancel.assert_not_called()
        self.assertIn('KRW-A', self.lv.d['resting_orders'])

    def test_expired_floor_order_cancels_and_sells_remainder_at_market(self):
        self._rest(deadline=0)
        part = {'uuid': 'L1', 'market': 'KRW-A', 'side': 'ask', 'state': 'cancel', 'paid_fee': '1.6',
                'executed_volume': '40', 'trades': [{'volume': '40', 'funds': '3184'}]}
        done = {'uuid': 'M1', 'market': 'KRW-A', 'side': 'ask', 'state': 'done', 'paid_fee': '2',
                'executed_volume': '60', 'trades': [{'volume': '60', 'funds': '5100'}]}
        with patch.object(upbit, 'get_order', return_value=dict(part, state='wait')), \
                patch.object(upbit, 'cancel_order') as cancel, \
                patch.object(self.lv, '_wait', side_effect=[part, done]), \
                patch.object(upbit, 'accounts', return_value=[{'currency': 'A', 'balance': '60'}]), \
                patch.object(upbit, 'market_sell', return_value=done) as ms:
            self.lv.manage_resting({'KRW-A': 85.})   # 남은 60개×85=5,100원(최소 주문 5,000원 이상)
        cancel.assert_called_once_with('L1')
        self.assertEqual(ms.call_args.args[:2], ('KRW-A', 60.))
        self.assertNotIn('KRW-A', self.lv.d['positions'])
        self.assertFalse(self.lv.d.get('resting_orders'))


class BidDepth(Base):
    def book(self, bid_units):
        return {'KRW-B': {'ask': 101., 'bid': 100., 'units': [
            {'bid_price': p, 'bid_size': q, 'ask_price': 101., 'ask_size': 1.} for p, q in bid_units]}}

    def block(self, units, krw=10000):
        self.lv.cfg.update(min_bid_depth_enabled=True, min_bid_depth_pct=0.005, min_bid_depth_mult=10)
        return self.lv.bid_depth_block(self.book(units)['KRW-B'], krw)

    def test_counts_only_bids_within_band(self):
        # 100원 600개 + 99.5원 400개 = 99,800 < 100,000 → 보류 / 99원은 범위 밖
        self.assertIn('호가 얇음', self.block([(100., 600.), (99.5, 400.), (99., 10000.)]))
        self.assertIsNone(self.block([(100., 600.), (99.5, 403.)]))

    def test_missing_units_blocks(self):
        self.lv.cfg.update(min_bid_depth_enabled=True)
        self.assertIn('확인 불가', self.lv.bid_depth_block({'ask': 101., 'bid': 100.}, 10000))

    def test_buy_block_uses_depth_only_when_enabled(self):
        markets = {'KRW-B': {}}
        with patch.object(upbit, 'accounts', return_value=[{'currency': 'KRW', 'balance': '1000000'}]), \
                patch.object(upbit, 'orderbooks', return_value=self.book([(100., 10.)])):
            self.assertEqual(self.lv.buy_block('KRW-B', 10000, markets), '')
            self.lv.cfg['min_bid_depth_enabled'] = True
            self.assertIn('호가 얇음', self.lv.buy_block('KRW-B', 10000, markets))


class FastManualCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name) / 'data/live'; folder.mkdir(parents=True)
        f = folder.parent / 'user_protected_assets.json'; f.write_text('{"markets":[]}')
        self.now = [2000.]
        self.policy = Policy(f, clock=lambda: self.now[0])
        self.api = FakeClient(); self.calls = []
        orig = self.api.orders
        self.api.orders = lambda market=None: (self.calls.append(market), orig(market))[1]
        self.d = Detector(self.policy, folder, self.api, clock=lambda: self.now[0])
        self.state = {'positions': {'KRW-A': {'qty': 1., 'cost': 100., 'stop': 90., 'entry': 100.}}}
        self.d.scan(self.state)          # 정기 전역 확인 통과 → good 기록
        self.calls.clear()

    def test_recent_good_and_equal_balance_skips_history(self):
        self.now[0] += 10
        self.assertEqual(self.d.scan(self.state, 'KRW-A', True, 1., fast_max_age=30), 1.)
        self.assertEqual(self.calls, [])

    def test_stale_good_does_full_check(self):
        self.now[0] += 31
        self.d.scan(self.state, 'KRW-A', True, 1., fast_max_age=30)
        self.assertEqual(self.calls, ['KRW-A'])

    def test_balance_change_or_lock_does_full_check_and_protects(self):
        self.now[0] += 5
        self.api.rows[0]['balance'] = '2'     # 그 사이 직접 매수
        with self.assertRaises(ProtectionError):
            self.d.scan(self.state, 'KRW-A', True, 1., fast_max_age=30)
        self.assertEqual(self.calls, ['KRW-A'])

    def test_locked_quantity_does_full_check(self):
        self.now[0] += 5
        self.api.rows[0].update(balance='0', locked='1')   # 직접 미체결 주문
        self.api.records = [order(uid='m', state='wait')]
        with self.assertRaises(ProtectionError):
            self.d.scan(self.state, 'KRW-A', True, 1., fast_max_age=30)
        self.assertEqual(self.calls, ['KRW-A'])

    def test_not_used_for_buys_or_without_flag(self):
        self.now[0] += 5
        self.d.scan(self.state, 'KRW-A', False, 1., fast_max_age=30)
        self.d.scan(self.state, 'KRW-A', True, 1.)
        self.assertEqual(self.calls, ['KRW-A', 'KRW-A'])


if __name__ == '__main__':
    unittest.main()
