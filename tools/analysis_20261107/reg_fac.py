from reg_bt import *
SPLIT = pd.Timestamp('2026-03-01')
cdf = pd.DataFrame(C, index=tsi, columns=coins)
hour = np.where(tsi.minute.values == 45)[0]            # 매시 정각 직전 마감 봉
hour = hour[(hour > 300) & (hour < N - 300)]
def fwd(h): return (cdf.shift(-h) / cdf - 1).values
f4, f24 = fwd(16), fwd(96)
def past(h): return (cdf / cdf.shift(h) - 1).values
print('=== A) 종목 간 정렬: 지난 X 수익률 5분위별 이후 수익(비용 전, 시장 평균 대비), 장세별 ===')
for ph, nm in ((16, '지난4h'), (72, '지난18h'), (96, '지난24h')):
    P = past(ph)
    for fh, F_, fn in ((16, f4, '이후4h'), (96, f24, '이후24h')):
        out = {}
        for k in ('상승', '횡보', '하락'):
            sel = [i for i in hour if reg[i] == k]
            q1, q5, allm = [], [], []
            for i in sel:
                p, f = P[i], F_[i]
                ok = ~np.isnan(p) & ~np.isnan(f)
                if ok.sum() < 30: continue
                o = np.argsort(p[ok]); fv = f[ok][o]; n = len(fv); a = max(3, n // 5)
                q1.append(fv[:a].mean() - fv.mean()); q5.append(fv[-a:].mean() - fv.mean()); allm.append(fv.mean())
            out[k] = (np.mean(q1) * 100, np.mean(q5) * 100, np.mean(allm) * 100, len(sel))
        print(f'{nm} → {fn}: ' + ' | '.join(f'{k}: 하위20% {v[0]:+.2f}% 상위20% {v[1]:+.2f}% (시장평균 {v[2]:+.2f}%, n={v[3]})' for k, v in out.items()))
print('  (하위20% = 가장 많이 떨어진 종목, 상위20% = 가장 많이 오른 종목. 값은 시장 평균 대비 초과 수익, 비용 0.3~0.5% 전)')

print('\n=== B) 시장 전체 타이밍: 24시간 시장 수익률 구간별 이후 시장 수익(균등 바스켓, 비용 전) ===')
R1h = R1[hour]; F24 = fwd24[hour]; F72 = (idx.shift(-288) / idx - 1).values[hour]; F4 = (idx.shift(-16) / idx - 1).values[hour]
bins = [(-1, -0.06), (-0.06, -0.04), (-0.04, -0.02), (-0.02, 0), (0, 0.02), (0.02, 0.04), (0.04, 1)]
for lo, hi in bins:
    m = (R1h > lo) & (R1h <= hi) & ~np.isnan(F24)
    if m.sum() < 20: continue
    print(f'지난24h {lo*100:+.0f}~{hi*100:+.0f}%: n={m.sum():4d} | 이후4h {np.nanmean(F4[m])*100:+.2f}% 이후24h 평균 {np.mean(F24[m])*100:+.2f}% 중앙 {np.median(F24[m])*100:+.2f}% 양수 {np.mean(F24[m]>0)*100:.0f}% | 이후72h {np.nanmean(F72[m])*100:+.2f}%')
print('\n=== C) 3일 장세별 이후 시장 수익 (개발/검증) ===')
for k in ('상승', '횡보', '하락'):
    for per, sel in (('개발', tsi[hour] < SPLIT), ('검증', tsi[hour] >= SPLIT)):
        m = (reg[hour] == k) & sel & ~np.isnan(F24)
        print(f'{k} {per}: n={m.sum():4d} 이후24h 평균 {np.mean(F24[m])*100:+.2f}% 중앙 {np.median(F24[m])*100:+.2f}% 양수 {np.mean(F24[m]>0)*100:.0f}%')
print('\n=== D) 변동성(24h 평균 봉 변동폭)과 이후 24h 시장 절대 변동 -- 횡보장 박스폭 ===')
rng = pd.Series(np.abs(mr), index=tsi).rolling(96).mean().values
for k in ('상승', '횡보', '하락'):
    m = (reg == k) & ~np.isnan(rng)
    print(k, '시장 15분 평균 변동 %.3f%%, 24h 시장 고저폭 중앙값 %.2f%%' % (np.mean(rng[m]) * 100,
          np.median((idx.rolling(96).max() / idx.rolling(96).min() - 1).values[m & ~np.isnan((idx.rolling(96).max() / idx.rolling(96).min() - 1).values)]) * 100))
