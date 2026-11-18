"""2026-11-18: 파일 교체 PermissionError로 account_sync가 죽고 live.json 저장이 밀리던 것, 체결 수집기 중복 방지."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fsutil
import trade_history

SRC = Path(__file__).resolve().parent


class ReplaceRetry(unittest.TestCase):
    def test_retries_then_succeeds(self):
        calls = []
        real = fsutil.os.replace

        def flaky(a, b):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError(5, 'denied')
            real(a, b)
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / 'a.tmp', Path(d) / 'b'
            a.write_text('x')
            with mock.patch.object(fsutil.os, 'replace', flaky), mock.patch.object(fsutil.time, 'sleep'):
                fsutil.replace_retry(a, b)
            self.assertEqual(b.read_text(), 'x')
        self.assertEqual(len(calls), 3)

    def test_gives_up_after_tries(self):
        with mock.patch.object(fsutil.os, 'replace', side_effect=PermissionError(5, 'denied')), \
                mock.patch.object(fsutil.time, 'sleep'):
            with self.assertRaises(PermissionError):
                fsutil.replace_retry('a', 'b', tries=3)

    def test_writers_use_retry(self):
        self.assertIn('replace_retry(tmp, self.file)', (SRC / 'live.py').read_text(encoding='utf-8'))
        s = (SRC / 'account_sync.py').read_text(encoding='utf-8')
        self.assertIn('replace_retry(temp, path)', s)
        self.assertIn("except OSError:\n                pass", s)   # 화면 쓰기 실패로 프로세스가 죽지 않음


class TradeHistorySingle(unittest.TestCase):
    def test_second_collector_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            first = trade_history.single_instance(Path(d))
            held = trade_history._LOCK
            try:
                self.assertTrue(first)
                trade_history._LOCK = None
                self.assertFalse(trade_history.single_instance(Path(d)))
            finally:
                held.close()


if __name__ == '__main__':
    unittest.main()
