"""기존 하락 흐름 조기 손절(손실 1%↑·5분 2연속·15분·1시간 하락)은 그대로 두고, 1시간이 하락이 아닐 때(SLX 같은 경우)
추가로 붙일 만한 조건 조사 (2026-11-28 사용자 요청, 분석만).

대상: 손절 3.5% 적용(11-11 09:45) 뒤 봇 매수 왕복. 5분봉(early_stop_sim 캐시)으로 보유 중 매 5분봉 마감을 다시 보며
 '기존 규칙 또는 추가 규칙' 중 먼저 맞는 봉 종가에 판 것(비용 0.15%)으로 계산 → 기존 규칙만 쓸 때와 비교.
추가 규칙은 모두 '손실 1%↑ + 5분 2연속↓ + 15분↓'(1시간 무관)에 조건 하나를 더한 것.
읽기 전용, 주문 없음.
"""
import datetime as dt, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis_20261111'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis_20261127'))
import stop035_review as R
import early_stop_sim as E

UTC = dt.timezone.utc


def btc_streaks(start, end):
    bars = E.candles('KRW-BTC', start, end)
    return {x[0]: x for x in E.series(bars)}


def main():
    trips, _, _ = R.load()
    trips = [t for t in trips if t['open'] >= R.CUT]
    start = min(t['open'] for t in trips) - dt.timedelta(hours=2); end = max(t['close'] for t in trips)
    btc = btc_streaks(start, end)
    base_sig = lambda x, e: x['c'] / e - 1 <= -0.01 and x['s5'] <= -2 and x['s15'] <= -1
    cur = lambda x, e: base_sig(x, e) and x['s60'] <= -1
    ADD = {
        '손실 1.5% 이상': lambda x, e: x['c'] / e - 1 <= -0.015,
        '손실 2% 이상': lambda x, e: x['c'] / e - 1 <= -0.02,
        '손실 2.5% 이상': lambda x, e: x['c'] / e - 1 <= -0.025,
        '5분 3연속 이상': lambda x, e: x['s5'] <= -3,
        '5분 4연속 이상': lambda x, e: x['s5'] <= -4,
        '15분 2연속 이상': lambda x, e: x['s15'] <= -2,
        '1시간 보합(0)': lambda x, e: x['s60'] == 0,
        '매수 뒤 한 번도 +0.5% 못 감': lambda x, e: x['maxg'] < 0.005,
        '매수 뒤 한 번도 +1% 못 감': lambda x, e: x['maxg'] < 0.01,
        '매수 뒤 고점에서 −3% 이상 내려옴': lambda x, e: x['dd'] <= -0.03,
        '매수 뒤 1시간 지남': lambda x, e: x['age'] >= 1,
        '매수 뒤 1.5시간 지남': lambda x, e: x['age'] >= 1.5,
        '매수 뒤 2시간 지남': lambda x, e: x['age'] >= 2,
        '매수 뒤 3시간 지남': lambda x, e: x['age'] >= 3,
        '매수 뒤 2시간 지남 + 1시간 보합 이하(0 또는 하락)': lambda x, e: x['age'] >= 2 and x['s60'] <= 0,
        '매수 뒤 4시간 지남': lambda x, e: x['age'] >= 4,
        '직전 1시간 −2% 이상 하락': lambda x, e: x['r1h'] <= -0.02,
        '직전 1시간 −3% 이상 하락': lambda x, e: x['r1h'] <= -0.03,
        'BTC 15분 하락': lambda x, e: x['b15'] is not None and x['b15'] <= -1,
        'BTC 1시간 하락': lambda x, e: x['b60'] is not None and x['b60'] <= -1,
        'BTC 5분 2연속 하락': lambda x, e: x['b5'] is not None and x['b5'] <= -2,
    }
    rows = []
    for t in trips:
        entry = t['bf'] / t['bq']; inv = t['cost'] * t['sq'] / t['bq']
        bars = E.candles(t['m'], t['open'] - dt.timedelta(hours=1, minutes=5), t['close'])
        ser = E.series(bars)
        pts, hi = [], entry
        closes = [(x[0], x[1]) for x in ser]
        for k, x in enumerate(ser):
            if not (t['open'] + dt.timedelta(minutes=5) <= x[0] < t['close'] - dt.timedelta(seconds=30)): continue
            hi = max(hi, x[1])
            prev = [c for tt, c in closes[:k + 1] if tt <= x[0] - dt.timedelta(hours=1)]
            b = btc.get(x[0])
            pts.append(dict(t=x[0], c=x[1], s5=x[2], s15=x[3], s60=x[4], maxg=hi / entry - 1, dd=x[1] / hi - 1,
                            age=(x[0] - t['open']).total_seconds() / 3600, r1h=(x[1] / prev[-1] - 1) if prev else 0,
                            b5=b[2] if b else None, b15=b[3] if b else None, b60=b[4] if b else None))
        rows.append((t, entry, inv, pts))
    mid = sorted(t['open'] for t, *_ in rows)[len(rows) // 2]

    def run(rule):
        tot = a = b = 0.; fires = 0
        for t, e, inv, pts in rows:
            hit = next((x for x in pts if rule(x, e)), None)
            if hit is None: continue
            fires += 1
            d = inv * (hit['c'] * (1 - 0.0015) / e - 1) - t['pnl']
            tot += d
            if t['open'] < mid: a += d
            else: b += d
        return fires, tot, a, b

    f0, d0, a0, b0 = run(cur)
    print(f'대상 왕복 {len(rows)}건. 기존 규칙만: 발동 {f0}건, 실제 대비 {d0:+,.0f}원 (앞 {a0:+,.0f} / 뒤 {b0:+,.0f})')
    fb, db, ab, bb = run(base_sig)
    print(f'참고 1시간 조건 없이(5분·15분만): 발동 {fb}건, 실제 대비 {db:+,.0f}원 (앞 {ab:+,.0f} / 뒤 {bb:+,.0f}) → 기존 대비 {db - d0:+,.0f}원')
    print('\n| 추가 조건(1시간 무관, 손실 1%·5분 2연속·15분 하락에 더함) | 늘어난 발동 | 기존 대비 추가 이득 | 앞 절반 | 뒤 절반 | 추가 발동 중 결국 이익으로 끝난 건 |')
    print('|---|---|---|---|---|---|')
    out = []
    for name, extra in ADD.items():
        rule = lambda x, e, extra=extra: cur(x, e) or (base_sig(x, e) and extra(x, e))
        f, d, a, b = run(rule)
        # 추가로 발동한 왕복(기존은 발동 안 했거나 더 늦게)의 이익 끝 수
        prof = 0; nadd = 0
        for t, e, inv, pts in rows:
            h1 = next((x for x in pts if cur(x, e)), None); h2 = next((x for x in pts if base_sig(x, e) and extra(x, e)), None)
            if h2 and (h1 is None or h2['t'] < h1['t']):
                nadd += 1; prof += t['pnl'] > 0
        out.append((d - d0, f'| {name} | +{nadd}건 | **{d - d0:+,.0f}원** | {a - a0:+,.0f} | {b - b0:+,.0f} | {prof}/{nadd} |'))
    for _, l in sorted(out, key=lambda x: -x[0]): print(l)


if __name__ == '__main__':
    main()
