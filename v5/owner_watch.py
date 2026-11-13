"""관찰창: 실행권이 없는 PC에서 실거래 PC(집 봇)가 제대로 돌고 있는지 지켜보고, 실행권을 가져올지 판단하게 돕는다.

사용자 지시(2026-11-13): "실행권이 없는 쪽에서는 보유종목현황을 볼 수 없다. 실행권을 가져와야 할 상황이 되면
의사결정할 수 있게 관찰할 수 있는 창을 만들어."

조회 전용이다. 주문·취소·실행권 변경을 하지 않는다. data/observe.html 을 10초마다 새로 쓴다(브라우저 자동 새로고침).
  pythonw owner_watch.py          계속 갱신(중복 실행 방지: data/owner_watch.lock)
  python owner_watch.py --once    한 번만 쓰고 끝
보는 것: 실행권 기록(GitHub, 30초) · 실제 계좌 보유(account_sync가 쓰는 data/account_live.json, 멈췄으면 직접 조회)
        · 미체결 주문(30초) · 최근 6시간 체결(60초).
판단(judge): 실행권 만료·주인 없음 = 위험, 추정 손절가 아래로 3분 넘게 안 팔린 종목·실행권 갱신 지연 = 주의.
"""
import datetime as dt
import html
import json
import os
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
OUT = DATA / 'observe.html'
LOCK = DATA / 'owner_watch.lock'
KST = dt.timezone(dt.timedelta(hours=9))
STOP_PCT = 0.035        # 손절 상한(strategy.stop_max_pct). 실제 손절가는 이익보호로 더 높을 수 있어 '추정'
BELOW_ALERT = 180       # 추정 손절가 아래에서 이만큼(초) 안 팔리면 주의
RENEW_LATE = 150        # 실행권 기록 갱신이 이만큼(초) 없으면 주의(정상은 약 2분마다 갱신)
e = html.escape


def kst(ts):
    return dt.datetime.fromtimestamp(ts, KST).strftime('%m-%d %H:%M:%S')


def fmt(px):
    if not isinstance(px, (int, float)) or px <= 0: return '—'
    return f'{px:,.0f}' if px >= 100 else f'{px:,.4g}' if px >= 1 else f'{px:.4g}'


def ago(sec):
    sec = int(max(0, sec))
    return f'{sec}초' if sec < 120 else f'{sec // 60}분' if sec < 7200 else f'{sec // 3600}시간'


def judge(lease, holdings, now, me=None, below_since=None):
    """(등급, 결론, 이유 목록). 등급: ok | warn | bad. 조회 전용 판단이며 아무것도 바꾸지 않는다."""
    me = me or socket.gethostname()
    below_since = below_since or {}
    why, level = [], 'ok'
    if lease is None:
        return 'warn', '실행권 기록을 읽지 못함(인터넷·GitHub 확인)', ['GitHub 실행권 기록 조회 실패']
    holder, owner = lease.get('pc') or '?', lease.get('owner') or ''
    if owner and holder == me and now < lease.get('expires', 0):
        return 'ok', f'이 PC({me})가 실행권을 갖고 있음 — 이 창은 다른 PC에서 볼 때 쓰는 화면', []
    if not owner:
        level = 'bad'; why.append(f'아무 PC도 실행권이 없음(마지막 {holder}) → 지금은 어느 봇도 손절하지 못함')
    elif now > lease.get('expires', 0):
        level = 'bad'; why.append(f'{holder} 실행권이 {ago(now - lease["expires"])} 전에 만료됨 → 그 PC 봇이 꺼졌거나 인터넷이 끊김')
    elif now - lease.get('updated', 0) > RENEW_LATE:
        level = 'warn'; why.append(f'{holder} 실행권 갱신이 {ago(now - lease["updated"])}째 없음(정상은 약 2분마다)')
    if lease.get('request'):
        level = 'warn' if level == 'ok' else level; why.append('다른 PC가 실행권 양도를 요청 중')
    for h in holdings:
        t = below_since.get(h['market'])
        if h.get('stop') and t and now - t >= BELOW_ALERT:
            level = 'warn' if level == 'ok' else level
            why.append(f"{h['market'][4:]}: 추정 손절가 아래로 {ago(now - t)}째 안 팔림(이익보호로 손절가가 낮게 잡혔거나 실거래 봇 이상)")
    head = {'ok': f'{holder} 봇 정상 — 실행권을 가져올 필요 없음',
            'warn': '주의 — 아래 이유를 보고 집 PC 상태를 확인',
            'bad': '실행권 가져오기 검토 — 지금 실거래 봇이 주문하지 못하는 상태'}[level]
    return level, head, why


