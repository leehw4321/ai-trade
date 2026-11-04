import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
for line in Path(r'C:\Users\User\Desktop\코인 트레이딩\.env').read_text(encoding='utf-8-sig').splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        k, v = line.split('=', 1)
        os.environ[k.strip()] = v.strip().strip('"').strip("'")
sys.path.insert(0, r'C:\Users\User\Desktop\ai-trade\v5')
import upbit
import manual_protection as mp

# 1) BMT 실제 체결(식별자 v5-, 시각·가격·수량)
rows = mp.HistoryClient().orders('KRW-BMT')
print('BMT orders (executed only):')
for o in sorted(rows, key=lambda x: x.get('created_at', '')):
    vol = float(o.get('executed_volume') or 0)
    if vol <= 0:
        continue
    funds = float(o.get('executed_funds') or 0) or None
    t = datetime.fromisoformat(o['created_at']).astimezone(KST).strftime('%m-%d %H:%M:%S')
    px = (funds / vol) if funds else None
    print(' ', t, o.get('side'), 'bot' if str(o.get('identifier', '')).startswith('v5-') else 'USER/other', f'vol={vol:.4f}', f'avg={px:.3f}' if px else '', o.get('state'))

# 2) 공개 15분봉으로 16:00 시점 신호 재구성(현재 거래대금 상위 30 근사)
import time
def get(url):
    for i in range(8):
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                time.sleep(0.2)
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code != 429: raise
            time.sleep(1.5 * (i + 1))
    raise RuntimeError('429')

tick = get('https://api.upbit.com/v1/ticker/all?quote_currencies=KRW')
ex = {'KRW-USDT', 'KRW-USDC', 'KRW-USDE', 'KRW-USD1', 'KRW-DAI'}
top = [t['market'] for t in sorted((t for t in tick if t['market'] not in ex), key=lambda t: -t['acc_trade_price_24h'])[:30]]
if 'KRW-BMT' not in top:
    top.append('KRW-BMT')
bars = {}
to = datetime.now(timezone.utc)
for m in top:
    cs = get(f"https://api.upbit.com/v1/candles/minutes/15?market={m}&count=200&to={to.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    bars[m] = {c['candle_date_time_kst']: c for c in cs}
cut = '2026-11-04T15:45:00'  # 16:00에 마감된 봉(시작 15:45)
rs = {}
for m, b in bars.items():
    ks = sorted(b)
    if cut not in b:
        continue
    i = ks.index(cut)
    if i < 72:
        continue
    rs[m] = b[cut]['trade_price'] / b[ks[i - 72]]['trade_price'] - 1
rank = sorted(rs, key=lambda m: -rs[m])
print('\n16:00 마감 봉 기준 18h 상승률 순위 상위 8(현재 상위 40 근사):')
for k, m in enumerate(rank[:8], 1):
    print(f'  {k}. {m[4:]:8s} {rs[m]*100:+.1f}%')
if 'KRW-BMT' in rs:
    print('BMT rank', rank.index('KRW-BMT') + 1, f"rs {rs['KRW-BMT']*100:+.1f}%  (과열 상한 15%)")
b = bars['KRW-BMT']
ks = sorted(k for k in b if '2026-11-04T14:30:00' <= k <= '2026-11-04T16:45:00')
print('\nBMT 15분봉 종가(14:30~):')
print(' '.join(f"{k[11:16]}:{b[k]['trade_price']}" for k in ks))
hi = max(b[k]['high_price'] for k in ks if k <= '2026-11-04T16:00:00')
print('16:00까지 고점', hi, '| 16:00 종가', b['2026-11-04T15:45:00']['trade_price'], '| 최근 종가', b[ks[-1]]['trade_price'])

