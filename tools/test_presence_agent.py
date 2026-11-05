import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import presence_agent as p

NOW = 1_000_000.0
LIVE_OTHER = {'owner': 'x:1', 'expires': NOW + 100}
LIVE_MINE = {'owner': 'me:1', 'expires': NOW + 100}


def d(idle=5, streak=3, lease=LIVE_OTHER, mine=False, bot=True, last=0, fails=0, changed=0, paused=False):
    return p.decide(NOW, idle, streak, lease, mine, bot, last, fails, changed, paused)[0]


class Decide(unittest.TestCase):
    def test_active_user_takes_over_from_other_pc(self):
        self.assertEqual(d(), 'take')

    def test_not_active_does_nothing(self):
        self.assertEqual(d(idle=600), 'idle')
        self.assertEqual(d(streak=1), 'wait')

    def test_pause_file_wins(self):
        self.assertEqual(d(paused=True), 'pause')

    def test_already_mine_with_bot_running_is_left_alone(self):
        self.assertEqual(d(lease=LIVE_MINE, mine=True), 'ok')

    def test_mine_but_bot_down_restarts(self):
        self.assertEqual(d(lease=LIVE_MINE, mine=True, bot=False), 'take')

    def test_free_or_expired_lease_is_taken(self):
        self.assertEqual(d(lease={'owner': ''}), 'take')
        self.assertEqual(d(lease={'owner': 'x:1', 'expires': NOW - 500}), 'take')

    def test_cooldown_and_backoff_after_failures(self):
        self.assertEqual(d(last=NOW - 100), 'cooldown')
        self.assertEqual(d(last=NOW - 700), 'take')
        self.assertEqual(d(last=NOW - 700, fails=1), 'take')       # 5분 단계는 10분 기본 간격에 묻힌다
        self.assertEqual(d(last=NOW - 700, fails=2), 'cooldown')   # 15분 단계
        self.assertEqual(d(last=NOW - 2000, fails=3), 'cooldown')  # 1시간 단계
        self.assertEqual(d(last=NOW - 4000, fails=3), 'take')

    def test_other_pc_that_just_took_over_is_not_taken_back_immediately(self):
        self.assertEqual(d(changed=NOW - 60), 'settling')
        self.assertEqual(d(changed=NOW - 700), 'take')


if __name__ == '__main__':
    unittest.main()