class Source:
    """조회 결과를 주기별로 캐시한다. 실패해도 마지막 값과 오류를 남긴다."""
    def __init__(self, every, fn):
        self.every, self.fn, self.at, self.val, self.err = every, fn, 0.0, None, ''

    def get(self, now):
        if now - self.at >= self.every:
            self.at = now
            try:
                self.val, self.err = self.fn(), ''
            except Exception as ex:
                self.err = f'{type(ex).__name__}: {str(ex)[:120]}'
        return self.val


def read_lease():
    from session_guard import GitStore
    return GitStore(DATA / 'session_git').read()[1]


def read_account():
    """account_sync가 5초마다 쓰는 파일을 쓴다. 1분 넘게 멈췄으면 직접 조회(GET)."""
    f = DATA / 'account_live.json'
    try:
        d = json.loads(f.read_text(encoding='utf-8'))
        if time.time() - d.get('epoch', 0) < 60:
            return d, 'account_sync'
    except (OSError, ValueError):
        pass
    import upbit
    rows = upbit.accounts()
    coins = {'KRW-' + r['currency']: r for r in rows if r['currency'] != 'KRW'}
    px = {m: t['trade_price'] for m, t in upbit.tickers(list(coins)).items()} if coins else {}
    assets = [{'currency': 'KRW', 'quantity': float(r['balance']) + float(r['locked']), 'price': 1.0}
              for r in rows if r['currency'] == 'KRW']
    for m, r in coins.items():
        q = float(r['balance']) + float(r['locked'])
        assets.append({'currency': m[4:], 'quantity': q, 'avg': float(r['avg_buy_price']), 'price': px.get(m),
                       'value': q * px[m] if px.get(m) else None})
    return {'epoch': time.time(), 'assets': assets}, '직접 조회'


def read_open():
    import pnl
    return pnl._get('/v1/orders/open', [('states[]', 'wait'), ('states[]', 'watch'), ('limit', 100)])


def read_closed():
    import pnl
    now = dt.datetime.now(KST)
    rows = pnl.closed_between(now - dt.timedelta(hours=6), now + dt.timedelta(minutes=1))
    return sorted([o for o in rows if float(o.get('executed_volume') or 0) > 0], key=lambda o: o['created_at'], reverse=True)


def protected():
    try:
        import asset_protection as a
        p = a.Policy(DATA / 'user_protected_assets.json', [])
        return set(a.effective(p.read(), p.configured))
    except Exception:
        return {'KRW-BTC', 'KRW-DOGE', 'KRW-XRP'}


def holdings_from(account, prot):
    out = []
    for a in (account or {}).get('assets', []):
        if a.get('currency') == 'KRW': continue
        m, px, avg = 'KRW-' + a['currency'], a.get('price'), a.get('avg')
        value = a.get('value') or (a['quantity'] * px if px else None)
        if not value or value < 5000: continue
        guarded = m in prot
        stop = None if guarded or not avg else avg * (1 - STOP_PCT)
        out.append({'market': m, 'qty': a['quantity'], 'avg': avg, 'px': px, 'value': value, 'protected': guarded, 'stop': stop,
                    'pnl': (px / avg - 1) if px and avg else None})
    return sorted(out, key=lambda h: -h['value'])


CSS = """:root{--bg:#f7f7f5;--fg:#1d1d1b;--mut:#6b6b66;--card:#fff;--line:#e3e2dd;--ok:#1f7a4d;--warn:#a86400;--bad:#b3261e;--okb:#e6f4ec;--warnb:#fff3dc;--badb:#fde8e6}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ececea;--mut:#9a9a94;--card:#1f1f1d;--line:#33332f;--ok:#5cc28d;--warn:#f0b351;--bad:#ff8a80;--okb:#173325;--warnb:#3a2c12;--badb:#3d1c1a}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 'Malgun Gothic',system-ui,sans-serif}
main{max-width:1000px;margin:0 auto;padding:16px}h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:22px 0 8px}
.sub{color:var(--mut);font-size:12px}.lamp{border-radius:10px;padding:14px 16px;margin:12px 0;border:1px solid var(--line)}
.ok{background:var(--okb);border-color:var(--ok)}.warn{background:var(--warnb);border-color:var(--warn)}.bad{background:var(--badb);border-color:var(--bad)}
.lamp b{font-size:17px}.lamp ul{margin:6px 0 0 18px;padding:0}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}th{font-size:12px;color:var(--mut);font-weight:600}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}.up{color:var(--bad)}.dn{color:#1f5fbf}tr.below td{background:var(--badb)}
.wrap{overflow-x:auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:8px}
.kv{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px}.kv div:first-child{font-size:12px;color:var(--mut)}
.guide{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px}.guide li{margin:3px 0}"""


