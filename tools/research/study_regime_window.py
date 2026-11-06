"""탐색 연구(2026-11-06 사용자 질문): 장세 필터의 기간 비교 — 필터 없음 / 24h / 12h / 6h / 3h.

질문: '거래대금 상위 30 균등지수가 N시간 동안 0 이하이면 신규 매수 보류'가 통계적으로 근거가 있나, 아니면 '장이 나쁘니 쉬자' 수준인가.
짧은 기간(소나기만 피하기)이 더 나은가.

두 가지로 본다.
1) 진입 단위: 매시 후보(7위 안·0<18h 상승률<15%, 최대 4개)를 지금 실거래 청산(손절 4.5%·이익보호 +3%/30%, 최대 72h)으로
   하나씩 끝까지 따라간다. 같은 종목은 앞 진입이 끝나기 전 다시 넣지 않는다. 각 진입에 판단 시점의 3/6/12/24h 지수를 붙여
   '막혔을 진입(지수 ≤ 0)'과 '통과 진입'의 비용 후 평균 차이를 낸다. 신뢰구간은 7일 블록 부트스트랩, 4개 기간 Bonferroni(98.75%).
2) 포트폴리오: study_portfolio.run(4칸, 지금 실거래와 같은 V7 = 순위 매도 없음 + 본전 잠금 + 부분 교체 / 비교 V1)에
   지수 기간만 바꿔 넣는다. 180일 수익·세 구간·최대 낙폭.
자료: 회사 PC research_data/candles15(180일, 299종목, 시점별 유니버스). 한계: 생존 편향, 15분봉 해상도, 체결 미끄러짐은 비용으로만,
같은 자료로 기간을 고르면 탐색(확증 아님). 2026-11-05 이후 자료는 쓰지 않는다.
  python study_regime_window.py
"""
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pit_universe as pit
import study_regime_exit as rx
import study_portfolio as sp

B, HOUR = pit.BAR, 3600
DATA_DIR = Path(r'C:\Users\User\Desktop\research_data\candles15')
OUT = DATA_DIR.parent / 'study_regime_window' / 'result.json'
CUTOFF_KST = '2026-11-05T00:00:00'
WINDOWS = {'24h': 96, '12h': 48, '6h': 24, '3h': 12}
POL = dict(stop=0.045, guard=(0.03, 0.30), tp=None, rank=None, hours=72)
COSTS = (0.0040, 0.0085)
WANT, SCAN = 4, 7
BOOT, BLOCK_DAYS = 2000, 7
LEVEL = 1 - 0.05 / len(WINDOWS)


def hourly_state(data, t0, t1):
    """정시마다 ({종목: (rs, 순위)}, 유니버스 크기, {기간: 균등지수})."""
    out = {}
    T = math.ceil(t0 / HOUR) * HOUR
    while T < t1:
        uni = [m for m, _ in pit.universe_at(data, T, top_n=30)]
        rows, r = [], {k: [] for k in WINDOWS}
        for m in uni:
            bars = data[m]; c0 = bars.get(T - B)
            if not c0: continue
            c72 = bars.get(T - B - 72 * B)
            if c72: rows.append((m, c0[3] / c72[3] - 1))
            for k, n in WINDOWS.items():
                cn = bars.get(T - B - n * B)
                if cn: r[k].append(c0[3] / cn[3] - 1)
        rows.sort(key=lambda x: (-x[1], x[0]))
        idx = {k: (sum(v) / len(v) if len(v) >= rx.MIN_UNIVERSE else None) for k, v in r.items()}
        out[T] = ({m: (rs, i + 1) for i, (m, rs) in enumerate(rows)}, len(uni), idx)
        T += HOUR
    return out


def entries(data, state):
    out, busy = [], {}
    for T in sorted(state):
        ranks, n, idx = state[T]
        if n < rx.MIN_UNIVERSE: continue
        top = sorted((rank, m, rs) for m, (rs, rank) in ranks.items() if rank <= SCAN)
        for rank, m, rs in [x for x in top if 0 < x[2] < rx.RS_CAP][:WANT]:
            if busy.get(m, 0) > T or T - B not in data[m]: continue
            px = data[m][T - B][3]
            r = rx.simulate(data[m], m, T, px, POL, state)
            if r is None: continue
            busy[m] = T + int(math.ceil(r[1])) * HOUR + HOUR
            out.append({'T': T, 'm': m, 'gross': r[0], 'h': r[1], 'why': r[2], 'idx': idx})
    return out


