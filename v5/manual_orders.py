"""화면의 '강제 청산'·'추가 매수' 요청을 받는 로컬 전용 소형 서버.

이 서버는 주문을 내지 않는다. 검증을 통과한 요청을 큐에 넣고, 봇 메인 루프가 손절과 같은 경로로 실행한 결과를 기다려 돌려준다.
방어: 127.0.0.1에만 바인딩 · Host 검사(DNS 재바인딩) · Origin은 file://('null')만 허용 · 봇을 시작할 때마다 새로 만드는 비밀 토큰 ·
작은 본문·종목 형식 검사 · 같은 종목 30초 재요청 거부 · 한 번에 한 요청. 토큰은 로컬 status.html 안에만 있다.
"""
import hmac
import json
import queue
import re
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8765
MARKET = re.compile(r'^KRW-[A-Z0-9]{1,20}$')
MAX_BODY = 2048
KINDS = {'/liquidate': 'liquidate', '/buy': 'buy', '/protect':'protect', '/unprotect':'unprotect', '/grant': 'grant', '/obs': 'obs',
         '/promote': 'promote', '/unpromote': 'unpromote', '/buy_now': 'buy_now', '/refresh': 'refresh', '/notice_ack': 'notice_ack'}
MIN_KRW, MAX_KRW = 5000, 100_000_000  # 업비트 최소 주문금액 / 형식 점검용 상한. 실제 한도는 봇 설정이 정한다
SELL_PCTS = (25, 50, 75, 100)
SELL_ORDS = ('market', 'limit')
WAIT_SECONDS = 45
REPEAT_SECONDS = 30


class _ExclusiveServer(ThreadingHTTPServer):
    """같은 포트를 다른 프로세스가 함께 열지 못하게 한다. Windows의 SO_REUSEADDR은 중복 바인딩을 허용해 요청(토큰 포함)을 가로챌 수 있다."""
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Request:
    def __init__(self, market, kind='liquidate', krw=None, revision=None, options=None):
        self.market, self.kind, self.krw = market, kind, krw
        self.revision=revision
        self.options = options or {}
        self.event = threading.Event()
        self.ok = False
        self.message = '처리 시간 초과: 봇이 응답하지 않았습니다. 주문 기록에서 결과를 확인하세요.'

    def finish(self, ok, message):
        self.ok, self.message = bool(ok), str(message)
        self.event.set()


