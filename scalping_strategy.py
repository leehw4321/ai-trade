"""V3 causal 1h/15m/5m strategy. Pure functions shared by replay and observation."""
import math

from adaptive_model import ema, indicators

VERSION = 'auto-v4.0'
MARKETS = ('KRW-BTC', 'KRW-ETH')
RULES = {'risk': .0025, 'max_position': .25, 'max_total': .5,
         'max_open_risk': .005, 'min_distance': .004, 'max_distance': .015,
         'max_spread': .001, 'partial_r': 1.5, 'partial_ratio': .3,
         'trail_atr': 2.5, 'max_seconds': 2700, 'progress_seconds':900, 'cooldown_seconds':600, 'signal_age': 30,
         'fee': .0005, 'impact': .0005, 'minimum': 5000}


def validate_bars(rows, seconds, count, now):
    if len(rows) < count:
        raise ValueError(f'{seconds//60}분봉 {count}개 준비 중')
    if not 0 <= now - rows[-1]['t'] - seconds < seconds:
        raise ValueError('마감 봉 지연/미완성')
    for b in rows:
        if (not all(isinstance(b[k], (int, float)) and math.isfinite(b[k]) for k in ('t','h','l','c','v'))
                or not 0 < b['l'] <= b['c'] <= b['h'] or b['v'] < 0
                or b['t'] % seconds):
            raise ValueError('봉 가격·시간 오류')
    if any(b['t']-a['t'] != seconds for a,b in zip(rows, rows[1:])):
        raise ValueError('봉 공백·중복·역순: 신규 진입 중지')


def evaluate(hour, quarter, five, now):
    """Never reads a forming bar or a higher-timeframe bar closed after now."""
    out = {'ok': False, 'version': VERSION, 'time': now, 'checks': {}}
    try:
        for rows, sec, count in ((hour,3600,600),(quarter,900,200),(five,300,200)):
            validate_bars(rows, sec, count, now)
        h = [b['c'] for b in hour]
        f15, f5 = indicators(quarter), indicators(five)
        end = five[-1]['t'] + 300
        session = math.floor(end/86400)*86400
        # The bar ending exactly at midnight belongs to the preceding session.
        today = [b for b in five if b['t'] >= session]
        if not today or today[0]['t'] != session or end-session < 1800:
            raise ValueError('VWAP 세션 준비: KST 09:30 이후 진입')
        volume = sum(b['v'] for b in today)
        if volume <= 0:
            raise ValueError('VWAP 거래량 없음')
        exact = all('value' in b and math.isfinite(b['value']) and b['value'] >= 0 for b in today)
        vwap = sum(b['value'] if exact else (b['h']+b['l']+b['c'])/3*b['v'] for b in today)/volume
        checks = {'1시간 상승': h[-1] > ema(h,50) > ema(h,200) and ema(h,50)>ema(h[:-3],50),
                  '15분 추세': f15['ema20'] > f15['ema50'] and f15['adx'] >= 25 and f15['plus_di'] > f15['minus_di'],
                  'VWAP 위': five[-1]['c'] > vwap,
                  '돌파 후 첫 눌림': False}
        candidate = None
        for lag in (1,2,3):
            i = len(five)-1-lag
            before = five[i-20:i]
            level = max(b['h'] for b in before)
            mean = sum(b['v'] for b in before)/20
            if not (five[i]['c'] > level and mean > 0 and five[i]['v'] >= mean*1.5):
                continue
            pull = five[i+1:]
            a = f5['atr']
            if min(b['l'] for b in pull) < level-.5*a:
                continue
            # Only the FIRST qualifying closed retest may create an entry.
            hits = [j for j,b in enumerate(pull) if abs(b['l']-level)<=.25*a and b['c']>level]
            if hits and hits[0] == len(pull)-1:
                candidate = (level,min(b['l'] for b in pull),five[i]['v']/mean)
                break
        checks['돌파 후 첫 눌림'] = candidate is not None
        out.update(checks=checks, atr=f5['atr'], close=five[-1]['c'], ema20=f5['ema20'],
                   adx=f15['adx'], vwap=vwap, vwap_method='거래대금/수량' if exact else 'HLC3 근사',
                   id=str(int(five[-1]['t'])), closed_at=end)
        if candidate:
            out.update(breakout=candidate[0], low=candidate[1], volume_ratio=candidate[2])
        out['ok'] = all(checks.values()) and f5['atr'] > 0
        out['reason'] = '돌파·눌림 조건 충족' if out['ok'] else ' / '.join(k for k,v in checks.items() if not v)
    except (KeyError, ValueError, TypeError, ZeroDivisionError, OverflowError) as e:
        out['reason'] = str(e)
    return out


