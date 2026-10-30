"""Upbit spot engine v2. Shared paper/live rules; no orders on import/startup."""
import datetime as dt
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid
from urllib.parse import urlencode, unquote
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import upbit_bot as legacy
import market_regime
import adaptive_model

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'trend_data'
MARKETS = ('KRW-BTC', 'KRW-ETH')
KST = dt.timezone(dt.timedelta(hours=9))
BUDGET = 1_000_000.0
FEE = .0005  # paper assumption; live reads orders/chance
SLIP = .0005  # paper adverse slippage per side, in addition to observed spread
_candle_gate = threading.Lock()
_next_candle_request = 0.0


class ApiError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__('업비트 HTTP ' + str(status) + ' (권한·등록 IP·잔고·주문 조건 확인)')


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


def http(method, path, params=None, signer=None):
    global _next_candle_request
    if method=='GET' and path.startswith('/v1/candles/'):
        # Share the candle allowance between research, strategy and trial setup.
        with _candle_gate:
            delay=_next_candle_request-time.monotonic()
            if delay>0:time.sleep(delay)
            _next_candle_request=time.monotonic()+.17
    params = params or {}
    query = urlencode(params, doseq=True)
    url = legacy.BASE + path
    headers = {'Accept': 'application/json', 'User-Agent': 'local-upbit-trend/2'}
    body = None
    if method == 'POST':
        headers['Content-Type'] = 'application/json'
        body = json.dumps(params).encode()
    elif query:
        url += '?' + query
    if signer:
        headers['Authorization'] = 'Bearer ' + signer.token(unquote(query))
    try:
        with urlopen(Request(url, data=body, headers=headers, method=method), timeout=4) as r:
            return json.load(r)
    except HTTPError as e:
        # Never expose signed requests, credentials, or unfiltered response bodies.
        raise ApiError(e.code) from None
    except Exception:
        raise RuntimeError('업비트 응답 실패 또는 시간 초과. 신규 주문을 보류합니다.') from None


def ema(values, n):
    return legacy.ema(values, n)


def evaluate(hour, four, now=None, four_features=None):
    """Input is chronological *closed* OHLCV. Current breakout bar excluded from reference."""
    now = time.time() if now is None else now
    if len(hour) < 30 or len(four) < 600:
        return {'ok': False, 'reason': '지표 준비 중: 4시간봉 600개 필요'}
    # Historical exchange pauses must not lock the strategy forever. Restart hourly
    # ATR warmup after an old gap, but reject gaps inside the last 30 hourly bars.
    hourly_gaps = [i + 1 for i, (a, b) in enumerate(zip(hour, hour[1:])) if b['t'] - a['t'] != 3600]
    if hourly_gaps:
        hour = hour[hourly_gaps[-1]:]
        if len(hour) < 30:
            return {'ok': False, 'reason': '최근 봉 데이터 누락: 신규 진입 중단'}
    for rows, seconds in ((hour, 3600), (four, 14400)):
        if rows[-1]['t'] + seconds > now or now - (rows[-1]['t'] + seconds) >= seconds:
            return {'ok': False, 'reason': '마감 봉 지연 또는 미완성 봉'}
        if any(b['t'] - a['t'] != seconds for a, b in zip(rows, rows[1:])):
            return {'ok': False, 'reason': '봉 데이터 누락: 신규 진입 중단'}
    closes = [x['c'] for x in four]
    trend = (closes[-1] > four_features['ema200'] and four_features['ema50'] > four_features['ema50_prev']) if four_features else (closes[-1] > ema(closes, 200) and ema(closes, 50) > ema(closes[:-1], 50))
    last = hour[-1]
    high = max(x['h'] for x in hour[-21:-1])
    mean_vol = sum(x['v'] for x in hour[-21:-1]) / 20
    vol = last['v'] / mean_vol if mean_vol else 0
    tr = [max(b['h'] - b['l'], abs(b['h'] - a['c']), abs(b['l'] - a['c']))
          for a, b in zip(hour, hour[1:])]
    atr = sum(tr[:14]) / 14
    for x in tr[14:]:
        atr = (atr * 13 + x) / 14
    checks = [trend, last['c'] > high, vol >= 1.5, atr > 0]
    names = ['4시간 상승 추세', '직전 20봉 고점 돌파', '거래량 1.5배', 'ATR 유효']
    return {'ok': all(checks), 'reason': ' / '.join(n for n, ok in zip(names, checks) if not ok) or '매수 조건 충족',
            'id': str(last['t']), 'close': last['c'], 'breakout': high, 'atr': atr,
            'volume_ratio': vol, 'time': now, 'closed_at': last['t'] + 3600,
            'checks': dict(zip(names, checks))}


