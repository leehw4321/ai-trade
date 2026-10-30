import copy
import unittest
from unittest.mock import patch

import adaptive_model as m


def fixture():
    now=1800000000//14400*14400+100
    four=[{'t':now-100-(600-i)*14400,'o':100+i*.1,'c':100+i*.1,
           'h':101+i*.1,'l':99+i*.1,'v':1} for i in range(600)]
    hour=[{'t':now-100-(200-i)*3600,'o':100,'c':100,'h':101,'l':99,'v':1} for i in range(200)]
    return hour,four,now


class ModelTests(unittest.TestCase):
    def test_adx_strong_up_and_down_is_strength_not_direction(self):
        h,f,now=fixture()
        up=m.indicators(f)
        down=m.indicators([{**r,'o':300-r['o'],'c':300-r['c'],'h':300-r['l'],'l':300-r['h']} for r in f])
        self.assertGreater(up['adx'],99)
        self.assertGreater(down['adx'],99)
        self.assertTrue(m.bullish(up))
        self.assertFalse(m.bullish(down))
        self.assertTrue(m.bearish(down))

    def test_steady_confirmed_uptrend(self):
        h,f,now=fixture()
        self.assertEqual(m.classify(h,f,now)['phase'],'rise')

    def test_confirmed_downtrend(self):
        h,f,now=fixture()
        f=[{**r,'o':300-r['o'],'c':300-r['c'],'h':300-r['l'],'l':300-r['h']} for r in f]
        self.assertEqual(m.classify(h,f,now)['phase'],'fall')

    def test_flat_market_no_nan(self):
        h,f,now=fixture()
        f=[{**r,'o':100,'c':100,'h':100,'l':100} for r in f]
        result=m.classify(h,f,now)
        self.assertEqual(result['phase'],'range')
        self.assertEqual(result['adx'],0)

    def test_crash_override_does_not_wait_for_adx(self):
        h,f,now=fixture()
        h[-1]['c']=95
        result=m.classify(h,f,now)
        self.assertEqual(result['phase'],'crash')
        self.assertTrue(result['liquidate'])
        self.assertFalse(result['allow_entry'])

    def test_surge_blocks_chasing_and_tightens_trail(self):
        h,f,now=fixture()
        h[-1]['c']=110
        result=m.classify(h,f,now)
        self.assertEqual(result['phase'],'surge')
        self.assertFalse(result['allow_entry'])
        self.assertEqual(result['trail_atr'],2)

    def test_future_or_stale_bars_cannot_enter(self):
        h,f,now=fixture()
        self.assertFalse(m.classify(h,f,now+15000)['available'])
        h[-1]['t']=now
        self.assertFalse(m.classify(h,f,now)['available'])

    def test_recent_gap_rejected(self):
        h,f,now=fixture()
        del h[-4]
        self.assertFalse(m.classify(h,f,now)['available'])

    def test_two_closed_four_hour_confirmations_required(self):
        h,f,now=fixture()
        current=m.indicators(f)
        previous={**current,'adx':10}
        self.assertEqual(m.classify(h,f,now,features=(current,previous))['phase'],'range')

    def test_range_candidate_never_enabled_by_default(self):
        h,f,now=fixture()
        p=m.policy('range','test',now)
        result=m.signal(h,f,p,{'ok':True,'atr':1,'close':100,'breakout':99})
        self.assertFalse(result['ok'])

    def test_uptrend_reuses_exact_base_entry(self):
        h,f,now=fixture()
        base={'ok':False,'atr':1,'reason':'low volume'}
        result=m.signal(h,f,m.policy('rise','test',now),base)
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'],'low volume')

    def test_cached_features_match_direct_range_candidate_calculation(self):
        h,f,now=fixture()
        base={'ok':False,'atr':1,'close':100,'reason':'test'}
        p=m.policy('range','test',now)
        self.assertEqual(m.signal(h,f,p,base,allow_range=True),
                         m.signal(h,f,p,base,allow_range=True,asset_features=m.indicators(f)))

    def test_prefix_invariance_future_changes_do_not_affect_current(self):
        h,f,now=fixture()
        expected=m.classify(h,f,now)
        future_h=h+[dict(h[-1],t=now+3600,c=999999)]
        future_f=f+[dict(f[-1],t=now+14400,c=1)]
        closed_h=[r for r in future_h if r['t']+3600<=now]
        closed_f=[r for r in future_f if r['t']+14400<=now]
        self.assertEqual(m.classify(closed_h,closed_f,now),expected)


if __name__=='__main__':
    unittest.main()
