"""손실 중 순위 매도 유예(2026-11-10 사용자 지시 '유예적용, 4시간').

근거: 과거 손실 상태 순위·교체 매도 35건 중 다수가 1~2시간 안에 본전으로 돌아왔다(모의 '본전에서 매도, 최대 4시간' +20,813원,
큰 3건 제외 시 효과 작음 — 표본 35건, 실험으로 확인한다).

규칙
- 정시 주문안에서 순위 매도 대상인데 손실 중(평가액 × (1-수수료) < 장부 원가)이면 팔지 않고 유예를 시작한다. (주문안 생성기 shadow)
- 유예 중 3초 가격이 본전(원가 ÷ 수량 ÷ (1-수수료)) × 1.001 이상이면 바로 판다. (봇 3초 감시, live.defer_sell)
- 유예 중 순위에 다시 들어오면 유예를 풀고 계속 보유한다.
- 유예 시작 뒤 hours 시간이 지난 정시에도 순위 밖·손실이면 그때 판다(주문안의 일반 순위 매도).
- 손절·이익보호·계단 보호는 유예와 상관없이 그대로 작동한다.

비교: '바로 팔았다면' = 유예를 시작한 정시의 평가액 × (1-수수료) - 원가(실제 그 시점 가격). 유예한 포지션이 실제로 끝난 손익과 비교한다.

파일
- data/rank_defer.json   : 유예 중 종목(shadow만 쓴다, 봇은 읽기만)
- data/rank_defer_result.json : 유예한 포지션의 결과 추적(봇만 쓴다)
- data/rank_defer.jsonl  : 사건 기록(추가만)
"""
import datetime as dt
import json
import math
import os
import time

FEE = 0.0005
BE_MARGIN = 0.001
DEFAULT_HOURS = 4
EXPIRY_SLACK = 600   # 정시 주문안이 1~2분 늦게 만들어져도 4번째 정시에 만료되게


def _ok(x):
    return type(x) in (int, float) and math.isfinite(x) and x > 0


def _read(path, default):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError): return default


def _write(path, obj):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, path)


def event(data_dir, row):
    with open(data_dir / 'rank_defer.jsonl', 'a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')


def load_state(data_dir):
    s = _read(data_dir / 'rank_defer.json', {})
    return s if isinstance(s, dict) else {}


def save_state(data_dir, state):
    _write(data_dir / 'rank_defer.json', state)


def in_loss(value, cost):
    return _ok(value) and _ok(cost) and value * (1 - FEE) < cost


def check(state, m, value, pos, now, hours):
    """주문안 생성 중 순위 매도 대상 m에 대해 호출. 반환: (유예 여부, 사유 문구).
    pos = 실거래 장부 포지션({'cost','qty','time'}). 손실 중이 아니거나 장부 원가를 모르면 유예하지 않는다."""
    if not isinstance(pos, dict) or not in_loss(value, pos.get('cost')) or not _ok(pos.get('qty')):
        if m in state:
            state[m]['end'] = 'profit'   # 손실을 벗어남 → 일반 매도
        return False, None
    w = state.get(m)
    if w is None or w.get('opened') != pos.get('time'):
        w = state[m] = {'m': m, 'opened': pos.get('time'), 'started': now, 'value': value, 'cost': pos['cost'], 'qty': pos['qty'],
                        'immediate_pnl': value * (1 - FEE) - pos['cost'], 'hours': hours}
        w['new'] = True
    w['checked'] = True
    left = w['started'] + hours * 3600 - EXPIRY_SLACK - now
    if left <= 0:
        w['end'] = 'expired'
        return False, f'손실 유예 {hours:g}시간 만료'
    be = pos['cost'] / pos['qty'] / (1 - FEE)
    return True, f'손실 중 순위 매도 유예 (본전 {be:,.8g}원 닿으면 매도, {left / 3600:.1f}시간 남음)'


def finish(data_dir, state, held, sold, now):
    """주문안 생성 끝에 호출: 유예가 끝난 종목을 정리하고 저장한다.
    held = 지금 계좌 보유 종목, sold = 이번 주문안에서 매도할 종목."""
    for m in list(state):
        w = state[m]
        if w.pop('new', False):
            event(data_dir, {'event': '유예 시작', 'market': m, 'opened': w['opened'], 'value': w['value'], 'cost': w['cost'],
                             'immediate_pnl': w['immediate_pnl'], 'at': now})
        end = w.pop('end', None)
        if m not in held:
            end = end or 'closed'
        elif m in sold:
            end = end or 'sold'
        elif not w.get('checked'):
            end = end or 'returned'   # 이번 정시에 순위 매도 대상이 아니었다 = 순위 복귀
        if end:
            event(data_dir, {'event': {'expired': '유예 만료 매도', 'profit': '손실 벗어남(일반 매도)', 'returned': '순위 복귀',
                                       'closed': '보유 종료', 'sold': '주문안 매도'}.get(end, end), 'market': m, 'at': now})
            state.pop(m)
        else:
            w.pop('checked', None)
    save_state(data_dir, state)


