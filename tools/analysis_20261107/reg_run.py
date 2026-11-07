from reg_bt import *
import time, pickle
SPLIT = pd.Timestamp('2026-03-01')
minute = tsi.minute.values
res = {}
def report(name, trades):
    t = summarize(name, trades)
    res[name] = t
    print(f'\n### {name}  (전체 {len(t)}건)')
    for k in ('상승', '횡보', '하락'):
        for per, sel in (('개발(~2026-02)', t['time'] < SPLIT), ('검증(2026-03~)', t['time'] >= SPLIT)):
            print('  ', line(f'{k} {per}', t[(t['reg'] == k) & sel]))

t0 = time.time()
# --- M0 모멘텀(현재 봇과 비슷한 성격): 매시 18시간 상승률 상위 3, 이익보호식 청산
R18 = F['r18']
def cand_m0(i):
    if minute[i] != 45: return []
    row = R18[i]; ok = np.where(~np.isnan(row) & (row > 0))[0]
    if len(ok) == 0: return []
    top = ok[np.argsort(-row[ok])][:3]
    return [(-row[c], c) for c in top]
report('M0 모멘텀(18h 상위3, 손절4.5% 이익보호3%/30%, 24h)', run('M0', cand_m0, lambda i, ci, p: dict(stop=0.045, guard=(0.03, 0.30), tmax=96)))
print('  경과', round(time.time() - t0), '초')

# --- MR 평균회귀: 24시간 평균 대비 z <= zth 에서 매수, 평균 회복에서 익절
Zm = F['z']; SMA = F['sma']
def make_mr(zth, stopp, need=0.015):
    mask = (Zm <= zth) & ~np.isnan(Zm)
    def cand(i):
        ix = np.where(mask[i])[0]
        out = []
        for c in ix:
            p = C[i, c]
            if not np.isnan(SMA[i, c]) and SMA[i, c] / p - 1 >= need: out.append((Zm[i, c], c))
        return out
    return cand, (lambda i, ci, p: dict(tp=SMA[i, ci], stop=stopp, tmax=48))
for zth in (-2.0, -2.5, -3.0):
    for sp in (0.03, 0.05):
        cf, ef = make_mr(zth, sp)
        report(f'MR z<={zth} 평균회복익절 손절{int(sp*100)}% 12h', run('MR', cf, ef))
print('  경과', round(time.time() - t0), '초')

# --- RG 박스권: 48시간 폭 5~20%, 하단 15% 이하에서 매수, 중간선 익절, 박스 하단 -1% 손절
P = F['rg_pos']; W = F['rg_w']; LO = F['rg_lo']; HI = F['rg_hi']
mask = (P <= 0.15) & (W >= 0.05) & (W <= 0.20) & ~np.isnan(P)
def cand_rg(i):
    out = []
    for c in np.where(mask[i])[0]:
        mid = LO[i, c] + 0.5 * (HI[i, c] - LO[i, c])
        if mid / C[i, c] - 1 >= 0.015: out.append((P[i, c], c))
    return out
def ex_rg(i, ci, p):
    mid = LO[i, ci] + 0.5 * (HI[i, ci] - LO[i, ci])
    return dict(tp=mid, stop_abs=LO[i, ci] * 0.99 if LO[i, ci] * 0.99 < p else None, stop=0.05, tmax=96)
report('RG 박스하단매수 중간선익절 박스하단-1%손절 24h', run('RG', cand_rg, ex_rg))
print('  경과', round(time.time() - t0), '초')
pickle.dump({k: v for k, v in res.items()}, open(S + r'\reg_res.pkl', 'wb'))
