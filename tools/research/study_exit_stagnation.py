"""study_exit_fills.py 출력으로 손실 경로·체크포인트·정체 지정가 탈출을 계산한다(읽기 전용, 탐색용).

2판(ChatGPT 2차 검산 반영):
- 보유 경로 = 첫 매수 체결 뒤 시작한 봉부터, 매도 체결 전에 끝난 봉까지(매수 전·매도 뒤 가격 섞임 방지). 매수·매도가 걸친 봉은 버린다.
- 지정가: 주문 시각 t0(15분 경계로 올림) 뒤에 시작해 실제 매도 전에 끝난 봉에서만 체결을 본다. 실제 매도가 일어난 봉은
  손절과 선후를 모르므로 '미체결'(불리)로 둔다. 조건에 '현재가(직전 완료 봉 종가) < 본전'을 넣었다.
- 체결 가정 4단계 민감도: 닿음 / 한 호가 위 통과 / 한 호가 위 + 봉 거래대금 ≥ 주문의 5배 / 두 호가 위 + 20배.
  호가 단위는 그 종목 봉 가격들 사이의 가장 작은 간격(자료에서 추정).
- 매도 뒤 24시간은 자료가 24시간 다 있을 때만 쓰고, 모자라면 따로 센다.
"""
import argparse, datetime as dt, json, math, statistics as S
from pathlib import Path

KST = dt.timezone(dt.timedelta(hours=9)); FEE = 0.0005; BAR = 900
f = lambda t: dt.datetime.fromtimestamp(t, KST).strftime('%m-%d %H:%M')
up = lambda t: math.ceil(t / BAR) * BAR


def tick_of(bars):
    v = sorted({round(x, 10) for b in bars for x in b[1:5]})
    d = [round(b - a, 10) for a, b in zip(v, v[1:]) if b - a > 1e-12]
    return min(d) if d else 0.0


def build(P):
    rows = []
    for p in P:
        q = sum(s['q'] for s in p['sells'])
        e = p['sold_cost'] / q; be = e / (1 - FEE)
        bars = p['bars']; tk = tick_of(bars)
        hold = [b for b in bars if b[0] >= up(p['open']) and b[0] + BAR <= p['close']]
        after = [b for b in bars if b[0] >= up(p['close'])]
        mfe = (max(b[2] for b in hold) / e - 1) if hold else None
        rows.append({**p, 'q': q, 'e': e, 'be': be, 'tick': tk, 'hold': hold, 'after': after, 'r': p['pnl'] / p['sold_cost'],
                     'hold_h': (p['close'] - p['open']) / 3600, 'mfe': mfe,
                     'ever_close_be': any(b[4] >= be for b in hold), 'ever_high_be': any(b[2] >= be for b in hold),
                     'post_24': (after[95][4] / p['exit_px'] - 1) if len(after) >= 96 else None,
                     'partial_before': [s['t'] for s in p['sells'][:-1]]})
    return rows


FILL = {'닿음': (0, 0), '한호가+통과': (1, 0), '한호가+대금5배': (1, 5), '두호가+대금20배': (2, 20)}


