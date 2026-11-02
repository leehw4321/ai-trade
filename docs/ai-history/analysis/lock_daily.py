import sys, time, json, urllib.request, statistics as S
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
c = {m: [x['c'] for x in b][-n:] for m, b in bars.items()}; coins = list(c)
ew = [S.mean(c[m][t] / c[m][0] for m in coins) for t in range(n)]
sma = lambda v, t, N: S.mean(v[t - N + 1:t + 1])
def ok(kind, t):
    if kind == 'none': return True
    if kind == 'breadth': return sum(c[m][t] / c[m][t - 7] - 1 > 0 for m in coins) / len(coins) > 0.5
def path(win, top, reg, cap, dd=None, cool=0, cost=0.005, t0=50):
    v, peak, held, eq, cash_until, log = 1.0, 1.0, set(), [], -1, []
    for t in range(t0, n - 1):
        if t < cash_until: new = set()
        else:
            if ok(reg, t):
                rs = {m: c[m][t] / c[m][t - win] - 1 for m in coins}
                new = set(m for m in sorted(rs, key=rs.get, reverse=True)[:top] if rs[m] > 0 and (cap is None or rs[m] < cap))
            else: new = set()
        turn = len(new ^ held) / max(len(new | held), 1) if (new or held) else 0
        v *= 1 - cost * turn; held = new
        v *= 1 + (S.mean(c[m][t + 1] / c[m][t] - 1 for m in held) if held else 0.0)
        eq.append(v); peak = max(peak, v)
        if dd and v <= peak * (1 - dd) and held:   # 포트폴리오 이익 보호: 고점 대비 dd 하락 시 전량 현금, cool일 쉼
            v *= 1 - cost; held = set(); cash_until = t + 1 + cool; peak = v; log.append(t)
    pk, md = 1, 0
    for x in eq: pk = max(pk, x); md = min(md, x / pk - 1)
    top_i = max(range(len(eq)), key=lambda i: eq[i])
    return eq, md, top_i, len(log)
H = n // 2 - 50
print(f'일봉 {n}일 중 시험 구간 {n-51}일(앞 50일은 기준 계산용). 전반=처음 {H}일, 후반=나머지')
for (win, top, reg, cap, label) in [(21, 1, 'breadth', None, '21일·상위1·breadth·과열상한 없음(전반 1위 설정)'),
                                    (7, 1, 'none', 0.15, '7일·상위1·필터 없음·과열 15%'),
                                    (21, 1, 'none', 0.15, '21일·상위1·필터 없음·과열 15%')]:
    print(f'\n■ {label}')
    eq, md, ti, _ = path(win, top, reg, cap)
    print(f'  보호 장치 없음(전체 연속 운용): 전반 끝 {eq[H-1]-1:+.1%} → 최고점 {eq[ti]-1:+.1%}({ti}일째) → 마지막 {eq[-1]-1:+.1%}, 최대낙폭 {md:.0%}')
    print(f'    (후반만 새로 시작하면 +7%라고 했던 것은 전반 성과를 버리고 1.0에서 다시 시작한 별개 계산이다)')
    for dd in (0.05, 0.08, 0.10, 0.15):
        for cool in (3, 7):
            eq2, md2, ti2, nlock = path(win, top, reg, cap, dd=dd, cool=cool)
            print(f'  이익 보호: 고점 대비 −{dd:.0%} 하락 시 전량 현금 {cool}일 휴식 → 전반 끝 {eq2[H-1]-1:+.1%}, 마지막 {eq2[-1]-1:+.1%}, 최대낙폭 {md2:.0%}, 발동 {nlock}회')
