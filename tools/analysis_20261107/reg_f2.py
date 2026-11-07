import reg_bt as rb
from reg_bt import *
SPLIT = pd.Timestamp('2026-03-01')
minute = tsi.minute.values
R6 = trailing(24)    # 6시간 시장 수익률 (봇 장세 필터와 같은 길이)
R18 = F['r18']
def make(filt):
    def cand(i):
        if minute[i] != 45: return []
        if not filt(i): return []
        row = R18[i]; ok = np.where(~np.isnan(row) & (row > 0))[0]
        if len(ok) == 0: return []
        return [(-row[c], c) for c in ok[np.argsort(-row[ok])][:3]]
    return cand
ex = lambda i, ci, p: dict(stop=0.045, guard=(0.03, 0.30), tmax=96)
def show(name, filt):
    t = summarize(name, rb.run(name, make(filt), ex))
    print(f'\n### {name}: 전체 {len(t)}건 평균 {t.r.mean()*100:+.2f}% 승률 {(t.r>0).mean()*100:.0f}%')
    for k in ('상승', '횡보', '하락'):
        for per, sel in (('개발', t['time'] < SPLIT), ('검증', t['time'] >= SPLIT)):
            x = t[(t['reg'] == k) & sel]
            if len(x): print(f'   {k} {per}: n={len(x):4d} 평균 {x.r.mean()*100:+.2f}% 승률 {(x.r>0).mean()*100:.0f}%')
show('M0 필터 없음', lambda i: True)
show('M0 + 시장 6h 상승(>0) 필터 [현재 봇과 같은 종류]', lambda i: not np.isnan(R6[i]) and R6[i] > 0)
show('M0 + 시장 6h 하락(<0)일 때만(반대)', lambda i: not np.isnan(R6[i]) and R6[i] < 0)
show('M0 + 시장 6h 상승 + 지난 24h -2% 이하(급락 뒤 반등)', lambda i: not np.isnan(R6[i]) and R6[i] > 0 and not np.isnan(R1[i]) and R1[i] <= -0.02)
show('M0 + 시장 6h 상승 + 지난 24h -2% 초과(이미 오른 시장)', lambda i: not np.isnan(R6[i]) and R6[i] > 0 and not np.isnan(R1[i]) and R1[i] > -0.02)
