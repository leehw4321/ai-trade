"""쉬기/거래 추천 창(사용자 지시 2026-11-21): 쉬는 중이면 시작/계속 쉼, 거래 중이면 쉬기/계속 거래 추천."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path

import buy_pause as bp
import pause_advice as pa

NOW = 2_000_000_000.0


def trips(pnls):
    return [{'m': f'KRW-X{i}', 'open': NOW - 90000 + i * 100, 'close': NOW - 80000 + i * 100, 'pnl': p} for i, p in enumerate(pnls)]


def mkt(i24=0.0, up=15, brake=False, age=600):
    at = dt.datetime.fromtimestamp(NOW - age, pa.KST).isoformat()
    return {'at': at, 'index_24h': i24, 'up': up, 'total': 30, 'brake_4h': brake}


def state(paused, virtual=()):
    return {'paused': paused, 'since': NOW - 100000 if paused else None, 'log': [], 'virtual': list(virtual)}


class Verdicts(unittest.TestCase):
    def test_trading_losing_recommends_rest(self):
        a = pa.assess(state(False), trips([-2000] * 20), mkt(-0.02, 8), NOW)
        self.assertEqual(a['verdict'], 'rest'); self.assertEqual(a['title'], '쉬는 것이 유리합니다')
        self.assertIn('수익이 나기 힘듭니다', a['sub'])

    def test_trading_winning_recommends_keep_trading(self):
        a = pa.assess(state(False), trips([1000] * 20), mkt(), NOW)
        self.assertEqual(a['verdict'], 'keep_trade')

    def test_trading_mixed_is_caution(self):
        a = pa.assess(state(False), trips([-1000] * 10 + [1500] * 10), mkt(-0.02, 8), NOW)   # +2 +1 -1 = +2 → 계속
        self.assertEqual(a['verdict'], 'keep_trade')
        a = pa.assess(state(False), trips([3000] * 10 + [-1000] * 10), mkt(-0.02, 8), NOW)   # +2 -1 -1 = 0 → 주의
        self.assertEqual(a['verdict'], 'caution')

    def test_paused_recovered_recommends_start(self):
        a = pa.assess(state(True), trips([500] * 20), mkt(), NOW)
        self.assertEqual(a['verdict'], 'start'); self.assertEqual(a['title'], '지금은 매수 시점입니다')

    def test_paused_never_starts_while_recent20_negative(self):
        v = [{'t': NOW - 50000, 'm': f'KRW-V{i}', 'krw': 60000, 'ret': 0.03} for i in range(6)]
        a = pa.assess(state(True, v), trips([-3000] * 14), mkt(0.03, 25), NOW)   # 20건 합 -42000+10800 < 0
        self.assertEqual(a['verdict'], 'keep_rest')

    def test_paused_losing_strong_rest(self):
        a = pa.assess(state(True), trips([-1000] * 20), mkt(brake=True), NOW)
        self.assertEqual(a['verdict'], 'keep_rest'); self.assertIn('수익이 나기 힘듭니다', a['sub'])

    def test_too_few_trades_is_neutral(self):
        a = pa.assess(state(False), trips([-1000] * 3), None, NOW)
        self.assertEqual(a['score'], 0); self.assertEqual(a['verdict'], 'caution')

    def test_stale_market_ignored(self):
        self.assertEqual(pa._market(mkt(-0.05, 2, age=4 * 3600), NOW)[0], 0)


class Render(unittest.TestCase):
    def test_render_shows_state_reasons_and_warning(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); bp.set_paused(d, True, now=NOW - 100000)
            h = pa.render(d, trips([-1000] * 20), mkt(), {'value': 12, 'label': '극도의 공포'}, now=NOW, elsewhere='OFFICE')
            for s in ('쉬는 것이 유리합니다', '지금 쉬는 중', '최근 20건 성적', '공포탐욕 지수 12', 'OFFICE', '주문에 영향 없음'):
                self.assertIn(s, h)


if __name__ == '__main__':
    unittest.main()
