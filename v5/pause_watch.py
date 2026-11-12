"""최근 성적 매수 휴식 관찰(사용자 지시 2026-11-12 '진행'). 주문과 무관, 기록·표시만.

규칙(관찰 대상): 새 매수 시점에 이미 끝난 봇 거래 직전 N=20건의 실현 손익 합이 0 이하이면 '쉬었어야 할 매수'.
근거: 2년 재현(tools/analysis_20261112/regime_filter_2y.py)에서 이 규칙이 매수 1건 평균을 +0.25%p 가름(3개 연도 일관), 단 켜진 때도 평균 음수.
자료: data/exchange_orders.jsonl(v5- 주문, trade_history.py가 5분마다 갱신). 왕복 = 첫 매수 ~ 잔량 5,000원 미만이 된 매도, 이동평균 원가·수수료 포함.
'막은 효과' = −(쉬었어야 할 매수들의 실현 손익 합). 양수면 쉬는 편이 이득. 시작 시각 이후 '매수한' 왕복만 센다(직전 20건 판단에는 그 전 거래도 쓴다).
"""
import datetime as dt
import json
import time
from pathlib import Path

N = 20
DECIDE_DAYS = 14
KST = dt.timezone(dt.timedelta(hours=9))
STATE = 'pause_watch.json'


def trips(path):
    fills = []
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            r = json.loads(line)
            if not str(r.get('identifier') or '').startswith('v5-') or float(r.get('executed_volume') or 0) <= 0: continue
            fills.append((dt.datetime.fromisoformat(r['created_at']).timestamp(), r['market'], r['side'],
                          float(r['executed_volume']), float(r['executed_funds']), float(r.get('paid_fee') or 0)))
        except (ValueError, KeyError, TypeError):
            continue
    fills.sort()
    cur, out = {}, []
    for t, m, side, v, f, fee in fills:
        x = cur.get(m)
        if side == 'bid':
            if x is None: x = cur[m] = {'m': m, 'open': t, 'q': 0., 'cost': 0., 'pnl': 0.}
            x['q'] += v; x['cost'] += f + fee
        elif x is not None and x['q'] > 0:
            take = min(v, x['q']); c = x['cost'] * take / x['q']
            x['pnl'] += f * take / v - fee * take / v - c; x['cost'] -= c; x['q'] -= take
            if x['q'] <= 1e-8 or x['cost'] < 5000:
                x['close'] = t; out.append(x); cur.pop(m)
    return out


def evaluate(trs, since, now=None):
    now = now or time.time()
    closed = sorted(trs, key=lambda x: x['close'])

    def paused(at):
        done = [x['pnl'] for x in closed if x['close'] < at][-N:]
        return len(done) == N and sum(done) <= 0, sum(done) if done else 0.

    rows = []
    for x in trs:
        if x['open'] >= since:
            p, s = paused(x['open'])
            rows.append({**x, 'paused': p, 'recent': s})
    now_p, now_s = paused(now)
    return rows, now_p, now_s


def render(data_dir, now=None):
    now = now or time.time()
    d = Path(data_dir)
    try:
        st = json.loads((d / STATE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        st = {'since': now}
        (d / STATE).write_text(json.dumps(st), encoding='utf-8')
    since = st['since']
    rows, now_p, now_s = evaluate(trips(d / 'exchange_orders.jsonl'), since, now)
    pz = [r for r in rows if r['paused']]; ok = [r for r in rows if not r['paused']]
    eff = -sum(r['pnl'] for r in pz)
    col = '#1a7f37' if eff > 0 else '#b42318' if eff < 0 else 'inherit'
    due = since + DECIDE_DAYS * 86400
    left = due - now
    dd = (f"재결정 {dt.datetime.fromtimestamp(due, KST):%m-%d} (D-{int(left // 86400) + 1})" if left > 0
          else f"<b style=color:#b42318>재결정일 {dt.datetime.fromtimestamp(due, KST):%m-%d} 지남 → 결정 필요</b>")
    days = {}
    for r in pz:
        k = dt.datetime.fromtimestamp(r['open'], KST).strftime('%m-%d')
        a = days.setdefault(k, [0, 0.]); a[0] += 1; a[1] -= r['pnl']
    day_txt = ' · '.join(f'{k}: {n}건 {v:+,.0f}' for k, (n, v) in sorted(days.items())[-14:]) or '아직 없음'
    state = (f"<b style=color:#b42318>지금: 쉴 때</b>(직전 {N}건 합 {now_s:+,.0f}원)" if now_p
             else f"지금: 살 때(직전 {N}건 합 {now_s:+,.0f}원)")
    body = (f"쉬었어야 할 매수 {len(pz)}건(이익 {sum(r['pnl'] > 0 for r in pz)}) 손익 {sum(r['pnl'] for r in pz):+,.0f}원 → "
            f"<b style='color:{col}'>쉬었다면 효과 {eff:+,.0f}원</b> │ 그 밖 매수 {len(ok)}건 손익 {sum(r['pnl'] for r in ok):+,.0f}원"
            if rows else '시작 뒤 끝난 매수 없음')
    return ("<div style='margin:6px 0;padding:8px 12px;border:2px solid #b45309;border-radius:8px;font-size:15px'>"
            f"<b>🧪 최근 성적 매수 휴식 관찰</b>(주문 무영향) · {dt.datetime.fromtimestamp(since, KST):%m-%d %H:%M}부터 · {dd} · {state}<br>{body}"
            f"<div class=sub style='margin-top:3px'>규칙: 새 매수 때 봇의 직전 {N}건 실현 손익 합이 0 이하면 '쉬었어야 할 매수' · "
            f"일별 쉬었다면 효과: {day_txt} · 끝난 왕복만 셈(보유 중 제외)</div></div>")
