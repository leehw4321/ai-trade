"""Minimal Upbit client: public quotes, account lookup, and market orders used by live.py."""
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
import urllib.parse
import urllib.request
import uuid

BASE = 'https://api.upbit.com'
ROOT = Path(__file__).resolve().parent
ENV_FILES = [ROOT / '.env', ROOT.parent / '.env']


def _get(path, params=None, headers=None, retries=3):
    url = BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={'Accept': 'application/json', **(headers or {})})
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(1 + attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(1 + attempt)


def krw_markets():
    """KRW markets with a warning flag (투자유의 또는 주의 항목)."""
    rows = _get('/v1/market/all', {'is_details': 'true'})
    out = {}
    for r in rows:
        if not r['market'].startswith('KRW-'):
            continue
        ev = r.get('market_event') or {}
        warned = bool(ev.get('warning')) or any((ev.get('caution') or {}).values()) or r.get('market_warning') == 'CAUTION'
        out[r['market']] = {'name': r.get('korean_name', ''), 'warning': warned}
    return out


def tickers(markets):
    out = {}
    markets = list(markets)
    for i in range(0, len(markets), 100):
        for t in _get('/v1/ticker', {'markets': ','.join(markets[i:i + 100])}):
            out[t['market']] = t
    return out


def orderbooks(markets):
    out = {}
    for ob in _get('/v1/orderbook', {'markets': ','.join(markets)}):
        unit = ob['orderbook_units'][0]
        out[ob['market']] = {'ask': unit['ask_price'], 'bid': unit['bid_price']}
    return out


def completed_candles(market, minutes, count=200, now=None):
    """Chronological candles whose period has fully ended. Each bar: t (UTC start), o, h, l, c."""
    path = '/v1/candles/days' if minutes == 1440 else f'/v1/candles/minutes/{minutes}'
    rows = _get(path, {'market': market, 'count': count})
    now = now or dt.datetime.now(dt.timezone.utc)
    bars = []
    for r in reversed(rows):
        start = dt.datetime.fromisoformat(r['candle_date_time_utc']).replace(tzinfo=dt.timezone.utc)
        if start + dt.timedelta(minutes=minutes) > now:
            continue
        bars.append({'t': start.isoformat(), 'o': r['opening_price'], 'h': r['high_price'],
                     'l': r['low_price'], 'c': r['trade_price']})
    return bars


def _keys():
    keys = {k: os.getenv(k, '').strip() for k in ('UPBIT_ACCESS_KEY', 'UPBIT_SECRET_KEY')}
    for f in ENV_FILES:
        if all(keys.values()) or not f.exists():
            continue
        for line in f.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line:
                k, v = line.split('=', 1)
                if k.strip() in keys and not keys[k.strip()]:
                    keys[k.strip()] = v.strip()
    return keys['UPBIT_ACCESS_KEY'], keys['UPBIT_SECRET_KEY']


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def accounts():
    """Read-only balance lookup (GET /v1/accounts)."""
    access, secret = _keys()
    if not access or not secret:
        raise RuntimeError('업비트 API 키를 찾지 못했습니다.')
    header = _b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())
    payload = _b64(json.dumps({'access_key': access, 'nonce': str(uuid.uuid4())}).encode())
    sig = _b64(hmac.new(secret.encode(), f'{header}.{payload}'.encode(), hashlib.sha256).digest())
    return _get('/v1/accounts', headers={'Authorization': f'Bearer {header}.{payload}.{sig}'})


def _auth(params=None):
    access, secret = _keys()
    if not access or not secret:
        raise RuntimeError('업비트 API 키를 찾지 못했습니다.')
    payload = {'access_key': access, 'nonce': str(uuid.uuid4())}
    if params:
        q = urllib.parse.urlencode(params)
        payload.update({'query_hash': hashlib.sha512(q.encode()).hexdigest(), 'query_hash_alg': 'SHA512'})
    header = _b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())
    body = _b64(json.dumps(payload).encode())
    sig = _b64(hmac.new(secret.encode(), f'{header}.{body}'.encode(), hashlib.sha256).digest())
    return {'Authorization': f'Bearer {header}.{body}.{sig}'}


def get_order(order_uuid):
    params = {'uuid': order_uuid}
    return _get('/v1/order', params, headers=_auth(params))


def open_orders():
    params = {'state': 'wait'}
    return _get('/v1/orders/open', params, headers=_auth(params))


def market_buy(market, krw):
    """시장가 매수: 원화 금액 지정."""
    return _post_order({'market': market, 'side': 'bid', 'ord_type': 'price', 'price': str(int(krw))})


def market_sell(market, volume):
    """시장가 매도: 수량 지정."""
    return _post_order({'market': market, 'side': 'ask', 'ord_type': 'market', 'volume': f'{volume:.8f}'})


def _post_order(params):
    req = urllib.request.Request(BASE + '/v1/orders', data=json.dumps(params).encode(), method='POST',
                                 headers={'Content-Type': 'application/json', **_auth(params)})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'주문 거부 {e.code}: {e.read().decode(errors="replace")[:300]}')
