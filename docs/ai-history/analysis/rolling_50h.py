import sys, time, json, urllib.request, datetime as dt, statistics as S
sys.path.insert(0, r'c:/Users/User/Desktop/ai-trade/v5')
import upbit, strategy as st
P = st.Params(); L = 72; M = 5; TOP = 3; KST = dt.timezone(dt.timedelta(hours=9)); FEE = 0.0005
CAPITAL = 1_887_743  # 현재 총 평가금액
mk = [x['market'] for x in json.loads(urllib.request.urlopen('https://api.upbit.com/v1/market/all').read()) if x['market'].startswith('KRW-')]
tk = upbit.tickers(mk)
pool = sorted((m for m in tk if m not in P.exclude), key=lambda m: -tk[m]['acc_trade_price_24h'])[:30]
if 'KRW-BTC' not in pool: pool.append('KRW-BTC')
def fetch(m):
    rows = []; to = None
    for _ in range(6):
        p = {'market': m, 'count': 200}
        if to: p['to'] = to
        r = upbit._get('/v1/candles/minutes/15', p)
        rows += r
        to = r[-1]['candle_date_time_utc'] + 'Z'
        time.sleep(0.12)
    return rows
now = dt.datetime.now(dt.timezone.utc)
raw = {}
for m in pool:
    try: raw[m] = fetch(m)
    except Exception as e: print('skip', m, e)
def parse(rows):
    out = {}
    for r in rows:
        t = dt.datetime.fromisoformat(r['candle_date_time_utc']).replace(tzinfo=dt.timezone.utc)
        if t + dt.timedelta(minutes=15) <= now: out[t] = (r['opening_price'], r['high_price'], r['low_price'], r['trade_price'])
    return out
ser = {m: parse(r) for m, r in raw.items()}
grid = sorted(set.intersection(*[set(ser[m]) for m in ('KRW-BTC',)]))
# 공통 격자: BTC 시각 기준, 다른 종목은 결측 시 직전 종가로 채움
data = {}
for m in ser:
    last = None; arr = []
    for t in grid:
        if t in ser[m]: last = ser[m][t]; arr.append(last)
        elif last: arr.append((last[3], last[3], last[3], last[3]))
        else: arr.append(None)
    data[m] = arr
