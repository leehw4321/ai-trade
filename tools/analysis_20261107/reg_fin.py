import reg_bt as rb
from reg_bt import *
SPLIT = pd.Timestamp('2026-03-01')
minute = tsi.minute.values
# ---------- 1) 급락 반등: 사건 단위로 묶기 ----------
print('=== 1) 시장 24시간 -6% 이하 급락 사건별 이후 수익 (사건 = 24시간 안 연속 신호를 하나로) ===')
hour = np.where(minute == 45)[0]
ev = []; last = -10**9
for i in hour:
    if i < 300 or i > N - 300: continue
    if not np.isnan(R1[i]) and R1[i] <= -0.06 and i - last > 96:
        ev.append(i)
    if not np.isnan(R1[i]) and R1[i] <= -0.06: last = i
lv = idx.values
rows = []
for i in ev:
    f = lambda h: lv[i + h] / lv[i] - 1
    # 3일 장세와 무관하게, 진입은 다음 봉 시가, 비용 0.4% 왕복 가정
    rows.append((tsi[i], R1[i] * 100, f(16) * 100, f(96) * 100, f(288) * 100, reg[i]))
t = pd.DataFrame(rows, columns=['시각', '지난24h', '이후4h', '이후24h', '이후72h', '장세'])
print(t.round(2).to_string(index=False))
print('사건 수', len(t), '| 이후24h 평균 %.2f%% 중앙 %.2f%% 양수 %d/%d | 이후72h 평균 %.2f%% 양수 %d/%d' % (t['이후24h'].mean(), t['이후24h'].median(), (t['이후24h'] > 0).sum(), len(t), t['이후72h'].mean(), (t['이후72h'] > 0).sum(), len(t)))
for per, m in (('개발(~2026-02)', t['시각'] < SPLIT), ('검증(2026-03~)', t['시각'] >= SPLIT)):
    x = t[m]
    if len(x): print(f'  {per}: 사건 {len(x)} 이후24h 평균 {x["이후24h"].mean():+.2f}% 양수 {(x["이후24h"]>0).sum()}/{len(x)} | 이후72h 평균 {x["이후72h"].mean():+.2f}%')

# ---------- 2) 비용 민감도: 같은 전략을 비용 0 / 메이커(지정가 왕복 0.1%) / 현실 ----------
Zm = F['z']; SMA = F['sma']; R18 = F['r18']; P = F['rg_pos']; W = F['rg_w']; LO = F['rg_lo']; HI = F['rg_hi']
def cand_m0(i):
    if minute[i] != 45: return []
    row = R18[i]; ok = np.where(~np.isnan(row) & (row > 0))[0]
    if len(ok) == 0: return []
    return [(-row[c], c) for c in ok[np.argsort(-row[ok])][:3]]
ex_m0 = lambda i, ci, p: dict(stop=0.045, guard=(0.03, 0.30), tmax=96)
mask = (Zm <= -2.5) & ~np.isnan(Zm)
def cand_mr(i):
    out = []
    for c in np.where(mask[i])[0]:
        if not np.isnan(SMA[i, c]) and SMA[i, c] / C[i, c] - 1 >= 0.015: out.append((Zm[i, c], c))
    return out
ex_mr = lambda i, ci, p: dict(tp=SMA[i, ci], stop=0.05, tmax=48)
mask2 = (P <= 0.15) & (W >= 0.05) & (W <= 0.20) & ~np.isnan(P)
def cand_rg(i):
    out = []
    for c in np.where(mask2[i])[0]:
        mid = LO[i, c] + 0.5 * (HI[i, c] - LO[i, c])
        if mid / C[i, c] - 1 >= 0.015: out.append((P[i, c], c))
    return out
ex_rg = lambda i, ci, p: dict(tp=LO[i, ci] + 0.5 * (HI[i, ci] - LO[i, ci]), stop_abs=LO[i, ci] * 0.99 if LO[i, ci] * 0.99 < p else None, stop=0.05, tmax=96)
print('\n=== 2) 비용 민감도 (전 기간 평균 순수익/건, 장세별) ===')
for cname, (ci_, fe, sl) in {'비용 0 (이론)': (0, 0, 0), '지정가 왕복 0.1% (수수료만)': (0, 0.0005, 0), '현실(진입0.15%+청산 슬리피지0.25%)': (0.0015, 0.0005, 0.0025)}.items():
    rb.COST_IN, rb.FEE, rb.SLIP_MKT = ci_, fe, sl
    for sname, cf, ef in (('M0 모멘텀', cand_m0, ex_m0), ('MR z<=-2.5', cand_mr, ex_mr), ('RG 박스권', cand_rg, ex_rg)):
        tr = rb.run(sname, cf, ef); t2 = summarize(sname, tr)
        s = ' | '.join(f"{k} {t2[t2['reg']==k]['r'].mean()*100:+.2f}% (승률 {(t2[t2['reg']==k]['r']>0).mean()*100:.0f}%, n={int((t2['reg']==k).sum())})" for k in ('상승', '횡보', '하락'))
        print(f'{cname:34s} {sname:10s} {s}')
