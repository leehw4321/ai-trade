"""실제 업비트 공개 API 응답으로 수집기의 가정을 확인한다(네트워크 필요, 불가하면 건너뜀)."""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_candles as cc
import pit_universe as pit

FMT = '%Y-%m-%dT%H:%M:%SZ'


def api(market, to):
    return cc.get(f"{cc.API}/candles/minutes/15?market={market}&count=200&to={to.strftime(FMT)}")


class ApiContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.page1 = api('KRW-BTC', datetime.now(timezone.utc))
        except Exception as e:
            raise unittest.SkipTest(f'네트워크 불가: {e}')

    def test_newest_first_and_strictly_descending(self):
        times = [c['candle_date_time_utc'] for c in self.page1]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertEqual(len(set(times)), len(times))

    def test_to_is_an_exclusive_upper_bound_and_pages_join_without_overlap_or_gap(self):
        oldest = self.page1[-1]['candle_date_time_utc']
        to = datetime.fromisoformat(oldest).replace(tzinfo=timezone.utc)
        page2 = api('KRW-BTC', to)
        newest2 = datetime.fromisoformat(page2[0]['candle_date_time_utc']).replace(tzinfo=timezone.utc)
        self.assertLess(newest2, to)  # 배타적: 상한 시각의 봉은 포함되지 않는다
        self.assertEqual(to - newest2, timedelta(minutes=15))  # 이웃 봉이므로 페이지 경계 누락·중복 없음(BTC는 매 봉 거래)

    def test_kst_and_utc_fields_are_nine_hours_apart(self):
        c = self.page1[0]
        kst = datetime.fromisoformat(c['candle_date_time_kst'])
        utc = datetime.fromisoformat(c['candle_date_time_utc'])
        self.assertEqual(kst - utc, timedelta(hours=9))
        self.assertEqual(pit.to_epoch(c['candle_date_time_kst']), int(utc.replace(tzinfo=timezone.utc).timestamp()))

    def test_resume_and_extend_equal_a_single_bulk_collection_for_completed_bars(self):
        since_short = datetime.now(timezone.utc) - timedelta(days=2)
        since_long = datetime.now(timezone.utc) - timedelta(days=4)
        with tempfile.TemporaryDirectory() as bulk, tempfile.TemporaryDirectory() as step:
            cc.collect_market('KRW-ETH', Path(bulk), since_long)
            cc.collect_market('KRW-ETH', Path(step), since_short)  # 짧게 받고
            cc.collect_market('KRW-ETH', Path(step), since_long)  # 과거로 확장(+최신 이어받기)
            cutoff = int((datetime.now(timezone.utc) - timedelta(minutes=30)).timestamp()) // 900 * 900
            a = pit.load_dir(bulk, cutoff=cutoff)['KRW-ETH']
            b = pit.load_dir(step, cutoff=cutoff)['KRW-ETH']
            common = set(a) & set(b)
            self.assertGreater(len(common), 150)
            self.assertEqual({s: a[s] for s in common}, {s: b[s] for s in common})
            # 두 방식의 완결 봉 집합도 같아야 한다(과거 쪽 경계 하루 이내 차이는 허용하지 않음: 같은 since)
            lo = max(min(a), min(b))
            self.assertEqual({s for s in a if s >= lo}, {s for s in b if s >= lo})


if __name__ == '__main__':
    unittest.main()
