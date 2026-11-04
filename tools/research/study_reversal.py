"""사전 등록 연구: '하락 후 반등' 후보 선행 단서가 4시간 뒤 비용 후 수익을 예측하는가.

규칙·정의·합격 기준은 docs/ai-history/2026-11-04-reversal-study-protocol.md 에 동결돼 있다(이 스크립트와 같은 커밋).
  python study_reversal.py dev                    개발 구간 워크포워드(앞 120일). 잠금 구간은 읽지 않는다.
  python study_reversal.py locked --open-locked   잠금 구간 최종 평가(1회만, 개발 결과가 게이트를 통과했을 때만)
모든 시도·지표를 출력하고 결과 JSON을 연구 자료 폴더에 저장한다. 실거래·주문과 무관하다.
"""
import argparse
import hashlib
import json
import math
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pit_universe as pit

B = pit.BAR
STEP = 4 * 3600          # 판단 시각 간격(4시간, 라벨이 겹치지 않음)
H = 16                   # 라벨 지평: 15분봉 16개 = 4시간
COST = 0.003             # 왕복 비용 0.30%
STRESS = 0.003           # 스트레스 비용 추가분(총 0.60%)
LABEL_THR = 0.01         # 비용 차감 후 +1% 이상이면 정답
TOP_K = 3
EMBARGO = 8 * 3600
LAMBDA = 1.0             # 로지스틱 L2(고정, 튜닝하지 않음)
BLOCK = 42               # 7일 = 42개 판단 시점
BOOT = 2000
SEED = 20261104
DATA_DIR = Path(r'C:\Users\User\Desktop\research_data\candles15')
OUT_DIR = DATA_DIR.parent / 'study_reversal'
CUTOFF_KST = '2026-11-04T08:15:00'
FEATS = ['ret_15m', 'ret_1h', 'ret_4h', 'rs_18h', 'range_pos_4h', 'dd_24h', 'vol_ratio', 'volat_4h']
REVERSAL_FEATS = ['ret_1h', 'ret_4h', 'range_pos_4h', 'dd_24h']


# ---------- 특징·라벨 (미래 봉 미사용: 특징은 s0 = T - BAR 에 마감된 봉까지) ----------
def _close(bars, s):
    b = bars.get(s)
    return b[3] if b else None


def features(bars, s0):
    c0 = _close(bars, s0)
    c15, c1h, c4h, c18h = (_close(bars, s0 - k * B) for k in (1, 4, 16, 72))
    if None in (c0, c15, c1h, c4h, c18h):
        return None
    win = [bars[s] for s in range(s0 - 15 * B, s0 + B, B) if s in bars]  # 최근 16봉
    day = [bars[s][1] for s in range(s0 - 95 * B, s0 + B, B) if s in bars]  # 최근 96봉 고가
    prev = sorted(bars[s][4] for s in range(s0 - 20 * B, s0, B) if s in bars)
    if len(win) < 12 or len(day) < 80 or len(prev) < 15:
        return None
    med = prev[len(prev) // 2] if len(prev) % 2 else (prev[len(prev) // 2 - 1] + prev[len(prev) // 2]) / 2
    if med <= 0:
        return None
    hi, lo = max(w[1] for w in win), min(w[2] for w in win)
    closes = [w[3] for w in win]
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    mu = sum(rets) / len(rets)
    vol = math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets))
    return {'ret_15m': c0 / c15 - 1, 'ret_1h': c0 / c1h - 1, 'ret_4h': c0 / c4h - 1, 'rs_18h': c0 / c18h - 1,
            'range_pos_4h': (c0 - lo) / (hi - lo) if hi > lo else 0.5, 'dd_24h': c0 / max(day) - 1,
            'vol_ratio': bars[s0][4] / med, 'volat_4h': vol}


def forward(bars, s0):
    c0, cf = _close(bars, s0), _close(bars, s0 + H * B)
    return cf / c0 - 1 - COST if c0 and cf else None