class ManualOrderServer:
    def __init__(self, port=DEFAULT_PORT, token=None, clock=time.time, allowed_kinds=None):
        self.port = int(port)
        self.token = token or secrets.token_urlsafe(24)
        self.clock = clock
        self.queue = queue.Queue()
        self.busy = threading.Lock()
        self.recent = {}
        self.httpd = None
        self.thread = None
        self.allowed_kinds=set(('buy','liquidate') if allowed_kinds is None else allowed_kinds)

    # ---------- 검증 (서버 없이 시험할 수 있게 분리) ----------
    def check(self, host, origin, body, kind='liquidate'):
        """(상태코드, (종목, 금액) 또는 오류문). 통과하면 (200, (market, krw)). 청산은 금액이 None이다."""
        if host not in (f'127.0.0.1:{self.port}', f'localhost:{self.port}'):
            return 403, '허용되지 않은 호스트'
        if origin not in (None, 'null'):
            return 403, '허용되지 않은 출처'
        if kind not in self.allowed_kinds:
            return 403, '이 화면에서 허용되지 않은 기능'
        if len(body) > MAX_BODY:
            return 413, '요청이 너무 큼'
        try:
            doc = json.loads(body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return 400, '요청 형식 오류'
        if not isinstance(doc, dict) or not hmac.compare_digest(str(doc.get('token', '')), self.token):
            return 403, '인증 실패: 화면을 새로고침한 뒤 다시 시도하세요'
        if kind == 'refresh':  # 화면 즉시 새로고침: 주문과 무관, 종목이 필요 없다
            return (200, (None, None)) if doc.get('confirm') is True else (400, '확인되지 않은 요청')
        if kind == 'grant':  # 이 PC에 실행권 받기: 종목이 필요 없다
            return (200, (None, None)) if doc.get('confirm') is True else (400, '확인되지 않은 요청')
        if kind == 'notice_ack':  # 화면 상단 공지 확인: 주문과 무관
            nid = doc.get('id')
            if doc.get('confirm') is not True or not isinstance(nid, str) or not re.match(r'^[A-Za-z0-9_.-]{1,80}$', nid):
                return 400, '요청 형식 오류'
            return 200, (nid, None)
        if kind == 'obs':  # 관찰·그림자 관리 창: 주문과 무관한 기록 변경
            oid, act, note = doc.get('id'), doc.get('action'), doc.get('note')
            bad_id = not isinstance(oid, str) or not re.match(r'^[a-z0-9_-]{1,40}$', oid)
            bad_note = note is not None and (not isinstance(note, str) or len(note) > 300)
            if doc.get('confirm') is not True or bad_id or act not in ('done', 'hold', 'open', 'note') or bad_note:
                return 400, '요청 형식 오류'
            return 200, (oid, {'action': act, 'note': note})
        market = doc.get('market')
        if doc.get('confirm') is not True or not isinstance(market, str) or not MARKET.match(market):
            return 400, '확인되지 않은 요청'
        krw = None
        if kind == 'buy':
            krw = doc.get('krw')
            if type(krw) is not int or not MIN_KRW <= krw <= MAX_KRW:
                return 400, '매수 금액이 올바르지 않음'
        if kind == 'liquidate':   # 수동 매도(2026-11-07): 비율 25/50/75/100%, 주문 방식 시장가/지정가(매도호가). 생략하면 전량 시장가(기존 동작)
            pct, ordk = doc.get('pct', 100), doc.get('ord', 'market')
            if type(pct) is not int or pct not in SELL_PCTS or ordk not in SELL_ORDS:
                return 400, '매도 비율 또는 주문 방식이 올바르지 않음'
        if kind == 'unprotect' and (type(doc.get('revision')) is not int or doc['revision']<0):
            return 400, '보호 상태를 새로고침한 뒤 다시 확인하세요'
        return 200, (market, krw)

    def submit(self, market, kind='liquidate', krw=None, revision=None, options=None):
        """검증된 요청을 큐에 넣고 봇의 처리 결과를 기다린다. (상태코드, 문서)"""
        now = self.clock()
        key = (kind, market)
        if now - self.recent.get(key, -1e9) < (3 if kind == 'refresh' else REPEAT_SECONDS):
            return 429, {'ok': False, 'message': '같은 종목을 방금 요청했습니다. 잠시 뒤 결과를 확인하세요.'}
        if not self.busy.acquire(blocking=False):
            return 409, {'ok': False, 'message': '다른 청산 요청을 처리 중입니다.'}
        try:
            self.recent[key] = now
            req = Request(market, kind, krw, revision, options)
            self.queue.put(req)
            req.event.wait(WAIT_SECONDS)
            return 200, {'ok': req.ok, 'message': req.message}
        finally:
            self.busy.release()

    def drain(self):
        out = []
        while True:
            try:
                out.append(self.queue.get_nowait())
            except queue.Empty:
                return out

    # ---------- HTTP ----------
    def start(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, doc):
                data = json.dumps(doc, ensure_ascii=False).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                if self.headers.get('Origin') == 'null':
                    self.send_header('Access-Control-Allow-Origin', 'null')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)

            def do_OPTIONS(self):
                self.send_response(204)
                if self.headers.get('Origin') == 'null':
                    self.send_header('Access-Control-Allow-Origin', 'null')
                    self.send_header('Access-Control-Allow-Methods', 'POST')
                    self.send_header('Access-Control-Allow-Headers', 'Content-Type')
                self.end_headers()

            def do_GET(self):
                self._send(200, {'ok': True, 'service': 'v5-manual'} if self.path == '/ping' else {'ok': False})

            def do_POST(self):
                kind = KINDS.get(self.path)
                if not kind:
                    return self._send(404, {'ok': False, 'message': '없는 경로'})
                try:
                    length = int(self.headers.get('Content-Length') or 0)
                except ValueError:
                    length = MAX_BODY + 1
                body = self.rfile.read(length) if 0 <= length <= MAX_BODY else b'x' * (MAX_BODY + 1)
                code, value = server.check(self.headers.get('Host'), self.headers.get('Origin'), body, kind)
                if code != 200:
                    return self._send(code, {'ok': False, 'message': value})
                posted = json.loads(body.decode('utf-8'))
                revision = posted.get('revision') if kind == 'unprotect' else None
                options = {'pct': posted.get('pct', 100), 'ord': posted.get('ord', 'market')} if kind == 'liquidate' else None
                code, doc = server.submit(value[0], kind, value[1], revision, options)
                self._send(code, doc)

        try:
            self.httpd = _ExclusiveServer(('127.0.0.1', self.port), Handler)
        except OSError:
            return False
        self.port=int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