def render(now, level, head, why, lease, lease_err, holdings, acct_src, acct_age, krw, opens, open_err, closed, closed_err, below_since):
    sign = lambda v: '' if v is None else ('up' if v > 0 else 'dn' if v < 0 else '')
    rows = []
    for h in holdings:
        below = h['stop'] and h['px'] and h['px'] <= h['stop']
        dist = (h['px'] / h['stop'] - 1) if h['stop'] and h['px'] else None
        kind = '보호(봇 매도 안 함)' if h['protected'] else '봇 관리'
        stop_txt = '—' if h['protected'] else fmt(h['stop'])
        dist_txt = '—' if dist is None else (f"아래 {ago(now - below_since[h['market']])}째" if below and h['market'] in below_since else f'{dist:+.2%}')
        rows.append(f"<tr class='{'below' if below else ''}'><td>{e(h['market'][4:])}</td><td>{kind}</td><td class=n>{h['qty']:,.8g}</td>"
                    f"<td class=n>{fmt(h['avg'])}</td><td class=n>{fmt(h['px'])}</td><td class=n>{h['value']:,.0f}</td>"
                    f"<td class='n {sign(h['pnl'])}'>{'—' if h['pnl'] is None else format(h['pnl'], '+.2%')}</td>"
                    f"<td class=n>{stop_txt}</td><td class=n>{dist_txt}</td></tr>")
    total = sum(h['value'] for h in holdings) + (krw or 0)
    orow = ''.join(f"<tr><td>{e(o['created_at'][5:16].replace('T', ' '))}</td><td>{e(o['market'][4:])}</td><td>{'매수' if o['side'] == 'bid' else '매도'}</td>"
                   f"<td>{e(o.get('ord_type') or '')}{' · ' + e(o['time_in_force']) if o.get('time_in_force') else ''}</td>"
                   f"<td class=n>{fmt(float(o['price'])) if o.get('price') else '시장가'}</td><td class=n>{float(o.get('remaining_volume') or 0):,.8g}</td></tr>"
                   for o in (opens or [])) or "<tr><td colspan=6 class=sub>없음</td></tr>"
    crow = []
    for o in (closed or [])[:25]:
        v, f = float(o['executed_volume']), float(o.get('executed_funds') or 0)
        crow.append(f"<tr><td>{e(o['created_at'][5:16].replace('T', ' '))}</td><td>{e(o['market'][4:])}</td>"
                    f"<td class='{'up' if o['side'] == 'bid' else 'dn'}'>{'매수' if o['side'] == 'bid' else '매도'}</td>"
                    f"<td class=n>{fmt(f / v) if v else '—'}</td><td class=n>{f:,.0f}</td><td>{e(o.get('time_in_force') or o.get('ord_type') or '')}</td></tr>")
    crow = ''.join(crow) or "<tr><td colspan=6 class=sub>최근 6시간 체결 없음</td></tr>"
    L = lease or {}
    lease_cells = [('실행권 보유 PC', e(L.get('pc') or '없음') + (' (이 PC)' if L.get('pc') == socket.gethostname() else '')),
                   ('마지막 갱신', (ago(now - L['updated']) + ' 전') if L.get('updated') else '—'),
                   ('만료까지', (ago(L['expires'] - now) if L.get('expires', 0) > now else '만료됨') if L.get('expires') else '—'),
                   ('계좌 조회', f'{acct_src} · {ago(acct_age)} 전' if acct_age is not None else '실패')]
    kv = ''.join(f"<div class=kv><div>{k}</div><div><b>{v}</b></div></div>" for k, v in lease_cells)
    errs = ' · '.join(x for x in (lease_err and '실행권 ' + lease_err, open_err and '미체결 ' + open_err, closed_err and '체결 ' + closed_err) if x)
    why_html = ('<ul>' + ''.join(f'<li>{e(w)}</li>' for w in why) + '</ul>') if why else ''
    return f"""<!doctype html><html lang=ko><head><meta charset=utf-8><meta http-equiv=refresh content=10>
<meta name=viewport content="width=device-width,initial-scale=1"><title>자동거래 관찰창</title><style>{CSS}</style></head><body><main>
<h1>자동거래 관찰창 <span class=sub>· {e(socket.gethostname())} · 조회 전용(주문 없음) · {kst(now)} · 10초마다 새로 고침</span></h1>
<div class='lamp {level}'><b>{e(head)}</b>{why_html}</div>
<div class=grid>{kv}</div>
<h2>보유 종목 (실제 계좌) <span class=sub>· 총 {total:,.0f}원 · 원화 {krw or 0:,.0f}원</span></h2>
<div class=wrap><table><tr><th>코인</th><th>구분</th><th class=n>수량</th><th class=n>평단</th><th class=n>현재가</th><th class=n>평가액</th><th class=n>손익률</th><th class=n>추정 손절가</th><th class=n>손절까지</th></tr>
{''.join(rows) or '<tr><td colspan=9 class=sub>5,000원 이상 보유 없음</td></tr>'}</table></div>
<div class=sub>추정 손절가 = 평단 −{STOP_PCT:.1%}(손절 상한). 실거래 봇의 실제 손절가는 그 PC 장부에만 있고, 이익보호·본전 잠금으로 더 높을 수 있다. 빨간 줄 = 현재가가 추정 손절가 이하.</div>
<h2>미체결 주문</h2><div class=wrap><table><tr><th>접수</th><th>코인</th><th>방향</th><th>방식</th><th class=n>가격</th><th class=n>남은 수량</th></tr>{orow}</table></div>
<h2>최근 6시간 체결 <span class=sub>(실거래 봇이 움직이고 있는지 보는 곳)</span></h2>
<div class=wrap><table><tr><th>접수</th><th>코인</th><th>방향</th><th class=n>평균가</th><th class=n>금액</th><th>방식</th></tr>{crow}</table></div>
<h2>실행권을 가져와야 할 때</h2><div class=guide><ul>
<li><b>가져올 상황</b>: 위 램프가 빨강(실행권 만료·주인 없음)이고 집 PC를 바로 확인할 수 없을 때. 노랑은 먼저 집 PC 상태(전원·인터넷·봇 창)를 확인한다.</li>
<li><b>방법</b>: 바탕화면 <b>「자동거래 이 PC에서 시작」</b>. 확인창 한 번 뒤 최신 코드 받기 → 집 봇에 양도 요청 → 계좌 인수 → 이 PC 봇 시작. 손절가는 현재가 기준으로 다시 잡힌다.</li>
<li><b>주의</b>: 집 봇이 살아 있으면 양도 요청을 받아 스스로 멈춘 뒤 넘겨준다. 집 PC가 꺼져 응답이 없으면 실거래 연결을 넘기지 않고 멈출 수 있다(두 PC 동시 실거래 방지). 그때는 Claude에게 상황을 말한다.</li>
<li>보호 종목(BTC·XRP·DOGE 등)은 어느 PC 봇도 팔지 않는다.</li></ul></div>
<div class=sub style='margin-top:10px'>{e(errs)}</div>
</main></body></html>"""


