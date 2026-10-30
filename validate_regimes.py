"""Fixed-rule chronological, cost-aware comparison. Public candles; no orders or credentials."""
import bisect
import ast
import collections
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import time

import adaptive_model as model
from trend_engine import evaluate, position_size, update_stop

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'analysis_runs'/'regime_2026-10-27'
MARKETS=('KRW-BTC','KRW-ETH')
FEE=.0005


def stamp(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp()


def prepare():
    fingerprint={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('adaptive_model.py',)}
    tree=ast.parse((ROOT/'trend_engine.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node,ast.FunctionDef) and node.name in ('evaluate','position_size','update_stop'):
            fingerprint[node.name]=hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest()
    fingerprint.update({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.glob('KRW-*.json')})
    cache_path=OUT/'prepared.json'
    if cache_path.exists():
        cache=json.loads(cache_path.read_text(encoding='utf-8'))
        if cache.get('fingerprint')==fingerprint:
            print('using prepared cache',len(cache['rows']),flush=True)
            return cache['rows']
    datasets={m:{u:json.loads((OUT/f'{m}_{u}.json').read_text()) for u in (60,240)} for m in MARKETS}
    times={m:{u:[x['t'] for x in datasets[m][u]] for u in (60,240)} for m in MARKETS}
    bars={m:{x['t']:x for x in datasets[m][60]} for m in MARKETS}
    timeline=sorted(set(bars['KRW-BTC']) & set(bars['KRW-ETH']))
    context={}
    prepared=[]
    for t in timeline:
        if t<stamp('2024-01-01'):
            continue
        hourly,fourly,features={},{},{}
        for m in MARKETS:
            i=bisect.bisect_right(times[m][60],t-3600)
            j=bisect.bisect_right(times[m][240],t-14400)
            hourly[m]=datasets[m][60][max(0,i-200):i]
            fourly[m]=datasets[m][240][max(0,j-600):j]
            key=(m,j)
            if key not in context:
                fs=fourly[m]
                context[key]=(model.indicators(fs),model.indicators(fs[:-1])) if len(fs)>=600 else None
            features[m]=context[key]
        p=model.classify(hourly['KRW-BTC'],fourly['KRW-BTC'],t,features['KRW-BTC'])
        base,adaptive,ranged={},{},{}
        for m in MARKETS:
            base[m]=evaluate(hourly[m],fourly[m],t,features[m][0] if features[m] else None)
            adaptive[m]=model.signal(hourly[m],fourly[m],p,base[m])
            ranged[m]=model.signal(hourly[m],fourly[m],p,base[m],allow_range=True,asset_features=features[m][0] if features[m] else None)
        prepared.append({'t':t,'bars':{m:bars[m][t] for m in MARKETS},'policy':p,
                         'baseline':base,'defensive_regime':adaptive,'regime_with_range_candidate':ranged})
        if len(prepared)%5000==0:
            print('prepared',len(prepared),flush=True)
    print('prepared',len(prepared),'hourly portfolio observations',flush=True)
    cache_path.write_text(json.dumps({'fingerprint':fingerprint,'rows':prepared},separators=(',',':')),encoding='utf-8')
    return prepared


def replay(prepared, kind, slip, start, end):
    cash=1e6
    positions={}
    cooldown={}
    trades=[]
    regimes=collections.Counter()
    entries=collections.Counter()
    daily=None
    day_start=1e6
    peak=1e6
    mdd=0
    halt=False
    embargo=0
    curve=[]
    relevant=[x for x in prepared if start<=x['t']<end]

    def value(prices):
        return cash+sum(p['volume']*prices[m]*(1-FEE) for m,p in positions.items())

    def sell(m,px,t,reason):
        nonlocal cash
        p=positions.pop(m)
        funds=p['volume']*px*(1-slip)
        proceeds=funds*(1-FEE)
        cash+=proceeds
        trades.append({'t':t,'market':m,'net':proceeds-p['cost'],'reason':reason,'strategy':p['strategy'],'regime':p['regime']})
        cooldown[m]=t+14400

    for x in relevant:
        t=x['t']; bars=x['bars']; policy=x['policy']
        adaptive=kind!='baseline'
        phase=policy['phase']
        regimes[phase]+=1
        opens={m:bars[m]['o'] for m in MARKETS}
        eq=value(opens)
        day=int((t+9*3600)//86400)
        if day!=daily:
            daily=day;day_start=eq
        peak=max(peak,eq)
        draw=1-eq/peak
        mdd=max(mdd,draw)
        if draw>=.1:
            halt=True
        if adaptive and phase=='crash':
            embargo=max(embargo,t+86400)
        liquidate=draw>=.2 or (adaptive and policy.get('liquidate'))
        for m in list(positions):
            p=positions[m]
            if liquidate:
                sell(m,opens[m],t,'regime/account liquidation')
            elif opens[m]<=p['stop']:
                sell(m,opens[m],t,'gap below stop')
            elif p['strategy']=='range' and (opens[m]>=p['target'] or t-p['entered']>=43200):
                sell(m,opens[m],t,'range target/time')
        eq=value(opens)
        if not halt and eq>day_start*.97 and (not adaptive or (policy.get('available') and t>=embargo)):
            for m in MARKETS:
                sig=x[kind][m]
                if m in positions or len(positions)>=2 or t<cooldown.get(m,0) or not sig.get('ok'):
                    continue
                px=opens[m]*(1+slip)
                # Next-open price check mirrors the live signal-price guard (no historical book).
                if not sig['breakout']<opens[m] or opens[m]>sig['close']*1.003 or opens[m]<sig['close']*.997:
                    continue
                stop_atr=sig.get('stop_atr',2)
                exposure=sum(positions[k]['volume']*opens[k] for k in positions)
                size=math.floor(position_size(eq,cash,exposure,stop_atr*sig['atr']/opens[m],2*FEE,
                                               sig.get('risk',.005),sig.get('max_position',.4),sig.get('max_total',.8)))
                if size<5000:
                    continue
                volume=size/px
                cash-=size*(1+FEE)
                positions[m]={'volume':volume,'cost':size*(1+FEE),'entry':px,'atr':sig['atr'],
                              'stop':px-stop_atr*sig['atr'],'peak':px,'entered':t,
                              'strategy':sig.get('strategy','trend'),'regime':phase,'target':sig.get('target',0)}
                entries[phase]+=1
                eq=value(opens)
        # Intrabar ordering is unknown. Check old stop first, then targets; only
        # raise trailing stop for the NEXT bar. Never fill a new stop using prior high.
        for m in list(positions):
            p=positions[m]; b=bars[m]
            if b['l']<=p['stop']:
                sell(m,min(b['o'],p['stop']),t,'stop first (conservative)')
            elif p['strategy']=='range' and b['h']>=p['target']:
                sell(m,max(b['o'],p['target']),t,'range target')
            else:
                trail=policy.get('trail_atr',3) if adaptive else 3
                p['peak'],p['stop']=update_stop(p,b['h'],trail)
        eq=value({m:bars[m]['c'] for m in MARKETS})
        peak=max(peak,eq)
        mdd=max(mdd,1-eq/peak)
        curve.append({'t':t,'equity':eq})
    if relevant:
        last=relevant[-1]
        for m in list(positions):
            sell(m,last['bars'][m]['c'],last['t']+3600,'period end')
        mdd=max(mdd,1-cash/peak)
    wins=[t['net'] for t in trades if t['net']>0]
    losses=[t['net'] for t in trades if t['net']<0]
    pf=sum(wins)/abs(sum(losses)) if losses else None
    return {'model':kind,'slippage_each_side':slip,'return_pct':(cash/1e6-1)*100,
            'net_krw':cash-1e6,'max_drawdown_pct':mdd*100,'completed_positions':len(trades),
            'win_rate_pct':len(wins)/len(trades)*100 if trades else None,'profit_factor':pf,
            'regime_hours':dict(regimes),'entries_by_regime':dict(entries),
            'range_positions':sum(t['strategy']=='range' for t in trades),'review_lock_triggered':halt},trades,curve


def main():
    prepared=prepare()
    start,end=stamp('2025-01-01'),stamp('2026-10-27')
    results=[]
    quarters=[]
    models=('baseline','defensive_regime','regime_with_range_candidate')
    for kind in models:
        for slip in (.001,.002,.004):
            result,trades,curve=replay(prepared,kind,slip,start,end)
            results.append(result)
            if slip==.001:
                (OUT/(kind+'_trades.json')).write_text(json.dumps(trades,indent=2),encoding='utf-8')
                (OUT/(kind+'_curve.json')).write_text(json.dumps(curve,separators=(',',':')),encoding='utf-8')
            for year,month in ((2025,1),(2025,4),(2025,7),(2025,10),(2026,1),(2026,4),(2026,7),(2026,10)):
                qstart=stamp(f'{year}-{month:02d}-01')
                qend=min(end,stamp(f'{year+1}-01-01') if month==10 else stamp(f'{year}-{month+3:02d}-01'))
                r,_,_=replay(prepared,kind,slip,qstart,qend)
                quarters.append({'quarter':f'{year}Q{(month-1)//3+1}',**r})
            print(kind,slip,'return',round(result['return_pct'],2),'MDD',round(result['max_drawdown_pct'],2),
                  'positions',result['completed_positions'],flush=True)
    base=next(r for r in results if r['model']=='baseline' and r['slippage_each_side']==.001)
    decisions={}
    for kind in models[1:]:
        rows=[r for r in results if r['model']==kind]
        normal=rows[0]
        checks={'base_positive':normal['net_krw']>0,'stress_positive':all(r['net_krw']>0 for r in rows[1:]),
                'minimum_50_positions':normal['completed_positions']>=50,
                'profit_factor_1_1':(normal['profit_factor'] or 0)>=1.1,
                'drawdown_not_worse':normal['max_drawdown_pct']<=base['max_drawdown_pct'],
                'forward_execution_validated':False}
        decisions[kind]={'checks':checks,'historical_pass':all(v for k,v in checks.items() if k!='forward_execution_validated'),
                         'live_entry_approved':all(checks.values())}
    report={'model_version':model.VERSION,'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'evaluation_start':'2025-01-01 UTC','evaluation_end_exclusive':'2026-10-27 UTC',
            'results':results,'quarterly_independent_accounts':quarters,'decisions':decisions,
            'sources_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('adaptive_model.py','trend_engine.py','validate_regimes.py')},
            'data_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.glob('KRW-*.json')},
            'limitations':['Hourly OHLC, no historical orderbook/depth/latency','Stops use existing stop first, trail update next bar',
                           'No parameter optimization; quarterly accounts reset separately, continuous evaluation does not',
                           'Review lock stays on after 10% drawdown','No claim of statistically proven edge or forward performance']}
    (OUT/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('saved',OUT/'validation.json',flush=True)


if __name__=='__main__':
    main()
