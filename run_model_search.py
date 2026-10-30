"""Frozen 16-model train/validation selection, then one held-out evaluation.

Only public historical data is read. No exchange account or live order code.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'analysis_runs'/'model_search'
sys.path.insert(0,str(OUT))
from model_candidates import CANDIDATES, prepare_features, iter_signals
from research_portfolio import load_5m, simulate, cost_config, parse_time, buy_hold_baseline


def write(path,value):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)


def metrics(summary):
    return {'return_pct':summary['net_return_pct'],'trades':summary['trade_count'],
            'max_drawdown_pct':summary['max_drawdown_pct'],
            'win_rate_pct':summary['win_rate_pct'],'profit_factor':summary['profit_factor'],
            'start':summary['start_kst'],'end':summary['end_exclusive_kst']}


def name(config):
    family={'breakout':'추세 돌파','pullback':'추세 눌림 회복',
            'bb_recovery':'볼린저 하단 회복','rsi_recovery':'RSI 과매도 회복'}[config['family']]
    return f"{config['timeframe_s']//60}분 {family} ({config['id']})"


def qualified(item):
    tr=item['train']['base'];v=item['validation']['base'];s=item['validation']['stress']
    return (tr['trade_count']>=10 and tr['max_drawdown_pct']<=5 and
            v['trade_count']>=5 and v['net_return_pct']>0 and s['net_return_pct']>0 and
            v['max_drawdown_pct']<=5 and s['max_drawdown_pct']<=5)


def main():
    started=time.monotonic();protocol=json.loads((OUT/'protocol.json').read_text(encoding='utf-8'))
    assert len(CANDIDATES)==protocol['max_candidates']==16
    assert hashlib.sha256((OUT/'candidates.json').read_bytes()).hexdigest()==protocol['candidate_sha256']
    db=ROOT/'analysis_runs/ytd_v4/historical/candles_1m.sqlite'
    periods={key:tuple(parse_time(x) for x in protocol['selection_'+key]) for key in ('train','validation')}
    test=tuple(parse_time(x) for x in protocol['evaluation_test'])
    print(json.dumps({'stage':'loading_train_validation_only','end':protocol['selection_validation'][1]}),flush=True)
    rows,quality=load_5m(db,end=periods['validation'][1])
    features={m:prepare_features(bars) for m,bars in rows.items()}
    results=[]
    for config in CANDIDATES:
        signals={m:list(iter_signals(p,config)) for m,p in features.items()}
        item={'config':config,'name':name(config)}
        for period,(start,end) in periods.items():
            item[period]={}
            for cost in ('base','stress'):
                result=simulate(rows,signals,start,end,cost_config(cost),candidate_id=config['id'],include_equity=False)
                item[period][cost]=result['summary']
        item['qualified_validation']=qualified(item)
        results.append(item)
        write(OUT/'search_progress.json',{'status':'training_validation','completed':len(results),'total':16,'results':results})
        print(json.dumps({'stage':'candidate_complete','candidate':config['id'],
                          'train':item['train']['base']['net_return_pct'],
                          'validation':item['validation']['base']['net_return_pct'],
                          'validation_stress':item['validation']['stress']['net_return_pct'],
                          'validation_trades':item['validation']['base']['trade_count'],
                          'qualified':item['qualified_validation']},ensure_ascii=True),flush=True)
    eligible=[r for r in results if r['qualified_validation']]
    pool=eligible or [r for r in results if r['train']['base']['trade_count']>=10] or results
    selected=sorted(pool,key=lambda r:(-r['validation']['base']['net_return_pct'],
                                      r['validation']['base']['max_drawdown_pct'],r['config']['id']))[0]
    frozen={'selected_config':selected['config'],'qualified_validation':bool(eligible),
            'selected_before_test':True,'selected_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
            'candidate_sha256':protocol['candidate_sha256'],
            'source_sha256':{n:hashlib.sha256((OUT/n).read_bytes()).hexdigest() for n in ('model_candidates.py','research_portfolio.py')},
            'ranking':'validation return descending among fixed eligibility; no test model reselection'}
    write(OUT/'frozen_selection_before_test.json',frozen)
    write(OUT/'train_validation_results.json',{'protocol':protocol,'quality':quality,'results':results})
    print(json.dumps({'stage':'selection_frozen','candidate':selected['config']['id'],'qualified':bool(eligible)}),flush=True)
    # The held-out data is loaded only after the selected ID has been written.
    del features,rows
    rows,full_quality=load_5m(db,end=test[1])
    features={m:prepare_features(bars) for m,bars in rows.items()}
    signals={m:list(iter_signals(p,selected['config'])) for m,p in features.items()}
    held={};full={}
    for cost in ('base','stress'):
        held[cost]=simulate(rows,signals,*test,cost_config(cost),candidate_id=selected['config']['id'])
        full[cost]=simulate(rows,signals,periods['train'][0],test[1],cost_config(cost),candidate_id=selected['config']['id'])
    approved=(bool(eligible) and held['base']['summary']['net_return_pct']>0 and
              held['stress']['summary']['net_return_pct']>0 and held['base']['summary']['trade_count']>=10 and
              held['base']['summary']['max_drawdown_pct']<=5 and held['stress']['summary']['max_drawdown_pct']<=5)
    result={'status':'complete','created_kst':dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(),
            'protocol':protocol,'quality':full_quality,'train_validation':results,
            'selected_config':selected['config'],'selected_name':selected['name'],
            'held_out':held,'selected_full_year_replay':full,
            'buy_hold_test':buy_hold_baseline(rows,*test,cost_config('base')),
            'qualified_validation':bool(eligible),'approved_paper':approved,
            'test_used_for_model_selection':False,'expected_future_return_estimated':False,
            'real_orders_submitted':0,'elapsed_seconds':time.monotonic()-started}
    write(OUT/'results.json',result)
    choice={'status':'selected' if eligible else 'no_qualified_model',
            'created_kst':result['created_kst'],'selected_config':selected['config']|{'name':selected['name'],'timeframe':str(selected['config']['timeframe_s']//60)+'분'},
            'selected_metrics':{'train':metrics(selected['train']['base']),
                                'validation':metrics(selected['validation']['base']),
                                'test':metrics(held['base']['summary'])},
            'stress_test_metrics':metrics(held['stress']['summary']),
            'selection_rule':protocol['selection'],'approved_paper':approved,
            'scope':'2026-01-01~10-30 · 현재 상장 대표10종목 · 현물 매수 · 수수료·슬리피지 포함; 전체299종목 아님',
            'paper_only':True,'expected_future_return_estimated':False}
    write(OUT/'selection.json',choice)
    print(json.dumps({'stage':'complete','model':selected['config']['id'],'qualified_validation':bool(eligible),
                      'held_out_base':held['base']['summary']['net_return_pct'],
                      'held_out_stress':held['stress']['summary']['net_return_pct'],
                      'held_out_trades':held['base']['summary']['trade_count'],'approved_paper':approved,
                      'seconds':result['elapsed_seconds']},ensure_ascii=True),flush=True)


if __name__=='__main__':main()
