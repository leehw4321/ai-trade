"""보유 N시간 시점에 수익률이 X% 미만이면 시장가 청산했을 때(가정) 손익. 왕복 비용은 실제 체결에 이미 들어 있고, 가정 청산은 매도수수료 0.05%+슬리피지 0.1% 차감."""
import json, sys, time, datetime as dt, urllib.request
from pathlib import Path
S = Path(sys.argv[1])
t = S.read_text(encoding='utf-8-sig'); rows = json.loads(t[:t.rfind('OPEN')])
KST = dt.timezone(dt.timedelta(hours=9))

def c1(market, at):
    url = f'https://api.upbit.com/v1/candles/minutes/1?market={market}&count=1&to={(at + dt.timedelta(minutes=1)).astimezone(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}'
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'analysis'}), timeout=10) as r:
        time.sleep(0.15); return json.loads(r.read())[0]['trade_price']

for H, X in [(2, 0.0), (2, 1.0), (3, 0.0), (3, 1.0), (4, 0.0)]:
    tot = 0; changed = []
    for r in rows:
        if r['hold_min'] <= H * 60 or r['mfe'] is None:
            tot += r['pnl']; continue
        op = dt.datetime.strptime('2026-' + r['open'], '%Y-%m-%d %H:%M').replace(tzinfo=KST)
        entry = r['invest'] / (1 + 0.0005)  # 대략 진입가 기준(수수료 포함 원가)
        px = c1('KRW-' + r['coin'], op + dt.timedelta(hours=H))
        # 진입가 추정: 실제 수익률과 청산 시점 가격을 쓰지 않고 1분봉 진입 시점 가격으로
        e0 = c1('KRW-' + r['coin'], op)
        ret = px / e0 - 1
        if ret * 100 < X:
            alt = r['invest'] * (ret - 0.0005 - 0.001 - 0.0005)
            changed.append((r['coin'], r['open'], r['pnl'], round(alt)))
            tot += alt
        else:
            tot += r['pnl']
    print(f'{H}h <{X}%: 합계 {round(tot):,} (실제 {sum(r["pnl"] for r in rows):,})', changed)
