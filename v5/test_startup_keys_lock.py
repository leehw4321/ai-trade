"""2026-11-16 블루스크린 뒤 재시작 사고 재현 시험.

1) 남은 잠금 파일 확인: tasklist 한글(cp949) 출력을 UTF-8로 읽어 봇·그림자가 시작하자마자 죽었다.
2) 키 위치: 실행 사본(코인 트레이딩/v5-app/v5)에서 두 단계 위 .env를 찾지 못했다.
3) 키 없이 실행권: 실거래인데 키가 없으면 실행권을 잡지 않는다.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bot
import upbit
from session_guard import SessionLost


class StaleLock(unittest.TestCase):
    def test_pid_check_reads_korean_tasklist_output(self):
        cp949 = '정보: 지정된 조건에 일치하는 작업이 없습니다.\r\n'.encode('cp949')
        real = subprocess.run

        def fake(args, **kw):
            self.assertEqual(kw.get('encoding'), 'mbcs')
            return real([sys.executable, '-c', f'import sys; sys.stdout.buffer.write({cp949!r})'], **kw)
        with mock.patch.object(bot.subprocess, 'run', side_effect=fake):
            self.assertNotIn('424242', bot.pid_alive_text('424242'))

    @unittest.skipUnless(os.name == 'nt', 'Windows tasklist')
    def test_stale_lock_of_dead_pid_is_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / 'bot.lock'
            lock.write_text('999999')  # 존재하지 않는 PID (비정상 종료 뒤 남은 잠금)
            with mock.patch.object(bot, 'LOCK', lock):
                bot.single_instance()
            self.assertEqual(lock.read_text(), str(os.getpid()))

    def test_shadow_lock_check_uses_oem_encoding(self):
        src = (Path(__file__).resolve().parent / 'shadow.py').read_text(encoding='utf-8')
        line = next(l for l in src.splitlines() if "'tasklist'" in l)
        nxt = src.splitlines()[src.splitlines().index(line) + 1]
        self.assertIn("encoding='mbcs'", line + nxt)


class KeyLocation(unittest.TestCase):
    def test_env_two_levels_up_is_searched(self):
        # 실행 사본 v5-app/v5 기준 '코인 트레이딩/.env'
        self.assertIn(upbit.ROOT.parent.parent / '.env', upbit.ENV_FILES)

    def test_keys_found_in_grandparent_env(self):
        with tempfile.TemporaryDirectory() as d:
            app = Path(d) / 'v5-app' / 'v5'
            app.mkdir(parents=True)
            (Path(d) / '.env').write_text('UPBIT_ACCESS_KEY=a\nUPBIT_SECRET_KEY=b\n', encoding='utf-8')
            files = [app / '.env', app.parent / '.env', app.parent.parent / '.env']
            with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(upbit, 'ENV_FILES', files):
                self.assertEqual(upbit._keys(), ('a', 'b'))


class Guard:
    def __init__(self):
        self.reason = ''
        self.acquired = False

    def acquire(self, confirm):
        self.acquired = True
        return True


class ClaimWithoutKeys(unittest.TestCase):
    def test_live_without_keys_does_not_take_execution_right(self):
        g = Guard()
        self.assertFalse(bot.initial_claim(g, {'live_enabled': True}, keys=('', '')))
        self.assertFalse(g.acquired)
        self.assertEqual(g.reason, bot.KEYS_MISSING)

    def test_live_with_keys_claims(self):
        g = Guard()
        self.assertTrue(bot.initial_claim(g, {'live_enabled': True}, keys=('a', 'b')))
        self.assertTrue(g.acquired)

    def test_paper_mode_needs_no_keys(self):
        g = Guard()
        self.assertFalse(bot.initial_claim(g, {'live_enabled': False}, keys=('', '')))
        self.assertFalse(g.acquired)
        self.assertIn('실거래 설정이 꺼져', g.reason)

    def test_session_lost_reason_kept(self):
        g = Guard()
        g.acquire = mock.Mock(side_effect=SessionLost('다른 PC'))
        self.assertFalse(bot.initial_claim(g, {'live_enabled': True}, keys=('a', 'b')))
        self.assertEqual(g.reason, '다른 PC')


if __name__ == '__main__':
    unittest.main()
