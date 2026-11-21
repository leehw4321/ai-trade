"""검증 연구(2026-11-21 사용자 지시 '1의 경우를 매수·매도에 적용할 규칙을 스터디'): 손절 순간 차트가 '상승 추세 속 고변동 눌림'이면
손절을 미루거나(매도 규칙) 손절 뒤 반등 확인 재매수(매수 규칙)가 180일 과거 자료에서도 이득인가.

가설 출처: 실거래 손절 99건(11-02~11-20, docs/ai-history/2026-11-21-stoploss-after-study.md). 이 연구 자료(05-09~11-05)는 그와 거의 겹치지 않는다.
규칙·값은 실행 전에 아래로 고정했다. 결과를 보고 값을 바꿔 재실행하면 그 사실을 기록한다.

조건 ①(손절 순간, 손절 봉 시작 전에 마감된 봉만 사용): 15분 ATR(14봉)/종가 ≥ 1.5% 그리고 24시간(96봉) 수익률 ≥ +3%.
진입: study_regime_window.entries와 같은 방식(정시, 거래대금 상위 30 중 18h 상승률 순위 7위 안, 0<상승률<15%, 최대 3개).
기본 청산 B0(지금 실거래 근사): 손절 −3.5%(stop_max_pct), 이익보호 +3% 시작·30% 반납, 최대 72h. 한 봉 안에서는 불리한 쪽 먼저.
규칙(①일 때만 바뀜, ①이 아니면 B0와 같음):
  S1 버팀: 손절 대신 −10% 강제 손절선 + 매수가 +5% 익절, 손절 순간부터 최대 72h.
  S2 버팀: −10% 강제 손절선 + 기존 이익보호 유지(익절 없음), 최대 72h.
  S3 짧게 버팀: −8% 강제 손절선 + 매수가 +3% 익절, 최대 24h.
  R1 재매수: 손절은 그대로. 24h 안에 15분 종가가 손절 매도가 × 1.02 이상이면 다음 봉 시가에 다시 사고 B0 청산(1회).
  R2 재매수: 24h 안에 15분 종가가 처음 매수가 이상(본전 회복 확인)이면 다음 봉 시가에 재매수, B0 청산(1회).
대조(조건 의미 확인): S1all = 조건 없이 모든 손절에 S1. S1not = ①이 아닌 손절에만 S1.
지표: 진입 1건당 비용 후 평균(비용은 매매 1회 왕복마다 0.40%·0.85%), B0 대비 짝지은 차이, 7일 블록 부트스트랩 99% 구간(규칙 5개 Bonferroni),
세 구간 일관성. 한계: 생존 편향, 15분봉 해상도, 실제 손절은 ATR 기반이라 3.5%보다 가까운 경우가 있음, 미끄러짐은 비용으로만.
  python tools/research/study_stop_hold_rule.py
"""
import json, math, random, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pit_universe as pit  # noqa: E402
import study_regime_window as rw  # noqa: E402
import study_regime_exit as rx  # noqa: E402

B, HOUR = pit.BAR, 3600
STOP, GUARD, HOURS = 0.035, (0.03, 0.30), 72
ATR_MIN, R24_MIN = 0.015, 0.03
COSTS = (0.0040, 0.0085)
RULES = ('S1', 'S2', 'S3', 'R1', 'R2', 'S1all', 'S1not')
LEVEL = 0.99
BOOT, BLOCK_DAYS = 2000, 7
OUT = rw.DATA_DIR.parent / 'study_stop_hold_rule' / 'result.json'


def cond1(bars, s):
    """손절 봉 시작 s 이전 마감 봉으로 ① 판정. (참/거짓, atr%, r24) — 자료 부족이면 None."""
    past = [bars.get(s - k * B) for k in range(1, 16)]
    if any(x is None for x in past[:15]):
        return None
    tr = [max(b[1], p[3]) - min(b[2], p[3]) for b, p in zip(past[:14], past[1:15])]
    c = past[0][3]
    atr = sum(tr) / 14 / c
    old = next((bars.get(s - k * B) for k in range(97, 101) if bars.get(s - k * B)), None)
    if old is None:
        return None
    r24 = c / old[3] - 1
    return (atr >= ATR_MIN and r24 >= R24_MIN), atr, r24


