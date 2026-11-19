"""사용자 매수 쉬기·다시 시작(사용자 지시 2026-11-15 '쉬어가는 아이콘, 다시 시작하는 아이콘, 쉬면서 가상 매수 기록').

- 쉬는 동안: 봇의 자동 매수(주문안의 매수)만 막는다. 손절·이익보호·순위 매도·사용자 직접 매수는 그대로.
- 막힌 매수는 '가상 매수'로 기록한다(그 시점 현재가, 주문안 금액). 같은 종목이 가상 보유 중이면 다시 세지 않고, 가상 동시 보유는 SLOTS개까지.
- 가상 결과: 15분봉으로 손절·이익보호·최대 6시간(drop_watch.simulate와 같은 모의). 실제 봇은 순위 매도로 더 일찍/늦게 팔 수 있다 → 근사.
- 다시 살 신호(참고): 실거래 끝난 왕복과 결과 나온 가상 매수를 합친 최근 20건 손익 합이 플러스.
파일: data/buy_pause.json (봇만 쓴다. live.json과 별개라 실행권 체크섬과 무관)
"""
import datetime as dt
import html
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
        v = {'t': now, 'm': m, 'krw': int(krw), 'px': float(px), 'pause': s.get('since')}
        s['virtual'].append(v); open_.append(v); held.add(m); n += 1
    if n:
        save(data_dir, s); write_csv(data_dir, s)
    return n


def simulate(px, bars, start, stop_pct, arm, give):
    """drop_watch.simulate와 같은 규칙(손절 → 이익보호 → 최대 6시간). 반환 (수익률, 사유, 매도 시각, 매도가) 또는 None."""
    hold = drop_watch.HOLD_H * 3600
    xs = [(dt.datetime.fromisoformat(b['t']).timestamp(), b) for b in bars]
    xs = [(t, b) for t, b in xs if start <= t < start + hold]
    if len(xs) < drop_watch.HOLD_H * 4 - 1:
        return None
    stop, peak = px * (1 - stop_pct), px
    for t, b in xs:
        if b['l'] <= stop:
            out = min(stop, b['o']); return out / px - 1 - drop_watch.FEE2, '손절', t + 900, out
        peak = max(peak, b['h'])
        if arm > 0 and peak >= px * (1 + arm) and b['c'] <= px + (peak - px) * (1 - give):
            return b['c'] / px - 1 - drop_watch.FEE2, '이익보호', t + 900, b['c']
    t, b = xs[-1]
    return b['c'] / px - 1 - drop_watch.FEE2, '6시간', t + 900, b['c']


CSV = 'buy_pause_trades.csv'


def write_csv(data_dir, s):
    """엑셀에서 바로 여는 가상 거래 내역(UTF-8 BOM). 실패해도 무시."""
    def f(t):
        return dt.datetime.fromtimestamp(t, KST).strftime('%Y-%m-%d %H:%M') if t else ''
    rows = ['휴식 시작,매수 시각,코인,금액,매수가,매도 시각,매도가,사유,수익금액,수익률']
    for v in s['virtual']:
        r = v.get('ret')
        rows.append(','.join(str(x) for x in (f(v.get('pause')), f(v['t']), v['m'][4:], v['krw'], v['px'], f(v.get('out_t')),
                                              v.get('out_px', ''), v.get('why', '진행'),
                                              round(r * v['krw']) if isinstance(r, (int, float)) else '',
                                              f'{r:.4f}' if isinstance(r, (int, float)) else '')))
    try:
        (Path(data_dir) / CSV).write_text('\n'.join(rows) + '\n', encoding='utf-8-sig')
    except OSError:
        pass


def resolve(data_dir, candles, stop_pct, arm, give, now=None):
    """6시간 창이 끝난 가상 매수의 결과를 채운다. candles(m) → 최근 15분봉."""
    now = time.time() if now is None else now
    s = load(data_dir); n = 0
    for v in s['virtual']:
        if 'ret' in v or v['t'] + drop_watch.HOLD_H * 3600 + 900 > now:
            continue
        try:
            res = simulate(v['px'], candles(v['m']), v['t'], stop_pct, arm, give)
        except Exception:
            res = None
        if res is None and now - v['t'] > 40 * 3600:
            v['ret'], v['why'] = None, '자료 없음'; n += 1
        elif res is not None:
            v['ret'], v['why'], v['out_t'], v['out_px'] = res; n += 1
    if n:
        save(data_dir, s); write_csv(data_dir, s)
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


