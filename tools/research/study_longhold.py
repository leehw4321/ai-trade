"""사전 등록 연구: 긴 보유·낮은 거래 빈도, 순위 범위 확대(5→8·10위)를 처음 보는 자료(2023-10~2024-11-02)에서 1회 검증한다.

규칙·가설·판정은 docs/ai-history/2026-11-04-longhold-study-protocol.md (이 스크립트와 같은 커밋으로 동결).
결과를 보고 값을 바꾸지 않고 재실행하지 않는다.
  python study_longhold.py
"""
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pit_universe as pit
import study_reversal as sr

B = pit.BAR
HOUR = 3600
EXT_DIR = Path(r'C:\Users\User\Desktop\research_data\candles15_ext')
CUTOFF_KST = '2024-11-02T16:30:00'
MIN_UNIVERSE = 20
RS_CAP = 0.15
WANT = 3
MAX_DAYS = 7
COSTS = (0.003, 0.0085)
LEVEL = 1 - 0.05 / 4 / 1      # 4개 가설 Bonferroni, 양쪽 구간 98.75%
BOOT = 2000
BLOCK_DAYS = 7
# 이름: (scan, 순위 이탈 기준 또는 None, 손절 비율, 최대 보유 시간)
POLICIES = {
    'A 현재(순위5)': (5, 5, 0.045, MAX_DAYS * 24),
    'B 순위8': (8, 8, 0.045, MAX_DAYS * 24),
    'C 순위10': (10, 10, 0.045, MAX_DAYS * 24),
    'D 24시간 보유': (5, None, 0.045, 24),
    'E 72시간 보유': (5, None, 0.08, 72),
}
OUT = sr.OUT_DIR / 'longhold_result.json'


def hourly_state(data, t_start, t_end):
    """정시마다 {T: {종목: (rs, 순위)}}과 유니버스 크기. 순위는 상위30 안 rs 내림차순."""
    out = {}
    T = math.ceil(t_start / HOUR) * HOUR
    while T < t_end:
        uni = [m for m, _ in pit.universe_at(data, T, top_n=30)]
        rows = []
        for m in uni:
            bars = data[m]
            c0, c72 = bars.get(T - B), bars.get(T - B - 72 * B)
            if c0 and c72:
                rows.append((m, c0[3] / c72[3] - 1))
        rows.sort(key=lambda x: (-x[1], x[0]))
        out[T] = ({m: (rs, i + 1) for i, (m, rs) in enumerate(rows)}, len(uni))
        T += HOUR
    return out


def candidates(st, scan):
    """순위 ≤ scan 이고 0<rs<RS_CAP 인 종목을 순위순으로 최대 WANT개. (후보 목록, 과열로 걸러진 수)"""
    top = sorted(((rank, m, rs) for m, (rs, rank) in st.items() if rank <= scan))
    cand = [(m, rs) for rank, m, rs in top if 0 < rs < RS_CAP]
    hot = sum(1 for rank, m, rs in top if rs >= RS_CAP)
    return cand[:WANT], hot


def simulate(bars, market, T, entry, rank_limit, stop_pct, max_hours, state):
    """T에 entry로 진입 → (총수익, 보유 시간, 사유). 미래는 시간 순서로만 읽는다. 관측 불가면 None."""
    stop = entry * (1 - stop_pct)
    end = T + max_hours * HOUR
    s = T
    while s < end:
        b = bars.get(s)
        if b is not None and b[2] <= stop:
            px = min(stop, b[0])
            return px / entry - 1, (s - T) / HOUR, 'stop'
        nxt = s + B
        if rank_limit is not None and nxt % HOUR == 0:
            snap = state.get(nxt)
            c = bars.get(s)
            if snap is not None and c is not None:
                info = snap[0].get(market)
                if info is None or info[1] > rank_limit or info[0] < 0:
                    return c[3] / entry - 1, (nxt - T) / HOUR, 'rank'
        s = nxt
    c = bars.get(end - B)
    if c is None:
        for k in range(1, 40):
            c = bars.get(end - B - k * B)
            if c:
                break
    return (c[3] / entry - 1, max_hours, 'time') if c else None


def diff_ci(a, b):
    """a, b: [(블록 번호, 값)]. 블록 부트스트랩으로 mean(b) - mean(a)의 LEVEL 구간(블록은 같은 번호를 공유)."""
    ids = sorted({k for k, _ in a} | {k for k, _ in b})
    ga, gb = defaultdict(list), defaultdict(list)
    for k, v in a:
        ga[k].append(v)
    for k, v in b:
        gb[k].append(v)
    rng = random.Random(sr.SEED)
    ds = []
    for _ in range(BOOT):
        sa = ca = sb = cb = 0.0
        for _ in ids:
            k = rng.choice(ids)
            sa += sum(ga[k]); ca += len(ga[k])
            sb += sum(gb[k]); cb += len(gb[k])
        if ca and cb:
            ds.append(sb / cb - sa / ca)
    ds.sort()
    lo = ds[int((1 - LEVEL) / 2 * len(ds))]
    hi = ds[int((1 + LEVEL) / 2 * len(ds)) - 1]
    return lo, hi


