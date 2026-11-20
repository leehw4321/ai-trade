"""매수 쉬기/거래 추천 창(사용자 지시 2026-11-21: '현재 매수 시점입니다·쉬는 것이 유리합니다·수익이 나기 힘듭니다' 등
지금 상황에서 쉬는 중이면 시작/계속 쉼, 거래 중이면 쉬기/계속 거래를 추천하는 창).

표시 전용이다. 주문·쉬기 상태를 바꾸지 않는다. 쉬기/다시 시작은 사용자가 버튼으로 직접 한다.
근거(점수, + = 사기 좋음, - = 쉬기 좋음):
  1. 최근 20건 합(실거래 끝난 왕복 + 결과 나온 가상 매수) : ±2  ← buy_pause의 '다시 살 신호'와 같은 기준
  2. 그중 최근 10건 합(흐름)                               : ±1
  3. 이번 휴식의 가상 매수 결과(쉬는 중, 결과 5건 이상)        : ±1  (+면 쉬는 동안 벌 기회를 놓치는 중)
  4. 시장 분위기(market_state: 지수 24시간·상승 종목 비율·4시간 브레이크, 3시간 이내 값만) : ±1
  공포탐욕 지수는 방향이 엇갈려(역발상) 점수에 넣지 않고 참고로만 보인다.
판단(현재 상태 쪽으로 기울여 자주 뒤집히지 않게 함):
  쉬는 중 : 최근 20건 합 > 0 이고 점수 ≥ 2 → 다시 시작 추천 / 그 밖은 계속 쉬기(점수 ≤ -2면 강하게)
  거래 중 : 점수 ≤ -2 → 쉬기 추천 / 점수 ≥ 1 → 계속 거래 추천 / 그 밖은 계속 거래하되 주의
규칙은 단독 작성(검증 전)이고 수익을 보장하지 않는다.
"""
import datetime as dt
import html
import time

import buy_pause as bp

KST = dt.timezone(dt.timedelta(hours=9))
MARKET_MAX_AGE = 3 * 3600


def _won(x):
    return f'{x:+,.0f}원'


def _series(s, trips, now):
    """끝난 순서의 손익 목록(실거래 + 결과 나온 가상)."""
    xs = [(x['close'], x['pnl']) for x in trips if x.get('close') is not None and x['close'] <= now]
    xs += [(v['t'] + bp.drop_watch.HOLD_H * 3600, v['ret'] * v['krw']) for v in s['virtual']
           if isinstance(v.get('ret'), (int, float))]
    return [p for _, p in sorted(xs)]


def _market(mkt, now):
    if not isinstance(mkt, dict):
        return 0, '시장 정보 없음'
    try:
        at = dt.datetime.fromisoformat(mkt['at']).timestamp()
    except (KeyError, TypeError, ValueError):
        return 0, '시장 정보 없음'
    if now - at > MARKET_MAX_AGE:
        return 0, '시장 정보가 오래됨(3시간 넘음)'
    i24 = float(mkt.get('index_24h') or 0)
    up, total = int(mkt.get('up') or 0), int(mkt.get('total') or 0)
    ratio = up / total if total else 0.5
    txt = f"시장 지수 24시간 {i24:+.1%} · 상승 {up}/{total}종목" + (' · 4시간 급락 브레이크' if mkt.get('brake_4h') else '')
    if mkt.get('brake_4h') or i24 <= -0.01 or ratio < 0.35:
        return -1, txt
    if i24 >= 0.01 and ratio >= 0.5:
        return 1, txt
    return 0, txt


