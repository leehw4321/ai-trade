"""Historical replay of paper signals and isolated exit/filter diagnostics.

Uses only candles available before each decision; synthetic bid/ask, not historical order books.
Production strategy and credentials are never modified.
"""
import bisect
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path

import morning_analysis as hist
import paper_event as original

OUT = hist.OUT
_snapshot = OUT / 'settings_snapshot.json'
CFG = json.loads(_snapshot.read_text(encoding='utf-8')) if _snapshot.exists() else None


class Tape:
    def __init__(self):
        self.rows, self.times = {}, {}
        for f in (OUT / 'seconds').glob('*.json'):
            rows = json.loads(f.read_text(encoding='utf-8'))
            self.rows[f.stem] = rows
            self.times[f.stem] = [hist.bot.utc_ts(r['candle_date_time_utc']) for r in rows]

    def quote(self, market, now):
        times = self.times.get(market, [])
        idx = bisect.bisect_right(times, now - 1) - 1
        if idx < 0:
            return None
        return float(self.rows[market][idx]['trade_price'])

    def high(self, market, start, now):
        times = self.times.get(market, [])
        lo, hi = bisect.bisect_left(times, start), bisect.bisect_right(times, now - 1)
        return max((float(r['high_price']) for r in self.rows[market][lo:hi]), default=0)


