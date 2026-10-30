"""All KRW pairs are observed; liquidity/market events decide entry eligibility."""
import math
import re

STABLE = {'KRW-USDT','KRW-USDC','KRW-DAI','KRW-USDE','KRW-USDS','KRW-USD1','KRW-FDUSD','KRW-TUSD'}
LIMITS = {'min_turnover_24h':1_000_000_000, 'max_trade_age':60,
          'max_day_move':.30,'priority_slots':8,'rotation_slots':4,'catalog_ttl':900,'ticker_ttl':45}


def valid_code(value):
    return isinstance(value,str) and re.fullmatch(r'KRW-[A-Z0-9]{1,24}',value) is not None


def screen(catalog, tickers, now):
    result={}
    for market,info in catalog.items():
        if not valid_code(market):continue
        tick=tickers.get(market,{})
        reason='';turnover=0.
        try:
            event=info.get('market_event',{})
            turnover=float(tick.get('acc_trade_price_24h',0))
            age=now-float(tick.get('trade_timestamp',0))/1000
            move=float(tick.get('signed_change_rate',0))
            price=float(tick.get('trade_price',0))
            if not all(math.isfinite(v) for v in (turnover,age,move,price)) or price<=0:
                reason='시세 데이터 확인 중'
            elif market in STABLE:reason='스테이블코인: 단타 대상 제외'
            elif info.get('market_warning')=='CAUTION' or event.get('warning'):
                reason='거래 유의 종목'
            elif any(event.get('caution',{}).values()):reason='시장 주의 경보'
            # Allow sub-millisecond float conversion noise, not future trade data.
            elif not -.001<=age<=LIMITS['max_trade_age']:reason='최근 60초 거래 없음/시세 지연'
            elif turnover<LIMITS['min_turnover_24h']:reason='24시간 거래대금 10억 원 미달'
            elif abs(move)>LIMITS['max_day_move']:reason='당일 30% 초과 급등락 추격 차단'
        except (TypeError,ValueError,AttributeError):reason='시세 형식 오류'
        result[market]={'eligible':not reason,'reason':reason or '유동성 1차 통과 · 단타 조건 대기',
                        'turnover':turnover if math.isfinite(turnover) else 0,
                        'name':info.get('korean_name',market)}
    return result


def choose_candidates(screened, cursor=0, protected=()):
    eligible=sorted((m for m,r in screened.items() if r['eligible']),key=lambda m:(-screened[m]['turnover'],m))
    top=eligible[:LIMITS['priority_slots']];remaining=eligible[LIMITS['priority_slots']:]
    rotating=[remaining[(cursor+i)%len(remaining)] for i in range(min(LIMITS['rotation_slots'],len(remaining)))] if remaining else []
    return list(dict.fromkeys([*protected,*top,*rotating])),cursor+len(rotating)
