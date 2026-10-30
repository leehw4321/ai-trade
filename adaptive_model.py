"""Causal, interpretable long-only regime rules. Thresholds are hypotheses, not guarantees."""
import math
import statistics
import time

VERSION = 'regime-defense-1'
LABELS = {'crash':'폭락','fall':'하락','range':'횡보','rise':'상승','surge':'급등','unknown':'확인 불가'}
ZONES = {'crash':0,'fall':1,'range':2,'rise':3,'surge':4}


def ema(values, period):
    value = values[0]
    alpha = 2/(period+1)
    for x in values[1:]:
        value = x*alpha + value*(1-alpha)
    return value


def rma(values, period=14):
    if len(values)<period:
        raise ValueError('insufficient smoothing data')
    out = [sum(values[:period])/period]
    for value in values[period:]:
        out.append((out[-1]*(period-1)+value)/period)
    return out


def indicators(rows):
    if len(rows)<30:
        raise ValueError('insufficient indicator data')
    closes = [r['c'] for r in rows]
    tr, plus, minus, gains, losses = [], [], [], [], []
    for a,b in zip(rows,rows[1:]):
        tr.append(max(b['h']-b['l'],abs(b['h']-a['c']),abs(b['l']-a['c'])))
        up,down=b['h']-a['h'],a['l']-b['l']
        plus.append(max(up,0) if up>down else 0)
        minus.append(max(down,0) if down>up else 0)
        gains.append(max(0,b['c']-a['c']))
        losses.append(max(0,a['c']-b['c']))
    atrs,ps,ms=rma(tr),rma(plus),rma(minus)
    pd=[100*p/t if t else 0 for p,t in zip(ps,atrs)]
    md=[100*m/t if t else 0 for m,t in zip(ms,atrs)]
    dx=[100*abs(p-m)/(p+m) if p+m else 0 for p,m in zip(pd,md)]
    gain,loss=rma(gains)[-1],rma(losses)[-1]
    rsi=50 if gain==loss==0 else 100 if not loss else 100-100/(1+gain/loss)
    return {'close':closes[-1],'ema200':ema(closes,200),'ema50':ema(closes,50),
            'ema50_prev':ema(closes[:-1],50),'ema20':ema(closes,20),'atr':atrs[-1],
            'adx':rma(dx)[-1],'plus_di':pd[-1],'minus_di':md[-1],'rsi':rsi}


def unknown(reason):
    return {'available':False,'phase':'unknown','label':LABELS['unknown'],'reason':reason,
            'allow_entry':False,'risk':0,'max_position':0,'max_total':0,'trail_atr':3,
            'version':VERSION,'time':0}


def bullish(f):
    return f['close']>f['ema200'] and f['ema50']>f['ema50_prev'] and f['plus_di']>f['minus_di'] and f['adx']>=25


def bearish(f):
    return f['close']<f['ema200'] and f['ema50']<f['ema50_prev'] and f['minus_di']>f['plus_di'] and f['adx']>=25


def policy(phase, reason, now, **features):
    entry = phase=='rise'
    trail={'crash':1.5,'fall':2,'range':2.5,'rise':3,'surge':2}[phase]
    descriptions={'crash':'신규 매수 금지 · 봇 보유분 청산 시도 · 24시간 이상 회복 대기',
                  'fall':'신규 매수 금지 · 봇 보유분 청산 시도 · 현금 대기',
                  'range':'새 돌파 매수 중지 · 기존 보유분 2.5 ATR 추적 · 평균회귀는 연구 전용',
                  'rise':'확정 상승 추세 + 거래량 돌파 · 거래당 0.5% 위험 · 3 ATR 추적',
                  'surge':'추격 매수 금지 · 추가 매수 중지 · 기존 보유분 2 ATR 추적'}
    return {'available':True,'phase':phase,'label':LABELS[phase],'zone':ZONES[phase],
            'score':(-80,-40,0,40,80)[ZONES[phase]],'reason':reason,'action':descriptions[phase],
            'allow_entry':entry,'risk':.005 if entry else 0,'max_position':.4 if entry else 0,
            'max_total':.8 if entry else 0,'stop_atr':2,'trail_atr':trail,
            'liquidate':phase in ('crash','fall'),'version':VERSION,'time':now,**features}


