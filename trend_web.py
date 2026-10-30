"""Local browser dashboard for the shared v2 engine. Loopback and session protected."""
import ctypes
import hmac
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from trend_engine import Engine, Broker, ROOT, KST
from news_monitor import NewsMonitor
from portfolio import Portfolio
from recommendations import Recommender
from realtime_sync import RealtimeSync
from auto_trader import AutoTrader
from holding_settings import HoldingSettings
from market_indicators import MarketIndicators
from scalping_strategy import VERSION
from research_status import load_research_status, load_research_report

PORT = 8768
APP_ID = 'auto-trading-v4'


class Dashboard:
    def __init__(self, engine=None):
        self.engine = engine or Engine(execution_enabled=False)
        # Preserve recovery of legacy live exposure; otherwise analysis only.
        self.engine.execution_enabled = self.engine.live_exposure()
        self.token = secrets.token_urlsafe(32)
        self.account_lock = threading.Lock()
        self.account = {'connected': False, 'error': '계좌 조회 중'}
        self.command_lock = threading.Lock()
        self.shutdown_requested = threading.Event()
        self.shutdown_saved = False
        self.workers = []
        self.holding_settings = HoldingSettings(self.engine.root)
        self.indicators = MarketIndicators()
        self.news = NewsMonitor(self.engine.root/'news')
        self.portfolio = Portfolio(self.engine.root, self.engine)
        self.recommendations = Recommender(self.engine.root)
        self.realtime = RealtimeSync(self.portfolio)
        self.auto_v3 = AutoTrader(self.engine.root, self.engine.stop_event, record=True)
        self.portfolio.profiles = self.holding_settings.profiles
        self.auto_v3.profiles = self.holding_settings.profiles
        self.realtime.book_listener = self.auto_v3.ingest
        self.realtime.trade_listener = self.auto_v3.ingest_trade
        self.realtime.book_provider = self.auto_v3.book_markets

    def account_loop(self):
        while not self.engine.stop_event.is_set():
            try:
                self.portfolio.sync_account()
                with self.portfolio.lock:rows=[r.copy() for r in self.portfolio.accounts]
                krw = next((r for r in rows if r['currency'] == 'KRW'), {})
                value = {'connected': True, 'time': time.time(), 'krw': float(krw.get('balance', 0)),
                         'locked': float(krw.get('locked', 0)),
                         'holdings': [{'currency': r['currency'], 'available': float(r['balance']),
                                       'locked': float(r['locked'])} for r in rows if r['currency'] != 'KRW'
                                      and float(r['balance']) + float(r['locked']) > 0]}
            except Exception:
                with self.portfolio.lock:self.portfolio.account_error='계좌 REST 조회 지연 · 권한·등록 IP·연결 확인'
                value = {'connected': False, 'error': '연결 확인 필요: API 권한·등록 IP·인터넷을 확인하세요.'}
            with self.account_lock:
                self.account = value
            self.portfolio.account_wake.wait(2)
            self.portfolio.account_wake.clear()

    def data(self):
        s = self.engine.snapshot()
        with self.account_lock:
            s['account'] = self.account.copy()
        with self.engine.lock:
            s['hourly'] = [{'t': b['t'], 'pct': (b['c']/b['o']-1)*100} for b in self.engine.cache.get(('KRW-BTC', 60), [])[-25:] if b.get('o')]
        s['holding_settings'] = self.holding_settings.snapshot()
        s['now'] = time.time()
        s['portfolio'] = self.portfolio.snapshot()
        s['portfolio']['sync']['channels']=self.realtime.snapshot()
        s['auto_v3'] = self.auto_v3.snapshot()
        return s

    def action(self, body):
        action = body.get('action')
        if action == 'app_shutdown':
            return self.prepare_shutdown(body.get('confirm') is True)
        if self.shutdown_requested.is_set():
            raise ValueError('프로그램 종료 중입니다. 새로운 작업을 실행할 수 없습니다.')
        if action == 'holding_settings':
            with self.command_lock:
                if self.shutdown_requested.is_set():raise ValueError('Application stopping')
                return self.holding_settings.update(body)
        if isinstance(action,str) and action.startswith('auto_v3_'):
            if not self.command_lock.acquire(blocking=False):
                raise ValueError('이전 작업을 처리 중입니다. 잠시 후 다시 시도하세요.')
            try:
                if self.shutdown_requested.is_set():raise ValueError('프로그램 종료 중입니다.')
                return self.auto_v3.action(body)
            finally:self.command_lock.release()
        if isinstance(action,str) and action.startswith('portfolio_'):
            if not self.command_lock.acquire(blocking=False):
                raise ValueError('이전 주문·변경을 처리 중입니다. 잠시 후 다시 시도하세요.')
            try:
                if self.shutdown_requested.is_set():raise ValueError('프로그램 종료 중입니다.')
                if action=='portfolio_trial_start':
                    data=self.recommendations.snapshot()
                    bucket=body.get('bucket','all')
                    if bucket not in ('all','long','swing','short'):
                        raise ValueError('추천 기간 오류')
                    if data['stale']:raise ValueError('추천 조사 갱신 후 시작하세요.')
                    return self.portfolio.start_trial(data['rankings'][bucket],data['time'])
                return self.portfolio.action(body)
            finally:
                self.command_lock.release()
        commands = {'mode': lambda: self.engine.switch(body.get('mode')), 'start': self.engine.start,
                    'pause': self.engine.pause, 'flatten': self.engine.flatten,
                    'connect': self.engine.connect, 'reset': self.engine.reset_paper,
                    'recover': self.engine.clear_unsubmitted}
        if action not in commands:
            raise ValueError('지원하지 않는 작업입니다.')
        if action not in ('start','pause'):
            raise ValueError('이전 주문 경로는 사용하지 않습니다. 새 포트폴리오 버튼을 이용하세요.')
        if not self.command_lock.acquire(blocking=False):
            raise ValueError('이전 작업을 처리 중입니다. 잠시 후 다시 시도하세요.')
        try:
            if self.shutdown_requested.is_set():raise ValueError('프로그램 종료 중입니다.')
            return commands[action]()
        finally:
            self.command_lock.release()

    def prepare_shutdown(self, confirmed):
        if not confirmed:
            raise ValueError('보유분 자동 감시 중단 안내를 확인한 후 종료하세요.')
        with self.command_lock:
            if self.shutdown_requested.is_set() and self.shutdown_saved:return '이미 프로그램 종료 중입니다.'
            self.shutdown_requested.set()
            # Stop workers before waiting for their locks. No new order may begin.
            self.engine.stop_event.set()
            self.engine.running=False;self.portfolio.running=False;self.auto_v3.running=False
            self.auto_v3.wake.set();self.portfolio.account_wake.set();self.portfolio.exchange_wake.set()
            try:
                with self.auto_v3.lock:
                    self.auto_v3.cancel_buys('프로그램 종료',time.time())
                    self.auto_v3.event('프로그램 종료 · 보유량·매도 예약 유지, 자동 감시 중단',time.time())
                    self.auto_v3.save()
                with self.portfolio.lock:
                    for order in self.portfolio.active('paper'):
                        if order['side']=='bid':order.update(status='cancel',message='프로그램 종료')
                    self.portfolio.save()
                with self.engine.lock:
                    self.engine.status='프로그램 종료 · 자동 감시 중단'
                    self.engine.log(self.engine.status)
                    for mode in ('paper','live'):self.engine.save(mode)
            except Exception:
                # Keep the process alive to expose the save error; never claim a clean exit.
                raise RuntimeError('종료 기록 저장 실패. 새 매수·감시는 정지됐으며 서버는 유지합니다. 디스크 상태를 확인하세요.') from None
            self.shutdown_saved=True
            return '기록 저장 완료 · 프로그램을 종료합니다. 보유분 자동 감시도 중단됩니다.'

    def finish_shutdown(self):
        self.engine.stop_event.set();self.auto_v3.wake.set()
        self.portfolio.account_wake.set();self.portfolio.exchange_wake.set()
        deadline=time.monotonic()+8
        for thread in self.workers+self.auto_v3.threads:
            thread.join(timeout=max(0,deadline-time.monotonic()))
        with self.auto_v3.lock:self.auto_v3.save()
        with self.portfolio.lock:self.portfolio.save()
        with self.engine.lock:
            for mode in ('paper','live'):self.engine.save(mode)


