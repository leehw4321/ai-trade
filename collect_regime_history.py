"""Public GET-only dataset collection. No account or order endpoints."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

OUT = Path(__file__).resolve().parent / 'analysis_runs' / 'regime_2026-10-27'
START = dt.datetime(2023, 9, 1, tzinfo=dt.timezone.utc).timestamp()
END = dt.datetime(2026, 10, 27, tzinfo=dt.timezone.utc).timestamp()


def collect(market, unit):
    path = OUT / f'{market}_{unit}.json'
    if path.exists():
        rows = json.loads(path.read_text(encoding='utf-8'))
        if rows and rows[0]['t'] <= START and rows[-1]['t'] + unit*60 >= END:
            print(market, unit, 'cached', len(rows), flush=True)
            return
    rows = {}
    cursor = END
    pages = 0
    while cursor > START:
        params = {'market': market, 'count': 200,
                  'to': dt.datetime.fromtimestamp(cursor, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
        url = 'https://api.upbit.com/v1/candles/minutes/' + str(unit) + '?' + urlencode(params)
        for attempt in range(5):
            try:
                with urlopen(Request(url, headers={'Accept':'application/json','User-Agent':'local-readonly-regime-validation/1'}), timeout=8) as response:
                    batch = json.load(response)
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(min(2**attempt, 8))
        if not batch:
            break
        for r in batch:
            stamp=dt.datetime.fromisoformat(r['candle_date_time_utc']).replace(tzinfo=dt.timezone.utc).timestamp()
            if stamp + unit*60 <= END:
                rows[stamp]={'t':stamp,'o':r['opening_price'],'h':r['high_price'],'l':r['low_price'],
                             'c':r['trade_price'],'v':r['candle_acc_trade_volume']}
        earliest=min(rows)
        if earliest >= cursor:
            raise RuntimeError('Pagination did not advance')
        cursor=earliest
        pages+=1
        if pages % 25 == 0:
            print(market,unit,'pages',pages,'through',dt.datetime.fromtimestamp(cursor,dt.timezone.utc).date(),flush=True)
        time.sleep(.16)
    result=sorted(rows.values(),key=lambda r:r['t'])
    path.write_text(json.dumps(result,separators=(',',':')),encoding='utf-8')
    print(market,unit,'saved',len(result),'sha256',hashlib.sha256(path.read_bytes()).hexdigest(),flush=True)


if __name__=='__main__':
    OUT.mkdir(parents=True,exist_ok=True)
    manifest={'frozen_before_results':True,'data_start':'2023-09-01 UTC','data_end_exclusive':'2026-10-27 UTC',
              'development':'2024-01-01..2024-12-31','evaluation':'2025-01-01..2026-10-26',
              'models':['baseline','defensive_regime','regime_with_range_candidate'],
              'optimization':'none; fixed rules; evaluation results will not tune parameters',
              'fee_per_side':.0005,'adverse_price_per_side':[.001,.002,.004],
              'acceptance':{'positive_base_and_stress_returns':True,'minimum_completed_positions':50,
                            'base_profit_factor_min':1.1,'max_drawdown_not_worse_than_baseline':True,
                            'forward_execution_validation_required':True},
              'limitations':['No historical orderbooks, depth or latency','Hourly bars approximate intrabar order','BTC/ETH only']}
    manifest_path=OUT/'protocol.json'
    if not manifest_path.exists():
        manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    for market in ('KRW-BTC','KRW-ETH'):
        for unit in (60,240):
            collect(market,unit)