def ordinal_ranks(values, names):
    """-0.5..+0.5 의 순서 순위(동률은 종목 코드순으로 결정적)."""
    order = sorted(range(len(values)), key=lambda i: (values[i], names[i]))
    n = len(values)
    r = [0.0] * n
    for pos, i in enumerate(order):
        r[i] = pos / (n - 1) - 0.5 if n > 1 else 0.0
    return r


def build_events(data, t_start, t_end):
    """T in [t_start, t_end) 의 이벤트 행들: dict(T, m, raw, rank, fwd, rs)."""
    events = []
    T = t_start
    while T < t_end:
        uni = [m for m, _ in pit.universe_at(data, T, top_n=30)]
        rows = []
        for m in uni:
            f = features(data[m], T - B)
            y = forward(data[m], T - B)
            if f is not None and y is not None:
                rows.append((m, f, y))
        if len(rows) >= 15:
            ranks = {k: ordinal_ranks([r[1][k] for r in rows], [r[0] for r in rows]) for k in FEATS}
            for i, (m, f, y) in enumerate(rows):
                events.append({'T': T, 'm': m, 'raw': f, 'rank': {k: ranks[k][i] for k in FEATS}, 'fwd': y})
        T += STEP
    return events


# ---------- 모델 ----------
def fit_logistic(events):
    X = np.array([[1.0] + [e['rank'][k] for k in FEATS] for e in events])
    y = np.array([1.0 if e['fwd'] >= LABEL_THR else 0.0 for e in events])
    w = np.zeros(X.shape[1])
    pen = np.eye(X.shape[1]) * LAMBDA
    pen[0, 0] = 0.0  # 절편은 벌점 없음
    for _ in range(25):
        p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))
        W = np.clip(p * (1 - p), 1e-6, None)
        g = X.T @ (y - p) - pen @ w
        Hs = (X * W[:, None]).T @ X + pen
        step = np.linalg.solve(Hs, g)
        w = w + step
        if np.max(np.abs(step)) < 1e-8:
            break
    return w


def score_logistic(w, e):
    z = w[0] + sum(w[i + 1] * e['rank'][k] for i, k in enumerate(FEATS))
    return 1 / (1 + math.exp(-max(-30, min(30, z))))


def score_reversal(_, e):
    return -sum(e['rank'][k] for k in REVERSAL_FEATS) / len(REVERSAL_FEATS)


def pick(model, state, rows):
    """한 시점의 후보 rows 에서 최대 TOP_K 종목."""
    if model == 'current':  # 현재 봇 규칙 근사: 18h 상승률 0~15% 중 상위
        cand = [e for e in rows if 0 < e['raw']['rs_18h'] < 0.15]
        cand.sort(key=lambda e: (-e['raw']['rs_18h'], e['m']))
        return cand[:TOP_K]
    fn = score_reversal if model == 'reversal' else score_logistic
    ranked = sorted(rows, key=lambda e: (-fn(state, e), e['m']))
    return ranked[:TOP_K]


# ---------- 평가 ----------
def evaluate(events, model, state, extra_cost=0.0):
    by_t = {}
    for e in events:
        by_t.setdefault(e['T'], []).append(e)
    ts = sorted(by_t)
    per_t = []  # (T, 선택 평균, 유니버스 평균, 선택 수, 정답 수)
    base_hit = []
    for T in ts:
        rows = by_t[T]
        uni_mean = sum(e['fwd'] for e in rows) / len(rows) - extra_cost
        base_hit += [1.0 if e['fwd'] - extra_cost >= LABEL_THR else 0.0 for e in rows]
        picks = pick(model, state, rows)
        if picks:
            sel = [e['fwd'] - extra_cost for e in picks]
            per_t.append((T, sum(sel) / len(sel), uni_mean, len(sel), sum(1 for v in sel if v >= LABEL_THR)))
    return per_t, (sum(base_hit) / len(base_hit) if base_hit else float('nan'))


