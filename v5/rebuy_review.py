"""이익 청산 뒤 재매수 사후 점검(2026-11-08). 눌림 조건(reentry_dip_atr)을 1에서 0으로 풀고 4시간 금지만 둔 효과를 실제 거래로 모아 본다.

대상: 직전 왕복이 이익으로 끝난 같은 종목을 청산 4시간 뒤~24시간 안에 다시 산 왕복(SINCE 이후 진입), 청산까지 끝난 것.
기준(결과를 보기 전에 고정): MIN_N건 이상이면 알린다. 평균 손익 GOOD_PCT 이상이면 0 유지, BAD_PCT 이하이거나 합계가 음수면 0.5ATR 검토, 그 사이면 건수를 늘려 다시 본다.
이 모듈은 판정과 안내만 한다. 주문·설정은 바꾸지 않고, 설정 변경은 사용자 승인 뒤에만 한다.
"""
import datetime as dt
import json
from pathlib import Path

SINCE = '2026-11-08T00:30:00+09:00'
MIN_N, PROFIT_H, WINDOW_H = 15, 4.0, 24.0
GOOD_PCT, BAD_PCT = 0.3, -0.3
# 눌림 조건별 모의 비교(2026-11-08, 이익 청산 뒤 4시간 뒤 재매수 23건, 8만원 기준)
SIM = {'0': (1.08, 19879), '0.5': (0.66, 10004), '1': (0.61, 7858)}


def _t(s):
    return dt.datetime.fromisoformat(s)


def round_trips(orders_json):
    """거래소 주문 기록에서 V5가 연 왕복(매수~전량 매도)을 평균단가법으로 만든다. [{market, open, close, cost, pnl}] 시각순."""
    try:
        orders = json.loads(Path(orders_json).read_text(encoding='utf-8')).get('orders', {})
    except (OSError, ValueError):
        return []
    ev = []
    for r in orders.values():
        if not r.get('trades'):
            continue
        q = sum(float(x['volume']) for x in r['trades']); f = sum(float(x['funds']) for x in r['trades'])
        ev.append((min(x['created_at'] for x in r['trades']), r['market'], r['side'], q, f, float(r.get('paid_fee') or 0),
                   str(r.get('identifier') or '').startswith('v5-')))
    ev.sort()
    pos, out = {}, []
    for t, m, side, q, f, fee, mine in ev:
        p = pos.setdefault(m, {'q': 0., 'cost': 0., 'trip': None})
        if side == 'bid':
            if p['q'] <= 1e-9 or p['trip'] is None:
                p['trip'] = {'market': m, 'open': t, 'close': None, 'cost': 0., 'pnl': 0.} if mine else None
            p['q'] += q; p['cost'] += f + fee
            if p['trip']: p['trip']['cost'] += f + fee
        elif p['q'] > 1e-9:
            take = min(q, p['q']); share = take / p['q']; alloc = p['cost'] * share
            if p['trip']: p['trip']['pnl'] += (f - fee) * (take / q) - alloc
            p['q'] -= take; p['cost'] -= alloc
            if p['q'] <= 1e-9 or p['q'] < take * 0.001:
                if p['trip']:
                    p['trip']['close'] = t; out.append(p['trip'])
                p['q'] = 0.; p['cost'] = 0.; p['trip'] = None
    return out


def evaluate(orders_json, since=SINCE):
    """{n(청산까지 끝난 재매수 수), pnl, avg_pct, wins, state} 상태: collecting / keep / raise / extend."""
    trips = round_trips(orders_json)
    by = {}
    for x in trips:
        by.setdefault(x['market'], []).append(x)
    hit = []
    s = _t(since)
    for xs in by.values():
        for a, b in zip(xs, xs[1:]):
            if a['pnl'] <= 0 or _t(b['open']) < s:
                continue
            gap = (_t(b['open']) - _t(a['close'])).total_seconds() / 3600
            if PROFIT_H <= gap < WINDOW_H:
                hit.append(b)
    n = len(hit)
    pnl = sum(x['pnl'] for x in hit)
    pcts = [x['pnl'] / x['cost'] * 100 for x in hit if x['cost'] > 0]
    avg = sum(pcts) / len(pcts) if pcts else 0.0
    wins = sum(1 for x in hit if x['pnl'] > 0)
    if n < MIN_N:
        state = 'collecting'
    elif pnl < 0 or avg <= BAD_PCT:
        state = 'raise'
    elif avg >= GOOD_PCT:
        state = 'keep'
    else:
        state = 'extend'
    return {'version': 'v1', 'since': since, 'n': n, 'pnl': round(pnl), 'avg_pct': round(avg, 3), 'wins': wins, 'state': state,
            'items': [{'market': x['market'], 'open': x['open'], 'pnl': round(x['pnl']), 'pct': round(x['pnl'] / x['cost'] * 100, 2)}
                      for x in hit if x['cost'] > 0][-30:]}


def notice_text(r):
    """(제목, 본문, 수준). 아직 모으는 중이면 None."""
    if r['state'] == 'collecting':
        return None
    head = f"이익 청산 뒤 4~24시간 재매수 {r['n']}건: 평균 {r['avg_pct']:+.2f}% · 합계 {r['pnl']:+,}원 · 승 {r['wins']}건"
    sim = ' / '.join(f'눌림 {k}ATR 모의 평균 {v[0]:+.2f}%' for k, v in SIM.items())
    todo = {
        'keep': f'할 일: 눌림 조건 0을 그대로 둡니다. 평균이 {GOOD_PCT:+.1f}% 이상이라 효과가 있다고 봅니다. 따로 바꿀 것은 없습니다.',
        'raise': f'할 일: 평균이 {BAD_PCT:+.1f}% 이하이거나 합계가 음수입니다. Claude 새 대화에서 "재매수 점검 결과 보고"라고 말해 거래별 내역을 보고, 눌림 조건을 0.5ATR로 올릴지 정하세요.',
        'extend': f'할 일: 평균이 {BAD_PCT:+.1f}%~{GOOD_PCT:+.1f}% 사이라 판단하기 이릅니다. 설정은 그대로 두고 건수가 더 쌓일 때까지 기다립니다.',
    }[r['state']]
    body = f"{head}\n비교(모의 23건): {sim}\n{todo}\n설정 변경은 사용자가 승인한 뒤에만 합니다(이 알림은 주문·설정을 바꾸지 않습니다)."
    return ('재매수 사후 점검 결과가 나왔습니다', body, 'action' if r['state'] == 'raise' else 'info')