def initial_stop(entry, atr, low):
    return min(low-.2*atr, entry-1.5*atr)


def evaluate_intraday(quarter,five,one,now):
    """15m direction, 5m retest, 1m closed confirmation; typical hold 5..30m."""
    out={'ok':False,'version':VERSION,'time':now,'checks':{}}
    try:
        for rows,seconds,count in ((quarter,900,200),(five,300,200),(one,60,60)):
            validate_bars(rows,seconds,count,now)
        f15,f5,f1=indicators(quarter),indicators(five),indicators(one)
        close=one[-1]['c'];a=f5['atr'];end=one[-1]['t']+60
        out.update(id=str(int(one[-1]['t'])),closed_at=end,close=close,atr=a,ema20=f1['ema20'],
                   adx=f5['adx'],timeframe='15m/5m/1m')
        session=math.floor(end/86400)*86400
        today=[b for b in five if b['t']>=session]
        if not today or today[0]['t']!=session or end-session<300:
            raise ValueError('VWAP 세션 준비: KST 09:05 이후 진입')
        volume=sum(b['v'] for b in today)
        if volume<=0:raise ValueError('VWAP 거래량 없음')
        exact=all('value' in b and math.isfinite(b['value']) and b['value']>=0 for b in today)
        vwap=sum(b['value'] if exact else (b['h']+b['l']+b['c'])/3*b['v'] for b in today)/volume
        # Last closed 5m bar must be the FIRST retest after a volume breakout.
        candidate=None
        for lag in (1,2,3):
            i=len(five)-1-lag;before=five[i-20:i];level=max(b['h'] for b in before)
            average=sum(b['v'] for b in before)/20
            if not (average>0 and five[i]['c']>level and five[i]['v']>=average*1.5):continue
            pull=five[i+1:]
            if min(b['l'] for b in pull)<level-.5*a:continue
            hits=[j for j,b in enumerate(pull) if abs(b['l']-level)<=.25*a and b['c']>level]
            if hits and hits[0]==len(pull)-1:
                candidate=(level,min(b['l'] for b in pull),five[i]['v']/average);break
        v1=sum(b['v'] for b in one[-21:-1])/20
        value1=one[-1].get('value',one[-1]['c']*one[-1]['v'])
        value5=five[-1].get('value',five[-1]['c']*five[-1]['v'])
        checks={'15분 상승 방향':f15['close']>f15['ema20']>f15['ema50'] and f15['ema50']>f15['ema50_prev'],
                '5분 추세 강도':f5['ema20']>f5['ema50'] and f5['adx']>=20 and f5['plus_di']>f5['minus_di'],
                '5분 돌파 후 첫 눌림':candidate is not None,
                '1분 상승 확인':close>one[-2]['h'] and close>f1['ema20'],
                '1분 거래량 1.3배':v1>0 and one[-1]['v']>=v1*1.3,
                '최근 거래대금':value1>=5_000_000 and value5>=30_000_000,
                '급등 추격 차단':abs(close/one[-2]['c']-1)<=.015 and abs(five[-1]['c']/five[-2]['c']-1)<=.04,
                'VWAP 위':close>vwap}
        if candidate:out.update(breakout=candidate[0],low=candidate[1],volume_ratio=candidate[2])
        out.update(checks=checks,vwap=vwap,vwap_method='거래대금/수량' if exact else 'HLC3 근사',
                   ok=all(checks.values()) and a>0)
        out['reason']='단타 15분/5분/1분 조건 충족' if out['ok'] else ' / '.join(k for k,v in checks.items() if not v)
    except (KeyError,ValueError,TypeError,ZeroDivisionError,OverflowError) as e:out['reason']=str(e)
    return out


def walk_book(units, side, volume):
    """Executable VWAP for the ENTIRE requested quantity; shallow books fail closed."""
    left, funds = volume, 0.
    for u in units:
        take = min(left, float(u[side+'_size']))
        funds += take*float(u[side+'_price'])
        left -= take
        if left <= 1e-10:
            return funds/volume
    raise ValueError('호가 깊이 부족')