N = len(grid)
S_MIN = L + 1
coins = [m for m in data if all(x is not None for x in data[m][S_MIN - L:])]
closes = {m: [x[3] for x in data[m]] for m in coins}
def atr4h(m, i):
    # 15분 격자를 UTC 4시간 구간으로 묶어 Wilder ATR14. 부족하면 None
    bk = {}
    for j in range(max(0, i - 400), i + 1):
        t = grid[j]; key = (t.date(), t.hour // 4)
        o, h, l, c = data[m][j]
        if key not in bk: bk[key] = [o, h, l, c]
        else: bk[key][1] = max(bk[key][1], h); bk[key][2] = min(bk[key][2], l); bk[key][3] = c
    keys = sorted(bk)[:-1]  # 마지막(진행 중) 구간 제외
    b = [{'h': bk[k][1], 'l': bk[k][2], 'c': bk[k][3]} for k in keys]
    return st.wilder_atr(b)
def cost_rate(m): return P.cost_major if m in st.MAJORS else P.cost_other
def ranks(i):
    rows = sorted(((closes[m][i] / closes[m][i - L] - 1, m) for m in coins), reverse=True)
    return {m: (k + 1, rs) for k, (rs, m) in enumerate(rows)}
RK = {i: ranks(i) for i in range(S_MIN, N)}
def run(name, S, E, cap=0.15, coin_cap=0.25, total_cap=0.5, stop_cap=0.08, step=4, trail=None):
    cash, pos, trades, eqs = float(CAPITAL), {}, [], []
    def mark(i): return cash + sum(p['qty'] * closes[m][i] for m, p in pos.items())
    def sell(m, i, px, why):
        nonlocal cash
        p = pos.pop(m); c = cost_rate(m)
        proceeds = p['qty'] * px * (1 - c) * (1 - FEE); cash += proceeds
        trades.append(dict(t=grid[i].astimezone(KST), m=m, why=why, pnl=proceeds / p['spend'] - 1, spend=p['spend']))
    for i in range(S, E):
        # 손절(15분봉 저가가 손절가 이하): 30초 점검을 봉 단위로 근사
        for m in list(pos):
            o, h, l, c = data[m][i]
            if l <= pos[m]['stop']: sell(m, i, min(pos[m]['stop'], o), '손절')
        if (i - S) % step == 0:
            r = RK[i]
            sold = 0.0
            for m in list(pos):
                k, rs = r[m]
                if k > M or rs < 0:
                    before = cash; sell(m, i, closes[m][i], f'순위 {k}위 이탈' if k > M else '상대강도 음수'); sold += cash - before
            eq = mark(i); invested = sum(p['qty'] * closes[m][i] for m, p in pos.items())
            c_avail = cash * 0.98
            picks = [m for m, (k, rs) in sorted(r.items(), key=lambda x: x[1][0])[:TOP] if rs > 0 and (cap is None or rs < cap) and m not in pos]
            total_room = max(0.0, total_cap * eq - invested)
            for j, m in enumerate(picks):
                coin_room = max(0.0, coin_cap * eq)
                amt = int(min(c_avail / (len(picks) - j), coin_room, total_room) // 1000 * 1000)
                if amt < 5000: continue
                c = cost_rate(m); px = closes[m][i] * (1 + c)
                qty = amt / (px * (1 + FEE)); cash -= amt; c_avail -= amt; total_room -= amt
                a4 = atr4h(m, i); entry = px
                stop = entry - 3 * a4 if a4 else entry * (1 - 0.08)
                if stop_cap: stop = max(stop, entry * (1 - stop_cap))
                pos[m] = dict(qty=qty, spend=amt, entry=entry, stop=stop)
        eqs.append(mark(i))
    for m in list(pos): sell(m, E - 1, closes[m][E - 1], '구간 끝 평가')
    final = cash
    peak, mdd = eqs[0], 0.0
    for v in eqs: peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    closed = [t for t in trades if t['why'] != '구간 끝 평가']
    wins = sum(t['pnl'] > 0 for t in trades)
    avg_inv = S_ = None
    return dict(name=name, final=final, ret=final / CAPITAL - 1, mdd=mdd, n=len(trades), wins=wins, trades=trades, eqs=eqs)

WIN = 200
starts = list(range(S_MIN, N - WIN, 16))
rows = []
for S0 in starts:
    E0 = S0 + WIN
    a = run('A', S0, E0); b = run('B', S0, E0, coin_cap=1.0, total_cap=1.0); c = run('C', S0, E0, cap=None, stop_cap=None)
    ew = sum(closes[m][E0 - 1] / closes[m][S0] - 1 for m in coins) / len(coins)
    btc = closes['KRW-BTC'][E0 - 1] / closes['KRW-BTC'][S0] - 1
    rows.append((grid[S0].astimezone(KST), a['ret'], b['ret'], c['ret'], ew, btc, a['n']))
print(f"자료 {(N - S_MIN) / 4:.0f}시간(순위 계산 선행 제외), 50시간 창을 4시간 간격으로 {len(rows)}개 (창끼리 겹침), 시작 자본 {CAPITAL:,}원")
print('\n시작 시각          A현재모델   B전액투자   C이전모델   30종목균등  BTC보유   거래수')
for t, a, b, c, ew, btc, n in rows:
    print(f"{t:%m-%d %H:%M}   {a:+8.2%}   {b:+8.2%}   {c:+8.2%}   {ew:+8.2%}  {btc:+7.2%}   {n}")
import statistics as S2
def stat(i, name):
    v = [r[i] for r in rows]
    print(f"{name}: 평균 {S2.mean(v):+.2%} · 중앙값 {S2.median(v):+.2%} · 최악 {min(v):+.2%} · 최고 {max(v):+.2%} · 이익 구간 {sum(x > 0 for x in v)}/{len(v)}")
print()
stat(1, 'A 현재 모델 '); stat(2, 'B 전액 투자  '); stat(3, 'C 이전 모델  '); stat(4, '30종목 균등 보유'); stat(5, 'BTC 보유     ')
d = [r[1] - r[4] for r in rows]
print(f"A − 균등보유(시장 대비): 평균 {S2.mean(d):+.2%} · 이긴 구간 {sum(x > 0 for x in d)}/{len(d)}")
