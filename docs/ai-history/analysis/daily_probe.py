import sys, time, json, urllib.request, statistics as S
sys.path.insert(0, r'c:/Users/User/Desktop/ai-trade/v5')
import upbit, strategy as st
P = st.Params()
mk = [x['market'] for x in json.loads(urllib.request.urlopen('https://api.upbit.com/v1/market/all').read()) if x['market'].startswith('KRW-')]
tk = upbit.tickers(mk)
pool = sorted((m for m in tk if m not in P.exclude), key=lambda m: -tk[m]['acc_trade_price_24h'])[:30]
for m in ('KRW-BTC', 'KRW-ETH', 'KRW-XRP', 'KRW-DOGE'):
    if m not in pool: pool.append(m)
bars = {}
for m in pool:
    try: bars[m] = upbit.completed_candles(m, 1440, 200)
    except Exception: pass
    time.sleep(0.12)
bars = {m: b for m, b in bars.items() if len(b) >= 190}
n = min(len(b) for b in bars.values())
c = {m: [x['c'] for x in b][-n:] for m, b in bars.items()}
print(f'일봉 {n}일, 종목 {len(c)}개 (현재 상위 거래대금 기준이라 생존 편향 있음)')
def hold(m): return c[m][-1] / c[m][0] - 1
for m in ('KRW-BTC', 'KRW-ETH', 'KRW-XRP', 'KRW-DOGE'): print(f'  {m[4:]:5s} 기간 보유 수익 {hold(m):+.1%}')
def mdd(eq):
    pk, d = 1, 0
    for v in eq: pk = max(pk, v); d = min(d, v / pk - 1)
    return d
def rot(win, cap, top=3, cost=0.005):
    eq, v, held = [1.0], 1.0, set()
    for t in range(win, n - 1):
        rs = {m: c[m][t] / c[m][t - win] - 1 for m in c}
        cand = [m for m in sorted(rs, key=rs.get, reverse=True)[:top] if rs[m] > 0 and (cap is None or rs[m] < cap)]
        new = set(cand)
        turn = len(new - held) / max(len(new), 1) if new else (1 if held else 0)
        if not new: r = 0.0
        else: r = S.mean(c[m][t + 1] / c[m][t] - 1 for m in new)
        r -= cost * turn * (1 if new else 0)
        v *= 1 + r; eq.append(v); held = new
    return v - 1, mdd(eq)
print('\n[일봉 모멘텀 순환: 매일 상승률 상위 3종목 균등 보유, 교체 비용 0.5%]')
for win in (1, 3, 7):
    for cap in (None, 0.15):
        r, d = rot(win, cap)
        print(f'  상승률 기간 {win}일, 과열 상한 {"없음" if cap is None else "15%"}: 수익 {r:+.1%}, 최대낙폭 {d:.1%}')
btc = [c['KRW-BTC'][t] / c['KRW-BTC'][0] for t in range(n)]
print(f'  비교 BTC 보유: 수익 {btc[-1]-1:+.1%}, 최대낙폭 {mdd(btc):.1%}')
core = [S.mean(c[m][t] / c[m][0] for m in ('KRW-BTC', 'KRW-ETH', 'KRW-XRP')) for t in range(n)]
print(f'  비교 BTC·ETH·XRP 균등 보유: 수익 {core[-1]-1:+.1%}, 최대낙폭 {mdd(core):.1%}')

print('\n[메이저 추세 규칙: 종가가 N일 평균 위면 보유, 아래면 현금(전량), 전환 비용 0.5%]')
def sma(v, t, N): return S.mean(v[t - N + 1:t + 1])
for N in (10, 20, 50):
    for names in (('KRW-BTC',), ('KRW-BTC', 'KRW-ETH', 'KRW-XRP', 'KRW-DOGE')):
        eq, v = [1.0], 1.0
        pos = {m: False for m in names}
        for t in range(N, n - 1):
            rets = []
            for m in names:
                on = c[m][t] > sma(c[m], t, N)
                r = c[m][t + 1] / c[m][t] - 1 if on else 0.0
                if on != pos[m]: r -= 0.005 / 2   # 전환 한 번(편도)당 0.25%: 왕복 0.5%
                pos[m] = on
                rets.append(r)
            v *= 1 + S.mean(rets); eq.append(v)
        hold_eq = [S.mean(c[m][t] / c[m][N] for m in names) for t in range(N, n)]
        print(f'  {N:2d}일 평균, {"BTC" if len(names)==1 else "BTC·ETH·XRP·DOGE"}: 규칙 수익 {v-1:+.1%} (최대낙폭 {mdd(eq):.1%}) vs 그냥 보유 {hold_eq[-1]-1:+.1%} (최대낙폭 {mdd(hold_eq):.1%})')