def classify(hour, four, now=None, features=None):
    now=time.time() if now is None else now
    if len(hour)<30 or len(four)<600:
        return unknown('BTC 1시간봉 30개·4시간봉 600개 준비 필요')
    for rows,seconds,recent in ((hour,3600,30),(four,14400,50)):
        if not 0<=now-(rows[-1]['t']+seconds)<seconds:
            return unknown('거래 국면 기준 봉 지연/미완성')
        if any(b['t']-a['t']!=seconds for a,b in zip(rows[-recent:],rows[-recent+1:])):
            return unknown('최근 국면 기준 봉 공백: 신규 진입 금지')
    try:
        f,prev=features if features is not None else (indicators(four),indicators(four[:-1]))
        r1=hour[-1]['c']/hour[-2]['c']-1
        r4=four[-1]['c']/four[-2]['c']-1
        r24=hour[-1]['c']/hour[-25]['c']-1
        fields={'adx':f['adx'],'atr_pct':f['atr']/f['close'], 'r1':r1,'r4':r4,'r24':r24,
                'bar':four[-1]['t'],'hour_bar':hour[-1]['t']}
        if not all(math.isfinite(v) for v in fields.values()):
            return unknown('국면 지표 값 오류')
        if r1<=-.03 or r4<=-.06:
            return policy('crash','BTC 마감 1시간 -3% 또는 4시간 -6% 충격',now,**fields)
        if bullish(f) and (r24>=.08 or (f['atr']>0 and (f['close']-f['ema20'])/f['atr']>=3)):
            return policy('surge','상승 추세 중 BTC 24시간 +8% 또는 EMA20에서 3 ATR 이격',now,**fields)
        if bearish(f) and bearish(prev):
            return policy('fall','BTC 4시간 하락 추세·ADX 25 이상을 2개 마감 봉에서 확인',now,**fields)
        if bullish(f) and bullish(prev):
            return policy('rise','BTC 4시간 상승 추세·ADX 25 이상을 2개 마감 봉에서 확인',now,**fields)
        return policy('range','추세 미확정/전환 대기 또는 ADX 약화: 신규 매수 대기',now,**fields)
    except (ValueError,KeyError,ZeroDivisionError,OverflowError):
        return unknown('국면 지표 계산 실패')


def signal(hour, four, market_policy, base, allow_range=False, asset_features=None):
    """Same function for live, paper and replay. base comes from the original evaluate()."""
    out={**base,'stop_atr':2,'trail_atr':market_policy.get('trail_atr',3),'strategy':'trend',
         'regime':market_policy.get('phase','unknown')}
    if not market_policy.get('available'):
        return {**out,'ok':False,'reason':market_policy['reason']}
    phase=market_policy['phase']
    if phase=='rise':
        return out
    if phase!='range' or not allow_range or 'atr' not in base:
        return {**out,'ok':False,'reason':market_policy['action']}
    try:
        f,prev=indicators(hour),indicators(hour[:-1])
        ff=asset_features if asset_features is not None else indicators(four)
        mid=statistics.mean(r['c'] for r in hour[-20:])
        lower=mid-2*statistics.pstdev(r['c'] for r in hour[-20:])
        old_mid=statistics.mean(r['c'] for r in hour[-21:-1])
        old_lower=old_mid-2*statistics.pstdev(r['c'] for r in hour[-21:-1])
        ok=(ff['adx']<20 and abs(ff['close']/ff['ema200']-1)<=.03 and
            prev['rsi']<30<=f['rsi'] and hour[-2]['c']<old_lower and hour[-1]['c']>lower)
        return {**out,'ok':ok,'reason':'횡보 평균회귀 후보' if ok else '횡보 평균회귀 조건 미충족',
                'strategy':'range','stop_atr':1.5,'trail_atr':2,'target':mid,'max_hours':12,
                'breakout':lower,'risk':.00125,'max_position':.2,'max_total':.4}
    except (ValueError,KeyError,ZeroDivisionError):
        return {**out,'ok':False,'reason':'평균회귀 계산 실패'}
