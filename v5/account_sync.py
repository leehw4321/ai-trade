"""Read-only, independent account display; no order methods are called."""
import datetime as dt
import html
import json
import os
from pathlib import Path
import time
import msvcrt

import upbit

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'


DUST_KRW = 5000  # 평가금액이 이 값 미만이거나 시세가 없는(미평가) 보유는 기본으로 숨긴다


def snapshot():
    balances = upbit.accounts()
    markets = upbit.krw_markets()
    wanted = ['KRW-' + r['currency'] for r in balances
              if r['currency'] != 'KRW' and 'KRW-' + r['currency'] in markets]
    tick = upbit.tickers(wanted) if wanted else {}
    assets = []
    for r in balances:
        cur = r['currency']
        available, locked = float(r['balance']), float(r['locked'])
        price = 1 if cur == 'KRW' else tick.get('KRW-' + cur, {}).get('trade_price')
        assets.append(dict(currency=cur, available=available, locked=locked,
                           quantity=available + locked, avg=float(r['avg_buy_price']),
                           name='원화' if cur == 'KRW' else markets.get('KRW-' + cur, {}).get('name', cur),
                           price=price, value=None if price is None else (available + locked) * price))
    return {'time': dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec='seconds'),
            'epoch': time.time(), 'assets': assets,
            'priced_total': sum(a['value'] for a in assets if a['value'] is not None)}


def atomic(path, content):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(content, encoding='utf-8')
    os.replace(temp, path)


def performance(assets):
    """Unrealized return on holdings with both a price and a known positive cost."""
    comparable = [a for a in assets if a['currency'] != 'KRW'
                  and a['value'] is not None and a['avg'] > 0]
    cost = sum(a['quantity'] * a['avg'] for a in comparable)
    pnl = sum(a['value'] - a['quantity'] * a['avg'] for a in comparable)
    return cost, pnl, pnl / cost * 100 if cost else None


