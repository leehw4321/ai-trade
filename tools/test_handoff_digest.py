import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff_digest as hd

GOOD = '''# 한 장 인계
작성: Claude · 2026-11-04 09:00
## 1. 지금 상태
## 2. 바뀐 것
## 3. 하지 말 것
## 4. 다음 할 일
## 5. 더 읽을 곳
'''


class Digest(unittest.TestCase):
    def test_good_page_passes(self):
        self.assertEqual(hd.problems(GOOD), [])

    def test_missing_sections_header_and_secret_are_reported(self):
        bad = '# x\n## 1. 지금 상태\napi_key = ' + 'A' * 24 + '\n'
        found = ' '.join(hd.problems(bad))
        self.assertIn('필수 절 누락', found)
        self.assertIn('비밀정보', found)
        self.assertIn('작성', found)

    def test_too_long_is_reported(self):
        long_text = GOOD + '\n'.join(f'줄 {i}' for i in range(hd.MAX_LINES + 5))
        self.assertTrue(any('너무 김' in p for p in hd.problems(long_text)))

    def test_archive_keeps_previous_version_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            latest = root / 'LATEST.md'
            latest.write_text(GOOD, encoding='utf-8')
            folder = root / 'latest-archive'
            hd.archive(latest, folder, now=dt.datetime(2026, 11, 4, 9, 0, 0))
            latest.write_text(GOOD + 'v2\n', encoding='utf-8')
            hd.archive(latest, folder, now=dt.datetime(2026, 11, 4, 9, 5, 0))
            names = sorted(p.name for p in folder.iterdir())
            self.assertEqual(names, ['LATEST_2026-11-04_090000.md', 'LATEST_2026-11-04_090500.md'])
            self.assertEqual((folder / names[0]).read_text(encoding='utf-8'), GOOD)

    def test_check_reports_missing_file(self):
        self.assertEqual(hd.check(Path(tempfile.gettempdir()) / 'no_such_latest.md'), 1)


if __name__ == '__main__':
    unittest.main()