def handler(dashboard):
    class H(BaseHTTPRequestHandler):
        def trusted(self, token=False):
            origins = (f'http://127.0.0.1:{PORT}', f'http://localhost:{PORT}')
            if self.headers.get('Host') not in tuple(x[7:] for x in origins):
                return False
            if self.headers.get('Origin') not in (None, *origins):
                return False
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return False
            return not token or hmac.compare_digest(self.headers.get('X-Session-Token', ''), dashboard.token)

        def reply(self, value, code=200, kind='application/json'):
            data = json.dumps(value, ensure_ascii=False).encode() if kind == 'application/json' else value.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', kind + '; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self.trusted():
                return self.reply({'error': 'forbidden'}, 403)
            if self.path == '/api/health':
                return self.reply({'app': APP_ID, 'version':VERSION,'auto_v3':True,'all_krw':True,
                                   'breakout_supported':True,
                                   'shutdown_supported':True,'shutting_down':dashboard.shutdown_requested.is_set()})
            if self.path == '/':
                page = (ROOT / 'trend_dashboard.html').read_text(encoding='utf-8')
                script = (ROOT / 'portfolio_ui.js').read_text(encoding='utf-8')
                script += '\n'+(ROOT / 'recommendations_ui.js').read_text(encoding='utf-8')
                script += '\n'+(ROOT / 'selection_ui.js').read_text(encoding='utf-8')
                script += '\n'+(ROOT / 'exchange_ui.js').read_text(encoding='utf-8')
                script += '\n'+(ROOT / 'scalp_tooltip.js').read_text(encoding='utf-8')
                script += '\n'+(ROOT / 'auto_v3_ui.js').read_text(encoding='utf-8')
                script += '\n'+(ROOT / 'market_ui.js').read_text(encoding='utf-8')
                script += '\n'+(ROOT / 'research_ui.js').read_text(encoding='utf-8')
                return self.reply(page.replace('__SESSION_TOKEN__', dashboard.token).replace('__PORTFOLIO_JS__', script), kind='text/html')
            if self.path == '/guide':
                return self.reply((ROOT / 'PORTFOLIO_사용법.md').read_text(encoding='utf-8'), kind='text/plain')
            if self.path == '/v3-guide':
                return self.reply((ROOT / 'AUTO_V3_사용법.md').read_text(encoding='utf-8'), kind='text/plain')
            if self.path == '/simulation':
                page=(ROOT/'simulation_dashboard.html').read_text(encoding='utf-8')
                return self.reply(page.replace('__SESSION_TOKEN__',dashboard.token),kind='text/html')
            if self.path == '/regime-review':
                report=ROOT/'analysis_runs'/'regime_2026-10-27'/'report.html'
                if report.exists():
                    return self.reply(report.read_text(encoding='utf-8'),kind='text/html')
                return self.reply((ROOT/'REGIME_RESEARCH.md').read_text(encoding='utf-8'),kind='text/plain')
            if self.path == '/research-report':
                report = load_research_report(ROOT)
                if report is None:
                    return self.reply('연구 보고서가 아직 준비되지 않았습니다.', 404, kind='text/plain')
                return self.reply(report, kind='text/plain')
            if self.path == '/api/research-model':
                if not self.trusted(token=True):
                    return self.reply({'error': 'forbidden'}, 403)
                return self.reply(load_research_status(ROOT))
            if self.path == '/api/data':
                if not self.trusted(token=True):
                    return self.reply({'error': 'forbidden'}, 403)
                return self.reply(dashboard.data())
            if self.path == '/api/indicators':
                if not self.trusted(token=True):return self.reply({'error':'forbidden'},403)
                return self.reply(dashboard.indicators.snapshot())
            if self.path == '/api/news':
                if not self.trusted(token=True):
                    return self.reply({'error':'forbidden'},403)
                return self.reply(dashboard.news.snapshot())
            if self.path == '/api/recommendations':
                if not self.trusted(token=True):
                    return self.reply({'error':'forbidden'},403)
                return self.reply(dashboard.recommendations.snapshot())
            self.reply({'error': 'not found'}, 404)

        def do_POST(self):
            if not self.trusted(token=True) or not self.headers.get('Content-Type', '').startswith('application/json'):
                # Drain a small rejected body before closing. On Windows an unread
                # TCP request body can reset the socket before the 403 reaches the client.
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if 0<length<=4096:
                        old_timeout=self.connection.gettimeout()
                        try:
                            self.connection.settimeout(1)
                            self.rfile.read(length)
                        finally:self.connection.settimeout(old_timeout)
                except (ValueError,OSError):pass
                return self.reply({'error': 'forbidden'}, 403)
            if self.path != '/api/action':
                return self.reply({'error': 'not found'}, 404)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError('요청 크기 오류')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('요청 형식 오류')
                result = dashboard.action(body)
                if body.get('action')=='app_shutdown' and dashboard.shutdown_requested.is_set():
                    # Respond before stopping serve_forever; shutdown must run in another thread.
                    try:
                        self.reply({'ok':True,'message':result})
                        self.wfile.flush()
                    finally:
                        threading.Thread(target=self.server.shutdown,daemon=True).start()
                    return
                return self.reply({'ok': True, 'message': result or '적용했습니다.'})
            except Exception as e:
                self.reply({'error': str(e)}, 400)

        def log_message(self, *args):
            pass
    return H


def main():
    mutex = None
    if os.name == 'nt':
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        mutex = kernel.CreateMutexW(None, False, 'Local\\UpbitTrendBotV2')
        if not mutex or ctypes.get_last_error() == 183:
            raise RuntimeError('기존 트렌드 봇이 실행 중입니다. 중복 실행을 차단합니다.')
    # Bind before starting workers so port conflicts never leave trading workers behind.
    server = ThreadingHTTPServer(('127.0.0.1', PORT), BaseHTTPRequestHandler)
    d = Dashboard()
    server.RequestHandlerClass = handler(d)
    for worker in (d.account_loop,d.portfolio.loop,d.portfolio.exchange_loop):
        thread=threading.Thread(target=worker,daemon=True)
        d.workers.append(thread);thread.start()
    indicator_worker = threading.Thread(target=d.indicators.loop, args=(d.engine.stop_event,), daemon=True)
    d.workers.append(indicator_worker)
    indicator_worker.start()
    d.news.start(d.engine.stop_event)
    d.recommendations.start(d.engine.stop_event)
    d.realtime.start()
    d.auto_v3.start()
    try:
        server.serve_forever()
    finally:
        try:d.finish_shutdown()
        finally:server.server_close()


if __name__ == '__main__':
    main()
