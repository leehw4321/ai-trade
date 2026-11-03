"""지정 시각 이후의 실거래 결과 요약 (읽기 전용: 주문 기록 파일과 공개 시세만 사용).

  python tools/results_since.py --since 2026-11-04T08:33 [--app <실행 폴더>]

평단법으로 청산 거래의 수수료 포함 순손익을 계산한다. 이후에 산 물량의 원가는 주문 기록 전체를 재생해 구한다.
결과는 짧은 기간의 우연이 크므로 '개선 입증'으로 읽지 않는다. 판단은 사전에 정한 최소 표본(완결 거래 30건 또는 3일)과 비용 후 합계로 한다.
"""
import argparse
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
DEFAULT_APP = Path(r'C:\Users\User\Desktop\코인 트레이딩\v5-app\v5')


def load(journal):
    rows = []
    for line in Path(journal).read_text(encoding='utf-8').splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def replay(rows, since):
    """(청산 거래 목록, 열린 포지션 {종목: (수량, 원가)}). 청산 거래는 매도 시각이 since 이후인 것만."""
    book, closed = {}, []
    for r in rows:
        side = r.get('side')
        if side not in ('매수', '매도') or r.get('state') not in ('done', 'cancel') or not r.get('volume'):
            continue
        m, t = r['market'], datetime.fromisoformat(r['time'])
        if side == '매수':
            b = book.setdefault(m, {'q': 0.0, 'c': 0.0, 't': t})
            if b['q'] <= 1e-9:
                b.update(q=0.0, c=0.0, t=t)
            b['q'] += r['volume']
            b['c'] += r['funds'] + r['fee']
        else:
            b = book.get(m)
            if not b or b['q'] <= 1e-9:
                continue  # 원가를 알 수 없는 매도(인수 보유분 등)는 제외
            frac = min(1.0, r['volume'] / b['q'])
            basis = b['c'] * frac
            pnl = r['funds'] - r['fee'] - basis
            b['q'] -= r['volume']
            b['c'] -= basis
            if t >= since:
                closed.append({'time': t, 'market': m, 'hold_h': (t - b['t']).total_seconds() / 3600, 'pnl': pnl,
                               'pct': pnl / basis if basis else 0.0, 'basis': basis, 'fee': r['fee'], 'reason': r.get('reason', '')})
    open_pos = {m: (b['q'], b['c']) for m, b in book.items() if b['q'] > 1e-9}
    return closed, open_pos


def prices(markets):
    if not markets:
        return {}
    url = 'https://api.upbit.com/v1/ticker?markets=' + ','.join(markets)
    with urllib.request.urlopen(url, timeout=10) as r:
        return {x['market']: x['trade_price'] for x in json.loads(r.read())}


def summarize(closed, open_pos, px):
    lines = []
    for c in sorted(closed, key=lambda x: x['time']):
        lines.append(f"{c['time']:%m-%d %H:%M} {c['market'][4:]:8s} 보유 {c['hold_h']:4.1f}h {c['pnl']:>8,.0f}원 ({c['pct']:+.2%}) {c['reason'][:14]}")
    n = len(closed)
    wins = [c for c in closed if c['pnl'] > 0]
    total = sum(c['pnl'] for c in closed)
    fees = sum(c['fee'] for c in closed)
    head = f'청산 {n}건 · 이익 {len(wins)} / 손실 {n - len(wins)} · 순손익 {total:+,.0f}원 · 매도 수수료 {fees:,.0f}원'
    if n:
        head += f" · 평균 {sum(c['pct'] for c in closed) / n:+.2%}/건 · 평균 이익 {(sum(c['pct'] for c in wins) / len(wins) if wins else 0):+.2%} / 평균 손실 {(sum(c['pct'] for c in closed if c['pnl'] <= 0) / max(1, n - len(wins))):+.2%}"
    lines.append(head)
    unreal = 0.0
    for m, (q, cost) in sorted(open_pos.items()):
        p = px.get(m)
        if p is None or cost < 5000:
            continue
        v = q * p * 0.9995
        unreal += v - cost
        lines.append(f'보유 {m[4:]:8s} 평가 {v:>9,.0f}원 원가 {cost:>9,.0f}원 ({v / cost - 1:+.2%})')
    lines.append(f'미실현 합계(원가 5천 원 이상 보유) {unreal:+,.0f}원')
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', required=True, help='예: 2026-11-04T08:33 (KST)')
    ap.add_argument('--app', default=str(DEFAULT_APP))
    a = ap.parse_args(argv)
    since = datetime.fromisoformat(a.since)
    since = since if since.tzinfo else since.replace(tzinfo=KST)
    rows = load(Path(a.app) / 'data' / 'live' / 'orders.jsonl')
    closed, open_pos = replay(rows, since)
    try:
        px = prices([m for m in open_pos])
    except Exception:
        px = {}
    print(f'== {since:%Y-%m-%d %H:%M} KST 이후 실거래 결과 (읽기 전용) ==')
    print(summarize(closed, open_pos, px))
    print('\n주의: 짧은 기간의 결과는 우연이 크다. 완결 거래 30건 또는 3일 전에는 개선·악화를 단정하지 않는다.')
    return 0


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
