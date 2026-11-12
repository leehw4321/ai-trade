"""급락 중 매수 보류 관찰(사용자 지시 2026-11-12: '2시간 6% 선 즉시 적용, 나머지는 관찰, 봇 맨 위에 매일 결과, 2주 뒤 재결정').

기준(직전 등락은 판단 때 방금 마감된 15분봉 종가 ÷ N시간 전 봉 시가 − 1, strategy.recent_change)
  2h6  직전 2시간 ≤ −6%   실거래 적용(strategy.drop2h_block_pct = 0.06)
  2h4  직전 2시간 ≤ −4%   관찰만
  4h10 직전 4시간 ≤ −10%  관찰만

기록 대상: 정시 주문안에서 매수 신호(순위·상승률)가 있고, '급락 중' 말고 다른 차단이 없는 후보(missed_ledger의 blocks 기준).
  같은 종목은 앞 기록의 6시간 창이 끝나기 전에는 다시 기록하지 않는다(같은 장면을 여러 번 세지 않음). 단 그 사이 새 기준이 걸리면 기록한다.
'샀다면' 결과: 그 정시 신호 종가에 7만 원어치 샀다고 보고, 이후 15분봉으로 손절(stop_max_pct) → 이익보호(arm·giveback) → 6시간 종가 순으로 판다.
  같은 봉에서 고가·저가 순서는 모르므로 손절을 먼저 본다(보수). 수수료 왕복 0.1%. 실제 체결·슬리피지는 반영하지 않는다(모의).
막은 효과 = −(걸린 후보의 '샀다면' 손익 합). 양수면 막아서 이득.
파일: data/drop_watch.json (shadow만 쓴다, 봇은 읽기만)
"""
import datetime as dt
import json
import os
import time
from pathlib import Path

RULES = (('2h6', 'r2', -0.06, '2시간 −6%', True), ('2h4', 'r2', -0.04, '2시간 −4%', False), ('4h10', 'r4', -0.10, '4시간 −10%', False))
HOLD_H, SIZE, FEE2 = 6, 70000, 0.001
DECIDE_DAYS = 14
FILE = 'drop_watch.json'
KST = dt.timezone(dt.timedelta(hours=9))


