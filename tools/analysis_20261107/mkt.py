import json, urllib.request, time, os, datetime as dt, statistics as st
S = os.path.dirname(os.path.abspath(__file__))
def get(u):
    for _ in range(5):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'x'}), timeout=20))
        except Exception: time.sleep(1.5)
    raise RuntimeError(u)
P = S + r'\mkt.json'
if not os.path.exists(P):
    tk = get('https://api.upbit.com/v1/ticker/all?quote_currencies=KRW')
    tk.sort(key=lambda t: -t['acc_trade_price_24h'])
    mk = [t['market'] for t in tk[:40]]
    if 'KRW-BTC' not in mk: mk.append('KRW-BTC')
    out = {}
    for m in mk:
        rows = []; to = dt.datetime.utcnow() + dt.timedelta(hours=1)
        for _ in range(2):
            r = get(f'https://api.upbit.com/v1/candles/minutes/60?market={m}&count=200&to={to.strftime("%Y-%m-%dT%H:%M:%S")}')
            time.sleep(0.12)
            if not r: break
            rows += r; to = dt.datetime.fromisoformat(min(c['candle_date_time_utc'] for c in r))
        out[m] = {c['candle_date_time_kst']: c['trade_price'] for c in rows}
    json.dump(out, open(P, 'w'))
M = json.load(open(P))
hours = sorted(set.intersection(*[set(v) for v in M.values()]))
print('코인', len(M), '시간', hours[0], '~', hours[-1])
# 균등 지수 (유니버스 평균 로그수익) , BTC 별도
idx = {}; lvl = 100.0
for a, b in zip(hours, hours[1:]):
    rs = [M[m][b] / M[m][a] - 1 for m in M if m != 'KRW-BTC']
    lvl *= 1 + st.mean(rs); idx[b] = (lvl, sum(r > 0 for r in rs) / len(rs))
json.dump({'idx': idx}, open(S + r'\idx.json', 'w'))
def L(h): return idx[h][0] if h in idx else None
print('=== 날짜별 시장 (KST 09시 기준 아님, 0~24시) ===')
days = sorted(set(h[:10] for h in idx))
for d in days:
    hs = [h for h in idx if h.startswith(d)]
    if len(hs) < 6: continue
    a = L(hs[0]); b = L(hs[-1])
    btc = M['KRW-BTC'][hs[-1]] / M['KRW-BTC'][hs[0]] - 1
    up = [M[m][hs[-1]] / M[m][hs[0]] - 1 for m in M if m != 'KRW-BTC']
    print(f'{d} 시간 {len(hs):2d}개  균등지수 {((b/a)-1)*100:+.2f}%  BTC {btc*100:+.2f}%  오른 종목 {sum(x>0 for x in up)}/{len(up)}  중앙값 {st.median(up)*100:+.2f}%  평균 시간당 상승 종목비율 {st.mean(idx[h][1] for h in hs)*100:.0f}%')
print('=== 오늘 시간별 균등지수 / 상승 종목 비율 ===')
for h in [x for x in idx if x >= '2026-11-06T18']:
    print(h[5:13], f'{L(h):.2f}', f'{idx[h][1]*100:3.0f}%', '#' * int((L(h) - 94) * 12))
