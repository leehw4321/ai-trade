"""R 정의 수정(4시간 ATR → 15분 ATR)의 효과 크기 확인(탐색, 소진된 자료 두 개). 현재 이익보호(3%)와 손절 4.5%·순위이탈은 동일."""
import math
import random
import sys
from collections import defaultdict
from statistics import mean

sys.path.insert(0, r'C:\Users\User\Desktop\ai-trade\tools\research')
import pit_universe as pit
import study_hold as sh
import study_reversal as sr
import study_replicate as rep

B, HOUR = pit.BAR, 3600
FEE, COSTC, GIVE, ARM = 0.0005, 0.002, 0.4, 0.03
K, LOCK_R, START_R, TRAIL_M, GB, PART_R, PART_F = 3.0, 1.0, 2.0, 2.0, 0.5, 2.0, 0.3333


def atr15(bars, s0, n=14):
    trs, prev = [], None
    for s in range(s0 - 99 * B, s0 + B, B):
        b = bars.get(s)
        if b is None:
            continue
        if prev is not None:
            trs.append(max(b[1] - b[2], abs(b[1] - prev), abs(b[2] - prev)))
        prev = b[3]
    if len(trs) < n:
        return None
    a = sum(trs[:n]) / n
    for t in trs[n:]:
        a = (a * (n - 1) + t) / n
    return a


def atr4h(bars, s0, n=14):
    blocks = []
    for k in range(100, 0, -1):
        seg = [bars[s] for s in range(s0 - (k * 16 - 1) * B, s0 - ((k - 1) * 16 - 1) * B, B) if s in bars]
        if len(seg) >= 12:
            blocks.append((max(x[1] for x in seg), min(x[2] for x in seg), seg[-1][3]))
    if len(blocks) < n + 1:
        return None
    trs = [max(h - l, abs(h - blocks[i][2]), abs(l - blocks[i][2])) for i, (h, l, c) in enumerate(blocks[1:])]
    a = sum(trs[:n]) / n
    for t in trs[n:]:
        a = (a * (n - 1) + t) / n
    return a


def simulate(bars, mkt, T, entry, R_atr, state):
    stop0 = entry * 0.955
    stop, guard, peak_hi, peak_cl = stop0, 0.0, entry, entry
    part = False
    sold_frac, sold_px = 0.0, 0.0
    end = T + 7 * 24 * HOUR
    s = T
    while s < end:
        b = bars.get(s)
        if b is not None:
            line = max(stop, guard)
            if b[2] <= line:
                px = min(line, b[0])
                rest = 1 - sold_frac
                return sold_frac * (sold_px / entry - 1) + rest * (px / entry - 1) - sr.COST
            peak_hi = max(peak_hi, b[1])
            if peak_hi >= entry * (1 + ARM):
                guard = max(guard, entry * (1 + 2 * FEE + COSTC), entry + (peak_hi - entry) * (1 - GIVE))
            if R_atr:  # EXIT-T1: 닫힌 봉 종가 기준
                c = b[3]
                peak_cl = max(peak_cl, c)
                R = K * R_atr
                g = (peak_cl - entry) / R
                if g >= LOCK_R:
                    stop = max(stop, entry * (1 + 2 * FEE + COSTC))
                if g >= START_R:
                    stop = max(stop, peak_cl - TRAIL_M * R_atr)
                    if peak_cl > entry and (peak_cl - c) / (peak_cl - entry) >= GB:
                        rest = 1 - sold_frac
                        return sold_frac * (sold_px / entry - 1) + rest * (c / entry - 1) - sr.COST
                if not part and (c - entry) / R >= PART_R:
                    part, sold_frac, sold_px = True, PART_F, c
        nxt = s + B
        if nxt % HOUR == 0:
            st = state.get(nxt)
            c = bars.get(s)
            if st is not None and c is not None:
                info = st.get(mkt)
                if info is None or info[1] > 5 or info[0] < 0:
                    rest = 1 - sold_frac
                    return sold_frac * (sold_px / entry - 1) + rest * (c[3] / entry - 1) - sr.COST
        s = nxt
    c = bars.get(end - B)
    if not c:
        return None
    rest = 1 - sold_frac
    return sold_frac * (sold_px / entry - 1) + rest * (c[3] / entry - 1) - sr.COST


def block_ci(vals, times):
    order = sorted(range(len(vals)), key=lambda i: times[i])
    t0 = times[order[0]]
    blocks = defaultdict(list)
    for i in order:
        blocks[(times[i] - t0) // (7 * 86400)].append(vals[i])
    ids = list(blocks)
    rng = random.Random(2)
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


def run(label, data_dir, cutoff):
    data = pit.load_dir(data_dir, cutoff=cutoff)
    for m, b in data.items():
        sh._MKT[id(b)] = m
    allmin = min(min(b) for b in data.values())
    t_start = math.ceil((allmin + 400 * B) / sr.STEP) * sr.STEP  # 4시간 ATR 계산용 이력 확보
    state = sh.hourly_state(data, t_start, cutoff)
    last_entry = cutoff - 7 * 24 * HOUR - 2 * HOUR
    entries = []
    T = t_start
    while T <= last_entry:
        st = state.get(T)
        if st:
            cand = sorted(((m, rs) for m, (rs, r) in st.items() if 0 < rs < 0.15), key=lambda x: (-x[1], x[0]))
            for m, rs in cand[:3]:
                c = data[m].get(T - B)
                if c:
                    entries.append((T, m, c[3]))
        T += sr.STEP
    res = {'A 현재(R=4시간 ATR)': [], 'B 수정(R=15분 ATR)': [], 'C 이익보호만(T1 끔)': []}
    keep = []
    for T, m, e in entries:
        bars = data[m]
        a4, a15 = atr4h(bars, T - B), atr15(bars, T - B)
        if not a4 or not a15:
            continue
        keep.append(T)
        res['A 현재(R=4시간 ATR)'].append(simulate(bars, m, T, e, a4, state))
        res['B 수정(R=15분 ATR)'].append(simulate(bars, m, T, e, a15, state))
        res['C 이익보호만(T1 끔)'].append(simulate(bars, m, T, e, None, state))
    ok = [i for i in range(len(keep)) if all(res[k][i] is not None for k in res)]
    print(f'\n[{label}] 진입 {len(ok)}건')
    base = [res['A 현재(R=4시간 ATR)'][i] for i in ok]
    times = [keep[i] for i in ok]
    for k in res:
        v = [res[k][i] for i in ok]
        d = [x - y for x, y in zip(v, base)]
        lo, hi = block_ci(d, times) if any(abs(x) > 1e-12 for x in d) else (0, 0)
        changed = sum(1 for x in d if abs(x) > 1e-12) / len(d)
        print(f'  {k}: 평균 {mean(v)*100:+.3f}% · A 대비 {mean(d)*100:+.3f}%p [{lo*100:+.3f}, {hi*100:+.3f}] · 달라진 진입 {changed:.1%}')


run('180일(소진)', sr.DATA_DIR, pit.to_epoch(sr.CUTOFF_KST))
run('2024-11~2026-05(재현 소진)', rep.LONG_DIR, rep.SEEN_START)
