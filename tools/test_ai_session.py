import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ai_session import find_secrets


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


if __name__ == '__main__':
    unittest.main()
