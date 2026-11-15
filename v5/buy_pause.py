"""사용자 매수 쉬기·다시 시작(사용자 지시 2026-11-15 '쉬어가는 아이콘, 다시 시작하는 아이콘, 쉬면서 가상 매수 기록').

- 쉬는 동안: 봇의 자동 매수(주문안의 매수)만 막는다. 손절·이익보호·순위 매도·사용자 직접 매수는 그대로.
- 막힌 매수는 '가상 매수'로 기록한다(그 시점 현재가, 주문안 금액). 같은 종목이 가상 보유 중이면 다시 세지 않고, 가상 동시 보유는 SLOTS개까지.
- 가상 결과: 15분봉으로 손절·이익보호·최대 6시간(drop_watch.simulate와 같은 모의). 실제 봇은 순위 매도로 더 일찍/늦게 팔 수 있다 → 근사.
- 다시 살 신호(참고): 실거래 끝난 왕복과 결과 나온 가상 매수를 합친 최근 20건 손익 합이 플러스.
파일: data/buy_pause.json (봇만 쓴다. live.json과 별개라 실행권 체크섬과 무관)
"""
import datetime as dt
import json
import os
import time
from pathlib import Path

import drop_watch

FILE = 'buy_pause.json'
SLOTS = 3
N = 20
KST = dt.timezone(dt.timedelta(hours=9))


def load(data_dir):
    try:
        s = json.loads((Path(data_dir) / FILE).read_text(encoding='utf-8'))
        if isinstance(s, dict):
            s.setdefault('paused', False); s.setdefault('since', None); s.setdefault('log', []); s.setdefault('virtual', [])
            return s
    except (OSError, ValueError):
        pass
    return {'paused': False, 'since': None, 'log': [], 'virtual': []}


def save(data_dir, s):
    p = Path(data_dir) / FILE
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(s, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, p)


def is_paused(data_dir):
    return bool(load(data_dir)['paused'])


def set_paused(data_dir, paused, now=None, by='화면 버튼'):
    """(바뀌었는지, 안내문)."""
    now = time.time() if now is None else now
    s = load(data_dir)
    if bool(s['paused']) == bool(paused):
        return False, '이미 매수를 쉬는 중입니다.' if paused else '이미 매수 중입니다.'
    s['paused'] = bool(paused)
    s['since'] = now if paused else None
    s['log'].append({'at': now, 'action': '쉬기' if paused else '다시 시작', 'by': by})
    save(data_dir, s)
    return True, ('신규 자동 매수를 쉽니다. 손절·이익보호는 계속하고, 봇이 사려던 매수는 가상으로 기록합니다.' if paused
                  else '신규 자동 매수를 다시 시작합니다.')


def record_virtual(data_dir, buys, now=None):
    """buys: [(종목, 금액, 현재가)]. 기록한 건수."""
    now = time.time() if now is None else now
    s = load(data_dir)
    open_ = [v for v in s['virtual'] if 'ret' not in v and now < v['t'] + drop_watch.HOLD_H * 3600]
    held = {v['m'] for v in open_}
    n = 0
    for m, krw, px in buys:
        if m in held or len(open_) >= SLOTS or not (isinstance(px, (int, float)) and px > 0):
            continue
        v = {'t': now, 'm': m, 'krw': int(krw), 'px': float(px)}
        s['virtual'].append(v); open_.append(v); held.add(m); n += 1
    if n:
        save(data_dir, s)
    return n


def resolve(data_dir, candles, stop_pct, arm, give, now=None):
    """6시간 창이 끝난 가상 매수의 결과를 채운다. candles(m) → 최근 15분봉."""
    now = time.time() if now is None else now
    s = load(data_dir); n = 0
    for v in s['virtual']:
        if 'ret' in v or v['t'] + drop_watch.HOLD_H * 3600 + 900 > now:
            continue
        try:
            res = drop_watch.simulate(v['px'], candles(v['m']), v['t'], stop_pct, arm, give)
        except Exception:
            res = None
        if res is None and now - v['t'] > 40 * 3600:
            v['ret'], v['why'] = None, '자료 없음'; n += 1
        elif res is not None:
            v['ret'], v['why'] = res; n += 1
    if n:
        save(data_dir, s)
    return n


def recent_sum(s, trips, now=None):
    """실거래 끝난 왕복 + 결과 나온 가상 매수, 끝난 순서로 최근 N건의 (건수, 손익 합)."""
    now = time.time() if now is None else now
    xs = [(x['close'], x['pnl']) for x in trips if x.get('close') is not None and x['close'] <= now]
    xs += [(v['t'] + drop_watch.HOLD_H * 3600, v['ret'] * v['krw']) for v in s['virtual'] if isinstance(v.get('ret'), (int, float))]
    last = [p for _, p in sorted(xs)[-N:]]
    return len(last), sum(last)


