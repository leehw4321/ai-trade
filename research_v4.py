"""Research-derived conservative filters; hypotheses, not proven returns."""
import math
from adaptive_model import ema
from scalping_strategy import validate_bars


def trend_assessment(rows, unit, now):
    validate_bars(rows, unit * 60, 200, now)
    closes = [b['c'] for b in rows]
    momentum = closes[-1] / closes[-21] - 1
    average = ema(closes, 200)
    return {'trend_ok': closes[-1] > average and momentum > 0,
            'momentum_20': momentum, 'ema200': average,
            'evidence': 'research hypothesis; profitability unverified'}


def adjust_plan(plan, price, volatility_target=.005):
    """Shrink only. Existing stop/exposure/depth checks remain authoritative.

    Planned reward is a price-distance proxy, never an expected return forecast.
    """
    out = plan.copy()
    values = (price, out['atr'], out['stop'], out['cost_rate'], out['amount'])
    if not all(isinstance(x, (float, int)) and math.isfinite(x) for x in values):
        raise ValueError('v4: 유효하지 않은 주문 계획')
    if price <= 0 or out['atr'] <= 0 or out['amount'] <= 0 or not 0 < out['stop'] < price or out['cost_rate'] <= 0:
        raise ValueError('v4: 가격·비용·위험 확인 필요')
    reward = 1.5 * (price - out['stop']) / price
    if reward < 3 * out['cost_rate']:
        raise ValueError('v4: 계획 보상이 예상 왕복 비용의 3배 미만')
    scale = min(1., volatility_target / (out['atr'] / price))
    out['amount'] *= scale
    if out['amount'] < 5000:
        raise ValueError('v4: 변동성 조절 후 최소 주문금 미달')
    return out
