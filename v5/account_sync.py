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
                           price=price, value=None if price is None else (available + locked) * price))
    return {'time': dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec='seconds'),
            'epoch': time.time(), 'assets': assets,
            'priced_total': sum(a['value'] for a in assets if a['value'] is not None)}


def atomic(path, content):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(content, encoding='utf-8')
    os.replace(temp, path)


def render(data, error=''):
    esc = html.escape
    rows = ''
    for a in sorted(data.get('assets', []), key=lambda a: -(a['value'] or 0)):
        value = '미평가' if a['value'] is None else f"{a['value']:,.0f}원"
        rows += (f"<tr><td>{esc(a['currency'])}</td><td>{a['available']:,.8g}</td>"
                 f"<td>{a['locked']:,.8g}</td><td>{value}</td></tr>")
    status = '연결 오류 · 마지막 성공 자료 유지: ' + error if error else '연결 정상 · 5초 간격 조회'
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta http-equiv="refresh" content="5"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>업비트 실시간 계좌</title><style>body{{font:15px/1.5 'Malgun Gothic',sans-serif;background:#f4f6f7;color:#17212b;margin:16px}}
table{{width:100%;border-collapse:collapse}}td,th{{padding:7px;text-align:right;border-bottom:1px solid #ddd}}td:first-child,th:first-child{{text-align:left}}.warn{{color:#b33}}</style>
<h2>실제 업비트 계좌 · 평가 가능 자산 {data.get('priced_total',0):,.0f}원</h2>
<p class="{'warn' if error else ''}">{esc(status)}<br>마지막 계좌·시세 조회: {esc(data.get('time','아직 성공하지 않음'))}
<br><span id="fresh"></span> · 미평가 자산은 총액에서 제외하며 수량은 보존합니다.</p>
<table><tr><th>자산</th><th>주문 가능 수량/원화</th><th>잠금 수량/원화</th><th>평가액</th></tr>{rows}</table>
<script>const age=Date.now()/1000-{float(data.get('epoch',0))};document.getElementById('fresh').textContent=age>20?'갱신 지연: '+Math.floor(age)+'초 경과':'조회 후 '+Math.max(0,Math.floor(age))+'초';</script></html>'''


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
