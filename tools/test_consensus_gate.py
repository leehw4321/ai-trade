import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import consensus_gate as cg

TABLE = """| # | 항목 | 상태 | 내용 |
|---|---|---|---|
| 1 | a | 제안 | 제안 중 [배포:D-1] |
| 2 | b | 합의 | 합의됨 [배포:D-2] 동일 버전 수용 |
| 3 | c | 이견 | 이견 [배포:D-3] |
| 4 | d | 합의 | 표지 없는 합의 |
| 5 | e | 합의 | 위험 [배포:D-5] |
"""


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        r = Path(self.tmp.name)
        self.debate, self.manifest, self.state = r / 't.md', r / 'm.json', r / 's.json'
        self.debate.write_text(TABLE, encoding='utf-8')
        self.manifest.write_text(json.dumps({
            'D-1': {'commit': 'x', 'config': {}}, 'D-2': {'commit': 'abc', 'config': {'live': {'trail_enabled': True}}},
            'D-3': {'commit': 'x', 'config': {}},
            'D-5': {'commit': 'abc', 'config': {'live': {'max_order_krw': 999999999}}}}), encoding='utf-8')

    def go(self, apply=True, code=0):
        with mock.patch.object(cg, 'head_contains', return_value=True), \
                mock.patch.object(cg.dp, 'deploy', return_value=code) as d:
            return cg.run_once(apply, self.debate, self.manifest, self.state), d

    def test_only_agreed_tagged_rows_are_candidates(self):
        self.assertEqual(cg.agreed_tags(TABLE), {'D-2': 2, 'D-5': 5})

    def test_applies_agreed_once_and_rejects_forbidden_key(self):
        res, d = self.go()
        msgs = {t: m for t, _, m in res}
        self.assertEqual(msgs['D-2'], '적용 완료')
        self.assertIn('거부', msgs['D-5'])                            # 주문 금액 한도는 게이트로 못 바꿈
        self.assertEqual(d.call_count, 1)
        self.assertEqual(d.call_args.kwargs['config_patch'], {'live': {'trail_enabled': True}})
        res2, d2 = self.go()
        self.assertNotIn('D-2', {t for t, _, _ in res2})              # 두 번 적용하지 않음
        self.assertEqual(d2.call_count, 0)

    def test_dry_run_does_not_deploy(self):
        res, d = self.go(apply=False)
        self.assertEqual(d.call_count, 0)
        self.assertIn('점검 모드', dict((t, m) for t, _, m in res)['D-2'])

    def test_failed_deploy_is_retried_not_recorded(self):
        res, _ = self.go(code=1)
        self.assertIn('실패', dict((t, m) for t, _, m in res)['D-2'])
        self.assertFalse(self.state.exists())

    def test_config_whitelist(self):
        self.assertTrue(cg.config_allowed({'live': {'trail_enabled': True}})[0])
        self.assertFalse(cg.config_allowed({'live': {'enabled': False}})[0])
        self.assertFalse(cg.config_allowed({'live': {'loss_stop_krw': 1}})[0])


if __name__ == '__main__':
    unittest.main()
