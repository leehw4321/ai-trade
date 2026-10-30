"""Public-only smoke observation. Does not fund, enable entries, or load credentials."""
import argparse
import json
import tempfile
import time
from pathlib import Path

from auto_trader import AutoTrader
from trend_engine import atomic


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=30)
    parser.add_argument('--output',type=Path,default=Path('analysis_runs/auto_v3_2026-10-29/public_observation.json'))
    args=parser.parse_args()
    if not 5<=args.seconds<=300:parser.error('seconds must be 5..300')
    with tempfile.TemporaryDirectory() as folder:
        a=AutoTrader(Path(folder));a.start()
        try:
            end=time.monotonic()+args.seconds
            while time.monotonic()<end:time.sleep(.25)
            result=a.snapshot()
        finally:a.close()
    result['observation_seconds']=args.seconds
    result['real_orders_submitted']=0
    result['profitability_verified']=False
    result['note']='REST 공개 시세·지표 연결 관찰. WebSocket과 실거래 체결·수익성 검증은 아님.'
    atomic(args.output,result)
    print(json.dumps({'output':str(args.output),'feed':result['feed_state'],
                      'universe':{k:result['universe'][k] for k in ('total','listed','eligible','candidates','error')},
                      'quotes':result['quotes'],'signals':{m:s.get('reason') for m,s in result['signals'].items()},
                      'monitor_ms':result['metrics']['monitor_ms'],
                      'real_orders_submitted':0,'profitability_verified':False},ensure_ascii=False))


if __name__=='__main__':main()
