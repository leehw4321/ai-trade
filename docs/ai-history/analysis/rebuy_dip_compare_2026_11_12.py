import sys, json, time, datetime as dt
from pathlib import Path
APP = Path(sys.argv[1]); sys.path.insert(0, str(APP))
import upbit, strategy as st, rebuy_review as R

orders = json.loads((APP/'data/pnl/orders.json').read_text(encoding='utf-8')).get('orders', {})
ev = []
for r in orders.values():
    if not r.get('trades'): continue
    q = sum(float(x['volume']) for x in r['trades']); f = sum(float(x['funds']) for x in r['trades'])
    ev.append((min(x['created_at'] for x in r['trades']), r['market'], r['side'], q, f, float(r.get('paid_fee') or 0)))
ev.sort()
# trips with prices
pos, trips = {}, []
for t, m, side, q, f, fee in ev:
    p = pos.setdefault(m, {'q':0.,'cost':0.,'trip':None})
    if side == 'bid':
        if p['q'] <= 1e-9 or p['trip'] is None:
            p['trip'] = {'market':m,'open':t,'bq':0.,'bf':0.,'sq':0.,'sf':0.}
        p['q'] += q; p['cost'] += f+fee; p['trip']['bq'] += q; p['trip']['bf'] += f
    elif p['q'] > 1e-9:
        take = min(q, p['q']); p['q'] -= take
        if p['trip']: p['trip']['sq'] += take; p['trip']['sf'] += f*take/q
        if p['q'] <= 1e-9 or p['q'] < take*0.001:
            if p['trip']: p['trip']['close'] = t; trips.append(p['trip'])
            p['q'] = 0.; p['trip'] = None
by = {}
for x in trips: by.setdefault(x['market'], []).append(x)

items = json.loads((APP/'data/rebuy_review.json').read_text(encoding='utf-8'))['items']
def T(s): return dt.datetime.fromisoformat(s)
rows = []
for it in items:
    xs = by[it['market']]
    i = next(k for k, x in enumerate(xs) if x['open'] == it['open'])
    prev, cur = xs[i-1], xs[i]
    sell = prev['sf']/prev['sq']; buy = cur['bf']/cur['bq']
    to = T(cur['open']).astimezone(dt.timezone.utc)
    c = upbit._get('/v1/candles/minutes/15', {'market': it['market'], 'count': 100, 'to': to.strftime('%Y-%m-%dT%H:%M:%S')})
    bars = [{'h':r['high_price'],'l':r['low_price'],'c':r['trade_price']} for r in reversed(c)]
    atr = st.wilder_atr(bars); ap = atr/buy
    # 24h window after previous sell: did price dip to limits after the actual rebuy?
    end = T(prev['close']) + dt.timedelta(hours=24)
    c2 = upbit._get('/v1/candles/minutes/15', {'market': it['market'], 'count': 100, 'to': end.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')})
    lows = [r['low_price'] for r in c2 if dt.datetime.fromisoformat(r['candle_date_time_utc']).replace(tzinfo=dt.timezone.utc) >= to]
    mn = min(lows) if lows else None
    row = dict(m=it['market'][4:], open=it['open'][5:16], gap=(T(cur['open'])-T(prev['close'])).total_seconds()/3600,
               sell=sell, buy=buy, d=buy/sell-1, atrp=ap, pnl=it['pnl'], pct=it['pct'])
    for k in (0.5, 1.0):
        lim = sell*(1-k*ap); row[f'b{k}'] = buy > lim; row[f'later{k}'] = (mn is not None and mn <= lim) if buy > lim else None
    rows.append(row); time.sleep(0.15)
print(f"{'종목':6}{'재매수':12}{'경과h':>6}{'매도가':>10}{'재매수가':>10}{'차이':>8}{'ATR%':>6}{'손익':>8}{'0.5ATR':>10}{'1ATR':>10}")
for r in rows:
    f = lambda k: ('막힘' + ('*' if r[f'later{k}'] else '')) if r[f'b{k}'] else '통과'
    print(f"{r['m']:6}{r['open']:12}{r['gap']:6.1f}{r['sell']:10.4g}{r['buy']:10.4g}{r['d']:+8.2%}{r['atrp']:6.2%}{r['pnl']:+8,}{f(0.5):>10}{f(1.0):>10}")
for k in (0.5, 1.0):
    bl = [r for r in rows if r[f'b{k}']]; ps = [r for r in rows if not r[f'b{k}']]
    lat = [r for r in bl if r[f'later{k}']]
    print(f"\n{k}ATR: 막힘 {len(bl)}건 (실제 손익 합 {sum(r['pnl'] for r in bl):+,}원, 승 {sum(r['pnl']>0 for r in bl)}) / 통과 {len(ps)}건 (합 {sum(r['pnl'] for r in ps):+,}원, 평균 {sum(r['pct'] for r in ps)/max(len(ps),1):+.2f}%)")
    print(f"   막힌 것 중 24시간 안에 눌림가까지 내려온 것(나중에 샀을 수도 있음) {len(lat)}건: {', '.join(r['m'] for r in lat)}")
