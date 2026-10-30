"""Read-only public market data, isolated from trading and browser requests."""
import concurrent.futures
import copy
import json
import math
import threading
import time
import urllib.parse
import urllib.request

SERIES = [
    ('kospi', '코스피 종합주가지수', '^KS11', 'pt'),
    ('kosdaq', '코스닥', '^KQ11', 'pt'),
    ('usdkrw', '원/달러', 'KRW=X', '원 / 1달러'),
    ('jpykrw', '원/엔', 'JPYKRW=X', '원 / 100엔'),
    ('sp500', 'S&P 500', '^GSPC', 'pt'),
    ('nasdaq', '나스닥 종합', '^IXIC', 'pt'),
]


def get_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=8) as response:
        return json.loads(response.read(2_000_000))


def number(value):
    if isinstance(value, bool):
        raise ValueError('invalid number')
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('invalid number')
    return value


def fetch_series(spec, fetch=get_json):
    key, name, symbol, unit = spec
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(symbol, safe='') + '?interval=1m&range=1d'
    meta = fetch(url)['chart']['result'][0]['meta']
    value = number(meta['regularMarketPrice'])
    stamp = number(meta['regularMarketTime'])
    if stamp > time.time()+300:
        raise ValueError('future timestamp')
    previous = meta.get('chartPreviousClose', meta.get('previousClose'))
    change = (value/number(previous)-1)*100 if previous else None
    # Yahoo JPYKRW is KRW per one yen; display the customary 100-yen quote.
    displayed_value = value * 100 if key == 'jpykrw' else value
    return {'id': key, 'name': name, 'unit': unit, 'value': displayed_value, 'time': stamp,
            'change_pct': change, 'source': 'Yahoo Finance',
            'url': 'https://finance.yahoo.com/quote/'+urllib.parse.quote(symbol, safe='')+'/',
            'frequency': '공개 시세 · 지연 가능 · 장 마감 후 종가', 'error': False}


def fetch_fear(fetch=get_json):
    row = fetch('https://api.alternative.me/fng/?limit=1')['data'][0]
    value = float(row['value'])
    stamp = number(row['timestamp'])
    if not math.isfinite(value) or not 0 <= value <= 100 or stamp > time.time()+300:
        raise ValueError('invalid fear index')
    labels = {'Extreme Fear': '극도의 공포', 'Fear': '공포', 'Neutral': '중립',
              'Greed': '탐욕', 'Extreme Greed': '극도의 탐욕'}
    return {'id': 'fear', 'name': '가상화폐 공포·탐욕 (BTC 중심)', 'value': value,
            'unit': '/ 100', 'time': stamp, 'change_pct': None,
            'detail': labels.get(row.get('value_classification'), '분류 미제공'),
            'source': 'Alternative.me', 'url': 'https://alternative.me/crypto/fear-and-greed-index/',
            'frequency': '일간 지표 · 실시간 아님', 'error': False}


class MarketIndicators:
    def __init__(self):
        self.lock = threading.Lock()
        self.rows = {s[0]: {'id': s[0], 'name': s[1], 'unit': s[3], 'value': None,
                             'error': True} for s in SERIES}
        self.rows['fear'] = {'id': 'fear', 'name': '가상화폐 공포·탐욕 (BTC 중심)', 'value': None, 'error': True}
        self.checked = 0

    def refresh(self):
        jobs = [(s[0], lambda s=s: fetch_series(s)) for s in SERIES]
        jobs.append(('fear', fetch_fear))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fn): key for key, fn in jobs}
            for future in concurrent.futures.as_completed(futures):
                key = futures[future]
                try:
                    row = future.result()
                except Exception:
                    with self.lock:
                        self.rows[key]['error'] = True
                    continue
                with self.lock:
                    self.rows[key] = row
        with self.lock:
            self.checked = time.time()

    def snapshot(self):
        now = time.time()
        with self.lock:
            rows = copy.deepcopy(list(self.rows.values()))
            checked = self.checked
        for row in rows:
            row['old'] = now-row.get('time', 0) > (172800 if row['id']=='fear' else 1200)
        return {'rows': rows, 'checked': checked, 'refresh_seconds': 60}

    def loop(self, stop):
        while not stop.is_set():
            self.refresh()
            stop.wait(60)
