"""2026-11-19 사용자 지시(화면 표시만 고치기): 실행권 없는 PC의 매수 쉬기 상자가 실제 계좌 상태처럼 보이던 것."""
import json
import tempfile
import unittest
from pathlib import Path

import buy_pause as bp

SRC = (Path(__file__).resolve().parent / 'bot.py').read_text(encoding='utf-8')


class Elsewhere(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def state(self, paused):
        (self.d / 'buy_pause.json').write_text(json.dumps({'paused': paused, 'since': 1000.0 if paused else None,
                                                           'log': [], 'virtual': []}), encoding='utf-8')

    def test_owner_pc_unchanged(self):
        self.state(True)
        h = bp.render(self.d, None, [], now=5000)
        self.assertIn('⏸ 매수 쉬는 중', h)
        self.assertNotIn('실행권은', h)

    def test_other_pc_shows_real_buying(self):
        self.state(True)
        trips = [{'m': 'KRW-A', 'open': 900, 'close': 1100, 'pnl': -500.0},     # 쉬기 전 매수 → 제외
                 {'m': 'KRW-B', 'open': 2000, 'close': 2600, 'pnl': 1200.0},
                 {'m': 'KRW-C', 'open': 3000, 'close': 3300, 'pnl': -200.0}]
        h = bp.render(self.d, None, trips, now=5000, elsewhere='leehanwook')
        self.assertIn('실행권은 <b>leehanwook</b> PC', h)
        self.assertIn('이 PC에만 저장된 상태', h)
        self.assertIn('⏸ 이 PC 기록: 쉬는 중', h)
        self.assertIn('2건', h)
        self.assertIn('+1,000원', h)
        self.assertIn('실제 계좌는 매수 중', h)

    def test_other_pc_no_real_buys(self):
        self.state(True)
        h = bp.render(self.d, None, [], now=5000, elsewhere='x')
        self.assertIn('실제 계좌 매수 기록 없음', h)

    def test_other_pc_not_paused(self):
        self.state(False)
        h = bp.render(self.d, None, [], now=5000, elsewhere='<pc>')
        self.assertIn('&lt;pc&gt;', h)   # 이름은 이스케이프
        self.assertIn('그 PC 화면에서', h)

    def test_bot_passes_owner_state(self):
        self.assertIn("elsewhere=away", SRC)
        self.assertIn("snap_['owner']", SRC)


if __name__ == '__main__':
    unittest.main()
