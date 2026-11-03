"""Math and time-boundary checks; never contacts an exchange."""
import datetime as dt
import unittest

import tactical_observer as obs


class ObserverChecks(unittest.TestCase):
    def candles(self):
        start = dt.datetime(2026, 11, 3, tzinfo=dt.timezone.utc)
        raw = []
        for i in range(22):
            raw.append({'candle_date_time_utc': (start+dt.timedelta(minutes=5*i)).replace(tzinfo=None).isoformat(),
                        'opening_price': 100., 'high_price': 101., 'low_price': 99., 'trade_price': 100.,
                        'candle_acc_trade_price': 1000.})
        return raw, start.timestamp()

    def test_open_bar_and_publication_window_never_enter_features(self):
        raw, start = self.candles()
        raw[-1]['trade_price'] = 10000.
        data = obs.features(raw, start+22*300+10)
        self.assertFalse(data['breakout20'])  # last bar is within the 20s publication window
        self.assertEqual(data['bar_end'], start+21*300)

    def test_missing_candle_is_flagged_not_misreported_as_clean_signal(self):
        raw, start = self.candles()
        del raw[10]
        data = obs.features(raw, start+22*300+25)
        self.assertFalse(data['contiguous'])
        self.assertIsNone(data['breakout20'])

    def test_old_bar_does_not_generate_a_current_signal(self):
        raw, start = self.candles()
        data = obs.features(raw, start+24*300)
        self.assertTrue(data['stale_bar'])
        self.assertIsNone(data['recovery_prev_downbar'])

    def test_depth_weighted_cost_includes_both_fees(self):
        book = {'timestamp': 1, 'orderbook_units': [
            {'ask_price': 100., 'ask_size': 1., 'bid_price': 99., 'bid_size': 1.},
            {'ask_price': 110., 'ask_size': 2., 'bid_price': 98., 'bid_size': 2.}]}
        result = obs.book_cost(book, budget=210., fee=.001)
        self.assertTrue(result['depth_sufficient'])
        self.assertEqual(result['buy_vwap'], 105.)
        self.assertEqual(result['sell_vwap'], 98.5)
        self.assertAlmostEqual(result['roundtrip_cost_pct_same_book'], 1-197*.999/(210*1.001))

    def test_insufficient_depth_never_fabricates_a_full_exit(self):
        book = {'timestamp': 1, 'orderbook_units': [
            {'ask_price': 100., 'ask_size': 100., 'bid_price': 99., 'bid_size': 1.}]}
        result = obs.book_cost(book, budget=500.)
        self.assertFalse(result['depth_sufficient'])
        self.assertIsNone(result['roundtrip_cost_pct_same_book'])

    def test_warning_classification_never_treats_unknown_as_clean(self):
        self.assertEqual(obs.cohort({'investment_warning': True, 'warning': True, 'caution_types': []}),
                         'excluded_investment_warning')
        self.assertEqual(obs.cohort({'investment_warning': False, 'warning': True, 'caution_types': []}),
                         'excluded_other_or_unknown_caution')
        self.assertEqual(obs.cohort({'investment_warning': False, 'warning': True,
                                    'caution_types': ['TRADING_VOLUME_SOARING','DEPOSIT_AMOUNT_SOARING']}),
                         'excluded_other_or_unknown_caution')


if __name__ == '__main__':
    unittest.main()
