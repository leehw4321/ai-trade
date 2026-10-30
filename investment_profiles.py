"""Shared investment horizons. Initial hypotheses; no live-entry approval."""
PROFILES = {
    'long': {'name':'장기투자','unit':1440,'stop_atr':3,'trail_atr':4,'max_hours':0,
             'progress_seconds':0,'risk':.0025,'max_position':.25,'max_total':.5,'volume_ratio':1.2,
             'horizon':'4주 이상 · 추세 유지 시 계속 보유',
             'description':'일봉 종가>EMA50>EMA200·EMA50 상승·20봉 돌파·거래량 1.2배 / 3 ATR 손절·4 ATR 추적 / 고정 시간 청산 없음'},
    'swing': {'name':'스윙','unit':240,'stop_atr':2,'trail_atr':3,'max_hours':168,
              'progress_seconds':0,'risk':.0025,'max_position':.25,'max_total':.5,'volume_ratio':1.5,
              'horizon':'목표 2~7일 · 최대 7일',
              'description':'4시간봉 종가>EMA50>EMA200·EMA50 상승·20봉 돌파·거래량 1.5배 / 2 ATR 손절·3 ATR 추적 / 최대 7일'},
    'short': {'name':'단타','unit':5,'stop_atr':1.5,'trail_atr':2.5,'max_hours':.75,
              'progress_seconds':900,'risk':.0025,'max_position':.25,'max_total':.5,'volume_ratio':1.5,
              'horizon':'목표 5~30분 · 최대 45분',
              'description':'15분 상승 방향·5분 돌파 후 눌림·1분 거래량/상승 확인 / 5분 ATR 1.5배 손절·2.5배 추적 / 15분 진행 실패·최대 45분'},
}