def position_size(equity, cash, exposure, distance, fees=FEE * 2, risk=.005, max_position=.4, max_total=.8):
    if (not all(math.isfinite(x) for x in (equity, cash, exposure, distance, fees, risk, max_position, max_total))
            or distance <= 0 or not 0<=risk<=.005 or not 0<=max_position<=.4 or not 0<=max_total<=.8):
        return 0
    # Risk includes assumed spread/slippage reserve; cash also reserves entry fees.
    return max(0, min(equity * risk / (distance + fees + .002), equity * max_position,
                      equity * max_total - exposure, cash / (1 + max(fees, FEE))))


def update_stop(position, observed_price, trail_atr=3):
    peak=max(position['peak'],observed_price)
    return peak,max(position['stop'],peak-trail_atr*position['atr'])


def initial_state():
    return {'version': 2, 'cash': BUDGET, 'positions': {}, 'pending': None, 'last_signal': {},
            'cooldown': {}, 'peak': BUDGET, 'day': '', 'day_start': BUDGET,
            'halt': '', 'review': False, 'trades': [], 'baseline': {}, 'initialized': False}


class Broker:
    def __init__(self):
        legacy.load_env()
        self.api = legacy.Upbit()

    def get(self, path, params=None):
        return http('GET', path, params, self.api)

    def submit(self, pending):
        if legacy.paper_day_active():
            raise RuntimeError('기존 paper_day.json의 실거래 차단일입니다.')
        params = {'market': pending['market'], 'side': pending['side'], 'identifier': pending['identifier']}
        if pending['side'] == 'bid':
            params.update(ord_type='price', price=str(int(pending['amount'])))
        else:
            params.update(ord_type='market', volume=f"{pending['volume']:.8f}")
        return http('POST', '/v1/orders', params, self.api)