def block_bootstrap(values, seed=SEED):
    """이동 블록(7일) 부트스트랩 평균의 2.5·97.5 백분위."""
    n = len(values)
    if n == 0:
        return float('nan'), float('nan')
    rng = random.Random(seed)
    arr = values
    means = []
    nblocks = max(1, math.ceil(n / BLOCK))
    for _ in range(BOOT):
        total, cnt = 0.0, 0
        for _ in range(nblocks):
            s = rng.randrange(0, max(1, n - BLOCK + 1))
            chunk = arr[s:s + BLOCK]
            total += sum(chunk)
            cnt += len(chunk)
        means.append(total / cnt)
    means.sort()
    return means[int(0.025 * BOOT)], means[int(0.975 * BOOT) - 1]


def summarize(label, per_t, base_hit):
    if not per_t:
        print(f'  {label}: 선택 없음')
        return {'label': label, 'n_slots': 0}
    net = [p[1] for p in per_t]
    exc = [p[1] - p[2] for p in per_t]
    picks = sum(p[3] for p in per_t)
    hits = sum(p[4] for p in per_t)
    lo_n, hi_n = block_bootstrap(net)
    lo_e, hi_e = block_bootstrap(exc)
    r = {'label': label, 'n_slots': len(per_t), 'n_picks': picks, 'mean_net': sum(net) / len(net),
         'net_ci95': [lo_n, hi_n], 'mean_excess': sum(exc) / len(exc), 'excess_ci95': [lo_e, hi_e],
         'universe_mean': sum(p[2] for p in per_t) / len(per_t), 'hit_rate': hits / picks, 'base_hit_rate': base_hit}
    print(f"  {label}: 시점 {r['n_slots']} · 선택 {picks} · 평균 순수익 {r['mean_net']*100:+.3f}% [95% {lo_n*100:+.3f}, {hi_n*100:+.3f}] · "
          f"유니버스 평균 {r['universe_mean']*100:+.3f}% · 초과 {r['mean_excess']*100:+.3f}% [95% {lo_e*100:+.3f}, {hi_e*100:+.3f}] · "
          f"정답률 {r['hit_rate']:.1%} (기본 {base_hit:.1%})")
    return r


def grid(data):
    cut = pit.to_epoch(CUTOFF_KST)
    allmin = min(min(b) for b in data.values())
    t_start = math.ceil((allmin + 100 * B) / STEP) * STEP
    dev_end = (allmin + 120 * 86400) // STEP * STEP
    lock_end = (cut - H * B) // STEP * STEP + STEP  # T + 16봉 ≤ cut 인 마지막 T 까지
    return t_start, dev_end, lock_end


def run_dev():
    data = pit.load_dir(DATA_DIR, cutoff=pit.to_epoch(CUTOFF_KST))
    t_start, dev_end, _ = grid(data)
    print(f'개발 구간 {pit.from_epoch_kst(t_start)} ~ {pit.from_epoch_kst(dev_end)} (잠금 구간은 읽지 않음)')
    events = build_events(data, t_start, dev_end)
    print('개발 이벤트', len(events))
    folds = [(t_start + 60 * 86400, t_start + 90 * 86400), (t_start + 90 * 86400, dev_end)]
    results = {}
    for model in ('current', 'reversal', 'logistic'):
        all_per_t, base_hits = [], []
        for ts, te in folds:
            train = [e for e in events if e['T'] <= ts - (H * B) - EMBARGO]
            test = [e for e in events if ts <= e['T'] < te]
            state = fit_logistic(train) if model == 'logistic' else None
            per_t, base = evaluate(test, model, state)
            all_per_t += per_t
            base_hits.append(base)
        print(f'[{model}] 워크포워드 테스트 2폴드 합산')
        results[model] = summarize(model, all_per_t, sum(base_hits) / len(base_hits))
        results[model]['stress_mean_net'] = (lambda v: sum(v) / len(v))([p[1] - STRESS for p in all_per_t]) if all_per_t else None
    w = fit_logistic([e for e in events if e['T'] <= dev_end - (H * B) - EMBARGO])
    print('로지스틱 가중치(개발 전체 학습, 절편 + 특징):', {k: round(float(w[i + 1]), 3) for i, k in enumerate(FEATS)}, '절편', round(float(w[0]), 3))
    cand = max(('reversal', 'logistic'), key=lambda m: results[m].get('mean_excess', -9))
    gate = results[cand].get('excess_ci95', [-1, -1])[0] > 0
    out = {'candidate': cand, 'gate_open_locked': bool(gate), 'results': results,
           'weights': {k: float(w[i + 1]) for i, k in enumerate(FEATS)}, 'intercept': float(w[0]),
           'note': '게이트: 개발 테스트 합산 초과수익 95% 구간 하한 > 0 이어야 잠금 구간을 연다'}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / 'dev_result.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"\n후보 모델(개발 초과수익이 더 큰 쪽): {cand} · 잠금 구간 열기 게이트: {'통과' if gate else '불통과'} (초과수익 95% 하한 {results[cand]['excess_ci95'][0]*100:+.3f}%)")
    return 0


