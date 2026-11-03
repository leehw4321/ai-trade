"""Read-only HTML view of existing pnl/summary.json; no exchange or order APIs."""
import argparse
import datetime as dt
import html
import json
import math
import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent.parent


def money(value):
    return f'{value:+,.0f}원' if type(value) in (int, float) and math.isfinite(value) else '미확정'


def render(data, error=''):
    esc = lambda x: html.escape(str(x))
    notices = []
    if error: notices.append('자료를 읽지 못했습니다: ' + error)
    stamp = data.get('computed_at', '')
    try:
        at = dt.datetime.fromisoformat(stamp)
        age = (dt.datetime.now(dt.timezone.utc) - at).total_seconds()
        if age > 180 or age < -60: notices.append('계산 자료가 3분 이상 지연되었거나 시각이 맞지 않습니다.')
    except (ValueError, TypeError): notices.append('계산 시각을 확인할 수 없습니다.')
    if data.get('mismatch'): notices.append('계좌 수량과 계산 장부가 다른 종목이 있습니다. 해당 손익을 확정값으로 보지 마세요.')
    cards = []
    for name in ('오늘', '이번 주', '이번 달', '누적'):
        p = data.get('periods', {}).get(name, {})
        cards.append(f'<article><label>{esc(name)}</label><b>{money(p.get("realized"))}</b>'
                     f'<small>확인분 {money(p.get("confirmed"))} · 추정분 {money(p.get("estimated"))}</small>'
                     f'<small>매도 {esc(p.get("sell_orders", "—"))}건 · 승 {esc(p.get("wins", "—"))} / 패 {esc(p.get("losses", "—"))}</small>'
                     f'<small>미확정 {esc(p.get("unconfirmed_count", "—"))}건 · {"기간 일부만 집계" if p.get("partial_period") else "집계 시작일 이후 기준"}</small></article>')
    recent = ''.join(f'<tr><td>{esc(r.get("time", ""))}</td><td>{esc(r.get("coin", ""))}</td>'
                     f'<td>{money(r.get("realized"))}</td><td>{esc(r.get("kind", ""))}</td>'
                     f'<td>{esc(r.get("source", ""))}<small>{esc(r.get("why", ""))}</small></td></tr>'
                     for r in data.get('recent', []))
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta http-equiv="refresh" content="20">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>실현손익 관리</title>
<style>body{{margin:0;padding:30px;background:#eef2f7;color:#20334d;font:14px/1.7 "Malgun Gothic",sans-serif}}main{{max-width:1250px;margin:auto}}header{{padding:25px;background:#17324f;color:white;border-radius:16px}}h1{{margin:0;font-size:26px}}header small{{color:#bbd0e6}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:20px 0}}article,section{{background:white;border:1px solid #dce4ed;border-radius:14px;padding:20px}}label,small{{display:block;color:#718199;font-size:12px}}b{{display:block;font-size:27px;margin:8px 0;color:#233b58}}section{{margin:18px 0;overflow:auto}}h2{{font-size:17px;margin:0 0 12px}}table{{width:100%;border-collapse:collapse;white-space:nowrap}}th,td{{text-align:left;padding:11px;border-bottom:1px solid #e8edf4}}th{{font-size:12px;color:#718199}}.warn{{padding:12px;background:#fff0de;color:#99481c;border-radius:10px}}footer{{font-size:12px;color:#657891}}@media(max-width:950px){{.cards{{grid-template-columns:1fr 1fr}}}}@media(max-width:520px){{body{{padding:12px}}.cards{{grid-template-columns:1fr}}}}</style>
<main><header><h1>실현손익 관리</h1><div>매도해 확정된 손익과 아직 보유 중인 평가손익을 나누어 확인합니다.</div><small>읽기 전용 · 주문 기능 없음 · 화면 20초 갱신</small></header>
<p>손익 계산 시각: {esc(stamp or '자료 없음')}<br><small>거래 자료 동기화: {esc(data.get('synced_at', '확인 불가'))}</small></p>
{''.join('<p class="warn">'+esc(x)+'</p>' for x in notices)}<div class="cards">{''.join(cards)}</div>
<section><h2>보유분 평가손익</h2><b>{money(data.get('unrealized_total'))}</b><small>위 실현손익에 더해 확정 수익으로 취급하지 않습니다. 수량 불일치·원가 불명 종목은 합계에서 제외될 수 있습니다.</small></section>
<section><h2>최근 매도 손익</h2><table><thead><tr><th>시각</th><th>종목</th><th>실현손익</th><th>구분</th><th>주문 출처·비고</th></tr></thead><tbody>{recent or '<tr><td colspan="5">표시할 기록이 없습니다.</td></tr>'}</tbody></table></section>
<footer>집계 시작: {esc(data.get('baseline_start', '확인 불가'))}<br>{esc(data.get('baseline_source', ''))}<br>기존 계산 자료를 표시합니다. 추정·미확정 손익과 집계 기간을 확인하세요. 거래 수수료를 표시된 실현손익에서 다시 빼지 마세요. 화면 갱신은 거래 자료 수집을 대신하지 않습니다.</footer></main></html>'''


def dashboard(bot_page):
    src = html.escape(bot_page.resolve().as_uri(), quote=True)
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>자동거래 v5 · 통합 현황</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#eef2f7;color:#20334d;font:14px/1.6 "Malgun Gothic",sans-serif}}header{{padding:12px 22px;background:#17324f;color:white;display:flex;justify-content:space-between}}header small{{color:#bdd0e3}}details{{margin:14px 18px;background:white;border:1px solid #d5e0ed;border-radius:12px;overflow:hidden}}summary{{cursor:pointer;padding:15px 20px;font-size:17px;font-weight:bold;color:#20486b}}summary small{{font-size:12px;color:#6c8199;font-weight:normal;margin-left:14px}}iframe{{display:block;border:0;width:100%}}.pnl{{height:min(78vh,900px);min-height:460px}}.bot{{height:calc(100vh - 135px);min-height:760px}}details[open] summary{{border-bottom:1px solid #e4ebf4}}</style>
<header><strong>자동거래 v5 · 통합 현황</strong><small>계좌·운용 현황 / 실현손익</small></header>
<details id="pnl"><summary>실현손익 관리<small>누르면 펼치기 · 다시 누르면 닫기</small></summary><iframe class="pnl" src="pnl.html" title="실현손익 관리"></iframe></details>
<iframe class="bot" src="{src}" title="기존 봇 계좌·운용 현황"></iframe>
<script>const panel=document.getElementById('pnl');try{{panel.open=sessionStorage.getItem('pnl-open')==='1';panel.addEventListener('toggle',()=>sessionStorage.setItem('pnl-open',panel.open?'1':'0'));}}catch(e){{}}</script></html>'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'pnl_view')
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--bot-page', type=Path, help='Existing bot status HTML; creates an integrated collapsible view')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    import msvcrt
    with (args.output / 'view.lock').open('a+b') as lock:
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        try: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError: return
        if args.bot_page:
            if not args.bot_page.is_file(): raise ValueError('bot page missing')
            (args.output / 'index.html').write_text(dashboard(args.bot_page), encoding='utf-8')
        while not (args.output / 'stop.txt').exists():
            data, error = {}, ''
            try:
                data = json.loads(args.summary.read_text(encoding='utf-8'))
                if not isinstance(data, dict): raise ValueError('summary must be an object')
            except (OSError, ValueError) as ex:
                data, error = {}, type(ex).__name__
            target = args.output / ('pnl.html' if args.bot_page else 'index.html')
            temp = target.with_suffix('.tmp')
            temp.write_text(render(data, error), encoding='utf-8')
            os.replace(temp, target)
            if not args.watch: break
            for _ in range(20):
                if (args.output / 'stop.txt').exists(): break
                time.sleep(1)


if __name__ == '__main__':
    main()
