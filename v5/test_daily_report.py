import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import daily_equity
import daily_report as dr

KST = dt.timezone(dt.timedelta(hours=9))


def order(uid, market, side, vol, funds, t, fee=0, mine=True):
    return uid, {'market': market, 'side': side, 'identifier': ('v5-' if mine else 'x-') + uid, 'paid_fee': str(fee),
                 'trades': [{'volume': str(vol), 'funds': str(funds), 'created_at': t}]}


def make(rows):
    p = Path(tempfile.mkdtemp())
    (p / 'o.json').write_text(json.dumps({'orders': dict(rows)}), encoding='utf-8')
    return p


class Report(unittest.TestCase):
    def test_sells_by_day_uses_average_cost_and_fees(self):
        p = make([order('b1', 'KRW-AAA', 'bid', 2, 2000, '2026-11-05T10:00:00+09:00', fee=1),
                  order('s1', 'KRW-AAA', 'ask', 1, 1100, '2026-11-06T09:00:00+09:00', fee=1),
                  order('s2', 'KRW-AAA', 'ask', 1, 900, '2026-11-06T10:00:00+09:00')])
        r = dr.sells_by_day(p / 'o.json')
        self.assertEqual([(d, c) for d, c, _, _ in r], [('2026-11-06', 'AAA'), ('2026-11-06', 'AAA')])
        self.assertAlmostEqual(r[0][2], 1100 - 1 - 1000.5)
        self.assertAlmostEqual(r[1][2], 900 - 1000.5)

    def test_build_splits_v5_and_manual_and_payoff(self):
        p = make([order('b1', 'KRW-AAA', 'bid', 1, 1000, '2026-11-06T01:00:00+09:00'),
                  order('s1', 'KRW-AAA', 'ask', 1, 1300, '2026-11-06T02:00:00+09:00'),
                  order('b2', 'KRW-BBB', 'bid', 1, 1000, '2026-11-06T03:00:00+09:00'),
                  order('s2', 'KRW-BBB', 'ask', 1, 900, '2026-11-06T04:00:00+09:00'),
                  order('b3', 'KRW-CCC', 'bid', 1, 1000, '2026-11-06T03:00:00+09:00', mine=False),
                  order('s3', 'KRW-CCC', 'ask', 1, 500, '2026-11-06T04:00:00+09:00', mine=False)])
        eq = p / 'e.json'
        daily_equity.record(eq, 1_000_000, dt.datetime(2026, 11, 6, 1, tzinfo=KST))
        d = dr.build('2026-11-06', eq, p / 'o.json')
        self.assertEqual((d['v5']['sells'], d['v5']['wins'], d['v5']['losses'], d['v5']['net']), (2, 1, 1, 200))
        self.assertEqual(d['all']['sells'], 3)
        self.assertEqual(d['payoff'], 3.0)
        self.assertEqual(d['worst'][0][0], 'BBB')
        self.assertIn('총자산', dr.summary_line(d))

    def test_save_recent_and_section_escape(self):
        p = make([])
        for day in ('2026-11-05', '2026-11-06'):
            doc = dr.build(day, p / 'e.json', p / 'o.json', {'halted': '<x>'}, {'state': 'collecting', 'records': 1, 'hit': 0, 'keep': 0}, day == '2026-11-05')
            dr.save(p / 'reports', doc)
        docs = dr.recent(p / 'reports')
        self.assertEqual([d['day'] for d in docs], ['2026-11-06', '2026-11-05'])
        h = dr.section(docs)
        self.assertIn('&lt;x&gt;', h)
        self.assertIn('잠정', h)
        self.assertEqual(dr.section([]), '')

    def test_missing_orders_file_is_empty(self):
        self.assertEqual(dr.sells_by_day('/nonexistent/o.json'), [])


if __name__ == '__main__':
    unittest.main()
