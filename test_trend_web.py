import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from trend_engine import Engine
from trend_web import Dashboard, handler, PORT


class WebTests(unittest.TestCase):
    def test_indicators_and_saved_holding_settings(self):
        code, body = self.request('/api/indicators')
        self.assertEqual(code, 200)
        rows = json.loads(body)['rows']
        self.assertEqual(len(rows), 7)
        self.assertEqual({r['id'] for r in rows},
                         {'kospi', 'kosdaq', 'usdkrw', 'jpykrw', 'sp500', 'nasdaq', 'fear'})
        self.assertEqual(self.request('/api/indicators', **{'X-Session-Token': 'wrong'})[0], 403)
        code, _ = self.request('/api/action', 'POST',
                              {'action': 'holding_settings', 'max_minutes': 25, 'progress_minutes': 0})
        self.assertEqual(code, 200)
        self.assertEqual(self.d.auto_v3.profiles()['short']['max_hours'], 25/60)
        self.assertEqual(self.d.portfolio.profiles()['short']['progress_seconds'], 0)
        self.assertEqual(self.request('/api/action', 'POST',
                         {'action': 'holding_settings', 'max_minutes': 5, 'progress_minutes': 10})[0], 400)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.e = Engine(self.tmp.name, network=False)
        self.d = Dashboard(self.e)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.d))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, method='GET', body=None, **overrides):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        headers = {'Host': f'127.0.0.1:{PORT}', 'X-Session-Token': self.d.token,
                   'Content-Type': 'application/json', **overrides}
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = connection.getresponse()
        content = response.read().decode('utf-8')
        code = response.status
        connection.close()
        return code, content

    def test_health_and_page_do_not_start_orders(self):
        self.assertEqual(self.request('/api/health')[0], 200)
        code, page = self.request('/')
        self.assertEqual(code, 200)
        self.assertIn('시황 시그널', page)
        self.assertIn(self.d.token, page)
        self.assertFalse(self.e.running)
        self.assertIsNone(self.e.broker)

    def test_data_includes_summary_account_and_hourly(self):
        code, content = self.request('/api/data')
        self.assertEqual(code, 200)
        data = json.loads(content)
        self.assertIn('market_summary', data)
        self.assertIn('account', data)
        self.assertEqual(data['hourly'], [])
        self.assertIn('auto_v3',data)

    def test_v3_budget_start_and_live_gate_are_session_protected(self):
        body={'action':'auto_v3_config','mode':'paper','budget':100000,'markets':['KRW-BTC']}
        self.assertEqual(self.request('/api/action','POST',body,**{'X-Session-Token':'wrong'})[0],403)
        self.assertEqual(self.request('/api/action','POST',body)[0],200)
        self.assertEqual(self.request('/api/action','POST',{'action':'auto_v3_start','mode':'paper'})[0],200)
        self.assertTrue(self.d.auto_v3.running)
        code,result=self.request('/api/action','POST',{'action':'auto_v3_start','mode':'live'})
        self.assertEqual(code,400);self.assertIn('검증',result)
        self.assertIsNone(self.e.broker)
        self.assertEqual(self.request('/api/action','POST',{'action':'auto_v3_pause'})[0],200)

    def test_v3_view_keeps_existing_panels(self):
        code,page=self.request('/')
        self.assertEqual(code,200)
        for text in ('시장 영향 주요 뉴스','전체 자산과 수익 현황','시황 시그널','자동 거래 V3',
                     '자동 신규 매수 시작','v3-positions','v3-orders','v3-fills','data-delegate'):
            self.assertIn(text,page)

    def test_wrong_token_cannot_read_account(self):
        self.assertEqual(self.request('/api/data', **{'X-Session-Token': 'wrong'})[0], 403)

    def test_recommendations_are_protected_read_only_data(self):
        self.assertEqual(self.request('/api/recommendations', **{'X-Session-Token':'wrong'})[0],403)
        code,content=self.request('/api/recommendations')
        self.assertEqual(code,200);self.assertIn('rankings',json.loads(content))
        self.assertIsNone(self.e.broker);self.assertFalse(self.e.running)

    def test_simulation_window_uses_same_protected_account_api(self):
        code,page=self.request('/simulation')
        self.assertEqual(code,200);self.assertIn('실시간 모의 종목 현황',page);self.assertIn(self.d.token,page)
        self.assertFalse(self.d.portfolio.running);self.assertEqual(self.d.portfolio.state['orders']['live'],[])

    def test_signal_is_directly_below_profit_and_above_recommendations(self):
        _,page=self.request('/')
        self.assertLess(page.index('전체 자산과 수익 현황'),page.index('<h2>시황 시그널'))
        self.assertLess(page.index('<h2>시황 시그널'),page.index('투자 우선순위 TOP 10'))

    def test_empty_or_stale_research_cannot_reset_paper_for_trial(self):
        before=self.d.portfolio.state.copy()
        code,content=self.request('/api/action','POST',{'action':'portfolio_trial_start','bucket':'all'})
        self.assertEqual(code,400);self.assertIn('갱신',json.loads(content)['error'])
        self.assertEqual(before,self.d.portfolio.state)

    def test_news_is_session_protected_and_does_not_trade(self):
        self.assertEqual(self.request('/api/news', **{'X-Session-Token':'wrong'})[0],403)
        code,content=self.request('/api/news')
        self.assertEqual(code,200)
        self.assertIn('history',json.loads(content))
        self.assertFalse(self.e.running)
        self.assertIsNone(self.e.broker)

    def test_cross_origin_cannot_start(self):
        self.assertEqual(self.request('/api/action', 'POST', {'action': 'start'}, Origin='https://evil.example')[0], 403)
        self.assertFalse(self.e.running)

    def test_cross_site_cannot_read(self):
        self.assertEqual(self.request('/api/data', **{'Sec-Fetch-Site': 'cross-site'})[0], 403)

    def test_wrong_host_cannot_access_page(self):
        self.assertEqual(self.request('/', Host='evil.example')[0], 403)

    def test_paper_start_and_pause_use_shared_engine(self):
        self.assertEqual(self.request('/api/action', 'POST', {'action': 'start'})[0], 200)
        self.assertTrue(self.e.running)
        self.assertEqual(self.e.mode, 'paper')
        self.assertEqual(self.request('/api/action', 'POST', {'action': 'pause'})[0], 200)
        self.assertFalse(self.e.running)
        self.assertIsNone(self.e.broker)

    def test_invalid_action_cannot_change_mode(self):
        self.assertEqual(self.request('/api/action', 'POST', {'action': 'unknown'})[0], 400)
        self.assertEqual(self.e.mode, 'paper')

    def test_invalid_mode_rejected(self):
        self.assertEqual(self.request('/api/action', 'POST', {'action': 'mode', 'mode': 'bad'})[0], 400)

    def test_non_json_post_rejected(self):
        self.assertEqual(self.request('/api/action', 'POST', {'action':'start'}, **{'Content-Type':'text/plain'})[0], 403)
        self.assertFalse(self.e.running)

    def test_shutdown_requires_token_origin_and_explicit_confirmation(self):
        body={'action':'app_shutdown','confirm':True}
        self.assertEqual(self.request('/api/action','POST',body,**{'X-Session-Token':'wrong'})[0],403)
        self.assertEqual(self.request('/api/action','POST',body,Origin='https://evil.example')[0],403)
        self.assertEqual(self.request('/api/action','POST',{'action':'app_shutdown'})[0],400)
        self.assertFalse(self.e.stop_event.is_set())

    def test_shutdown_saves_state_stops_server_and_never_sells_holdings(self):
        self.e.running=True;self.d.portfolio.running=True;self.d.auto_v3.running=True
        # Real open exchange orders must be retained, not canceled by an exit button.
        self.d.portfolio.state['orders']['live'].append({'identifier':'pf-owned','side':'ask','status':'wait'})
        code,content=self.request('/api/action','POST',{'action':'app_shutdown','confirm':True})
        self.assertEqual(code,200);self.assertTrue(json.loads(content)['ok'])
        self.thread.join(timeout=2);self.assertFalse(self.thread.is_alive())
        self.assertTrue(self.e.stop_event.is_set());self.assertFalse(self.e.running)
        self.assertFalse(self.d.auto_v3.running);self.assertFalse(self.d.portfolio.running)
        self.assertTrue(self.d.shutdown_saved);self.assertIsNone(self.e.broker)
        saved=json.loads(self.d.portfolio.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['orders']['live'][0]['status'],'wait')
        self.assertTrue(self.d.auto_v3.path.exists())

    def test_shutdown_cancels_only_local_paper_buys_and_keeps_sell_reservations(self):
        self.d.auto_v3.action({'action':'auto_v3_config','budget':100000,'markets':['KRW-BTC']})
        order=self.d.auto_v3.new_order('KRW-BTC','bid',100000,.1,'entry',1,
            atr=650,low=99500,cost_rate=.0021,signal='test',expires=30,breakout=99000,ceiling=101000)
        self.d.portfolio.state['orders']['paper']=[{'identifier':'pf-buy','side':'bid','status':'wait'},
                                                  {'identifier':'pf-sell','side':'ask','status':'wait'}]
        self.d.prepare_shutdown(True)
        self.assertEqual(order['status'],'cancel')
        self.assertEqual(self.d.portfolio.state['orders']['paper'][0]['status'],'cancel')
        self.assertEqual(self.d.portfolio.state['orders']['paper'][1]['status'],'wait')
        with self.assertRaisesRegex(ValueError,'종료'):
            self.d.action({'action':'auto_v3_start'})

    def test_failed_shutdown_save_keeps_server_available_and_allows_retry(self):
        with patch.object(self.d.auto_v3,'save',side_effect=OSError('disk full')):
            code,content=self.request('/api/action','POST',{'action':'app_shutdown','confirm':True})
        self.assertEqual(code,400);self.assertIn('저장 실패',content)
        self.assertTrue(self.thread.is_alive());self.assertFalse(self.d.shutdown_saved)
        self.assertEqual(self.request('/api/health')[0],200)
        self.assertEqual(self.request('/api/action','POST',{'action':'auto_v3_start'})[0],400)
        self.assertEqual(self.request('/api/action','POST',{'action':'app_shutdown','confirm':True})[0],200)


if __name__ == '__main__':
    unittest.main()