class Watcher:
    def __init__(self):
        self.lease = Source(30, read_lease)
        self.account = Source(10, read_account)
        self.open = Source(30, read_open)
        self.closed = Source(60, read_closed)
        self.below_since, self.last_level = {}, 'ok'

    def tick(self, now=None):
        now = time.time() if now is None else now
        lease = self.lease.get(now)
        acct = self.account.get(now)
        account, src = acct if acct else (None, '실패')
        holdings = holdings_from(account, protected())
        for h in holdings:
            if h['stop'] and h['px'] and h['px'] <= h['stop']:
                self.below_since.setdefault(h['market'], now)
            else:
                self.below_since.pop(h['market'], None)
        level, head, why = judge(lease if not self.lease.err or lease else None, holdings, now, below_since=self.below_since)
        krw = sum(a['quantity'] for a in (account or {}).get('assets', []) if a.get('currency') == 'KRW')
        page = render(now, level, head, why, lease, self.lease.err, holdings, src,
                      (now - account['epoch']) if account else None, krw,
                      self.open.get(now), self.open.err, self.closed.get(now), self.closed.err, self.below_since)
        tmp = OUT.with_suffix('.tmp')
        tmp.write_text(page, encoding='utf-8'); tmp.replace(OUT)
        if level == 'bad' and self.last_level != 'bad':
            try:
                from session_guard import notify
                notify(head + ' · ' + '; '.join(why)[:200], '자동거래 관찰창')
            except Exception:
                pass
        self.last_level = level
        return level


def lock_alive():
    try:
        pid = int(LOCK.read_text().strip())
    except (OSError, ValueError):
        return False
    if pid == os.getpid(): return False
    import subprocess
    r = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'], capture_output=True, text=True, errors='replace',
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return f'"{pid}"' in (r.stdout or '')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    os.chdir(ROOT); sys.path.insert(0, str(ROOT)); DATA.mkdir(exist_ok=True)
    w = Watcher()
    if '--once' in argv:
        print(w.tick()); return 0
    if lock_alive():
        return 0
    LOCK.write_text(str(os.getpid()))
    while not (DATA / 'owner_watch_stop.txt').exists():
        try:
            w.tick()
        except Exception as ex:
            try:
                (DATA / 'owner_watch.err').write_text(f'{kst(time.time())} {type(ex).__name__}: {ex}', encoding='utf-8')
            except OSError:
                pass
        time.sleep(10)
    (DATA / 'owner_watch_stop.txt').unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