def render(data_dir, action, trips, now=None, elsewhere=None):
    """action: {'port','token'} 또는 None(버튼 없음). trips: pause_watch.trips 결과.
    elsewhere: 이 PC에 실행권이 없을 때 실행권 PC 이름(없으면 None). 쉬기 상태는 PC마다 따로 저장돼 실행권과 함께 넘어가지 않으므로
    실행권 없는 PC에서는 이 상자가 실제 계좌 상태가 아님을 밝히고, 쉬기 뒤 실제 계좌 매수를 함께 보여 준다(2026-11-19 사용자 지시)."""
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
    if elsewhere is not None:
        head = notice(s, trips, elsewhere) + head.replace('⏸ 매수 쉬는 중', '⏸ 이 PC 기록: 쉬는 중')
    table = history_table(vs)
    return (f"<div style='margin:6px 0;padding:8px 12px;border:2px solid {border};border-radius:8px;font-size:15px'>{head}<br>{body}"
            "<div class=sub style='margin-top:3px'>쉬는 동안 봇 자동 매수만 막음(손절·이익보호·순위 매도·직접 매수는 그대로) · "
            f"가상 매수 = 봇이 사려던 때 현재가·주문 금액, 15분봉으로 손절·이익보호·최대 6시간 모의(동시 {SLOTS}개) · "
            "다시 살 신호는 참고용, 재개는 버튼으로 직접" + (f" · 최근 가상: {recent}" if recent else '') + f"</div>{table}</div>")


def notice(s, trips, pc):
    """실행권 없는 PC의 안내 줄. 실제 계좌 매수 = 거래소 체결 기록에서 쉬기 시작 뒤 매수로 시작한 왕복."""
    pc = html.escape(str(pc or '?'))
    if not s['paused']:
        return (f"<div style='background:#fff4e5;border-radius:6px;padding:4px 8px;margin-bottom:4px'>⚠ 실행권은 <b>{pc}</b> PC에 있습니다. "
                "쉬기 상태는 PC마다 따로 저장되므로 쉬기·다시 시작은 그 PC 화면에서 확인·조작하세요.</div>")
    since = s['since']
    after = [x for x in trips if x['open'] >= since]
    pnl = sum(x['pnl'] for x in after)
    last = max((x['open'] for x in after), default=None)
    real = (f"쉬기 시작 뒤 실제 계좌가 산 거래 {len(after)}건(끝난 것) · 합 <b style='color:{'#b42318' if pnl > 0 else '#1f6feb'}'>{pnl:+,.0f}원</b>"
            f" · 마지막 매수 {dt.datetime.fromtimestamp(last, KST):%m-%d %H:%M} → <b>실제 계좌는 매수 중</b>" if after
            else '쉬기 시작 뒤 실제 계좌 매수 기록 없음(끝난 거래 기준)')
    return (f"<div style='background:#fff4e5;border-radius:6px;padding:4px 8px;margin-bottom:4px'>⚠ 실행권은 <b>{pc}</b> PC에 있습니다. "
            "아래 '쉬는 중'은 <b>이 PC에만 저장된 상태</b>라 실제 계좌에 적용되지 않습니다(쉬기 상태는 실행권과 함께 넘어가지 않음). "
            f"가상 매수 기록도 이 PC가 실행권을 잃은 뒤 멈춥니다. │ {real}</div>")


def history_table(vs, limit=200):
    """쉬는 동안 가상 거래 전체 내역(휴식 구간별 합계 + 최근 limit건). 수익 빨강·손실 파랑."""
    if not vs:
        return ''
    def f(t):
        return dt.datetime.fromtimestamp(t, KST).strftime('%m-%d %H:%M') if t else '—'
    def money(r, krw):
        if not isinstance(r, (int, float)):
            return '<td class=n>—</td><td class=n>—</td>'
        c = '#d22' if r > 0 else '#1756c9' if r < 0 else 'inherit'
        return f"<td class=n style='color:{c}'>{r * krw:+,.0f}원</td><td class=n style='color:{c}'>{r:+.2%}</td>"
    groups = {}
    for v in vs:
        g = groups.setdefault(v.get('pause'), [0, 0, 0.])
        g[0] += 1
        if isinstance(v.get('ret'), (int, float)):
            g[1] += 1; g[2] += v['ret'] * v['krw']
    gl = ' · '.join(f"{f(k)} 시작 휴식: {a}건(결과 {b}) 샀다면 {c:+,.0f}원" for k, (a, b, c) in sorted(groups.items(), key=lambda x: x[0] or 0))
    rows = ''.join(f"<tr><td>{f(v['t'])}</td><td>{v['m'][4:]}</td><td class=n>{v['krw']:,}</td><td class=n>{v['px']:,.8g}</td>"
                   f"<td>{f(v.get('out_t')) if v.get('out_t') else '—'}</td><td class=n>{format(v['out_px'], ',.8g') if v.get('out_px') else '—'}</td>"
                   f"<td>{v.get('why', '진행 중')}</td>{money(v.get('ret'), v['krw'])}</tr>" for v in vs[-limit:][::-1])
    return (f"<details style='margin-top:4px'><summary>가상 거래 내역 {len(vs)}건 — 눌러서 열기</summary>"
            f"<div class=sub>{gl} · 전체 내역 파일: data/{CSV}(엑셀로 열림)</div>"
            "<table><tr><th>매수 시각</th><th>코인</th><th class=n>금액</th><th class=n>매수가</th><th>매도 시각</th><th class=n>매도가</th>"
            f"<th>사유</th><th class=n>수익금액</th><th class=n>수익률</th></tr>{rows}</table></details>")