def run_locked():
    marker = OUT_DIR / 'locked_opened.json'
    dev = json.loads((OUT_DIR / 'dev_result.json').read_text(encoding='utf-8'))
    if marker.exists():
        print('잠금 구간은 이미 한 번 열었다. 다시 평가하지 않는다:', marker.read_text(encoding='utf-8')[:200])
        return 1
    if not dev['gate_open_locked']:
        print('개발 구간 게이트 불통과: 잠금 구간을 열지 않는다. 결론: 개발 구간에서 예측 우위를 찾지 못했다.')
        return 1
    data = pit.load_dir(DATA_DIR, cutoff=pit.to_epoch(CUTOFF_KST))
    t_start, dev_end, lock_end = grid(data)
    marker.write_text(json.dumps({'opened_at_epoch': int(__import__('time').time()), 'candidate': dev['candidate']}), encoding='utf-8')
    print(f'잠금 구간 {pit.from_epoch_kst(dev_end)} ~ {pit.from_epoch_kst(lock_end)} (1회 평가)')
    train = build_events(data, t_start, dev_end)
    train = [e for e in train if e['T'] <= dev_end - (H * B) - EMBARGO]
    test = build_events(data, dev_end, lock_end)
    cand = dev['candidate']
    state = fit_logistic(train) if cand == 'logistic' else None
    report = {'candidate': cand}
    for model in (cand, 'current'):
        st = state if model == cand else None
        per_t, base = evaluate(test, model, st)
        print(f'[{model}]')
        report[model] = summarize(model, per_t, base)
        if per_t:
            thirds = [per_t[i * len(per_t) // 3:(i + 1) * len(per_t) // 3] for i in range(3)]
            report[model]['third_means'] = [sum(p[1] for p in t) / len(t) for t in thirds]
            report[model]['stress_mean_net'] = sum(p[1] - STRESS for p in per_t) / len(per_t)
            print('  3등분 평균 순수익:', [f'{v*100:+.3f}%' for v in report[model]['third_means']], '· 스트레스(0.60%) 평균 순수익', f"{report[model]['stress_mean_net']*100:+.3f}%")
    r = report[cand]
    checks = {'a 선택 ≥100': r.get('n_picks', 0) >= 100,
              'b 평균 순수익>0 이고 95% 하한>0': r.get('mean_net', -1) > 0 and r['net_ci95'][0] > 0,
              'c 유니버스 대비 초과>0': r.get('mean_excess', -1) > 0,
              'd 3등분 중 2개 이상 순수익>0': sum(1 for v in r.get('third_means', []) if v > 0) >= 2,
              'e 스트레스 비용에서도 평균 순수익>0': r.get('stress_mean_net', -1) > 0}
    report['checks'] = checks
    report['passed'] = all(checks.values())
    for k, v in checks.items():
        print(f'  {k}: {"충족" if v else "미충족"}')
    print('최종:', '합격' if report['passed'] else '불합격 — 사전 등록한 조건에서 비용 후 양의 기대수익을 입증하지 못했다. 이 방향의 신규 실거래는 시작하지 않는다.')
    (OUT_DIR / 'locked_result.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['dev', 'locked'])
    ap.add_argument('--open-locked', action='store_true')
    a = ap.parse_args()
    if a.mode == 'locked':
        if not a.open_locked:
            print('잠금 구간 평가는 --open-locked 가 필요하다(1회만).')
            return 2
        return run_locked()
    return run_dev()


if __name__ == '__main__':
    sys.exit(main())
