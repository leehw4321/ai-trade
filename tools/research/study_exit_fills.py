"""실제 체결(업비트 주문 캐시)로 포지션을 재구성하고, 공개 15분봉으로 보유 중 경로를 붙인다(읽기 전용)."""
import datetime as dt, json, sys, time, urllib.request
from pathlib import Path
from collections import defaultdict

SRC = Path(r'C:\Users\User\Desktop\코인 트레이딩\v5-app\v5\data\pnl\orders.json')
OUT = Path(__file__).with_name('exit_study_out.json')  # 출력은 data 성격이라 Git에 올리지 않는다
PROTECTED = {'KRW-BTC', 'KRW-XRP', 'KRW-DOGE'}
KST = dt.timezone(dt.timedelta(hours=9))
FEE = 0.0005

def t(s): return dt.datetime.fromisoformat(s).timestamp()

d = json.load(open(SRC, encoding='utf-8'))
orders = d['orders']; orders = list(orders.values()) if isinstance(orders, dict) else orders
fills = []
for o in orders:
    if o['market'] in PROTECTED or not o.get('trades'): continue
    vol = sum(float(x['volume']) for x in o['trades']); funds = sum(float(x['funds']) for x in o['trades'])
    if vol <= 0: continue
    fills.append({'m': o['market'], 'side': o['side'], 't': t(o['created_at']), 'vol': vol, 'funds': funds,
                  'fee': float(o.get('paid_fee') or 0)})
fills.sort(key=lambda x: x['t'])

# 포지션: 보유가 자투리(5,000원 미만)에서 매수로 열리고 매도로 자투리가 되면 닫힌다
pos, done = {}, []
for f in fills:
    px = f['funds'] / f['vol']
    p = pos.get(f['m'])
    if f['side'] == 'bid':
        if p is None or p['qty'] * px < 5000:
            p = pos[f['m']] = {'m': f['m'], 'open': f['t'], 'qty': 0., 'cost': 0., 'buys': 0, 'sells': [], 'proceeds': 0., 'sold_cost': 0.}
        p['qty'] += f['vol']; p['cost'] += f['funds'] + f['fee']; p['buys'] += 1
    else:
        if p is None or p['qty'] <= 0: continue
        q = min(f['vol'], p['qty']); avg = p['cost'] / p['qty']
        p['sold_cost'] += avg * q; p['cost'] -= avg * q; p['qty'] -= q
        p['proceeds'] += f['funds'] - f['fee']; p['sells'].append({'t': f['t'], 'px': px, 'q': q})
        if p['qty'] * px < 5000:
            p['close'] = f['t']; p['exit_px'] = px; done.append(p); del pos[f['m']]

def candles(m, start, end):
    out = {}; to = end
    while to > start:
        u = (f'https://api.upbit.com/v1/candles/minutes/15?market={m}&count=200&to='
             + dt.datetime.fromtimestamp(to, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
        for _ in range(3):
            try:
                rows = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'x'}), timeout=15).read()); break
            except Exception: time.sleep(1)
        else: rows = []
        time.sleep(0.12)
        if not rows: break
        for r in rows:
            k = dt.datetime.fromisoformat(r['candle_date_time_utc']).replace(tzinfo=dt.timezone.utc).timestamp()
            out[k] = (r['opening_price'], r['high_price'], r['low_price'], r['trade_price'])
        to = min(out)
    return dict(sorted((k, v) for k, v in out.items() if start <= k < end))

now = time.time()
res = []
for p in done:
    entry = p['sold_cost'] / sum(s['q'] for s in p['sells'])          # 수수료 포함 평단
    raw_entry = entry / (1 + FEE)
    c = candles(p['m'], p['open'] - 900, min(p['close'] + 24 * 3600, now))
    res.append({'m': p['m'], 'open': p['open'], 'close': p['close'], 'entry': entry, 'raw_entry': raw_entry,
                'exit_px': p['exit_px'], 'pnl': p['proceeds'] - p['sold_cost'], 'invest': p['sold_cost'],
                'buys': p['buys'], 'nsell': len(p['sells']), 'sells': p['sells'], 'bars': [[k, *v] for k, v in c.items()]})
    print(p['m'], dt.datetime.fromtimestamp(p['open'], KST).strftime('%m-%d %H:%M'), round(p['proceeds'] - p['sold_cost']), len(c), file=sys.stderr)
OUT.write_text(json.dumps({'positions': res, 'open': [{'m': p['m'], 'open': p['open'], 'qty': p['qty']} for p in pos.values()]}), encoding='utf-8')
print('closed', len(res), 'open', len(pos))
