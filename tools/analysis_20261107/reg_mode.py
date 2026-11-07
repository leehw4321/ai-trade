import reg_bt as rb
from reg_bt import *
import json, datetime as dt, statistics as st
SPLIT = pd.Timestamp('2026-03-01')
minute = tsi.minute.values
Zm = F['z']; SMA = F['sma']; R18 = F['r18']
def cand_m0(i):
    if minute[i] != 45: return []
    row = R18[i]; ok = np.where(~np.isnan(row) & (row > 0))[0]
    if len(ok) == 0: return []
    return [(-row[c], c) for c in ok[np.argsort(-row[ok])][:3]]
ex_m0 = lambda i, ci, p: dict(stop=0.045, guard=(0.03, 0.30), tmax=96)
mask = (Zm <= -2.5) & ~np.isnan(Zm)
def cand_mr(i):
    return [(Zm[i, c], c) for c in np.where(mask[i])[0] if not np.isnan(SMA[i, c]) and SMA[i, c] / C[i, c] - 1 >= 0.015]
ex_mr = lambda i, ci, p: dict(tp=SMA[i, ci], stop=0.05, tmax=48)

POL = {  # (상승, 횡보, 하락) 투입 비중
    '항상 100%': (1, 1, 1),
    '횡보·하락 50%': (1, .5, .5),
    '횡보·하락 25%': (1, .25, .25),
    '횡보·하락 0% (상승에서만)': (1, 0, 0),
    '하락만 0%': (1, 1, 0),
    '하락만 50%': (1, 1, .5),
    '횡보만 0%': (1, 0, 1),
}
def evalpol(name, trades):
    t = summarize(name, trades); t['exit'] = tsi[np.minimum(t['j'].values + 1, N - 1)]
    t = t.sort_values('exit')
    print(f'\n### {name}: 전체 {len(t)}건 (건당 자본의 1/5 투입 가정, 합계는 자본 대비 %, 단순 합산)')
    print(f"{'정책':26s} {'전체':>8s} {'개발':>8s} {'검증':>8s} {'최대낙폭':>8s} {'거래수':>6s}")
    for pn, (a, b, c) in POL.items():
        m = t['reg'].map({'상승': a, '횡보': b, '하락': c}).values
        pnl = t['r'].values * m / 5 * 100
        cum = np.cumsum(pnl); mdd = (np.maximum.accumulate(cum) - cum).max()
        dv = pnl[(t['exit'] < SPLIT).values].sum(); ts = pnl[(t['exit'] >= SPLIT).values].sum()
        print(f'{pn:26s} {pnl.sum():+7.1f}% {dv:+7.1f}% {ts:+7.1f}% {mdd:7.1f}% {int((m>0).sum()):6d}')
    return t

print('※ 2년 간이 시험(비용 반영). 투입 비중 0이면 그 장세에서 신규 매수를 하지 않음.')
tm0 = evalpol('M0 모멘텀 프록시', rb.run('M0', cand_m0, ex_m0))
tmr = evalpol('MR 평균회귀(z<=-2.5, 손절5%)', rb.run('MR', cand_mr, ex_mr))

# ---- 봇 실제 88건: 3일(72시간) 시장 수익률 장세 ----
print('\n=== 봇 실제 매수 88건 (현재 적용 규칙으로 재현) 장세별 ===')
from sim import *
from r2 import r2sim
Mk = json.load(open(S + r'\mkt.json'))
allh = sorted(set(h for v in Mk.values() for h in v)); ix2 = {}; l2 = 100.0
for a, b in zip(allh, allh[1:]):
    rs = [Mk[m][b] / Mk[m][a] - 1 for m in Mk if m != 'KRW-BTC' and a in Mk[m] and b in Mk[m]]
    if len(rs) < 20: continue
    l2 *= 1 + st.mean(rs); ix2[b] = l2
hs = sorted(ix2); pos_ = {h: i for i, h in enumerate(hs)}
rowsb = []
for e in ents:
    t = dt.datetime.fromisoformat(e['t']).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    k = t.strftime('%Y-%m-%dT%H:%M:%S')
    if k not in pos_ or pos_[k] < 72: continue
    r3 = ix2[k] / ix2[hs[pos_[k] - 72]] - 1
    g = '상승' if r3 > 0.03 else ('하락' if r3 < -0.03 else '횡보')
    rowsb.append((e['t'][:16], e['market'][4:], g, r3, r2sim(e, T=0.05)))
bd = pd.DataFrame(rowsb, columns=['t', 'm', 'reg', 'r3', 'r'])
for g in ('상승', '횡보', '하락'):
    x = bd[bd['reg'] == g]
    if len(x): print(f'{g}: {len(x)}건 승률 {(x.r>0).mean()*100:.0f}% 평균 {x.r.mean()*100:+.2f}% 합 {x.r.sum()*80000:+.0f}원')
print('전체', len(bd), '건 합', round(bd.r.sum() * 80000), '원')
for pn, (a, b, c) in POL.items():
    m = bd['reg'].map({'상승': a, '횡보': b, '하락': c}).values
    print(f'  {pn:26s} 합 {np.sum(bd.r.values*m)*80000:+7.0f}원 (매수 {int((m>0).sum())}건)')
print(bd.groupby(bd.t.str[:10]).apply(lambda x: f"{len(x)}건 " + ' '.join(f"{g}{(x.reg==g).sum()}" for g in ('상승','횡보','하락'))).to_string())
