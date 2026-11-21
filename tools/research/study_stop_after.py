"""손절(손실 왕복) 이후 흐름 + 매수/매도 시점 차트 특징 비교. 읽기 전용(공개 시세만 호출)."""
import json, sys, time, urllib.request, datetime as dt, statistics as st
from pathlib import Path

APP = Path(r'C:\Users\User\Desktop\코인 트레이딩\v5-app\v5')
OUT = Path(sys.argv[1])
KST = dt.timezone(dt.timedelta(hours=9))
FEE2 = 0.001  # 왕복 수수료 0.05%*2
CACHE = OUT / 'candles'; CACHE.mkdir(parents=True, exist_ok=True)


def get(url):
    for i in range(6):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(1 + i)
    raise RuntimeError(url)


def candles(m, start, end):
    """15분봉 [start,end) epoch, 오래된 순."""
    f = CACHE / f'{m}_{int(start)}_{int(end)}.json'
    if f.exists():
        return json.loads(f.read_text())
    out, to = {}, end
    while to > start:
        u = dt.datetime.fromtimestamp(to, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        rows = get(f'https://api.upbit.com/v1/candles/minutes/15?market={m}&count=200&to={u}')
        time.sleep(0.12)
        if not rows:
            break
        for r in rows:
            t = dt.datetime.fromisoformat(r['candle_date_time_kst']).replace(tzinfo=KST).timestamp()
            out[t] = {'t': t, 'o': r['opening_price'], 'h': r['high_price'], 'l': r['low_price'], 'c': r['trade_price'],
                      'val': r['candle_acc_trade_price']}
        to = min(out) if out else start
        if len(rows) < 200:
            break
    bars = [out[k] for k in sorted(out) if start <= k < end]
    f.write_text(json.dumps(bars))
    return bars


# 1) 왕복 만들기
orders = json.loads((APP / 'data/pnl/orders.json').read_text(encoding='utf-8'))['orders'].values()
fills = []
for o in orders:
    if not str(o.get('identifier') or '').startswith('v5-'):
        continue
    for tr in o.get('trades') or []:
        t = dt.datetime.fromisoformat(tr['created_at']).timestamp()
        fills.append((t, o['market'], o['side'], float(tr['volume']), float(tr['funds'])))
fills.sort()
cur, trips = {}, []
for t, m, side, v, f in fills:
    x = cur.get(m)
    if side == 'bid':
        if x is None:
            x = cur[m] = {'m': m, 'open': t, 'q': 0., 'cost': 0., 'buy_funds': 0., 'buy_q': 0., 'sell_funds': 0., 'sell_q': 0.}
        x['q'] += v; x['cost'] += f * 1.0005; x['buy_funds'] += f; x['buy_q'] += v
    elif x is not None and x['q'] > 0:
        take = min(v, x['q'])
        x['q'] -= take; x['sell_funds'] += f * take / v; x['sell_q'] += take; x['close'] = t
        if x['q'] <= 1e-8 or x['q'] * (f / v) < 5000:
            trips.append(cur.pop(m))
for x in trips:
    x['buy_px'] = x['buy_funds'] / x['buy_q']
    x['sell_px'] = x['sell_funds'] / x['sell_q']
    x['ret'] = x['sell_px'] / x['buy_px'] - 1 - FEE2
    x['hold_h'] = (x['close'] - x['open']) / 3600

print('왕복', len(trips), '손실', sum(x['ret'] < 0 for x in trips))
stops = [x for x in trips if x['ret'] <= -0.015]
print('손절성(-1.5% 이하)', len(stops))

NOW = time.time() - 900
btc_all = candles('KRW-BTC', min(x['open'] for x in stops) - 26 * 3600, NOW)
btc = {b['t']: b for b in btc_all}


def feats(bars, t, prefix):
    """시점 t 직전 마감 봉 기준 특징."""
    past = [b for b in bars if b['t'] + 900 <= t]
    if len(past) < 96:
        return {}
    c = past[-1]['c']
    def r(n): return c / past[-1 - n]['c'] - 1
    closes = [b['c'] for b in past]
    gains = [max(0, closes[i] - closes[i - 1]) for i in range(-14, 0)]
    loss = [max(0, closes[i - 1] - closes[i]) for i in range(-14, 0)]
    rsi = 100 if sum(loss) == 0 else 100 - 100 / (1 + sum(gains) / sum(loss))
    tr = [max(b['h'], p['c']) - min(b['l'], p['c']) for p, b in zip(past[-15:-1], past[-14:])]
    hi24 = max(b['h'] for b in past[-96:]); lo24 = min(b['l'] for b in past[-96:])
    v1 = sum(b['val'] for b in past[-4:]); v24 = sum(b['val'] for b in past[-96:]) / 24
    ma20 = sum(closes[-20:]) / 20
    return {prefix + 'r1h': r(4), prefix + 'r4h': r(16), prefix + 'r24h': r(95 if len(past) > 96 else len(past) - 1),
            prefix + 'rsi': rsi, prefix + 'atr%': sum(tr) / 14 / c, prefix + 'from_hi24': c / hi24 - 1,
            prefix + 'pos24': (c - lo24) / (hi24 - lo24) if hi24 > lo24 else .5, prefix + 'vol_x': v1 / v24 if v24 else 0,
            prefix + 'vs_ma20': c / ma20 - 1, prefix + 'val24_억': sum(b['val'] for b in past[-96:]) / 1e8}


def btc_ret(t0, t1):
    a = [b for b in btc_all if b['t'] + 900 <= t0]; z = [b for b in btc_all if b['t'] + 900 <= t1]
    return z[-1]['c'] / a[-1]['c'] - 1 if a and z else None


rows = []
for i, x in enumerate(stops):
    bars = candles(x['m'], x['open'] - 26 * 3600, min(NOW, x['close'] + 8 * 86400))
    after = [b for b in bars if b['t'] >= x['close']]
    hold = [b for b in bars if x['open'] <= b['t'] < x['close']]
    if not after:
        continue
    bp, sp = x['buy_px'], x['sell_px']
    row = {'m': x['m'], 'open': dt.datetime.fromtimestamp(x['open'], KST).strftime('%m-%d %H:%M'),
           'close': dt.datetime.fromtimestamp(x['close'], KST).strftime('%m-%d %H:%M'), 'ret': x['ret'], 'hold_h': x['hold_h'],
           'buy_px': bp, 'sell_px': sp, 'after_h': (after[-1]['t'] + 900 - x['close']) / 3600}
    # 매수 후 보유 중 최고(이익이 났었는지)
    row['hold_max'] = max((b['h'] for b in hold), default=bp) / bp - 1
    for H in (6, 24, 72, 168):
        w = [b for b in after if b['t'] < x['close'] + H * 3600]
        row[f'max{H}'] = max(b['h'] for b in w) / bp - 1 - FEE2
        row[f'min{H}'] = min(b['l'] for b in w) / sp - 1
    # 매수가 회복까지 시간
    rec = next((b for b in after if b['h'] >= bp * (1 + FEE2)), None)
    row['recover_h'] = (rec['t'] + 900 - x['close']) / 3600 if rec else None
    p3 = next((b for b in after if b['h'] >= bp * 1.03 + bp * FEE2), None)
    row['plus3_h'] = (p3['t'] + 900 - x['close']) / 3600 if p3 else None
    # 회복 전에 더 빠졌던 깊이
    pre = [b for b in after if rec is None or b['t'] <= rec['t']]
    row['dd_before_rec'] = min(b['l'] for b in pre) / bp - 1
    row['last'] = after[-1]['c'] / bp - 1 - FEE2
    row.update(feats(bars, x['open'], 'B_'))
    row.update(feats(bars, x['close'], 'S_'))
    row['btc_pre24'] = btc_ret(x['open'] - 86400, x['open'])
    row['btc_hold'] = btc_ret(x['open'], x['close'])
    row['btc_after24'] = btc_ret(x['close'], min(NOW, x['close'] + 86400))
    rows.append(row)
    print(i, x['m'], f"{x['ret']:+.2%}", 'rec', row['recover_h'], flush=True)

(OUT / 'stop_rows.json').write_text(json.dumps(rows, ensure_ascii=False, indent=0), encoding='utf-8')
(OUT / 'all_trips.json').write_text(json.dumps([{k: x[k] for k in ('m', 'open', 'close', 'ret', 'hold_h', 'buy_px', 'sell_px')} for x in trips]), encoding='utf-8')
