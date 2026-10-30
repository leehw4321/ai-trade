"""Presentation-only explanation of the existing candidate selection."""
import math
from scalp_universe import LIMITS


def candidate_details(markets, screened, tickers, candidates, protected):
    ranked = sorted((m for m in markets if screened.get(m, {}).get('eligible')),
                    key=lambda m: (-screened[m]['turnover'], m))
    ranks = {m: i+1 for i, m in enumerate(ranked)}
    result = {}
    for market in candidates:
        tick = tickers.get(market, {})
        def value(key):
            try:
                v = float(tick[key])
                return v if math.isfinite(v) and v >= 0 else None
            except (KeyError, ValueError, TypeError):
                return None
        rank = ranks.get(market)
        if market in protected:
            reason = '보유분·미체결 주문 보호를 위해 우선 정밀 관리'
        elif rank and rank <= LIMITS['priority_slots']:
            reason = f'유동성 통과 종목 중 24시간 거래대금 {rank}위 · 상위 8종목 우선 분석'
        else:
            reason = '유동성 통과 종목의 순환 분석 슬롯 · 나머지 종목 중 최대 4종목씩 탐색'
        opening, high, low = value('opening_price'), value('high_price'), value('low_price')
        result[market] = {'selection_reason': reason, 'turnover_rank': rank,
                          'opening': opening, 'high': high, 'low': low,
                          'price': value('trade_price'), 'turnover_day': value('acc_trade_price'),
                          'turnover_24h': value('acc_trade_price_24h'),
                          'range_pct': (high-low)/opening*100 if opening and high is not None and low is not None and high >= low else None,
                          'timestamp': value('timestamp'), 'trade_timestamp': value('trade_timestamp')}
    return result
