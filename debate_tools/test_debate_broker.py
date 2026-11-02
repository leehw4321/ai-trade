import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('broker', Path(__file__).with_name('토론진행기.py'))
broker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(broker)


class BrokerTests(unittest.TestCase):
    def test_new_agenda_in_home_template_preserves_original(self):
        text = '**현재 차례: Claude** · **회차: 1**\n\n| # | 항목 | 상태 | 내용 |\n|---|---|---|---|\n| 1 | 재개 | 제안 | 기존 내용 |\n\n---\n'
        reply = {'body': '새 제안', 'updates': [{'id': 2, 'status': '제안', 'content': '검증|설계'}],
                 'agreement': '', 'finish': False}
        with patch.object(broker, 'CONTINUOUS', True):
            result, finished = broker.apply_reply(text, 'Claude', 1, reply)
        self.assertIn('| 1 | 재개 | 제안 | 기존 내용 |', result)
        self.assertIn('| 2 | 추가 결정 사항 | 제안 | 검증｜설계 |', result)
        self.assertIn('**현재 차례: ChatGPT** · **회차: 2**', result)
        self.assertFalse(finished)

    def test_child_environment_excludes_api_keys(self):
        with patch.dict(os.environ, {'UPBIT_ACCESS_KEY': 'test', 'UPBIT_SECRET_KEY': 'test',
                                     'OPENAI_API_KEY': 'test', 'ANTHROPIC_API_KEY': 'test'}):
            child = broker.env()
        for key in ('UPBIT_ACCESS_KEY', 'UPBIT_SECRET_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'):
            self.assertNotIn(key, child)


if __name__ == '__main__':
    unittest.main()
