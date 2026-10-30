import json
from pathlib import Path
import tempfile
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from research_status import ARTIFACT_DIRECTORY, load_research_report, load_research_status


class ResearchStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.directory = self.root / ARTIFACT_DIRECTORY
        self.directory.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def write_selection(self, **changes):
        metrics = {'return_pct': 1.2, 'trades': 15, 'max_drawdown_pct': 2.3}
        data = {'status': 'selected', 'selected_config': {'id': 'test', 'name': '테스트 모델'},
                'selected_metrics': {key: metrics.copy() for key in ('train', 'validation', 'test')},
                'scope': '10종목 표본', 'selection_rule': '검증 구간에서 선택',
                'created_kst': '2026-10-30 20:00:00 KST', 'approved_paper': True}
        data.update(changes)
        (self.directory / 'selection.json').write_text(json.dumps(data), encoding='utf-8')
        return data

    def test_missing_results_are_not_ready_and_not_zero_return(self):
        data = load_research_status(self.root)
        self.assertFalse(data['available'])
        self.assertEqual(data['status'], 'not_ready')
        self.assertIsNone(data['selected_metrics']['test'])
        self.assertFalse(data['approved_paper'])
        self.assertFalse(data['expected_future_return_estimated'])
        self.assertIsNone(load_research_report(self.root))

    def test_valid_result_has_allowlisted_summary_and_fixed_report(self):
        self.write_selection(access_key='never exposed', paper_only=False,
                             expected_future_return_estimated=True)
        (self.directory / 'report.md').write_text('# 연구 결과\n', encoding='utf-8')
        data = load_research_status(self.root)
        self.assertTrue(data['available'])
        self.assertTrue(data['approved_paper'])
        self.assertEqual(data['selected_metrics']['test']['trades'], 15)
        self.assertEqual(data['selected_metrics']['test']['return_pct'], 1.2)
        self.assertNotIn('access_key', data)
        self.assertTrue(data['paper_only'])
        self.assertFalse(data['expected_future_return_estimated'])
        self.assertTrue(data['report_available'])
        self.assertEqual(load_research_report(self.root), '# 연구 결과\n')

    def test_invalid_incomplete_and_nonfinite_results_do_not_fabricate_numbers(self):
        path = self.directory / 'selection.json'
        path.write_text('{incomplete', encoding='utf-8')
        self.assertEqual(load_research_status(self.root)['status'], 'invalid')
        self.write_selection(selected_metrics={'train': {}})
        self.assertFalse(load_research_status(self.root)['available'])
        raw = self.write_selection()
        raw['selected_metrics']['test'].update(return_pct=float('nan'), trades=True)
        path.write_text(json.dumps(raw), encoding='utf-8')
        data = load_research_status(self.root)
        self.assertIsNone(data['selected_metrics']['test']['return_pct'])
        self.assertIsNone(data['selected_metrics']['test']['trades'])

    def test_no_qualified_model_is_never_approved(self):
        self.write_selection(status='no_qualified_model', selected_config=None,
                             selected_metrics={}, approved_paper=True)
        data = load_research_status(self.root)
        self.assertTrue(data['available'])
        self.assertEqual(data['status'], 'no_qualified_model')
        self.assertFalse(data['approved_paper'])
        self.assertIsNone(data['selected_metrics']['test'])

    def test_http_summary_requires_session_and_both_routes_are_read_only(self):
        from trend_web import handler, PORT
        self.write_selection()
        (self.directory / 'report.md').write_text('# 연구 보고서', encoding='utf-8')
        dashboard = SimpleNamespace(token='session-test')
        with patch('trend_web.ROOT', self.root):
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler(dashboard))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                def request(path, token):
                    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=4)
                    connection.request('GET', path, headers={
                        'Host': f'127.0.0.1:{PORT}', 'X-Session-Token': token})
                    response = connection.getresponse()
                    result = response.status, response.read().decode('utf-8')
                    connection.close()
                    return result
                self.assertEqual(request('/api/research-model', 'wrong')[0], 403)
                code, body = request('/api/research-model', dashboard.token)
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(body)['selected_config']['id'], 'test')
                self.assertEqual(request('/research-report', '')[1], '# 연구 보고서')
                (self.directory / 'report.md').unlink()
                self.assertEqual(request('/research-report', '')[0], 404)
                self.assertEqual(vars(dashboard), {'token': 'session-test'})
            finally:
                server.shutdown()
                server.server_close()
                worker.join()


if __name__ == '__main__':
    unittest.main()