def load(data_dir):
    try:
        return json.loads((Path(data_dir) / FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'since': None, 'obs': []}


def save(data_dir, s):
    p = Path(data_dir) / FILE
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(s, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, p)


def hits(r2, r4):
    out = []
    for key, field, lim, _, _ in RULES:
        v = r2 if field == 'r2' else r4
        if isinstance(v, (int, float)) and v <= lim:
            out.append(key)
    return out


def record(data_dir, slot, ranked, missed_rows, now=None):
    """정시 주문안 직후 호출. missed_rows = missed_ledger.record 반환값."""
    now = now or time.time()
    s = load(data_dir)
    s['since'] = s.get('since') or now
    by = {r['market']: r for r in ranked}
    busy = {}   # 6시간 창이 안 끝난 종목 → 이미 기록된 기준들. 새 기준이 걸리면 다시 기록한다(먼저 통과 후보로 잡혀 급락 차단이 빠지는 것 방지)
    for o in s['obs']:
        if o['t'] + HOLD_H * 3600 > slot: busy.setdefault(o['m'], set()).update(o['hits'])
    added = 0
    for row in missed_rows:
        m = row['market']
        if not row.get('signal') or not isinstance(row.get('close'), (int, float)) or row['close'] <= 0:
            continue
        if any(b != '급락 중(2시간)' for b in row.get('blocks') or []):
            continue
        r = by.get(m) or {}
        h = hits(r.get('r2'), r.get('r4'))
        if m in busy and set(h) <= busy[m]:
            continue
        s['obs'].append({'m': m, 't': slot, 'px': row['close'], 'rank': row.get('rank'), 'r2': r.get('r2'), 'r4': r.get('r4'),
                         'hits': h, 'bought': row.get('outcome') == '매수'})
        busy.setdefault(m, set()).update(h); added += 1
    save(data_dir, s)
    return added


def simulate(px, bars, start, stop_pct, arm, give):
    """bars: 15분봉(시간순, t=UTC 시작 ISO). start 이후 HOLD_H시간. 반환 (수익률, 사유) 또는 None(자료 부족)."""
    end = start + HOLD_H * 3600
    xs = [b for b in bars if start <= dt.datetime.fromisoformat(b['t']).timestamp() < end]
    if len(xs) < HOLD_H * 4 - 1:
        return None
    stop, peak = px * (1 - stop_pct), px
    for b in xs:
        if b['l'] <= stop:
            return min(stop, b['o']) / px - 1 - FEE2, '손절'
        peak = max(peak, b['h'])
        if arm > 0 and peak >= px * (1 + arm) and b['c'] <= px + (peak - px) * (1 - give):
            return b['c'] / px - 1 - FEE2, '이익보호'
    return xs[-1]['c'] / px - 1 - FEE2, '6시간'


def resolve(data_dir, candles, stop_pct, arm, give, now=None):
    """6시간 창이 끝난 기록의 '샀다면' 결과를 채운다. candles(m) → 15분봉 목록(최근 200개)."""
    now = now or time.time()
    s = load(data_dir); n = 0
    for o in s['obs']:
        if 'ret' in o or o['t'] + HOLD_H * 3600 + 900 > now:
            continue
        try:
            res = simulate(o['px'], candles(o['m']), o['t'], stop_pct, arm, give)
        except Exception:
            res = None
        if res is None and now - o['t'] > 40 * 3600:   # 200봉(50시간) 밖으로 밀려나면 포기
            o['ret'], o['why'] = None, '자료 없음'; n += 1
        elif res is not None:
            o['ret'], o['why'] = res; n += 1
        time.sleep(0.12)
    if n: save(data_dir, s)
    return n


def summary(s):
    done = [o for o in s['obs'] if isinstance(o.get('ret'), (int, float))]
    rows = {}
    for key, *_ in RULES:
        h = [o for o in done if key in o['hits']]
        rows[key] = {'n': len(h), 'win': sum(o['ret'] > 0 for o in h), 'pnl': sum(o['ret'] for o in h) * SIZE,
                     'open': sum(1 for o in s['obs'] if key in o['hits'] and 'ret' not in o)}
    clean = [o for o in done if not o['hits']]
    days = {}
    for o in done:
        d = dt.datetime.fromtimestamp(o['t'], KST).strftime('%m-%d')
        x = days.setdefault(d, {k: [0, 0.0] for k, *_ in RULES})
        for k in o['hits']:
            x[k][0] += 1; x[k][1] += o['ret'] * SIZE
    return rows, clean, days


def render(data_dir, live_pct=None, now=None):
    now = now or time.time()
    s = load(data_dir)
    since = s.get('since')
    if not since:
        return ("<div style='margin:6px 0;padding:8px 12px;border:2px solid #0e7490;border-radius:8px;font-size:15px'>"
                "<b>🧪 급락 중 매수 보류</b> · 다음 정시 주문안부터 기록 시작</div>")
    rows, clean, days = summary(s)
    left = since + DECIDE_DAYS * 86400 - now
    due = dt.datetime.fromtimestamp(since + DECIDE_DAYS * 86400, KST).strftime('%m-%d')
    when = dt.datetime.fromtimestamp(since, KST).strftime('%m-%d %H:%M')
    dd = f'재결정 {due} (D-{max(0, int(left // 86400) + (1 if left % 86400 else 0))})' if left > 0 else f'<b style=color:#b42318>재결정일 {due} 지남 → 결정 필요</b>'
    base = (f"통과 후보 {len(clean)}건 평균 {sum(o['ret'] for o in clean) / len(clean):+.2%}" if clean else '통과 후보 결과 대기')
    cells = []
    for key, _, _, name, live in RULES:
        r = rows[key]
        eff = -r['pnl']
        col = '#1a7f37' if eff > 0 else '#b42318' if eff < 0 else 'inherit'
        tag = '적용' + (f' {live_pct:.0%}' if live_pct else '') if live else '관찰'
        body = (f"{r['n']}건(이익 {r['win']}) · 샀다면 {r['pnl']:+,.0f}원 → <b style='color:{col}'>막은 효과 {eff:+,.0f}원</b>"
                if r['n'] else '끝난 건 없음')
        cells.append(f"<b>{name}</b>[{tag}] {body}" + (f" · 진행 {r['open']}" if r['open'] else ''))
    day_txt = ' · '.join(f"{d}: " + '/'.join(f"{x[k][0]}건 {-x[k][1]:+,.0f}" for k, *_ in RULES)
                         for d, x in sorted(days.items())[-14:]) or '아직 없음'
    return ("<div style='margin:6px 0;padding:8px 12px;border:2px solid #0e7490;border-radius:8px;font-size:15px'>"
            f"<b>🧪 급락 중 매수 보류</b> · {when}부터 · {dd}<br>" + ' │ '.join(cells) +
            f"<div class=sub style='margin-top:3px'>일별 막은 효과(2시간−6% / 2시간−4% / 4시간−10%): {day_txt}"
            f" · {base} · 샀다면 = 정시 신호 종가에 7만 원 매수, 15분봉으로 손절·이익보호·6시간(모의, 실제 체결 아님)"
            " · 효과가 없으면 설정 strategy.drop2h_block_pct=0 으로 원래대로</div></div>")
