import subprocess
import tempfile
import unittest
from pathlib import Path

import handoff_sync as hs


def run(cwd, *a):
    subprocess.run(['git', '-C', str(cwd), *a], check=True, capture_output=True)


def ident(repo):
    for k, v in (('user.name', 't'), ('user.email', 't@t')):
        run(repo, 'config', k, v)


class Sync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.remote = t / 'r.git'
        run(t, 'init', '-q', '--bare', '-b', 'main', str(self.remote))
        self.a, self.b = t / 'a' / 'repo', t / 'b' / 'repo'
        self.a.parent.mkdir(); self.b.parent.mkdir()
        run(t, 'clone', '-q', str(self.remote), str(self.a)); ident(self.a)
        (self.a / 'x.txt').write_text('1'); (self.a / '.gitignore').write_text('data/\n')
        run(self.a, 'add', '-A'); run(self.a, 'commit', '-q', '-m', 'i'); run(self.a, 'push', '-q', '-u', 'origin', 'main')
        run(t, 'clone', '-q', str(self.remote), str(self.b)); ident(self.b)

    def test_push_then_pull_overwrites_and_keeps_ignored(self):
        (self.a / 'x.txt').write_text('2'); (self.a / 'new.txt').write_text('n')
        (self.a / 'data').mkdir(); (self.a / 'data' / 'live.json').write_text('secret')
        ok, msg = hs.push(self.a); self.assertTrue(ok, msg)
        (self.b / 'data').mkdir(); (self.b / 'data' / 'live.json').write_text('mine')
        ok, msg = hs.pull(self.b); self.assertTrue(ok, msg)
        self.assertEqual((self.b / 'x.txt').read_text(), '2')
        self.assertEqual((self.b / 'data' / 'live.json').read_text(), 'mine')     # 깃 밖 파일은 그대로
        ok, msg = hs.push(self.a); self.assertTrue(ok); self.assertIn('변경 없음', msg)

    def test_secret_files_block_push(self):
        (self.a / '.env').write_text('KEY=1'); (self.a / 'sub').mkdir(); (self.a / 'sub' / 'settings.json').write_text('{}')
        ok, msg = hs.push(self.a); self.assertFalse(ok); self.assertIn('.env', msg)
        self.assertEqual(hs.git(self.a, 'diff', '--cached', '--name-only'), '')    # 올릴 목록에 남기지 않음
        self.assertEqual(hs.git(self.remote, 'rev-list', '--count', 'main'), '1')

    def test_stops_when_other_side_changed_or_local_dirty(self):
        (self.a / 'x.txt').write_text('A'); hs.push(self.a)
        (self.b / 'x.txt').write_text('B')
        ok, msg = hs.pull(self.b); self.assertFalse(ok); self.assertIn('올리기', msg)
        ok, msg = hs.push(self.b); self.assertFalse(ok); self.assertIn('먼저 올린', msg)
        ok, msg = hs.pull(self.b, force=True); self.assertTrue(ok)
        self.assertEqual((self.b / 'x.txt').read_text(), 'A')

    def test_forbidden_names_and_size(self):
        self.assertEqual(hs.bad_paths(self.a, ['x.txt', '.env.example', 'docs/a.md']), [])
        for p in ('.env', 'v5/data/live.json', 'v5-app/v5/bot.py', 'private_handoff/a.txt', 'x/settings.json', 'v5/bot.lock'):
            self.assertTrue(hs.bad_paths(self.a, [p]), p)


if __name__ == '__main__':
    unittest.main()
