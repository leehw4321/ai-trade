"""놓친 이유 장부 집계(읽기 전용, 공개 시세만). v5/missed_ledger.py가 남긴 기록에 1·4·24시간 뒤 가격 변화를 붙여 사유별로 집계한다.

'차단 덕분에 피한 손실'과 '차단 때문에 놓친 수익'을 같은 표에서 본다. 가격 변화는 판단 가격(신호 봉 종가) 기준이며 비용은 빼지 않는다
(실제로 샀다면 비용 왕복 0.40~0.85%가 더 든다). 사유가 여러 개인 기록은 각 사유에 모두 들어간다.

  python tools/missed_report.py [--app <실행폴더>] [--since 2026-11-05]
"""
import argparse
import datetime as dt
import json
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT.parent / '코인 트레이딩' / 'v5-app' / 'v5'
HORIZONS = (1, 4, 24)
KST = dt.timezone(dt.timedelta(hours=9))


def candles(market, until):
    """market의 15분봉 {시작 에포크: 종가}, until(에포크)까지 최근 200개."""
    to = dt.datetime.fromtimestamp(until, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    url = f'https://api.upbit.com/v1/candles/minutes/15?market={market}&count=200&to={to}'
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'v5-missed-report'}), timeout=15) as r:
        rows = json.loads(r.read())
    time.sleep(0.12)
    return {int(dt.datetime.fromisoformat(x['candle_date_time_utc']).replace(tzinfo=dt.timezone.utc).timestamp()): x['trade_price'] for x in rows}


def forward(rows, now):
    """각 기록에 h시간 뒤 수익률을 붙인다(그 시각이 아직 안 왔으면 None)."""
    by_market = defaultdict(list)
    for r in rows:
        by_market[r['market']].append(r)
    for m, rs in by_market.items():
        need = max(r['slot'] for r in rs) + max(HORIZONS) * 3600
        try:
            px = candles(m, min(need + 900, now))
        except Exception:
            px = {}
        for r in rs:
            for h in HORIZONS:
                t = r['slot'] + h * 3600 - 900          # h시간 뒤에 마감된 봉의 시작
                c = px.get(t)
                r[f'r{h}'] = (c / r['close'] - 1) if c and r.get('close') else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--app', type=Path, default=APP)
    ap.add_argument('--since', default=None)
    a = ap.parse_args(argv)
    f = a.app / 'data' / 'missed_ledger.jsonl'
    if not f.exists():
        print('기록 없음:', f)
        return 1
    rows = [json.loads(x) for x in f.read_text(encoding='utf-8').splitlines() if x.strip()]
    if a.since:
        t0 = dt.datetime.fromisoformat(a.since).replace(tzinfo=KST).timestamp()
        rows = [r for r in rows if r['slot'] >= t0]
    rows = [r for r in rows if r.get('signal')]       # 매수 신호(7위 안·상승률 양수)가 있었던 것만
    forward(rows, time.time())
    groups = defaultdict(list)
    for r in rows:
        groups['결과: ' + r['outcome']].append(r)
        for b in r['blocks']:
            groups['사유: ' + b].append(r)
    print(f"신호 {len(rows)}건 · 판단 {len({r['slot'] for r in rows})}회 · 비용 미차감(실매수였다면 왕복 0.40~0.85% 추가)")
    print(f"{'구분':34} {'건수':>5} " + ' '.join(f'{h}시간 평균(건수)'.rjust(16) for h in HORIZONS))
    for k in sorted(groups):
        g = groups[k]
        cells = []
        for h in HORIZONS:
            v = [r[f'r{h}'] for r in g if r.get(f'r{h}') is not None]
            cells.append((f'{sum(v) / len(v) * 100:+.2f}% ({len(v)})' if v else '-').rjust(16))
        print(f'{k:34} {len(g):5} ' + ' '.join(cells))
    print('양수 = 그 사유로 안 산 종목이 이후 올랐다(놓친 수익) · 음수 = 이후 내렸다(피한 손실)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
