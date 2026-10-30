"""Persisted defaults for future short positions; existing timers remain intact."""
import json
import threading
from investment_profiles import PROFILES


class HoldingSettings:
    def __init__(self, root):
        self.path = root / 'holding_settings.json'
        self.lock = threading.RLock()
        self.value = {'max_minutes': 45, 'progress_minutes': 15}
        if self.path.exists():
            self.value = self.validate(json.loads(self.path.read_text(encoding='utf-8')))

    @staticmethod
    def validate(body):
        a, b = body.get('max_minutes'), body.get('progress_minutes')
        if (type(a) is not int or type(b) is not int or not 1 <= a <= 1440
                or not 0 <= b <= a):
            raise ValueError('최대 보유는 1~1440분 정수, 진행 확인은 0~최대 보유분으로 입력하세요. 0은 진행 확인 해제입니다.')
        return {'max_minutes': a, 'progress_minutes': b}

    def snapshot(self):
        with self.lock:
            return self.value.copy()

    def update(self, body):
        value = self.validate(body)
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(value), encoding='utf-8')
            temp.replace(self.path)
            self.value = value
        return '단타 시간 저장 완료 · 이후 신규 진입·분류 변경·자동 관리 위임부터 적용됩니다.'

    def profiles(self):
        value = self.snapshot()
        profiles = {k: v.copy() for k, v in PROFILES.items()}
        profiles['short'].update(max_hours=value['max_minutes']/60,
                                 progress_seconds=value['progress_minutes']*60,
                                 horizon=f"최대 {value['max_minutes']}분 · 진행 확인 {value['progress_minutes']}분",
                                 description='15분 방향·5분 눌림·1분 확인 / ATR 손절·추적 / 보유시간은 저장된 설정 적용')
        return profiles
