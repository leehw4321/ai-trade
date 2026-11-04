"""Read-only warning/rank explainer. Public API plus local cached ranking; never submits orders."""
import argparse
import datetime as dt
import html
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'v5'))
import upbit
from strategy import Params


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def snapshot(app, now=None):
    now = time.time() if now is None else now
    cfg = read_json(Path(app) / 'config.json')
    p = Params.from_dict(cfg.get('strategy', {}))
    if type(p.tf) is not int or p.tf <= 0 or type(p.L) is not int or p.L <= 0:
        raise ValueError('invalid timeframe')
    raw = upbit._get('/v1/market/all', {'is_details': 'true'})
    if not isinstance(raw, list) or not raw:
        raise ValueError('market metadata unavailable')
    markets = {r['market']: dict(name=r.get('korean_name', r['market']), **upbit.market_warning_details(r))
               for r in raw if r['market'].startswith('KRW-')}
    ticks = upbit.tickers(markets)
    eligible = [m for m in markets if m in ticks and m not in p.exclude]
    universe = set(sorted(eligible, key=lambda m: -ticks[m].get('acc_trade_price_24h', 0))[:p.universe])
    cache, cache_error, held, holdings_error = {}, False, set(), False
    try:
        cache = read_json(Path(app) / 'data' / 'P10' / 'ledger.json')
    except (OSError, ValueError):
        cache_error = True
    try:
        held = set(read_json(Path(app) / 'data' / 'live' / 'live.json')['positions'])
    except (OSError, ValueError, KeyError, TypeError):
        holdings_error = True
    ranks = {r['market']: r for r in cache.get('ranking', [])}
    asof = cache.get('ranking_time', '')
    try:
        stamp = dt.datetime.fromisoformat(asof)
        stale = (stamp.tzinfo is None or cache.get('timeframe_minutes') != p.tf
                 or not 0 <= now - stamp.timestamp() <= p.tf * 60 + 20)
    except (ValueError, TypeError):
        stale = True
    rows = []
    for m, detail in markets.items():
        if m not in universe and m not in held and not detail['warning']:
            continue
        row = dict(market=m, held=m in held, in_universe=m in universe, **detail)
        row.update(rank=ranks.get(m, {}).get('rank'), rs=ranks.get(m, {}).get('rs'))
        rows.append(row)
    rows.sort(key=lambda r: (r['rank'] if type(r['rank']) is int else 9999, r['market']))
    return {'time': dt.datetime.fromtimestamp(now, dt.timezone(dt.timedelta(hours=9))).isoformat(timespec='seconds'),
            'epoch': now, 'tf': p.tf, 'L': p.L, 'M': p.M, 'scan': p.entry_scan or p.entry_top,
            'want': p.entry_want or p.entry_top, 'universe': p.universe,
            'ranking_time': asof, 'ranking_stale': stale, 'ranking_unavailable': cache_error,
            'holdings_unavailable': holdings_error, 'rows': rows,
            'flagged_count': sum(d['warning'] for d in markets.values()),
            'investment_count': sum(d['investment_warning'] for d in markets.values())}