def sim(rows, T, m, k, rule, deadline=None):
    """(총손익, 해당, 체결, 개선합, 악화합)"""
    tot = n = hit = 0; better = worse = 0.
    ticks, mult = FILL[rule]
    for x in rows:
        t0 = up(x['open'] + T * 3600)
        if t0 >= x['close'] or any(s < t0 for s in x['partial_before']): tot += x['pnl']; continue
        pre = [b for b in x['hold'] if b[0] + BAR <= t0]
        if not pre: tot += x['pnl']; continue
        if max(b[2] for b in pre) / x['e'] - 1 >= m or pre[-1][4] >= x['be']: tot += x['pnl']; continue
        n += 1
        tk = x['tick'] or 0
        L = math.ceil(x['be'] * (1 + k) / tk - 1e-9) * tk if tk else x['be'] * (1 + k)
        need = L + ticks * tk; krw = x['q'] * L
        end = x['close'] if deadline is None else min(x['close'], t0 + deadline * 3600)
        ok = any(b[2] >= need and b[5] >= mult * krw for b in x['hold'] if b[0] >= t0 and b[0] + BAR <= end)
        v = x['q'] * L * (1 - FEE) - x['sold_cost'] if ok else x['pnl']
        hit += ok; d = v - x['pnl']; tot += v
        better += max(d, 0); worse += min(d, 0)
    return tot, n, hit, better, worse


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--inp', type=Path, default=Path(__file__).with_name('exit_study_out.json'))
    D = json.load(open(ap.parse_args().inp, encoding='utf-8')); rows = build(D['positions'])
    W = [x for x in rows if x['pnl'] > 0]; L = [x for x in rows if x['pnl'] <= 0]
    print(f"포지션 {len(rows)} · 이익 {len(W)} {sum(x['pnl'] for x in W):+,.0f} · 손실 {len(L)} {sum(x['pnl'] for x in L):+,.0f} · 합 {sum(x['pnl'] for x in rows):+,.0f}")
    co = [x for x in rows if x['carried_out'] > 0]; ci = [x for x in rows if x['carried_in'] > 0]
    cut = sum(1 for x in rows for s in x['sells'] if s['cut'])
    print(f"잔량 남기고 닫힘 {len(co)}건(이월 원가 {sum(x['carried_out'] for x in co):,.1f}원) · 이월받아 시작 {len(ci)}건 · 장부보다 많이 판 체결 {cut}건")
    nob = [x for x in L if not x['hold']]
    print(f"손실 {len(L)}건 중 완전한 보유 봉이 없음(30분 미만 보유 등) {len(nob)}건 → 경로 판정 불가로 따로 셈")
    Lh = [x for x in L if x['hold']]
    print(f"경로 판정 가능 손실 {len(Lh)}건: 보유 중 완료 봉 종가가 본전 이상인 적 없음 {sum(not x['ever_close_be'] for x in Lh)} · 고가조차 본전 미달 {sum(not x['ever_high_be'] for x in Lh)}")
    md = lambda v: S.median(v) if v else float('nan')
    for name, g in (('이익', W), ('손실', L)):
        g = [x for x in g if x['mfe'] is not None]
        print(f"[{name}] 경로 있는 {len(g)}건 · 보유 중앙 {md([x['hold_h'] for x in g]):.1f}h · MFE 중앙 {md([x['mfe'] for x in g])*100:.2f}%")
    s = [x for x in L if x['hold_h'] >= 4 and x['mfe'] is not None and x['mfe'] < 0.01]
    print('4h+ 보유 & MFE<1% 손실:', [x['m'][4:] for x in s], f"{sum(x['pnl'] for x in s):+,.0f}")
    p24 = [x for x in L if x['post_24'] is not None]
    print(f"손실 매도 뒤 24h: 자료 충분 {len(p24)}건(부족 {len(L)-len(p24)}) · +5% 초과 {sum(x['post_24']>.05 for x in p24)} · −5% 미만 {sum(x['post_24']<-.05 for x in p24)}")

    print('\n체크포인트: 약세(그때까지 완료 봉 최고 < +1%, 직전 봉 종가 < 본전) / 나머지, 그 시점 보유 중만')
    for mins in (30, 60, 120, 240, 360):
        g, h = [], []
        for x in rows:
            t0 = x['open'] + mins * 60
            if x['close'] <= t0: continue
            pre = [b for b in x['hold'] if b[0] + BAR <= t0]
            if not pre: continue
            (g if (max(b[2] for b in pre) / x['e'] - 1 < 0.01 and pre[-1][4] < x['be']) else h).append(x)
        w = lambda s: sum(x['pnl'] > 0 for x in s)
        print(f"{mins:4}분 약세 {len(g):2}건 중 최종 이익 {w(g):2} ({sum(x['pnl'] for x in g):+8,.0f}) | 나머지 {len(h):2}건 중 이익 {w(h):2} ({sum(x['pnl'] for x in h):+8,.0f})")

    print('\n고정 경로(청산 규칙과 무관): 체크포인트에 약세였던 포지션의 그 뒤 시장 가격. 본전 대비 종가 변화 중앙 / 그 구간 고가가 본전 이상인 비율')
    for mins in (60, 120, 240):
        cells = []
        for h in (1, 4, 24):
            ch = []; reach = []
            for x in rows:
                t0 = up(x['open'] + mins * 60)
                if x['close'] <= t0: continue
                pre = [b for b in x['hold'] if b[0] + BAR <= t0]
                if not pre or max(b[2] for b in pre) / x['e'] - 1 >= 0.01 or pre[-1][4] >= x['be']: continue
                win = [b for b in x['bars'] if t0 <= b[0] and b[0] + BAR <= t0 + h * 3600]
                if len(win) < h * 4: continue               # 자료가 그 시간만큼 다 있어야 센다
                ch.append(win[-1][4] / x['be'] - 1); reach.append(max(b[2] for b in win) >= x['be'])
            cells.append(f"+{h}h {len(ch):2}건 {md(ch)*100:+6.2f}% / 본전 회복 {sum(reach)}/{len(reach)}" if ch else f"+{h}h 자료 없음")
        print(f"{mins:4}분 약세 → " + ' · '.join(cells))

    base = sum(x['pnl'] for x in rows)
    print(f'\n정체 지정가 탈출 (실제 합 {base:+,.0f}). 개선=체결로 좋아진 합, 악화=나빠진 합. 기한 없음(GTC) / 2시간 기한')
    print(f"{'T':>3}{'m':>4}{'k':>6} {'가정':12}{'해당':>4}{'체결':>4}{'개선':>8}{'악화':>8} | 2h기한 체결{'개선':>8}")
    for T in (2, 4, 6):
        for m in (0.01,):
            for k in (0.0, 0.003):
                for rule in FILL:
                    _, n, hit, b, w_ = sim(rows, T, m, k, rule)
                    _, _, hit2, b2, _ = sim(rows, T, m, k, rule, deadline=2)
                    print(f"{T:3}{m*100:3.0f}%{k*100:5.1f}% {rule:12}{n:4}{hit:4}{b:+8,.0f}{w_:+8,.0f} | {hit2:4}{b2:+8,.0f}")

    print('\n11-05 21:00~11-06 01:00 매도')
    a = dt.datetime(2026, 11, 5, 21, tzinfo=KST).timestamp()
    for x in rows:
        if a <= x['close'] < a + 4 * 3600:
            p24 = f"{x['post_24']*100:+.2f}%" if x['post_24'] is not None else f"자료 {len(x['after'])/4:.1f}h뿐"
            print(x['m'], f(x['close']), f"{x['pnl']:+,.0f}원", '24h 뒤', p24)


if __name__ == '__main__':
    main()
