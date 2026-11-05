"""사전 등록 연구: 장세(하락·보합·상승, 강도 5단계)에 따라 청산 전략을 바꾸면 한 가지 청산을 계속 쓰는 것보다 나은가.

규칙·가설·판정은 docs/ai-history/2026-11-05-regime-exit-protocol.md (이 스크립트와 같은 커밋으로 동결).
결과를 보고 값을 바꾸지 않고 재실행하지 않는다. 세 구간 모두 다른 가설에 한 번씩 쓰인 자료라 '확증'이 아니라 '일관성 점검'이다.
  python study_regime_exit.py
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
CUTOFF_KST = '2026-11-03T00:00:00'            # 이후 자료는 앞으로의 그림자 확인용으로 남긴다
PERIODS = (('P1', None, '2024-11-02T16:30:00'), ('P2', '2024-11-02T16:30:00', '2026-05-04T00:00:00'), ('P3', '2026-05-04T00:00:00', None))
MIN_UNIVERSE = 20
RS_CAP = 0.15
WANT = 3
SCAN = 7
INDEX_BARS = 96                                # 24시간 균등지수(봇의 market_index와 같은 정의)
STAGE_EDGES = (-0.03, -0.01, 0.01, 0.03)       # 강하락 / 하락 / 보합 / 상승 / 강상승
STAGE_NAMES = ('강하락', '하락', '보합', '상승', '강상승')
REGIME_OF_STAGE = ('하락', '하락', '보합', '상승', '상승')
COSTS = (0.003, 0.0085)
LEVEL = 1 - 0.05 / 2                           # 2개 가설 Bonferroni 97.5%
BOOT = 2000
BLOCK_DAYS = 7
MAX_DAYS = 7
# 이름: dict(손절, 이익보호(시작, 반납) 또는 None, 익절 또는 None, 순위 이탈 기준 또는 None, 최대 보유 시간)
POLICIES = {
    'P0 현재': dict(stop=0.045, guard=(0.03, 0.30), tp=None, rank=9, hours=168),
    'D 방어': dict(stop=0.03, guard=(0.02, 0.30), tp=None, rank=7, hours=24),
    'S 스윙': dict(stop=0.035, guard=None, tp=0.03, rank=None, hours=48),
    'U 추세': dict(stop=0.045, guard=(0.05, 0.40), tp=None, rank=12, hours=72),
}
R3 = {'하락': 'D 방어', '보합': 'S 스윙', '상승': 'U 추세'}      # 사용자 제안(사전 고정)
OUT = sr.OUT_DIR / 'regime_exit_result.json'


def stage_of(idx):
    for i, e in enumerate(STAGE_EDGES):
        if idx < e:
            return i
    return len(STAGE_EDGES)


def hourly_state(data, t_start, t_end):
    """정시마다 ({종목: (rs, 순위)}, 유니버스 크기, 24시간 균등지수 또는 None)."""
    out = {}
    T = math.ceil(t_start / HOUR) * HOUR
    while T < t_end:
        uni = [m for m, _ in pit.universe_at(data, T, top_n=30)]
        rows, r24 = [], []
        for m in uni:
            bars = data[m]
            c0, c72, c96 = bars.get(T - B), bars.get(T - B - 72 * B), bars.get(T - B - INDEX_BARS * B)
            if c0 and c72:
                rows.append((m, c0[3] / c72[3] - 1))
            if c0 and c96:
                r24.append(c0[3] / c96[3] - 1)
        rows.sort(key=lambda x: (-x[1], x[0]))
        idx = sum(r24) / len(r24) if len(r24) >= MIN_UNIVERSE else None
        out[T] = ({m: (rs, i + 1) for i, (m, rs) in enumerate(rows)}, len(uni), idx)
        T += HOUR
    return out


def candidates(st, scan):
    top = sorted(((rank, m, rs) for m, (rs, rank) in st.items() if rank <= scan))
    return [(m, rs) for rank, m, rs in top if 0 < rs < RS_CAP][:WANT]


def simulate(bars, market, T, entry, pol, state):
    """T에 entry로 진입 → (총수익, 보유 시간, 사유). 한 봉 안에서는 손절 → 이익보호 → 익절 순으로 불리하게 먼저 본다."""
    stop = entry * (1 - pol['stop'])
    guard = pol['guard']
    tp = entry * (1 + pol['tp']) if pol['tp'] else None
    peak = entry
    end = T + pol['hours'] * HOUR
    s = T
    while s < end:
        b = bars.get(s)
        if b is not None:
            if b[2] <= stop:
                return min(stop, b[0]) / entry - 1, (s - T) / HOUR, 'stop'
            if guard and peak >= entry * (1 + guard[0]):
                gs = entry + (peak - entry) * (1 - guard[1])
                if b[2] <= gs:
                    return min(gs, b[0]) / entry - 1, (s - T) / HOUR, 'guard'
            if tp and b[1] >= tp:
                return max(tp, b[0]) / entry - 1, (s - T) / HOUR, 'tp'
            peak = max(peak, b[1])
        nxt = s + B
        if pol['rank'] is not None and nxt % HOUR == 0:
            snap = state.get(nxt)
            c = bars.get(s)
            if snap is not None and c is not None:
                info = snap[0].get(market)
                if info is None or info[1] > pol['rank'] or info[0] < 0:
                    return c[3] / entry - 1, (nxt - T) / HOUR, 'rank'
        s = nxt
    c = bars.get(end - B)
    if c is None:
        for k in range(1, 40):
            c = bars.get(end - B - k * B)
            if c:
                break
    return (c[3] / entry - 1, pol['hours'], 'time') if c else None


def paired_ci(rows):
    """rows: [(블록, 차이)]. 블록 부트스트랩으로 평균 차이의 LEVEL 구간."""
    g = defaultdict(list)
    for k, d in rows:
        g[k].append(d)
    ids = sorted(g)
    rng = random.Random(sr.SEED)
    ds = []
    for _ in range(BOOT):
        s = c = 0.0
        for _ in ids:
            v = g[rng.choice(ids)]
            s += sum(v)
            c += len(v)
        if c:
            ds.append(s / c)
    ds.sort()
    return ds[int((1 - LEVEL) / 2 * len(ds))], ds[int((1 + LEVEL) / 2 * len(ds)) - 1]


def choose_mapping(entries, train_periods):
    """학습 구간들에서 3장세별로 평균 총수익이 가장 높은 정책(동률은 정책 이름순)."""
    best = {}
    for reg in ('하락', '보합', '상승'):
        score = {}
        for name in POLICIES:
            v = [e['ret'][name] for e in entries if e['period'] in train_periods and e['regime'] == reg]
            if v:
                score[name] = sum(v) / len(v)
        best[reg] = sorted(score, key=lambda n: (-score[n], n))[0] if score else 'P0 현재'
    return best


def verdict(lo, d, per_period, net30, net85):
    consistent = all(x > 0 for x in per_period)
    if lo > 0 and consistent and net30 > 0 and net85 > 0:
        return '실거래 변경 제안 가능'
    if lo > 0 and consistent and net30 > 0:
        return '순수익 단서(0.85%에서는 음수)'
    if lo > 0 and consistent:
        return '상대 우위(순수익은 음수)'
    if lo > 0:
        return '풀링 우위이나 구간별 일관성 없음'
    return '불확실/근거 없음'


def main():
    cut = pit.to_epoch(CUTOFF_KST)
    data = pit.load_dir(EXT_DIR, cutoff=cut)
    data = {m: b for m, b in data.items() if b}
    allmin = min(min(b) for b in data.values())
    t_start = math.ceil((allmin + 120 * B) / sr.STEP) * sr.STEP
    last_entry = cut - MAX_DAYS * 24 * HOUR - 2 * HOUR
    print(f'진입 구간 {pit.from_epoch_kst(t_start)} ~ {pit.from_epoch_kst(last_entry)} · 종목 {len(data)}개', flush=True)
    state = hourly_state(data, t_start, cut)
    bounds = []
    for name, a, b in PERIODS:
        bounds.append((name, pit.to_epoch(a) if a else t_start, pit.to_epoch(b) if b else last_entry + 1))
    entries = []
    T = math.ceil(t_start / sr.STEP) * sr.STEP
    while T <= last_entry:
        snap = state.get(T)
        if snap and snap[1] >= MIN_UNIVERSE and snap[2] is not None:
            period = next((n for n, a, b in bounds if a <= T < b), None)
            st = stage_of(snap[2])
            for m, rs in candidates(snap[0], SCAN):
                c = data[m].get(T - B)
                if not c or period is None:
                    continue
                rets = {}
                for name, pol in POLICIES.items():
                    out = simulate(data[m], m, T, c[3], pol, state)
                    if out is None:
                        break
                    rets[name] = out
                else:
                    entries.append({'T': T, 'm': m, 'period': period, 'stage': st, 'regime': REGIME_OF_STAGE[st], 'idx': snap[2],
                                    'ret': {n: o[0] for n, o in rets.items()}, 'hold': {n: o[1] for n, o in rets.items()},
                                    'why': {n: o[2] for n, o in rets.items()}, 'block': (T - t_start) // (BLOCK_DAYS * 86400)})
        T += sr.STEP
    print('진입 표본', len(entries))
    res = {'n': len(entries), 'by_period': {}, 'matrix': {}, 'hypotheses': {}}
    for p, _, _ in PERIODS:
        res['by_period'][p] = sum(1 for e in entries if e['period'] == p)
    print('기간별 진입 수', res['by_period'])
    print('\n[설명] 장세 5단계별 진입 수와 정책별 평균 총수익 / 승률 / 손실 거래 평균 손실 / 평균 보유')
    for s in range(5):
        sub = [e for e in entries if e['stage'] == s]
        if not sub:
            continue
        row = {}
        for name in POLICIES:
            v = [e['ret'][name] for e in sub]
            losses = [x for x in v if x < 0]
            row[name] = {'mean': sum(v) / len(v), 'win': sum(1 for x in v if x > 0) / len(v), 'avg_loss': sum(losses) / len(losses) if losses else 0.0,
                         'p5': sorted(v)[int(0.05 * len(v))], 'hold_h': sum(e['hold'][name] for e in sub) / len(sub)}
        res['matrix'][STAGE_NAMES[s]] = {'n': len(sub), **row}
        print(f'  {STAGE_NAMES[s]} n={len(sub)}: ' + ' | '.join(f"{n} {r['mean']*100:+.2f}% 승{r['win']:.0%} 손실{r['avg_loss']*100:+.2f}% 보유{r['hold_h']:.0f}h" for n, r in row.items()))

    def pick(e, mapping):
        return e['ret'][mapping[e['regime']]]
    print('\n[H1] 사용자 제안 R3(하락 D·보합 S·상승 U, 사전 고정) vs P0 현재')
    diffs = [(e['block'], pick(e, R3) - e['ret']['P0 현재']) for e in entries]
    d = sum(x for _, x in diffs) / len(diffs)
    lo, hi = paired_ci(diffs)
    perp = []
    for p, _, _ in PERIODS:
        v = [pick(e, R3) - e['ret']['P0 현재'] for e in entries if e['period'] == p]
        perp.append(sum(v) / len(v) if v else 0.0)
    r3 = [pick(e, R3) for e in entries]
    net30, net85 = sum(r3) / len(r3) - COSTS[0], sum(r3) / len(r3) - COSTS[1]
    v1 = verdict(lo, d, perp, net30, net85)
    res['hypotheses']['H1'] = {'diff': d, 'ci': [lo, hi], 'per_period': perp, 'gross_mean': sum(r3) / len(r3), 'net_0.003': net30, 'net_0.0085': net85, 'verdict': v1,
                               'p0_gross_mean': sum(e['ret']['P0 현재'] for e in entries) / len(entries)}
    print(f'  차이 {d*100:+.3f}%p [{lo*100:+.3f}, {hi*100:+.3f}] · 구간별 {[round(x*100, 3) for x in perp]} · R3 순수익 0.30% {net30*100:+.3f}% / 0.85% {net85*100:+.3f}% → {v1}')
    print('\n[H2] 자료로 고른 장세별 정책(다른 두 구간에서 학습, 남은 구간에 적용) vs P0 현재')
    rows, perp2, maps = [], [], {}
    sel_all = []
    for p, _, _ in PERIODS:
        mp = choose_mapping(entries, {q for q, _, _ in PERIODS if q != p})
        maps[p] = mp
        sub = [e for e in entries if e['period'] == p]
        dd = [(e['block'], pick(e, mp) - e['ret']['P0 현재']) for e in sub]
        rows += dd
        perp2.append(sum(x for _, x in dd) / len(dd) if dd else 0.0)
        sel_all += [pick(e, mp) for e in sub]
        print(f'  {p} 학습 선택 {mp} → 차이 {perp2[-1]*100:+.3f}%p')
    d2 = sum(x for _, x in rows) / len(rows)
    lo2, hi2 = paired_ci(rows)
    n30, n85 = sum(sel_all) / len(sel_all) - COSTS[0], sum(sel_all) / len(sel_all) - COSTS[1]
    v2 = verdict(lo2, d2, perp2, n30, n85)
    res['hypotheses']['H2'] = {'diff': d2, 'ci': [lo2, hi2], 'per_period': perp2, 'mappings': maps, 'net_0.003': n30, 'net_0.0085': n85, 'verdict': v2}
    print(f'  합산 차이 {d2*100:+.3f}%p [{lo2*100:+.3f}, {hi2*100:+.3f}] · 순수익 0.30% {n30*100:+.3f}% / 0.85% {n85*100:+.3f}% → {v2}')
    print('\n[설명용] 하락 장세 진입을 아예 하지 않으면 평균 총수익 변화')
    skip = [0.0 if e['regime'] == '하락' else pick(e, R3) for e in entries]
    res['skip_down'] = {'gross_mean': sum(skip) / len(skip), 'share_down': sum(1 for e in entries if e['regime'] == '하락') / len(entries)}
    print(f"  하락 진입 비중 {res['skip_down']['share_down']:.0%} · R3 평균 {sum(r3)/len(r3)*100:+.3f}% → 하락 건너뛰기 평균 {sum(skip)/len(skip)*100:+.3f}% (건너뛴 거래는 비용도 없음)")
    sr.OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    print('저장:', OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
