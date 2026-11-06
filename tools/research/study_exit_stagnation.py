"""study_exit_fills.py 출력으로 손실 경로·체크포인트·정체 지정가 탈출을 계산한다(읽기 전용, 탐색용).

2판(ChatGPT 2차 검산): 매수 전·매도 뒤 봉 제외, 지정가는 주문 뒤 시작·실제 매도 전 종료 봉에서만, 실제 매도 봉은 미체결(불리),
'현재가 < 본전' 조건, 체결 가정 민감도, 24시간 자료 부족 구분.
3판(ChatGPT 4차 검산):
- 호가 단위 = 업비트 공식 원화 마켓 표(2025-07-31 기준, docs.upbit.com/kr/docs/krw-market-info). 주문 가격에서 정한다.
  (2024-10-14 16개 종목 100~1,000원 예외는 종목 목록이 없어 반영 못 함 → 한계로 적는다.)
- 최고수익: 매수가 걸친 15분봉 안은 매수 뒤 1분봉으로 본다. 1분봉도 없으면 '경계 불확실'로 따로 센다.
- 단순화한 절차 재생(ChatGPT 5차: '전체 절차'라 부르지 않는다): t0 본전+k 지정가(2시간) → 미체결이면 18h 상승률 ≤ 0일 때 매수 1호가 매도(다음 봉 시가 − 1호가로 가정),
  아니면 취소하고 실제 결과. 손절(실제 매도)이 먼저 오면 실제 결과. 순위 15위 밖 조건은 과거 순위 자료가 없어 빠졌다.
- 매도 뒤 24시간은 봉 개수가 아니라 시각으로(매도+24h 직전 1시간 안의 마지막 봉).
"""
import argparse, datetime as dt, json, math, statistics as S
from pathlib import Path

KST = dt.timezone(dt.timedelta(hours=9)); FEE = 0.0005; BAR = 900
f = lambda t: dt.datetime.fromtimestamp(t, KST).strftime('%m-%d %H:%M')
up = lambda t: math.ceil(t / BAR) * BAR
md = lambda v: S.median(v) if v else float('nan')
TABLE = [(2_000_000, 1000), (1_000_000, 1000), (500_000, 500), (100_000, 100), (50_000, 50), (10_000, 10), (5_000, 5),
         (1_000, 1), (100, 1), (10, .1), (1, .01), (.1, .001), (.01, .0001), (.001, .00001), (.0001, .000001), (.00001, .0000001)]


def tick(px):
    for lo, t in TABLE:
        if px >= lo: return t
    return 1e-8


def ceil_tick(px):
    t = tick(px); return round(math.ceil(px / t - 1e-9) * t, 10)


def at_or_before(bars, ts, within=3600):
    c = [b for b in bars if b[0] + BAR <= ts and b[0] + BAR > ts - within]
    return c[-1] if c else None


def build(P):
    rows = []
    for p in P:
        q = sum(s['q'] for s in p['sells'])
        e = p['sold_cost'] / q; be = e / (1 - FEE)
        bars = p['bars']
        hold = [b for b in bars if b[0] >= up(p['open']) and b[0] + BAR <= p['close']]
        early = [b for b in p.get('entry_minutes', []) if b[0] >= math.ceil(p['open'] / 60) * 60 and b[0] + 60 <= min(p['close'], up(p['open']))]
        highs = [b[2] for b in hold] + [b[2] for b in early]
        main_close = max(s['t'] for s in p['sells'] if not s.get('residual'))
        end = at_or_before(bars, main_close + 24 * 3600)
        rows.append({**p, 'q': q, 'e': e, 'be': be, 'hold': hold, 'early': early, 'r': p['pnl'] / p['sold_cost'],
                     'hold_h': (p['close'] - p['open']) / 3600, 'mfe': (max(highs) / e - 1) if highs else None,
                     'entry_bar_gap': not early and up(p['open']) > p['open'],      # 매수 봉 안 가격을 못 봄
                     'ever_close_be': any(b[4] >= be for b in hold), 'ever_high_be': any(h >= be for h in highs),
                     'post_24': (end[4] / p['exit_px'] - 1) if end and main_close + 24 * 3600 <= bars[-1][0] + BAR else None,
                     'partial_before': [s['t'] for s in p['sells'] if s['t'] < p['close']]})
    return rows


