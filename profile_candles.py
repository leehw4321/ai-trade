"""Closed candle loader shared by portfolio and V3 profile management workers."""
import datetime as dt
import math
import time


def load_closed(public, cache, market, unit, count, now=None):
    now=time.time() if now is None else now
    key=(market,unit);old=cache.get(key,[])
    expected=int(now//(unit*60))*unit*60
    if len(old)>=count and old[-1]['t']+unit*60==expected:return old[-count:]
    data={b['t']:b for b in old}
    path='/v1/candles/days' if unit==1440 else '/v1/candles/minutes/'+str(unit)
    params={'market':market,'count':200}
    for _ in range(1 if len(old)>=count else math.ceil((count+1)/200)):
        rows=public(path,params)
        if not rows:break
        for r in rows:
            stamp=dt.datetime.fromisoformat(r['candle_date_time_utc']).replace(tzinfo=dt.timezone.utc).timestamp()
            if stamp+unit*60<=now:
                b={'t':stamp,'o':float(r['opening_price']),'h':float(r['high_price']),
                   'l':float(r['low_price']),'c':float(r['trade_price']),'v':float(r['candle_acc_trade_volume'])}
                if 'candle_acc_trade_price' in r:b['value']=float(r['candle_acc_trade_price'])
                data[stamp]=b
        params['to']=rows[-1]['candle_date_time_utc']+'Z'
    result=sorted(data.values(),key=lambda b:b['t'])[-count:]
    cache[key]=result
    return result
