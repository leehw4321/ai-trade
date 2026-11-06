"""탐색 연구(2026-11-06 사용자 지시 '측정'): 호가 1칸이 큰 종목의 매수호가 지정가 진입 vs 시장가.

질문: 호가 1칸/가격이 큰 후보를 시장가 대신 매수 1호가 지정가(post_only)로 사면, 놓친 거래까지 넣어 결과가 좋아지나.
진입·청산은 study_regime_window.entries(지금 실거래 청산: 손절 4.5%·이익보호 +3%/30%, 최대 72h)를 그대로 쓴다.

모형(한계 포함):
- 판단 시각 D = 봉 마감 T + 120초(봇은 약 1.5분 뒤 주문). 시장가 매수 가격 P = D 직전 1분봉 종가로 본다(실제는 매도 1호가라 P 또는 P+1칸).
- 지정가 = P − 1칸(매수 1호가로 가정). 체결 판정 두 가지: 엄격 = 대기 중 1분봉 저가가 지정가보다 낮음(줄 서 있던 주문은 반드시 체결),
  낙관 = 저가가 지정가 이하(닿기만 함).
- 지정가 체결 시 결과 = 시장가 결과 + (P/지정가 − 1). 청산 경로는 같다고 근사(손절가가 1칸 낮아지는 효과·늦은 체결 시점 무시).
- 놓치면 0(현금 유지). '포기' 방식. '전환' 방식은 기한에 가격이 P+1칸 이하면 그 가격에 시장가로 산다.
- 비용 0.40%(왕복 수수료+미끄러짐)는 모든 체결에 같게 뺀다. 1분봉은 거래가 있던 분만 있다.
정체 박스(챗 가설, 매수 이전 자료만): 직전 2시간 15분봉 종가 범위가 3칸 이하 또는 가격의 1% 이하이면 '정체'.
  python study_limit_entry.py
"""
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pit_universe as pit
import study_regime_window as w
from study_exit_fills import candles
from study_exit_stagnation import tick

B = pit.BAR
OUT = w.DATA_DIR.parent / 'study_limit_entry'
COST, BIG, WAITS = 0.004, 0.003, (3, 5, 15)


