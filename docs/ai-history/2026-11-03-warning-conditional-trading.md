# 경보종목도 조건부로 거래할 수 있는가


## 2026-11-03 13:24 KST 현재 경보종목 조건부 매매 제안(미합의)
- 공개market/all과ticker/orderbook 직접조회:KRW경보15개. 투자유의5(EGLD,MANTRA,BLUR,SOPH,ZIL);거래량단독6(WAVES,DKA,CASHCAT,FLUID,MEGA,GRVT);입금단독NEAR/ZK;괴리단독ELF;거래량+입금POD. 시점별변동가능하며당시경보를과거에소급하지않음. API는주의/경고/위험단계미제공.
- 제안은원인별대응이며위험성우열이통계검증됐다는뜻아님. 거래량/가격형은모멘텀과겹쳐기회도있지만거래량은하락/분배/허위유동성에도동반될수있음. 투자유의/거래지원종료/기술문제,입금·괴리·집중형또는혼합형은신규진입실험에서우선제외하고사유확인. 상승기회가전혀없다는주장은아님.
- 보유관리: 경보만강제전량청산하지않음. 기존손절/이익보호선유지,악화시합리적감축검토. 신규/추가/재진입: 조건부연구가능하되추격·손실물타기예외가아님. 확정5분봉눌림저점유지→직전봉고점종가회복을진입가설로삼고다음시점호가를사용. 손절은눌림구조실패,추가물량별원가/손절/계좌총위험분리필요(현재lot분리미구현). 세부정의미정으로실행불가.
- 비용이중요:13:24:15호가spread WAVES.649%,DKA.230%,CASHCAT.699%,FLUID.491%,MEGA.334%,GRVT.337%. buyask→sellbid가정의순간가격차이며왕복전체비용확정값아님. 계좌수수료+수량별호가소진/시장충격/청산시스프레드변화별도. 목표1%단타가유리하다고단정못함. 지정가체결도확실하지않고체결후역선택위험이있음.
- 기존전술실험과통합제안:가상예산계좌5%이내/거래위험0.1%/동시1종목/최대30분은검증가설. 현행경보차단vs가격거래량형만포함을동일진입/위험/비용으로비교하고무경보동일신호도함께평가. 실패·거부·청산미체결/비용스트레스포함,선별성공사례만사용안함. 원인별순기대값/낙폭/회전비용검증전실거래완화없음.
- 실제Claude호출은사용량한도(15:40복귀안내)로응답없음. ChatGPT제안이며합의아님. 공유근거문서에등록,실거래코드/설정/주문변경없음.

## 공식 근거
- [경보 API 단계 해석](https://docs.upbit.com/kr/changelog/market_event)
- [호가·잔량 조회](https://docs.upbit.com/kr/reference/list-orderbooks)

## 시점별 공개 관측
```json
{
  "asof_kst": "2026-11-03T13:24:15+09:00",
  "warnings": [
    {
      "market": "KRW-WAVES",
      "name": "웨이브",
      "investment_warning": false,
      "causes": [
        "TRADING_VOLUME_SOARING"
      ]
    },
    {
      "market": "KRW-DKA",
      "name": "디카르고",
      "investment_warning": false,
      "causes": [
        "TRADING_VOLUME_SOARING"
      ]
    },
    {
      "market": "KRW-EGLD",
      "name": "멀티버스엑스",
      "investment_warning": true,
      "causes": [
        "GLOBAL_PRICE_DIFFERENCES"
      ]
    },
    {
      "market": "KRW-CASHCAT",
      "name": "캐시캣",
      "investment_warning": false,
      "causes": [
        "TRADING_VOLUME_SOARING"
      ]
    },
    {
      "market": "KRW-NEAR",
      "name": "니어프로토콜",
      "investment_warning": false,
      "causes": [
        "DEPOSIT_AMOUNT_SOARING"
      ]
    },
    {
      "market": "KRW-MANTRA",
      "name": "만트라",
      "investment_warning": true,
      "causes": [
        "GLOBAL_PRICE_DIFFERENCES"
      ]
    },
    {
      "market": "KRW-BLUR",
      "name": "블러",
      "investment_warning": true,
      "causes": [
        "GLOBAL_PRICE_DIFFERENCES"
      ]
    },
    {
      "market": "KRW-POD",
      "name": "돌핀",
      "investment_warning": false,
      "causes": [
        "TRADING_VOLUME_SOARING",
        "DEPOSIT_AMOUNT_SOARING"
      ]
    },
    {
      "market": "KRW-ZK",
      "name": "지케이싱크",
      "investment_warning": false,
      "causes": [
        "DEPOSIT_AMOUNT_SOARING"
      ]
    },
    {
      "market": "KRW-ELF",
      "name": "엘프",
      "investment_warning": false,
      "causes": [
        "GLOBAL_PRICE_DIFFERENCES"
      ]
    },
    {
      "market": "KRW-SOPH",
      "name": "소폰",
      "investment_warning": true,
      "causes": [
        "TRADING_VOLUME_SOARING",
        "GLOBAL_PRICE_DIFFERENCES"
      ]
    },
    {
      "market": "KRW-FLUID",
      "name": "플루이드",
      "investment_warning": false,
      "causes": [
        "TRADING_VOLUME_SOARING"
      ]
    },
    {
      "market": "KRW-MEGA",
      "name": "메가이더",
      "investment_warning": false,
      "causes": [
        "TRADING_VOLUME_SOARING"
      ]
    },
    {
      "market": "KRW-ZIL",
      "name": "질리카",
      "investment_warning": true,
      "causes": [
        "TRADING_VOLUME_SOARING",
        "GLOBAL_PRICE_DIFFERENCES"
      ]
    },
    {
      "market": "KRW-GRVT",
      "name": "그래비티토큰",
      "investment_warning": false,
      "causes": [
        "TRADING_VOLUME_SOARING"
      ]
    }
  ],
  "volume_price_only_book_snapshot": [
    {
      "market": "KRW-WAVES",
      "last": 306.0,
      "spread_pct": 0.649,
      "bid_depth_within_0_5pct_krw": 18082538,
      "change_vs_previous_close_pct": -2.86
    },
    {
      "market": "KRW-DKA",
      "last": 4.34,
      "spread_pct": 0.23,
      "bid_depth_within_0_5pct_krw": 9964213,
      "change_vs_previous_close_pct": -4.82
    },
    {
      "market": "KRW-CASHCAT",
      "last": 143.0,
      "spread_pct": 0.699,
      "bid_depth_within_0_5pct_krw": 22141590,
      "change_vs_previous_close_pct": 5.15
    },
    {
      "market": "KRW-FLUID",
      "last": 2446.0,
      "spread_pct": 0.491,
      "bid_depth_within_0_5pct_krw": 29633844,
      "change_vs_previous_close_pct": -0.73
    },
    {
      "market": "KRW-MEGA",
      "last": 59.6,
      "spread_pct": 0.334,
      "bid_depth_within_0_5pct_krw": 52500941,
      "change_vs_previous_close_pct": -2.3
    },
    {
      "market": "KRW-GRVT",
      "last": 296.0,
      "spread_pct": 0.337,
      "bid_depth_within_0_5pct_krw": 19253437,
      "change_vs_previous_close_pct": -2.31
    }
  ]
}
```

## 실제 Claude 호출 결과
You've hit your 5-hour limit · resets 3:40pm (Asia/Seoul)