"""탐색 연구(사전 선언): 같은 진입을 '얼마나 오래 들고 가는가'가 결과를 바꾸는가. 청산 직후 가격은 어떻게 움직였는가.

규칙은 docs/ai-history/2026-11-04-hold-study-protocol.md (이 스크립트와 같은 커밋). 전부 탐색(잠금 구간 소진).
  python study_hold.py
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
COST = sr.COST
MAX_DAYS = 7
POLICIES = {   # 이름: (손절 비율, 최대 보유 시간, 순위 이탈 청산 여부)
    'P1 현재(손절4.5%+순위이탈, 최대7일)': (0.045, MAX_DAYS * 24, True),
    'P2 손절4.5% 최대24h(순위 무시)': (0.045, 24, False),
    'P3 손절8% 최대72h(순위 무시)': (0.08, 72, False),
    'P4 손절12% 최대7일(순위 무시)': (0.12, MAX_DAYS * 24, False),
    'P5 손절없음 24h 보유': (None, 24, False),
    'P6 손절없음 72h 보유': (None, 72, False),
}
BOOT = 2000
BLOCK_DAYS = 7


def hourly_state(data, t_start, t_end):
    """정시(1시간)마다 상위30 안 종목의 18h 상승률 rs와 순위. {T: {종목: (rs, rank)}}"""
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
        out[T] = {m: (rs, i + 1) for i, (m, rs) in enumerate(rows)}
        T += HOUR
    return out


def simulate(bars, T, entry, stop_pct, max_hours, rank_exit, state):
    """T(판단 시각)에 entry(직전 마감 종가)로 진입 → (수익률 총, 청산 시각, 사유, 청산가). 미래는 순서대로만 읽는다."""
    stop = entry * (1 - stop_pct) if stop_pct else None
    end = T + max_hours * HOUR
    s = T
    while s < end:
        b = bars.get(s)
        if b is not None:
            if stop is not None and b[2] <= stop:
                px = min(stop, b[0])
                return px / entry - 1, s, 'stop', px
        nxt = s + B
        if rank_exit and nxt % HOUR == 0 and nxt > T:
            st = state.get(nxt)
            c = bars.get(s)
            if st is not None and c is not None:
                info = st.get(bars_market(bars))
                if info is None or info[1] > 5 or info[0] < 0:
                    return c[3] / entry - 1, nxt, 'rank', c[3]
        s = nxt
    c = bars.get(end - B)
    if c is None:  # 해당 봉이 없으면 가장 가까운 이전 종가
        for k in range(1, 40):
            c = bars.get(end - B - k * B)
            if c:
                break
    return (c[3] / entry - 1, end, 'time', c[3]) if c else None


_MKT = {}


def bars_market(bars):
    return _MKT[id(bars)]


def block_mean_ci(vals, times):
    """7일 블록 부트스트랩(진입 시각 순서)."""
    order = sorted(range(len(vals)), key=lambda i: times[i])
    v = [vals[i] for i in order]
    t = [times[i] for i in order]
    if not v:
        return float('nan'), float('nan')
    # 시각 기반 블록: 7일 구간 번호로 묶는다
    blocks = defaultdict(list)
    t0 = t[0]
    for x, tt in zip(v, t):
        blocks[(tt - t0) // (BLOCK_DAYS * 86400)].append(x)
    ids = list(blocks)
    rng = random.Random(sr.SEED)
    means = []
    for _ in range(BOOT):
        tot, cnt = 0.0, 0
        for _ in ids:
            b = blocks[rng.choice(ids)]
            tot += sum(b)
            cnt += len(b)
        means.append(tot / cnt)
    means.sort()
    return means[int(0.025 * BOOT)], means[int(0.975 * BOOT) - 1]


def main():
    data = pit.load_dir(sr.DATA_DIR, cutoff=pit.to_epoch(sr.CUTOFF_KST))
    for m, b in data.items():
        _MKT[id(b)] = m
    t_start, _, lock_end = sr.grid(data)
    cut = pit.to_epoch(sr.CUTOFF_KST)
    last_entry = cut - (MAX_DAYS * 24 * HOUR) - 2 * HOUR  # 모든 정책이 끝까지 관측되도록 마지막 7일은 진입하지 않는다
    print(f'진입 구간 {pit.from_epoch_kst(t_start)} ~ {pit.from_epoch_kst(last_entry)} (진입 4시간 간격, 현재 봇 진입 근사 상위 3)')
    state = hourly_state(data, t_start, cut)
    entries = []
    T = math.ceil(t_start / sr.STEP) * sr.STEP
    while T <= last_entry:
        st = state.get(T)
        if st:
            cand = [(m, rs) for m, (rs, rank) in st.items() if 0 < rs < 0.15]
            cand.sort(key=lambda x: (-x[1], x[0]))
            for m, rs in cand[:sr.TOP_K]:
                c = data[m].get(T - B)
                if c:
                    entries.append((T, m, c[3]))
        T += sr.STEP
    print('진입 수', len(entries))
    res = {name: [] for name in POLICIES}
    post = defaultdict(list)  # (사유) -> 청산 후 24h/72h 총수익
    for T, m, entry in entries:
        bars = data[m]
        for name, (sp, mh, rk) in POLICIES.items():
            out = simulate(bars, T, entry, sp, mh, rk, state)
            if out is None:
                res[name].append(None)
                continue
            g, ts, why, px = out
            res[name].append((g - COST, (ts - T) / HOUR, why, T))
            if name.startswith('P1') and why in ('stop', 'rank'):
                for h in (24, 72):
                    cf = bars.get(ts + h * HOUR - B)
                    if cf:
                        post[(why, h)].append(cf[3] / px - 1)
    base = res['P1 현재(손절4.5%+순위이탈, 최대7일)']
    ok = [i for i in range(len(entries)) if all(res[n][i] is not None for n in POLICIES)]
    print('모든 정책이 관측된 진입', len(ok))
    summary = {}
    print('\n[정책별 같은 진입 비교: 비용 0.3% 차감 후 순수익/진입]')
    for name in POLICIES:
        vals = [res[name][i][0] for i in ok]
        times = [res[name][i][3] for i in ok]
        lo, hi = block_mean_ci(vals, times)
        diff = [res[name][i][0] - base[i][0] for i in ok]
        dlo, dhi = block_mean_ci(diff, times)
        holds = [res[name][i][1] for i in ok]
        reasons = defaultdict(int)
        for i in ok:
            reasons[res[name][i][2]] += 1
        wins = sum(1 for v in vals if v > 0) / len(vals)
        s = {'mean': sum(vals) / len(vals), 'ci': [lo, hi], 'median': sorted(vals)[len(vals) // 2], 'win': wins,
             'avg_hold_h': sum(holds) / len(holds), 'vs_P1': sum(diff) / len(diff), 'vs_P1_ci': [dlo, dhi], 'reasons': dict(reasons)}
        summary[name] = s
        print(f"  {name}: 평균 {s['mean']*100:+.3f}% [{lo*100:+.3f}, {hi*100:+.3f}] · 중앙 {s['median']*100:+.2f}% · 승률 {wins:.0%} · 평균 보유 {s['avg_hold_h']:.1f}h · P1 대비 {s['vs_P1']*100:+.3f}%p [{dlo*100:+.3f}, {dhi*100:+.3f}] · 청산 사유 {dict(reasons)}")
    print('\n[P1 청산 직후 가격(총수익, 비용 전): 청산가 대비 24h/72h 뒤 종가]')
    post_s = {}
    for why in ('stop', 'rank'):
        for h in (24, 72):
            v = post.get((why, h), [])
            if v:
                share_up = sum(1 for x in v if x > 0) / len(v)
                post_s[f'{why}_{h}'] = {'n': len(v), 'mean': sum(v) / len(v), 'share_up': share_up}
                print(f"  {'손절' if why=='stop' else '순위이탈'} 후 {h}시간: 평균 {sum(v)/len(v)*100:+.2f}% · 오른 비율 {share_up:.0%} · n={len(v)}")
    out = {'summary': summary, 'post_exit': post_s, 'entries': len(entries), 'observed': len(ok)}
    (sr.OUT_DIR / 'hold_result.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