def run_exit(bars, s, end, entry, stop, guard, tp, peak=None):
    """s부터 end 전까지 청산. 반환 (가격비, 끝난 봉 시작, 사유). 같은 봉에서 손절 → 보호 → 익절."""
    peak = entry if peak is None else peak
    last = None
    while s < end:
        b = bars.get(s)
        if b is not None:
            last = (b, s)
            if b[2] <= stop:
                return min(stop, b[0]) / entry, s, 'stop'
            if guard and peak >= entry * (1 + guard[0]):
                gs = entry + (peak - entry) * (1 - guard[1])
                if b[2] <= gs:
                    return min(gs, b[0]) / entry, s, 'guard'
            if tp and b[1] >= entry * (1 + tp):
                return max(entry * (1 + tp), b[0]) / entry, s, 'tp'
            peak = max(peak, b[1])
        s += B
    return (last[0][3] / entry, last[1], 'time') if last else None


def legs(bars, T, entry, rule):
    """진입 1건의 매매 목록 [(총수익, 사유)] 과 ① 정보."""
    end = T + HOURS * HOUR
    r = run_exit(bars, T, end, entry, entry * (1 - STOP), GUARD, None)
    if r is None:
        return None, None
    ratio, s, why = r
    if why != 'stop':
        return [(ratio - 1, why)], None
    c = cond1(bars, s)
    hit = bool(c and c[0])
    use = {'S1all': True, 'S1not': not hit}.get(rule, hit)
    if rule == 'B0' or not use:
        out = [(ratio - 1, 'stop')]
    elif rule in ('S1', 'S2', 'S3', 'S1all', 'S1not'):
        wide, tp, hrs, g = {'S1': (.10, .05, 72, None), 'S1all': (.10, .05, 72, None), 'S1not': (.10, .05, 72, None),
                            'S2': (.10, None, 72, GUARD), 'S3': (.08, .03, 24, None)}[rule]
        b = bars[s]
        if b[2] <= entry * (1 - wide):          # 같은 봉에서 강제선까지 빠짐
            return [(min(entry * (1 - wide), b[0]) / entry - 1, 'wide')], c
        r2 = run_exit(bars, s + B, s + hrs * HOUR, entry, entry * (1 - wide), g, tp, peak=max(entry, b[1]))
        out = [((r2[0] - 1) if r2 else (b[3] / entry - 1), 'hold-' + (r2[2] if r2 else 'none'))]
    else:                                         # R1/R2 재매수
        out = [(ratio - 1, 'stop')]
        sell = min(entry * (1 - STOP), bars[s][0])
        trig = sell * 1.02 if rule == 'R1' else entry
        k = s + B
        while k < s + 24 * HOUR:
            b = bars.get(k)
            if b is not None and b[3] >= trig:
                nb = next((k + j * B for j in range(1, 5) if bars.get(k + j * B)), None)
                if nb:
                    e2 = bars[nb][0]
                    r3 = run_exit(bars, nb, nb + HOURS * HOUR, e2, e2 * (1 - STOP), GUARD, None)
                    if r3:
                        out.append((r3[0] - 1, 're-' + r3[2]))
                break
            k += B
    return out, c


def net(ls, cost):
    return sum(g - cost for g, _ in ls)