def assess(s, trips, mkt=None, now=None):
    """dict: paused, score, verdict(start/keep_rest/rest/keep_trade/caution), title, sub, reasons[(점수, 제목, 설명)]."""
    now = time.time() if now is None else now
    pnl = _series(s, trips, now)
    last20, last10 = pnl[-bp.N:], pnl[-10:]
    reasons = []
    if len(last20) >= bp.N:
        t = sum(last20); w = sum(p > 0 for p in last20)
        reasons.append((2 if t > 0 else -2, '최근 20건 성적', f'합 {_won(t)} · 이익 {w}건/손실 {len(last20) - w}건'))
    else:
        t = None
        reasons.append((0, '최근 20건 성적', f'끝난 거래가 {len(last20)}건뿐이라 판단 보류'))
    if len(last10) >= 10:
        t10 = sum(last10)
        reasons.append((1 if t10 > 0 else -1, '최근 10건 흐름', f'합 {_won(t10)}' + (' · 나아지는 중' if t10 > 0 else ' · 아직 손실 흐름')))
    if s.get('paused'):
        cd = [v for v in s['virtual'] if v['t'] >= (s.get('since') or 0) and isinstance(v.get('ret'), (int, float))]
        if len(cd) >= 5:
            cp = sum(v['ret'] * v['krw'] for v in cd)
            reasons.append((1 if cp > 0 else -1, '쉬는 동안 가상 매수',
                            f'{len(cd)}건 샀다면 {_won(cp)}' + (' · 쉬면서 벌 기회를 놓치는 중' if cp > 0 else ' · 쉰 덕에 손실을 피함')))
        else:
            reasons.append((0, '쉬는 동안 가상 매수', f'결과 {len(cd)}건 · 5건 이상 쌓이면 반영'))
    m, mtxt = _market(mkt, now)
    reasons.append((m, '시장 분위기', mtxt))
    score = sum(r[0] for r in reasons)
    if s.get('paused'):
        if t is not None and t > 0 and score >= 2:
            v, title, sub = 'start', '지금은 매수 시점입니다', '▶ 매수 다시 시작을 추천합니다 · 최근 성적과 흐름이 플러스로 돌아섰습니다'
        elif score <= -2:
            v, title, sub = 'keep_rest', '쉬는 것이 유리합니다', '⏸ 계속 쉬기를 추천합니다 · 지금 사면 수익이 나기 힘듭니다'
        else:
            v, title, sub = 'keep_rest', '조금 더 쉬며 지켜보세요', '⏸ 계속 쉬기를 추천합니다 · 좋아지는 근거가 일부 있지만 아직 이릅니다'
    else:
        if score <= -2:
            v, title, sub = 'rest', '쉬는 것이 유리합니다', '⏸ 매수 쉬기를 추천합니다 · 지금 사면 수익이 나기 힘듭니다'
        elif score >= 1:
            v, title, sub = 'keep_trade', '거래하기 좋은 흐름입니다', '▶ 계속 거래를 추천합니다'
        else:
            v, title, sub = 'caution', '계속 거래하되 주의하세요', '▶ 계속 거래 · 성적이 흔들리고 있어 나빠지면 쉬기를 고려하세요'
    return {'paused': bool(s.get('paused')), 'score': score, 'verdict': v, 'title': title, 'sub': sub, 'reasons': reasons}


COLORS = {'start': '#1a7f37', 'keep_trade': '#1a7f37', 'keep_rest': '#b42318', 'rest': '#b42318', 'caution': '#b45309'}
ICON = {'start': '▶', 'keep_trade': '▶', 'keep_rest': '⏸', 'rest': '⏸', 'caution': '⚠'}


def render(data_dir, trips, mkt=None, fng=None, now=None, elsewhere=None):
    """봇 현황 맨 위 추천 창 HTML. elsewhere: 실행권 PC 이름(이 PC에 실행권이 없을 때)."""
    now = time.time() if now is None else now
    s = bp.load(data_dir)
    a = assess(s, trips, mkt, now)
    e = html.escape
    col = COLORS[a['verdict']]
    rows = ''.join(
        f"<div style='display:flex;gap:8px;align-items:baseline;padding:2px 0'>"
        f"<span style='min-width:22px;font-weight:700;color:{'#1a7f37' if p > 0 else '#b42318' if p < 0 else 'var(--muted)'}'>{'👍' if p > 0 else '👎' if p < 0 else '➖'}</span>"
        f"<b style='min-width:120px'>{e(k)}</b><span>{e(d)}</span></div>" for p, k, d in a['reasons'])
    if isinstance(fng, dict) and fng.get('value') is not None:
        rows += (f"<div class=sub style='padding:2px 0 0 30px'>참고: 공포탐욕 지수 {e(str(fng.get('value')))}({e(str(fng.get('label', '')))}) · "
                 "공포가 클 때 싸게 사는 기회일 수도, 더 떨어질 신호일 수도 있어 점수에 넣지 않음</div>")
    state = '⏸ 지금 쉬는 중' if a['paused'] else '▶ 지금 거래 중'
    warn = (f"<div style='background:rgba(180,83,9,.12);border-radius:6px;padding:4px 8px;margin-top:6px'>⚠ 실행권이 <b>{e(str(elsewhere))}</b> PC에 있어 "
            "이 PC의 쉬기 상태는 실제 계좌와 다를 수 있습니다. 실행권 PC 화면의 추천을 보세요.</div>") if elsewhere is not None else ''
    return (f"<div style='margin:6px 0;padding:12px 16px;border-radius:10px;border:2px solid {col};"
            f"background:linear-gradient(90deg,{col}1f,transparent 70%)'>"
            f"<div style='display:flex;gap:12px;align-items:center;flex-wrap:wrap'>"
            f"<span style='font-size:30px;line-height:1;color:{col}'>{ICON[a['verdict']]}</span>"
            f"<div><div style='font-size:20px;font-weight:800;color:{col}'>{e(a['title'])}</div>"
            f"<div style='font-size:15px;font-weight:600'>{e(a['sub'])}</div></div>"
            f"<span class=sub style='margin-left:auto'>{state} · 점수 {a['score']:+d} · {dt.datetime.fromtimestamp(now, KST):%m-%d %H:%M} 기준</span></div>"
            f"<div style='margin-top:8px;border-top:1px solid var(--line);padding-top:6px'>{rows}</div>{warn}"
            "<div class=sub style='margin-top:6px'>참고용 추천 · 주문에 영향 없음 · 쉬기/다시 시작은 아래 버튼으로 직접 · "
            "규칙: 최근 20건 ±2, 최근 10건 ±1, 쉬는 동안 가상 ±1, 시장 ±1 (단독 작성, 검증 전 · 수익 보장 아님)</div></div>")
