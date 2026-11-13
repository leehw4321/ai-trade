import datetime as dt
import unittest
from pathlib import Path
import owner_watch as ow

NOW = 1_800_000_000.0
LIVE = {'owner': 'x', 'pc': 'HOME', 'updated': NOW - 30, 'expires': NOW + 150, 'request': ''}


class JudgeTest(unittest.TestCase):
    def test_other_pc_alive_is_ok(self):
        self.assertEqual(ow.judge(LIVE, [], NOW, me='OFFICE')[0], 'ok')

    def test_expired_or_no_owner_is_bad(self):
        self.assertEqual(ow.judge(dict(LIVE, expires=NOW - 60), [], NOW, me='OFFICE')[0], 'bad')
        self.assertEqual(ow.judge(dict(LIVE, owner=''), [], NOW, me='OFFICE')[0], 'bad')

    def test_late_renew_and_unsold_below_stop_warn(self):
        self.assertEqual(ow.judge(dict(LIVE, updated=NOW - 200), [], NOW, me='OFFICE')[0], 'warn')
        h = [{'market': 'KRW-A', 'stop': 96.5}]
        self.assertEqual(ow.judge(LIVE, h, NOW, me='OFFICE', below_since={'KRW-A': NOW - 60})[0], 'ok')
        lvl, _, why = ow.judge(LIVE, h, NOW, me='OFFICE', below_since={'KRW-A': NOW - 400})
        self.assertEqual(lvl, 'warn'); self.assertIn('A: 추정 손절가 아래', why[0])

    def test_lease_unreadable_warns(self):
        self.assertEqual(ow.judge(None, [], NOW)[0], 'warn')

    def test_holdings_skip_dust_and_mark_protected(self):
        acct = {'assets': [{'currency': 'KRW', 'quantity': 1000}, {'currency': 'A', 'quantity': 10, 'avg': 100, 'price': 98, 'value': 980},
                           {'currency': 'B', 'quantity': 100, 'avg': 100, 'price': 95, 'value': 9500},
                           {'currency': 'DOGE', 'quantity': 100, 'avg': 100, 'price': 90, 'value': 9000}]}
        h = ow.holdings_from(acct, {'KRW-DOGE'})
        self.assertEqual([x['market'] for x in h], ['KRW-B', 'KRW-DOGE'])
        self.assertAlmostEqual(h[0]['stop'], 96.5); self.assertIsNone(h[1]['stop'])


class TripsTest(unittest.TestCase):
    def o(self, m, side, when, vol, funds, fee):
        return {'market': m, 'side': side, 'created_at': f'2026-11-13T{when}:00+09:00', 'executed_volume': str(vol),
                'executed_funds': str(funds), 'paid_fee': str(fee)}

    def test_buy_sell_on_one_row_with_profit_including_fees(self):
        rows = ow.trips([self.o('KRW-A', 'bid', '12:01', 610, 65880, 33), self.o('KRW-A', 'ask', '12:13', 610, 62220, 31),
                         self.o('KRW-B', 'bid', '12:20', 1000, 10000, 5)], 0)
        self.assertEqual([r['market'] for r in rows], ['KRW-B', 'KRW-A'])
        b, a = rows
        self.assertIsNone(b['sell_time'])                                   # 아직 보유: 매수만
        self.assertAlmostEqual(a['buy_px'], 108); self.assertAlmostEqual(a['sell_px'], 102)
        self.assertAlmostEqual(a['pnl'], 62220 - 31 - 65913)               # 수수료 포함
        self.assertLess(a['pct'], 0)

    def test_window_and_unknown_buy(self):
        rows = ow.trips([self.o('KRW-A', 'ask', '12:13', 10, 1000, 1)], 0)
        self.assertIsNone(rows[0]['pnl'])                                   # 매수가 조회 기간 밖
        old = dt.datetime(2026, 11, 13, 12, 30, tzinfo=ow.KST).timestamp()
        self.assertEqual(ow.trips([self.o('KRW-A', 'ask', '12:13', 10, 1000, 1)], old), [])


class IconTest(unittest.TestCase):
    def test_status_header_has_observer_icon(self):  # 사용자 지시 2026-11-13 '봇에 아이콘을 만들어줘'
        import bot
        self.assertIn("href='observe.html'", bot.OBSERVE_BTN)
        self.assertIn('{OBSERVE_BTN}', Path(bot.__file__).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