def weak_at(x, t0, m=0.01):
    """t0 시점 약세 여부: (그때까지 관측 최고 < +m) 그리고 (직전 완료 봉 종가 < 본전). 관측 불가면 None."""
    pre = [b for b in x['hold'] if b[0] + BAR <= t0]
    if not pre: return None
    highs = [b[2] for b in pre] + [b[2] for b in x['early']]
    return max(highs) / x['e'] - 1 < m and pre[-1][4] < x['be']


FILL = {'닿음': 0, '한호가위': 1, '두호가위': 2}


STAGE2 = {'다음봉시가-1호가': lambda b: b[1] - tick(b[1]), '다음봉저가(최악)': lambda b: b[3], '2단계없음': None}


def sim(rows, T, k, rule, full=True, s2='다음봉시가-1호가', skip_gap=False):
    """단순화한 절차. (총손익, 해당, 1단계 체결, 2단계 매도, 개선합, 악화합)"""
    if STAGE2[s2] is None: full = False
    tot = n = h1 = h2 = 0; better = worse = 0.
    for x in rows:
        t0 = up(x['open'] + T * 3600)
        if t0 >= x['close'] or any(s < t0 for s in x['partial_before']) or not weak_at(x, t0) or (skip_gap and x['entry_bar_gap']):
            tot += x['pnl']; continue
        n += 1; L = ceil_tick(x['be'] * (1 + k)); need = L + FILL[rule] * tick(L)
        dl = t0 + 2 * 3600
        ok = any(b[2] >= need for b in x['hold'] if b[0] >= t0 and b[0] + BAR <= min(x['close'], dl))
        v = x['pnl']
        if ok:
            v = x['q'] * L * (1 - FEE) - x['sold_cost']; h1 += 1
        elif full and dl < x['close']:
            prev = at_or_before(x['bars'], dl, within=BAR); base = at_or_before(x['bars'], dl - 18 * 3600, within=BAR)
            nxt = [b for b in x['hold'] if b[0] >= dl]
            if prev and base and nxt and prev[4] / base[4] - 1 <= 0:      # 근거 약화(18h 상승률 ≤ 0) → 매수 1호가 매도
                px = STAGE2[s2](nxt[0])
                v = x['q'] * px * (1 - FEE) - x['sold_cost']; h2 += 1
        d = v - x['pnl']; tot += v; better += max(d, 0); worse += min(d, 0)
    return tot, n, h1, h2, better, worse


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--inp', type=Path, default=Path(__file__).with_name('exit_study_out.json'))
    D = json.load(open(ap.parse_args().inp, encoding='utf-8')); rows = build(D['positions'])
    W = [x for x in rows if x['pnl'] > 0]; L = [x for x in rows if x['pnl'] <= 0]
    print(f"포지션 {len(rows)} · 이익 {len(W)} {sum(x['pnl'] for x in W):+,.0f} · 손실 {len(L)} {sum(x['pnl'] for x in L):+,.0f} · 합 {sum(x['pnl'] for x in rows):+,.0f}")
    rs = [x for x in rows if x['residual']]
    print(f"잔량 남기고 닫힘 {sum(x['carried_out'] > 0 for x in rows)}건 · 그 잔량을 나중에 판 포지션 {len(rs)}건 {[x['m'][4:] for x in rs]} 매도대금 {sum(x['residual'] for x in rs):,.0f}원")
    gap = [x for x in rows if x['entry_bar_gap']]
    print(f"매수 봉 안 1분봉 없음(경계 불확실) {len(gap)}건")
    Lh = [x for x in L if x['mfe'] is not None]
    Lc = [x for x in L if x['hold']]
    print(f"손실 {len(L)}건 · 완료 15분봉이 있는 {len(Lc)}건 중 그 종가가 본전 이상인 적 없음 {sum(not x['ever_close_be'] for x in Lc)} · "
          f"관측 가격(15분봉+매수 봉 1분봉)이 있는 {len(Lh)}건 중 고가도 본전 미달 {sum(not x['ever_high_be'] for x in Lh)}")
    for name, g in (('이익', W), ('손실', L)):
        g = [x for x in g if x['mfe'] is not None]
        print(f"[{name}] 관측 {len(g)}건 · 보유 중앙 {md([x['hold_h'] for x in g]):.1f}h · 관측 MFE 중앙 {md([x['mfe'] for x in g])*100:.2f}%")
    s = [x for x in L if x['hold_h'] >= 4 and x['mfe'] is not None and x['mfe'] < 0.01]
    print('4h+ 보유 & 관측 MFE<1% 손실:', [x['m'][4:] for x in s], f"{sum(x['pnl'] for x in s):+,.0f}")
    p24 = [x for x in L if x['post_24'] is not None]
    print(f"손실 매도 뒤 정확히 24h(시각 기준): 자료 충분 {len(p24)}건(부족 {len(L)-len(p24)}) · +5% 초과 {sum(x['post_24']>.05 for x in p24)} · −5% 미만 {sum(x['post_24']<-.05 for x in p24)}")

    print('\n체크포인트(관측한 완전 봉 + 매수 봉 1분봉 기준): 약세 / 나머지, 그 시점 보유 중만')
    for mins in (30, 60, 120, 240, 360):
        g, h = [], []
        for x in rows:
            t0 = x['open'] + mins * 60
            if x['close'] <= t0: continue
            w = weak_at(x, t0)
            if w is None: continue
            (g if w else h).append(x)
        win = lambda s: sum(x['pnl'] > 0 for x in s)
        print(f"{mins:4}분 약세 {len(g):2}건 중 최종 이익 {win(g):2} ({sum(x['pnl'] for x in g):+8,.0f}) | 나머지 {len(h):2}건 중 이익 {win(h):2} ({sum(x['pnl'] for x in h):+8,.0f})")

    print('\n고정 경로(청산 규칙과 무관, 시각 기준): 약세 판정 뒤 종가(본전 대비) 중앙 / 그 구간 고가의 본전 회복')
    for mins in (60, 120, 240):
        cells = []
        for hh in (1, 4, 24):
            ch, reach = [], []
            for x in rows:
                t0 = up(x['open'] + mins * 60)
                if x['close'] <= t0 or not weak_at(x, t0): continue
                end = at_or_before(x['bars'], t0 + hh * 3600, within=BAR)
                if not end or t0 + hh * 3600 > x['bars'][-1][0] + BAR: continue
                win_ = [b for b in x['bars'] if t0 <= b[0] and b[0] + BAR <= t0 + hh * 3600]
                ch.append(end[4] / x['be'] - 1); reach.append(max(b[2] for b in win_) >= x['be'])
            cells.append(f"+{hh}h {len(ch):2}건 {md(ch)*100:+6.2f}% / 회복 {sum(reach)}/{len(reach)}" if ch else f"+{hh}h 없음")
        print(f"{mins:4}분 약세 → " + ' · '.join(cells))

    base = sum(x['pnl'] for x in rows)
    print('\n민감도: T=2h·k=0·한 호가 위 / 2단계 체결 가정 × 매수 경계 불확실 거래 포함·제외')
    for s2 in STAGE2:
        for sk in (False, True):
            tot, n, a1, a2, b, w = sim(rows, 2, 0.0, '한호가위', s2=s2, skip_gap=sk)
            print(f"  {s2:12} {'불확실 제외' if sk else '불확실 포함'}: 해당 {n} · 1단계 {a1} · 2단계 {a2} · 순변화 {tot-base:+,.0f} (악화 {w:+,.0f})")
    print(f'\n정체 탈출 단순화한 절차 (실제 합 {base:+,.0f}). 1단계=본전+k 지정가 2시간, 2단계=미체결 & 18h 상승률≤0이면 매수 1호가 매도')
    print(f"{'T':>3}{'k':>6} {'체결가정':8}{'해당':>4}{'1단계':>5}{'2단계':>5}{'개선':>9}{'악화':>9}{'순변화':>9} | 1단계만 순변화")
    for T in (2, 4):
        for k in (0.0, 0.003):
            for rule in FILL:
                tot, n, a1, a2, b, w = sim(rows, T, k, rule)
                tot1 = sim(rows, T, k, rule, full=False)[0]
                print(f"{T:3}{k*100:5.1f}% {rule:8}{n:4}{a1:5}{a2:5}{b:+9,.0f}{w:+9,.0f}{tot-base:+9,.0f} | {tot1-base:+9,.0f}")

    print('\n11-05 21:00~11-06 01:00 매도')
    a = dt.datetime(2026, 11, 5, 21, tzinfo=KST).timestamp()
    for x in rows:
        if a <= x['close'] < a + 4 * 3600:
            print(x['m'], f(x['close']), f"{x['pnl']:+,.0f}원", '24h 뒤', f"{x['post_24']*100:+.2f}%" if x['post_24'] is not None else '자료 부족')


if __name__ == '__main__':
    main()
