"""Offline recorded-event replay using the same V3 reservation/exit engine.

JSONL event: {time: delivery_epoch, type: book|signal, market: KRW-BTC,
 book: {time: exchange_epoch, units: [...]}} OR {signal: {...}}.
No synthetic intrabar ordering, no network, no real account. Input must be sorted.
"""
import argparse
import json
import math
import tempfile
from pathlib import Path

from auto_trader import AutoTrader
from trend_engine import atomic
from scalp_universe import valid_code


def replay(events, budget=1000000, delay=0):
    if not math.isfinite(delay) or not 0<=delay<=10:raise ValueError('delay must be 0..10')
    with tempfile.TemporaryDirectory() as folder:
        a=AutoTrader(Path(folder),public=lambda *args: (_ for _ in ()).throw(RuntimeError('offline replay')))
        a.action({'action':'auto_v3_config','budget':budget,'markets':['KRW-BTC','KRW-ETH']})
        a.running=True;previous=-math.inf;first=None;count=0;high=budget;max_dd=0
        for event in events:
            when=float(event['time'])+delay
            if not math.isfinite(when) or when<previous:raise ValueError('events must be chronological')
            first=when if first is None else first;previous=when;count+=1
            if event['type']=='universe':
                markets=event['markets']
                if not isinstance(markets,list) or any(not valid_code(m) for m in markets):raise ValueError('invalid universe')
                a.state.update(markets=markets,universe_mode='all')
                a.catalog={m:{} for m in markets};a.catalog_time=event.get('catalog_time',when)
                a.universe_time=when;a.screened=event['screened'];a.candidates=event['candidates']
                a.step(when);continue
            m=event['market']
            if not valid_code(m):raise ValueError('unsupported market')
            if m not in a.state['markets'] and not a.catalog:
                a.state['markets'].append(m)  # Explicit offline event, never a live market listing.
            if event['type']=='profile':
                a.move_position_profile(m,event['bucket'],when)
            elif event['type']=='book':
                b=event['book'];a.ingest(m,b['units'],b['time'],'replay',when)
            elif event['type'] in ('signal','breakout_signal'):
                s=event['signal']
                if s.get('time',when)>when or s.get('closed_at',when)>when:
                    raise ValueError('future signal')
                if event['type']=='breakout_signal':
                    a.breakout_signals[m]=s
                    if m not in a.candidates:a.candidates.append(m)
                else:a.signals[m]=s
                if event['type']=='signal' and all(k in s for k in ('atr','close','ema20','closed_at')):
                    a.contexts[m]={k:s[k] for k in ('atr','close','ema20','closed_at','bucket','unit_seconds') if k in s}
            else:raise ValueError('unsupported event type')
            a.step(when)
            value=a.equity();high=max(high,value);max_dd=max(max_dd,1-value/high)
        a.validate();result=a.snapshot()
        result.update(events=count,delay_seconds=delay,max_drawdown_pct=max_dd*100,
                      first=first,last=None if previous==-math.inf else previous,
                      profitability_verified=False,real_orders_submitted=0,
                      note='기록 이벤트 재현. 미청산 포지션을 낙관적 종가 체결로 강제 청산하지 않음. 과거 호가·독립 평가·장기 관찰 없이 실거래 해제 불가.')
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--budget',type=float,default=1000000)
    parser.add_argument('--delay',type=float,default=0)
    args=parser.parse_args()
    with args.events.open(encoding='utf-8') as f:
        result=replay((json.loads(line) for line in f if line.strip()),args.budget,args.delay)
    atomic(args.output,result)
    print(json.dumps({'output':str(args.output),'events':result['events'],
                      'stats':result['stats'],'pnl':result['pnl'],'max_drawdown_pct':result['max_drawdown_pct']},ensure_ascii=False))


if __name__=='__main__':main()
