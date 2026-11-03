import sys
import unittest
import tempfile
import subprocess
import io
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ai_session import find_secrets
import ai_session


class SecretScan(unittest.TestCase):
    def test_flags_real_looking_keys(self):
        self.assertTrue(find_secrets('UPBIT_ACCESS_KEY=AbCdEfGhIjKlMnOpQrStUv123456'))
        self.assertTrue(find_secrets('secret_key: "abcdefghijklmnopqrstuvwxyz0123"'))
        self.assertTrue(find_secrets('Authorization: Bearer abcdefghijklmnopqrstuvwxyz.123'))
        self.assertTrue(find_secrets('token ghp_abcdefghijklmnopqrstuvwxyz1234'))

    def test_allows_placeholders_and_prose(self):
        self.assertFalse(find_secrets('UPBIT_ACCESS_KEY=your_access_key'))
        self.assertFalse(find_secrets('UPBIT_SECRET_KEY='))
        self.assertFalse(find_secrets('API 키는 집 PC에서 별도 설정한다. 커밋 b363497a152cff23fd6bd5c714e16b5be28b1ef1'))


class StatusDiscovery(unittest.TestCase):
    def test_missing_app_is_explicitly_unknown(self):
        output = io.StringIO()
        with patch.object(ai_session, 'v5_dirs', return_value=[]), \
             patch.object(ai_session, 'running', return_value=[]), redirect_stdout(output):
            ai_session.status(None)
        self.assertIn('코드 대조 불가', output.getvalue())
        self.assertIn('V5_APP_DIR', output.getvalue())

    def test_pythonw_is_included_and_errors_are_not_reported_as_stopped(self):
        result = subprocess.CompletedProcess([], 0, '123 pythonw bot.py\n456 pythonw shadow.py\n', '')
        with patch.object(ai_session.subprocess, 'run', return_value=result) as run:
            self.assertEqual(len(ai_session.running()), 2)
            self.assertIn("Name='pythonw.exe'", run.call_args.args[0][-1])
        result.returncode = 1
        with patch.object(ai_session.subprocess, 'run', return_value=result):
            self.assertIn('확인할 수 없음', ai_session.running()[0])

    def test_company_and_desktop_app_paths_are_discovered_without_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); repo = base / 'repo'; home = base / 'home'
            paths = [repo / 'v5', base / '자동거래 v5', home / 'Desktop' / '코인 트레이딩' / 'v5-app' / 'v5']
            for p in paths:
                p.mkdir(parents=True); (p / 'config.json').write_text('{}')
            with patch.object(ai_session, 'ROOT', repo), patch.object(Path, 'home', return_value=home), \
                 patch.dict(ai_session.os.environ, {'V5_APP_DIR': str(paths[-1])}):
                self.assertEqual(ai_session.v5_dirs(), paths)


if __name__ == '__main__':
    unittest.main()
