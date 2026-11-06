"""탐색 연구(2026-11-06 사용자 지시): 호가 1칸이 큰 종목의 '급하지 않은 매도'를 매도 1호가 지정가로 하면 나아지나.

study_regime_window.entries(지금 실거래 청산)에서 호가 1칸 ≥ 0.3% 진입의 청산을 1분봉으로 다시 본다.
- 청산이 일어난 15분봉 안에서 1분봉 저가가 처음 청산선(손절·이익보호)에 닿은 분을 청산 시각, 그 선을 시장가 매도 가격 Pe로 본다.
  시간 만료 청산은 마지막 봉 마감 종가가 Pe.
- 지정가 = Pe + 1칸(매도 1호가 가정). 체결: 엄격 = 대기 중 1분봉 고가 > 지정가, 낙관 = 고가 ≥ 지정가.
- 매도는 포기할 수 없으므로 기한까지 안 팔리면 그때 1분봉 종가로 시장가 매도(전환).
- 결과 = 시장가 매도 대비 차이(가격 비율). 손절은 급한 매도라 참고로만 본다.
  python study_limit_exit.py
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pit_universe as pit
import study_regime_window as w
from study_exit_fills import candles
from study_exit_stagnation import tick
from study_limit_entry import boot, BIG, WAITS

B, HOUR = pit.BAR, 3600
OUT = w.DATA_DIR.parent / 'study_limit_exit'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cut = pit.to_epoch(w.CUTOFF_KST)
    data = {m: b for m, b in pit.load_dir(w.DATA_DIR, cutoff=cut).items() if b}
    allmin = min(min(b) for b in data.values())
    t0 = math.ceil((allmin + 120 * B) / HOUR) * HOUR
    E = w.entries(data, w.hourly_state(data, t0 - HOUR, cut))
    big = [x for x in E if tick(data[x['m']][x['T'] - B][3]) / data[x['m']][x['T'] - B][3] >= BIG]
    print(f'진입 {len(E)}건 · 호가 1칸 ≥ {BIG:.1%}: {len(big)}건', flush=True)
    cache_f = OUT / 'm1.json'
    cache = json.loads(cache_f.read_text(encoding='utf-8')) if cache_f.exists() else {}
    rows = []
    for i, x in enumerate(big):
        entry = data[x['m']][x['T'] - B][3]
        Pe = entry * (1 + x['gross'])
        if x['why'] == 'time':
            t_bar = x['T'] + int(x['h'] * HOUR)      # 시간 만료: 마지막 봉 마감 직후
            start = t_bar
        else:
            start = x['T'] + int(round(x['h'] * HOUR))   # 청산이 일어난 15분봉 시작
        k = f"{x['m']}|{start}"
        if k not in cache:
            cache[k] = candles(x['m'], start, start + 35 * 60, unit=1)
            if i % 100 == 0:
                cache_f.write_text(json.dumps(cache), encoding='utf-8'); print(f'  1분봉 {i}/{len(big)}', flush=True)
        m1 = cache[k]
        if x['why'] == 'time':
            te = start
        else:
            hit = [c for c in m1 if c[0] < start + B and c[3] <= Pe * (1 + 1e-9)]
            te = hit[0][0] + 60 if hit else start + B
        tk = tick(Pe); ask = Pe + tk
        r = {'T': x['T'], 'why': x['why'], 'save': ask / Pe - 1}
        for W in WAITS:
            win = [c for c in m1 if te <= c[0] < te + W * 60]
            r[f's{W}'] = any(c[2] > ask for c in win)
            r[f't{W}'] = any(c[2] >= ask for c in win)
            last = [c for c in m1 if c[0] + 60 <= te + W * 60]
            r[f'pw{W}'] = (last[-1][4] / Pe - 1) if last else 0.0
        rows.append(r)
    cache_f.write_text(json.dumps(cache), encoding='utf-8')
    res = {}
    for why in ('guard', 'time', 'stop'):
        rs = [r for r in rows if r['why'] == why]
        if not rs: continue
        print(f'\n[{why}] {len(rs)}건' + (' (급한 매도: 참고용)' if why == 'stop' else ''))
        for W in WAITS:
            for kind, lab in (('s', '엄격'), ('t', '낙관')):
                f = lambda s, W=W, kind=kind: sum(r['save'] if r[f'{kind}{W}'] else r[f'pw{W}'] for r in s) / len(s)
                fill = sum(r[f'{kind}{W}'] for r in rs) / len(rs)
                miss = [r[f'pw{W}'] for r in rs if not r[f'{kind}{W}']]
                d = f(rs); lo, hi = boot(rs, f)
                res.setdefault(why, {})[f'{W}m_{lab}'] = {'n': len(rs), 'fill': fill, 'diff': d, 'ci': [lo, hi],
                                                          'missed_move': sum(miss) / len(miss) if miss else None}
                print(f'  {W:2}분 {lab}: 체결 {fill:4.0%} | 못 판 경우 기한까지 가격 {(sum(miss)/len(miss)*100 if miss else 0):+.2f}% | '
                      f'지정가→시장가 − 시장가 {d*100:+.3f}%p [{lo*100:+.3f}, {hi*100:+.3f}]')
    (OUT / 'result.json').write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    print('저장:', OUT / 'result.json')


if __name__ == '__main__':
    main()
