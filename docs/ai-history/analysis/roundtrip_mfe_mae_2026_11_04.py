import json
import statistics as S
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
W = Path(r'C:/Users/User/Desktop/코인 트레이딩')
rows = [json.loads(l) for l in (W / 'v5-app/v5/data/live/orders.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
book = {}
rts = []
for r in rows:
    s = r.get('side')
    if s not in ('매수', '매도') or r.get('state') not in ('done', 'cancel') or not r.get('volume'):
        continue
    m = r['market']; t = datetime.fromisoformat(r['time'])
    if s == '매수':
        b = book.setdefault(m, dict(q=0, c=0, t=None, n=0))
        if b['q'] < 1e-9:
            b['t'] = t; b['c'] = 0; b['q'] = 0; b['n'] = 0
        b['q'] += r['volume']; b['c'] += r['funds'] + r['fee']; b['n'] += 1
    else:
        b = book.get(m)
        if not b or b['q'] <= 1e-9 or not b['t']:
            continue
        frac = min(1, r['volume'] / b['q']); basis = b['c'] * frac
        rts.append(dict(m=m, t0=b['t'], t1=t, entry=b['c'] / b['q'], exitpx=(r['funds'] - r['fee']) / r['volume'],
                        net=(r['funds'] - r['fee'] - basis) / basis, why=r.get('reason', ''), size=basis))
        b['q'] -= r['volume']; b['c'] -= basis
rts = [x for x in rts if x['t0'] >= datetime(2026, 11, 3, 0, 0, tzinfo=KST)]


def candles(m, to):
    url = f"https://api.upbit.com/v1/candles/minutes/15?market={m}&count=200&to={to.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    for _ in range(3):
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(1)
    return []


cache = {}
end = datetime.now(timezone.utc)
for m in {x['m'] for x in rts}:
    bars = {}
    to = end
    for _ in range(3):
        cs = candles(m, to)
        if not cs: break
        for c in cs:
            bars[datetime.fromisoformat(c['candle_date_time_kst']).replace(tzinfo=KST)] = c
        to = datetime.fromisoformat(cs[-1]['candle_date_time_utc']).replace(tzinfo=timezone.utc)
        time.sleep(0.12)
    cache[m] = bars


def fl(t):
    return t.replace(minute=t.minute // 15 * 15, second=0, microsecond=0)


out = []
for x in rts:
    bars = cache[x['m']]
    k0, k1 = fl(x['t0']), fl(x['t1'])
    path = [c for k, c in bars.items() if k0 <= k <= k1]
    prev = [c for k, c in bars.items() if k0 - timedelta(hours=4) <= k < k0]
    post = [c for k, c in bars.items() if k1 < k <= k1 + timedelta(hours=3)]
    if not path:
        continue
    e = x['entry']
    x['mfe'] = max(c['high_price'] for c in path) / e - 1
    x['mae'] = min(c['low_price'] for c in path) / e - 1
    x['hold'] = (x['t1'] - x['t0']).total_seconds() / 3600
    x['near_high'] = (e / max(c['high_price'] for c in prev) - 1) if prev else None
    x['post3'] = (post[-1]['trade_price'] / x['exitpx'] - 1) if post else None
    out.append(x)

print(f'{"종목":8s} {"진입":11s} {"보유h":>5s} {"순손익":>7s} {"최대유리":>7s} {"최대불리":>7s} {"진입/4h고점":>9s} {"청산후3h":>7s} 사유')
for x in sorted(out, key=lambda z: z['t0']):
    print(f"{x['m'][4:]:8s} {x['t0'].strftime('%m-%d %H:%M'):11s} {x['hold']:5.1f} {x['net']*100:+6.2f}% {x['mfe']*100:+6.2f}% {x['mae']*100:+6.2f}% "
          f"{(x['near_high'] or 0)*100:+8.2f}% {(x['post3'] if x['post3'] is not None else float('nan'))*100:+6.2f}% {x['why'][:10]}")


def st(label, xs):
    if not xs:
        return
    n = [x['net'] * 100 for x in xs]
    print(f"{label:14s} n={len(xs):2d} 합={sum(n):+6.2f}%p 평균={S.mean(n):+.2f}% 승률={sum(v>0 for v in n)/len(n)*100:3.0f}% 평균최대유리={S.mean(x['mfe'] for x in xs)*100:+.2f}% 평균최대불리={S.mean(x['mae'] for x in xs)*100:+.2f}%")


print()
st('전체', out)
st('보유<=1.2h', [x for x in out if x['hold'] <= 1.2])
st('보유 1.2~5h', [x for x in out if 1.2 < x['hold'] <= 5])
st('보유>5h', [x for x in out if x['hold'] > 5])
st('순위이탈 청산', [x for x in out if '주문안' in x['why']])
st('이익보호 청산', [x for x in out if '이익보호' in x['why']])
st('손절', [x for x in out if '손절' in x['why']])
lose = [x for x in out if x['net'] <= 0]
print('손실거래 중 한때 +1% 이상 올랐다가 손실로 끝남:', sum(x['mfe'] >= .01 for x in lose), '/', len(lose))
print('승리거래 평균 순수익률', f"{S.mean(x['net'] for x in out if x['net']>0)*100:+.2f}%", ' 평균 최대유리', f"{S.mean(x['mfe'] for x in out if x['net']>0)*100:+.2f}%")
nh = [x['near_high'] for x in out if x['near_high'] is not None]
print('진입가가 직전 4시간 고점 대비 평균', f"{S.mean(nh)*100:+.2f}%", ' (0에 가까울수록 고점 근처 진입)')
cost = sum(x['size'] for x in out) * 0.001
print(f'수수료만으로 {len(out)}건 왕복 약 {cost:,.0f}원 (거래금액 합 {sum(x["size"] for x in out):,.0f}원 x 0.1%)')
