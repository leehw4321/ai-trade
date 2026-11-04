"""사전 등록 재현 연구: 180일 탐색에서 나온 가설을 '한 번도 본 적 없는' 이전 자료(2년 수집 중 앞선 약 18개월)에서 검증한다.

규칙·가설·판정은 docs/ai-history/2026-11-04-replication-protocol.md (이 스크립트와 같은 커밋으로 동결).
이 스크립트는 결과를 보고 값을 바꾸지 않는다. '유의미할 때까지 반복'하지 않는다: 가설 5개, 한 번 실행, 다중비교 보정(99% 구간).
  python study_replicate.py
"""
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pit_universe as pit
import study_hold as sh
import study_regime as rg
import study_reversal as sr

B = pit.BAR
LONG_DIR = Path(r'C:\Users\User\Desktop\research_data\candles15_long')
SEEN_START = pit.to_epoch('2026-05-03T21:30:00')  # 앞선 탐색이 쓴 180일 자료의 첫 봉. 이 시각 이후 봉은 읽지 않는다.
LEVEL = 0.99   # 5개 가설 Bonferroni: 0.05/5 = 0.01
BOOT = 2000
OUT = sr.OUT_DIR / 'replication_result.json'


def block_ci(values, times, level=LEVEL, block_days=7):
    order = sorted(range(len(values)), key=lambda i: times[i])
    t0 = times[order[0]]
    blocks = defaultdict(list)
    for i in order:
        blocks[(times[i] - t0) // (block_days * 86400)].append(values[i])
    ids = list(blocks)
    rng = random.Random(sr.SEED)
    means = []
    for _ in range(BOOT):
        tot = cnt = 0
        for _ in ids:
            b = blocks[rng.choice(ids)]
            tot += sum(b)
            cnt += len(b)
        means.append(tot / cnt)
    means.sort()
    lo = means[int((1 - level) / 2 * BOOT)]
    hi = means[int((1 + level) / 2 * BOOT) - 1]
    return sum(values) / len(values), lo, hi


def ic_stats(events, feat):
    ics = rg.spearman_by_t(events, feat, lambda e: True)
    m, s = float(np.mean(ics)), float(np.std(ics))
    return m, (m / (s / math.sqrt(len(ics))) if s > 0 else 0.0), len(ics)


def main():
    cut = SEEN_START
    data = pit.load_dir(LONG_DIR, cutoff=cut)
    if not data:
        print('자료 없음:', LONG_DIR)
        return 1
    allmin = min(min(b) for b in data.values())
    t_start = math.ceil((allmin + 100 * B) / sr.STEP) * sr.STEP
    t_end = (cut - sr.H * B) // sr.STEP * sr.STEP
    print(f'재현 구간(처음 보는 자료) {pit.from_epoch_kst(t_start)} ~ {pit.from_epoch_kst(t_end)} · 종목 {len(data)}개 · 읽은 마지막 봉 마감 ≤ {pit.from_epoch_kst(cut)}')
    events = rg.add_regime(data, sr.build_events(data, t_start, t_end))
    ts = sorted({e['T'] for e in events})
    print('이벤트', len(events), '· 판단 시점', len(ts))
    result = {'period': [pit.from_epoch_kst(t_start), pit.from_epoch_kst(t_end)], 'events': len(events)}
    verdict = {}

    # ---- H1, H4: 선택 초과수익
    for key, model in (('H1', 'reversal'), ('H4', 'current')):
        per_t, base = sr.evaluate(events, model, None)
        exc = [p[1] - p[2] for p in per_t]
        net = [p[1] for p in per_t]
        m, lo, hi = block_ci(exc, [p[0] for p in per_t])
        mn, lon, hin = block_ci(net, [p[0] for p in per_t])
        uni = sum(p[2] for p in per_t) / len(per_t)
        print(f'[{key}] {model}: 초과 {m*100:+.3f}%p [99% {lo*100:+.3f}, {hi*100:+.3f}] · 평균 순수익 {mn*100:+.3f}% [{lon*100:+.3f}, {hin*100:+.3f}] · 유니버스 평균 {uni*100:+.3f}% · n시점 {len(per_t)}')
        result[key] = {'model': model, 'excess': m, 'excess_ci': [lo, hi], 'net': mn, 'net_ci': [lon, hin], 'universe': uni}
        verdict[key] = (lo > 0) if key == 'H1' else (not lo > 0)  # H4: 현재 기준이 유니버스보다 유의하게 좋지 않음

    # ---- H2, H3: IC
    ic = {f: ic_stats(events, f) for f in sr.FEATS}
    for f in sr.FEATS:
        print(f'  IC {f:13s} {ic[f][0]:+.4f} (t {ic[f][1]:+.1f}, n={ic[f][2]})')
    result['ic'] = {f: {'ic': v[0], 't': v[1], 'n': v[2]} for f, v in ic.items()}
    verdict['H2'] = ic['volat_4h'][0] < 0 and ic['volat_4h'][1] < -3
    verdict['H3'] = all(ic[f][0] < 0 and ic[f][1] < -3 for f in ('ret_4h', 'range_pos_4h'))

    # ---- H5: 보유 정책 (P3, P6 가 P1보다 나쁜가)
    for m_, b in data.items():
        sh._MKT[id(b)] = m_
    state = sh.hourly_state(data, t_start, cut)
    last_entry = cut - sh.MAX_DAYS * 24 * sh.HOUR - 2 * sh.HOUR
    entries = []
    T = math.ceil(t_start / sr.STEP) * sr.STEP
    while T <= last_entry:
        st = state.get(T)
        if st:
            cand = sorted(((m_, rs) for m_, (rs, rank) in st.items() if 0 < rs < 0.15), key=lambda x: (-x[1], x[0]))
            for m_, rs in cand[:sr.TOP_K]:
                c = data[m_].get(T - B)
                if c:
                    entries.append((T, m_, c[3]))
        T += sr.STEP
    print('보유 정책 진입', len(entries))
    res = {n: [] for n in sh.POLICIES}
    for T, m_, entry in entries:
        for n, (sp, mh, rk) in sh.POLICIES.items():
            out = sh.simulate(data[m_], T, entry, sp, mh, rk, state)
            res[n].append(None if out is None else (out[0] - sh.COST, out[1] and (out[1] - T) / sh.HOUR, out[2], T))
    ok = [i for i in range(len(entries)) if all(res[n][i] is not None for n in sh.POLICIES)]
    names = list(sh.POLICIES)
    base = names[0]
    hold = {}
    for n in names:
        vals = [res[n][i][0] for i in ok]
        times = [res[n][i][3] for i in ok]
        m, lo, hi = block_ci(vals, times)
        diff = [res[n][i][0] - res[base][i][0] for i in ok]
        dm, dlo, dhi = block_ci(diff, times)
        hold[n] = {'mean': m, 'ci': [lo, hi], 'vs_P1': dm, 'vs_P1_ci': [dlo, dhi]}
        print(f'  {n}: 평균 {m*100:+.3f}% [{lo*100:+.3f}, {hi*100:+.3f}] · P1 대비 {dm*100:+.3f}%p [{dlo*100:+.3f}, {dhi*100:+.3f}]')
    result['hold'] = hold
    p3, p6 = names[2], names[5]
    verdict['H5'] = hold[p3]['vs_P1_ci'][1] < 0 and hold[p6]['vs_P1_ci'][1] < 0

    # ---- H6 (설명용, 가설 검정 아님): 장세와 상승 구간
    print('\n[H6 설명용] 장세별 유니버스 평균 순수익/4h:')
    for reg in ('up', 'flat', 'down'):
        sub = [e for e in events if rg.regime24(e) == reg]
        if sub:
            print(f"  {reg:>4s}: 이벤트 {len(sub)} · 평균 {sum(e['fwd'] for e in sub)/len(sub)*100:+.3f}%")
    by_t = defaultdict(list)
    for e in events:
        by_t[e['T']].append(e['fwd'] + sr.COST)
    cum, months = 1.0, {}
    for T in sorted(by_t):
        cum *= 1 + sum(by_t[T]) / len(by_t[T])
        months[pit.from_epoch_kst(T)[:7]] = cum
    print('  상위30 균등 지수(비용 전, 시작=1.0) 월말:', {k: round(v, 2) for k, v in months.items()})
    result['index_by_month'] = months

    print('\n[가설별 재현 판정] (99% 구간, 5개 가설 보정; 결과에 따라 값을 바꾸지 않는다)')
    labels = {'H1': '반전 점수가 유니버스보다 유의하게 나음', 'H2': '변동성 높은 종목이 덜 오름(IC<0, t<-3)',
              'H3': '4시간 상승률·범위 상단 종목이 덜 오름(IC<0, t<-3, 둘 다)', 'H4': '현재 봇 진입 기준이 유니버스보다 좋지 않음(유의하게 낫지 않음)',
              'H5': '오래 보유(P3·P6)가 현재 방식보다 유의하게 나쁨'}
    for k in ('H1', 'H2', 'H3', 'H4', 'H5'):
        print(f'  {k} {labels[k]}: {"재현" if verdict[k] else "재현 안 됨"}')
    result['verdict'] = verdict
    sr.OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    print('저장:', OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
