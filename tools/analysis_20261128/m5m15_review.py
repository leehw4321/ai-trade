"""'손실 1%↑ + 5분 2연속 하락 + 15분 하락'(1시간 무관) 신호가 뜬 실거래 왕복의 결과 (2026-11-28 사용자 요청, 분석만).

대상: 손절 3.5% 적용(11-11 09:45) 뒤 봇 매수 왕복. 각 왕복에서 위 신호가 처음 뜬 5분봉을 찾아
그때 1시간봉이 하락(지금 규칙 발동)이었는지 아닌지로 나누고, 실제로 어떻게 끝났는지(이익 / 3.5% 손절 / 그 밖 손실)를 센다.
또 신호 때 바로 팔았다면(종가, 비용 0.15%)과 실제 손익을 비교한다. 읽기 전용, 주문 없음.
"""
import datetime as dt, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis_20261111'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis_20261127'))
import stop035_review as R
import early_stop_sim as E


def main():
    trips, _, _ = R.load()
    trips = [t for t in trips if t['open'] >= R.CUT]
    groups = {'1시간 하락(지금 규칙 발동)': [], '1시간 하락 아님(지금 규칙은 기다림)': []}
    nosig = []
    for t in trips:
        entry = t['bf'] / t['bq']; inv = t['cost'] * t['sq'] / t['bq']
        bars = E.candles(t['m'], t['open'] - dt.timedelta(hours=1, minutes=5), t['close'])
        s = [x for x in E.series(bars) if t['open'] + dt.timedelta(minutes=5) <= x[0] < t['close'] - dt.timedelta(seconds=30)]
        hit = next((x for x in s if x[1] / entry - 1 <= -0.01 and x[2] <= -2 and x[3] <= -1), None)
        if not hit: nosig.append(t); continue
        later60 = next((x for x in s if x[0] >= hit[0] and x[1] / entry - 1 <= -0.01 and x[2] <= -2 and x[3] <= -1 and x[4] <= -1), None)
        w = ' '.join(t['why'])
        end = '이익' if t['pnl'] > 0 else ('3.5% 손절' if ('손절' in w and '조기' not in w) else '조기 손절' if '조기' in w else '그 밖 손실')
        sell_now = inv * (hit[1] * (1 - 0.0015) / entry - 1)
        g = '1시간 하락(지금 규칙 발동)' if hit[4] <= -1 else '1시간 하락 아님(지금 규칙은 기다림)'
        groups[g].append(dict(m=t['m'], open=t['open'], sig=hit[0], loss=hit[1] / entry - 1, end=end, pnl=t['pnl'], now=sell_now,
                              ret=t['ret'], later60=later60 is not None, hours=(t['close'] - hit[0]).total_seconds() / 3600))
    print(f'대상 왕복 {len(trips)}건, 신호 없음 {len(nosig)}건 (손익 {sum(t["pnl"] for t in nosig):+,.0f}원)')
    for g, xs in groups.items():
        if not xs: continue
        n = len(xs)
        print(f'\n## {g}: {n}건')
        for k in ('이익', '3.5% 손절', '조기 손절', '그 밖 손실'):
            v = [x for x in xs if x['end'] == k]
            if v: print(f"  {k}: {len(v)}건 ({len(v) / n:.0%}) 실제 손익 {sum(x['pnl'] for x in v):+,.0f}원, 신호 때 팔았다면 {sum(x['now'] for x in v):+,.0f}원")
        print(f"  합계: 실제 {sum(x['pnl'] for x in xs):+,.0f}원 / 신호 때 팔았다면 {sum(x['now'] for x in xs):+,.0f}원 → 차이 {sum(x['now'] - x['pnl'] for x in xs):+,.0f}원")
        if g.startswith('1시간 하락 아님'):
            lv = [x for x in xs if x['later60']]
            print(f"  기다리는 동안 1시간도 하락으로 바뀌어 지금 규칙이 결국 발동했을 건: {len(lv)}건")
            print('  이익으로 끝난 건:')
            for x in xs:
                if x['end'] == '이익':
                    print(f"    {x['open']:%m-%d %H:%M} {x['m'][4:]} 신호 {x['sig'].astimezone(R.KST):%H:%M}(그때 {x['loss']:+.1%}) → 최종 {x['ret']:+.2%} ({x['pnl']:+,.0f}원), 신호 후 {x['hours']:.1f}시간")


if __name__ == '__main__':
    main()
