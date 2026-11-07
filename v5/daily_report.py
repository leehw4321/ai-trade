"""하루(한국시간 0시~24시) 리포트. 매일 0시 이후 전날 것을 data/reports/YYYY-MM-DD.json 으로 확정해 화면 '일일 리포트'란에 올린다.

기록만 하고 주문·설정은 건드리지 않는다. 실현손익은 거래소 주문 기록을 평균단가법(수수료 포함)으로 다시 계산한 값이다.
"""
import datetime as dt
import html
import json
import os
from pathlib import Path

import daily_equity

KST = dt.timezone(dt.timedelta(hours=9))
KEEP = 120


def sells_by_day(orders_json):
    """[(KST 날짜, 종목, 실현손익, V5 주문 여부)] — 매도 한 건마다."""
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
        p = pos.setdefault(m, {'q': 0., 'cost': 0.})
        if side == 'bid':
            p['q'] += q; p['cost'] += f + fee
        elif p['q'] > 1e-9:
            take = min(q, p['q']); alloc = p['cost'] * take / p['q']
            out.append((t[:10], m[4:], f * (take / q) - fee - alloc, mine))
            p['q'] -= take; p['cost'] -= alloc
            if p['q'] <= 1e-9:
                p['q'], p['cost'] = 0., 0.
    return out


def build(day, equity_path, orders_json, live=None, review=None, final=True):
    """한 날의 리포트 문서."""
    eq = daily_equity.read(equity_path).get(day)
    rows = [s for s in sells_by_day(orders_json) if s[0] == day]
    mine = [s for s in rows if s[3]]
    doc = {'day': day, 'final': final, 'made_at': dt.datetime.now(KST).isoformat(timespec='minutes')}
    if eq:
        doc['equity'] = {k: eq.get(k) for k in ('open', 'last', 'pnl', 'pct', 'high', 'low')}
    for key, rs in (('v5', mine), ('all', rows)):
        wins = [s for s in rs if s[2] > 0]; loss = [s for s in rs if s[2] < 0]
        doc[key] = {'sells': len(rs), 'wins': len(wins), 'losses': len(loss), 'net': round(sum(s[2] for s in rs)),
                    'profit': round(sum(s[2] for s in wins)), 'loss': round(sum(s[2] for s in loss))}
    byc = {}
    for s in mine:
        byc[s[1]] = byc.get(s[1], 0.) + s[2]
    ranked = sorted(byc.items(), key=lambda kv: kv[1])
    doc['worst'] = [(c, round(v)) for c, v in ranked[:3] if v < 0]
    doc['best'] = [(c, round(v)) for c, v in ranked[::-1][:3] if v > 0]
    w, l = doc['v5']['profit'], -doc['v5']['loss']
    doc['payoff'] = round((w / max(doc['v5']['wins'], 1)) / (l / max(doc['v5']['losses'], 1)), 2) if w and l else None
    if live:
        doc['state'] = {'halted': live.get('halted') or '', 'loss_scope': live.get('loss_scope', '')}
    if review:
        doc['entry_flags'] = {k: review.get(k) for k in ('state', 'records', 'hit', 'keep')}
    return doc


def save(folder, doc):
    p = Path(folder)
    p.mkdir(parents=True, exist_ok=True)
    f = p / f"{doc['day']}.json"
    tmp = f.with_name(f.name + '.tmp')
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, f)
    for old in sorted(p.glob('????-??-??.json'))[:-KEEP]:
        old.unlink()


def recent(folder, n=14):
    out = []
    for f in sorted(Path(folder).glob('????-??-??.json'), reverse=True)[:n]:
        try:
            out.append(json.loads(f.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            pass
    return out


def summary_line(d):
    e = d.get('equity') or {}
    v = d['v5']
    tot = f"총자산 {e['pnl']:+,}원({e['pct']:+.2f}%)" if e.get('pnl') is not None else '총자산 기록 없음'
    return (f"{tot} · 봇 실현 {v['net']:+,}원 · 매도 {v['sells']}건(승 {v['wins']}·패 {v['losses']})"
            + (f" · 손익비 {d['payoff']}" if d.get('payoff') else ''))


def lines(d):
    """리포트 한 날의 내용 줄 목록(첫 줄이 요약). 화면 '일일 리포트'란과 공지 내용 창이 같이 쓴다."""
    eq = d.get('equity') or {}
    out = [summary_line(d)]
    if eq:
        out.append(f"총자산 시작 {eq['open']:,.0f} → 마지막 {eq['last']:,.0f} (최고 {eq['high']:,.0f} / 최저 {eq['low']:,.0f})")
    a = d['all']
    if a['sells'] != d['v5']['sells']:
        out.append(f"수동·출처불명 포함 전체 매도 {a['sells']}건 {a['net']:+,}원")
    if d.get('worst'):
        out.append('손실 큰 종목: ' + ' · '.join(f'{c} {v:+,}' for c, v in d['worst']))
    if d.get('best'):
        out.append('이익 큰 종목: ' + ' · '.join(f'{c} {v:+,}' for c, v in d['best']))
    s = d.get('state')
    if s and s['halted']:
        out.append('매수 중단 상태: ' + str(s['halted']))
    ef = d.get('entry_flags')
    if ef:
        out.append(f"급변 진입 검토: {ef['state']} (기록 {ef['records']}줄, 발동 {ef['hit']}건 / 통과 {ef['keep']}건)")
    return out


def section(docs):
    """화면 '일일 리포트'란 HTML. 최신이 맨 위."""
    if not docs:
        return ''
    e = html.escape
    items = []
    for i, d in enumerate(docs):
        tag = '' if d.get('final') else ' · 진행 중(잠정)'
        body = ''.join(f'<div>{e(x)}</div>' for x in lines(d))
        items.append(f"<details{' open' if i == 0 else ''}><summary><b>{e(d['day'])}</b>{tag} — {e(summary_line(d))}</summary>"
                     f"<div style='padding:6px 12px;line-height:1.6'>{body}</div></details>")
    return f"<section><h2>일일 리포트</h2>{''.join(items)}</section>"
