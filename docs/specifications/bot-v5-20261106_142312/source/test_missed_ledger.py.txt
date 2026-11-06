import json
import tempfile
import unittest
from pathlib import Path

import missed_ledger as ml
import strategy as st


class MissedLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.p = st.Params(entry_scan=7, entry_want=4)
        self.now = 1_000_000.0

    def test_all_reasons_are_kept_not_just_the_first(self):
        r = {'market': 'KRW-A', 'rank': 9, 'rs': .18, 'close': 120., 'atr': 1., 'bar': '2026-11-05T12:45:00+09:00'}
        last = {'time': self.now - 600, 'price': 125., 'profit': False}       # 손실 청산 10분 전 → 재진입 대기
        b = ml.blocks_for(r, self.p, set(), set(), {}, last, set(), True, 7, self.now)
        for want in ('순위 7위 밖', '매수 불가 경보', '장세 필터·매수 중단', '과열(상한 15%)', '재진입 대기'):
            self.assertIn(want, b)

    def test_outcomes_and_file(self):
        ranked = [{'market': 'KRW-A', 'rank': 1, 'rs': .05, 'close': 1.}, {'market': 'KRW-B', 'rank': 2, 'rs': .05, 'close': 1.},
                  {'market': 'KRW-C', 'rank': 3, 'rs': .30, 'close': 1.}]
        rows = ml.record(self.dir, 123, ranked, self.p, {}, set(), {}, {}, {'KRW-A', 'KRW-B', 'KRW-C'},
                         ['장세 필터 통과: 시장 균등지수 +1.00% > 0'], '실행 주문안: 매도 없음; 매수 A 79000원', now=self.now)
        self.assertEqual([r['outcome'] for r in rows], ['매수', '후보였으나 미매수(자리·현금·후보 수)', '차단'])
        self.assertEqual(rows[2]['blocks'], ['과열(상한 15%)'])
        saved = [json.loads(x) for x in (self.dir / ml.FILE).read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(saved), 3)
        self.assertEqual(saved[0]['slot'], 123)

    def test_regime_pass_message_is_not_a_block(self):
        rows = ml.record(self.dir, 1, [{'market': 'KRW-A', 'rank': 1, 'rs': .05}], self.p, {}, set(), {}, {}, {'KRW-A'},
                         ['장세 필터 통과: 시장 균등지수 +1.00% > 0'], '실행 주문안: 매도 없음; 매수 없음', now=self.now)
        self.assertEqual(rows[0]['blocks'], [])
        rows = ml.record(self.dir, 1, [{'market': 'KRW-A', 'rank': 1, 'rs': .05}], self.p, {}, set(), {}, {}, {'KRW-A'},
                         ['장세 필터: 시장 균등지수 24시간 -1.00% ≤ 0 → 신규 매수 보류'], '실행 주문안: 매도 없음; 매수 없음', now=self.now)
        self.assertEqual(rows[0]['blocks'], ['장세 필터·매수 중단'])


if __name__ == '__main__':
    unittest.main()
