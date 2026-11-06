"""봇 체결 이력 -> 진입 목록 + 1분봉 12시간 경로 캐시"""
import json, urllib.request, datetime as dt, time, os, sys
D = r'C:\Users\User\Desktop\코인 트레이딩\v5-app\v5\data'
S = os.path.dirname(os.path.abspath(__file__))
H = 12 * 60  # 분

rows = [json.loads(l) for l in open(D + r'\exchange_orders.jsonl', encoding='utf-8') if l.strip()]
rows = [r for r in rows if r.get('source') == 'bot-v5' and float(r['executed_volume'] or 0) > 0]
rows.sort(key=lambda r: r['created_at'])
# 봇 사유 (identifier -> reason)
reason = {}
for l in open(D + r'\live\orders.jsonl', encoding='utf-8'):
    try: o = json.loads(l)
    except Exception: continue
    if o.get('identifier') and o.get('reason'): reason[o['identifier']] = o['reason']

pos = {}      # market -> dict
entries = []
sells = []    # 모든 봇 매도 (재매수 분석용)
for r in rows:
    m = r['market']; v = float(r['executed_volume']); f = float(r['executed_funds']); fee = float(r['paid_fee'] or 0)
    t = dt.datetime.fromisoformat(r['created_at'])
    if r['side'] == 'bid':
        p = pos.get(m)
        if p is None:
            p = pos[m] = dict(market=m, t=t, buy_funds=0., buy_fee=0., vol=0., sold_funds=0., sold_fee=0., sold_vol=0., bids=[])
            entries.append(p)
        p['buy_funds'] += f; p['buy_fee'] += fee; p['vol'] += v; p['bids'].append(r['created_at'])
        p['ord'] = r['ord_type']
    else:
        rs = reason.get(r.get('identifier'), '')
        sells.append(dict(market=m, t=t, price=f / v, reason=rs, ident=r.get('identifier')))
        p = pos.get(m)
        if p is None: continue
        p['sold_funds'] += f; p['sold_fee'] += fee; p['sold_vol'] += v
        p.setdefault('sells', []).append((r['created_at'], f / v, rs))
        if p['sold_vol'] >= p['vol'] * 0.98:
            p['closed'] = t; p['reason'] = rs
            del pos[m]
for e in entries:
    e['price'] = e['buy_funds'] / e['vol']
    if 'closed' in e:
        # 전량 청산: 실제 순손익(%)
        e['actual'] = (e['sold_funds'] - e['sold_fee'] - e['buy_funds'] - e['buy_fee']) / e['buy_funds']
print('entries', len(entries), 'closed', sum('closed' in e for e in entries))

cache_p = S + r'\c1m.json'
cache = json.load(open(cache_p)) if os.path.exists(cache_p) else {}
now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
def fetch(m, start, end):
    out = {}
    to = end
    while True:
        u = 'https://api.upbit.com/v1/candles/minutes/1?market=%s&count=200&to=%s' % (m, (to - dt.timedelta(hours=9)).strftime('%Y-%m-%dT%H:%M:%S'))
        for k in range(5):
            try:
                r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'x'}), timeout=20)); break
            except Exception as ex:
                time.sleep(1.5)
        else: raise RuntimeError(u)
        time.sleep(0.13)
        if not r: break
        for c in r: out[c['candle_date_time_kst']] = [c['opening_price'], c['high_price'], c['low_price'], c['trade_price']]
        first = min(c['candle_date_time_kst'] for c in r)
        if dt.datetime.fromisoformat(first).replace(tzinfo=start.tzinfo) <= start: break
        to = dt.datetime.fromisoformat(first).replace(tzinfo=start.tzinfo)
    return out
for i, e in enumerate(entries):
    start = e['t'].replace(second=0, microsecond=0)
    end = min(start + dt.timedelta(minutes=H), now)
    key = '%s|%s' % (e['market'], start.isoformat())
    if key not in cache:
        c = fetch(e['market'], start - dt.timedelta(minutes=120), end)   # 직전 2시간 포함
        cache[key] = c
        print(i, key, len(c), flush=True)
    e['key'] = key
json.dump(cache, open(cache_p, 'w'))
json.dump(dict(entries=[{k: (v.isoformat() if isinstance(v, dt.datetime) else v) for k, v in e.items() if k not in ('bids',)} for e in entries],
               sells=[{**s, 't': s['t'].isoformat()} for s in sells]), open(S + r'\entries.json', 'w'), ensure_ascii=False)
