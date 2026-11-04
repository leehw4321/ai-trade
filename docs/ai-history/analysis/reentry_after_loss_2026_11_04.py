"""재진입 탐색(소진 자료): 같은 종목을 손실 청산 뒤 24시간 안에 다시 사면 결과가 더 나쁜가? 현재 봇 근사 규칙(P1)을 종목별 순차로 재생(한 종목 한 포지션, 손실 청산 뒤 6시간 대기)."""
import math
import random
import sys
from collections import defaultdict
from statistics import mean

sys.path.insert(0, r'C:\Users\User\Desktop\ai-trade\tools\research')
import pit_universe as pit
import study_hold as sh
import study_reversal as sr

B, HOUR = pit.BAR, 3600
data = pit.load_dir(sr.DATA_DIR, cutoff=pit.to_epoch(sr.CUTOFF_KST))
for m, b in data.items():
    sh._MKT[id(b)] = m
t_start, _, _ = sr.grid(data)
cut = pit.to_epoch(sr.CUTOFF_KST)
state = sh.hourly_state(data, t_start, cut)
last_entry = cut - 7 * 24 * HOUR - 2 * HOUR
cands = []
T = math.ceil(t_start / sr.STEP) * sr.STEP
while T <= last_entry:
    st = state.get(T)
    if st:
        c = sorted(((m, rs) for m, (rs, r) in st.items() if 0 < rs < 0.15), key=lambda x: (-x[1], x[0]))
        for m, rs in c[:3]:
            cands.append((T, m))
    T += sr.STEP
by_m = defaultdict(list)
for T, m in cands:
    by_m[m].append(T)
fresh, reentry, rows = [], [], []
for m, times in by_m.items():
    bars = data[m]
    open_until, last = 0, None   # last = (exit_time, exit_price, loss)
    for T in sorted(times):
        if T < open_until:
            continue
        if last and last[2] and T < last[0] + 6 * HOUR:
            continue
        c = bars.get(T - B)
        if not c:
            continue
        out = sh.simulate(bars, T, c[3], 0.045, 7 * 24, True, state)
        if out is None:
            continue
        g, ts, why, px = out
        net = g - sr.COST
        is_re = bool(last and last[2] and T - last[0] <= 24 * HOUR)
        prem = (c[3] / last[1] - 1) if is_re else None
        (reentry if is_re else fresh).append((net, T, prem))
        open_until = ts
        last = (ts, px, g < 0)
print('순차 재생 진입: 신규/일반', len(fresh), '· 손실 청산 후 24h 내 재진입', len(reentry))


def block_ci(items):
    order = sorted(items, key=lambda x: x[1])
    t0 = order[0][1]
    blocks = defaultdict(list)
    for net, T, _ in order:
        blocks[(T - t0) // (7 * 86400)].append(net)
    ids = list(blocks)
    rng = random.Random(3)
    ms = []
    for _ in range(1000):
        tot = cnt = 0
        for _ in ids:
            b = blocks[rng.choice(ids)]
            tot += sum(b)
            cnt += len(b)
        ms.append(tot / cnt)
    ms.sort()
    return ms[25], ms[974]


for name, items in (('일반 진입', fresh), ('손실 청산 후 24h 내 재진입', reentry)):
    v = [x[0] for x in items]
    lo, hi = block_ci(items)
    print(f'  {name}: n={len(v)} 평균 순수익 {mean(v)*100:+.3f}% [{lo*100:+.3f}, {hi*100:+.3f}] 승률 {sum(1 for x in v if x > 0)/len(v):.0%}')
prem = [x[2] for x in reentry if x[2] is not None]
print(f'  재진입 가격 프리미엄(직전 청산가 대비) 평균 {mean(prem)*100:+.2f}% · 직전 청산가보다 비싼 비율 {sum(1 for p in prem if p > 0)/len(prem):.0%}')
hi_p = [x[0] for x in reentry if x[2] is not None and x[2] > 0.03]
lo_p = [x[0] for x in reentry if x[2] is not None and x[2] <= 0.03]
if hi_p and lo_p:
    print(f'  재진입 중 직전 청산가보다 3% 넘게 비싸게 산 경우 n={len(hi_p)} 평균 {mean(hi_p)*100:+.3f}% / 그 이하 n={len(lo_p)} 평균 {mean(lo_p)*100:+.3f}%')
