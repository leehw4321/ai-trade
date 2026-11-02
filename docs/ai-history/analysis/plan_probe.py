import sys, time, json, urllib.request, statistics as S
sys.path.insert(0, r'c:/Users/User/Desktop/ai-trade/v5')
import upbit, strategy as st
P = st.Params(); L = 72; M = 5
mk = [x['market'] for x in json.loads(urllib.request.urlopen('https://api.upbit.com/v1/market/all').read()) if x['market'].startswith('KRW-')]
tk = upbit.tickers(mk)
pool = sorted((m for m in tk if m not in P.exclude), key=lambda m: -tk[m]['acc_trade_price_24h'])[:30]
bars = {}
for m in pool:
    try: bars[m] = upbit.completed_candles(m, 15, 200)
    except Exception: pass
    time.sleep(0.12)
n = min(len(b) for b in bars.values()); RT = 0.005
ranks = {}
for i in range(L + 1, n):
    rows = sorted(((st.relative_strength(bars[m][:i + 1], L), m) for m in bars if st.relative_strength(bars[m][:i + 1], L) is not None), reverse=True)
    ranks[i] = {m: (k + 1, rs) for k, (rs, m) in enumerate(rows)}
def sim(m, i0, cfg):
    b = bars[m]; A = st.wilder_atr(b[:i0 + 1]); E = b[i0]['c']; R = 3 * A
    W, entry, peak, stop = 1.0, E, E, E - min(3 * A, 0.08 * E)
    realized = 0.0; cost = RT * 1.0; part_done = False; state = 'hold'; reent = 0
    ex_px = pk = low = None; leg_tight = False
    for i in range(i0 + 1, n):
        c = b[i]['c']; k, rs = ranks.get(i, {}).get(m, (99, -1))
        if state == 'hold':
            peak = max(peak, c); g = (peak - entry) / R
            if g >= 0.5: stop = max(stop, entry * 1.003)
            mult = cfg['m2'] if (g >= 2.0 and cfg.get('tight2')) else cfg['m']
            if g >= cfg['start']: stop = max(stop, peak - mult * A)
            if cfg.get('part') and g >= 2.0 and not part_done and W > 0.5:
                sell = W / 3; realized += sell * (c / entry - 1); W -= sell; part_done = True; cost += RT * 0  # 비용은 레그 단위로 이미 반영
            give = g >= cfg['start'] and peak > entry and (peak - c) / (peak - entry) >= cfg['give']
            rot = (k > M or rs < 0) and g < cfg['start']  # 추적 모드가 켜지면 순위 이탈로 팔지 않고 추적선에 맡긴다
            if c <= stop or give or rot:
                realized += W * (c / entry - 1); profit = c > entry * 1.003 and g >= cfg['start']
                ex_px, pk, low = c, peak, c; W_prev = W; W = 0.0
                if cfg.get('rebuy') and profit and reent < 1: state = 'watch'
                else: state = 'done'; break
        elif state == 'watch':
            low = min(low, b[i]['l']); ok = k <= M and 0 < rs < 0.15
            kind = cfg['rebuy']
            hit = (kind == 'dip' and low <= ex_px - cfg['dip'] * A and c >= low + cfg['bounce'] * A and c < pk and ok) or \
                  (kind == 'break' and c >= ex_px + cfg['bounce'] * A and ok)
            if hit:
                W = cfg.get('size', 0.5); entry = c; peak = c; stop = c - cfg['rstop'] * A; cost += RT * W; state = 'hold'; reent += 1; part_done = True
            elif i - 0 > 0 and (k > M or rs < 0): state = 'done'; break
    if W > 0: realized += W * (b[n - 1]['c'] / entry - 1)
    return realized - cost, reent
sig = [(m, i) for i in range(L + 1, n - 8) for m, (k, rs) in sorted(ranks[i].items(), key=lambda x: x[1][0])[:3] if 0 < rs < 0.15]
base = {'m': 2.0, 'start': 1.0, 'give': 0.5}
variants = [
 ('V0 기존: 순환·손절만(비교용)', {'m': 99, 'start': 99, 'give': 9}),
 ('V1 추적만: 1R부터 고점−2ATR·반납50%', base),
 ('V2 = V1 + 2R에서 1/3 확정, 2R 이후 고점−1.5ATR', {**base, 'part': True, 'tight2': True, 'm2': 1.5}),
 ('V3 = V1 + 되돌림 재진입(1ATR 더 빠지고 1ATR 반등, 절반 크기, 손절 −1.5ATR)', {**base, 'rebuy': 'dip', 'dip': 1.0, 'bounce': 1.0, 'rstop': 1.5, 'size': 0.5}),
 ('V4 = V1 + 되돌림 재진입(1.5ATR 더 빠지고 1ATR 반등)', {**base, 'rebuy': 'dip', 'dip': 1.5, 'bounce': 1.0, 'rstop': 1.5, 'size': 0.5}),
 ('V5 = V1 + 추세 재개 재진입(매도가+1ATR 회복)', {**base, 'rebuy': 'break', 'bounce': 1.0, 'rstop': 1.5, 'size': 0.5}),
 ('V6 = V2 + V3 (확정 + 재진입)', {**base, 'part': True, 'tight2': True, 'm2': 1.5, 'rebuy': 'dip', 'dip': 1.0, 'bounce': 1.0, 'rstop': 1.5, 'size': 0.5}),
 ('V7 합의안: 2R부터 고점−2ATR', {'m': 2.0, 'start': 2.0, 'give': 0.5}),
]
print(f'신호 {len(sig)}개(상위3·상승 0~15%), 약 {(n-L)*0.25:.0f}시간, 레그당 왕복 비용 0.5%')
for name, cfg in variants:
    res = [sim(m, i, cfg) for m, i in sig]; r = [x[0] for x in res]
    print(f'{name:70s} 평균 {S.mean(r)*100:+.2f}% 중앙 {S.median(r)*100:+.2f}% 승률 {100*sum(x>0 for x in r)/len(r):.0f}% 최악 {min(r)*100:+.1f}% 재진입 {sum(x[1] for x in res)}회')
