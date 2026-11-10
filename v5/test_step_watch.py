import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import step_watch as sw

T0 = dt.datetime(2026, 11, 10, 9, 0, tzinfo=dt.timezone.utc).timestamp()


def bars15(price=100.0, rng=2.0, n=20, end=T0):
    """매수 시각(end) 직전까지 15분봉. 모든 봉의 변동폭 = rng → ATR = rng/price."""
    out = []
    for i in range(n):
        t = dt.datetime.fromtimestamp(end - (n - i) * 900, dt.timezone.utc).isoformat()
        out.append({'t': t, 'o': price, 'h': price + rng / 2, 'l': price - rng / 2, 'c': price})
    return out


class StepWatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.w = sw.Watch(self.d)
        self.pos = {'KRW-A': {'qty': 1000.0, 'cost': 100_000.0, 'time': dt.datetime.fromtimestamp(T0, dt.timezone.utc).isoformat()}}

    def run_minutes(self, prices, start=T0 + 60):
        """1분마다 같은 가격 3번(3초 감시 흉내)."""
        t = start
        for px in prices:
            for s in (0, 20, 40):
                self.w.update(self.pos, {'KRW-A': px}, atr_bars=lambda m: bars15(), now=t + s)
            t += 60
        return t

    def events(self):
        return [json.loads(x)['event'] for x in (self.d / 'step_watch.jsonl').read_text(encoding='utf-8').splitlines()]

    def test_atr_step_and_ten_minute_hold_before_step_counts(self):
        self.run_minutes([100.0])
        w = next(iter(self.w.s.values()))
        self.assertAlmostEqual(w['step'], 0.02)            # ATR 2% × 1.0
        self.run_minutes([102.5] * 9, start=T0 + 120)      # 9분만 유지 → 아직 인정 안 함
        self.assertEqual(w['level'], 0)
        self.run_minutes([102.5] * 3, start=T0 + 120 + 9 * 60)
        self.assertEqual(w['level'], 1)
        self.assertAlmostEqual(w['stop'], 100 * (1 + 2 * sw.FEE))   # 1계단 = 본전(수수료 포함)

    def test_brief_spike_is_not_a_step(self):
        self.run_minutes([100.0, 103.0, 100.0, 103.0, 100.0] * 4)
        self.assertEqual(next(iter(self.w.s.values()))['level'], 0)
        self.assertNotIn('가상 매도', self.events())

    def test_record_mode_virtual_sell(self):
        t = self.run_minutes([100.0] + [102.5] * 12 + [100.05])
        w = next(iter(self.w.s.values()))
        self.assertEqual(w['state'], 'hit')
        self.assertAlmostEqual(w['hit_px'], 100.05)
        self.w.update({}, {}, now=t + 120)                  # 장부에서 사라짐 = 실제 매도
        self.assertEqual(w['state'], 'closed')
        self.assertIn('기록 모드', sw.render(self.w))

    def live_run(self, prices, sold, start=T0 + 60):
        t = start
        for px in prices:
            for s in (0, 20, 40):
                held = {} if sold else self.pos
                self.w.update(held, {'KRW-A': px}, atr_bars=lambda m: bars15(), now=t + s,
                              live_sell=self.sell, guard={'arm': 0.03, 'give': 0.3})
                if self.sold_calls and not sold:
                    return t + s
            t += 60
        return t

    def sell(self, m, w, px):
        self.sold_calls.append((m, px))
        w['fill_funds'], w['fill_fee'], w['fill_vol'] = w['qty'] * px, w['qty'] * px * sw.FEE, w['qty']
        return True

    def test_live_sell_then_old_rule_estimate_and_banner(self):
        self.sold_calls = []
        self.pos['KRW-A']['stop'] = 95.5                    # 그때 손절가
        t = self.live_run([100.0] + [102.5] * 12 + [100.05], sold=False)
        self.assertEqual(len(self.sold_calls), 1)
        w = next(iter(self.w.s.values()))
        self.assertEqual(w['state'], 'sold')
        self.assertEqual(self.w.cf_markets(), ['KRW-A'])
        self.live_run([99.0, 97.0, 95.0], sold=True, start=t + 60)   # 이전 규칙이었다면 손절 95.5에서 끝
        self.assertTrue(w['cf_done'])
        self.assertLess(w['cf_pnl'], w['actual_pnl'])
        s = self.w.summary()
        self.assertEqual((s['n'], s['win_now'], s['win_old']), (1, 0, 0))   # 둘 다 손실(본전 매도는 수수료만큼 손실)
        self.assertGreater(s['pnl_now'] - s['pnl_old'], 0)
        html = sw.render(self.w, live=True)
        self.assertIn('2+5 실험 중', html)
        self.assertIn('이전 조건', html)
        self.assertIn('효과 +', html)

    def test_live_failed_sell_stays_open_and_retries(self):
        self.sold_calls = []
        self.w.update(self.pos, {'KRW-A': 100.0}, atr_bars=lambda m: bars15(), now=T0 + 60, live_sell=lambda *a: False)
        t = T0 + 120
        for px in [102.5] * 12 + [100.0, 100.0]:
            self.w.update(self.pos, {'KRW-A': px}, now=t, live_sell=lambda *a: False); t += 60
        w = next(iter(self.w.s.values()))
        self.assertEqual(w['state'], 'open')
        self.assertIn('계단 보호 매도 보류', self.events())

    def test_position_closed_by_other_rule_counts_as_same_as_old(self):
        self.sold_calls = []
        self.live_run([100.0, 101.0], sold=False)
        self.w.update({}, {}, now=T0 + 3600, live_sell=self.sell)
        s = self.w.summary()
        self.assertEqual(s['n'], 1)
        self.assertAlmostEqual(s['pnl_now'], s['pnl_old'])

    def test_no_order_side_effects_and_bad_atr_retries_slowly(self):
        calls = []
        def fail(m):
            calls.append(m); raise OSError('net')
        for s in range(0, 120, 3):
            self.w.update(self.pos, {'KRW-A': 100.0}, atr_bars=fail, now=T0 + 60 + s)
        self.assertEqual(len(calls), 1)                     # 실패해도 3초마다 다시 조회하지 않는다
        self.assertEqual(self.w.s, {})

    def test_old_position_uses_recent_bars(self):
        self.pos['KRW-A']['time'] = dt.datetime.fromtimestamp(T0 - 86400, dt.timezone.utc).isoformat()
        self.w.update(self.pos, {'KRW-A': 100.0}, atr_bars=lambda m: bars15(), now=T0 + 60)
        w = next(iter(self.w.s.values()))
        self.assertTrue(w['late'])
        self.assertAlmostEqual(w['step'], 0.02)


if __name__ == '__main__':
    unittest.main()
