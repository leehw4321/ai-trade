import sys, time, json, urllib.request, datetime as dt, statistics as S
sys.path.insert(0, r'c:/Users/User/Desktop/ai-trade/v5')
import upbit, strategy as st

P = st.Params(); M = 5; TOP = 3; KST = dt.timezone(dt.timedelta(hours=9)); FEE = 0.0005
CAPITAL = 1_887_743
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
        rows += r; to = r[-1]['candle_date_time_utc'] + 'Z'
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
grid = sorted(ser['KRW-BTC'])
data = {}
for m in ser:
    last = None; arr = []
    for t in grid:
        if t in ser[m]: last = ser[m][t]; arr.append(last)
        elif last: arr.append((last[3], last[3], last[3], last[3]))
        else: arr.append(None)
    data[m] = arr
N = len(grid)
SMIN = 289   # L=288 변형까지 같은 창을 쓰기 위한 최소 시작 위치
coins = [m for m in data if all(x is not None for x in data[m][SMIN - 289:])]
closes = {m: [x[3] for x in data[m]] for m in coins}
lows = {m: [x[2] for x in data[m]] for m in coins}


def ema(v, n):
    k = 2 / (n + 1); out = [v[0]]
    for x in v[1:]: out.append(x * k + out[-1] * (1 - k))
    return out


EMA20 = {m: ema(closes[m], 20) for m in coins}
ATR15 = {}
for m in coins:
    arr = []
    for i in range(N):
        if i < 15: arr.append(None); continue
        b = [{'h': data[m][j][1], 'l': data[m][j][2], 'c': data[m][j][3]} for j in range(i - 40 if i >= 40 else 0, i + 1)]
        arr.append(st.wilder_atr(b))
    ATR15[m] = arr
EW = [S.mean(closes[m][i] / closes[m][0] for m in coins) for i in range(N)]