def boot(rows, f, n=2000, seed=7):
    blocks = {}
    for r in rows: blocks.setdefault(r['T'] // (7 * 86400), []).append(r)
    ks = list(blocks); rnd = random.Random(seed); bs = []
    for _ in range(n):
        v = f([x for k in (rnd.choice(ks) for _ in ks) for x in blocks[k]])
        if v is not None: bs.append(v)
    bs.sort()
    return bs[int(.025 * len(bs))], bs[int(.975 * len(bs)) - 1]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cut = pit.to_epoch(w.CUTOFF_KST)
    data = {m: b for m, b in pit.load_dir(w.DATA_DIR, cutoff=cut).items() if b}
    allmin = min(min(b) for b in data.values())
    t0 = math.ceil((allmin + 120 * B) / 3600) * 3600
    E = w.entries(data, w.hourly_state(data, t0 - 3600, cut))
    for x in E:
        bars = data[x['m']]; px = bars[x['T'] - B][3]; tk = tick(px)
        cl = [bars[x['T'] - i * B][3] for i in range(1, 9) if bars.get(x['T'] - i * B)]
        x.update(px=px, tk=tk / px, box=(max(cl) - min(cl) <= 3 * tk or (max(cl) - min(cl)) / px <= 0.01))
    print(f'진입 {len(E)}건 · 호가 1칸 ≥ {BIG:.1%}: {sum(x["tk"] >= BIG for x in E)}건', flush=True)

    cache_f = OUT / 'm1.json'
    cache = json.loads(cache_f.read_text(encoding='utf-8')) if cache_f.exists() else {}
    big = [x for x in E if x['tk'] >= BIG]
    for i, x in enumerate(big):
        k = f"{x['m']}|{x['T']}"
        if k not in cache:
            cache[k] = candles(x['m'], x['T'], x['T'] + 20 * 60, unit=1)
            if i % 100 == 0:
                cache_f.write_text(json.dumps(cache), encoding='utf-8'); print(f'  1분봉 {i}/{len(big)}', flush=True)
    cache_f.write_text(json.dumps(cache), encoding='utf-8')

    rows = []
    for x in big:
        m1 = cache.get(f"{x['m']}|{x['T']}") or []
        D = x['T'] + 120
        before = [c for c in m1 if c[0] + 60 <= D]
        P = before[-1][4] if before else x['px']
        tk = tick(P); bid = P - tk
        r = {'T': x['T'], 'm': x['m'], 'g': x['gross'], 'save': P / bid - 1, 'box': x['box']}
        for W in WAITS:
            win = [c for c in m1 if D - 60 < c[0] < D + W * 60]
            lows = [c[3] for c in win]
            r[f's{W}'] = any(l < bid for l in lows)
            r[f't{W}'] = any(l <= bid for l in lows)
            last = [c for c in m1 if c[0] + 60 <= D + W * 60]
            pw = last[-1][4] if last else P
            r[f'fb{W}'] = (pw / P - 1) if pw <= P + tk else None    # 전환 방식: 기한 가격(P 대비), 너무 오르면 None(안 삼)
        rows.append(r)

    res = {'n_all': len(E), 'n_big': len(rows), 'waits': {}}
    mk = lambda rs: sum(r['g'] - COST for r in rs) / len(rs)
    print(f'\n[1] 호가 1칸 ≥ {BIG:.1%} 진입 {len(rows)}건 · 진입당 평균(놓친 거래는 0) · 시장가 {mk(rows)*100:+.3f}%')
    for W in WAITS:
        for kind, lab in (('s', '엄격'), ('t', '낙관')):
            def lim(rs, W=W, kind=kind):
                return sum((r['g'] + r['save'] - COST) if r[f'{kind}{W}'] else 0 for r in rs) / len(rs)
            def fb(rs, W=W, kind=kind):
                v = 0.0
                for r in rs:
                    if r[f'{kind}{W}']: v += r['g'] + r['save'] - COST
                    elif r[f'fb{W}'] is not None: v += r['g'] - r[f'fb{W}'] - COST
                return v / len(rs)
            filled = [r for r in rows if r[f'{kind}{W}']]; miss = [r for r in rows if not r[f'{kind}{W}']]
            gf = sum(r['g'] for r in filled) / len(filled) if filled else float('nan')
            gm = sum(r['g'] for r in miss) / len(miss) if miss else float('nan')
            d = lim(rows) - mk(rows); lo, hi = boot(rows, lambda rs: lim(rs) - mk(rs))
            d2 = fb(rows) - mk(rows); lo2, hi2 = boot(rows, lambda rs: fb(rs) - mk(rs))
            res['waits'][f'{W}m_{lab}'] = {'fill': len(filled) / len(rows), 'g_filled': gf, 'g_missed': gm,
                                          'abandon_minus_market': d, 'ci': [lo, hi], 'fallback_minus_market': d2, 'ci_fb': [lo2, hi2]}
            print(f'  {W:2}분 {lab}: 체결 {len(filled)/len(rows):4.0%} | 시장가 결과(비용 전) 체결분 {gf*100:+.2f}% · 놓친분 {gm*100:+.2f}% | '
                  f'포기 − 시장가 {d*100:+.3f}%p [{lo*100:+.3f}, {hi*100:+.3f}] | 전환 − 시장가 {d2*100:+.3f}%p [{lo2*100:+.3f}, {hi2*100:+.3f}]')
    print(f'  평균 절감(체결 시) {sum(r["save"] for r in rows)/len(rows)*100:.2f}%')

    print('\n[2] 정체 박스 필터(직전 2시간 종가 범위 ≤ 3칸 또는 ≤ 1%) · 막힘 − 통과, 비용 전 평균')
    for name, rs in (('전체', E), (f'호가 1칸 ≥ {BIG:.1%}', [x for x in E if x['tk'] >= BIG])):
        def diff(s):
            a = [x['gross'] for x in s if x['box']]; b = [x['gross'] for x in s if not x['box']]
            return (sum(a) / len(a) - sum(b) / len(b)) if a and b else None
        d = diff(rs); lo, hi = boot(rs, diff)
        nb = sum(x['box'] for x in rs)
        res.setdefault('box', {})[name] = {'blocked': nb, 'n': len(rs), 'diff': d, 'ci': [lo, hi]}
        print(f'  {name}: 막힘 {nb}/{len(rs)}건 · 차이 {d*100:+.3f}%p [{lo*100:+.3f}, {hi*100:+.3f}]')
    (OUT / 'result.json').write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    print('저장:', OUT / 'result.json')


if __name__ == '__main__':
    main()
