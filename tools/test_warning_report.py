import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import warning_report as wr


class WarningReportTests(unittest.TestCase):
    def test_snapshot_uses_only_public_api_and_cached_rank(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder)
            (app / 'config.json').write_text(json.dumps({'strategy': {'tf': 15, 'L': 72}}))
            (app / 'data' / 'P10').mkdir(parents=True)
            (app / 'data' / 'live').mkdir()
            (app / 'data' / 'P10' / 'ledger.json').write_text(json.dumps({'ranking_time': '2026-11-03T00:00:00+00:00',
                'ranking': [{'market': 'KRW-X', 'rank': 1, 'rs': .1}]}))
            (app / 'data' / 'live' / 'live.json').write_text(json.dumps({'positions': {'KRW-X': {'private': 'not published'}}}))
            rows = [{'market': 'KRW-X', 'korean_name': '<test>', 'market_event': {'warning': False,
                     'caution': {'PRICE_FLUCTUATIONS': True}}}]
            with patch.object(wr.upbit, '_get', return_value=rows) as get, \
                 patch.object(wr.upbit, 'tickers', return_value={'KRW-X': {'acc_trade_price_24h': 100}}), \
                 patch.object(wr.upbit, 'accounts', side_effect=AssertionError('no private API')):
                data = wr.snapshot(app, now=1793664060)
            get.assert_called_once_with('/v1/market/all', {'is_details': 'true'})
            self.assertEqual(data['rows'][0]['rank'], 1)
            self.assertTrue(data['rows'][0]['held'])
            self.assertTrue(data['rows'][0]['warning'])
            self.assertNotIn('not published', json.dumps(data))
            page = wr.render(data)
            self.assertIn('&lt;test&gt;', page)
            self.assertNotIn('<test>', page)
            self.assertIn('경보 사유 매도 차단 없음', page)

    def test_missing_rank_is_not_fabricated(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder); (app / 'config.json').write_text('{}')
            with patch.object(wr.upbit, '_get', return_value=[{'market': 'KRW-X'}]), \
                 patch.object(wr.upbit, 'tickers', return_value={}):
                data = wr.snapshot(app)
            self.assertTrue(data['ranking_unavailable'])
            self.assertTrue(data['ranking_stale'])
            self.assertTrue(data['holdings_unavailable'])

    def test_error_is_escaped_and_old_data_marked(self):
        page = wr.render({}, '<offline>')
        self.assertIn('&lt;offline&gt;', page)
        self.assertIn('순위 자료가 오래됐거나 없습니다', page)


if __name__ == '__main__':
    unittest.main()
