import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import regime_review as rv

KST = dt.timezone(dt.timedelta(hours=9))
S = dt.datetime(2026, 11, 8, 2, 0, tzinfo=KST)


def iso(t):
    return t.isoformat(timespec='seconds')


def orders(trips):
    """trips: [(시작 시각, 수익률 %)] 각각 매수 100 → 매도 100*(1+r)."""
    o = {}
    for i, (t, pct) in enumerate(trips):
        m = f'KRW-X{i}'
        for k, (side, when, price) in enumerate((('bid', t, 100.0), ('ask', t + dt.timedelta(minutes=30), 100.0 * (1 + pct / 100)))):
            u = f'{i}-{k}'
            o[u] = {'uuid': u, 'market': m, 'side': side, 'state': 'done', 'created_at': iso(when), 'executed_volume': '10', 'paid_fee': '0',
                    'identifier': 'v5-' + u, 'trades': [{'market': m, 'uuid': u + 't', 'price': str(price), 'volume': '10', 'funds': str(10 * price),
                                                          'created_at': iso(when), 'side': side}]}
    p = Path(tempfile.mkdtemp()) / 'orders.json'
    p.write_text(json.dumps({'orders': o}), encoding='utf-8')
    return p


def watch(rows):
    p = Path(tempfile.mkdtemp()) / 'w.jsonl'
    p.write_text('\n'.join(json.dumps({'at': dt.datetime.fromisoformat(a).strftime('%Y-%m-%dT%H:%M') + '+09:00', 'index_24h': v}) for a, v in rows), encoding='utf-8')
    return p


class RegimeReview(unittest.TestCase):
    def test_groups_by_latest_index(self):
        w = watch([('2026-11-08T02:00', -0.01), ('2026-11-08T05:00', 0.02)])
        o = orders([(S + dt.timedelta(minutes=70), 1.0), (S + dt.timedelta(hours=4), -1.0)])
        r = rv.evaluate(o, w, since=iso(S), now=S + dt.timedelta(days=1))
        self.assertEqual((r['neg_n'], r['pos_n']), (1, 1))
        self.assertEqual(r['state'], 'collecting')
        self.assertIsNone(rv.notice_text(r))

    def test_missing_or_stale_index_is_unknown(self):
        w = watch([('2026-11-07T00:00', 0.01)])        # 6시간보다 오래된 기록
        r = rv.evaluate(orders([(S + dt.timedelta(hours=1), 1.0)]), w, since=iso(S), now=S + dt.timedelta(days=1))
        self.assertEqual((r['neg_n'], r['pos_n'], r['unknown']), (0, 0, 1))

    def _many(self, neg_pct, pos_pct, n=30):
        rows, trips = [], []
        for i in range(n):
            base = S + dt.timedelta(hours=i * 6)
            rows += [(base.strftime('%Y-%m-%dT%H:%M'), -0.01), ((base + dt.timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M'), 0.01)]
            trips += [(base + dt.timedelta(hours=1), neg_pct + (i % 3 - 1) * 0.2), (base + dt.timedelta(hours=4), pos_pct + (i % 3 - 1) * 0.2)]
        return orders(trips), watch(rows)

    def test_restore_when_negative_group_clearly_worse(self):
        o, w = self._many(-1.0, 0.2)
        r = rv.evaluate(o, w, since=iso(S), now=S + dt.timedelta(days=10))
        self.assertEqual(r['state'], 'restore')
        t = rv.notice_text(r)
        self.assertEqual(t[2], 'action'); self.assertIn('장세 필터 연구 결과 보고', t[1]); self.assertIn('승인한 뒤에만', t[1])

    def test_keep_off_when_negative_group_not_worse(self):
        o, w = self._many(0.3, -0.2)
        r = rv.evaluate(o, w, since=iso(S), now=S + dt.timedelta(days=10))
        self.assertEqual(r['state'], 'keep_off')

    def test_missing_files(self):
        r = rv.evaluate(Path(tempfile.mkdtemp()) / 'a.json', Path(tempfile.mkdtemp()) / 'b.jsonl', since=iso(S), now=S)
        self.assertEqual((r['neg_n'], r['state']), (0, 'collecting'))


if __name__ == '__main__':
    unittest.main()