def render(data, error=''):
    esc = html.escape
    assets = data.get('assets', [])
    total = data.get('priced_total', 0)
    cost, pnl, ret = performance(assets)
    cash = sum(a['value'] or 0 for a in assets if a['currency'] == 'KRW')
    available = sum(a['available'] for a in assets if a['currency'] == 'KRW')
    coins = [a for a in assets if a['currency'] != 'KRW']
    unknown = sum(a['value'] is None for a in coins)
    unknown_cost = sum(a['value'] is not None and a['avg'] <= 0 for a in coins)
    tone = lambda n: 'gain' if n > 0 else 'loss' if n < 0 else 'neutral'
    money = lambda n: f'{n:,.0f}'
    def price(n):
        if n is None: return '—'
        return f'{n:,.0f}' if n >= 1000 else f'{n:,.4f}'.rstrip('0').rstrip('.')
    rows = []
    dust_n = sum(1 for a in coins if a['value'] is None or a['value'] < DUST_KRW)
    for a in sorted(coins, key=lambda a: -(a['value'] or 0)):
        known = a['value'] is not None and a['avg'] > 0
        buy = a['quantity'] * a['avg'] if a['avg'] > 0 else None
        gain = a['value'] - buy if known else None
        rate = gain / buy * 100 if known and buy else None
        weight = a['value'] / total * 100 if a['value'] is not None and total else 0
        dust = a['value'] is None or a['value'] < DUST_KRW  # 소액과 미평가(시세 없음) 모두 숨김 대상
        rows.append(f'''<tr{' class="dust"' if dust else ''}><td><div class="asset"><span class="coin">{esc(a['currency'][:2])}</span><div><strong>{esc(a['currency'])}</strong><small>{esc(a.get('name',a['currency']))}</small></div></div></td>
<td>{a['quantity']:,.8g}<small>잠금 {a['locked']:,.8g}</small></td>
<td>{price(a['avg']) if a['avg'] > 0 else '—'}<small>KRW</small></td>
<td>{price(a['price'])}<small>KRW</small></td>
<td>{money(buy) if buy is not None else '—'}<small>원</small></td>
<td><strong>{money(a['value']) if a['value'] is not None else '미평가'}</strong><small>원</small></td>
<td class="{tone(gain or 0)}">{f'{gain:+,.0f}' if gain is not None else '—'}<small>{f'{rate:+.2f}%' if rate is not None else '계산 제외'}</small></td>
<td><span>{weight:.1f}%</span><div class="bar"><i style="width:{min(100,weight):.2f}%"></i></div></td></tr>''')
    status = '연결 오류 · 마지막 성공 자료 유지: ' + error if error else '계좌 연결 정상'
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta http-equiv="refresh" content="5"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>업비트 실시간 계좌</title><style>
*{{box-sizing:border-box}}body{{font:14px/1.5 "Malgun Gothic",sans-serif;background:#f3f6fb;color:#1c2d47;margin:0;padding:20px}}
.shell{{max-width:1400px;margin:auto}}header{{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:18px}}h1{{font-size:23px;letter-spacing:-1px;margin:0}}.eyebrow{{font-size:11px;color:#62758e;letter-spacing:2px;font-weight:700}}.status{{background:#e6f5ee;color:#16805b;border:1px solid #c6e9d8;border-radius:20px;padding:6px 12px;font-size:12px}}.warn{{color:#ae3d31;background:#fff0eb}}.cards{{display:grid;grid-template-columns:1.25fr 1fr 1fr 1fr;gap:12px}}.card{{background:white;border:1px solid #e2e9f2;border-radius:14px;padding:18px 20px}}.card.primary{{background:#183250;color:white;border-color:#183250}}.label{{font-size:12px;color:#718198;margin-bottom:9px}}.primary .label,.primary small{{color:#b9cce2}}.amount{{font-size:25px;font-weight:700;letter-spacing:-.8px;font-variant-numeric:tabular-nums}}.unit{{font-size:12px;font-weight:400;margin-left:4px}}small{{display:block;font-size:11px;color:#7d8da1;margin-top:4px;font-weight:400}}.gain{{color:#d84b48}}.loss{{color:#3975ce}}.neutral{{color:#52637b}}.panel{{margin-top:18px;background:white;border:1px solid #e2e9f2;border-radius:14px;overflow:hidden}}.panelhead{{padding:17px 20px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #e9eef5}}h2{{font-size:15px;margin:0}}.count{{color:#77889c;font-size:12px}}.scroll{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;white-space:nowrap;font-variant-numeric:tabular-nums}}th{{font-size:11px;font-weight:500;color:#7b8ba0;background:#fafbfd}}td,th{{padding:13px 16px;text-align:right;border-bottom:1px solid #eef2f7}}td:first-child,th:first-child{{text-align:left}}tbody tr:last-child td{{border:0}}tbody tr:hover{{background:#f8faff}}.asset{{display:flex;gap:10px;align-items:center}}.coin{{display:grid;place-items:center;width:34px;height:34px;border-radius:11px;background:#edf2fa;color:#516f97;font-size:11px;font-weight:700}}.asset small{{margin:0}}.bar{{width:70px;height:4px;background:#eaf0f8;border-radius:4px;margin:7px 0 0 auto}}.bar i{{display:block;height:100%;background:#688fca;border-radius:4px}}footer{{font-size:11px;color:#7b8ba0;line-height:1.8;padding:13px 3px}}.stamp{{display:flex;justify-content:space-between;gap:12px;margin-top:12px;color:#7b8ba0;font-size:11px}}@media(max-width:850px){{.cards{{grid-template-columns:1fr 1fr}}body{{padding:12px}}.amount{{font-size:22px}}header{{align-items:flex-start}}h1{{font-size:20px}}.stamp{{display:block}}}}@media(max-width:430px){{.card{{padding:14px}}.amount{{font-size:19px}}}}
html.hd .dust{{display:none}}.dustswitch{{font-size:12px;color:#62758e;margin-left:10px;cursor:pointer;white-space:nowrap}}.dustswitch input{{vertical-align:middle;margin-right:4px}}</style><script>try{{if(localStorage.getItem('hideDust')!=='0')document.documentElement.classList.add('hd');}}catch(e){{document.documentElement.classList.add('hd');}}</script><main class="shell"><header><div><div class="eyebrow">MY PORTFOLIO</div><h1>업비트 실시간 계좌</h1></div><span class="status {'warn' if error else ''}">{esc(status)}</span></header>
<div class="cards"><div class="card primary"><div class="label">총 보유자산 · 평가 가능 기준</div><div class="amount">{total:,.0f}<span class="unit">원</span></div><small>현금 + 보유 코인 평가액</small></div>
<div class="card"><div class="label">보유 코인 평가손익</div><div class="amount {tone(pnl)}">{f'{pnl:+,.0f}' if ret is not None else '—'}<span class="unit">원</span></div><small class="{tone(pnl)}">수익률 {f'{ret:+.2f}%' if ret is not None else '계산 불가'}</small></div>
<div class="card"><div class="label">총 매입금액 · 계산 가능 보유분</div><div class="amount">{cost:,.0f}<span class="unit">원</span></div><small>코인 평가액 {total-cash:,.0f}원</small></div>
<div class="card"><div class="label">보유 원화</div><div class="amount">{cash:,.0f}<span class="unit">원</span></div><small>주문 가능 {available:,.0f}원 · 비중 {(cash/total*100 if total else 0):.1f}%</small></div></div>
<div class="stamp"><span>마지막 조회 {esc(data.get('time','아직 성공하지 않음').replace('T',' '))}</span><span id="fresh">5초 간격 자동 갱신</span></div>
<section class="panel"><div class="panelhead"><h2>보유 코인</h2><span class="count"><span id="cnt">{len(coins)}개 자산</span> · 매입 대비 손익<label class="dustswitch"><input type="checkbox" id="hideDust">소액({DUST_KRW:,}원 미만)·미평가 숨기기</label></span></div><div class="scroll"><table><thead><tr><th>자산</th><th>보유 수량</th><th>매입 평균가</th><th>현재가</th><th>매입금액</th><th>평가금액</th><th>평가손익 / 수익률</th><th>계좌 비중</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="8">계좌 조회를 기다리고 있습니다.</td></tr>'}</tbody></table></div></section>
<footer>수익률은 현재 보유분의 평균 매입가 대비 미실현 손익이며, 입출금을 반영한 계좌 누적 수익률이 아닙니다. 수수료·예상 매도 비용은 차감 전입니다.<br>시세 없는 자산 {unknown}개는 총 평가액에서 제외하고 수량을 보존합니다. 시세는 있으나 평균 매입가가 없는 {unknown_cost}개는 합산 손익·수익률 계산에서 제외합니다.</footer></main>
<script>(function(){{const box=document.getElementById('hideDust'),cnt=document.getElementById('cnt');function apply(){{const on=box.checked;document.documentElement.classList.toggle('hd',on);try{{localStorage.setItem('hideDust',on?'1':'0');}}catch(e){{}}cnt.textContent=on?'{len(coins)-dust_n}개 표시 · 소액 {dust_n}개 숨김':'전체 {len(coins)}개 자산';}}box.checked=document.documentElement.classList.contains('hd');box.addEventListener('change',apply);apply();}})();</script><script>function freshness(){{const age=Math.max(0,Math.floor(Date.now()/1000-{float(data.get('epoch',0))}));const el=document.getElementById('fresh');el.textContent=age>20?'갱신 지연 · '+age+'초 경과':'5초 간격 자동 갱신 · 조회 후 '+age+'초';if(age>20)el.style.color='#c34b43';}}freshness();setInterval(freshness,1000);
try{{const k='account-scroll:'+location.pathname;const saved=JSON.parse(sessionStorage.getItem(k)||'null');if(saved){{window.scrollTo(0,saved.y);document.querySelector('.scroll').scrollLeft=saved.x;}}window.addEventListener('pagehide',()=>sessionStorage.setItem(k,JSON.stringify({{y:window.scrollY,x:document.querySelector('.scroll').scrollLeft}})));}}catch(e){{}}</script></html>'''


def run():
    DATA.mkdir(exist_ok=True)
    with (DATA / 'account_sync.lock').open('a+b') as lock:
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return
        data = {}
        while not (ROOT / 'account_sync_stop.txt').exists():
            error = ''
            try:
                data = snapshot()
                atomic(DATA / 'account_live.json', json.dumps(data, ensure_ascii=False, indent=2))
            except Exception as ex:
                error = f'{type(ex).__name__}: {ex}'
            atomic(DATA / 'account_live.html', render(data, error))
            time.sleep(5 if not error else 15)


if __name__ == '__main__':
    run()
