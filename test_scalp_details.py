import unittest
from scalp_details import candidate_details


class ScalpDetailsTests(unittest.TestCase):
    def test_ranking_rotation_protection_and_day_values(self):
        markets = [f'KRW-C{i}' for i in range(10)]
        rows = {m: {'eligible': True, 'turnover': 100-i} for i, m in enumerate(markets)}
        ticks = {markets[0]: {'opening_price': 100, 'high_price': 110, 'low_price': 90,
                              'acc_trade_price': 123, 'acc_trade_price_24h': 456}}
        details = candidate_details(markets, rows, ticks, markets, [markets[1]])
        self.assertIn('1위', details[markets[0]]['selection_reason'])
        self.assertIn('보호', details[markets[1]]['selection_reason'])
        self.assertIn('순환', details[markets[9]]['selection_reason'])
        self.assertEqual(details[markets[0]]['range_pct'], 20)
        self.assertEqual(details[markets[0]]['turnover_day'], 123)
        self.assertEqual(details[markets[0]]['turnover_24h'], 456)
        self.assertIsNone(details[markets[2]]['opening'])

    def test_invalid_data_is_not_displayed_as_zero(self):
        ticks = {'KRW-X': {'opening_price': 0, 'high_price': float('nan'), 'low_price': -1}}
        details = candidate_details(['KRW-X'], {}, ticks, ['KRW-X'], [])['KRW-X']
        self.assertIsNone(details['range_pct'])
        self.assertIsNone(details['high'])
        self.assertIsNone(details['low'])
