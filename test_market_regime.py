import tempfile
import unittest
from unittest.mock import patch

from market_regime import summarize, current_view
from trend_engine import Engine


class MarketTests(unittest.TestCase):
    now = 1793063000.0

    def rows(self, change=0, count=50):
        names = ['KRW-BTC', 'KRW-ETH'] + ['KRW-TEST' + str(i) for i in range(count-2)]
        return [{'market': m, 'trade_price': 100*(1+change), 'prev_closing_price': 100,
                 'acc_trade_price_24h': 1e10-i*1000, 'trade_timestamp': (self.now-1)*1000}
                for i,m in enumerate(names)]

    def test_all_five_zones(self):
        for change, label in ((-.08,'폭락'),(-.02,'하락'),(0,'횡보'),(.02,'상승'),(.08,'급등')):
            with self.subTest(label=label):
                result = summarize(self.rows(change),self.now)
                self.assertEqual(result['label'],label)
                self.assertTrue(-100 <= result['score'] <= 100)

    def test_btc_crash_override(self):
        rows=self.rows()
        rows[0]['trade_price']=93
        self.assertEqual(summarize(rows,self.now)['label'],'폭락')

    def test_counts_and_top_fifty(self):
        result=summarize(self.rows(.01,60),self.now)
        self.assertEqual(result['count'],50)
        self.assertEqual(result['up'],50)
        self.assertEqual(result['down']+result['flat'],0)

    def test_flat_band_point_three_percent(self):
        result=summarize(self.rows(.002),self.now)
        self.assertEqual(result['flat'],50)
        self.assertEqual(result['label'],'횡보')

    def test_stale_quotes_never_appear_sideways(self):
        rows=self.rows()
        for r in rows:
            r['trade_timestamp']-=301000
        self.assertFalse(summarize(rows,self.now)['available'])

    def test_missing_major_asset_unavailable(self):
        self.assertFalse(summarize(self.rows()[1:],self.now)['available'])

    def test_less_than_ten_markets_unavailable(self):
        self.assertFalse(summarize(self.rows(count=9),self.now)['available'])

    def test_invalid_and_stablecoin_rows_excluded(self):
        rows=self.rows(count=10)
        rows += [dict(rows[0],market='KRW-USDT'), dict(rows[0],market='KRW-BAD',trade_price=float('nan')),
                 dict(rows[0],market='KRW-ZERO',prev_closing_price=0), None, {}]
        self.assertEqual(summarize(rows,self.now)['count'],10)

    def test_stale_view_has_no_active_pointer(self):
        result=summarize(self.rows(),self.now)
        self.assertFalse(current_view(result,self.now+91)['available'])

    def test_session_rollover_invalidates_cached_view(self):
        result=summarize(self.rows(),self.now)
        boundary=(self.now//86400+1)*86400
        result['time']=boundary-1
        self.assertFalse(current_view(result,boundary+1)['available'])

    def test_future_trade_timestamp_excluded(self):
        rows=self.rows()
        rows[0]['trade_timestamp']=(self.now+60)*1000
        self.assertFalse(summarize(rows,self.now)['available'])

    def test_network_failure_marks_summary_unavailable(self):
        with tempfile.TemporaryDirectory() as root:
            e=Engine(root,network=False)
            e.market_summary=summarize(self.rows(),self.now)
            with patch('trend_engine.http',side_effect=RuntimeError('offline')):
                e.refresh_market_summary()
            self.assertFalse(e.snapshot()['market_summary']['available'])
            self.assertEqual(e.mode,'paper')
            self.assertFalse(e.running)


if __name__=='__main__':
    unittest.main()