class Engine:
    def __init__(self, root=DATA, broker=None, network=True, execution_enabled=True):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.mode = 'paper'  # never resume live automatically
        self.running = False
        self.status = '시뮬레이션 대기 — 실제 주문 없음'
        self.errors = []
        self.quotes, self.signals, self.cache = {}, {}, {}
        self.last_scan = 0
        self.scan_busy = False
        self.account = None
        self.broker = broker
        self.chances = {}
        self.network = network
        self.execution_enabled = execution_enabled
        self.market_summary = market_regime.unavailable()
        self.policy_snapshot = adaptive_model.unknown('장세별 거래 모델 준비 중')
        self.validation_path = ROOT / 'analysis_runs' / 'regime_2026-10-27' / 'validation.json'
        guard_path=self.root/'regime_guard.json'
        self.regime_guard=json.loads(guard_path.read_text(encoding='utf-8')) if guard_path.exists() else {'embargo_until':0,'event':''}
        if (not isinstance(self.regime_guard,dict) or not isinstance(self.regime_guard.get('event'),str)
                or not isinstance(self.regime_guard.get('embargo_until'),(int,float))
                or not math.isfinite(self.regime_guard['embargo_until']) or self.regime_guard['embargo_until']<0):
            raise RuntimeError('폭락 방어 상태 파일 손상: 복구 전 실행할 수 없습니다.')
        self.states = {}
        for mode in ('paper', 'live'):
            path = self.root / (mode + '.json')
            if path.exists():
                # Fail closed on corrupt state instead of silently forgetting ownership/orders.
                state = json.loads(path.read_text(encoding='utf-8'))
                required = set(initial_state())
                if not isinstance(state, dict) or not required.issubset(state) or state['version'] != 2:
                    raise RuntimeError(mode + ' 상태 파일 손상. 복구 전 실행할 수 없습니다.')
                if not math.isfinite(float(state['cash'])):
                    raise RuntimeError('상태 파일 금액 오류')
                self.states[mode] = state
            else:
                self.states[mode] = initial_state()
                self.save(mode)
        if self.live_exposure():
            self.mode = 'live'
            self.status = '복구 모드: 신규 매수 중지. 기존 실거래 주문·보유분 관리 중'
        if network:
            threading.Thread(target=self.loop, daemon=True).start()
            threading.Thread(target=self.market_loop, daemon=True).start()

    def save(self, mode):
        atomic(self.root / (mode + '.json'), self.states[mode])

    def log(self, message):
        stamp = dt.datetime.now(KST).isoformat(timespec='seconds')
        self.errors.append(stamp[11:19] + ' ' + message)
        self.errors = self.errors[-80:]
        with (self.root / 'events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps({'time': stamp, 'mode': self.mode, 'message': message}, ensure_ascii=False) + '\n')

    def live_exposure(self):
        s = self.states.get('live', {})
        return bool(s.get('positions') or s.get('pending'))

    def connect(self, require_funds=False):
        with self.lock:
            if self.broker is None:
                self.broker = Broker()
            rows = self.broker.get('/v1/accounts')
            if not isinstance(rows, list):
                raise RuntimeError('계좌 응답 형식 오류')
            account = {r['currency']: {'balance': float(r['balance']), 'locked': float(r['locked'])} for r in rows}
            for market in MARKETS:
                chance = self.broker.get('/v1/orders/chance', {'market': market})
                fees = (float(chance['bid_fee']), float(chance['ask_fee']))
                if any(not math.isfinite(f) or not 0 <= f < .01 for f in fees):
                    raise RuntimeError('수수료 정보 오류')
                self.chances[market] = chance
                opened = self.broker.get('/v1/orders/open', {'market': market, 'limit': 100})
                if opened:
                    own = self.states['live'].get('pending')
                    if any(not own or x.get('identifier') != own['identifier'] for x in opened):
                        raise RuntimeError('BTC/ETH에 다른 미체결 주문이 있습니다. 기존 주문 확인 필요')
            s = self.states['live']
            if not s['initialized']:
                cash = min(BUDGET, account.get('KRW', {}).get('balance', 0))
                if cash < BUDGET:
                    self.account = account
                    message = f'API 조회 연결 정상 · 주문 가능 원화 {cash:,.0f}원. 실거래 시작에는 원화 100만 원 이상 필요합니다. 기존 코인은 자동 매도하지 않습니다.'
                    if require_funds:
                        raise RuntimeError(message)
                    return message
                s.update(cash=BUDGET, initialized=True,
                         baseline={m: account.get(m[4:], {}).get('balance', 0) + account.get(m[4:], {}).get('locked', 0) for m in MARKETS})
                self.save('live')
            self.account = account
            self.check_balances()
            return 'API 조회 연결 정상 · 주문 전송 없음 · 기존 보유 코인은 제외'

    def check_balances(self):
        s = self.states['live']
        if s['pending']:
            return
        for m in MARKETS:
            actual = self.account.get(m[4:], {})
            total = actual.get('balance', 0) + actual.get('locked', 0)
            expected = s['baseline'].get(m, 0) + s['positions'].get(m, {}).get('volume', 0)
            if abs(total - expected) > max(2e-8, expected * 1e-7) or actual.get('locked', 0) > 1e-8:
                s['halt'] = '계좌 코인 수량 변경/잠금: 수동 거래·다른 봇 확인 필요'
                s['review'] = True
                self.save('live')
                raise RuntimeError(s['halt'])

    def switch(self, mode):
        with self.lock:
            if mode not in ('paper', 'live'):
                raise ValueError('모드 오류')
            if mode == self.mode:
                return
            if self.live_exposure():
                raise RuntimeError('실거래 보유분/확인 중 주문이 남아 있습니다. 청산 완료 후 전환하세요.')
            if mode == 'live':
                self.connect(require_funds=True)
            self.running = False
            self.mode = mode
            self.status = ('실거래' if mode == 'live' else '시뮬레이션') + ' 대기 · 시작 버튼으로 신규 진입 활성화'
            self.log('모드 전환: ' + mode + ' (신규 진입 정지)')

    def start(self):
        with self.lock:
            s = self.states[self.mode]
            if s['halt'] or s['review']:
                raise RuntimeError('손실/수량 검토 잠금 상태입니다. 기록을 확인하세요: ' + s['halt'])
            if self.mode == 'live':
                release = self.model_validation()
                if not release['live_entry_approved']:
                    raise RuntimeError('장세별 모델의 실거래 신규 매수는 검증 미통과로 차단했습니다. 시뮬레이션에서 확인하세요. 기존 보유분 방어·청산은 계속됩니다.')
                self.connect(require_funds=True)
            self.running = True
            self.status = '자동 매수 활성 · 손절/추적 청산 관리 중'
            self.log('자동 매수 시작')

    def pause(self):
        with self.lock:
            self.running = False
            self.status = '신규 매수 중지 · 보유분 손절/청산 관리는 계속'
            self.log(self.status)

    def reset_paper(self):
        with self.lock:
            if self.mode != 'paper' or self.running:
                raise RuntimeError('시뮬레이션을 정지한 뒤 초기화하세요.')
            atomic(self.root / ('paper_archive_' + str(time.time_ns()) + '.json'), self.states['paper'])
            self.states['paper'] = initial_state()
            self.save('paper')

    def equity(self, mode):
        s = self.states[mode]
        value = s['cash']
        for m, p in s['positions'].items():
            q = self.quotes.get(m)
            px = q['bid'] if q else p['entry']
            value += px * p['volume'] * (1 - self.fees(mode, m)[1])
        return value

    def fees(self, mode, m):
        c = self.chances.get(m, {}) if mode == 'live' else {}
        return float(c.get('bid_fee', FEE)), float(c.get('ask_fee', FEE))

    def risk(self, mode):
        s = self.states[mode]
        eq = self.equity(mode)
        day = dt.datetime.now(KST).date().isoformat()
        if s['day'] != day:
            s.update(day=day, day_start=eq)
        s['peak'] = max(s['peak'], eq)
        dd = 1 - eq / s['peak'] if s['peak'] else 0
        if dd >= .2:
            s['halt'] = '최고 자산 대비 -20%: 전량 청산 및 운용 중단'
            s['review'] = True
            return 'liquidate'
        if dd >= .1:
            s['halt'] = '최고 자산 대비 -10%: 검토 잠금, 신규 매수 중지'
            s['review'] = True
        if s['day_start'] > 0 and eq <= s['day_start'] * .97:
            return 'day_stop'
        return 'halt' if s['halt'] or s['review'] else 'ok'

    def quote_refresh(self):
        rows = http('GET', '/v1/orderbook', {'markets': ','.join(MARKETS)})
        now = time.time()
        quotes = {}
        for r in rows:
            units = r['orderbook_units']
            u = units[0]
            ask, bid = float(u['ask_price']), float(u['bid_price'])
            if not 0 < bid <= ask or abs(now - float(r['timestamp']) / 1000) > 8:
                raise RuntimeError('오래되거나 비정상인 호가: 신규 진입 중단')
            quotes[r['market']] = {'ask': ask, 'bid': bid, 'spread': (ask - bid) / ask,
                                    'time': now, 'units': units}
        if set(quotes) != set(MARKETS):
            raise RuntimeError('호가 응답 누락')
        with self.lock:
            self.quotes = quotes

    def bars(self, market, unit, count):
        key = (market, unit)
        existing = self.cache.get(key, [])
        collected = {x['t']: x for x in existing}
        params = {'market': market, 'count': 200}
        # Cold start: enough warmup for EMA 200. Refresh merges last 200 closed bars.
        for _ in range(1 if len(existing) >= count else math.ceil((count + 1) / 200)):
            rows = http('GET', '/v1/candles/minutes/' + str(unit), params)
            if not rows:
                break
            now = time.time()
            for r in rows:
                stamp = dt.datetime.fromisoformat(r['candle_date_time_utc']).replace(tzinfo=dt.timezone.utc).timestamp()
                if stamp + unit * 60 <= now:
                    collected[stamp] = {'t': stamp, 'o': float(r['opening_price']), 'h': float(r['high_price']),
                                        'l': float(r['low_price']), 'c': float(r['trade_price']), 'v': float(r['candle_acc_trade_volume'])}
            params['to'] = rows[-1]['candle_date_time_utc'] + 'Z'
            time.sleep(.15)
        result = sorted(collected.values(), key=lambda x: x['t'])[-count:]
        self.cache[key] = result
        return result

    def scan(self):
        try:
            scanned = {}
            series = {}
            for m in MARKETS:
                try:
                    hour, four = self.bars(m, 60, 200), self.bars(m, 240, 600)
                    result = evaluate(hour, four)
                    series[m] = (hour, four)
                except Exception:
                    result = {'ok': False, 'reason': '시세/봉 조회 실패', 'time': time.time()}
                scanned[m] = result
            if 'KRW-BTC' in series:
                regime = adaptive_model.classify(*series['KRW-BTC'])
            else:
                regime = adaptive_model.unknown('BTC 장세 기준 데이터 없음')
            for m in MARKETS:
                if m in series:
                    scanned[m] = adaptive_model.signal(*series[m], regime, scanned[m])
            with self.lock:
                self.policy_snapshot = regime
                self.signals = scanned
        finally:
            with self.lock:
                self.last_scan = time.time()
                self.scan_busy = False

    def reconcile(self):
        s = self.states['live']
        pending = s['pending']
        if not pending:
            return
        if self.broker is None:
            self.broker = Broker()
        try:
            order = self.broker.get('/v1/order', {'identifier': pending['identifier']})
        except Exception:
            self.status = '실거래 주문 확인 중: 재전송 금지. 조회로만 복구합니다.'
            return
        if order.get('state') not in ('done', 'cancel'):
            self.status = '실거래 주문 체결 확인 중 · 모드 전환 잠금'
            return
        trades = order.get('trades') or []
        volume = sum(float(t['volume']) for t in trades)
        funds = sum(float(t.get('funds', float(t['price']) * float(t['volume']))) for t in trades)
        executed = float(order.get('executed_volume', 0))
        if abs(executed - volume) > max(1e-8, executed * 1e-7):
            self.status = '체결 합계 불일치: 주문 확인 유지'
            return
        fee = float(order.get('paid_fee', 0))
        if volume:
            self.apply_fill('live', pending, volume, funds, fee)
        s['pending'] = None
        self.save('live')
        self.log('실거래 체결 확인: ' + pending['market'] + ' ' + pending['side'])

    def apply_fill(self, mode, order, volume, funds, fee):
        s = self.states[mode]
        m = order['market']
        if order['side'] == 'bid':
            if m in s['positions']:
                raise RuntimeError('중복 보유 기록: 수동 점검 필요')
            entry = funds / volume
            s['cash'] -= funds + fee
            s['positions'][m] = {'volume': volume, 'entry': entry, 'cost': funds + fee,
                                 'atr': order['atr'], 'stop': entry - order.get('stop_atr',2) * order['atr'],
                                 'peak': entry, 'entry_time': time.time(), 'strategy':order.get('strategy','trend'),
                                 'regime':order.get('regime','legacy'), 'trail_atr':order.get('trail_atr',3)}
        else:
            p = s['positions'][m]
            if volume > p['volume'] + 1e-8:
                raise RuntimeError('매도 체결 수량 초과: 수동 점검 필요')
            fraction = min(1, volume / p['volume'])
            remaining = max(0, p['volume'] - volume)
            # Include discarded sub-1e-8 dust cost in P&L so ledger still reconciles.
            cost = p['cost'] if remaining < 1e-8 else p['cost'] * fraction
            s['cash'] += funds - fee
            s['trades'].append({'time': dt.datetime.now(KST).isoformat(timespec='seconds'), 'market': m,
                                'net': funds - fee - cost, 'reason': order['reason'], 'volume': volume,
                                'entry': p['entry'], 'exit': funds / volume, 'fee_sell': fee})
            s['trades'] = s['trades'][-1000:]
            if remaining < 1e-8:
                del s['positions'][m]
                s['cooldown'][m] = time.time() + 4 * 3600
            else:
                p['cost'] -= cost
                p['volume'] = remaining
        # Live caller commits fill AND clears pending in one atomic replacement.
        # A crash must never replay an already applied fill from a pending intent.
        if mode == 'paper':
            self.save(mode)

    def order(self, mode, m, side, amount=0, atr=0, reason=''):
        if self.stop_event.is_set():raise RuntimeError('프로그램 종료 중: 신규 주문 중단')
        s = self.states[mode]
        if s['pending']:
            return
        q = self.quotes.get(m)
        if not q or time.time() - q['time'] > 8:
            raise RuntimeError('호가 만료: 주문 보류')
        volume = math.floor(s['positions'][m]['volume'] * 1e8 + 1e-7) / 1e8 if side == 'ask' else 0
        minimum = float(self.chances.get(m, {}).get('market', {}).get('bid' if side == 'bid' else 'ask', {}).get('min_total', 5000))
        if (amount if side == 'bid' else volume * q['bid']) < minimum:
            if side == 'ask':
                s.update(halt='최소 주문 미만 보유분: 수동 정리 필요', review=True)
                self.save(mode)
            return
        pending = {'identifier': 'trend2-' + uuid.uuid4().hex, 'market': m, 'side': side, 'amount': amount,
                   'volume': volume, 'atr': atr, 'reason': reason, 'created': time.time()}
        if side == 'bid':
            sig = self.signals.get(m,{})
            pending.update(stop_atr=sig.get('stop_atr',2),trail_atr=sig.get('trail_atr',3),
                           strategy=sig.get('strategy','trend'),regime=sig.get('regime','legacy'))
        if mode == 'paper':
            px = q['ask'] * (1 + SLIP) if side == 'bid' else q['bid'] * (1 - SLIP)
            funds = amount if side == 'bid' else volume * px
            volume = funds / px if side == 'bid' else volume
            fee = funds * self.fees(mode, m)[0 if side == 'bid' else 1]
            self.apply_fill(mode, pending, volume, funds, fee)
            self.log('모의 체결: ' + m + ' ' + side + ' ' + reason)
            return
        if mode != self.mode:
            raise RuntimeError('실거래 모드가 아닙니다.')
        if self.broker is None:
            raise RuntimeError('API 연결 필요')
        if side == 'ask':
            rows = self.broker.get('/v1/accounts')
            self.account = {r['currency']: {'balance': float(r['balance']), 'locked': float(r['locked'])} for r in rows}
            self.check_balances()
            if self.account.get(m[4:], {}).get('balance', 0) + 1e-8 < volume:
                raise RuntimeError('봇 소유 코인 부족: 기존 보유분 매도 차단')
            if time.time() - q['time'] > 8:
                raise RuntimeError('매도 전 조회 중 호가 만료: 다음 주기에 재시도')
        # Persist intent BEFORE sending. Ambiguous failures stay pending, never blind-resend.
        s['pending'] = pending
        self.save(mode)
        try:
            self.broker.submit(pending)
        except ApiError as e:
            if e.status in (400, 401, 403, 404, 422):
                s['pending'] = None
                self.running = False
                self.save(mode)
                self.log('주문 거절: ' + str(e))
                self.status = '주문 거절로 신규 매수 정지: 권한·잔고·주문 조건 확인'
                return
            self.status = '주문 응답 불명: 재전송 금지, 조회로 복구'
        except Exception:
            self.status = '주문 결과 불명: 자동 재전송 없이 조회로 확인. 모드 전환 잠금'
            self.log(self.status)
        self.reconcile()

    def clear_unsubmitted(self):
        """Manual recovery only, after a persisted intent can be proven absent at exchange."""
        with self.lock:
            s = self.states['live']
            p = s['pending']
            if not p:
                return '확인 중 주문 없음'
            if time.time() - p['created'] < 60:
                raise RuntimeError('주문 생성 60초 이후에 다시 확인하세요.')
            if self.broker is None:
                self.broker = Broker()
            try:
                self.broker.get('/v1/order', {'identifier': p['identifier']})
            except ApiError as e:
                if e.status != 404:
                    raise
            else:
                self.reconcile()
                return '거래소에 주문이 있습니다. 체결 조회로 처리합니다.'
            # Query absence twice, no resend. A network/auth error never counts as absence.
            time.sleep(1)
            try:
                self.broker.get('/v1/order', {'identifier': p['identifier']})
            except ApiError as e:
                if e.status != 404:
                    raise
            else:
                raise RuntimeError('주문 발견. 조회 복구를 기다리세요.')
            s['pending'] = None
            self.running = False
            self.save('live')
            self.log('거래소 주문 404 두 번 확인: 미접수 의도 해제, 자동 매수 정지')
            return '미접수 주문 잠금 해제. 자동 매수는 정지 상태입니다.'

    def manage(self, mode, liquidate=False):
        s = self.states[mode]
        policy = self.trade_policy(mode,record=True)
        liquidate = liquidate or policy.get('liquidate',False)
        for m, p in list(s['positions'].items()):
            try:
                q = self.quotes.get(m)
                if not q or time.time() - q['time'] > 8:
                    continue
                p['peak'],p['stop'] = update_stop(p,q['bid'],policy.get('trail_atr',3))
                self.save(mode)
                if liquidate or q['bid'] <= p['stop']:
                    reason = ('국면 방어: '+policy['label']) if policy.get('liquidate') else '계좌/수동 전량 청산'
                    self.order(mode, m, 'ask', reason=reason if liquidate else '손절/추적 청산')
            except Exception as e:
                self.log(m + ' 관리 오류: ' + str(e))

    def enter(self, mode):
        s = self.states[mode]
        policy = self.trade_policy(mode,record=True)
        if not policy.get('allow_entry'):
            return
        if mode == 'live' and not self.model_validation()['live_entry_approved']:
            return
        if self.risk(mode) != 'ok' or s['pending'] or len(s['positions']) >= 2:
            return
        for m in MARKETS:
            if m in s['positions'] or time.time() < s['cooldown'].get(m, 0):
                continue
            signal, q = self.signals.get(m, {}), self.quotes.get(m)
            if not signal.get('ok') or not q or time.time() - signal.get('time', 0) > 90:
                continue
            if not 0 <= time.time() - signal.get('closed_at', 0) <= 300:
                continue
            if s['last_signal'].get(m) == signal['id']:
                continue
            if q['spread'] > .001 or time.time() - q['time'] > 5:
                continue
            # Recheck current price: avoid buying a broken breakout or chasing far above signal.
            if (not signal['breakout'] < q['bid'] or q['ask'] > signal['close'] * 1.003
                    or q['bid'] < signal['close'] * .997):
                continue
            equity = self.equity(mode)
            exposure = sum(self.quotes[x]['ask'] * p['volume'] for x, p in s['positions'].items())
            cash = s['cash']
            if mode == 'live':
                rows = self.broker.get('/v1/accounts')
                self.account = {r['currency']: {'balance': float(r['balance']), 'locked': float(r['locked'])} for r in rows}
                self.check_balances()
                cash = min(cash, self.account.get('KRW', {}).get('balance', 0))
                # Account query may have delayed us: get new quotes, check signal again next tick.
                if time.time() - q['time'] > 5:
                    continue
            size = math.floor(position_size(equity, cash, exposure, signal.get('stop_atr',2) * signal['atr'] / q['ask'],
                                             sum(self.fees(mode, m)),policy['risk'],policy['max_position'],policy['max_total']))
            # Reject shallow books. Volume on displayed ask side must exceed planned buy by 5x.
            depth = sum(float(u['ask_price']) * float(u['ask_size']) for u in q['units']
                        if float(u['ask_price']) <= q['ask'] * 1.001)
            if size < 5000 or depth < size * 5:
                continue
            s['last_signal'][m] = signal['id']
            self.save(mode)
            self.order(mode, m, 'bid', size, signal['atr'], '추세 돌파')
            if s['pending']:
                break

    def flatten(self):
        with self.lock:
            self.running = False
            s = self.states[self.mode]
            s.update(halt='수동 전량 청산 요청: 운용 검토 필요', review=True)
            self.save(self.mode)
            self.manage(self.mode, True)

    def loop(self):
        while not self.stop_event.is_set():
            try:
                with self.lock:
                    if self.execution_enabled and self.live_exposure():
                        self.reconcile()
                self.quote_refresh()
                with self.lock:
                    for mode in (('paper', 'live') if self.execution_enabled else ()):
                        if mode == 'live' and not self.live_exposure():
                            continue
                        action = self.risk(mode)
                        s = self.states[mode]
                        self.manage(mode, action == 'liquidate' or s['halt'].startswith('수동 전량'))
                        self.save(mode)
                    if self.execution_enabled and self.running:
                        self.enter(self.mode)
                    if not self.scan_busy and time.time() - self.last_scan > 60:
                        self.scan_busy = True
                        threading.Thread(target=self.scan, daemon=True).start()
            except Exception as e:
                with self.lock:
                    self.status = str(e)
                    self.log(str(e))
            self.stop_event.wait(3)

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps({'mode': self.mode, 'running': self.running, 'status': self.status,
                                          'states': self.states, 'signals': self.signals, 'quotes': self.quotes,
                                          'equity': {m: self.equity(m) for m in self.states},
                                          'events': self.errors[-20:], 'api_connected': self.account is not None,
                                          'market_summary': market_regime.current_view(self.market_summary),
                                          'trade_policy':self.trade_policy(self.mode), 'model_validation':self.model_validation()}))

    def model_validation(self):
        try:
            report=json.loads(self.validation_path.read_text(encoding='utf-8'))
            import hashlib
            digest=hashlib.sha256((ROOT/'adaptive_model.py').read_bytes()).hexdigest()
            if (report.get('model_version')!=adaptive_model.VERSION
                    or report.get('sources_sha256',{}).get('adaptive_model.py')!=digest):
                raise ValueError('model version mismatch')
            result=report['decisions']['defensive_regime']
            return {'available':True,'live_entry_approved':result['live_entry_approved'],
                    'historical_pass':result['historical_pass'],'checks':result['checks'],
                    'result':next(r for r in report['results'] if r['model']=='defensive_regime' and r['slippage_each_side']==.001),
                    'comparison':[r for r in report['results'] if r['slippage_each_side']==.001],
                    'cost_stress':[r for r in report['results'] if r['model']=='defensive_regime']}
        except (OSError,ValueError,KeyError,StopIteration):
            return {'available':False,'live_entry_approved':False,'historical_pass':False,'checks':{}}

    def trade_policy(self, mode=None, record=False):
        now=time.time()
        p=self.policy_snapshot.copy()
        if p.get('available') and not 0<=now-p.get('time',0)<=90:
            p=adaptive_model.unknown('거래 국면 갱신 지연: 신규 매수 금지')
        # Intrabar protection uses only a fresh quote and already closed reference candles.
        h=self.cache.get(('KRW-BTC',60),[])
        f=self.cache.get(('KRW-BTC',240),[])
        q=self.quotes.get('KRW-BTC')
        if q and 0<=now-q['time']<=8 and h and 0<=now-(h[-1]['t']+3600)<3600:
            shock_hour=q['bid']/h[-1]['c']-1<=-.03
            shock_four=bool(f and 0<=now-(f[-1]['t']+14400)<14400 and q['bid']/f[-1]['c']-1<=-.06)
            if shock_hour or shock_four:
                p=adaptive_model.policy('crash','실시간 BTC 가격이 마지막 마감 봉 종가 대비 급락',now,
                                        hour_bar=h[-1]['t'],emergency=True)
        if mode is not None:
            if record and p.get('phase')=='crash':
                event=str(p.get('hour_bar',''))
                if self.regime_guard.get('event')!=event:
                    self.regime_guard.update(event=event,embargo_until=max(self.regime_guard.get('embargo_until',0),now+86400))
                    atomic(self.root/'regime_guard.json',self.regime_guard)
                    self.log('폭락 방어: 모든 모드 최소 24시간 신규 진입 중단')
            until=self.regime_guard.get('embargo_until',0)
            if now<until:
                p=adaptive_model.policy('crash','폭락 이후 최소 24시간 회복 대기',now,
                                        embargo_until=until,adx=p.get('adx'),atr_pct=p.get('atr_pct'))
        return p

    def refresh_market_summary(self):
        try:
            rows = http('GET', '/v1/ticker/all', {'quote_currencies': 'KRW'})
            result = market_regime.summarize(rows)
        except Exception:
            result = market_regime.unavailable('시황 조회 실패 · 연결 확인 필요')
        with self.lock:
            self.market_summary = result

    def market_loop(self):
        # Separate polling: breadth lookups must not delay position management.
        while not self.stop_event.is_set():
            self.refresh_market_summary()
            self.stop_event.wait(30)
