import unittest

import adaptive_model as model
from validate_regimes import replay


class ReplayTests(unittest.TestCase):
    def prepared(self):
        t=1704067200
        signal={'ok':True,'atr':1000,'close':100000,'breakout':99900}
        rows=[]
        for i in range(3):
            bars={m:{'o':100000,'h':100500,'l':99500,'c':100100} for m in ('KRW-BTC','KRW-ETH')}
            rows.append({'t':t+i*3600,'bars':bars,'policy':model.policy('rise','test',t+i*3600),
                         'baseline':{'KRW-BTC':signal,'KRW-ETH':{'ok':False}}})
        return rows

    def test_unknown_future_slippage_does_not_filter_out_stress_trades(self):
        rows=self.prepared()
        normal,_,_=replay(rows,'baseline',.001,rows[0]['t'],rows[-1]['t']+3600)
        stress,_,_=replay(rows,'baseline',.004,rows[0]['t'],rows[-1]['t']+3600)
        self.assertEqual(normal['completed_positions'],1)
        self.assertEqual(stress['completed_positions'],1)
        self.assertLess(stress['net_krw'],normal['net_krw'])

    def test_gap_stop_fills_at_worse_open(self):
        rows=self.prepared()
        rows[1]['bars']['KRW-BTC'].update(o=90000,h=90500,l=89500,c=90000)
        _,trades,_=replay(rows,'baseline',.001,rows[0]['t'],rows[-1]['t']+3600)
        self.assertEqual(trades[0]['reason'],'gap below stop')
        self.assertLess(trades[0]['net'],-15000)


if __name__=='__main__':
    unittest.main()