def diff_ci(rows, key, cost, seed=7):
    """막힌 진입 평균 − 통과 진입 평균(비용 후)과 블록 부트스트랩 구간."""
    def stat(rs):
        a = [x['gross'] - cost for x in rs if x['idx'][key] is not None and x['idx'][key] > 0]
        b = [x['gross'] - cost for x in rs if x['idx'][key] is not None and x['idx'][key] <= 0]
        return (sum(b) / len(b) - sum(a) / len(a)) if a and b else None
    d = stat(rows)
    blocks = {}
    for x in rows: blocks.setdefault(x['T'] // (BLOCK_DAYS * 86400), []).append(x)
    keys = list(blocks); rnd = random.Random(seed); bs = []
    for _ in range(BOOT):
        s = [x for k in (rnd.choice(keys) for _ in keys) for x in blocks[k]]
        v = stat(s)
        if v is not None: bs.append(v)
    bs.sort(); a = (1 - LEVEL) / 2
    return d, bs[int(a * len(bs))], bs[int((1 - a) * len(bs)) - 1]


def main():
    cut = pit.to_epoch(CUTOFF_KST)
    data = {m: b for m, b in pit.load_dir(DATA_DIR, cutoff=cut).items() if b}
    allmin = min(min(b) for b in data.values())
    t0 = math.ceil((allmin + 120 * B) / HOUR) * HOUR
    print(f'구간 {pit.from_epoch_kst(t0)} ~ {CUTOFF_KST} · 종목 {len(data)}개', flush=True)
    state = hourly_state(data, t0 - HOUR, cut)
    E = entries(data, state)
    span = (cut - t0) // 3 // HOUR * HOUR
    thirds = [(t0 + i * span, t0 + (i + 1) * span) for i in range(3)]
    res = {'entries': len(E), 'windows': {}}
    print(f'\n[1] 진입 단위 {len(E)}건 (지금 실거래 청산, 최대 72h). 막힘 = 그 기간 지수 ≤ 0')
    allg = sum(x['gross'] for x in E) / len(E)
    print(f"  필터 없음: 평균 총수익 {allg*100:+.3f}% · 비용 0.40% 뒤 {(allg-.004)*100:+.3f}% · 0.85% 뒤 {(allg-.0085)*100:+.3f}%")
    for k in WINDOWS:
        ok = [x for x in E if x['idx'][k] is not None and x['idx'][k] > 0]
        bad = [x for x in E if x['idx'][k] is not None and x['idx'][k] <= 0]
        mo, mb = sum(x['gross'] for x in ok) / len(ok), sum(x['gross'] for x in bad) / len(bad)
        d, lo, hi = diff_ci(E, k, COSTS[0])
        per = []
        for a, b in thirds:
            o = [x['gross'] for x in ok if a <= x['T'] < b]; bb = [x['gross'] for x in bad if a <= x['T'] < b]
            per.append((sum(bb) / len(bb) - sum(o) / len(o)) if o and bb else None)
        res['windows'][k] = {'pass_n': len(ok), 'block_n': len(bad), 'pass_mean': mo, 'block_mean': mb, 'diff': d, 'ci': [lo, hi], 'thirds': per}
        sign = '근거 있음(구간이 0 아래)' if hi < 0 else ('반대(막힌 쪽이 나음)' if lo > 0 else '불확실(구간이 0 포함)')
        print(f"  {k:>3}: 통과 {len(ok):4}건 평균 {mo*100:+.3f}% | 막힘 {len(bad):4}건({len(bad)/len(E):.0%}) 평균 {mb*100:+.3f}% | "
              f"차이(막힘−통과) {d*100:+.3f}%p [{lo*100:+.3f}, {hi*100:+.3f}] {sign} | 세 구간 " + ' '.join(f'{p*100:+.2f}' if p is not None else '-' for p in per))
    print('\n[2] 4칸 포트폴리오 (study_portfolio.run, 1회 매수 = 평가액/4.75 자산 비례, 순위 매도 없음)')
    big = 1.0
    for fee in (0.002, 0.00425):
        sp.FEE = fee
        for vname, cfg in (('V1 지금(본전잠금 없음)', dict(rank=None, prop=True)), ('V2 +본전잠금', dict(rank=None, lock=True, prop=True))):
            for k in ('없음',) + tuple(WINDOWS):
                st = {T: (s_[0], s_[1], (big if k == '없음' else s_[2][k])) for T, s_ in state.items()}
                val, log = sp.run(data, st, t0, cut, cfg)
                parts = [sp.run(data, st, a, b, cfg)[0] / sp.START_CASH - 1 for a, b in thirds]
                peak, mdd = sp.START_CASH, 0.0
                for _, v in log['curve']:
                    peak = max(peak, v); mdd = max(mdd, 1 - v / peak)
                ret = val / sp.START_CASH - 1
                res.setdefault('portfolio', {}).setdefault(f'{vname} 편도{fee:.2%}', {})[k] = {'ret': ret, 'thirds': parts, 'mdd': mdd, 'buys': log['buys']}
                print(f"  편도 {fee:.2%} {vname[:10]:10} 필터 {k:>3}: 180일 {ret*100:+7.1f}% · 구간 " + ' '.join(f'{x*100:+6.1f}%' for x in parts)
                      + f" · 최대낙폭 {mdd*100:5.1f}% · 매수 {log['buys']}", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    print('저장:', OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
