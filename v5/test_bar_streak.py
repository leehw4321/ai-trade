"""순위표 봉별 연속 상승·하락 표시(사용자 지시 2026-11-27)."""
import unittest

import bar_streak as bs


def bars(closes, unit=1, start=1795740000):
    import datetime as dt
    return [{'t': dt.datetime.fromtimestamp(start + i * unit * 60, dt.timezone.utc).isoformat(), 'c': c} for i, c in enumerate(closes)]


class Streak(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(bs.streak([1, 2, 3, 4]), 3)
        self.assertEqual(bs.streak([5, 6, 5, 4, 3]), -3)
        self.assertEqual(bs.streak([1, 2, 2]), 0)
        self.assertEqual(bs.streak([3]), 0)

    def test_fetches_only_when_new_bar_closes_and_respects_budget(self):
        calls, now = [], [1795740000 + 10 * 60]
        def fetch(m, unit, count):
            calls.append((m, unit)); return bars([1, 2, 3], unit, now[0] - 3 * unit * 60)
        s = bs.Streaks(fetch=fetch, clock=lambda: now[0])
        out = s.get(['KRW-A'], budget=2)
        self.assertEqual(out['KRW-A'], {1: 2, 5: 2})          # 예산 2: 1분·5분만
        s.get(['KRW-A'], budget=10)
        n = len(calls); s.get(['KRW-A'], budget=10)
        self.assertEqual(len(calls), n)                         # 새 봉 마감 전에는 다시 조회하지 않는다
        now[0] += 65; s.get(['KRW-A'], budget=10)
        self.assertIn(('KRW-A', 1), calls[n:])                  # 1분 뒤 1분봉만 다시

    def test_fetch_error_keeps_page_working(self):
        s = bs.Streaks(fetch=lambda *a, **k: (_ for _ in ()).throw(OSError('x')), clock=lambda: 0)
        self.assertEqual(s.get(['KRW-A']), {'KRW-A': {}})
        self.assertEqual(bs.render({}), '')

    def test_render(self):
        h = bs.render({1: 3, 5: -2, 15: 0})
        self.assertIn('1분 <span class=up>▲3</span>', h)
        self.assertIn('5분 <span class=dn>▼2</span>', h)


if __name__ == '__main__':
    unittest.main()
