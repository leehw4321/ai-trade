"""Offline fixed-candidate swing pilot, not a trading engine or live bot module.

Reads public daily files only. Prior close signals execute at the next open.
Four equal initial subaccounts drift independently; no cross-asset rebalancing.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import statistics

MODELS = ('SMA65', 'EMA10_20', 'Donchian55_20_ATR', 'SMA65_VOL20', 'ENSEMBLE_VOL20')
CAPITAL = 1_000_000.
MINIMUM = 5000.
FEE = .0005


def indicators(bars):
    close = [b['c'] for b in bars]
    ema10 = ema20 = close[0]
    atr = None
    tr_history = []
    out = []
    returns = []
    for i, b in enumerate(bars):
        ema10 += 2/11 * (b['c']-ema10)
        ema20 += 2/21 * (b['c']-ema20)
        tr = max(b['h']-b['l'], abs(b['h']-close[i-1]), abs(b['l']-close[i-1])) if i else b['h']-b['l']
        tr_history.append(tr)
        if i == 19:
            atr = statistics.mean(tr_history)
        elif i > 19:
            atr = (19*atr+tr)/20
        if i:
            returns.append(b['c']/close[i-1]-1)
        sma = {n: statistics.mean(close[i-n+1:i+1]) if i+1 >= n else None for n in (20,65,150)}
        out.append({'sma': sma, 'ema10': ema10, 'ema20': ema20, 'atr20': atr,
                    'sigma20': statistics.stdev(returns[-20:])*math.sqrt(365) if len(returns)>=20 else None,
                    'prior55high': max(x['h'] for x in bars[i-55:i]) if i>=55 else None,
                    'prior20low': min(x['l'] for x in bars[i-20:i]) if i>=20 else None})
    return out


def exposure(model, previous, indicator, invested):
    if model == 'HOLD':
        return 1.
    c = previous['c']
    if model == 'EMA10_20':
        return float(indicator['ema10'] > indicator['ema20'])
    if model == 'Donchian55_20_ATR':
        if invested:
            return float(not (indicator['prior20low'] is not None and c < indicator['prior20low']))
        return float(indicator['prior55high'] is not None and c > indicator['prior55high'])
    score = (sum(c > indicator['sma'][n] for n in (20,65,150))/3
             if model == 'ENSEMBLE_VOL20' else float(c > indicator['sma'][65]))
    if model.endswith('VOL20'):
        sigma = indicator['sigma20']
        return score * min(1., .20/sigma) if sigma and sigma>0 else 0.
    return score


def simulate_one(bars, model, start_index, initial, slippage, fee=FEE, minimum=MINIMUM):
    assert start_index >= 200
    assert 0 <= slippage < 1 and 0 <= fee < 1
    features = indicators(bars)
    cash, qty, stop, peak = initial, 0., None, None
    curve, exposures, actions, cycles = [], [], [], []
    cycle_cost = cycle_proceeds = 0.
    entry_index = None

    def sell(at_price, amount, i, reason):
        nonlocal cash, qty, cycle_proceeds, cycle_cost, entry_index
        amount = min(qty, amount)
        if amount <= 0 or amount*at_price < minimum:
            return False
        funds = amount*at_price*(1-slippage)
        cash += funds*(1-fee)
        qty = max(0., qty-amount)
        cycle_proceeds += funds*(1-fee)
        actions.append({'time': bars[i]['time'], 'side': 'sell', 'reason': reason,
                        'funds': funds, 'fee': funds*fee})
        if qty <= 1e-12:
            qty = 0.
            cycles.append({'net': cycle_proceeds-cycle_cost,
                           'holding_days': i-entry_index if entry_index is not None else None})
            cycle_proceeds = cycle_cost = 0.
            entry_index = None
        return True

    for i in range(start_index, len(bars)):
        b, prev, ind = bars[i], bars[i-1], features[i-1]
        was_invested = qty > 0
        target_fraction = exposure(model, prev, ind, was_invested)
        assert 0 <= target_fraction <= 1
        stopped = False
        if model == 'Donchian55_20_ATR' and was_invested:
            peak = max(peak, prev['c'])
            stop = max(stop, peak-3*ind['atr20'])
            if b['o'] <= stop:
                stopped = sell(b['o'], qty, i, 'gap_stop')
                target_fraction = 0.
        equity_open = cash+qty*b['o']
        target_value = equity_open*target_fraction
        current_value = qty*b['o']
        if target_value < current_value and not stopped:
            sell(b['o'], qty if target_fraction == 0 else (current_value-target_value)/b['o'], i, 'signal_or_resize')
        elif target_value > current_value and not stopped:
            amount_krw = min(cash, target_value-current_value)
            if amount_krw >= minimum:
                fill = b['o']*(1+slippage)
                bought = math.floor(amount_krw/(fill*(1+fee))*1e8)/1e8
                spent = bought*fill*(1+fee)
                if bought > 0:
                    if qty == 0:
                        entry_index = i
                    qty += bought
                    cash -= spent
                    cycle_cost += spent
                    actions.append({'time': b['time'], 'side': 'buy', 'reason': 'signal_or_resize',
                                    'funds': bought*fill, 'fee': bought*fill*fee})
                    if model == 'Donchian55_20_ATR' and not was_invested:
                        peak, stop = fill, fill-2.5*ind['atr20']
        if model == 'Donchian55_20_ATR' and qty>0 and not stopped and b['l'] <= stop:
            sell(min(b['o'], stop), qty, i, 'intraday_stop_proxy')
        if qty == 0:
            peak = stop = None
        assert cash >= -1e-6 and qty >= 0
        eq = cash+qty*b['c']
        curve.append(eq)
        exposures.append(qty*b['c']/eq if eq else 0.)
    return {'equity': curve, 'exposure': exposures, 'actions': actions, 'cycles': cycles,
            'open_position': qty>0, 'ending_cash': cash, 'ending_position_value': qty*bars[-1]['c']}


def statistics_for(equity, dates, initial):
    full = [initial]+equity
    returns = [b/a-1 for a,b in zip(full,full[1:])]
    peak, dd = initial, 0.
    for value in equity:
        peak = max(peak,value)
        dd = min(dd,value/peak-1)
    years = (dt.datetime.fromisoformat(dates[-1])-dt.datetime.fromisoformat(dates[0])).days/365.25+1/365.25
    cagr = (equity[-1]/initial)**(1/years)-1
    sd = statistics.stdev(returns) if len(returns)>1 else 0.
    return {'total_return_pct': (equity[-1]/initial-1)*100, 'cagr_pct': cagr*100,
            'maximum_daily_close_drawdown_pct': dd*100,
            'annualized_volatility_pct': sd*math.sqrt(365)*100,
            'sharpe_zero_cash': statistics.mean(returns)/sd*math.sqrt(365) if sd else None,
            'calmar': cagr/abs(dd) if dd else None, 'ending_equity_krw': equity[-1]}


def run(folder):
    protocol = json.loads((folder/'protocol.json').read_text(encoding='utf-8'))
    manifest = json.loads((folder/'data-manifest.json').read_text(encoding='utf-8'))
    data = {r['market']:json.loads((folder/(r['market']+'-daily.json')).read_text(encoding='utf-8')) for r in manifest}
    for item in manifest:
        assert hashlib.sha256((folder/(item['market']+'-daily.json')).read_bytes()).hexdigest()==item['sha256']
    # Common valid period is a data-availability constraint, never chosen by returns.
    starts = [dt.datetime.fromisoformat(protocol['evaluation_from'])]
    starts.extend(dt.datetime.fromisoformat(rows[200]['time']) for rows in data.values())
    start = max(starts)
    dates = [r['time'] for r in next(iter(data.values())) if dt.datetime.fromisoformat(r['time'])>=start]
    aligned = {}
    start_indices = {}
    for market, rows in data.items():
        index = next(i for i,r in enumerate(rows) if r['time']==dates[0])
        assert [r['time'] for r in rows[index:]] == dates
        aligned[market] = rows
        start_indices[market] = index
    results = []
    for slippage in protocol['slippage_one_way_scenarios']:
        for model in ('HOLD',)+MODELS:
            sleeves = {m:simulate_one(rows,model,start_indices[m],CAPITAL/len(data),slippage)
                       for m,rows in aligned.items()}
            equity = [sum(s['equity'][i] for s in sleeves.values()) for i in range(len(dates))]
            actions = [a for s in sleeves.values() for a in s['actions']]
            cycles = [c for s in sleeves.values() for c in s['cycles']]
            report = {'model':model,'slippage_one_way':slippage,**statistics_for(equity,dates,CAPITAL),
                      'completed_position_cycles':len(cycles),
                      'profitable_cycles':sum(c['net']>0 for c in cycles),
                      'median_cycle_days':statistics.median(c['holding_days'] for c in cycles) if cycles else None,
                      'buy_or_resize_orders':sum(a['side']=='buy' for a in actions),
                      'sell_or_resize_orders':sum(a['side']=='sell' for a in actions),
                      'fees_paid_krw':sum(a['fee'] for a in actions),
                      'open_positions':sum(s['open_position'] for s in sleeves.values()),
                      'average_invested_fraction':statistics.mean(sum(s['exposure'][i]*s['equity'][i] for s in sleeves.values())/equity[i] for i in range(len(dates))),
                      'maximum_coin_fraction':max(max(s['exposure'][i]*s['equity'][i]/equity[i] for s in sleeves.values()) for i in range(len(dates)))}
            later = next((i for i,d in enumerate(dates) if d>=protocol['later_reference_from']),None)
            if later is not None:
                report['later_reference_2024_onward'] = statistics_for(equity[later:],dates[later:],equity[later-1] if later else CAPITAL)
            year_summaries = {}
            for year in sorted({d[:4] for d in dates}):
                idx=[i for i,d in enumerate(dates) if d.startswith(year)]
                base=equity[idx[0]-1] if idx[0] else CAPITAL
                year_summaries[year]=(equity[idx[-1]]/base-1)*100
            report['calendar_year_returns_pct']=year_summaries
            report['per_coin']={m:statistics_for(s['equity'],dates,CAPITAL/len(data)) for m,s in sleeves.items()}
            results.append(report)
        btc=simulate_one(aligned['KRW-BTC'],'HOLD',start_indices['KRW-BTC'],CAPITAL,slippage)
        results.append({'model':'BTC_HOLD','slippage_one_way':slippage,**statistics_for(btc['equity'],dates,CAPITAL)})
    output={'schema':'gpt-swing-fixed-pilot-v1','protocol_sha256':hashlib.sha256((folder/'protocol.json').read_bytes()).hexdigest(),
            'source_code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'first_daily_open_utc':dates[0],'last_daily_open_utc':dates[-1],
            'last_completed_close_utc':(dt.datetime.fromisoformat(dates[-1])+dt.timedelta(days=1)).isoformat(),
            'common_daily_observations':len(dates),'data_manifest':manifest,'results':results,
            'limits':protocol['limits']+['Candle-open fill proxy, not orderbook execution',
                'Daily-close drawdown understates possible intraday drawdown',
                'Four independent cash sleeves drift; no production coin cap or cross-sleeve order router modeled',
                '20% scaler is a per-coin exposure rule, not a proven portfolio volatility ceiling',
                '2024-onward is a later historical reference, not a pristine untouched validation set',
                '70/30 combined live portfolio and takeover of existing holdings not simulated']}
    (folder/'pilot-results.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
    print(json.dumps({'period':[dates[0],output['last_completed_close_utc']], 'days':len(dates),
                     'results':[{k:r.get(k) for k in ['model','slippage_one_way','cagr_pct','maximum_daily_close_drawdown_pct','sharpe_zero_cash','completed_position_cycles','median_cycle_days','average_invested_fraction','maximum_coin_fraction','later_reference_2024_onward']} for r in results]},indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('data_folder',type=Path)
    run(parser.parse_args().data_folder)
