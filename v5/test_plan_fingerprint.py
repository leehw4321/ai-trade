"""2026-11-27 재발 방지: 주문안(shadow)과 봇의 설정 지문 어긋남(11-26 21:54~11-27 08:52 주문 0건 사고)."""
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

import strategy as st
import rotation_policy as rotation
import shadow


class PlanFingerprintTest(unittest.TestCase):
    def test_mismatch_detected_only_when_hash_differs(self):
        p = st.Params()
        same = {'rotation': {'policy_hash': rotation.policy_hash(p)}}
        self.assertIsNone(rotation.proposal_mismatch(same, p))
        other = replace(p, chase_half_cap=0.33)
        mm = rotation.proposal_mismatch(same, other)
        self.assertEqual(mm, (rotation.policy_hash(p), rotation.policy_hash(other)))

    def test_missing_envelope_is_not_alarm(self):
        p = st.Params()
        for prop in (None, {}, {'rotation': None}, {'rotation': {'policy_hash': 3}}):
            self.assertIsNone(rotation.proposal_mismatch(prop, p))

    def test_mismatch_proposal_is_really_rejected(self):
        """경보가 실제 거절 조건과 같은 원인을 본다: 지문만 다른 주문안은 validate가 거절."""
        p = replace(st.Params(), rotation_enabled=True)
        sec = rotation.slot_seconds(p)
        closed = 1795739400 // sec * sec
        now = closed + 60
        old = replace(p, chase_half_cap=0.33)
        prop = {'rotation': rotation.envelope(old, closed, now), 'signal_closed_at': closed, 'signal_tf_minutes': p.tf}
        self.assertIsNotNone(rotation.proposal_mismatch(prop, p))
        with self.assertRaises(ValueError):
            rotation.validate(prop, p, now, -1)
        good = dict(prop, rotation=rotation.envelope(p, closed, now))
        self.assertIsNone(rotation.proposal_mismatch(good, p))
        rotation.validate(good, p, now, -1)

    def test_shadow_restarts_on_stable_change_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'config.json').write_text('{"strategy": {}}', encoding='utf-8')
            fp0 = shadow.fingerprint(root, ('config.json', 'strategy.py'))
            self.assertEqual(shadow.changed_stably(fp0, None, fp0), (False, None))
            (root / 'config.json').write_text('{"strategy": {"chase_half_cap": 0.2}}', encoding='utf-8')
            fp1 = shadow.fingerprint(root, ('config.json', 'strategy.py'))
            self.assertNotEqual(fp0, fp1)
            restart, pending = shadow.changed_stably(fp0, None, fp1)
            self.assertFalse(restart)          # 처음 본 변경은 보류(쓰는 중일 수 있음)
            self.assertTrue(shadow.changed_stably(fp0, pending, fp1)[0])   # 같은 변경을 다시 보면 재시작
            (root / 'strategy.py').write_text('x', encoding='utf-8')
            fp2 = shadow.fingerprint(root, ('config.json', 'strategy.py'))
            self.assertFalse(shadow.changed_stably(fp0, fp1, fp2)[0])      # 또 바뀌는 중이면 기다림
            self.assertEqual(shadow.changed_stably(fp0, fp1, fp0), (False, None))  # 원래대로 돌아오면 취소


if __name__ == '__main__':
    unittest.main()
