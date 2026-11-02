import sys, time, json, urllib.request, itertools, statistics as S
sys.path.insert(0, r'c:/Users/User/Desktop/ai-trade/v5')
import upbit, strategy as st
P = st.Params()
mk = [x['market'] for x in json.loads(urllib.request.urlopen('https://api.upbit.com/v1/market/all').read()) if x['market'].startswith('KRW-')]
tk = upbit.tickers(mk)
pool = sorted((m for m in tk if m not in P.exclude), key=lambda m: -tk[m]['acc_trade_price_24h'])[:40]
if 'KRW-BTC' not in pool: pool.append('KRW-BTC')
bars = {}
for m in pool:
    try: bars[m] = upbit.completed_candles(m, 1440, 200)
    except Exception: pass
    time.sleep(0.12)
bars = {m: b for m, b in bars.items() if len(b) >= 190}
n = min(len(b) for b in bars.values())
c = {m: [x['c'] for x in b][-n:] for m, b in bars.items()}
coins = list(c)
print(f'일봉 {n}일, {len(coins)}종목(현재 거래대금 상위 중 이력 충분, 생존 편향 있음)')
sma = lambda v, t, N: S.mean(v[t - N + 1:t + 1])
ew = [S.mean(c[m][t] / c[m][0] for m in coins) for t in range(n)]
btc = c['KRW-BTC']
def regime_ok(kind, t):
    if kind == 'none': return True
    if kind == 'btc20': return btc[t] > sma(btc, t, 20)
    if kind == 'btc50': return t >= 49 and btc[t] > sma(btc, t, 50)
    if kind == 'ew20': return ew[t] > sma(ew, t, 20)
    if kind == 'breadth': return sum(c[m][t] / c[m][t - 7] - 1 > 0 for m in coins) / len(coins) > 0.5
def run(win, top, reg, cap, rb, t0, t1, cost=0.005):
    v, eq, held = 1.0, [1.0], set()
    for t in range(max(t0, win, 50), t1 - 1):
        if (t - t0) % rb == 0:
            if regime_ok(reg, t):
                rs = {m: c[m][t] / c[m][t - win] - 1 for m in coins}
                new = set(m for m in sorted(rs, key=rs.get, reverse=True)[:top] if rs[m] > 0 and (cap is None or rs[m] < cap))
            else: new = set()
            turn = len(new ^ held) / 2 / max(len(new | held), 1) * 2 if (new or held) else 0
            v *= 1 - cost * turn
            held = new
        r = S.mean(c[m][t + 1] / c[m][t] - 1 for m in held) if held else 0.0
        v *= 1 + r; eq.append(v)
    pk, d = 1, 0
    for x in eq: pk = max(pk, x); d = min(d, x / pk - 1)
    return v - 1, d
H = n // 2
grid = list(itertools.product((3, 7, 14, 21, 30), (1, 3, 5), ('none', 'btc20', 'btc50', 'ew20', 'breadth'), (None, 0.15, 0.30), (1, 3, 7)))
res = []
for win, top, reg, cap, rb in grid:
    a = run(win, top, reg, cap, rb, 50, H)      # 전반(탐색)
    b = run(win, top, reg, cap, rb, H, n)       # 후반(검증)
    res.append(((win, top, reg, cap, rb), a, b))
bh = lambda t0, t1: (ew[t1 - 1] / ew[t0] - 1, btc[t1 - 1] / btc[t0] - 1)
print(f'\n전반(탐색) {50}~{H}일 / 후반(검증) {H}~{n}일')
print(f'  보유 비교 — 전반: 균등 {bh(50,H)[0]:+.1%} · BTC {bh(50,H)[1]:+.1%} / 후반: 균등 {bh(H,n)[0]:+.1%} · BTC {bh(H,n)[1]:+.1%}')
print(f'  검토한 설정 {len(res)}개')
both = [r for r in res if r[1][0] > 0 and r[2][0] > 0]
print(f'  전반·후반 모두 이익: {len(both)}개 / {len(res)}개 ({len(both)/len(res):.0%})')
print('\n[전반에서 가장 좋았던 상위 8개 → 후반 성적 (과적합 확인)]')
for k, a, b in sorted(res, key=lambda r: -r[1][0])[:8]:
    print(f'  기간 {k[0]}일·상위 {k[1]}·장세필터 {k[2]}·과열상한 {k[3]}·{k[4]}일마다: 전반 {a[0]:+.1%}(낙폭 {a[1]:.0%}) → 후반 {b[0]:+.1%}(낙폭 {b[1]:.0%})')
print('\n[두 구간 모두 이익이면서 두 구간 중 나쁜 쪽이 가장 좋은 상위 8개]')
for k, a, b in sorted(both, key=lambda r: -min(r[1][0], r[2][0]))[:8]:
    print(f'  기간 {k[0]}일·상위 {k[1]}·장세필터 {k[2]}·과열상한 {k[3]}·{k[4]}일마다: 전반 {a[0]:+.1%}(낙폭 {a[1]:.0%}) / 후반 {b[0]:+.1%}(낙폭 {b[1]:.0%})')
print('\n[장세 필터별 후반 평균 수익 (전체 설정 평균)]')
for reg in ('none', 'btc20', 'btc50', 'ew20', 'breadth'):
    xs = [r[2][0] for r in res if r[0][2] == reg]; ys = [r[1][0] for r in res if r[0][2] == reg]
    print(f'  {reg:8s} 전반 평균 {S.mean(ys):+.1%} · 후반 평균 {S.mean(xs):+.1%} · 후반 이익 설정 {sum(x>0 for x in xs)}/{len(xs)}')
print('[판단 기간별 후반 평균]')
for w in (3, 7, 14, 21, 30):
    xs = [r[2][0] for r in res if r[0][0] == w]
    print(f'  {w:2d}일: 후반 평균 {S.mean(xs):+.1%} · 이익 설정 {sum(x>0 for x in xs)}/{len(xs)}')
