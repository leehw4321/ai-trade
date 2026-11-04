import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
for line in Path(r'C:\Users\User\Desktop\코인 트레이딩\.env').read_text(encoding='utf-8-sig').splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        k, v = line.split('=', 1)
        os.environ[k.strip()] = v.strip().strip('"').strip("'")
sys.path.insert(0, r'C:\Users\User\Desktop\ai-trade\v5')
import manual_protection as mp

M = 'KRW-AXIS'
rows = mp.HistoryClient().orders(M)
fills = []
for o in rows:
    vol = float(o.get('executed_volume') or 0)
    if vol <= 0:
        continue
    funds = float(o.get('executed_funds') or 0)
    t = datetime.fromisoformat(o['created_at']).astimezone(KST)
    fills.append((t, o['side'], vol, funds / vol if funds else None, str(o.get('identifier', '')).startswith('v5-')))
fills.sort()
print('AXIS 체결(거래소, 최근 7일):')
for t, s, v, px, bot in fills:
    print(f"  {t:%m-%d %H:%M:%S} {'매수' if s=='bid' else '매도'} {v:,.2f}개 평균 {px:.1f} {'봇' if bot else '사용자/기타'}")


def get(url):
    for i in range(6):
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                time.sleep(0.2)
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(1.5 * (i + 1))


bars = {}
to = datetime.now(timezone.utc)
for _ in range(4):
    cs = get(f"https://api.upbit.com/v1/candles/minutes/15?market={M}&count=200&to={to.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    if not cs:
        break
    for c in cs:
        bars[datetime.fromisoformat(c['candle_date_time_kst']).replace(tzinfo=KST)] = c
    to = datetime.fromisoformat(cs[-1]['candle_date_time_utc']).replace(tzinfo=timezone.utc)
keys = sorted(bars)
now_px = bars[keys[-1]]['trade_price']
print('\n현재가', now_px, '(마지막 봉', keys[-1].strftime('%m-%d %H:%M'), ')')


def path(t0, t1):
    seg = [bars[k] for k in keys if t0 <= k <= t1]
    return (max(c['high_price'] for c in seg), min(c['low_price'] for c in seg)) if seg else (None, None)


# 매수→매도 쌍 단순 매칭
pos = None
trips = []
for t, s, v, px, bot in fills:
    if s == 'bid':
        pos = pos or {'t0': t, 'qty': 0.0, 'cost': 0.0}
        pos['qty'] += v
        pos['cost'] += v * px
    elif pos:
        trips.append((pos['t0'], pos['cost'] / pos['qty'], t, px))
        pos = None
print('\n왕복별(진입→청산) 결과와 청산 뒤 가격 경로:')
for t0, e, t1, x in trips:
    hi_in, lo_in = path(t0, t1)
    hi_after, lo_after = path(t1, t1 + timedelta(hours=24))
    hi_all, _ = path(t1, keys[-1])
    print(f"  {t0:%m-%d %H:%M}→{t1:%m-%d %H:%M} 진입 {e:.1f} 청산 {x:.1f} ({(x/e-1)*100:+.1f}%) | 보유 중 최고 {(hi_in/e-1)*100:+.1f}% 최저 {(lo_in/e-1)*100:+.1f}% | 청산 후 24h 최고 {(hi_after/x-1)*100:+.1f}% 최저 {(lo_after/x-1)*100:+.1f}% | 청산 후 현재까지 최고 {(hi_all/x-1)*100:+.1f}%")
if pos:
    print(f"  현재 보유 중: 진입 {pos['t0']:%m-%d %H:%M} 평단 {pos['cost']/pos['qty']:.1f} → 현재가 {now_px} ({(now_px/(pos['cost']/pos['qty'])-1)*100:+.1f}%)")
print('\nAXIS 15분 종가 경로(최근 72h, 6시간 간격):')
print(' '.join(f"{k:%d일%H시}:{bars[k]['trade_price']}" for k in keys[-288::24]))
