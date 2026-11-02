import unittest
from unittest.mock import patch

import account_sync as sync


class AccountSyncTests(unittest.TestCase):
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
