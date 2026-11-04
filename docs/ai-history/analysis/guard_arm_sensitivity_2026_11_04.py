"""이익보호 시작선(arm) 민감도: 같은 진입에 arm만 바꿔 재생. 탐색(180일, 소진 자료), 연구 결론에 쓰지 않는다."""
import math
import random
import sys
from collections import defaultdict
from statistics import mean, pstdev

sys.path.insert(0, r'C:\Users\User\Desktop\ai-trade\tools\research')
import pit_universe as pit
import study_hold as sh
import study_reversal as sr

B, HOUR = pit.BAR, 3600
FEE, COSTC = 0.0005, 0.002
GIVE = 0.4
ARMS = [None, 0.02, 0.025, 0.03, 0.035, 0.04, 0.05]


def simulate(bars, mkt, T, entry, arm, state):
    stop0 = entry * (1 - 0.045)
    guard = 0.0
    peak = entry
    end = T + 7 * 24 * HOUR
    s = T
    while s < end:
        b = bars.get(s)
        if b is not None:
            line = max(stop0, guard)
            if b[2] <= line:
                px = min(line, b[0])
                return px / entry - 1 - sr.COST, (s - T) / HOUR, 'guard' if guard > stop0 else 'stop'
            peak = max(peak, b[1])
            if arm is not None and peak >= entry * (1 + arm):
                guard = max(guard, entry * (1 + 2 * FEE + COSTC), entry + (peak - entry) * (1 - GIVE))
        nxt = s + B
        if nxt % HOUR == 0:
            st = state.get(nxt)
            c = bars.get(s)
            if st is not None and c is not None:
                info = st.get(mkt)
                if info is None or info[1] > 5 or info[0] < 0:
                    return c[3] / entry - 1 - sr.COST, (nxt - T) / HOUR, 'rank'
        s = nxt
    c = bars.get(end - B)
    return (c[3] / entry - 1 - sr.COST, 168.0, 'time') if c else None


def block_ci(vals, times, level=0.95):
    order = sorted(range(len(vals)), key=lambda i: times[i])
    t0 = times[order[0]]
    blocks = defaultdict(list)
    for i in order:
        blocks[(times[i] - t0) // (7 * 86400)].append(vals[i])
    ids = list(blocks)
    rng = random.Random(1)
    ms = []
    for _ in range(1000):
        tot = cnt = 0
        for _ in ids:
            b = blocks[rng.choice(ids)]
            tot += sum(b)
            cnt += len(b)
        ms.append(tot / cnt)
    ms.sort()
    return ms[int((1 - level) / 2 * 1000)], ms[int((1 + level) / 2 * 1000) - 1]


data = pit.load_dir(sr.DATA_DIR, cutoff=pit.to_epoch(sr.CUTOFF_KST))
t_start, _, _ = sr.grid(data)
cut = pit.to_epoch(sr.CUTOFF_KST)
state = sh.hourly_state(data, t_start, cut)
last_entry = cut - 7 * 24 * HOUR - 2 * HOUR
entries = []
T = math.ceil(t_start / sr.STEP) * sr.STEP
while T <= last_entry:
    st = state.get(T)
    if st:
        cand = sorted(((m, rs) for m, (rs, r) in st.items() if 0 < rs < 0.15), key=lambda x: (-x[1], x[0]))
        for m, rs in cand[:3]:
            c = data[m].get(T - B)
            if c:
                entries.append((T, m, c[3]))
    T += sr.STEP
print('진입', len(entries))
res = {a: [] for a in ARMS}
for T, m, e in entries:
    for a in ARMS:
        res[a].append(simulate(data[m], m, T, e, a, state))
ok = [i for i in range(len(entries)) if all(res[a][i] for a in ARMS)]
times = [entries[i][0] for i in ok]
base = [res[0.03][i][0] for i in ok]
print('arm별 평균 순수익/진입 (P1+이익보호, 비용 0.3%)  [3.0% 대비 차이 95% 구간]  결과가 달라진 진입 비율')
sd = {}
for a in ARMS:
    v = [res[a][i][0] for i in ok]
    d = [x - y for x, y in zip(v, base)]
    lo, hi = block_ci(d, times)
    changed = sum(1 for x in d if abs(x) > 1e-12) / len(d)
    guards = sum(1 for i in ok if res[a][i][2] == 'guard') / len(ok)
    sd[a] = (mean(d), pstdev(d), changed)
    name = '이익보호 없음' if a is None else f'{a*100:.1f}%'
    print(f'  arm {name:>9s}: {mean(v)*100:+.3f}%  차이 {mean(d)*100:+.3f}%p [{lo*100:+.3f}, {hi*100:+.3f}]  달라진 진입 {changed:.1%}  보호선 청산 {guards:.1%}')
# 3.0 → 3.5 변경을 실거래에서 가려내는 데 필요한 표본(쌍체 차이, 80% 검정력 α=0.05)
m, s, ch = sd[0.035]
if abs(m) > 1e-9:
    n = (2.8 * s / abs(m)) ** 2
    print(f'\n3.0%→3.5% 차이 평균 {m*100:+.4f}%p, 차이의 표준편차 {s*100:.3f}%p → 쌍체 비교로 가려내려면 약 {n:,.0f}건(같은 진입에 두 설정을 동시에 적용했을 때). 서로 다른 거래에 번갈아 적용하면 거래당 표준편차 약 2.2%로 훨씬 많이 필요: {(2.8*2.2/abs(m*100))**2*2:,.0f}건 안팎')
else:
    print('\n차이 평균이 0에 가까워 필요 표본이 사실상 무한대')
print('실거래 속도 가늠: 최근 이틀 봇 완결 거래 약', '10건/일 안팎(오늘 22건 2.5일)')