class Watch:
    """봇 3초 감시: 유예 중 종목이 본전에 닿으면 팔고, 유예한 포지션이 끝날 때까지 결과를 기록한다."""

    def __init__(self, data_dir):
        self.dir = data_dir
        self.path = data_dir / 'rank_defer_result.json'
        self.r = _read(self.path, {})

    def update(self, positions, prices, last_sell=None, live_sell=None, now=None):
        """positions = 실거래 장부(보호 종목 제외), prices = {종목: 현재가}, last_sell = live.d['last_sell'],
        live_sell(종목, w, 가격) → bool(실제 매도). live_sell이 None이면 본전 매도를 하지 않는다(실험 꺼짐)."""
        now = now or time.time(); changed = False
        state = load_state(self.dir)
        for m, w in state.items():
            key = f"{m}|{w.get('opened')}"
            if key not in self.r:
                self.r[key] = {'m': m, 'opened': w.get('opened'), 'started': w.get('started'), 'cost': w.get('cost'),
                               'qty': w.get('qty'), 'immediate_pnl': w.get('immediate_pnl'), 'state': 'deferred'}
                changed = True
        for key, r in self.r.items():
            if r['state'] not in ('deferred', 'returned'):
                continue
            m = r['m']; p = positions.get(m)
            if not p or p.get('time') != r['opened'] or not _ok(p.get('qty')):
                ls = (last_sell or {}).get(m) or {}
                px = ls.get('price') if (ls.get('time') or 0) >= (r.get('started') or 0) else None
                q = r.get('qty') or (p or {}).get('qty')
                pnl = (q * px * (1 - FEE) - r['cost']) if _ok(px) and _ok(q) else None
                why = str(ls.get('reason') or '')
                how = ('본전 매도' if '손실 유예' in why else '손절' if '손절' in why else '이익보호' if '이익보호' in why
                       else '계단 보호' if '계단' in why else ('기한 매도' if r['state'] == 'deferred' else '순위 매도')) if px else '확인 불가'
                r.update(state='done', end_at=now, actual_pnl=pnl, how=how, after_return=r['state'] == 'returned')
                event(self.dir, {'event': '유예 포지션 종료', 'key': key, 'how': how, 'actual_pnl': pnl, 'immediate_pnl': r['immediate_pnl'], 'at': now})
                changed = True
                continue
            r['qty'] = p['qty']; r['cost'] = p.get('cost', r['cost'])
            if m not in state:
                if r['state'] == 'deferred':
                    r['state'] = 'returned'; changed = True   # 유예가 풀림(순위 복귀): 끝날 때까지 결과만 따라간다
                continue
            px = prices.get(m)
            be = r['cost'] / r['qty'] / (1 - FEE)
            if live_sell is not None and _ok(px) and px >= be * (1 + BE_MARGIN) and now - r.get('fail_at', 0) >= 60:
                w = {'opened': r['opened'], 'be': be}
                if live_sell(m, w, px):
                    funds, fee = w.get('fill_funds'), w.get('fill_fee')
                    pnl = (funds - fee - r['cost'] * min(1, w.get('fill_vol', r['qty']) / r['qty'])) if _ok(funds) else r['qty'] * px * (1 - FEE) - r['cost']
                    r.update(state='done', end_at=now, actual_pnl=pnl, how='본전 매도', after_return=False)
                    event(self.dir, {'event': '본전 매도', 'key': key, 'px': px, 'actual_pnl': pnl, 'immediate_pnl': r['immediate_pnl'], 'at': now})
                else:
                    r['fail_at'] = now
                    event(self.dir, {'event': '본전 매도 보류', 'key': key, 'px': px, 'at': now})
                changed = True
        if changed: _write(self.path, self.r)

    def summary(self):
        v = list(self.r.values()); done = [r for r in v if r['state'] == 'done' and r.get('actual_pnl') is not None
                                            and r.get('immediate_pnl') is not None]
        hows = {}
        for r in done: hows[r['how']] = hows.get(r['how'], 0) + 1
        return {'n_all': len(v), 'open': sum(r['state'] != 'done' for r in v), 'n': len(done), 'hows': hows,
                'win_now': sum(r['actual_pnl'] > 0 for r in done), 'win_old': sum(r['immediate_pnl'] > 0 for r in done),
                'pnl_now': sum(r['actual_pnl'] for r in done), 'pnl_old': sum(r['immediate_pnl'] for r in done),
                'first': min((r['started'] for r in v if r.get('started')), default=None)}


def render(watch, hours=DEFAULT_HOURS, since=None):
    s = watch.summary()
    start = since or s['first']
    when = dt.datetime.fromtimestamp(start).strftime('%m-%d %H:%M') + '부터' if start else '방금 시작'
    if s['n']:
        d = s['pnl_now'] - s['pnl_old']
        col = '#1a7f37' if d > 0 else '#b42318' if d < 0 else 'inherit'
        cmp_ = (f"끝난 {s['n']}건 · 승률 <b>{s['win_now'] / s['n']:.0%}</b> (바로 팔았다면 {s['win_old'] / s['n']:.0%}) · "
                f"손익 <b>{s['pnl_now']:+,.0f}원</b> (바로 팔았다면 {s['pnl_old']:+,.0f}원) → "
                f"<b style='color:{col};font-size:17px'>효과 {d:+,.0f}원</b>")
    else:
        cmp_ = '아직 끝난 유예 없음 · 효과 계산 대기'
    how = ' / '.join(f'{k} {v}' for k, v in sorted(s['hows'].items(), key=lambda x: -x[1])) or '-'
    return (f"<div style='margin:6px 0;padding:8px 12px;border:2px solid #7c3aed;border-radius:8px;font-size:15px'>"
            f"<b>🧪 손실 유예 실험 중</b> (손실 중 순위 매도를 최대 {hours:g}시간 미루고 본전 오면 매도) · {when} · {cmp_}"
            f"<div class=sub style='margin-top:3px'>유예 {s['n_all']}건(진행 중 {s['open']}) · 끝난 방식: {how}"
            " · 바로 팔았다면 = 유예를 시작한 정시 가격으로 판 경우(실제 가격)"
            " · 효과가 없으면 설정 live.rank_loss_defer_enabled=false 로 원래대로</div></div>")
