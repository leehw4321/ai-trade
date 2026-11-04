"""라벨 값(4시간·+1%·비용 0.3%)이 합리적인지 점검. 개발 구간(앞 120일)만 사용, 마지막 구간은 열어 보지 않는다.
1) 비용 대비 잡음: 지평별 순수익률 분산·비용 비중, 라벨 양성 비율
2) 선행 단서 스캔(선언된 6개 단순 특징): 날짜 단위 순위상관(IC)의 평균·t값 — 모델이 아니라 '신호가 있는지' 확인용
"""
import sys
from collections import defaultdict
from math import sqrt
from statistics import mean, median, pstdev

sys.path.insert(0, r'C:\Users\User\Desktop\ai-trade\tools\research')
import pit_universe as pit

CUT = pit.to_epoch('2026-11-04T08:15:00')
data = pit.load_dir(r'C:\Users\User\Desktop\research_data\candles15', cutoff=CUT)
B = pit.BAR
allmin = min(min(b) for b in data.values())
DEV_END = allmin + 120 * 86400  # 개발 구간 끝: 이후 약 60일은 열지 않는다
COST = 0.003


def rank(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(v):
        j = i
        while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(x, y):
    if len(x) < 8:
        return None
    rx, ry = rank(x), rank(y)
    mx, my = mean(rx), mean(ry)
    sx = sqrt(sum((a - mx) ** 2 for a in rx))
    sy = sqrt(sum((b - my) ** 2 for b in ry))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / (sx * sy)


def close(bars, s):
    b = bars.get(s)
    return b[3] if b else None


def features(bars, s0):
    c0 = close(bars, s0)
    if c0 is None:
        return None
    c1, c4, c18 = close(bars, s0 - 4 * B), close(bars, s0 - 16 * B), close(bars, s0 - 72 * B)
    prev = [bars[s][4] for s in range(s0 - 20 * B, s0, B) if s in bars]
    v0 = bars[s0][4]
    win = [bars[s] for s in range(s0 - 16 * B + B, s0 + B, B) if s in bars]
    if None in (c1, c4, c18) or len(prev) < 15 or len(win) < 12 or median(prev) <= 0:
        return None
    hi, lo = max(w[1] for w in win), min(w[2] for w in win)
    return {'ret_1h': c0 / c1 - 1, 'ret_4h': c0 / c4 - 1, 'rs_18h': c0 / c18 - 1,
            'vol_ratio': v0 / median(prev), 'range_pos_4h': (c0 - lo) / (hi - lo) if hi > lo else 0.5,
            'quiet_volup': (v0 / median(prev)) / (1 + abs(c0 / c1 - 1) * 50)}  # 가격은 조용한데 거래대금만 늘어난 정도(선행 후보)


FEATS = ['ret_1h', 'ret_4h', 'rs_18h', 'vol_ratio', 'range_pos_4h', 'quiet_volup']
HORIZONS = {'1h': 4, '4h': 16}
ic = {(f, h): [] for f in FEATS for h in HORIZONS}
fwd_pool = {h: [] for h in HORIZONS}
fwd_pool['24h'] = []
pos = {h: [0, 0] for h in HORIZONS}
pos_thr = {}
T = (allmin + 100 * B) // (4 * 3600) * (4 * 3600) + 4 * 3600
n_times = 0
while T < DEV_END:
    uni = [m for m, _ in pit.universe_at(data, T, top_n=30)]
    rows = []
    for m in uni:
        bars = data[m]
        s0 = T - B
        f = features(bars, s0)
        if f is None:
            continue
        fw = {}
        c0 = close(bars, s0)
        for h, n in HORIZONS.items():
            cf = close(bars, s0 + n * B)
            fw[h] = cf / c0 - 1 - COST if cf else None
        cf24 = close(bars, s0 + 96 * B)
        if cf24:
            fwd_pool['24h'].append(cf24 / c0 - 1 - COST)
        rows.append((f, fw))
    if len(rows) >= 15:
        n_times += 1
        for h in HORIZONS:
            ys = [fw[h] for f, fw in rows if fw[h] is not None]
            fwd_pool[h].extend(ys)
            pos[h][0] += sum(1 for y in ys if y >= 0.01)
            pos[h][1] += len(ys)
            for feat in FEATS:
                xs = [f[feat] for f, fw in rows if fw[h] is not None]
                v = spearman(xs, ys)
                if v is not None:
                    ic[(feat, h)].append(v)
    T += 4 * 3600

print(f'개발 구간 {pit.from_epoch_kst(allmin)} ~ {pit.from_epoch_kst(DEV_END)} (120일), 표집 시점 {n_times}개(4시간 간격), 상위30 유니버스')
print('\n[1] 비용 대비 잡음 (비용 차감 후 순수익률)')
for h in ('1h', '4h', '24h'):
    v = fwd_pool[h]
    sd = pstdev(v)
    print(f'  {h:>3s}: 표준편차 {sd*100:.2f}%  중앙값 {median(v)*100:+.2f}%  평균 {mean(v)*100:+.3f}%  비용 0.30%는 표준편차의 {0.003/sd*100:.0f}%  표본 {len(v)}')
for h in HORIZONS:
    print(f'  {h} 후 순수익 >= +1% 인 비율: {pos[h][0] / pos[h][1]:.1%} ({pos[h][0]}/{pos[h][1]})')
print('\n[2] 선행 단서 스캔: 날짜 단위 순위상관(IC) 평균, t값 (|t|>2 가 우연이 아닐 가능성; 6개x2지평=12회 비교이므로 |t|>3 기준 권장)')
for feat in FEATS:
    for h in HORIZONS:
        v = ic[(feat, h)]
        m, s = mean(v), pstdev(v)
        t = m / (s / sqrt(len(v))) if s > 0 else 0
        print(f'  {feat:13s} {h:>3s}  IC 평균 {m:+.4f}  t={t:+.2f}  양(+) 비율 {sum(1 for x in v if x > 0)/len(v):.0%}  n={len(v)}')
