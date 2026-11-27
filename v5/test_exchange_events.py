"""실행권이 다른 PC에 있을 때 '실거래 기록'을 업비트 체결 기록으로 보여 주는 bot.exchange_events (사용자 지시 2026-11-27)."""
import json
import tempfile
import unittest
from pathlib import Path

import bot


class ExchangeEvents(unittest.TestCase):
    def test_bot_fills_newest_first_and_skips_manual_and_unfilled(self):
        rows = [
            {'identifier': 'v5-a', 'created_at': '2026-11-27T10:46:37+09:00', 'market': 'KRW-BIGTIME', 'side': 'bid', 'executed_funds': '57999.99'},
            {'identifier': 'v5-b', 'created_at': '2026-11-27T11:22:12+09:00', 'market': 'KRW-BIGTIME', 'side': 'ask', 'executed_funds': '43908.73'},
            {'identifier': None, 'created_at': '2026-11-27T11:30:00+09:00', 'market': 'KRW-DOGE', 'side': 'bid', 'executed_funds': '100000'},
            {'identifier': 'v5-c', 'created_at': '2026-11-27T11:31:00+09:00', 'market': 'KRW-XRP', 'side': 'bid', 'executed_funds': '0'},
        ]
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'exchange_orders.jsonl'
            f.write_text('\n'.join(json.dumps(r) for r in rows) + '\nnot json\n', encoding='utf-8')
            out = bot.exchange_events(f)
        self.assertEqual(out, [('2026-11-27T11:22:12+09:00', '매도 BIGTIME 43,909원'),
                               ('2026-11-27T10:46:37+09:00', '매수 BIGTIME 58,000원')])

    def test_missing_file_is_empty(self):
        self.assertEqual(bot.exchange_events(Path(tempfile.gettempdir()) / 'nope_exchange_orders.jsonl'), [])


if __name__ == '__main__':
    unittest.main()