def ci(diffs):
    blocks = {}
    for T, d in diffs:
        blocks.setdefault(T // (BLOCK_DAYS * 86400), []).append(d)
    keys = list(blocks); rnd = random.Random(7); bs = []
    for _ in range(BOOT):
        v = [d for k in (rnd.choice(keys) for _ in keys) for d in blocks[k]]
        bs.append(sum(v) / len(v))
    bs.sort(); a = (1 - LEVEL) / 2
    return bs[int(a * BOOT)], bs[int((1 - a) * BOOT) - 1]


def main():
    cut = pit.to_epoch(rw.CUTOFF_KST)
    data = {m: b for m, b in pit.load_dir(rw.DATA_DIR, cutoff=cut).items() if b}
    allmin = min(min(b) for b in data.values())
    t0 = math.ceil((allmin + 120 * B) / HOUR) * HOUR
    print(f'구간 {pit.from_epoch_kst(t0)} ~ {rw.CUTOFF_KST} · 종목 {len(data)}', flush=True)
    state = rw.hourly_state(data, t0 - HOUR, cut)
    rw.POL = dict(stop=STOP, guard=GUARD, tp=None, rank=None, hours=HOURS)
    E = rw.entries(data, state)
    print('진입', len(E), flush=True)
    span = (cut - t0) // 3
    rows = []
    for x in E:
        bars = data[x['m']]; entry = bars[x['T'] - B][3]
        base, c = legs(bars, x['T'], entry, 'B0')
        if base is None:
            continue
        row = {'T': x['T'], 'm': x['m'], 'idx24': x['idx'].get('24h'), 'B0': base, 'c': c}
        for r in RULES:
            row[r] = legs(bars, x['T'], entry, r)[0]
        rows.append(row)
    stops = [r for r in rows if r['B0'][0][1] == 'stop']
    hit = [r for r in stops if r['c'] and r['c'][0]]
    print(f'진입 {len(rows)} · 손절 {len(stops)} ({len(stops)/len(rows):.0%}) · 그중 ① {len(hit)} · 자료부족 {sum(1 for r in stops if r["c"] is None)}')
    res = {'entries': len(rows), 'stops': len(stops), 'cond1': len(hit), 'rules': {}}
    for cost in COSTS:
        b0 = sum(net(r['B0'], cost) for r in rows) / len(rows)
        print(f'\n비용 왕복 {cost:.2%} · B0 진입당 {b0*100:+.3f}%')
        for rule in RULES:
            d = [(r['T'], net(r[rule], cost) - net(r['B0'], cost)) for r in rows]
            m = sum(v for _, v in d) / len(d); lo, hi = ci(d)
            th = []
            for i in range(3):
                a, b = t0 + i * span, t0 + (i + 1) * span
                v = [dd for T, dd in d if a <= T < b]
                th.append(sum(v) / len(v) if v else 0)
            aff = [r for r in rows if r[rule] != r['B0']]
            affd = [net(r[rule], cost) - net(r['B0'], cost) for r in aff]
            verdict = '이득(구간>0)' if lo > 0 else ('손해(구간<0)' if hi < 0 else '불확실')
            print(f'  {rule:6} 진입당 차이 {m*100:+.3f}%p [{lo*100:+.3f}, {hi*100:+.3f}] {verdict} · 세 구간 '
                  + ' '.join(f'{v*100:+.3f}' for v in th)
                  + f' · 바뀐 건 {len(aff)} (건당 {sum(affd)/len(affd)*100 if aff else 0:+.2f}%p, 나아짐 {sum(v>0 for v in affd)})')
            res['rules'].setdefault(rule, {})[f'{cost}'] = {'diff': m, 'ci': [lo, hi], 'thirds': th, 'affected': len(aff),
                                                           'per_affected': sum(affd) / len(affd) if aff else 0}
    # ① 손절의 실제 이후 흐름(규칙 무관 참고)
    from collections import Counter
    for rule in ('S1', 'S2', 'S3'):
        print(f'  {rule} 버팀 결과 분포:', dict(Counter(r[rule][0][1] for r in hit)))
    print('  R1 재매수 발생:', sum(len(r['R1']) > 1 for r in hit), '/', len(hit), ' R2:', sum(len(r['R2']) > 1 for r in hit))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    print('저장:', OUT)


if __name__ == '__main__':
    main()
