import io
import json
import tempfile
import unittest
from pathlib import Path

import exit_watch as ew
import market_state as ms

H = 3600


def bars(closes):
    return [{'t': str(i), 'c': c} for i, c in enumerate(closes)]


class MarketState(unittest.TestCase):
    def test_zones_and_compute(self):
        self.assertEqual(ms.zone(-0.08)[0], '폭락')
        self.assertEqual(ms.zone(-0.03)[0], '약세')
        self.assertEqual(ms.zone(0.0)[0], '횡보')
        self.assertEqual(ms.zone(0.03)[0], '상승')
        self.assertEqual(ms.zone(0.2)[0], '폭등')
        uni = {'KRW-BTC': bars([100] * 96 + [110]), 'KRW-A': bars([100] * 96 + [90])}
        s = ms.compute(uni, -0.03, -0.025, '2026-11-06T12:00+09:00')
        self.assertEqual((s['up'], s['total'], s['zone'], s['brake_4h']), (1, 2, '약세', True))
        self.assertAlmostEqual(s['btc_24h'], 0.10)

    def test_render_with_and_without_data(self):
        self.assertIn('첫 판단 뒤', ms.render(None, None))
        page = ms.render({'index_24h': -0.047, 'index_4h': -0.01, 'up': 5, 'total': 30, 'at': '2026-11-06T12:00+09:00'},
                         {'value': 22, 'label': '극도의 공포', 'fetched': 0}, now=1)
        for want in ('약세', '-4.70%', '5/30', '공포·탐욕', '22'):
            self.assertIn(want, page)

    def test_fng_fetch_saves_and_rejects_bad_value(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'fng.json'
            ok = lambda req, timeout: io.BytesIO(json.dumps({'data': [{'value': '30', 'value_classification': 'Fear', 'timestamp': '1'}]}).encode())
            ms.fetch_fng(path, now=5, opener=ok)
            self.assertEqual(ms.load(path)['label'], '공포')
            bad = lambda req, timeout: io.BytesIO(json.dumps({'data': [{'value': '300'}]}).encode())
            with self.assertRaises(ValueError):
                ms.fetch_fng(path, opener=bad)
            self.assertEqual(ms.load(path)['value'], 30)        # 실패해도 이전 값 유지


class ExitWatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.w = ew.Watch(Path(self.tmp.name))
        self.t0 = 1_800_000_000.0
        import datetime as dt
        self.iso = dt.datetime.fromtimestamp(self.t0).astimezone().isoformat()
        self.pos = {'KRW-A': {'qty': 1000.0, 'cost': 100_050.0, 'time': self.iso, 'profit_guard_peak': 100.5}}   # 평단 100.05

    def events(self):
        return [json.loads(x)['event'] for x in (Path(self.tmp.name) / 'exit_watch.jsonl').read_text(encoding='utf-8').splitlines()]

    def test_tick_table(self):
        self.assertEqual(ew.tick(118), 1); self.assertEqual(ew.tick(6.04), 0.01); self.assertEqual(ew.tick(3_119_000), 1000)
        self.assertEqual(ew.ceil_tick(100.1), 101)

    def test_weak_position_arms_then_virtual_fill(self):
        self.w.update(self.pos, {'KRW-A': 99.0}, {'KRW-A': (3, .05)}, now=self.t0 + 1 * H)
        self.assertEqual(self.w.s, {})                                   # 2시간 전에는 판정하지 않는다
        self.w.update(self.pos, {'KRW-A': 99.0}, {'KRW-A': (3, .05)}, now=self.t0 + 2 * H)
        k = f'KRW-A|{self.iso}|2'
        self.assertEqual((self.w.s[k]['state'], self.w.s[k]['limit']), ('armed', 101))
        self.w.update(self.pos, {'KRW-A': 101.0}, {}, now=self.t0 + 2.5 * H)   # 지정가에 닿기만 함: 체결 아님
        self.assertEqual(self.w.s[k]['state'], 'armed')
        self.w.update(self.pos, {'KRW-A': 102.0}, {}, now=self.t0 + 2.6 * H)   # 한 호가 위 통과
        self.assertEqual(self.w.s[k]['state'], 'filled')
        self.assertGreater(self.w.s[k]['pnl'], 0)

    def test_strong_position_passes_and_sale_first_is_recorded(self):
        strong = {'KRW-A': dict(self.pos['KRW-A'], profit_guard_peak=103.0)}
        self.w.update(strong, {'KRW-A': 99.0}, {'KRW-A': (3, .05)}, now=self.t0 + 2 * H)
        self.assertEqual(self.w.s[f'KRW-A|{self.iso}|2']['state'], 'pass')
        w2 = ew.Watch(Path(self.tmp.name) / '..' / Path(self.tmp.name).name)
        w2.s = {}
        w2.update(self.pos, {'KRW-A': 99.0}, {'KRW-A': (3, .05)}, now=self.t0 + 2 * H)
        w2.update(self.pos, {}, {}, now=self.t0 + 2.1 * H)                # 가격을 못 받은 순간은 보유로 본다
        self.assertEqual(w2.s[f'KRW-A|{self.iso}|2']['state'], 'armed')
        w2.update({}, {}, {}, now=self.t0 + 2.2 * H)                       # 포지션이 사라짐 = 실제 매도 먼저
        self.assertEqual(w2.s[f'KRW-A|{self.iso}|2']['state'], 'sold_first')

    def test_deadline_stage2_or_cancel(self):
        self.w.update(self.pos, {'KRW-A': 99.0}, {'KRW-A': (3, .05)}, now=self.t0 + 2 * H)
        k = f'KRW-A|{self.iso}|2'
        self.w.update(self.pos, {'KRW-A': 99.0}, {}, now=self.t0 + 4.1 * H)    # 순위 자료 없으면 판단 미룸
        self.assertEqual(self.w.s[k]['state'], 'armed')
        self.w.update(self.pos, {'KRW-A': 99.0}, {'KRW-A': (20, .01)}, now=self.t0 + 4.2 * H)
        self.assertEqual(self.w.s[k]['state'], 'stage2')                   # 15위 밖 = 근거 약화
        self.assertIn('2단계 매도(가상)', self.events())
        s, first = self.w.summary()
        self.assertEqual(s[2]['armed'], 1)
        self.assertIn('기록 모드', ew.render(self.w, Path(self.tmp.name) / 'none.jsonl'))


if __name__ == '__main__':
    unittest.main()
