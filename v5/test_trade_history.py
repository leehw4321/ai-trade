import datetime as dt
import tempfile
import unittest
from pathlib import Path

import trade_history as th

KST = dt.timezone(dt.timedelta(hours=9))


def order(uuid, side, market='KRW-A', funds='30000', vol='6000', ident=None, minutes_ago=5, state='done'):
    t = dt.datetime.now(KST) - dt.timedelta(minutes=minutes_ago)
    return {'uuid': uuid, 'created_at': t.isoformat(), 'market': market, 'side': side, 'ord_type': 'market', 'state': state,
            'executed_volume': vol, 'executed_funds': funds, 'identifier': ident}


class History(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'data' / 'exchange_orders.jsonl'

    def test_saves_only_executed_and_dedupes(self):
        rows = [order('a', 'bid'), order('b', 'ask', vol='0'), order('c', 'ask', ident='v5-x')]
        self.assertEqual(th.sync(self.path, lambda: rows), 2)
        self.assertEqual(th.sync(self.path, lambda: rows), 0)            # 두 번째는 새 것이 없음
        self.assertEqual(th.sync(self.path, lambda: rows + [order('d', 'bid')]), 1)
        self.assertEqual(sorted(th.load(self.path)), ['a', 'c', 'd'])

    def test_source_classification(self):
        th.sync(self.path, lambda: [order('a', 'bid', ident='v5-123'), order('b', 'ask')])
        d = th.load(self.path)
        self.assertEqual((d['a']['source'], d['b']['source']), ('bot-v5', 'unknown'))

    def test_summary_lists_sells_and_old_rows_excluded(self):
        th.sync(self.path, lambda: [order('a', 'bid'), order('b', 'ask', market='KRW-B', funds='28500'),
                                    order('old', 'ask', market='KRW-C', minutes_ago=60 * 24 * 3)])
        text = th.summary(self.path, days=1)
        self.assertIn('KRW-B', text)
        self.assertNotIn('KRW-C', text)
        self.assertIn('출처 불명', text)


if __name__ == '__main__':
    unittest.main()