def atr4h(m, i):
    bk = {}
    for j in range(max(0, i - 400), i + 1):
        t = grid[j]; key = (t.date(), t.hour // 4)
        o, h, l, c = data[m][j]
        if key not in bk: bk[key] = [o, h, l, c]
        else: bk[key][1] = max(bk[key][1], h); bk[key][2] = min(bk[key][2], l); bk[key][3] = c
    keys = sorted(bk)[:-1]
    return st.wilder_atr([{'h': bk[k][1], 'l': bk[k][2], 'c': bk[k][3]} for k in keys])


def cost_rate(m): return P.cost_major if m in st.MAJORS else P.cost_other


RKCACHE = {}


def ranks(i, L):
    key = (i, L)
    if key not in RKCACHE:
        rows = sorted(((closes[m][i] / closes[m][i - L] - 1, m) for m in coins), reverse=True)
        RKCACHE[key] = {m: (k + 1, rs) for k, (rs, m) in enumerate(rows)}
    return RKCACHE[key]


def run(S0, E0, L=72, protect=True, reentry=True, regime=False, confirm=False):
    cash, pos, last_sell, trades = float(CAPITAL), {}, {}, 0

    def mark(i): return cash + sum(p['qty'] * closes[m][i] for m, p in pos.items())

    def sell(m, i, px, qty=None, reason=''):
        nonlocal cash, trades
        p = pos[m]; q = p['qty'] if qty is None else qty; c = cost_rate(m)
        proceeds = q * px * (1 - c) * (1 - FEE); cash += proceeds
        basis = p['spend'] * q / p['qty']
        p['spend'] -= basis; p['qty'] -= q; trades += 1
        if p['qty'] <= 1e-12:
            last_sell[m] = dict(i=i, price=px, profit=proceeds > basis)
            del pos[m]

    for i in range(S0, E0):
        for m in list(pos):
            p = pos[m]; o, h, l, c_ = data[m][i]
            if l <= p['stop']:
                sell(m, i, min(p['stop'], o)); continue
            if protect:
                R = 3 * p['atr']; E = p['entry']
                p['peak'] = max(p['peak'], c_)
                g = (p['peak'] - E) / R if R > 0 else 0
                if g >= 1.0: p['stop'] = max(p['stop'], E * (1 + 2 * FEE + cost_rate(m)))
                if g >= 2.0: p['stop'] = max(p['stop'], p['peak'] - 2 * p['atr'])
                if g >= 2.0 and p['peak'] > E and (p['peak'] - c_) / (p['peak'] - E) >= 0.5:
                    sell(m, i, c_); continue
                if c_ <= p['stop'] and g >= 1.0:
                    sell(m, i, c_); continue
                if not p['part'] and (c_ - E) / R >= 2.0 and p['qty'] * c_ * 0.6667 >= 5000:
                    sell(m, i, c_, qty=p['qty'] / 3); p['part'] = True
        if (i - S0) % 4 == 0:
            r = ranks(i, L)
            for m in list(pos):
                k, rs = r[m]
                if k > M or rs < 0: sell(m, i, closes[m][i])
            eq = mark(i); invested = sum(p['qty'] * closes[m][i] for m, p in pos.items())
            c_avail = cash * 0.98
            ok_regime = (not regime) or EW[i] > S.mean(EW[i - 95:i + 1])
            picks = []
            for m, (k, rs) in sorted(r.items(), key=lambda x: x[1][0])[:TOP]:
                if m in pos or not (0 < rs < 0.15): continue
                if regime and not ok_regime: continue
                if confirm and not (closes[m][i] / closes[m][i - 4] - 1 >= 0 and closes[m][i] > EMA20[m][i]): continue
                if reentry and m in last_sell:
                    ls = last_sell[m]; hrs = (i - ls['i']) / 4
                    if hrs < P.reentry_window_hours:
                        if ls['profit']:
                            a = ATR15[m][i] or 0
                            if hrs < P.reentry_profit_hours or closes[m][i] > ls['price'] * (1 - P.reentry_dip_atr * a / closes[m][i]): continue
                        elif hrs < P.reentry_loss_hours: continue
                picks.append(m)
            total_room = max(0.0, 0.5 * eq - invested)
            for j, m in enumerate(picks):
                amt = int(min(c_avail / (len(picks) - j), 0.25 * eq, total_room) // 1000 * 1000)
                if amt < 5000: continue
                c = cost_rate(m); px = closes[m][i] * (1 + c)
                qty = amt / (px * (1 + FEE)); cash -= amt; c_avail -= amt; total_room -= amt
                a4 = atr4h(m, i); a15 = ATR15[m][i] or px * 0.01
                stop = px - 3 * a4 if a4 else px * 0.92
                stop = max(stop, px * 0.92)
                pos[m] = dict(qty=qty, spend=amt, entry=px, stop=stop, peak=px, atr=a15, part=False)
    for m in list(pos): sell(m, E0 - 1, closes[m][E0 - 1])
    return cash / CAPITAL - 1, trades


variants = [
    ('A0 이전(이익보호·재진입 규칙 없음)', dict(protect=False, reentry=False)),
    ('BASE 현재 조건(이익보호+재진입 규칙)', dict()),
    ('H1 장세 필터: 시장 균등지수가 24시간 평균 위일 때만 신규 매수', dict(regime=True)),
    ('H2 판단 기간 3일: 72시간 상승률 순위(L=288봉)', dict(L=288)),
    ('H3 단기 추세 확인: 직전 1시간 상승 ≥0 & 종가>15분 EMA20일 때만 매수', dict(confirm=True)),
]
WIN = 200
starts = list(range(SMIN, N - WIN, 16))
res = {n: [] for n, _ in variants}
for S0 in starts:
    for n, kw in variants:
        res[n].append(run(S0, S0 + WIN, **kw))
ew_hold = [S.mean(closes[m][S0 + WIN - 1] / closes[m][S0] - 1 for m in coins) for S0 in starts]
print(f'자료 {(N - SMIN) / 4:.0f}시간 · 50시간 창 {len(starts)}개(4시간 간격, 겹침) · 시작 자본 {CAPITAL:,}원 · 유니버스 {len(coins)}종목')
print(f'첫 창 {grid[starts[0]].astimezone(KST):%m-%d %H:%M} ~ 마지막 창 시작 {grid[starts[-1]].astimezone(KST):%m-%d %H:%M} KST')
base = [r[0] for r in res['BASE 현재 조건(이익보호+재진입 규칙)']]
print('\n방식                                                          평균     중앙값    최악     최고    이익창   BASE보다 나은 창  평균거래수')
for n, _ in variants:
    v = [r[0] for r in res[n]]; tr = S.mean(r[1] for r in res[n])
    better = sum(a > b for a, b in zip(v, base))
    print(f'{n[:56]:56s} {S.mean(v):+7.2%} {S.median(v):+8.2%} {min(v):+8.2%} {max(v):+8.2%}  {sum(x > 0 for x in v):>2d}/{len(v)}   {better:>2d}/{len(v)}        {tr:5.1f}')
print(f"{'30종목 그냥 보유(균등)':56s} {S.mean(ew_hold):+7.2%} {S.median(ew_hold):+8.2%} {min(ew_hold):+8.2%} {max(ew_hold):+8.2%}  {sum(x > 0 for x in ew_hold):>2d}/{len(ew_hold)}")
last = len(starts) - 1
print(f"\n[가장 최근 50시간 창 ({grid[starts[last]].astimezone(KST):%m-%d %H:%M} 시작)]  ", ' · '.join(f"{n.split()[0]} {res[n][last][0]:+.1%}" for n, _ in variants), f"· 균등보유 {ew_hold[last]:+.1%}")
# 시장 국면별(균등 보유 수익 기준 상승/횡보·하락)
up = [i for i, e in enumerate(ew_hold) if e > 0.02]; dn = [i for i, e in enumerate(ew_hold) if e <= 0.02]
print(f'\n[국면별 평균] 상승 창 {len(up)}개(균등 보유 +2% 초과) / 그 외 {len(dn)}개')
for n, _ in variants:
    v = [r[0] for r in res[n]]
    print(f"  {n[:30]:30s} 상승 창 {S.mean([v[i] for i in up]):+7.2%} · 그 외 {S.mean([v[i] for i in dn]):+7.2%}")
