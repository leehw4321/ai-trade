"""실거래 체결의 실제 비용 점검(읽기 전용): 봇 시장가 주문의 체결가가 판단 시점 가격(직전 마감 15분봉 종가)과 얼마나 달랐나."""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median

KST = timezone(timedelta(hours=9))
for line in Path(r'C:\Users\User\Desktop\코인 트레이딩\.env').read_text(encoding='utf-8-sig').splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        k, v = line.split('=', 1)
        os.environ[k.strip()] = v.strip().strip('"').strip("'")
sys.path.insert(0, r'C:\Users\User\Desktop\ai-trade\v5')
import manual_protection as mp


def get(url):
    for i in range(8):
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                time.sleep(0.2)
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(1.5 * (i + 1))


rows = mp.HistoryClient().orders(None)
bot = [o for o in rows if str(o.get('identifier', '')).startswith('v5-') and float(o.get('executed_volume') or 0) > 0 and o.get('ord_type') in ('price', 'market')]
print('봇 시장가 체결 주문', len(bot), '(최근 7일, 조회 한도 내)')
cache = {}
res = []
for o in sorted(bot, key=lambda x: x['created_at']):
    m = o['market']
    t = datetime.fromisoformat(o['created_at']).astimezone(KST)
    fill = float(o['executed_funds']) / float(o['executed_volume']) if o.get('executed_funds') else None
    if not fill:
        continue
    last_bar_start = t.replace(second=0, microsecond=0)
    last_bar_start -= timedelta(minutes=last_bar_start.minute % 15 + 15)  # 직전에 마감된 15분봉의 시작
    key = (m, last_bar_start.strftime('%Y-%m-%dT%H:%M:%S'))
    if key not in cache:
        end = (last_bar_start + timedelta(minutes=15)).astimezone(timezone.utc)
        cs = get(f"https://api.upbit.com/v1/candles/minutes/15?market={m}&count=1&to={end.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        cache[key] = cs[0]['trade_price'] if cs and cs[0]['candle_date_time_kst'] == key[1] else None
    ref = cache[key]
    if not ref:
        continue
    side = o['side']
    slip = (fill / ref - 1) if side == 'bid' else (ref / fill - 1)  # 양수 = 불리
    delay = (t - (last_bar_start + timedelta(minutes=15))).total_seconds()
    res.append((t, m, side, slip, delay))
print('비교 가능', len(res))
for side, name in (('bid', '매수'), ('ask', '매도')):
    v = [r[3] for r in res if r[2] == side]
    if v:
        print(f'  {name}: n={len(v)} 평균 불리 {mean(v)*100:+.3f}% 중앙 {median(v)*100:+.3f}% 최악 {max(v)*100:+.2f}% 최선 {min(v)*100:+.2f}%')
allv = [r[3] for r in res]
print(f'  전체 평균 편도 슬리피지 {mean(allv)*100:+.3f}% → 왕복 약 {2*mean(allv)*100:+.3f}% + 수수료 0.10% = 왕복 총비용 약 {(2*mean(allv)+0.001)*100:.3f}% (가정 0.30%)')
d = [r[4] for r in res]
print(f'  판단 시점(봉 마감) 대비 주문 지연 평균 {mean(d):.0f}초 중앙 {median(d):.0f}초')
print('  큰 불리 체결 상위 5:', [(r[0].strftime('%m-%d %H:%M'), r[1][4:], '매수' if r[2] == 'bid' else '매도', f'{r[3]*100:+.2f}%') for r in sorted(res, key=lambda r: -r[3])[:5]])
