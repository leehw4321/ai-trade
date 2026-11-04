"""탐색 연구(사전 선언): 장세(상승·보합·하락)별로 같은 모델·특징이 어떻게 달라지는가, 상승장의 유사 패턴.

규칙·정의·해석 기준은 docs/ai-history/2026-11-04-regime-study-protocol.md (이 스크립트와 같은 커밋으로 동결).
잠금 구간은 반전 연구에서 소진됐으므로 이 연구는 전부 '탐색'이다. 확인은 11-04 이후 새로 쌓이는 자료로만 한다.
  python study_regime.py
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pit_universe as pit
import study_reversal as sr

B = pit.BAR
UP, DOWN = 0.01, -0.01          # 24시간 시장 수익률 기준(균등 평균)
UP72, DOWN72 = 0.02, -0.02      # 강건성 변형: 72시간
K_NEIGHBORS = 50
TRAIN_FRACTION = 0.6            # 상승장 이벤트를 시간순으로 앞 60% 라이브러리 / 뒤 40% 테스트
OUT = sr.OUT_DIR / 'regime_result.json'


def add_regime(data, events):
    by_t = defaultdict(list)
    for e in events:
        by_t[e['T']].append(e)
    for T, rows in by_t.items():
        r24, r72 = [], []
        for e in rows:
            bars, s0 = data[e['m']], T - B
            c0, c24, c72 = (bars.get(s0), bars.get(s0 - 96 * B), bars.get(s0 - 288 * B))
            if c0 and c24:
                r24.append(c0[3] / c24[3] - 1)
            if c0 and c72:
                r72.append(c0[3] / c72[3] - 1)
        m24 = sum(r24) / len(r24) if len(r24) >= 15 else None
        m72 = sum(r72) / len(r72) if len(r72) >= 15 else None
        for e in rows:
            e['m24'], e['m72'] = m24, m72
    return events


def regime24(e):
    m = e.get('m24')
    return None if m is None else 'up' if m >= UP else 'down' if m <= DOWN else 'flat'


def regime72(e):
    m = e.get('m72')
    return None if m is None else 'up' if m >= UP72 else 'down' if m <= DOWN72 else 'flat'


def describe_regimes(events, fn, name):
    ts = {}
    for e in events:
        ts[e['T']] = fn(e)
    counts = defaultdict(int)
    months = defaultdict(lambda: defaultdict(int))
    days = defaultdict(set)
    for T, r in ts.items():
        if r is None:
            continue
        counts[r] += 1
        months[r][pit.from_epoch_kst(T)[:7]] += 1
        days[r].add(T // 86400)
    print(f'[{name}] 시점 수:', dict(counts), '· 해당 장세가 나타난 날 수:', {k: len(v) for k, v in days.items()})
    print('   월별 상승장 시점 수:', dict(sorted(months['up'].items())))


def stats_by_regime(events, fn, label):
    out = {}
    for reg in ('up', 'flat', 'down'):
        sub = [e for e in events if fn(e) == reg]
        if not sub:
            continue
        uni = sum(e['fwd'] for e in sub) / len(sub)
        hit = sum(1 for e in sub if e['fwd'] >= sr.LABEL_THR) / len(sub)
        print(f'  {label} {reg:>4s}: 이벤트 {len(sub)} · 유니버스 평균 순수익 {uni*100:+.3f}%/4h · 기본 정답률 {hit:.1%}')
        out[reg] = {'events': len(sub), 'universe_mean': uni, 'base_hit': hit}
        for model in ('current', 'reversal'):
            per_t, base = sr.evaluate(sub, model, None)
            out[reg][model] = sr.summarize(f'{model:8s}', per_t, base)
    return out


def spearman_by_t(events, feat, rng_filter):
    by_t = defaultdict(list)
    for e in events:
        if rng_filter(e):
            by_t[e['T']].append(e)
    ics = []
    for T, rows in by_t.items():
        if len(rows) < 15:
            continue
        x = np.array([e['rank'][feat] for e in rows])
        y = np.array([e['fwd'] for e in rows])
        ry = np.argsort(np.argsort(y)).astype(float)
        if np.std(x) == 0 or np.std(ry) == 0:
            continue
        ics.append(float(np.corrcoef(x, ry)[0, 1]))  # x는 이미 순위
    return ics


def ic_table(events, fn):
    print('\n[특징별 날짜 단위 순위상관 IC (4시간 뒤 순수익), 장세별] 평균 (t값) — 24개 비교이므로 |t|>3만 의미 있게 본다')
    table = {}
    for feat in sr.FEATS:
        line, row = f'  {feat:13s}', {}
        for reg in ('up', 'flat', 'down'):
            v = spearman_by_t(events, feat, lambda e, r=reg: fn(e) == r)
            if len(v) < 10:
                line += f' {reg}: n/a'
                continue
            m, s = float(np.mean(v)), float(np.std(v))
            t = m / (s / math.sqrt(len(v))) if s > 0 else 0.0
            line += f' | {reg} {m:+.3f} (t {t:+.1f}, n={len(v)})'
            row[reg] = {'ic': m, 't': t, 'n': len(v)}
        print(line)
        table[feat] = row
    return table


def quintiles(events, fn, reg):
    sub = [e for e in events if fn(e) == reg]
    print(f'\n[{reg}장 특징 5분위별 평균 순수익(4h) — Q1 낮은 순위 … Q5 높은 순위]')
    out = {}
    for feat in sr.FEATS:
        vals = sorted(sub, key=lambda e: e['rank'][feat])
        n = len(vals)
        q = [vals[i * n // 5:(i + 1) * n // 5] for i in range(5)]
        means = [sum(e['fwd'] for e in c) / len(c) for c in q]
        hits = [sum(1 for e in c if e['fwd'] >= sr.LABEL_THR) / len(c) for c in q]
        print(f'  {feat:13s} 평균 ' + ' '.join(f'{m*100:+.2f}%' for m in means) + ' | 정답률 ' + ' '.join(f'{h:.0%}' for h in hits))
        out[feat] = {'means': means, 'hits': hits}
    return out


def knn_test(events, fn):
    up = sorted([e for e in events if fn(e) == 'up'], key=lambda e: (e['T'], e['m']))
    ts = sorted({e['T'] for e in up})
    cut = ts[int(len(ts) * TRAIN_FRACTION)]
    train = [e for e in up if e['T'] < cut - sr.EMBARGO - sr.H * sr.B]
    test = [e for e in up if e['T'] >= cut]
    print(f'\n[상승장 유사 패턴(최근접 {K_NEIGHBORS}개) 시간 분할] 라이브러리 {len(train)}건(앞 60%), 테스트 {len(test)}건(뒤 40%), 경계 {pit.from_epoch_kst(cut)}')
    X = np.array([[e['rank'][k] for k in sr.FEATS] for e in train])
    y = np.array([e['fwd'] for e in train])
    Xt = np.array([[e['rank'][k] for k in sr.FEATS] for e in test])
    pred = np.empty(len(test))
    for i in range(0, len(test), 500):
        d = ((Xt[i:i + 500, None, :] - X[None, :, :]) ** 2).sum(axis=2)
        idx = np.argpartition(d, K_NEIGHBORS, axis=1)[:, :K_NEIGHBORS]
        pred[i:i + 500] = y[idx].mean(axis=1)
    for e, p in zip(test, pred):
        e['_knn'] = float(p)
    by_t = defaultdict(list)
    for e in test:
        by_t[e['T']].append(e)
    per_t = []
    for T in sorted(by_t):
        rows = by_t[T]
        picks = sorted(rows, key=lambda e: (-e['_knn'], e['m']))[:sr.TOP_K]
        per_t.append((T, sum(e['fwd'] for e in picks) / len(picks), sum(e['fwd'] for e in rows) / len(rows), len(picks),
                      sum(1 for e in picks if e['fwd'] >= sr.LABEL_THR)))
    base = sum(1 for e in test if e['fwd'] >= sr.LABEL_THR) / len(test)
    res = {'knn': sr.summarize('knn(유사 패턴)', per_t, base)}
    for model in ('current', 'reversal'):
        pt, b = sr.evaluate(test, model, None)
        res[model] = sr.summarize(f'{model:8s}', pt, b)
    corr = float(np.corrcoef(pred, [e['fwd'] for e in test])[0, 1])
    print(f'  예측값과 실제 4시간 순수익의 상관계수(테스트): {corr:+.3f}')
    res['pred_corr'] = corr
    return res


def main():
    data = pit.load_dir(sr.DATA_DIR, cutoff=pit.to_epoch(sr.CUTOFF_KST))
    t_start, dev_end, lock_end = sr.grid(data)
    print(f'전체 구간 {pit.from_epoch_kst(t_start)} ~ {pit.from_epoch_kst(lock_end)} (개발+소진된 잠금; 전부 탐색)')
    events = add_regime(data, sr.build_events(data, t_start, lock_end))
    print('이벤트', len(events))
    describe_regimes(events, regime24, '24시간 기준(±1%)')
    describe_regimes(events, regime72, '72시간 기준(±2%, 강건성)')
    result = {}
    print('\n[장세별 모델 결과: 24시간 기준]')
    result['by_regime24'] = stats_by_regime(events, regime24, '24h')
    print('\n[장세별 모델 결과: 72시간 기준(강건성)]')
    result['by_regime72'] = stats_by_regime(events, regime72, '72h')
    print('\n[기간 분할(개발 vs 이전 잠금 구간)에서 상승장(24h) 일관성]')
    for name, lo, hi in (('개발(앞 120일)', t_start, dev_end), ('이후 구간', dev_end, lock_end)):
        sub = [e for e in events if lo <= e['T'] < hi]
        result[f'split_{name}'] = stats_by_regime([e for e in sub if regime24(e) == 'up'], lambda e: 'up', name)
    result['ic24'] = ic_table(events, regime24)
    result['quintiles_up'] = quintiles(events, regime24, 'up')
    result['knn_up'] = knn_test(events, regime24)
    sr.OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    print('\n결과 저장:', OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