def _button(action, kind, label, color, ask):
    if not action:
        return ''
    return (f"<button id=bp-{kind} style='font-size:16px;font-weight:700;padding:8px 20px;margin-left:10px;cursor:pointer;background:{color};"
            "color:#fff;border:0;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.25)'>" + label + "</button>"
            f"<span id=bp-{kind}-r class=sub></span><script>(function(){{var A=" + json.dumps(action) + f",b=document.getElementById('bp-{kind}'),"
            f"r=document.getElementById('bp-{kind}-r');b.onclick=function(){{if(!confirm({json.dumps(ask)}))return;b.disabled=true;r.textContent=' ⏳ 처리 중…';"
            f"fetch('http://127.0.0.1:'+A.port+'/{kind}',{{method:'POST',headers:{{'Content-Type':'text/plain'}},body:JSON.stringify({{token:A.token,confirm:true}})}})"
            ".then(function(x){return x.json()}).then(function(d){r.textContent=' '+(d.message||'');setTimeout(function(){location.reload()},4000)})"
            ".catch(function(){r.textContent=' 봇에 연결하지 못함';b.disabled=false})}})()</script>")


def render(data_dir, action, trips, now=None):
    """action: {'port','token'} 또는 None(버튼 없음). trips: pause_watch.trips 결과."""
    now = time.time() if now is None else now
    s = load(data_dir)
    k, tot = recent_sum(s, trips, now)
    sig = (f"최근 {k}건(실거래+가상) 합 <b style='color:{'#1a7f37' if tot > 0 else '#b42318'}'>{tot:+,.0f}원</b>"
           + (" → <b style=color:#1a7f37>다시 살 신호</b>" if k >= N and tot > 0 else (" → 아직 쉴 때" if s['paused'] else " → <b style=color:#b42318>쉴 때</b>") if k >= N else ''))
    vs = s['virtual']
    done = [v for v in vs if isinstance(v.get('ret'), (int, float))]
    pnl = sum(v['ret'] * v['krw'] for v in done)
    pend = sum(1 for v in vs if 'ret' not in v)
    if s['paused']:
        since = dt.datetime.fromtimestamp(s['since'], KST)
        cur = [v for v in vs if v['t'] >= s['since']]
        cd = [v for v in cur if isinstance(v.get('ret'), (int, float))]
        cp = sum(v['ret'] * v['krw'] for v in cd)
        col = '#1a7f37' if cp < 0 else '#b42318' if cp > 0 else 'inherit'
        head = (f"<b style='color:#b42318'>⏸ 매수 쉬는 중</b> · {since:%m-%d %H:%M}부터 · "
                + _button(action, 'resume_buy', '▶ 매수 다시 시작', '#1f6feb', '봇의 신규 자동 매수를 다시 시작할까요?'))
        body = (f"이번 휴식 가상 매수 {len(cur)}건(결과 {len(cd)} · 이익 {sum(v['ret'] > 0 for v in cd)} · 진행 {len(cur) - len(cd)}) · "
                f"샀다면 {cp:+,.0f}원 → <b style='color:{col}'>쉰 효과 {-cp:+,.0f}원</b> │ {sig}")
        recent = ' · '.join(f"{dt.datetime.fromtimestamp(v['t'], KST):%m-%d %H:%M} {v['m'][4:]} "
                            + (f"{v['ret']:+.1%}({v['why']})" if isinstance(v.get('ret'), (int, float)) else '진행') for v in cur[-6:][::-1])
        border = '#b42318'
    else:
        head = ("<b>▶ 매수 중</b> · " + _button(action, 'pause_buy', '⏸ 매수 쉬기', '#b45309',
                                                '봇의 신규 자동 매수를 쉴까요? 손절·이익보호는 계속하고, 사려던 매수는 가상으로 기록합니다.'))
        body = (f"지난 휴식 가상 매수 누계 {len(vs)}건 · 샀다면 {pnl:+,.0f}원" + (f"(진행 {pend})" if pend else '') + " │ " if vs else '') + sig
        recent = ''
        border = '#1f6feb'
    if not action:
        head += "<span class=sub>(버튼은 실행권이 있는 PC의 봇 화면에서만 동작)</span>"
    return (f"<div style='margin:6px 0;padding:8px 12px;border:2px solid {border};border-radius:8px;font-size:15px'>{head}<br>{body}"
            "<div class=sub style='margin-top:3px'>쉬는 동안 봇 자동 매수만 막음(손절·이익보호·순위 매도·직접 매수는 그대로) · "
            f"가상 매수 = 봇이 사려던 때 현재가·주문 금액, 15분봉으로 손절·이익보호·최대 6시간 모의(동시 {SLOTS}개) · "
            "다시 살 신호는 참고용, 재개는 버튼으로 직접" + (f" · 최근 가상: {recent}" if recent else '') + "</div></div>")
