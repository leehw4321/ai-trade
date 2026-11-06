import copy
import datetime as dt
import unittest

import gpt_swing_pilot as p


def bars(n=240, trend=0.):
    result=[]
    for i in range(n):
        c=100*(1+trend)**i
        result.append({'time':(dt.datetime(2020,1,1,tzinfo=dt.timezone.utc)+dt.timedelta(days=i)).isoformat(),
                       'o':c,'h':c*1.001,'l':c*.999,'c':c})
    return result


class SwingPilotTests(unittest.TestCase):
    def test_completed_close_executes_next_open_only(self):
        b=bars(203);b[200].update(c=200.,h=201.)
        b[201].update(o=200.,h=201.,l=99.,c=200.)
        s=p.simulate_one(b,'SMA65',200,100000.,0.)
        self.assertEqual(s['actions'][0]['time'],b[201]['time'])
        self.assertEqual(s['equity'][0],100000.)

    def test_future_prices_cannot_change_prior_equity(self):
        b=bars(trend=.01);altered=copy.deepcopy(b)
        altered[218].update(o=10.,h=20.,l=5.,c=15.)
        for model in p.MODELS:
            with self.subTest(model=model):
                a=p.simulate_one(b,model,200,100000.,.0005)
                c=p.simulate_one(altered,model,200,100000.,.0005)
                self.assertEqual(a['equity'][:18],c['equity'][:18])

    def test_flat_buy_hold_costs_money_not_profit(self):
        s=p.simulate_one(bars(),'HOLD',200,100000.,.0005)
        self.assertLess(s['equity'][-1],100000.)
        self.assertGreaterEqual(s['ending_cash'],0.)

    def test_zero_cost_flat_buy_hold_is_flat(self):
        s=p.simulate_one(bars(),'HOLD',200,100000.,0.,fee=0.)
        self.assertAlmostEqual(s['equity'][-1],100000.)

    def test_gap_stop_uses_worse_open_not_old_stop(self):
        b=bars(trend=.01)
        original=b[202]['o']
        b[202].update(o=original*.5,h=original*.51,l=original*.49,c=original*.5)
        s=p.simulate_one(b,'Donchian55_20_ATR',200,100000.,0.)
        exits=[x for x in s['actions'] if x['reason']=='gap_stop']
        self.assertTrue(exits)
        self.assertEqual(exits[0]['time'],b[202]['time'])
        self.assertLess(exits[0]['funds'],60000.)

    def test_no_leverage_or_negative_cash_in_crash(self):
        b=bars(trend=.004)
        for i in range(220,len(b)):
            b[i].update(o=10.,h=11.,l=9.,c=10.)
        for model in p.MODELS:
            with self.subTest(model=model):
                s=p.simulate_one(b,model,200,100000.,.002)
                self.assertGreaterEqual(s['ending_cash'],0.)
                self.assertTrue(all(0<=e<=1 for e in s['exposure']))
                self.assertTrue(all(e>0 for e in s['equity']))

    def test_minimum_order_is_not_ignored(self):
        s=p.simulate_one(bars(),'HOLD',200,4000.,.0005)
        self.assertEqual(s['actions'],[])
        self.assertEqual(s['equity'][-1],4000.)

    def test_drawdown_arithmetic(self):
        stats=p.statistics_for([110.,80.,90.],
            ['2020-01-01T00:00:00+00:00','2020-01-02T00:00:00+00:00','2020-01-03T00:00:00+00:00'],100.)
        self.assertAlmostEqual(stats['total_return_pct'],-10.)
        self.assertAlmostEqual(stats['maximum_daily_close_drawdown_pct'],(80/110-1)*100)


if __name__=='__main__':unittest.main()