def entry_plan(signal, quote, equity, cash, exposure, open_risk, now, rules=None):
    RULES = rules or globals()['RULES']
    if not signal.get('ok') or not 0 <= now-signal.get('closed_at',0) <= RULES['signal_age']:
        raise ValueError('신호 만료/조건 미충족')
    if not 0 <= now-quote['time'] <= 2:
        raise ValueError('호가 2초 이상 지연')
    u = quote['units'][0]
    ask, bid = float(u['ask_price']), float(u['bid_price'])
    if (ask-bid)/((ask+bid)/2) > RULES['max_spread'] or bid <= signal['breakout']:
        raise ValueError('호가 차이 초과/돌파 이탈')
    if ask > signal['close']+.25*signal['atr']:
        raise ValueError('급등 추격 차단')
    stop = initial_stop(ask, signal['atr'], signal['low'])
    distance = (ask-stop)/ask
    if not RULES['min_distance'] <= distance <= RULES['max_distance']:
        raise ValueError('손절 거리 0.4~1.5% 범위 이탈')
    # Include observed spread once relative to mid, and adverse impact reserve.
    cost = 2*RULES['fee']+(ask-bid)/((ask+bid)/2)+2*RULES['impact']
    # A 1% reserve of the risk allowance absorbs fee/rounding and equity mark changes
    # between reservation and fill; it does not enlarge the allowed account risk.
    risk_cash = max(0,min(equity*RULES['risk'],equity*RULES['max_open_risk']-open_risk))*.99
    amount = max(0,min(risk_cash/(distance+cost),equity*RULES['max_position'],
                       equity*RULES['max_total']-exposure,cash/(1+RULES['fee'])))
    if amount < RULES['minimum']:
        raise ValueError('운용자금/위험 한도 부족')
    volume = amount/ask
    buy = walk_book(quote['units'],'ask',volume)
    sell = walk_book(quote['units'],'bid',volume)
    cost = 2*RULES['fee']+(buy-sell)/buy+2*RULES['impact']
    if cost > distance*.25:
        raise ValueError('예상 왕복 비용이 손절 거리의 25% 초과')
    # Re-size against actual depth cost (may only shrink).
    amount = min(amount, risk_cash/(distance+cost))
    for side, limit in (('ask',ask*1.001),('bid',bid*.999)):
        depth = sum(float(v[side+'_price'])*float(v[side+'_size']) for v in quote['units']
                    if (float(v[side+'_price'])<=limit if side=='ask' else float(v[side+'_price'])>=limit))
        if depth < amount*5:
            raise ValueError('진입/청산 5배 호가 잔량 부족')
    # Passive reservation; do not chase ask. Executed cost is recalculated on fill.
    return {'amount':amount,'price':min(bid,signal['close']), 'stop':stop,
            'atr':signal['atr'],'low':signal['low'],'cost_rate':cost,'signal':signal['id'],
            'expires':signal['closed_at']+30,'breakout':signal['breakout']}


def exit_decision(p, bid, context, now):
    """Mutates only protective state; returns intent, never submits orders."""
    p['peak'] = max(p['peak'],bid)
    p['trough'] = min(p.get('trough',p['entry']),bid)
    r = p['r']
    bucket=p.get('bucket','short')
    if bucket!='short' or p['peak'] >= p['entry']+RULES['partial_r']*r:
        p['trail_on'] = True
    if p.get('trail_on') and (bucket=='short' or context.get('bucket')==bucket):
        from investment_profiles import PROFILES
        multiplier=PROFILES[bucket]['trail_atr']
        p['stop'] = max(p['stop'],p['peak']-multiplier*context.get('atr',p.get('management_atr',p['atr'])))
    if p.get('part_done') and bucket=='short':
        p['stop'] = max(p['stop'],p['entry']*(1+RULES['fee'])/(1-RULES['fee']-RULES['impact']))
    if bid <= p['stop']:
        return '손절/추적 청산'
    if (p.get('strategy')=='breakout' and bucket=='short' and now-p['since']<=60
            and bid<=p.get('breakout_level',0)):
        return '돌파 실패 · 60초 내 돌파선 복귀'
    since=p.get('profile_since',p['since']);limit=p.get('max_seconds',RULES['max_seconds'])
    if limit>0 and now-since >= limit:
        return '최대 보유시간 종료'
    progress=p.get('progress_seconds',RULES['progress_seconds'])
    if (progress>0 and now-since>=progress and p['peak']-p['entry']<.5*r
            and context.get('fresh') and context['close']<context['ema20']):
        return '단타 진행 실패'
    return ''