class Replay(original.Session):
    def __init__(self, minutes, uni, tape, spread=0, diagnostics=True, config=None):
        source = config if config is not None else CFG
        if source is None:
            raise FileNotFoundError('Historical replay requires settings_snapshot.json; unit tests must supply an explicit fixture config.')
        self.cfg = dict(source)
        self.minute_data = {m: minutes[m] for m in uni}
        self.universe, self.tape, self.spread = uni, tape, spread
        self.loose, self.now = False, hist.T0
        self.slot = 250000
        self.events = []
        self.ports = [original.Port('S1', 'fixed'), original.Port('S4', 'auto', 30, .02, (.03, .01)),
                      original.Port('S6', 'auto', 59, .02, (.03, .01))]
        if diagnostics:
            self.ports += [original.Port('S4_current_exit', 'auto', 30),
                           original.Port('S4_without_stop', 'auto', 30, None, (.03, .01)),
                           original.Port('S4_chase_gate', 'auto', 30, .02, (.03, .01)),
                           original.Port('S4_no_same_coin_reentry', 'auto', 30, .02, (.03, .01))]
        for p in self.ports:
            p.diagnostics = []
            p.used = set()

    def ev(self, kind, **kw):
        self.events.append(dict(kind=kind, t=hist.stamp(self.now), **kw))

    def quotes(self, markets):
        q = {}
        for market in markets:
            price = self.tape.quote(market, self.now)
            if price:
                q[market] = (price * (1 - self.spread / 2), price * (1 + self.spread / 2))
        return q

    def open_pos(self, p, market, ask, bid, score, extra):
        k = int((self.now - hist.T0) // 60)
        previous = original.fill_range(self.minute_data[market], -10, k - 1)[-1]
        gap = ask / previous[3] - 1 if previous else None
        observed = self.tape.quote(market,self.now) if self.tape is not None else ask
        signal_gap = observed/previous[3]-1 if previous else None
        if p.name == 'S4_chase_gate' and signal_gap is not None and signal_gap > self.cfg['chase_gap']:
            p.diagnostics.append({'market': market, 't': hist.stamp(self.now), 'skip': 'chase', 'gap': gap})
            return
        if p.name == 'S4_no_same_coin_reentry' and market in p.used:
            return
        p.used.add(market)
        initial_peak = ask if (p.name == 'S4_current_exit' or p.mode == 'event') else bid
        p.pos[market] = {'entry': ask, 'peak': initial_peak, 't': self.now, 'score': score, 'extra': extra,
                         'gap': gap, 'remaining': 1.0, 'net': 0.0, 'weighted_exit': 0.0,
                         'part_done': False, 'exit_events': []}
        self.ev('buy', port=p.name, market=market, price=ask, gap=gap, **extra)

    def close_part(self, p, market, bid, reason, fraction):
        pos = p.pos[market]
        fraction = min(fraction, pos['remaining'])
        if fraction <= 0:
            return
        net = self.slot * fraction * ((bid * (1 - original.FEE)) / (pos['entry'] * (1 + original.FEE)) - 1)
        pos['net'] += net
        pos['weighted_exit'] += fraction * bid
        pos['remaining'] -= fraction
        pos['exit_events'].append({'time': hist.stamp(self.now), 'price': bid, 'fraction': fraction, 'reason': reason})
        if pos['remaining'] > 1e-9:
            return
        row = {'market': market, 'in': hist.stamp(pos['t']), 'out': hist.stamp(self.now),
               'entry': pos['entry'], 'exit': pos['weighted_exit'], 'net_krw': round(pos['net']),
               'gross_pct': (pos['weighted_exit'] / pos['entry'] - 1) * 100,
               'peak_pct': (pos['peak'] / pos['entry'] - 1) * 100,
               'why': reason, 'age_minutes': (self.now - pos['t']) / 60,
               'gap_pct': pos['gap'] * 100 if pos['gap'] is not None else None,
               'score': pos['score'], **pos['extra'], 'exit_events': pos['exit_events']}
        p.trades.append(row)
        del p.pos[market]

    def exits(self):
        exit_cfg = getattr(self, 'exit_cfg', self.cfg)
        for p in self.ports:
            interval = (3 if self.now < hist.T0 + 600 else 2) if p.mode == 'event' else (2 if p.name == 'S4_current_exit' else 5)
            if int(self.now - hist.T0) % interval:
                continue
            for market in list(p.pos):
                q = self.quotes([market]).get(market)
                if not q:
                    continue
                bid = q[0]
                pos = p.pos[market]
                peak = max(pos['peak'], bid)
                decision_price = bid
                if p.name == 'S4_current_exit' or p.mode == 'event':
                    decision_price = self.tape.quote(market,self.now)
                    peak = max(peak,decision_price,self.tape.high(market, pos['t'], self.now))
                pos['peak'] = peak
                pnl = decision_price / pos['entry'] - 1
                age = (self.now - pos['t']) / 60
                reason = None
                if self.now >= hist.END:
                    reason = 'window_end'
                elif p.name == 'S4_current_exit' or p.mode == 'event':
                    armed = pos['part_done'] or peak >= pos['entry'] * (1 + exit_cfg['part_on'])
                    if armed and decision_price <= pos['entry'] * (1 + exit_cfg['be_lock']):
                        reason = 'break_even'
                    elif pnl <= -exit_cfg['stop']:
                        reason = 'stop'
                    elif pnl >= exit_cfg['take']:
                        reason = 'take'
                    elif peak >= pos['entry'] * (1 + exit_cfg['trail_on']) and decision_price <= peak * (1 - exit_cfg['trail_gap']):
                        reason = 'trail'
                    elif exit_cfg['dead_on'] and not pos['part_done'] and age >= exit_cfg['dead_min'] and peak < pos['entry'] * (1 + exit_cfg['dead_gain']):
                        reason = 'no_progress'
                    elif age >= exit_cfg['max_minutes']:
                        reason = 'time_limit'
                    if not reason and not pos['part_done'] and pnl >= exit_cfg['part_on']:
                        self.close_part(p, market, bid, 'partial', exit_cfg['part_ratio'])
                        if market not in p.pos:
                            continue
                        p.pos[market]['part_done'] = True
                elif p.stop and pnl <= -p.stop:
                    reason = 'stop'
                elif p.trail and peak >= pos['entry'] * (1 + p.trail[0]) and bid <= peak * (1 - p.trail[1]):
                    reason = 'trail'
                if reason:
                    self.close_part(p, market, bid, reason, pos['remaining'])

    def run(self):
        for second in range(0, 3601):
            self.now = hist.T0 + second
            self.exits()
            k = second // 60
            if second % 60 != 2 or k >= 60:
                continue
            visible = {m: {idx: bar for idx, bar in got.items() if idx < k} for m, got in self.minute_data.items()}
            # No later candle is passed to either original signal method.
            assert all(idx < k for got in visible.values() for idx in got)
            if k == 0:
                self.enter_fixed(visible)
            for p in self.ports:
                if p.mode == 'auto' and 2 <= k <= p.win_end and len(p.pos) < original.MAXPOS:
                    self.enter_auto(p, visible, k)
        result = {}
        for p in self.ports:
            assert not p.pos, (p.name, 'unclosed position')
            result[p.name] = {'trades': len(p.trades), 'wins': sum(t['net_krw'] > 0 for t in p.trades),
                              'net_krw': sum(t['net_krw'] for t in p.trades), 'rows': p.trades,
                              'skipped': p.diagnostics}
        return result


def market_outcomes(data):
    rows = []
    for market, got in data.items():
        previous = original.fill_range(got, -20, -11)
        morning = original.fill_range(got, 0, 59)
        if any(b is None for b in previous) or any(b is None for b in morning):
            continue
        rows.append({'market': market, 'price_0850_proxy': previous[-1][3], 'open_0900': morning[0][0],
                     'close_0959': morning[-1][3],
                     'return_0900_1000_pct': (morning[-1][3] / morning[0][0] - 1) * 100,
                     'max_rise_from_0900_pct': (max(b[1] for b in morning) / morning[0][0] - 1) * 100,
                     'max_drop_from_0900_pct': (min(b[2] for b in morning) / morning[0][0] - 1) * 100,
                     'turnover_0900_1000': sum(b[4] for b in morning)})
    return sorted(rows, key=lambda r: r['return_0900_1000_pct'], reverse=True)


class EventReplay(Replay):
    """Current 09:00 event branch, conditional on disabling paper-day real-buy block.

    Snapshot universe is all currently allowed KRW markets, as in event_scan (not paper's frozen pool).
    Historical orderbook/latency are synthetic; normal 5m entries after event window are excluded.
    """
    def __init__(self, data, tape, spread=0, strict_cap=None, chase=False,
                 allow_rebuy=True, buy_cap=True, exit_overrides=None):
        markets = json.loads((OUT / 'event_candidate_markets.json').read_text(encoding='utf-8'))
        super().__init__(data, markets, tape, spread, False)
        # Freeze the historical baseline even when production settings are changed later.
        self.cfg = json.loads((OUT / 'effective_config_snapshot.json').read_text(encoding='utf-8'))
        self.exit_cfg = dict(CFG, **(exit_overrides or {}))
        self.prior = json.loads((OUT / 'event_prior_turnover.json').read_text(encoding='utf-8'))
        self.ports = [original.Port('CURRENT_EVENT_COUNTERFACTUAL', 'event')]
        self.ports[0].used, self.ports[0].diagnostics = set(), []
        self.bought, self.rebought, self.rotations = [], [], 0
        self.failures, self.ambiguous_turnover = Counter(), set()
        self.first_pass, self.cap_skips = {}, []
        self.strict_cap, self.chase = strict_cap, chase
        self.allow_rebuy, self.buy_cap = allow_rebuy, buy_cap
        per = self.cfg['budget'] * self.cfg['ev_ratio'] * .995 / self.cfg['ev_count']
        self.slot = round(per * .998) * (1 + original.FEE)

    def stats(self, market):
        times = self.tape.times.get(market, [])
        end = bisect.bisect_right(times, self.now - 1)
        if not end or self.now - times[end - 1] > 8:
            return None
        rows = list(zip(times[max(0, end-60):end], self.tape.rows[market][max(0, end-60):end]))
        last = float(rows[-1][1]['trade_price'])
        def at(age):
            return next((float(r['trade_price']) for t, r in reversed(rows) if t <= self.now-age),
                        float(rows[0][1]['opening_price']))
        turn10 = sum(float(r['candle_acc_trade_price']) for t, r in rows if t > self.now-10)
        prev = sum(float(r['candle_acc_trade_price']) for t, r in rows if self.now-30 < t <= self.now-10) / 2
        high = max((float(r['high_price']) for t, r in rows if t > self.now-30), default=last)
        return {'last': last, 'r10': last/at(10)-1, 'r30': last/at(30)-1,
                'active': sum(t > self.now-10 for t, r in rows), 't10': turn10,
                'accel': turn10/max(prev, 1000000), 'fade': last/high-1}

    def stage_one(self, market):
        got = self.minute_data[market]
        if 0 not in got:
            return None
        times = self.tape.times[market]
        first = bisect.bisect_left(times, hist.T0)
        end = bisect.bisect_right(times, self.now - 1)
        if first >= end:
            return None
        sec = self.stats(market)
        if not sec:
            return None
        k = int((self.now - hist.T0) // 60)
        current_start = hist.T0 + k * 60
        begin = bisect.bisect_left(times, current_start)
        current = self.tape.rows[market][begin:end]
        turnover = sum(b[4] for idx, b in got.items() if 0 <= idx < k)
        turnover += sum(float(r['candle_acc_trade_price']) for r in current)
        rise = sec['last'] / got[0][0] - 1
        if not self.cfg['ev_rise'] <= rise <= self.cfg['ev_max_rise'] or turnover < self.cfg['ev_open_krw']:
            return None
        prior = self.prior[market]
        upper = prior['turnover_24h_at_0900'] + turnover
        lower = upper - prior['prior_day_first10m']
        if lower < self.cfg['turnover']/5 <= upper:
            self.ambiguous_turnover.add(market)
            return None  # conservatively exclude an ambiguous rolling-24h turnover boundary
        if upper < self.cfg['turnover']/5:
            return None
        latest_minute = int((times[end-1] - hist.T0) // 60)
        minute_open = got[latest_minute][0] if latest_minute in got else sec['last']
        return {'market': market, 'rise': rise, 'turnover': turnover, 'sec': sec, 'minute_open': minute_open}

    def picks(self):
        stage = [s for m in self.universe if (s := self.stage_one(m))]
        stage.sort(key=lambda x: x['rise'], reverse=True)
        picks = []
        for item in stage[:10]:
            s = item['sec']
            failed = []
            tests = [('negative_r10', s['r10'] >= 0), ('weak_r30', s['r30'] >= .003),
                     ('sparse_trading', s['active'] >= 3), ('low_10s_turnover', s['t10'] >= self.cfg['ev_min_krw']),
                     ('low_acceleration', s['accel'] >= self.cfg['ev_vol']),
                     ('fading', s['fade'] > -self.cfg['ev_fade']),
                     ('red_minute', s['last'] >= item['minute_open']),
                     ('wide_spread', self.spread <= self.cfg['max_spread'] * 2)]
            if self.strict_cap is not None:
                tests.append(('research_rise_cap', item['rise'] <= self.strict_cap))
            for name, passed in tests:
                if not passed:
                    failed.append(name)
                    self.failures[name] += 1
            if not failed:
                item['score'] = item['rise']*100 + s['r30']*200 + min(s['accel'],10)*.5
                self.first_pass.setdefault(item['market'], hist.stamp(self.now))
                picks.append(item)
        return sorted(picks, key=lambda x:x['score'], reverse=True)

    def run(self):
        p = self.ports[0]
        for second in range(3601):
            self.now = hist.T0 + second
            self.exits()
            if second >= self.cfg['ev_window'] * 60 or second % 3:
                continue
            if sum(t['net_krw'] for t in p.trades) <= -self.cfg['daily_loss']:
                continue
            picks = self.picks()
            rotated = False
            for rank, item in enumerate(picks):
                market = item['market']
                if market in p.pos:
                    continue
                if not self.allow_rebuy and market in self.bought:
                    continue
                rebuy = market in self.bought and market not in self.rebought and rank < 3
                if market in self.bought and not rebuy:
                    continue
                if len(p.pos) < int(self.cfg['ev_count']):
                    if self.buy_cap and len(self.bought) >= int(self.cfg['ev_count'] + self.cfg['ev_rot_max']):
                        self.cap_skips.append({'time':hist.stamp(self.now),'market':market})
                        break
                elif self.cfg['ev_rot'] and self.rotations < self.cfg['ev_rot_max'] and not rotated:
                    rotated = True
                    weakest = None
                    for m, pos in p.pos.items():
                        sec = self.stats(m)
                        if self.now-pos['t'] < self.cfg['ev_hold'] or not sec or sec['last']/pos['entry']-1 >= self.cfg['ev_rot_gain']:
                            continue
                        opening = self.minute_data[m][0][0]
                        score = (sec['last']/opening-1)*100 + sec['r30']*200 + min(sec['accel'],10)*.5
                        if weakest is None or score < weakest[0]:
                            weakest = (score,m)
                    if not weakest or item['score']-weakest[0] < self.cfg['ev_rot_margin']:
                        continue
                    m = weakest[1]
                    self.close_part(p,m,self.quotes([m])[m][0],'event_rotate',p.pos[m]['remaining'])
                    self.rotations += 1
                else:
                    continue
                bid, ask = self.quotes([market])[market]
                previous = original.fill_range(self.minute_data[market],-10,int(second//60)-1)[-1]
                if self.chase and previous and item['sec']['last']/previous[3]-1 > self.cfg['chase_gap']:
                    self.failures['research_chase_gate'] += 1
                    continue
                if rebuy:
                    self.rebought.append(market)
                self.open_pos(p,market,ask,bid,item['score'],{'rise_pct':item['rise']*100,
                              'accel':item['sec']['accel'],'r30_pct':item['sec']['r30']*100,
                              'r10_pct':item['sec']['r10']*100,'t10_krw':item['sec']['t10']})
                self.bought.append(market)
        assert not p.pos
        return {'trades':len(p.trades),'wins':sum(t['net_krw']>0 for t in p.trades),
                'net_krw':sum(t['net_krw'] for t in p.trades),'rows':p.trades,
                'rotations':self.rotations,'slot_krw':self.slot,'filter_failures':dict(self.failures),
                'first_pass':self.first_pass,'buy_cap_skips':self.cap_skips,
                'ambiguous_24h_turnover':sorted(self.ambiguous_turnover)}


def run_event():
    data,tape=hist.load_minutes(),Tape()
    results={}
    for spread in (0,.003,.006,.012):
        results[str(round(spread*10000))+'bp']=EventReplay(data,tape,spread).run()
    # These are diagnostic ablations on this single morning, not selected/validated strategy settings.
    results['diagnostic_15pct_cap_30bp']=EventReplay(data,tape,.003,strict_cap=.15).run()
    results['diagnostic_chase_gate_30bp']=EventReplay(data,tape,.003,chase=True).run()
    for bp in (0,30,60,120):
        results['diagnostic_no_rebuy_'+str(bp)+'bp']=EventReplay(data,tape,bp/10000,allow_rebuy=False).run()
    results['diagnostic_no_buy_cap_30bp']=EventReplay(data,tape,.003,buy_cap=False).run()
    results['diagnostic_no_partial_30bp']=EventReplay(data,tape,.003,exit_overrides={'part_ratio':0}).run()
    results['diagnostic_no_stop_30bp']=EventReplay(data,tape,.003,exit_overrides={'stop':.99}).run()
    hist.dump(OUT/'current_event_replay.json',results)
    for name,r in results.items():
        print('CURRENT_EVENT',name,r['trades'],r['net_krw'],'ambiguous',r['ambiguous_24h_turnover'])


def run_all():
    data, tape = hist.load_minutes(), Tape()
    meta = json.loads((OUT / 'universe.json').read_text(encoding='utf-8'))
    scenarios = {}
    for label, uni in [('frozen_0850', meta['at_0850']), ('legacy_0859', meta['legacy_0859'])]:
        for spread in (0, .003, .006, .012):
            key = label + '_spread_' + str(int(round(spread * 10000))) + 'bp'
            scenarios[key] = Replay(data, uni, tape, spread, diagnostics=True).run()
            print(key, {p: (r['trades'], r['net_krw']) for p, r in scenarios[key].items()}, flush=True)
    hist.dump(OUT / 'replay_results.json', scenarios)
    hist.dump(OUT / 'market_outcomes.json', market_outcomes(data))
    fields = ['scenario', 'strategy', 'market', 'in', 'out', 'entry', 'exit', 'gross_pct', 'net_krw',
              'why', 'age_minutes', 'peak_pct', 'gap_pct', 'score']
    with (OUT / 'replay_trades.csv').open('w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for scenario, portfolios in scenarios.items():
            for name, result in portfolios.items():
                for t in result['rows']:
                    writer.writerow(dict(scenario=scenario, strategy=name, **t))
    baseline = OUT / 'production_baseline'
    sources = {f: hashlib.sha256(((baseline if (baseline/f).exists() else hist.ROOT) / f).read_bytes()).hexdigest()
               for f in ('paper_event.py', 'upbit_bot.py', 'settings.json')}
    hist.dump(OUT / 'methodology.json', {'date': hist.DAY, 'selection_time': hist.stamp(hist.START),
        'entry_start': hist.stamp(hist.T0), 'end': hist.stamp(hist.END),
        'fee_per_side': original.FEE, 'slot_krw': 250000, 'source_sha256': sources,
        'synthetic_roundtrip_spreads': [0, .003, .006, .012],
        'entry_observation': 'Only completed candles before decision. Paper signals reused from original file.',
        'execution': 'Last completed 1-second trade close with synthetic half-spread each side; no historical orderbook.',
        'current_exit': 'Diagnostic: S4 entries with current exits and 2-second checks, without live API execution latency.',
        'not_replayed': ['Full live bot regular 5m/15m entry scan', 'Live event 1s entry ranking and rotation',
                         'Historical orderbook or limit-order queue fills', 'Other bots or manual orders'],
        'paper_day': json.loads((hist.ROOT / 'paper_day.json').read_text(encoding='utf-8'))})


if __name__ == '__main__':
    import sys
    if '--event' in sys.argv:
        run_event()
    else:
        run_all()