def render(data, error=''):
    esc = html.escape
    rows = []
    for r in data.get('rows', []):
        rs = r.get('rs')
        pct = f'{rs:+.2%}' if type(rs) in (int, float) and math.isfinite(rs) else '—'
        reasons = ' · '.join(r['warning_reasons']) or '경보 없음'
        stage = '단계 API 미제공' if r['caution_severity'] == 'unknown' else '—'
        rows.append(f"<tr><td>{r['rank'] if type(r['rank']) is int else '—'}</td>"
                    f"<td><strong>{esc(r['market'][4:])}</strong><small>{esc(r['name'])}</small></td>"
                    f"<td>{pct}</td><td>{'보유' if r['held'] else '—'}</td>"
                    f"<td>{esc(reasons)}<small>{stage}</small></td>"
                    f"<td>{'신규·추가 매수 보류' if r['warning'] else '일반 매수 조건 별도 검토'}</td>"
                    '<td>경보 사유 매도 차단 없음</td></tr>')
    warn = []
    if error: warn.append('갱신 실패 · 마지막 성공 자료 유지: ' + error)
    if data.get('ranking_stale', True): warn.append('순위 자료가 오래됐거나 없습니다. 현재 매수 신호로 사용하지 마세요.')
    if data.get('holdings_unavailable'): warn.append('보유 장부 조회 불가 · 보유 표시는 확인되지 않았습니다.')
    alerts = ''.join(f'<p class="alert">{esc(x)}</p>' for x in warn)
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta http-equiv="refresh" content="30">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>상대강도와 경보 정책</title>
<style>body{{font:14px/1.6 "Malgun Gothic",sans-serif;background:#f3f6fb;color:#20344e;margin:24px}}main{{max-width:1300px;margin:auto}}h1{{font-size:25px;margin:0}}h2{{font-size:17px}}.sub,small{{color:#71839a;font-size:12px}}small{{display:block}}.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}}.card,section{{background:white;border:1px solid #dfe6f0;border-radius:12px;padding:18px}}.card{{flex:1;min-width:180px}}.card b{{font-size:20px;display:block}}section{{overflow:auto;margin-top:16px}}table{{border-collapse:collapse;width:100%;white-space:nowrap}}th,td{{padding:10px 12px;border-bottom:1px solid #edf1f6;text-align:left}}th{{font-size:12px;color:#75859a}}.alert{{background:#fff1e9;color:#9f4a22;padding:10px;border-radius:8px}}footer{{font-size:12px;color:#73839a;margin-top:16px}}</style>
<main><h1>상대강도 순위 · 경보 판단</h1><div class="sub">조회 전용 · 경보 갱신 {esc(data.get('time','아직 없음'))}<br>순위는 봇의 저장 자료: {esc(data.get('ranking_time','') or '없음')} · 실주문 기능 없음</div>{alerts}
<div class="cards"><div class="card">순위 기준<b>{data.get('tf','—')}분봉 × {data.get('L','—')}개</b>종가 대비 단순 상승률</div>
<div class="card">전체 원화 경보 종목<b>{data.get('flagged_count','—')}개</b>투자유의 포함 · 중복 합산 안 함</div>
<div class="card">신규·추가 매수<b>경보면 보류</b>주관적인 수익 확신으로 예외 진입 안 함</div>
<div class="card">보유 후 경보 발생<b>청산 경로 유지</b>경보만으로 손절·익절·순환 매도를 막지 않음</div></div>
<section><h2>현재 순위 규칙</h2><p>최근 확정 종가 ÷ {data.get('L','—')}개 봉 전 종가 − 1. 거래대금 상위 {data.get('universe','—')}개에서 계산합니다. 경보 종목도 순위에는 포함되며 매수 적격 검사에서 제외합니다. 신규 후보는 상위 {data.get('scan','—')}위까지 찾아 최대 {data.get('want','—')}개, 유지 기준은 {data.get('M','—')}위입니다. 과열·재진입·호가·현금·위험 한도 검사는 추가로 적용됩니다.</p><p>BTC 대비 성과나 변동성 조정 점수가 아닙니다. 거래 없는 구간의 봉은 없으므로 관측 시간은 봉 누락 여부에 따라 달라집니다.</p></section>
<section><h2>경보 원인과 보유분 처리</h2><table><thead><tr><th>저장 순위</th><th>종목</th><th>저장 상승률</th><th>보유</th><th>경보 원인</th><th>매수 정책</th><th>매도 정책</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>
<footer>API의 투자유의와 시장경보 원인을 구분합니다. 시장경보의 주의·경고·위험 단계는 이 API에서 제공되지 않아 추정하지 않습니다. 순위 표에는 저장된 상위 일부만 있으므로 — 표시는 최하위라는 뜻이 아닙니다.<br>매도 실행은 거래 지원, 잔고·최소주문, 실행권·미확정 주문 조건을 충족해야 합니다. 빠른 매도나 수익 실현을 보장하지 않습니다.<br><a href="https://docs.upbit.com/kr/changelog/market_event">업비트 경보 API 설명</a> · <a href="https://www.upbit.com/service_center/market_warning_history">공식 시장경보 현황</a></footer></main></html>'''


def atomic(path, content):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(content, encoding='utf-8')
    os.replace(temp, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'warning_policy')
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    import msvcrt
    with (args.output / 'report.lock').open('a+b') as lock:
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        try: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError: return
        data = {}
        while not (args.output / 'stop.txt').exists():
            error = ''
            try:
                data = snapshot(args.app)
                atomic(args.output / 'snapshot.json', json.dumps(data, ensure_ascii=False, indent=2))
            except Exception as ex:
                error = type(ex).__name__
            atomic(args.output / 'index.html', render(data, error))
            if not args.watch: break
            for _ in range(60):
                if (args.output / 'stop.txt').exists(): break
                time.sleep(1)


if __name__ == '__main__':
    main()
