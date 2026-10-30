"""Per-account paper sizing settings. Values are ratios except max_positions."""
import json
import math
from trend_engine import atomic

DEFAULTS = dict(max_position=.25, max_total=.5, max_positions=2, risk=.0025,
                max_open_risk=.005, volatility_target=.005)


def validate(value):
    result = {}
    for key, default in DEFAULTS.items():
        raw = value.get(key, default)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
            raise ValueError('투자 비중은 유효한 숫자로 입력하세요.')
        if key == 'max_positions':
            if int(raw) != raw or not 1 <= raw <= 10:
                raise ValueError('동시 보유·예약은 1~10종목입니다.')
            result[key] = int(raw)
        else:
            maximum = 1 if key in ('max_position', 'max_total') else .05 if key == 'max_open_risk' else .01 if key == 'risk' else .1
            if not 0 < raw <= maximum:
                raise ValueError('비중 범위: 종목·전체 100%, 거래 위험 1%, 총 위험 5%, ATR 기준 10% 이하입니다.')
            result[key] = float(raw)
    if result['max_position'] > result['max_total']:
        raise ValueError('종목당 비중은 전체 투자 비중 이하여야 합니다.')
    if result['risk'] > result['max_open_risk']:
        raise ValueError('거래당 위험은 총 계획 위험 이하여야 합니다.')
    return result


class AllocationSettings:
    def __init__(self, root):
        self.path = root / 'allocation_v4.json'
        self.value = validate(json.loads(self.path.read_text(encoding='utf-8'))) if self.path.exists() else DEFAULTS.copy()

    def update(self, body):
        value = validate(body)
        atomic(self.path, value)
        self.value = value

    def rules(self, base):
        return base | {k: self.value[k] for k in ('risk', 'max_open_risk', 'max_position', 'max_total')}
