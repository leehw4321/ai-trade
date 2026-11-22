"""매수 주문안 주기 buy_slot_minutes(사용자 지시 2026-11-22: 매수 주기 줄여 48시간 실거래 시험, 순위 매도는 매시 유지)."""
import unittest

import rotation_policy as rotation
import shadow as sh
import strategy as st

H = 3600
BASE = 1_795_000_000 // H * H   # 정시


def params(**kw):
    return st.Params(**{'rotation_enabled': True, **kw})


class SlotSeconds(unittest.TestCase):
    def test_default_is_hourly(self):
        self.assertEqual(rotation.slot_seconds(params()), H)

    def test_allowed_values(self):
        for m in (15, 30, 60):
            self.assertEqual(rotation.slot_seconds(params(buy_slot_minutes=m)), m * 60)

    def test_invalid_falls_back_to_hourly(self):
        for m in (10, 20, 0, -15, 15.0, '15', None):
            self.assertEqual(rotation.slot_seconds(params(buy_slot_minutes=m)), H, m)
        self.assertEqual(rotation.slot_seconds(params(buy_slot_minutes=15, tf=10)), H)   # 봉과 안 맞음


class Envelope(unittest.TestCase):
    def test_hourly_policy_rejects_quarter(self):
        with self.assertRaises(ValueError):
            rotation.envelope(params(), BASE + 900, BASE + 900 + 60)

    def test_15min_policy_accepts_quarter_and_hour(self):
        p = params(buy_slot_minutes=15)
        for closed in (BASE, BASE + 900, BASE + 1800, BASE + 2700):
            self.assertEqual(rotation.envelope(p, closed, closed + 90)['slot'], closed)

    def test_15min_policy_rejects_stale_or_off_grid(self):
        p = params(buy_slot_minutes=15)
        with self.assertRaises(ValueError):
            rotation.envelope(p, BASE + 900, BASE + 1800 + 90)   # 지난 슬롯
        with self.assertRaises(ValueError):
            rotation.envelope(p, BASE + 600, BASE + 690)          # 15분 격자 밖

    def test_decision_ids_unique_per_slot(self):
        p = params(buy_slot_minutes=15)
        ids = {rotation.envelope(p, c, c + 90)['decision_id'] for c in (BASE, BASE + 900, BASE + 1800)}
        self.assertEqual(len(ids), 3)

    def test_hourly(self):
        self.assertTrue(rotation.hourly(BASE)); self.assertFalse(rotation.hourly(BASE + 900))


class ShadowSlots(unittest.TestCase):
    def test_slot_due_every_15min(self):
        S = 900
        self.assertEqual(sh.slot_due(BASE + 900 + 100, BASE, S), BASE + 900)
        self.assertIsNone(sh.slot_due(BASE + 900 + 100, BASE + 900, S))     # 이미 처리
        self.assertIsNone(sh.slot_due(BASE + 900 + 30, BASE, S))            # 판단 대기(90초 전)

    def test_hourly_default_unchanged(self):
        self.assertIsNone(sh.slot_due(BASE + 900 + 100, BASE))
        self.assertEqual(sh.slot_due(BASE + H + 100, BASE), BASE + H)


class RankSellsOnlyOnHour(unittest.TestCase):
    def setUp(self):
        self.p = params(buy_slot_minutes=15, M=2, entry_top=3, entry_want=1, entry_scan=3, slots=3)
        self.ranked = [{'market': m, 'rank': i, 'rs': 0.05 - i * 0.01, 'close': 100.0, 'atr': 1.0, 'bar': '2026-11-22T00:00:00+09:00'}
                       for i, m in enumerate(['KRW-AAA', 'KRW-BBB', 'KRW-CCC', 'KRW-DDD'], 1)]
        self.caps = {'max_order_krw': 30000, 'max_total_buy_krw': 10 ** 9, 'max_sell_krw_per_plan': 10 ** 9}

    def make(self, rank_sells):
        return sh.proposal({'KRW-DDD': 50000}, self.ranked, {}, {r['market'] for r in self.ranked}, self.p, self.caps,
                           500000, 0, rotation_state={}, rotation_slot=BASE, rank_sells=rank_sells)

    def test_hour_slot_sells_out_of_rank(self):
        text, reasons = self.make(True)
        self.assertIn('매도 DDD', text)

    def test_quarter_slot_keeps_holding_but_still_buys(self):
        text, reasons = self.make(False)
        self.assertNotIn('매도 DDD', text)
        self.assertTrue(any('순위 매도 판단은 정시에만' in r for r in reasons))
        self.assertIn('매수 AAA', text)


if __name__ == '__main__':
    unittest.main()
