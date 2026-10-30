import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from holding_settings import HoldingSettings
from market_indicators import fetch_series, fetch_fear, MarketIndicators, SERIES


class MarketSettingsTests(unittest.TestCase):
    def test_persist_and_profiles(self):
        with tempfile.TemporaryDirectory() as root:
            settings = HoldingSettings(Path(root))
            settings.update({'max_minutes': 30, 'progress_minutes': 0})
            loaded = HoldingSettings(Path(root))
            self.assertEqual(loaded.profiles()['short']['max_hours'], .5)
            self.assertEqual(loaded.profiles()['short']['progress_seconds'], 0)
            self.assertEqual(loaded.profiles()['swing']['max_hours'], 168)
            self.assertEqual(loaded.profiles()['long']['max_hours'], 0)

    def test_validation(self):
        for a, b in [(0, 0), (1441, 1), (30, 31), (30, -1), (True, 0), (float('nan'), 1), (1.5, 0)]:
            with self.subTest(a=a, b=b), self.assertRaises(ValueError):
                HoldingSettings.validate({'max_minutes': a, 'progress_minutes': b})

    def test_quotes_units_timestamp_change(self):
        payload = {'chart': {'result': [{'meta': {'regularMarketPrice': 150,
                   'regularMarketTime': time.time()-60, 'chartPreviousClose': 125}}]}}
        row = fetch_series(SERIES[3], lambda _: payload)
        self.assertEqual(row['unit'], '원 / 100엔')
        self.assertEqual(row['value'], 15000)
        self.assertAlmostEqual(row['change_pct'], 20)
        payload['chart']['result'][0]['meta']['regularMarketPrice'] = float('nan')
        with self.assertRaises(ValueError):fetch_series(SERIES[3], lambda _: payload)

    def test_fear_zero_and_daily(self):
        row = fetch_fear(lambda _: {'data': [{'value': '0', 'timestamp': str(time.time()),
                                             'value_classification': 'Extreme Fear'}]})
        self.assertEqual(row['value'], 0)
        self.assertIn('일간', row['frequency'])
        self.assertEqual(row['source'], 'Alternative.me')

    def test_failure_keeps_old_quote(self):
        service = MarketIndicators()
        service.rows['kospi'].update(value=2500, time=time.time()-86400)
        with patch('market_indicators.fetch_series', side_effect=ValueError), patch('market_indicators.fetch_fear', side_effect=ValueError):
            service.refresh()
        row = service.snapshot()['rows'][0]
        self.assertEqual(row['value'], 2500)
        self.assertTrue(row['old'])
        self.assertTrue(row['error'])