def main():
    cut = pit.to_epoch(CUTOFF_KST)
    data = pit.load_dir(EXT_DIR, cutoff=cut)
    data = {m: b for m, b in data.items() if b}
    allmin = min(min(b) for b in data.values())
    t_start = math.ceil((allmin + 100 * B) / sr.STEP) * sr.STEP
    last_entry = cut - MAX_DAYS * 24 * HOUR - 2 * HOUR
    print(f'처음 보는 자료 진입 구간 {pit.from_epoch_kst(t_start)} ~ {pit.from_epoch_kst(last_entry)} · 종목 {len(data)}개 · 읽은 마지막 봉 마감 ≤ {pit.from_epoch_kst(cut)}')
    state = hourly_state(data, t_start, cut)
    t0 = None
    entries = {n: [] for n in POLICIES}
    avail = {5: [], 8: [], 10: []}
    hot_n = {5: 0, 8: 0, 10: 0}
    steps = 0
    T = math.ceil(t_start / sr.STEP) * sr.STEP
    while T <= last_entry:
        snap = state.get(T)
        if snap and snap[1] >= MIN_UNIVERSE:
            t0 = T if t0 is None else t0
            steps += 1
            for sc in (5, 8, 10):
                cand, hot = candidates(snap[0], sc)
                avail[sc].append(len(cand))
                hot_n[sc] += hot
            for name, (scan, rl, sp, mh) in POLICIES.items():
                cand, _ = candidates(snap[0], scan)
                for m, rs in cand:
                    c = data[m].get(T - B)
                    if c:
                        out = simulate(data[m], m, T, c[3], rl, sp, mh, state)
                        if out is not None:
                            entries[name].append((T, m, out))
        T += sr.STEP
    print('사용한 판단 시점', steps, '(유니버스 ≥', MIN_UNIVERSE, '종목)')
    result = {'period': [pit.from_epoch_kst(t0), pit.from_epoch_kst(last_entry)], 'steps': steps, 'markets': len(data), 'policies': {}, 'availability': {}}

    print('\n[설명용] 후보 가용성 (과열 rs≥15% 제외 후 순위 범위 안 후보, 최대 3)')
    for sc in (5, 8, 10):
        v = avail[sc]
        row = {'avg_candidates': sum(v) / len(v), 'share_zero': sum(1 for x in v if x == 0) / len(v), 'share_lt3': sum(1 for x in v if x < 3) / len(v),
               'overheated_per_step': hot_n[sc] / steps}
        result['availability'][sc] = row
        print(f"  순위 {sc:>2d}: 평균 후보 {row['avg_candidates']:.2f}개 · 후보 0개 {row['share_zero']:.0%} · 3개 미만 {row['share_lt3']:.0%} · 시점당 과열 제외 {row['overheated_per_step']:.2f}개")

    blocks = {}
    base_t0 = t0
    for name in POLICIES:
        blocks[name] = [((T - base_t0) // (BLOCK_DAYS * 86400), out[0]) for T, m, out in entries[name]]
    print('\n[정책별 진입당 평균 순수익]')
    for name in POLICIES:
        gross = [out[0] for T, m, out in entries[name]]
        holds = [out[1] for T, m, out in entries[name]]
        why = defaultdict(int)
        for T, m, out in entries[name]:
            why[out[2]] += 1
        s = sorted(gross)
        row = {'n': len(gross), 'gross_mean': sum(gross) / len(gross), 'median_gross': s[len(s) // 2], 'avg_hold_h': sum(holds) / len(holds), 'reasons': dict(why)}
        for c in COSTS:
            row[f'net_{c}'] = row['gross_mean'] - c
            row[f'win_{c}'] = sum(1 for g in gross if g - c > 0) / len(gross)
        result['policies'][name] = row
        print(f"  {name}: n={row['n']} · 총수익 {row['gross_mean']*100:+.3f}% · 순수익 0.30% {row['net_0.003']*100:+.3f}% / 0.85% {row['net_0.0085']*100:+.3f}% · 중앙(총) {row['median_gross']*100:+.2f}% · 승률(0.30%) {row['win_0.003']:.0%} · 평균 보유 {row['avg_hold_h']:.1f}h · 사유 {dict(why)}")

    print(f'\n[가설 판정] A 대비 평균 차이(비용 무관) {LEVEL*100:.2f}% 구간, 4개 가설 보정')
    base = 'A 현재(순위5)'
    for name in list(POLICIES)[1:]:
        d = result['policies'][name]['gross_mean'] - result['policies'][base]['gross_mean']
        lo, hi = diff_ci(blocks[base], blocks[name])
        net30 = result['policies'][name]['net_0.003']
        net85 = result['policies'][name]['net_0.0085']
        if lo > 0 and net30 > 0 and net85 > 0:
            v = '실거래 변경 제안 가능'
        elif lo > 0 and net30 > 0:
            v = '순수익 단서(0.85%에서는 음수)'
        elif lo > 0:
            v = '상대 우위(순수익은 음수)'
        elif hi < 0:
            v = '유의하게 나쁨'
        else:
            v = '불확실/근거 없음'
        result['policies'][name].update({'vs_A': d, 'vs_A_ci': [lo, hi], 'verdict': v})
        print(f'  {name}: 차이 {d*100:+.3f}%p [{lo*100:+.3f}, {hi*100:+.3f}] → {v}')
    sr.OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    print('저장:', OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
