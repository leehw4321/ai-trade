import unittest
from unittest.mock import patch

import account_sync as sync


class AccountSyncTests(unittest.TestCase):
    def test_profit_and_return_exclude_cash_and_unknown_cost(self):
        assets = [dict(currency='BTC', quantity=2, avg=100, value=240),
                  dict(currency='KRW', quantity=50, avg=0, value=50),
                  dict(currency='FREE', quantity=3, avg=0, value=30),
                  dict(currency='OLD', quantity=5, avg=20, value=None)]
        self.assertEqual(sync.performance(assets), (200, 40, 20))
        assets[0]['value'] = 150
        self.assertEqual(sync.performance(assets), (200, -50, -25))
        self.assertEqual(sync.performance([]), (0, 0, None))

    def test_render_shows_price_cost_profit_and_weight(self):
        assets = [dict(currency='TEST', name='<coin>', quantity=2, avg=100,
                       value=240, price=120, available=1.5, locked=.5)]
        page = sync.render(dict(assets=assets, priced_total=240))
        for expected in ['매입 평균가', '현재가', '매입금액', '+40', '+20.00%', '100.0%', '&lt;coin&gt;']:
            self.assertIn(expected, page)

    def test_locked_funds_and_unpriced_assets_are_preserved(self):
        rows = [dict(currency='KRW', balance='100', locked='20', avg_buy_price='0'),
                dict(currency='BTC', balance='1', locked='.5', avg_buy_price='10'),
                dict(currency='OLD', balance='7', locked='0', avg_buy_price='0')]
        with patch.object(sync.upbit, 'accounts', return_value=rows), \
             patch.object(sync.upbit, 'krw_markets', return_value={'KRW-BTC': {}}), \
             patch.object(sync.upbit, 'tickers', return_value={'KRW-BTC': {'trade_price': 10}}):
            data = sync.snapshot()
        self.assertEqual(data['priced_total'], 135)
        self.assertEqual(data['assets'][1]['quantity'], 1.5)
        self.assertEqual(data['assets'][2]['quantity'], 7)
        self.assertIsNone(data['assets'][2]['value'])
        page = sync.render(data, 'offline <test>')
        self.assertIn('OLD', page)
        self.assertIn('offline &lt;test&gt;', page)
        self.assertNotIn('offline <test>', page)


if __name__ == '__main__':
    unittest.main()


class DustHiding(unittest.TestCase):
    def page(self):
        assets = [dict(currency='BIG', name='큰', quantity=1, avg=9000, value=20000, price=20000, available=1, locked=0),
                  dict(currency='TINY', name='작음', quantity=1, avg=10, value=3000, price=3000, available=1, locked=0),
                  dict(currency='ZERO', name='영', quantity=1, avg=0, value=0.0, price=0, available=1, locked=0),
                  dict(currency='NOPRICE', name='미평가', quantity=2, avg=5, value=None, price=None, available=2, locked=0)]
        return sync.render(dict(assets=assets, priced_total=23000))

    def test_small_rows_flagged_big_and_unpriced_not(self):
        page = self.page()
        self.assertEqual(page.count('<tr class="dust">'), 2)             # TINY(3,000원)·ZERO(0원)
        big = page[page.index('BIG') - 80:page.index('BIG')]
        self.assertNotIn('class="dust"', big)
        nop = page[page.index('NOPRICE') - 80:page.index('NOPRICE')]
        self.assertNotIn('class="dust"', nop)                          # 미평가 자산은 숨기지 않는다(경고 대상)

    def test_toggle_default_hidden_and_remembered(self):
        page = self.page()
        self.assertIn('id="hideDust"', page)
        self.assertIn("localStorage.getItem('hideDust')!=='0'", page)   # 저장값이 '0'(보이기)이 아니면 기본 숨김
        self.assertIn('html.hd .dust{display:none}', page)
        self.assertIn('소액 2개 숨김', page)
