# 업비트 자동거래 봇 재현 명세서

기준 2026년 11월 6일 14시 23분 12초 KST

이 문서는 사람과 AI가 현재 버전의 봇을 다시 구현하고 검증하도록 만든 기록이다. Claude가 제공된 소스 발췌로 작성한 초안을 참고하고, ChatGPT가 전체 소스 보관본·설정·핵심 실행 경로를 대조하여 편집했다. 수익성 인증이나 전체 코드 검산 완료를 뜻하지 않는다.

사람은 본문과 인수 시험을 먼저 읽고, AI는 같은 본문 뒤의 설정·함수 색인 및 JSON 파일을 함께 읽는다. 재현 패키지에는 원본 코드와 테스트가 들어 있다. API 키와 개인 체결 원장은 포함하지 않았다.

## 사람이 먼저 읽는 기능 요약
- 강한 종목을 고르는 순위는 15분봉72구간인18시간 수익률이다. 시장이 약하면 신규 매수를 쉬며 현재 설정의 시장 기준은6시간이다.
- 매수 후보는 상위7위에서 찾고 신규 운영 목표는 최대4종목이다. 1회 기준 금액은 봇 운용자금의4.5분의1이다.
- 이미 산 종목을 순위만으로 바로 팔지 않는다. 손절·이익보호·본전잠금과 조건부 부분교체가 각각 따로 작동한다.
- 가격 손절 상한 설정은4.5%지만 실제 체결 손실의 보장 상한은 아니다. 이익보호는3%부터 고점이익의70%를 남기는 선을 올리고, 본전잠금은1.5%부터 비용을 고려한다.
- 문서 작성 중 새로 추가된 매수 대기 기능은 큰 호가 종목 하나에 지정가를 걸고 최대15분 기다리는 코드다. 남은 금액은 조건에 따라 시장가로 전환할 수 있다. 이 추가판의 실거래 검산 완료를 뜻하지 않는다.
- 정체 종목의2/4시간 탈출은 아직 실제 매도가 아니라 가상 기록이다. 매수 대기 기능과 별개다.
- DOGE 등 보호 종목은 봇 매매에서 제외한다. 회사·집은 조회할 수 있지만 실제 주문 실행권은 하나다.
- 이 기록에는 특정 시점의 원본과 설정을 보존했다. 손익을 보장하거나 개인 계좌 장부를 백업한 문서는 아니다.

## 문서 작성 중 확인된 최신 변경판
이 문서의 기본판은 2026년 11월 6일 14시23분12초 스냅샷이다. 작성 중 다른 작업자가 실행 폴더를 바꿔 20261106_143543에 추가판을 고정했다. 아래 추가판이 관련 기능에 한해 기본판보다 우선한다. 기본판의 '매수 지정가 미구현' 설명은 14시23분 기준이며, 추가판에서는 코드가 추가되고 설정도 활성화된 상태다. 실행 프로세스의 메모리 버전이나 실제 체결은 이번 문서 작업에서 검증하지 않았다.
추가판의 전체 코드·설정·함수색인은 updates/20261106_143543/ 안에 있으며 변경된 소스는 bot.py, live.py, manual_orders.py, test_resting_buy.py, upbit.py 이다. 이 기능의 실거래 안전성/수익성 검산 완료를 뜻하지 않는다.

### 추가판 지정가 매수 대기 규칙
- 설정은 resting_buy_enabled=True, resting_min_tick=0.003, resting_wait_seconds=900다.
- Live.resting_candidate는 매수 후보 중 1칸/매도1호가 비율이 최소0.3% 이상인 종목 하나를 고른다. 비율이 가장 큰 하나만 고르고 다른 매수를 먼저 처리한 뒤 마지막에 대기 주문을 건다. 엄밀한 '저변동·급하지 않음' 조건은 코드에 별도 구현되지 않았다.
- _rest_buy는 매도1호가−1틱을 호가단위로 내린 가격에 post_only 지정가를 낸다. 이 값이 실제 매수1호가와 항상 같은 것은 아니다. 주문금액/(1+수수료)/가격의 수량을 소수8자리 내림으로 계산하고 최소주문을 검사한다.
- ATR·직접주문 보호·과열·미확정 주문을 검사하고 pending_order에 resting의 price/ref_ask/tick/krw/deadline을 저장한 뒤 거래소에 전송한다. 응답 불명 및 재시작 복구 동등성은 추가 검산이 필요하다.
- bot.run은 실시간 손절 처리 전에 manage_resting을 호출한다. 정상 대기 중 부분 체결은 장부에 반영하고, 사용자 보호 또는 기존 포지션의 stop_limit×1.005 이하 위험이 있으면 취소하고 남은 금액의 시장가 전환을 막는다.
- 기본 대기는15분이다. 기한 후 취소·조회·체결 반영을 거쳐 남은 예산이5천원 이상이고 매도1호가가 최초 ref_ask+1틱 이하이면 시장가 추가매수를 시도한다. 이는 기본판에서 제안했던 '미체결 포기'와 다른 구현이다.
- 화면 '즉시 시장가' 요청은 deadline=0과 force를 저장해 다음 관리주기에 취소 후 남은 예산을 시장가로 시도한다. force는 위1틱 상승 제한을 우회하지만 사용자 보호·실행권 등 다른 제한은 유지되어야 한다.
- upbit에 tick_size, price_str, limit_buy_post_only, cancel_order가 추가됐다. manual_orders에는 buy_now 경로가 추가됐고 순위표에 지정가/남은시간/즉시시장가 버튼을 표시한다.
- 정체 탈출 exit_watch는 여전히 기록 전용이다. 매수 지정가 대기와 매도 정체 탈출을 같은 기능으로 해석하지 않는다.

### 추가판에서 별도로 확인할 인수 시험
post_only 즉시취소, UUID없는 불명응답, 부분체결 중 재시작, 체결전 보유0건일 때 관리주기, 취소확정 전 재주문 금지, 매수보류조건 재검사, 기존종목 손절지연, force 요청의 권한, 예산·자리예약, 최소주문 미달잔량, 부동소수 호가경계를 실제 가짜거래소 시험으로 확인한다. 이번 문서에는 소스 원문을 기록했으며 이 시험을 실행하지 않았다.


## 캡처 범위와 검토 기준
이 기록의 기준은 2026년 11월 6일 14시 23분 12초 KST에 고정한 회사 PC 실행 폴더의 디스크 소스이다. 살아 있는 프로그램의 메모리에 적재된 코드와 디스크 파일을 대조한 결과는 아니다. 실행 중 설정 변경이 가능하므로 영구적인 최종본이라는 뜻이 아니다.
실행 폴더는 C:\Users\User\Desktop\코인 트레이딩\v5-app\v5 이며, D:\자동거래 v4\v5의 옛 사본과 다르다. 따라서 이 문서의 source-manifest.json을 버전의 기준으로 사용한다. Git의 작업 폴더 HEAD 하나만으로 실행본이 같다고 가정하지 않는다.
전체 소스 초안 요청은 제한시간을 넘겼고, 핵심 함수 발췌로 줄인 두 번째 실제 Claude CLI 요청에서 초안을 받았다. ChatGPT가 초안의 미확인 부분과 잘못된 의사코드를 소스에 대조하고 본문을 편집했다. 이것은 수익성 검증이나 전체 테스트 통과 선언이 아니다.
문서 본문보다 증거를 우선 확인할 때 순서는 원본 소스와 해시 → 합성 설정 → 실제 동적 상태 계약 → 서술 설명이다. 설명이 소스와 충돌하면 임의로 거래 규칙을 바꾸지 말고 차이를 이력으로 남긴다.

### 이번 캡처의 핵심 확인값
- 판단용 봉은 15분이고 상대강도는 72구간 전 대비 종가 수익률이다.
- 장세 필터는 regime_bars=24라 6시간이다. 과거 대화의 24시간 필터를 현재값으로 재사용하면 안 된다.
- 신규 매수 후보는 상위7위에서 최대4개, 최대 보유4종목이다. 인수분·기존 잔고·부분교체 과정 때문에 실제 표시 개수와 신규진입 한도는 다를 수 있다.
- bot_capital_divisor=4.5가 활성화되어 1회 기준예산은 봇 운용자금/4.5다. 설정값이 있어도 현금·위험·보호·주문실행 검사까지 모두 충족해야 주문한다.
- 순위 이탈만으로 매도하지 않는 rank_exit=false, 부족분을 손실 묶임에서 교체하는 swap_stuck_loss=true다.
- stop_max_pct=4.5%, profit_guard_arm_pct=3%, 이익반납30%, breakeven_lock_pct=1.5%다. 비용을 고려한 보호선은 가격 체결 보장이 아니다.
- stop_tf=240과 r_from_15m=true는 다른 용도다. 4시간봉 초기 손절용 ATR과 15분봉 R 정의를 혼동하지 않는다.
- 실거래 보호 점검 목표는 기본3초, 순차추적 확인 기본30초, 반복루프 sleep2초다. API·디스크·네트워크 지연 때문에 실시간 지연 상한을 보장하지 않는다.
- 실제 매수는 market_buy(ord_type=price), 실제 매도는 market_sell(ord_type=market) 경로다. 앞서 제안한 매수1호가 대기 지정가는 이 캡처에 구현되지 않았다.
- 2시간/4시간 정체 탈출은 exit_watch의 기록 모드다. 가상 체결을 실제 탈출 주문으로 해석하지 않는다.
- 보호 종목 상태를 별도로 읽어 저장한 비민감 목록은 BTC·DOGE·XRP이며, DOGE는 사용자 명시해제 전까지 보호한다. 사후 확인 목록이므로 코드 캡처 시각의 개인 상태 전체를 복원한 것으로 보지 않는다.
- 새 PC에서 봇을 켠다는 사실만으로 상대 PC 실행권을 가져오지 않는다. 인수 버튼과 별도 실행권 절차가 존재하며, 조회 전용 PC와 주문 PC를 구분한다.
- 자동 AI 반복 호출기는 폐기 상태다. 봇의 시세 조회·관찰 기록과 AI 토큰을 쓰는 자동 토론은 서로 다른 기능이다.

### 재현의 범위와 빠진 개인 상태
이 자료로 같은 버전의 프로그램과 공개 설정을 다시 구성할 수 있다. 그러나 같은 실거래 결과에는 동일한 입력 시세·호가·거래소 응답·시계·초기 장부·보호파일·미확정주문·실행권·손익기준이 추가로 필요하다. 이것은 개인 계좌 이사 자료가 아니다.
.env/API키/출금권한/실제 주문UUID/체결 원장/계좌 인수 식별자/세션 토큰/장부는 포함하지 않는다. 초기 상태와 외부 의존성이 없으면 같은 미래 수익을 재현할 수 없다.
정확한 계약이 서술에서 빠진 경우 함수 본문과 테스트가 들어 있는 runtime-source.zip에서 확인한다. 독립 재구현은 acceptance-cases.json과 실제 원본 테스트를 이용해 동일 입력·동일 출력 비교를 해야 한다.


### 구현상 남아 있는 과거 입력 경로
자동 AI 반복 호출기는 폐기됐지만 live.py의 과거 토론 문서 주문안/한도 파서는 코드에 남아 있다. AI 자동 호출 기능을 복원하라는 뜻이 아니다. 계좌/세션 상태 없는 재구현에서 과거 문서를 새 주문으로 재처리하지 않도록 last_plan·처리번호·pending의 영속 계약을 그대로 검증한다.

### 인수 시험 실행 방법
원본 코드와 공개 설정은 원문 보관본에서 복원하되 실거래는 꺼서 시작한다. 테스트 전용 임시 디렉터리에서 거래소 계좌/주문 함수를 가짜 응답으로 바꾸고 시간·시세·파일 경로를 고정한다. 원본 테스트 일부는 전역 공유 근거 파일 같은 외부 기록 경로를 사용하므로, 실제 계좌 폴더와 토론장에 연결한 채 전체 테스트를 실행하지 않는다. 이 문서 작업에서는 실제 원장을 쓰는 테스트나 거래 검증용 주문을 실행하지 않았다.

### 수정 후 문서 갱신 계약
동작 변경마다 새 스냅샷 디렉터리를 만들고 이전판은 보존한다. 코드 해시·기본값·덮어쓴 설정·동적파일 계약·관찰/실거래 기능 구분·테스트 결과를 함께 갱신한다. 현재 실행 상태는 이전 문서로 추측하지 말고 tools/ai_session.py status를 새로 실행한다. 작성자와 검토자를 구분하고 검토가 끝난 파일 해시를 기록한다. 수익 가설과 코드 동등성 검사를 별도 보고한다.


### 주문안 슬롯의 정확한 시간 규칙
shadow.py의 SLOT_SECONDS=3600, SLOT_DELAY=90, SLOT_MAX_LATE=600이다. 즉 매시 정각 이후 90초부터 600초 이내의 창에서 최신 신호가 준비되면 주문안을 한 번 생성한다. 종전 설명의 단순한 매시1분이 정확한 재현값은 아니다. 재시작 시 현재 슬롯은 이미 처리한 것으로 간주하여 다음 슬롯을 기다린다. 슬롯은 생성 전에 plan_slot.json에 선기록하며 생성 실패 뒤 같은 슬롯을 재생성하지 않는다. live의 proposal_max_age_seconds=120은 주문안 생성시각 이후 유효기간으로, 슬롯 지연 허용과 다른 조건이다. 회전 envelope도 신호시각·봉길이·정책해시·floor_slot을 별도로 검증한다.


## 1 목적과 재현의 의미
사용자가 원하는 것은 기존 계좌와 보유 종목을 인수해, 시장 상태를 보며 매수하고 위험과 이익을 관리하는 프로그램이다. 이 문서는 그 프로그램을 다른 사람 또는 AI가 다시 구현하거나 기존 구현을 검증할 때 사용할 기준이다. 수익성이 입증된 모델이라는 뜻은 아니다. 비용 이후 우위와 코드가 의도대로 움직이는지는 별도 문제다.
동일 프로그램 재현은 동일한 입력과 초기 상태에서 같은 후보·차단 사유·주문 의도·장부 변화를 만드는 것이다. 실제 체결은 거래소 대기열과 유동성에 의존하므로 같은 수익률을 보장하지 않는다. 설명만으로 모호한 경계는 원본 코드 및 원본 테스트를 기준으로 한다. 모든 소스는 해시와 함께 보관했다.

## 2 프로세스와 데이터 흐름
bot.py는 실거래 장부·보유 감시·사용자 요청·상태 화면을 관리한다. shadow.py는 여러 가상 전략을 관찰하는 별도 프로세스이고, 실거래 후보에 대한 주문안 data/proposal.json도 생성한다. shadow 자체는 거래소 주문을 전송하지 않는다. live.py의 Live가 주문안을 검증하고 upbit.py를 통해 실제 주문한다.
account_sync.py는 거래소 계좌와 현재가를 읽어 실제 계좌 평가 화면을 만든다. pnl.py와 pnl_service.py는 체결 내역과 기준 자료를 이용해 손익 표시를 지원한다. manual_orders.py는 127.0.0.1 전용 요청 서버이며 화면의 매수·청산·보호·관찰·갱신·실행권 요청을 봇으로 전달한다.
흐름은 공개 시세 → 완성 봉 구성 → 거래대금 유니버스 → 순위와 시장지수 → 후보·매도·교체·예산 계산 → 주문안 영속화 → 수신기 검증 → 주문 의도 저장 → 거래소 전송 → 조회·대사 → 장부 및 화면이다. 보호 감시는 이 흐름과 별도로 기존 보유분에 계속 작동한다.

## 3 입력과 시간 규약
시간 판단과 저장에는 epoch 또는 명시적 timezone이 있는 ISO시각을 사용하고 사용자 화면은 KST로 표시한다. 신호는 미완성 봉이 아니라 완성 봉을 사용한다. API가 반환하는 거래 없는 시간의 누락과 자료 부족을 실제 0원 가격으로 바꾸지 않는다. 마지막 봉과 기준 봉의 시각 정합성을 검사한다.
현재 tf=15분이다. 상대강도는 RS = 마지막 완성 종가 / 72봉 전 종가 − 1이며 BTC 대비 상대수익률이 아니다. 같은 수익률이면 종목코드로 정렬한다. ATR은 strategy.wilder_atr의 14기간 Wilder 계산으로, TR=max(고가−저가, |고가−이전종가|, |저가−이전종가|)를 사용한다. 최초 평균 이후 이전 ATR×13과 새 TR을 합쳐 14로 나눈다.
시장지수는 유효 종목의 n구간 수익률 단순평균이다. 마지막·기준 봉 시각이 가장 흔한 조합과 일치하는 종목만 사용하고 유효 종목이 최소20개 미만이면 None으로 판단한다. 현재 n=24라 6시간 수익률이다. 값이 양수인 경우만 신규 매수 허용을 검토하며, 0 이하·자료 부족·만료는 신규 매수 보류 사유가 된다. 다른 주문 조건을 대신하지 않는다.
근거: strategy.relative_strength, wilder_atr, market_index; upbit.completed_candles; shadow.hourly slot 관련 함수. 정확한 함수명 목록과 행 번호는 function-contracts.json을 따른다.

## 4 매수 후보와 차단
기본 유니버스는 원화 거래대금 상위30이며 스테이블 성격의 제외 목록은 합성 설정을 따른다. 현재 entry_scan=7, entry_want=4, slots=4다. 적격 후보라도 이미 보유한 종목, 사용자 보호, 자료 부족, 과열, 재진입 대기, 경보, 시장지수 차단, 자리·현금·예산 부족, 주문 한도 및 실행권 검사에 따라 매수하지 않을 수 있다.
과열 기본 상한은 strategy.chase_rs_cap의 15%다. 직접 지정과 수익 청산 뒤 재진입에 대한 종목별 상한·소비·초기화는 chase_override.cap_for, promote, consume, reset_cooled를 따른다. 관찰 지정과 과열 예외 지정은 다른 상태다. 예외가 시장지수·보호·실행권·현금 검사를 해제하지 않는다.
투자유의와 caution 세부 사유를 구분한다. 시세 변동·거래량·입금량 경보와 투자유의 지정은 동일하지 않으며, 실제 진입 허용 여부는 upbit.krw_markets 및 shadow/live의 검사 경로를 따른다. 경보 한 줄만 보고 모든 매수·매도가 막혔다고 해석하지 않는다. 기존 보유분 보호 매도와 신규 매수 차단을 분리한다.
수익 청산 뒤 기본2시간, 손실 청산 뒤 기본6시간의 재진입 대기가 있다. 24시간 창 안 수익 재진입에는 매도가 대비 1ATR 눌림 조건도 적용한다. rotation이 켜져 있으면 대기 종료 이후 새로운 완성 신호라는 fresh_reentry 조건을 추가 확인한다. 다만 chase_override.reentry_ok가 참인 수익청산 뒤 1회 재진입 예외는 대기·눌림 검사를 건너뛰는 별도 경로다. 이 예외의 사용 여부와 만료는 co의 상태를 재현해야 하며, 기본 대기만 무조건 적용하면 다른 봇이 된다. 과거 매도 상태가 복원돼야 재시작이 대기를 우회하지 않는다.
근거: strategy.plan/reentry_block/chase_block, chase_override, shadow.make_proposal, Live.buy_block/entry_chase_block, rotation_policy.fresh_reentry.

## 5 예산과 수량
현재 bot_capital_divisor=4.5가 켜져 있어 기준 매수 금액은 봇 운용자금÷4.5다. 운용자금은 원화와 봇 관리 가능 자산을 기준으로 계산하며 보호 종목 및 관리 대상이 아닌 소액은 별도 구분한다. 표시된 계좌 총자산을 그대로 매번 나누는 것으로 단순화하면 안 된다. shadow의 해당 계산이 최종 기준이다.
size_by_risk와 entry_risk도 설정에 남아 있으나 divisor 방식의 우선순위를 따른다. 기본 전략에는 위험예산/손절거리, 종목당 비중, 총투입 비중 계산이 따로 존재한다. 모의 장부와 실제 주문안의 예산 계산을 혼합하지 않는다. cash, min_order=5,000원, 실제 한도 및 수수료 여유를 확인한다.
현재 max_order_krw·max_total_buy_krw·max_sell_krw_per_plan은 각각10억원으로 사실상 높은 하드 한도다. 이것이 10억원 투자 지시라는 뜻은 아니다. 실제 운용 예산과 현금·자리·보호 조건은 계속 적용된다. 수량 변환은 upbit.vol8의 Decimal(repr(float(volume)))과 소수8자리 ROUND_HALF_EVEN이라는 현재 구현을 따르며, 99.9% 감액이나 일괄 내림으로 바꾸지 않는다.

## 6 매도와 보유 관리
rank_exit=false이므로 순위 이탈이나 상대강도 약화만으로 기존 보유분을 즉시 전량 청산하지 않는다. 과열로 신규 매수를 보류하는 것과 기존 보유분을 파는 것도 다르다. swap_stuck_loss=true에서는 매수 후보가 있는데 자리 또는 현금이 부족하면 손실 중인 묶임 종목의 부족분 교체를 검토한다. 보호·순위·부족액·최소주문·잔량 규약을 shadow와 Live 실행에서 함께 검사한다.
초기 손절에는 k=3 및 stop_max_pct=4.5% 상한이 있다. stop_tf=240의 손절용 봉과 r_from_15m=true의 R 계산은 분리한다. R은 k×15분 ATR을 사용할 수 있지만 손절용 ATR이 같은 값이라는 뜻은 아니다. 인수분의 손절 재산정, 일회 rebase, 진입 이후 올라간 보호선은 별도 영속 필드를 사용한다.
이익보호는 실제 관측 고점을 사용한다. 원가/잔량을 유효 기준가격 E로 삼고 관측고점 H가 E×1.03 이상이면 새 보호선=max(기존보호선, E×비용바닥, E+(H−E)×0.70)로 올린다. 비용바닥은 코드상 1+2×전략수수료+종목가격비용이다. 보호선은 내리지 않는다. 그 이전에 H≥E×1.015이면 본전 잠금으로 비용바닥을 올릴 수 있다. 과거 전체 차트 고점을 나중에 가져와 소급 적용하지 않는다.
trail_check는 확정1분봉 기반 추적/일부 익절을 별도로 검사한다. partial_enabled=true, partial_r=2, partial_frac=0.3333이다. 한 번 실행한 부분익절을 다시 실행하지 않도록 part_done 등을 저장하며 매도량·잔량이 최소 주문을 충족해야 한다. stop_limit은 기본 stop과 활성 profit_guard_stop 중 유효한 높은 선을 고려한다. 실제 순서는 bot.run이 stop_check를 먼저 호출하고 순차적 trail_check를 뒤에 호출하는 구조다.
일일 손실은 Live.check_loss의 실현·미실현·기준자산과 보호종목 제외 규칙으로 계산한다. loss_stop_pct=0.8%, loss_stop_krw=15,000원 설정을 적용한다. 저장된 기준과 금액 한도의 조합·자료누락 처리·날짜 초기화·재개요청은 원본 함수 계약을 그대로 따른다. 신규 매수 중단과 기존 보호 매도 중단은 같은 행동이 아니다.
근거: Live.stop_check/update_profit_guard/stop_limit/trail_check/check_loss/check_resume_request/rebase_adopted_stops, strategy.ratchet, rotation_policy.saved_loss_budget.


### 보호선과 설정 우선순위 보충
Live.stop_limit의 기준은 max(stop, stop0)이고 유효한 이익보호선이 활성화돼 있으면 그 값과 다시 max를 취한다. 실제 청산가는 보호선보다 불리할 수 있다. 주요종목 비용 분류는 strategy.MAJORS의 BTC·ETH이며 c=0.001, 기타는 c=0.002다. fee=0.0005를 쓰므로 비용바닥 배수는 각각1.002와1.003이다. E 자체가 cost/qty일 수 있으므로 거래소 평균매수가와 항상 같은 값은 아니다.
Live 설정 우선순위는 코드 DEFAULTS → config.live → data/live/live.json의 limits다. effective-settings.json은 앞의 두 층을 합성한 값이다. 장부 전체를 문서에 복사하지 않고 한도 필드만 별도 runtime-limit-overrides.snapshot.json으로 확인했으며 이번 확인에서는 덮어쓴 한도 키가 없었다. 동적 보호·직접지정·계좌 상태가 별도로 존재한다.

## 7 주문 상태와 장애 처리
실제 매수는 원화 금액을 지정하는 시장가 ord_type=price, 매도는 수량을 지정하는 시장가 ord_type=market이다. 이번 캡처에는 기다리는 매수1호가 지정가 주문 기능이 없다. 주문을 내기 전의 실행권·보호·현재 잔고·호가 차이·동일 주문안 재사용·기간 만료를 검사한다. max_spread=1%는 실제 호가 검사 조건이며 거래당 비용이 항상1%라는 뜻은 아니다.
주문 상태는 의도 생성 → 전송 전 영속저장 → 제출 → 응답 확정 또는 불명 → 거래소 조회 → 부분/전체 체결 반영 → 잔량·최종 상태 대사이다. 네트워크 오류가 주문 실패를 뜻하지 않으므로 identifier로 조회해 이미 접수됐는지 확인하기 전 같은 경제적 주문을 새로 전송하지 않는다. 거래소 거절과 불확정 응답은 다른 오류다.
부분 체결은 실제 수량·대금·수수료만 반영한다. 실제 거래소 가용 수량과 봇 원가 장부를 구분한다. 이미 잠긴 수량은 다시 매도하지 않는다. 자투리를 화면에서 숨기거나 관리에서 제외해도 거래소에서 소멸한 것이 아니다. 실제 자투리 정리 기능은 명시적인 요청 경로가 존재하며 DUST_BUY_KRW=5,500원의 추가매수 후 정리 행동이 포함될 수 있으므로 조회·숨기기와 혼동하지 않는다.
합의 문서의 옛 주문안/한도 파서는 live.py에 남아 있으나 자동 AI 반복 토론을 다시 시작하는 기능은 아니다. last_plan·처리 의도·pending을 유지해 과거 문서를 새 주문으로 재실행하지 않도록 해야 한다.
근거: Live._trade/poll_proposal/execute, upbit.market_buy/market_sell/_post_order/get_order_by_identifier, reconcile, reconciliation_guard.

## 8 사용자 보호와 PC 실행권
사용자가 보호한 종목은 자동매수·추가매수·순환매도·손절·익절·화면 매매에서 제외한다. DOGE는 명시 해제 전까지 보호한다. 이번 비민감 목록 확인에서는 BTC·DOGE·XRP가 포함됐다. 사용자 직접 주문은 auto_manual_protection에 의해 별도로 식별·보호하며, 명시적 해제와 자동 보호 기록의 병합·revision 경합 처리는 asset_protection.Store를 따른다. 보호 파일이 필수인데 없거나 잘못된 경우 거래 허용으로 처리하지 않는다.
회사/집 PC는 모두 계좌를 읽을 수 있지만 같은 계좌의 주문권은 하나다. bot 시작 시 다른 PC 권한을 자동 인수하지 않는다. UI의 실행권 요청·인수는 별도 절차이며 기존 소유자 중단과 장부 체크포인트, 미확정 주문, 로컬 상태를 대사해야 한다. 실행권은 Git 원격의 별도 레퍼런스 및 compare-and-swap 갱신을 사용한다. Git의 코드 main과 실행권 데이터는 목적이 다르다.
네트워크 단절시 갱신·검증 여부와 매수/위험청산 허용은 SessionGuard.assert_owner/check_now의 조건을 따른다. 일반적인 '네트워크가 끊기면 모두 허용/모두 금지'로 재구현하지 않는다. 현재 SELL_BLIND_MAX=3시간으로, 저장소 확인불가 상태에서 마지막으로 확인된 실행권자는 조건 충족 시 매도만 제한적으로 허용하고 신규 매수는 막는다. 명시적 타PC 소유권 확인과 단순 통신 장애를 구분한다. 백그라운드 실행권 확인은 기본30초이며 주문 직전에 다시 검사한다. 실행권 상태는 acquired/lost 및 관찰 상태·유효기한·blind_since 등과 함께 취급한다. 새 PC 코드 다운로드만으로 개인 장부와 보호선·손익 기준이 이전되지 않는다.
계좌 인수는 기존 봇 이력 복원과 다른 행동이다. adopt_account는 새 기준을 만들 수 있고 continuity는 거래소 이력에서 재진입 기록·원가 기반 상태를 복원한다. 완전한 과거 고점·ATR·부분익절 상태가 복원된다고 가정하지 않는다.

## 9 계좌 화면과 관찰 기록
실제 계좌 화면은 주문 가능 잔고와 잠금 잔고, 평균매수가, 현재가, 평가액, 손익·수익률을 구분한다. 미평가 자산은 수량을 보존하고 합계 산정 범위를 표시한다. 실현손익은 거래소 주문 이력 및 별도 기준자료에서 계산하며 입출금·수동 거래·계좌 인수 때문에 봇 장부 손익과 같다고 가정하지 않는다. 사용자가 본전 잠금/손절을 눌렀다는 사실도 실제 체결 확인을 대신하지 않는다.
상대강도 순위의 보유·경보·과열·투자 대상·관찰은 표시 상태다. 관찰 자체는 주문이 아니다. 과열 직접 지정, 개인 보호, 실행권, 계좌 매매 버튼은 서로 다른 기능이다. localhost 요청 서버는 토큰과 요청 종류·Host/Origin 등을 검사하며 기본 포트 사용불가시 대체 포트로 열 수 있다. 표시 오류가 곧 주문 엔진 오류라는 뜻은 아니다.
missed_ledger는 후보와 차단 원인을 기록하고, exit_watch는 매수2/4시간 뒤 정체 조건과 본전 지정가를 가정한 결과를 기록한다. 실제 주문을 내지 않는다. market_state/regime_compare/entry_flags/notices/caution_study 등의 장세·관찰·공지 지표도 화면 설명과 거래 차단의 실제 연결을 구분한다. 주기적인 공개 API 수집은 AI 토큰을 사용하는 토론 호출과 다르다.

## 10 재구현 순서와 의사코드
1. 소스 해시와 합성 설정을 고정하고, 거래소 어댑터와 가짜 시간·시세·주문 응답을 만든다. 처음에는 실제 주문을 차단한다.
2. 완성 봉·RS·ATR·시장지수를 순수 함수로 재현하고 원본과 수치 및 시각 경계를 비교한다.
3. 보호 목록과 계좌 분류를 불러오고 사용자 자산을 먼저 분리한다. 초기 장부·pending·처리한 슬롯·최근 매도 기록을 복원한다.
4. 정규 슬롯에서 후보·차단·예산·교체를 계산해 주문안을 선기록하고, 수신기는 소유권·정책해시·신호시각·유효기간·중복 의도를 검증한다.
5. 실제 주문 전 모든 제한을 다시 검사하고 의도를 저장한다. 응답 불명이면 조회하고, 부분 체결은 실제 체결만 반영한다.
6. 보유 감시에서 실시간 손절·이익보호를 먼저, 확정봉 추적과 부분익절을 다음에 처리한다. 보호 자산은 건너뛴다.
7. 화면·손익·관찰 로그를 갱신하고, 정지 시 미확정 상태를 남겨 재시작 대사를 가능하게 한다. 테스트 결과와 검토 해시가 일치한 버전만 다음 단계로 옮긴다.

## 11 인수 시험과 운용 재현
acceptance-cases.json의20개 시나리오는 새 구현의 인수 기준이다. source ZIP에는 원본 테스트도 포함돼 있다. 이번 문서 작성에서 전체 테스트를 실행한 것은 아니다. 입력뿐 아니라 API 실패·불확정 응답·부분 체결·시간 역행·자료누락·재시작·동시PC·사용자 보호 변경 등 비정상 경로를 비교해야 한다.
동일 시세와 장부를 두 구현에 넣어 후보·차단 사유·주문 의도·전송수량·원가·손익·보호선을 비교한다. 실제 거래 내역 재현과 미래 수익성 검증을 분리한다. 주어진 OHLC에서 봉 안 체결순서나 대기열을 알 수 없는 경우 미확인 또는 보수적 가정으로 표시한다.

## 12 연구 상태와 변경 관리
2시간 정체 지정가 탈출, 4시간 비교, 빠른 장세 브레이크, 약세장 보유 축소, 구조적 청산, 매수1호가 지정가 진입 등은 개별 연구·제안 상태를 확인해야 하며 이 문서의 실제 기능으로 자동 편입하지 않는다. exit_watch의 가상체결은 자기 주문 체결이나 수익성의 증명이 아니다. 과거 연구 결과에는 생존편향·동일 자료 탐색·봉 해상도·체결 가정과 코드 차이가 있다.
매 규칙 변경마다 새 캡처 시각·코드 해시·설정·변경 이유·작성자·검토자·검사 결과를 남기고 이전판을 보존한다. 기본값을 바꾸는 것, 실행 설정을 바꾸는 것, 현재 장부를 바꾸는 것은 각각 다른 변경이다. 한도의 활성값과 상대검토가 없는 제안을 문서 서술만으로 실거래에 켜지 않는다.
재현 제약은 비공개 초기 계좌 상태, 외부 거래소 응답, 세션 소유권, 네트워크·시간 지연, 동적 보호·직접 지정 파일이다. 원본 코드와 공개 설정은 보관돼 있지만 개인 장부를 포함하지 않으므로 이 기록만으로 현재 계좌를 그대로 복구하는 것은 아니다.


## 재구현 인수 시험 목록

다음은 새 구현이 반드시 확인해야 할 관찰 항목이다. 이번 문서 작성에서 실행한 테스트가 아니다. 정확한 세부 입력과 출력은 소스 보관본의 테스트와 함께 고정한다.

|ID|상황|검증할 관찰|

|---|---|---|

|T01|15분봉 72구간 상대강도|마지막 종가/72봉전 종가−1; 동률은 종목코드순|

|T02|장세 자료 부족|동일 시각 정렬된 유효 종목 20개 미만이면 신규 매수 보류|

|T03|6시간 필터 경계|현재 regime_bars24: 15분×24=6시간; 지수0이하 보류, 초과만 진입 가능|

|T04|사용자 보호|DOGE 및 사용자 보호 종목은 자동/수동UI 매수·매도·손절·익절에서 제외; 표시·조회만|

|T05|계좌 보호 파일 오류|필수 보호파일 누락/형식오류에서 거래 허용하지 않음; 실제 오류 경로는 스냅샷 소스와 비교|

|T06|중복 주문안|같은 슬롯·정책해시·의도는 중복 주문하지 않음; 재시작 후에도 영속 기록으로 확인|

|T07|주문 응답 불명|재전송 전에 identifier로 거래소 조회; 거래소 상태를 모른 채 새 주문 금지|

|T08|부분 체결|실제 체결수량·대금·수수료만 원가와 잔고에 반영; 남은 수량과 잠금을 구분|

|T09|동일 계획 매도 후 재매수|매도 직후 같은 신호를 재사용해 재진입하지 않음; fresh_reentry와 수신기 검사|

|T10|이익 보호|고점+3%에서 이익70% 보호선 계산; 보호선은 낮추지 않음; 체결가 보장은 아님|

|T11|본전 잠금|고점+1.5%에서 비용 고려 바닥 계산; 이미 더 높은 보호선이면 내리지 않음|

|T12|순위 이탈|rank_exit=false에서 단순 순위이탈은 청산하지 않음; swap_stuck_loss와 구분|

|T13|부분 교체|자리/현금 부족·적격 매수 후보·손실 묶임 조건에서만 부족액 기반 정리; 사용자 보호 제외|

|T14|계좌 손실 한도|보호종목 제외 범위·실현/미실현 합성·일일초기화와 재개요청 경로를 동일하게 재현|

|T15|PC 실행권|권한없는 PC는 조회만; 기동만으로 다른PC 권한을 빼앗지 않음; 인수 버튼·기존종료·체크포인트 검사|

|T16|정체 탈출|exit_watch가 가상체결 기록을 내더라도 upbit 실제주문 호출수는 증가하지 않음|

|T17|자투리|5천원미만 잔량은 별도 기록; 전량매도 수량은 vol8 및 avail/장부 구분에 따라 계산; 숨김과 소멸은 다름|

|T18|감시 지연|3초는 목표 점검주기; 동기 API호출·네트워크 지연으로 초과 가능. 실제지연을 측정하고 보장이라고 쓰지 않음|

|T19|미평가 자산|시세없는 자산은 수량 보존·미평가 표시; 0원 확정평가로 오해하지 않음|

|T20|자동 토론|주문·기록 프로세스와 AI 반복 호출은 별개; 폐기된 토론 호출기를 복원하지 않음|

## 부록 코드와 설정

이 부록은 실행 소스 스냅샷에서 기계적으로 추출한 값이다. 명세 본문보다 최신 사실을 자동으로 반영하는 파일은 아니며 동일한 캡처 버전이다.

## 실제 적용 설정

설정 우선순위는 기본값에 config.json의 해당 섹션을 덮어쓴 값이다. 그 밖의 동적 파일 및 실행 중 상태는 별도 계약을 따른다. 계좌 인수 식별자는 비공개라 마스킹했다.

### strategy

|키|기본값|설정에 지정한 값|합성 값|

|---|---|---|---|

|tf|15|15|15|

|stop_seconds|30|30|30|

|L|72|72|72|

|M|5|9|9|

|k|3.0|3.0|3.0|

|slots|3|4|4|

|entry_top|3|3|3|

|universe|30|30|30|

|risk|0.002|0.002|0.002|

|coin_cap|0.25|0.25|0.25|

|total_cap|0.5|0.5|0.5|

|min_order|5000.0|"<기본값 사용>"|5000.0|

|fee|0.0005|"<기본값 사용>"|0.0005|

|cost_major|0.001|0.001|0.001|

|cost_other|0.002|0.002|0.002|

|reentry_hours|24.0|24|24|

|require_positive_rs|true|true|true|

|chase_rs_cap|0.15|"<기본값 사용>"|0.15|

|stop_max_pct|0.08|0.045|0.045|

|entry_risk|0.0|0.00184|0.00184|

|size_by_risk|false|true|true|

|bot_capital_divisor|0.0|4.5|4.5|

|entry_want|0|4|4|

|entry_scan|0|7|7|

|reentry_profit_hours|2.0|"<기본값 사용>"|2.0|

|reentry_loss_hours|6.0|"<기본값 사용>"|6.0|

|reentry_dip_atr|1.0|"<기본값 사용>"|1.0|

|reentry_window_hours|24.0|"<기본값 사용>"|24.0|

|trail_enabled|false|"<기본값 사용>"|false|

|trail_lock_r|1.0|"<기본값 사용>"|1.0|

|trail_start_r|2.0|"<기본값 사용>"|2.0|

|trail_m|2.0|"<기본값 사용>"|2.0|

|giveback|0.5|"<기본값 사용>"|0.5|

|regime_filter|false|true|true|

|regime_bars|96|24|24|

|regime_min_markets|20|20|20|

|halt_new_buys|false|false|false|

|regime_expires|0.0|0|0|

|rotation_enabled|false|true|true|

|rotation_confirmations|1|1|1|

|rank_exit|true|false|false|

|swap_stuck_loss|false|true|true|

|exclude|["KRW-USDT", "KRW-USDC", "KRW-USDE", "KRW-USD1", "KRW-DAI"]|"<기본값 사용>"|["KRW-USDT", "KRW-USDC", "KRW-USDE", "KRW-USD1", "KRW-DAI"]|

### live

|키|기본값|설정에 지정한 값|합성 값|

|---|---|---|---|

|enabled|false|true|true|

|auto_proposal|false|true|true|

|max_order_krw|30000|1000000000|1000000000|

|max_total_buy_krw|100000|1000000000|1000000000|

|max_sell_krw_per_plan|300000|1000000000|1000000000|

|loss_stop_krw|15000|15000|15000|

|max_spread|0.01|0.01|0.01|

|stop_tf|240|240|240|

|trail_enabled|false|true|true|

|trail_adopted|false|true|true|

|adopted_trail_start_r|1.0|1.0|1.0|

|loss_daily|false|true|true|

|loss_stop_pct|0.0|0.008|0.008|

|partial_enabled|false|true|true|

|partial_r|2.0|2.0|2.0|

|partial_frac|0.3333|0.3333|0.3333|

|manual_ui|false|true|true|

|manual_port|8765|"<기본값 사용>"|8765|

|manual_buy_max_krw|100000|"<기본값 사용>"|100000|

|profit_guard_enabled|false|true|true|

|profit_guard_arm_pct|0.02|0.03|0.03|

|profit_guard_giveback|0.4|0.3|0.3|

|protected_markets|[]|"<기본값 사용>"|[]|

|auto_manual_protection|false|true|true|

|protection_ui|false|true|true|

|restore_continuity|false|true|true|

|r_from_15m|false|true|true|

|concurrent_warn_at|6|"<기본값 사용>"|6|

|proposal_max_age_seconds|"<없음>"|120|120|

|user_protection_required|"<없음>"|true|true|

|slot_plans|"<없음>"|true|true|

|breakeven_lock_pct|"<없음>"|0.015|0.015|

## 모듈과 함수 계약 색인

각 함수의 실제 본문·순서·예외 처리는 보관된 runtime-source.zip이 최종 근거다. 코드 해시는 source-manifest.json에 있다. 이 문서는 동작 설명이며 테스트 통과 또는 수익성 인증서가 아니다.

### account_sync.py

Read-only, independent account display; no order methods are called.

- `snapshot()` (행 19): 

- `atomic(path, content)` (행 39): 

- `performance(assets)` (행 45): Unrealized return on holdings with both a price and a known positive cost.

- `render(data, error='')` (행 54): 

- `run()` (행 104): 

### adopt_account.py

Explicitly delegated all-holdings adoption. A new baseline, not restored history.

- `inventory(rows)` (행 20): 

- `open_orders()` (행 24): 

- `build_state(rows, quotes, listed, atrs, previous, params, epoch, adoption_id)` (행 37): 

- `main()` (행 67): 

### asset_protection.py

User-owned asset protection policy. No exchange or order access.

- `ProtectionError` (행 19): 

- `normalize(value)` (행 23): 

- `validate(doc)` (행 31): 

- `effective(doc, configured=())` (행 76): 

- `explicit_release(doc, market)` (행 85): 

- `merge(left, right, left_configured=(), right_configured=())` (행 91): Latest explicit choice wins; equally dated/unknown conflicts favor protection.

- `_decimal(value)` (행 119): 

- `Policy` (행 126): 

  - `Policy.__init__(self, file, configured=(), required=True, clock=time.time, lock_timeout=1.0)` (행 127): 

  - `Policy.read(self)` (행 133): 

  - `Policy.locked(self)` (행 146): 

  - `Policy.mutate(self, change)` (행 184): 

  - `Policy.revision(self, market, doc=None)` (행 200): 

  - `Policy._choice(self, doc, market, protected, origin)` (행 203): 

  - `Policy.decide(self, market, protect, origin='user_ui', expected=None)` (행 209): 

  - `Policy.observe(self, orders=(), bootstrap=(), user_identifiers=())` (행 219): 

### bot.py

자동거래 v5 1판: C안 모의 운영. 실제 주문 기능 없음.  python bot.py          모의 운영 (판단 봉 마감마다 순환 판단, 30초마다 실시간 손절 확인) python bot.py --check  지금 시세로 순위·계획만 출력 (장부 변경 없음)

- `load_params()` (행 46): 

- `boundary(ts, period)` (행 52): 

- `utc(ts)` (행 56): 

- `ranking_rows(ranking, held, warned, p, esc=html.escape, promoted=(), hot=(), can_promote=False, stuck=None)` (행 60): 순위표 행. 바탕색: 보유=하늘색, 매수 불가 경보=붉은 파스텔, 투자 대상(매수 후보)=연두, 관찰=연노랑. '구분' 칸에도 적는다. 매수 후보 = 현재 매수 규칙(상위 entry_top 안, 상승률 0 초과 chase_rs_cap 미만, 보유·매수 불가 경보 아님). 관찰 = 5위(유지 순위) 안 정상 종목. warned: {종목: 경보 정보} (집합이면 모두 매수 불가로 본다). 사용자 지시(2026-11-04): 경보 종목 중 매수 가능하면 '매수 후보'와 경보 이유를, 불가하면 '매수 불가(경보 이유)'를 적는다.

- `stuck_html(stuck, esc=html.escape)` (행 98): 묶인 보유: 예전 규칙이면 순위로 팔았을 종목. 순위 매도 없이 손절·이익보호로만 판다(사용자 지시 2026-11-05).

- `fmt_price(x)` (행 111): 가격 표시: 지수 표기(1.047e+08) 없이 자릿수에 맞춰 쓴다.

- `holdings_table(rows, protected, stuck, positions, sell_line, esc=html.escape, min_value=5000)` (행 122): 보유 종목 한 표(실제 계좌 평가·원가 + 봇 장부 매도선 + 보호·묶임 구분). 5,000원 미만 자투리는 개수만 적는다. rows: pnl 요약의 unrealized_rows. sell_line(m) -> (이름, 가격) 또는 None.

- `useful_events(rows, limit=8)` (행 158): 실거래 기록 중 사람이 볼 것만: 체결(매수·매도 금액), 주문안 결과, 이익보호·본전 잠금 시작. 내부 기준 기록('0원' 줄)은 뺀다.

- `promote_script(api)` (행 179): 순위표 '매수 대상으로'/'지정 취소' 버튼. 서버가 없으면 버튼이 나오지 않는다.

- `Bot` (행 192): 

  - `Bot.__init__(self)` (행 193): 

  - `Bot.log(self, msg)` (행 208): 

  - `Bot.align_timeframe(self, now)` (행 212): Preserve holdings on a timeframe change and wait for the next new close.

  - `Bot.init_t0(self)` (행 227): 

  - `Bot.candidates(self)` (행 247): 

  - `Bot.fetch_bars(self, markets, end)` (행 254): 

  - `Bot.rebalance(self, end)` (행 264): 

  - `Bot.apply(self, name, lg, ranked, entry_ok, end, bars, uni)` (행 309): 

  - `Bot.stop_check(self, lg, live)` (행 357): 실시간 손절: 현재 체결가가 손절가 이하이면 최우선 매수호가로 매도 (사용자 의견으로 봉 마감 대기 폐지).

  - `Bot.prices(self, lg=None)` (행 368): 최신 체결가. 응답에 빠진 종목은 마지막 관측값을 쓰고 price_missing에 기록한다.

  - `Bot.refresh_real(self)` (행 380): 실제 업비트 계좌 조회 (주문 없음).

  - `Bot.refresh_display(self, next_close)` (행 398): Refresh exchange balances independently of strategy/ledger snapshots.

  - `Bot.sweep_dust_request(self)` (행 407): data/dust_sweep.json(["KRW-..."] 목록)이 있으면 한 번 자투리 정리를 하고 결과를 dust_sweep_result.jsonl에 남긴 뒤 요청을 지운다.

  - `Bot.can_act(self)` (행 422): 이 PC가 실행권을 가졌나(캐시된 판단). 실제 주문 직전에는 session_guard가 GitHub에서 다시 확인한다.

  - `Bot.grant_status(self)` (행 429): 

  - `Bot.request_grant(self)` (행 435): 화면 '이 PC에 실행권 받기' 버튼: 이전 도구(take_over)를 별도 프로세스로 시작한다. 이 봇은 도구가 멈추고 다시 켠다.

  - `Bot.ownership_html(self)` (행 450): 실행권 램프와 '이 PC에 실행권 받기' 버튼. 램프: 초록=이 PC, 빨강=다른 PC/없음, 노랑=확인 불가.

  - `Bot.pnl_summary(self)` (행 492): 

  - `Bot.holding_rows(self, s)` (행 499): 보유 종목 표의 행. 수량·평가액은 5초마다 갱신되는 실제 계좌 조회(account_live.json)를 기준으로 한다. 원가는 수량이 일치하는 것 중 손익 집계(pnl, 5분 주기) → 봇 장부 → 업비트 평균 매입가 순서로 고른다. 계좌 조회가 없거나 2분 넘게 멈췄으면 손익 집계 행을 그대로 쓴다.

  - `Bot.sell_line(self, m)` (행 530): 봇 장부 종목의 지금 매도선: (이름, 가격). 손절·이익보호·본전 중 실제로 먼저 걸리는 선.

  - `Bot.live_block(self, prices)` (행 542): 

  - `Bot.real_block(self, prices)` (행 599): 

  - `Bot.block(self, title, lg, prices)` (행 631): 

  - `Bot.held_markets(self, prices)` (행 659): 평가 5,000원 이상 보유 종목(실제 계좌 + 봇 관리 장부).

  - `Bot.pnl_block(self)` (행 665): 실현손익 관리(D-56). pnl.py가 거래소 체결로 계산한 data/pnl/summary.json을 보여 준다. 접기·펼치기 상태는 브라우저에 기억한다.

  - `Bot.write_status(self, prices, next_4h)` (행 708): 

  - `Bot.run_reviews(self)` (행 815): 급변 표시 효과를 정해 둔 기준으로 판정해 화면 상단 공지로 알린다. 판정만 하고 주문·설정은 바꾸지 않는다.

  - `Bot.notice_action(self)` (행 840): 

  - `Bot.run(self)` (행 845): 

  - `Bot.manual_action(self)` (행 961): 화면 청산 버튼용 접속 정보. 서버가 꺼져 있으면 None이라 버튼이 나오지 않는다.

  - `Bot.protection_action(self)` (행 967): 

  - `Bot.handle_manual_requests(self)` (행 971): 화면에서 확인된 강제 청산 요청을 메인 루프에서 실행한다. 서버 스레드는 주문을 내지 않는다.

  - `Bot.check(self)` (행 1037): 

- `single_instance()` (행 1052): 

### caution_study.py

D-59 합의: 경보(주의) 종목 진입 그림자 비교. 주문 기능 없음, 실거래와 분리된 별도 프로세스.    C0_경보차단  현행: 투자유의·주의 항목이 하나라도 있으면 신규·재매수 차단   C1_주의허용  주의 항목이 가격급변·거래량급등뿐인 종목을 아래 조건으로 허용 청산은 두 군 모두 현행 실거래 규칙(exit_study G0와 같음). 장부·청산·재시작·보고는 exit_study를 그대로 쓴다.  C1 주의 종목 매수 조건(합의 원문 debates/D-59/03): - 투자유의·해외가격차·소액계좌집중·입금량급등·경보 정보 형식 불명은 제외 - 기존 매수 규칙 전부(shadow.proposal) + 그 판단의 호가 차이 ≤ 0.4% + 최근 1시간(15분봉 4개) 종가 상승 > 0 - 금액은 proposal 금액의 1/2(최소 주문 미달이면 증액하지 않고 건너뜀), 주의 종목 동시 1개 - 주의 종목 묶음의 실현손익 + 진행 중 평가손익이 −1만 원 이하가 되면 C1의 주의 종목 신규 진입 중단 - 매수 시 호가 차이를 기록한다(왕복 비용 검토용)  python caution_study.py            실행 python caution_study.py --report   보고서만

- `caution_allowed(info)` (행 47): 주의 항목이 가격급변·거래량급등뿐이고 투자유의가 아니면 True. 정보가 없거나 형식이 다르면 False.

- `rise_1h(bars)` (행 55): 

- `pilot_pnl(book, prices)` (행 59): 주의 종목 묶음의 실현손익 + 진행 중 평가손익(청산가치).

- `CautionStudy` (행 70): 

  - `CautionStudy.__init__(self, folder=None)` (행 71): 

  - `CautionStudy.decide(self, end)` (행 89): 

  - `CautionStudy.caution_report(self, prices)` (행 170): 

- `report()` (행 183): 

### chase_override.py

과열 상한 예외 (사용자 지시 2026-11-05: 오른 종목을 끝까지 들고 가기).  1. 사용자 직접 지정(1회 권한, 사용자 지시 2026-11-05 16:30에 24시간 유지를 없앰): 18시간 상승률이 과열 상한(15%) 이상 EXT_CAP(20%) 미만인    종목을 화면에서 '매수 대상'으로 올리면 그 종목의 상한을 EXT_CAP으로 본다. 시간 제한은 없고, ① 그 종목을 한 번 사면(첫 체결) 권한이 사라지며    ② 상승률이 과열 상한 아래로 내려와 과열이 풀리면(순위 밖으로 빠진 경우 포함) 권한이 사라진다. 다시 과열이 되면 원래 규칙(15%)을 적용한다. 2. 이익 청산 뒤 재진입: 이익으로 전량 매도한 종목은 REENTRY_HOURS 안에 한 번, 상한 EXT_CAP으로 다시 살 수 있다.    재진입 대기·눌림 조건(reentry_profit_hours, reentry_dip_atr)은 이 한 번에는 적용하지 않는다. 재진입으로 산 물량을 판 뒤에는 다시 주지 않는다. 3. 과열 보유: 보유 중 18시간 상승률이 과열 상한 이상이 된 종목은 순위 매도(유지 순위 밖·상대강도 음수·거래대금 상위 밖)를 하지 않고    손절·이익보호로만 판다. 매도 뒤에는 표시를 지운다.  파일: data/chase_promoted.json(봇 화면이 쓴다), data/hot_held.json(주문안 생성기가 쓴다). 쓰는 쪽이 하나씩이라 서로 덮지 않는다. 읽기 실패·형식 오류는 '예외 없음'으로 본다(기존 15% 규칙으로 돌아간다).

- `_read(path)` (행 26): 

- `_write(path, doc)` (행 34): 

- `_finite(x)` (행 41): 

- `ext_cap(p)` (행 45): 예외 종목에 쓰는 상한. 과열 상한이 꺼져 있으면(0) 예외도 없다.

- `promoted(data_dir, now=None)` (행 51): 지금 유효한 직접 지정 종목 {종목: 지정 시각}. 시간 제한은 없다(사면 사라지고, 과열이 풀리면 지워진다).

- `consume(data_dir, market)` (행 57): 직접 지정 종목을 샀다: 1회 권한을 쓴 것으로 지운다. 지울 게 없으면 False.

- `reset_cooled(data_dir, rs_of, p)` (행 68): 상승률이 과열 상한 아래로 내려왔거나 순위 정보에서 사라진 종목의 직접 지정을 지운다(원래 규칙으로 되돌림). 지운 종목 목록.

- `promote(data_dir, market, rs, p, now=None)` (행 83): (성공 여부, 안내문). 상승률이 과열 상한 이상·EXT_CAP 미만일 때만 올린다.

- `unpromote(data_dir, market)` (행 97): 

- `reentry_ok(last, now=None)` (행 108): 이익 청산 뒤 한 번 허용되는 재진입인가.

- `cap_for(market, p, promoted_markets, last, now=None)` (행 116): 이 종목에 적용할 과열 상한.

- `hot_held(data_dir)` (행 123): 

- `write_stuck(data_dir, stuck)` (행 128): 묶인 보유(예전 규칙이면 순위로 팔았을 종목) 표시용. 주문 판단에는 쓰지 않는다.

- `read_stuck(data_dir)` (행 133): 

- `update_hot_held(data_dir, held, rs_of, p)` (행 138): 보유 중 과열 상한 이상이 된 종목을 표시하고, 더 이상 보유하지 않는 종목은 지운다. 반환: 표시된 종목 집합.

### continuity.py

PC를 옮겨 계좌를 새로 인수해도 봇 자신의 거래 기억(재진입 기록·평단 기반 손절 기준)을 거래소 체결 이력에서 복원한다.  사용자·거래소가 기록한 사실만 쓴다: 봇 주문은 식별자가 `v5-`로 시작한다. 확인이 부족하면 아무것도 복원하지 않는다(보수적). 순수 함수라 시험할 수 있다. 호출은 live.Live.restore_continuity().

- `_epoch(iso)` (행 14): 

- `_num(x)` (행 18): 

- `replay_bot_orders(rows)` (행 26): 거래소 주문 목록(rows)을 시간순으로 재생한다.  반환 {market: {'qty','cost','last_sell','mixed','open','known_basis'}} - qty·cost: 봇 체결만으로 만든 평균단가 재생(매수는 체결금액+수수료, 매도는 평균단가 비례 차감) - last_sell: {'time','price','profit'} 마지막 봇 매도. 매도 원가를 알 수 없으면 profit=False(수익 청산으로 추정하지 않음) - mixed: 같은 종목에 봇이 아닌 체결이 하나라도 있음 / open: 미체결 주문이 있음 / known_basis: 모든 매도 원가가 재생으로 확인됨

- `restorable_last_sell(replay_entry, existing, now)` (행 88): 장부에 넣을 last_sell(없으면 None): 7일 안이고 장부 기록보다 새로운 봇 매도.

- `bot_basis(replay_entry, ledger_qty)` (행 100): 인수받은 보유가 전부 봇이 산 물량임이 이력으로 입증되면 평균 매수가, 아니면 None.  조건: 비봇 체결 없음, 미체결 없음, 모든 매도 원가 확인, 창 안 첫 봇 체결이 매수(처음부터 재생), 재생 수량이 장부 수량과 0.1% 이내로 같음. 주문 시각은 거래소가 주는 생성 시각이다(시장가는 거의 즉시 체결). 조회 창(7일) 이전의 보유가 섞이면 첫 체결이 매도이거나 수량이 달라져 복원하지 않는다.

### daily_equity.py

하루(한국시간 0시~24시) 총자산 기록. 계좌 총자산의 날짜별 시작·마지막·최고·최저를 data/daily_equity.json에 남긴다.  하루 손익 = 마지막 총자산 - 시작 총자산. 시작은 전날 마지막 기록(전날이 아니면 그날 첫 관측). 입출금은 구분하지 못하므로 그날 입출금이 있으면 손익에 섞인다(화면에 안내).

- `day_of(t)` (행 16): 

- `read(path)` (행 20): 

- `record(path, total, now=None)` (행 28): 총자산 한 번 관측. 바뀐 게 있으면 저장하고 그날 행을 돌려준다. 값이 이상하면 None.

- `recent(path, n=14)` (행 57): 최근 n일(최신순) [(날짜, 행)].

### entry_flags.py

매수 후보의 '급변 직후 진입' 표시를 기록만 한다(주문에는 영향 없음). 정의·기준값은 VERSION이 같은 동안 바꾸지 않는다.  기준 시점 = 판단 때 방금 마감된 15분봉의 종가(주문 전에 알 수 있는 값).   r1  직전 1시간(4봉) 등락 %      r4  직전 4시간(16봉) 등락 %   vol 직전 1시간 거래대금 ÷ (그 앞 96봉 평균 × 4)   H1 급등·거래량: r4 >= +8 또는 vol >= 3     H2 하락 중: r4 <= -4     H3 1시간 급변: |r1| >= 3   R8 = H1 또는 H2 또는 H3

- `features(bars)` (행 18): 완료봉 목록(시간순)에서 지표를 계산한다. 자료가 모자라거나 이상하면 None(결측은 기록에 남긴다).

- `flags(f)` (행 38): H1·H2·H3·R8. 지표가 없으면 모두 None(통과로 세지 않는다).

- `record(path, when, ranked, bars, entry_ok, market, top_n=15)` (행 48): 순위 상위 top_n개의 지표·표시를 한 줄씩 덧붙인다. market: 시장 상태(BTC 변화·장세 지수).

- `market_state(bars, index)` (행 63): BTC 4시간·12시간 등락 %와 장세 지수(없으면 None).

### entry_flags_review.py

급변 표시(entry_flags)의 효과를 정해 둔 기준으로 판정한다. 기준은 결과를 보기 전에 고정했고 REVIEW_VERSION이 같은 동안 바꾸지 않는다.  기준(R8 기준, 비용 포함 실현손익 · 정시 후보 기록과 실제 매수를 (정시, 종목)으로 연결):   표본: R8 발동 40건 이상, 서로 다른 5일 이상, 8종목 이상   효과: 걸린 건 평균손익 < 통과 건 평균손익, 걸린 건 총손실 ≥ 총이익의 1.5배, 가장 큰 손실 1건을 빼도 평균손익이 통과 건보다 나쁨 판정: collecting(모으는 중) / propose(적용 제안) / reject(효과 없음 · 기준 재검토) / weak(약함 · 관찰 연장) / extend(14일 지나도 표본 미달) 적용은 판정과 별개로 사용자 승인 뒤에만 한다.

- `realized_by_entry(orders_json)` (행 17): 거래소 주문 기록에서 V5 매수별 실현손익을 평균단가법(수수료 포함)으로 구한다. {(KST 시각 'YYYY-MM-DDTHH:MM', 종목): 원}

- `load_flags(path)` (행 48): 

- `evaluate(flags_path, orders_json, now=None)` (행 61): 

- `notice_text(r)` (행 103): 판정 결과를 사용자용 공지 (제목, 본문, 수준)으로. 모으는 중이면 None.

### exit_study.py

D-51 합의: 청산 규칙 4개 비교군 전향 그림자 평가. 주문 기능 없음, 실거래 코드와 분리된 별도 프로세스.  비교군 (매수 판단은 모두 shadow.proposal과 같고 청산만 다르다)   G0 현행     R = k×ATR(4시간봉). 본전 잠금 1R, 추적 2R, 1/3 익절 2R   G1 R0       R0 = E−S0 (최초 확정 손절 폭). 본전 잠금 1R0, 추적 2R0, 1/3 익절 2R0   G2 R0+1R0   G1과 같고 1/3 익절만 1R0 (최초 수량의 1/3, 한 번)   G3 +재매수  G2 + 1/3 익절 체결 뒤 재매수 권리(별도 lot)  합의 명세: docs/ai-history/debates/D-51/05_Claude_수용_합의.md, 구현 검토 07_ChatGPT_구현검토.md 반영(2판). - 설정은 시작 때 frozen.json에 고정하고 재시작해도 그 값을 쓴다. 현재 config가 달라도 바꾸지 않는다. - 같은 판단 시점의 호가·ATR은 한 번 조회해 모든 비교군에 같이 쓴다. - 판단 시점에 거래대금 상위 종목의 확정 봉이 하나라도 없으면 그 판단은 매매하지 않고 '판단 공백'으로 기록한다. - 초기 손절 S0는 실시간(30초) 체결가로, 올라간 보호선·익절·재매수는 확정 1분봉을 빠짐없이 순서대로 재생해 판단한다. - 우선순위: 전량 청산 → 일부 익절 → 재매수 평가. 잔여분의 고점·손절·R0는 일부 익절 뒤에도 보존한다. - 재매수 권리: 익절 체결 2~3시간 사이, 매수호가 ≤ 익절 평균가×0.97, reentry_block ATR 눌림, 최신 신호 유지 순위 M 이내·   상대강도 > 0, 신규 매수 제한 전부(경보·과열·위험 기준 금액·종목·총투입·현금), 지출 ≤ 익절 순수령액.   전량 청산·순환 매도·기한 만료 시 취소. - 재매수 lot: E=체결가, S0=cap_stop(E, E−k×ATR 4시간봉), R0=E−S0, G2 규칙에서 일부 익절·재매수 없음. - 장부·거래·묶음 기록은 한 상태 파일에 원자적으로 저장한다(재시작 중복 없음). - 성과는 진입 묶음 단위, 낙폭은 5분 관측 청산가치(매수호가 기준 비용·수수료 차감) 자산곡선. 시세가 빠진 관측은 낙폭 계산에서 제외하고 표시한다.  python exit_study.py            실행 python exit_study.py --report   보고서만

- `now_iso()` (행 64): 

- `risk_unit(lot, mode, k)` (행 69): G0은 k×ATR, 나머지는 R0=E−S0(유한한 양수일 때만, 아니면 k×ATR).

- `protect(lot, close, R, p, market, partial_r)` (행 78): 확정 1분봉 종가 평가. 반환: ('exit', 사유) | ('partial', None) | (None, None). 손절가는 내려가지 않는다.

- `rebuy_ok(right, now, ask, r, entry_ok, held_value, equity, invested, cash, p)` (행 97): 재매수 권리 평가. 막으면 (사유, 0), 허용이면 (None, 금액).

- `liquidation(qty, market, price, p)` (행 121): 

- `lots(P)` (행 125): 

- `Book` (행 130): 

  - `Book.__init__(self, name, cfg, folder)` (행 131): 

  - `Book.save(self)` (행 138): 

  - `Book.trade(self, row)` (행 143): 

  - `Book.holdings(self, prices)` (행 146): 

  - `Book.open_lot(self, market, qty, E, atr, p, kind)` (행 149): 

  - `Book.buy(self, market, krw, ask, atr, p)` (행 156): 

  - `Book.sell_qty(self, market, lot, qty, bid, p, reason)` (행 169): 

  - `Book.close_all(self, market, bid, p, reason)` (행 180): 

  - `Book.finish(self, market, last_px)` (행 186): 

- `Study` (행 195): 

  - `Study.__init__(self, folder=None)` (행 196): 

  - `Study.note_gap(self, kind, **info)` (행 216): 

  - `Study.heartbeat(self)` (행 220): 

  - `Study.atr4(self, m)` (행 227): 

  - `Study.decide(self, end)` (행 235): 

  - `Study.realtime_stops(self)` (행 291): 초기 손절 S0만 실시간 체결가로 확인한다.

  - `Study.minute_bars(self)` (행 317): 확정 1분봉을 빠짐없이 순서대로 재생한다. 봉마다 전량 청산 → 일부 익절 → 재매수 순.

  - `Study.replay_bar(self, bk, g, m, P, bar, book, r, prices)` (행 348): 

  - `Study.try_rebuy(self, bk, m, P, r, book, prices)` (행 378): 

  - `Study.snapshot(self)` (행 406): 

  - `Study.run(self)` (행 416): 

- `parse(text)` (행 450): 

- `summarize(state, capital)` (행 456): 

- `report(out=None)` (행 477): 

### exit_watch.py

정체 탈출 기록 모드(주문 없음). 2026-11-06 제안서 안 B를 실시간 가격으로 '했다면 어땠을지'만 남긴다.  규칙(사전 고정): 매수 T시간(2h 1차, 4h 비교) 뒤, 그때까지 최고가(profit_guard_peak)가 평단 +1% 미만이고 현재가가 수수료 포함 본전 아래면 '본전 지정가(공식 호가 올림)'를 건 것으로 친다(유효 2시간). 그 뒤 실시간 체결가가 지정가보다 한 호가 이상 위로 거래되면 '가상 체결', 그 전에 봇이 실제로 팔면 '실제 매도 먼저', 기한이 지나면 그때 근거(18h 상승률 ≤ 0 또는 순위 15위 밖)를 보고 '2단계(매수 1호가 매도)' 또는 '취소·보유'로 끝낸다. 가상 체결은 실제 자기 주문 체결의 증명이 아니다(대기열·수량 미반영).  파일: data/exit_watch.json(진행 상태), data/exit_watch.jsonl(사건 기록, 추가만).

- `tick(px)` (행 24): 업비트 원화 마켓 호가 단위(공식 표, 2025-07-31 기준).

- `ceil_tick(px)` (행 31): 

- `_ts(iso)` (행 36): 

- `_ok(x)` (행 42): 

- `Watch` (행 46): 

  - `Watch.__init__(self, data_dir)` (행 47): 

  - `Watch._event(self, row)` (행 53): 

  - `Watch._save(self)` (행 57): 

  - `Watch.update(self, positions, prices, rs_all, protected=(), now=None)` (행 62): 3초 감시마다 부른다. positions = 실거래 장부 positions, rs_all = {종목: (순위, 18h 상승률)}.

  - `Watch.summary(self)` (행 107): 

- `render(watch, brake_log, esc=None)` (행 120): 화면 한 줄 요약.

### live.py

실거래 실행: 두 AI가 합의한 '실행 주문안'만 하드 한도 안에서 실행한다.  사용자 지시(2026-11-02): "합의되면 승인 절차 없이 시작해", "봇 거래로 인한 손실이 많으면 중지하고 다시 검토시킬게", "보유 현금으로 실제 투자를 시작하고 상황에 따라 보유 종목을 일부 혹은 전량 매도해서라도 투자를 전개해 봐. 권한을 위임한다". - auto_proposal이 켜져 있으면 shadow.py가 1시간마다 계산한 봇 주문안(data/proposal.json)도 실행한다. - 토론.md 안건 표에서 상태가 '합의'이고 내용에 `실행 주문안: ...`이 있는 가장 최근 칸만 읽는다. - 같은 주문안은 한 번만 실행한다. 조건부(<...>)나 형식이 틀린 주문안은 실행하지 않고 기록한다. - 한도(config.json live)를 넘거나 누적 손실이 중단선에 닿으면 신규 매수를 하지 않는다. - 사용자 지시 "한도도 둘이 의논해 자율적으로 정하고, 가능성 있는 모델이 보이면 확대해 전체를 운영하라":   안건 표의 합의된 `실행 한도:` 칸을 적용한다. 한 번에 각 값은 최대 2배까지만 오르고 계좌 총평가액을 넘지 않는다.   낮추는 변경은 바로 적용한다. 손실 중단 상태는 사용자만 해제한다.

- `ProtectedAssetError` (행 48): User-managed assets must never reach automated order submission.

- `normalize_protected_market(value)` (행 52): 

- `parse(text)` (행 80): '매도 ONG 100%; 매수 BTC 20000원' -> [('sell','KRW-ONG',100.0), ('buy','KRW-BTC',20000)]. 형식 오류는 ValueError.

- `latest_plan(doc)` (행 100): 합의 상태인 칸 중 번호가 가장 큰 실행 주문안 (번호, 원문).

- `parse_limits(text)` (행 112): '1건 60000원; 누적 300000원' -> {'max_order_krw': 60000, 'max_total_buy_krw': 300000}

- `latest_limits(doc)` (행 123): 

- `throttle(current, wanted, ceiling)` (행 134): 올리는 값은 현재의 2배와 계좌 총평가액 중 작은 값까지. 내리는 값은 그대로.

- `Live` (행 143): 

  - `Live.__init__(self, folder, cfg, params)` (행 144): 

  - `Live.save(self)` (행 164): 

  - `Live.protection_file(self)` (행 170): 

  - `Live.protected_markets(self)` (행 173): Reload at each decision/submission; malformed state blocks submissions.

  - `Live.is_protected(self, market)` (행 180): 

  - `Live.protection_policy(self)` (행 184): 

  - `Live.manual_detector(self)` (행 195): 

  - `Live.scan_manual_orders(self, market=None, risk=False, amount=None, allow_protected=False)` (행 200): 

  - `Live.set_user_protection(self, market, protect, revision=None)` (행 207): 

  - `Live.assert_unprotected(self, market)` (행 213): 

  - `Live.record(self, row)` (행 217): 

  - `Live.share(self, msg)` (행 223): 토론 AI가 다음 회차에 읽도록 근거자료에 체결 사실을 남긴다.

  - `Live.loss_excluded(self)` (행 232): 손실 집계에서 뺄 사용자 보호 종목. 목록을 못 읽으면 마지막으로 읽은 목록을 쓴다(없으면 빈 집합).

  - `Live.unrealized(self, prices, exclude_protected=True)` (행 240): 

  - `Live.missing_valuations(self, prices)` (행 245): 

  - `Live.equity_total(self, prices=None)` (행 250): 계좌 총자산(원화 잔고+잠금+코인 평가). 조회 실패·가격 누락이 있으면 None.

  - `Live.loss_limit(self, prices=None)` (행 268): 신규 매수 중단선(원). loss_stop_pct>0이면 기준 총자산 × 비율(기준은 처음 한 번 저장), 아니면 loss_stop_krw.

  - `Live.raw_loss(self, prices, exclude_protected=True)` (행 281): 

  - `Live.migrate_loss_scope(self, prices)` (행 284): 보호 종목 제외 방식으로 처음 바뀔 때, 오늘 손실 값이 갑자기 달라지지 않게 기준(offset)을 맞춘다.

  - `Live.resume_buying(self, prices, reason='사용자 지시')` (행 294): 중단 해제. 지금까지의 손실을 기준(offset)으로 옮겨 손실 집계를 지금부터 다시 쌓는다(과거 손실은 장부에 남는다).

  - `Live.check_resume_request(self, prices)` (행 309): data/live/RESUME_BUY 파일이 있으면 수동 재개 요청으로 처리한다(tools/resume_trading.py가 만든다).

  - `Live.check_concurrent(self, prices)` (행 323): 동시 보유 알림(주문을 막지 않는다): 관리 종목(사용자 보호·5천원 미만 제외)이 concurrent_warn_at 이상이면 경보를 켜고 기록한다.

  - `Live.check_loss(self, prices)` (행 349): 

  - `Live._wait(self, order)` (행 381): 

  - `Live._fill(o)` (행 390): 

  - `Live._pending(self, risk_market=None)` (행 396): 

  - `Live._set_pending(self, pending, risk_market=None)` (행 400): 

  - `Live.has_uncertain_orders(self)` (행 408): 

  - `Live.market_has_uncertain_order(self, market)` (행 411): 

  - `Live._apply_pending(self, order, risk_market=None)` (행 416): Apply cumulative fills once and retain partial/unknown orders durably.

  - `Live.recover_pending(self, risk_market=None)` (행 535): 

  - `Live.recover_all_pending(self)` (행 541): Unrelated market risk exits may continue while an order is unresolved.

  - `Live._trade(self, side, market, amount, reason, risk=False, entry_context=None, validate_entry=False)` (행 560): 

  - `Live.poll(self, markets)` (행 619): 봇 주문안(위임)과 토론 합의 주문안을 확인하고, 새것이면 한 번 실행한다.

  - `Live.account_value(self)` (행 656): 

  - `Live.apply_limits(self, doc)` (행 665): 

  - `Live.poll_proposal(self, markets)` (행 680): 

  - `Live.execute(self, plan, markets, entry_context=None, automatic=False, market_index=None)` (행 741): 

  - `Live.entry_chase_block(self, market, context, ask=_NO_ASK)` (행 820): Validate source identity and enforce the receiver's existing cap; no new threshold.

  - `Live.buy_block(self, m, krw, markets, entry_context=None, validate_entry=False)` (행 861): 

  - `Live.restore_continuity(self, rows=None)` (행 917): 시작할 때 한 번: 거래소 체결 이력에서 봇(v5-)의 마지막 매도(재진입 기록)와, 인수 보유의 원래 평단(손절 기준)을 복원한다. 이력 조회가 실패하거나 확인이 부족하면 아무것도 바꾸지 않는다. 사용자 보호·주문 경로는 건드리지 않는다.

  - `Live.rebase_adopted_stops(self, prices)` (행 949): 인수 보유분의 손절가가 현재가보다 stop_max_pct 넘게 멀면 현재가 기준 상한으로 한 번 올린다(내리지 않음). 실시간 보호 기준인 stop0에도 같은 상한을 한 번 적용한다(stop0_rebased).

  - `Live.stop_limit(self, pos)` (행 982): 실시간 가격으로 평가할 매도선. 사용자 지시(2026-11-04) "손절 기준을 넘어서면 인식 즉시 손절 실행"에 따라 추적으로 올라간 손절가도 1분봉 마감을 기다리지 않고 실시간 가격으로 평가한다. 이익 보호선도 함께 본다.

  - `Live.update_profit_guard(self, prices)` (행 989): 도입 후 관측한 고점이 평단 대비 arm_pct 이상이면 고점 이익의 (1-giveback)를 보존하는 보호선을 올린다(내리지 않음). 수수료 바닥은 전략 수수료 추정치이며 실제 체결가를 보장하지 않는다.

  - `Live.adopt_trail_init(self)` (행 1041): 인수 보유분(entry 없음)에 추적 기준을 한 번 만든다. E=거래소 평균 매수가(cost/qty), A=현재 15분 ATR, 고점=편입 이후 확정 15분봉 고점. 초기 고정 손절 stop0는 현재 손절가 그대로 쓴다. 진입 시점을 모르므로 편입 시각 이후 봉만 쓰고 R은 3×ATR15로 정의한다(D-22).

  - `Live.trail_check(self)` (행 1065): EXIT-T1(실거래, 기본 꺼짐). 확정 1분봉 종가로 봉마다 한 번 평가. 봇이 새로 산 포지션만 대상.

  - `Live.liquidate_position(self, market, prices, reason='수동 강제 청산')` (행 1122): 사용자가 화면에서 확인한 종목 한 개를 전량 시장가 매도한다. (성공 여부, 안내문)을 돌려준다.  봇이 관리하는 장부 종목만 허용하고 손절과 같은 위험 청산 경로(_trade risk=True)를 쓴다. 그래서 주문 식별자 선기록·실행권 검사·미확정 주문 대사·장부 반영이 손절과 동일하다.

  - `Live.sweep_dust(self, market, markets, krw=DUST_BUY_KRW)` (행 1156): 팔 수 없는 자투리(평가 5,000원 미만)를 없앤다(사용자 지시 2026-11-06). krw만큼 사서 자투리와 합친 뒤 잔고 전부를 판다. 비용은 수수료 2회와 호가 차이(종목당 수~수십 원). 손절과 같은 주문 경로(_trade)를 쓰고, 보호 종목·장부 밖 잔고·잠긴 수량은 하지 않는다.

  - `Live.add_to_position(self, market, krw, markets, reason='수동 추가 매수')` (행 1184): 사용자가 화면에서 확인한 금액만큼 이미 관리 중인 종목을 시장가로 더 산다. (성공 여부, 안내문)을 돌려준다.  자동 매수와 같은 검사(buy_block: 손실 중단·미확정 주문·한도·가용 원화·호가 차이)를 모두 통과해야 한다. 기존 규칙상 추가 매수한 종목은 추적 손절·이익 보호 대상에서 빠진다(_apply_pending, 안건 27 미합의).

  - `Live.stop_check(self, prices)` (행 1214): Manage explicitly delegated positions, retaining partial fills and unresolved orders.

### manual_orders.py

화면의 '강제 청산'·'추가 매수' 요청을 받는 로컬 전용 소형 서버.  이 서버는 주문을 내지 않는다. 검증을 통과한 요청을 큐에 넣고, 봇 메인 루프가 손절과 같은 경로로 실행한 결과를 기다려 돌려준다. 방어: 127.0.0.1에만 바인딩 · Host 검사(DNS 재바인딩) · Origin은 file://('null')만 허용 · 봇을 시작할 때마다 새로 만드는 비밀 토큰 · 작은 본문·종목 형식 검사 · 같은 종목 30초 재요청 거부 · 한 번에 한 요청. 토큰은 로컬 status.html 안에만 있다.

- `_ExclusiveServer` (행 27): 같은 포트를 다른 프로세스가 함께 열지 못하게 한다. Windows의 SO_REUSEADDR은 중복 바인딩을 허용해 요청(토큰 포함)을 가로챌 수 있다.

  - `_ExclusiveServer.server_bind(self)` (행 32): 

- `Request` (행 38): 

  - `Request.__init__(self, market, kind='liquidate', krw=None, revision=None)` (행 39): 

  - `Request.finish(self, ok, message)` (행 46): 

- `ManualOrderServer` (행 51): 

  - `ManualOrderServer.__init__(self, port=DEFAULT_PORT, token=None, clock=time.time, allowed_kinds=None)` (행 52): 

  - `ManualOrderServer.check(self, host, origin, body, kind='liquidate')` (행 64): (상태코드, (종목, 금액) 또는 오류문). 통과하면 (200, (market, krw)). 청산은 금액이 None이다.

  - `ManualOrderServer.submit(self, market, kind='liquidate', krw=None, revision=None)` (행 108): 검증된 요청을 큐에 넣고 봇의 처리 결과를 기다린다. (상태코드, 문서)

  - `ManualOrderServer.drain(self)` (행 125): 

  - `ManualOrderServer.start(self)` (행 134): 

  - `ManualOrderServer.stop(self)` (행 189): 

### manual_protection.py

Conservative manual-order detection. GET only, never cancels or submits orders.

- `HistoryClient` (행 14): 

  - `HistoryClient.get(self, path, params)` (행 15): 

  - `HistoryClient.accounts(self)` (행 22): 

  - `HistoryClient.orders(self, market=None)` (행 26): 

- `user_identifiers(folder)` (행 49): Bot-screen direct orders retain v5 IDs, so their local reason identifies them.

- `Detector` (행 62): 

  - `Detector.__init__(self, policy, live_folder, client=None, clock=time.time)` (행 63): 

  - `Detector._inventory(rows)` (행 71): 

  - `Detector._owned(state, market)` (행 83): 

  - `Detector._bot_explained(self, market, qty)` (행 90): 계좌 인수는 모든 보유를 inherited로 표시하므로, 봇이 산 물량도 '출처 미확인'으로 보호될 수 있다. 거래소 주문 이력에서 이 종목의 체결이 전부 봇(v5-) 식별자이고 순수량이 현재 잔고와 같으면 봇 물량으로 본다. 하나라도 확인이 안 되면(조회 실패·미체결 주문·비봇 체결·수량 불일치·이력 밖 물량) False: 기존대로 보호한다(실패 시 보호 쪽).

  - `Detector._bootstrap(self, state, inventory, markets=None)` (행 114): 

  - `Detector._record_balance_evidence(self, state, inventory)` (행 134): 

  - `Detector.scan(self, state, market=None, risk=False, amount=None, allow_protected=False)` (행 147): 

### market_state.py

시장 자세 바(폭락·약세·횡보·상승·폭등)와 공포·탐욕 지수. 화면 표시와 기록 전용이며 주문 판단에 쓰지 않는다.  자세는 봇의 장세 필터와 같은 '거래대금 상위 종목 균등지수 24시간 수익률'로 정한다. 구간 경계(±2%, ±6%)는 보기 위한 눈금이다. 공포·탐욕은 Alternative.me의 일간 BTC 중심 지표(0 극도의 공포 ~ 100 극도의 탐욕)이며 v4 화면과 같은 출처다. 단기 브레이크 기록: 4시간 균등지수 ≤ −2%면 '브레이크 발동(기록만)'으로 남긴다(2026-11-06 정체·장세 제안 안 A, 실거래 미적용).

- `zone(x)` (행 24): 

- `_ret(bars, n)` (행 31): 

- `compute(bars_uni, index_24h, index_4h, at, tf=15, index_6h=None, filter_hours=24)` (행 40): 15분 판단마다 부른다. bars_uni = 거래대금 상위 종목 봉.

- `save(path, state)` (행 51): 

- `load(path)` (행 57): 

- `record_brake(path, state)` (행 64): 정시마다 단기 브레이크가 발동했을지 기록만 한다(주문 무영향).

- `fetch_fng(path, now=None, opener=urllib.request.urlopen)` (행 70): 공포·탐욕을 받아 저장한다. 실패하면 이전 값을 그대로 둔다.

- `fng_background(path, last)` (행 82): 마지막 수신 뒤 FNG_EVERY가 지났으면 별도 스레드로 받는다. 화면·거래 루프를 기다리게 하지 않는다.

- `_quiet(fn, *a)` (행 91): 

- `_filter_note(state)` (행 96): 봇이 실제로 쓰는 매수 필터 기간과 그 값(24시간이 아닐 때만 따로 보인다).

- `_pos(x)` (행 106): 

- `render(state, fng, now=None, esc=html.escape)` (행 110): 머리글의 새로고침 버튼 옆에 들어가는 한 줄짜리 자세 바와 공포·탐욕.

### missed_ledger.py

놓친 이유 장부(2026-11-05, ChatGPT 제안 · Claude 구현). 주문 판단에는 쓰지 않는 기록 전용.  매시 주문안을 만들 때 순위 상위 종목마다 '매수 신호가 있었는지'와 '걸린 차단 사유 전부'를 data/missed_ledger.jsonl에 한 줄씩 남긴다. 주문안 사유에는 처음 걸린 사유 하나만 남아서, BEAM처럼 놓친 사례를 나중에 추측으로 설명하게 되는 문제를 막는다. 이후 가격 변화(1·4·24시간)는 tools/missed_report.py가 공개 시세로 채워 사유별로 '놓친 수익'과 '피한 손실'을 함께 집계한다. 기록 실패는 주문안 생성을 막지 않는다(호출하는 쪽에서 예외를 삼킨다).

- `blocks_for(r, base, held, protected, promoted, last, entry_ok, regime_closed, scan, now)` (행 20): 이 종목에 걸린 차단 사유 전부(순서 무관). 빈 목록이면 규칙상 매수 후보.

- `record(data_dir, signal_closed_at, ranked, base, holdings, protected, promoted, last_sells, entry_ok, reasons, text, now=None)` (행 43): 

### notices.py

화면 맨 위 큰 안내 배너(공지). 개선 결과·검토 결과를 사용자가 놓치지 않게 알린다.  data/notices.json 에 쌓이고, 사용자가 '확인했습니다'를 누르기 전까지 화면 맨 위에 큰 글씨로 남는다. level 'action'은 사용자 결정이 필요한 알림(노란 큰 배너 + 한 번 알림창), 'info'는 알림만(파란 배너).

- `_read(path)` (행 14): 

- `_write(path, items)` (행 22): 

- `add(path, nid, title, body, level='info', at='', popup=False)` (행 30): 같은 id가 이미 있으면 다시 만들지 않는다(확인한 공지가 되살아나지 않는다). 새로 만들면 True.

- `ack(path, nid)` (행 41): 

- `active(path)` (행 52): 

- `take_popups(path)` (행 56): 알림창을 아직 안 띄운 공지를 돌려주고 띄운 것으로 표시한다.

- `banner(items, action=None)` (행 68): 맨 위 배너 HTML. action={'port','token'}이 있으면 '확인했습니다' 버튼이 동작한다.

### observations.py

봇 화면의 '관찰·그림자 관리' 창: 앞으로 확인할 일, 그림자 후보, 사용자 결정 대기 항목을 기록하고 상기시킨다.  주문과 무관하다. 파일 하나(data/observations.json)에 항목을 두고, 화면의 버튼(완료·보류·다시 진행·메모)과 tools/obs.py가 같은 파일을 고친다. 기한이 지나거나 24시간 안으로 다가오면 접힌 제목 줄에 빨간 표시가 나와서, 사용자가 잊어도 화면을 열면 보인다.

- `_item(id, title, kind, due=None, status='진행', criteria='', source='')` (행 22): 

- `_now()` (행 51): 

- `load(path)` (행 55): 

- `save(path, doc)` (행 69): 

- `apply(path, id, payload)` (행 78): 화면 버튼·CLI 공통 처리. payload: {'action': done|hold|open|note, 'note': 글(선택)}. (성공 여부, 안내)

- `add(path, id, title, kind='observe', due=None, criteria='', source='')` (행 103): 

- `_due_info(item, now)` (행 116): (정렬 키, 표시 문구, 등급 'over'|'soon'|'later'|'none')

- `summary(doc, now=None)` (행 136): (진행 중 건수, 기한 지남, 24시간 이내)

- `render(path, action=None, now=None)` (행 145): 접어 둘 수 있는 창 HTML. action = {'port':…, 'token':…} 또는 None(버튼 비활성).

### open_page.py

봇 화면(HTML)을 크롬으로 연다. 크롬이 없으면 시스템 기본 브라우저를 쓴다.  - `CHROME_PATH` 환경변수로 크롬 위치를 지정할 수 있다. - `V5_NO_BROWSER=1`이면 아무것도 열지 않는다(배포 자동 재시작이 탭을 계속 늘리지 않게).

- `find_chrome()` (행 17): 

- `open_page(target)` (행 26): 파일 경로 또는 URL을 연다. 반환: 'skipped' | 'chrome' | 'default'.

### paper.py

모의 장부. 전략 배정액 A0=f×E(T0)로 시작하고, 실제 계좌 T0 보유 바구니 H와 비교한다.

- `now_iso()` (행 11): 

- `Ledger` (행 15): 

  - `Ledger.__init__(self, folder)` (행 16): 

  - `Ledger.ready(self)` (행 26): 

  - `Ledger.start(self, E, fraction, basket, krw, unpriced, label='')` (행 29): T0 확정. basket: {종목: 수량} 가격 있는 실제 보유, unpriced: 미평가 자산 목록.

  - `Ledger.save(self)` (행 39): 

  - `Ledger._append(self, f, row)` (행 44): 

  - `Ledger.event(self, msg)` (행 48): 

  - `Ledger.invested(self, prices)` (행 51): 

  - `Ledger.equity(self, prices)` (행 54): 

  - `Ledger.h_value(self, prices)` (행 57): T0 보유 바구니 평가. 가격이 하나라도 없으면 None (비교 보류).

  - `Ledger.buy(self, market, ask, atr, qty_fn, p)` (행 64): P=ask×(1+c), S=P−k×ATR 고정. qty_fn(P, S) -> 수량.

  - `Ledger.sell(self, market, bid, reason, p, banned_until=None)` (행 80): 

  - `Ledger.snapshot(self, prices)` (행 94): 

  - `Ledger.read_jsonl(self, name, last=50)` (행 100): 

### pnl.py

실현손익 관리 (D-56 합의). 거래소 체결을 원천으로 수수료 포함 이동평균 원가의 관리용 실현손익을 재구성한다.  주문을 내지 않는다(GET만). 봇 장부(live.json)와 무관하므로 PC 이동·계좌 인수·수동 주문에도 누적이 이어진다. 결과는 data/pnl/summary.json에 쓰고, bot.py 현황 화면의 '실현손익' 구역이 읽는다.    python pnl.py            한 번 동기화·계산   python pnl.py --loop 300 5분마다 (별도 프로세스)  - 기초: pnl_baseline.json (모든 PC 공통. 기초 시각의 수량·평균 매입가) - 기간: 한국시간 오늘·이번 주(월요일)·이번 달·기초 이후 누적. 매도 체결 시각으로 나눈다. - 원가: 매수 체결대금+수수료를 원가에 더하고, 매도 시 실현손익 = 체결대금 − 매도수수료 − 이동평균 배분원가. - 손익은 세 가지로 나눈다(ChatGPT 구현 검토 05 반영):   확정 = 기초 이후 체결 기록만으로 원가가 정해진 매도.   추정 = 기초 보유분의 평균 매입가(수수료 포함 여부·입출금 미확인)가 원가에 섞인 매도.   미확정 = 원가 불명·수량 부족·실제 잔고와 재구성 수량 불일치 종목·체결 상세 불완전 주문. - 관리용 손익이며 세무상 손익이 아니다.

- `kst(s)` (행 43): 

- `now()` (행 47): 

- `_get(path, params)` (행 52): 

- `closed_between(start, end)` (행 72): [start, end) 종료 주문 전부. 1000건이 차면 가장 이른 생성 시각으로 끝을 당겨 이어 받는다.

- `Store` (행 89): 

  - `Store.__init__(self, folder=OUT)` (행 90): 

  - `Store.save(self)` (행 96): 

  - `Store.sync(self, start)` (행 101): 기초 시각부터(이미 받은 구간은 1시간 겹쳐서) 지금까지 7일 창으로 받는다. 체결 있는 주문은 체결 내역을 받는다.

  - `Store.detail(self, uid)` (행 125): 

  - `Store.open_orders()` (행 140): 

- `fills(orders)` (행 154): 주문 → 체결 이벤트. 주문 수수료는 체결대금 비율로 나눈다.

- `rebuild(baseline, events)` (행 176): 이동평균 원가로 재구성. 반환: (종목 상태, 매도 체결 결과 목록).

- `period_starts(t)` (행 209): 

- `aggregate(sells, events, starts, mismatch=(), baseline_start=None)` (행 214): 

- `unrealized(pos, accounts, prices)` (행 240): 

- `compute(store, baseline, accounts, prices, at=None)` (행 264): 

- `run_once(folder=OUT)` (행 285): 

### pnl_service.py

Start the existing GET-only PnL worker with credentials; never place orders.

- `worker_environment(app, env=None)` (행 11): 

- `pnl_pids(rows, app)` (행 26): Inspect Python only; never count the PowerShell query itself as a worker.

- `running_pids(app)` (행 43): 

- `startup_lock(app)` (행 55): 

- `ensure_running(app, env=None, python=None)` (행 73): 

### position_popup.py

봇 실거래 보유 종목을 클릭하면 상세 정보를 보여 주고, 설정으로 켠 경우에만 '강제 청산'·'추가 매수' 버튼을 둔다.  화면은 정적 HTML(file://)이다. 상세 값은 페이지 안에 JSON으로 넣고, 30초마다 자동 새로고침되어도 열린 창이 유지되도록 sessionStorage에 종목을 기억한다. 청산·추가 매수는 확인 질문 '[예][아니오]'를 거쳐 manual_orders의 127.0.0.1 서버로만 보낸다.

- `_num(v)` (행 11): 

- `detail(market, pos, price, last_sell=None, now=None)` (행 15): 한 종목의 상세 값. 가격이 없으면 가격 의존 값은 None으로 두고 0으로 꾸미지 않는다.

- `_json_tag(tag_id, value)` (행 50): 

- `data_script(positions, prices, last_sells=None, now=None, action=None)` (행 55): 종목별 상세(와 청산 서버 접속 정보)를 담은 JSON 스크립트 태그. '<' 를 이스케이프해 태그가 깨지지 않게 한다.  action은 {'port': 포트, 'token': 토큰} 또는 None. None이면 화면에 청산 버튼이 나오지 않는다.

- `row_attrs(market, dust=False)` (행 65): 

### protection_panel.py

Protection choices at the top of the local bot dashboard.

- `render(live, account_markets=(), action=None)` (행 6): 

### reconcile.py

Pure management-ledger replay. No I/O, API, lease mutation, adoption, or baseline reset.

- `ReconciliationError` (행 14): 

- `canonical_sha256(value)` (행 18): 

- `preserved_basis(state)` (행 22): 

- `number(value)` (행 27): 

- `timestamp(value)` (행 38): 

- `totals(value=None)` (행 47): 

- `reconcile(base_state, detailed_orders, accounted_by_identifier, snapshot_epoch, current_quantities, protected_markets=(), verified_stop_floors=None, authorized_external_markets=())` (행 52): Return provisional candidate/report; caller separately validates ownership and coverage.  accounted_by_identifier MUST be sums from the SAME source snapshot's local fill journal. Order details are terminal cumulative fills. New inventory is never invented from balances. Unknown transient protection is allowed only if that new position closes in this replay.

### reconciliation_guard.py

Fail-closed, file-bound recovery proof gate. No network, writes or lease changes.

- `ProofInvalid` (행 23): 

- `canonical_bytes(value)` (행 27): Same sorted compact default-ASCII JSON as SessionGuard.checkpoint_hash.

- `canonical_sha256(value)` (행 32): 

- `preserved_basis(state)` (행 36): 

- `financial_projection(state)` (행 41): 

- `require(condition, code)` (행 46): 

- `load_json(raw)` (행 51): 

- `safe_file(data_root, relative)` (행 57): 

- `_independent_replay(source, orders, applied_local, accounts, floors, authorized)` (행 67): Bound to the peer-reviewed sibling replay module; no caller-supplied calculator.

- `validate_or_raise(root, cfg, lease, now=None)` (행 96): Validate a preserved-basis reconciliation bridge while the remote lease is idle.  Source snapshot hash is raw-file SHA256. Orders/account/applied-local artifacts must be canonical JSON bytes and are hashed as stored. Preserved-basis hash is normalized canonical JSON and must match source, separate artifact and target. All input paths are derived from the safe proof id; optional files mapping may only repeat those exact relative paths, never redirect them.

- `validate(root, cfg, lease, now=None)` (행 214): Return False on any malformed/missing proof, independent replay or file error.

### regime_compare.py

매수 필터 6시간 전환(2026-11-06 사용자 지시) 평가: '24시간 필터였다면 안 샀을 매수'만 골라 실제 손익을 모은다(표시 전용).  regime_watch.jsonl(정시마다 24h·6h 지수)과 거래소 체결 캐시(data/pnl/orders.json)를 맞춘다. 매수 체결이 들어간 정시 판단의 24h 지수가 0 이하이면 '6시간 전환으로 추가된 매수'다. 포지션은 보유가 5,000원 미만에서 매수로 열리고 매도로 5,000원 미만이 되면 닫힌다(수수료 포함, 평균 원가). 빈자리·현금 때문에 24시간 필터라면 다른 종목을 샀을 수 있는 효과는 계산하지 않는다.

- `_t(s)` (행 15): 

- `hours(watch_path)` (행 19): 

- `positions(orders_path, protected=lambda m: False)` (행 32): 

- `extra_buys(watch_path, orders_path, protected=lambda m: False)` (행 58): (닫힌 추가 매수 목록, 보유 중 추가 매수 목록).

- `render(watch_path, orders_path, protected=lambda m: False)` (행 71): 

### rotation_policy.py

Reviewed rotation envelope, evidence, and research-only confirmation helpers.  No network, credentials, order submission, process, or independent collector. Critical claim/pending state remains in Live's existing durable ledger.

- `finite(x)` (행 22): 

- `enabled(p)` (행 26): 

- `saved_loss_budget(cfg, ledger)` (행 30): Pure read: never fetch/save inside partially applied fill accounting.

- `slot(now)` (행 40): 

- `policy_hash(p)` (행 46): 

- `decision_id(closed, digest)` (행 51): 

- `intent_id(closed, market, side, digest)` (행 55): 

- `envelope(p, closed, now)` (행 61): 

- `validate(prop, p, now, floor_slot)` (행 70): 

- `fresh_reentry(last, p, closed)` (행 83): 

- `row_close(row, tf)` (행 96): 

- `rank_exit(state, market, rank, closed, keep, confirmations=1)` (행 106): Research knob only: production patch uses 1, not an invented 2-bar delay.

- `observe(pos, price, now)` (행 123): Observed ticks only, bounded by entry/basis revision; never backfill history.

- `trade_evidence(pos, reason, now, meta=None)` (행 147): 

- `append_audit(path, event, max_bytes=4 * 1024 * 1024)` (행 158): Optional diagnostics must not block risk orders; <=3 bounded log segments.

### session_guard.py

Cross-PC trading ownership via an atomic Git ref; no keys/balances are uploaded.

- `SessionLost` (행 16): 

- `SessionUnverified` (행 20): GitHub could not be reached, so ownership could not be checked. Not proof that ownership changed.

- `NotOwner` (행 24): 다른 PC가 실행권을 갖고 있어 이 PC는 주문하지 않는다. 오류가 아니라 정상적인 대기 상태.

- `GitStore` (행 34): 

  - `GitStore.__init__(self, folder, remote=DEFAULT_REMOTE, sleep=None)` (행 37): 

  - `GitStore.diagnostic(self, operation, category, duration, returncode=None)` (행 46): Fixed metadata only: never persist command arguments, URLs, stderr or state.

  - `GitStore.failure_category(stderr)` (행 59): 

  - `GitStore.git(self, *args, input=None, outside=False, check=True)` (행 73): 

  - `GitStore.read(self)` (행 109): 

  - `GitStore.cas(self, expected, state)` (행 118): 

- `confirm_takeover()` (행 136): 

- `notify(message, title='봇 실행 상태')` (행 151): 

- `pc_identity(root)` (행 161): 이 설치(PC)의 고정 식별자. 봇을 다시 켜도 같은 PC임을 알아보는 데 쓴다(실행권 기록의 owner는 '식별자:프로세스').

- `SessionGuard` (행 179): 

  - `SessionGuard.__init__(self, root, cfg=None, store=None, clock=time.time)` (행 180): 

  - `SessionGuard.lease_valid(self)` (행 201): 

  - `SessionGuard.checkpoint_hash(self)` (행 204): 

  - `SessionGuard.assert_transition_clear(self)` (행 210): 

  - `SessionGuard.record(self, **values)` (행 216): 

  - `SessionGuard.acquire(self, confirm=confirm_takeover, wait_seconds=240)` (행 222): 

  - `SessionGuard._landed(self, claim)` (행 291): 

  - `SessionGuard._same_pc(self, owner)` (행 298): 

  - `SessionGuard._note_state(self, state)` (행 301): 읽은 기록으로 램프 상태를 갱신한다(내가 소유자인지, 다른 PC인지, 아무도 아닌지).

  - `SessionGuard._mark_unverified(self, ex)` (행 312): 

  - `SessionGuard._give_up(self, state, why)` (행 319): 실행권을 잃었거나 양도 요청을 받았다: 주문을 멈추고 대기 상태로 둔다(봇은 종료하지 않는다). 요청이 있으면 비워 준다.

  - `SessionGuard.assert_owner(self, side=None)` (행 334): 주문 직전 확인. 실행권자가 아니면 NotOwner. 확인 자체를 못 하면 매수는 막고, 매도(손절)는 마지막 확인이 실행권자였을 때만 허용한다.

  - `SessionGuard._renew(self, oid)` (행 360): 

  - `SessionGuard.check_now(self)` (행 370): 주기 확인(감시 스레드). 어떤 실패도 봇을 끄지 않는다.

  - `SessionGuard.start(self)` (행 392): 

  - `SessionGuard.snapshot(self)` (행 402): 화면 램프용 요약.

  - `SessionGuard.release(self)` (행 409): 

### shadow.py

그림자 실험: C안 변형 12개를 같은 시세로 동시에 모의 운영하고, 1시간마다 성과를 분석해 토론에 공유한다.  주문 기능 없음. 실거래(bot.py의 live)와 완전히 분리된 별도 프로세스다. python shadow.py            실행 python shadow.py --report   지금 성과 보고서만 생성

- `current_slot(now)` (행 48): 

- `slot_due(now, last_slot)` (행 52): 지금 만들어야 할 슬롯 시작 시각. 이미 처리했거나 시작 전·허용 지연 밖이면 None.

- `load_last_slot(now, path=None)` (행 60): 영속 기록된 마지막 슬롯. 파일이 없거나 손상·비정각·미래 값이면 현재 슬롯을 처리한 것으로 간주한다(중복 주문안보다 한 슬롯 건너뛰는 쪽이 안전).  가동 시작에는 항상 현재 슬롯도 처리한 것으로 본다: 재시작이 허용 지연창 안에 있어도 새 판단을 만들지 않고 다음 정규 슬롯을 기다린다.

- `plan_tick(now, last_slot, ready, claim, make)` (행 75): 한 번의 반복에서 할 일. (새 last_slot, 상태). 신호가 아직 최신이 아니면 기록하지 않고 허용 지연 안에서 다시 시도한다.

- `claim_slot(slot, path=None)` (행 91): 주문안을 만들기 전에 슬롯을 먼저 기록한다. 기록 뒤 장애가 나도 같은 슬롯의 주문안이 두 번 만들어지지 않는다.

- `proposal(holdings, ranked, rs_out, entry_ok, base, caps, krw, bought, last_sells=None, market_index=None, rotation_state=None, rotation_slot=None, promoted=None, hot=None, protected=None, costs=None, stuck_out=None)` (행 102): C안 규칙을 실제 계좌에 그대로 적용한 주문안. 기존 보유도 신규 후보와 같은 기준으로 평가한다(사용자 지시).  holdings: {종목: 평가액}. 반환: (주문안 문구, 사유 목록) promoted: 사용자 직접 지정 종목, hot: 과열 보유 종목(순위 매도 제외). chase_override 참고.

- `variants(base)` (행 282): 

- `metrics(folder, A0)` (행 295): 

- `fmt(v, kind)` (행 318): 

- `report(base, prop=None)` (행 328): 

- `_md_to_html(md)` (행 363): 

- `share(md)` (행 378): 근거자료의 성과 구역만 최신 내용으로 바꾼다(파일이 계속 커지지 않게).

- `Shadow` (행 393): 

  - `Shadow.__init__(self)` (행 394): 

  - `Shadow.proposal_signal(self, now=None)` (행 404): Return provenance only for the current completed signal, never a relabelled cache.

  - `Shadow.protected_markets(self)` (행 428): 사용자 보호 종목(봇 매매 대상 아님). 읽기 실패면 빈 집합: 보호 종목이 투입액에 남아 매수 여력이 줄어드는 보수적인 쪽이다. 실제 주문의 보호 강제는 live가 따로 한다.

  - `Shadow.make_proposal(self)` (행 438): 

  - `Shadow.init(self)` (행 500): 

  - `Shadow.decide(self, end)` (행 509): 

  - `Shadow.stops(self)` (행 564): 

  - `Shadow.snapshot(self)` (행 611): 

  - `Shadow.run(self)` (행 617): 

### strategy.py

C안: 상대강도 순환 (AI 토론 합의 15·16·18·27번). 판단 봉 길이는 tf(분)로 정한다.  모두 순수 함수다. 시세 조회·장부·시간 처리는 bot.py와 paper.py가 맡는다.

- `Params` (행 13): 

  - `Params.from_dict(cls, d)` (행 66): 

- `cost(market, p)` (행 71): 

- `wilder_atr(bars, n=14)` (행 75): Wilder ATR: 첫 n개 TR 평균으로 시작해 (이전×(n−1)+TR)/n.

- `relative_strength(bars, L)` (행 86): 

- `market_index(bars_by_market, n, min_markets=20)` (행 92): 종목들의 최근 n개 봉 상승률의 단순 평균(균등지수 수익률). 자료가 부족·불일치하면 None.  비정상 값(NaN·무한대·0 이하)은 제외한다. 대다수 종목과 마지막 봉 시각 또는 n봉 전 봉 시각이 다른 종목(결측·정지)도 제외한다.

- `rank(bars_by_market, p)` (행 120): 적격 종목을 상대강도 내림차순(동률은 코드순)으로 정렬. 자료 부족 종목은 제외.

- `chase_block(r, p, cap=None)` (행 134): 과열 추격 매수 보류 사유. 후보 r(rs 포함)가 기간 상승률 상한 이상이면 문자열, 아니면 None. cap: 종목별 상한(직접 지정·이익 청산 뒤 재진입은 chase_override.cap_for). 없으면 p.chase_rs_cap.

- `cap_stop(entry, stop, p)` (행 143): 손절가가 기준가에서 stop_max_pct보다 멀면 그 거리로 당긴다(올리기만 함).

- `reentry_block(r, last, now, p)` (행 150): 직전 매도 기록 last={'time','price','profit'}가 있으면 재진입을 더 까다롭게 본다. 막으면 사유 문자열, 아니면 None. 수익 청산: reentry_profit_hours 뒤부터, 그리고 reentry_window_hours 안에서는 현재가가 매도가보다 reentry_dip_atr×ATR 이상 낮을 때만(고점에서 팔고 눌리면 다시 잡기). 손실 청산: reentry_loss_hours 뒤부터.

- `plan(ranked, held, banned, entry_ok, p)` (행 175): 15번 교체 규칙. held: 보유 종목, banned: 재진입 금지, entry_ok: 신규 진입 가능한 종목 집합.  반환: (매도 [(종목, 사유)], 매수 후보 [종목], 판단 보류 [종목])

- `size(equity, invested, cash, P, S, p)` (행 208): 16·27번 수량: min(계획위험/(P−S), 종목 상한, 잔여 총투입, 수수료 포함 가용현금). 최소주문 미달은 0.

- `ratchet(market, pos, price, p)` (행 220): EXIT-T1. 고점·손절가를 갱신하고 청산 사유('수익 반납'|'추적 손절'|'손절')를 반환한다. 손절가는 내려가지 않는다.  pos: entry, stop, atr 필요. 갱신값 peak, stop0(초기 손절가), stop을 pos에 기록한다. trail_enabled=False이면 기존 규칙(고정 손절)과 동일하다.

- `bar_after_entry(bar, entry_iso)` (행 248): 1분봉이 진입 시각 이후에 마감됐는지. 체결 이전·같은 봉 정보를 쓰지 않기 위한 검사.

### trade_history.py

업비트 종료 주문(체결 기록)을 읽기 전용으로 받아 data/exchange_orders.jsonl 에 누적 보관한다.  주문을 내지 않는다(GET /v1/orders/closed 만 사용). 봇이 낸 주문뿐 아니라 수동 주문·다른 PC의 봇 주문도 거래소 기록이므로 함께 보관하며, 주문 식별자가 `v5-` 로 시작하면 봇 주문으로, 아니면 '출처 불명(수동 또는 식별자 없는 봇)'으로 분류한다.    python trade_history.py                     한 번 받아 저장하고 오늘 요약을 출력   python trade_history.py --days 7            최근 7일 요약   python trade_history.py --loop 300          5분마다 받아 저장(별도 프로세스로 상시 실행)   python trade_history.py --data <data 폴더>  저장 위치 지정(기본: 이 폴더의 data)

- `fetch_closed(upbit)` (행 25): 종료 주문(done, cancel)을 최근순으로 받는다.

- `normalize(o)` (행 34): 

- `load(path)` (행 42): 

- `sync(path, fetch)` (행 53): 새 체결 주문만 덧붙인다(uuid 중복 제거). 반환: 새로 저장한 건수.

- `when(r)` (행 67): 

- `summary(path, days=1, now=None)` (행 71): 

- `find_keys(upbit)` (행 94): 프로젝트 상위 폴더들의 .env를 키 검색 경로에 추가한다(실행 위치와 무관하게 동작).

- `main(argv=None)` (행 102): 

### upbit.py

Minimal Upbit client: public quotes, account lookup, and market orders used by live.py.

- `OrderRejected` (행 20): The exchange definitively rejected a submission before creating this order.

  - `OrderRejected.__init__(self, status, name, message)` (행 22): 

- `_get(path, params=None, headers=None, retries=3)` (행 28): 

- `market_warning_details(row)` (행 49): Split Upbit warning metadata; 'warning' keeps the original combined formula.  Non-dict market_event raises ValueError. Non-dict caution keeps warning=True when the warning flag or legacy CAUTION is set; otherwise raises ValueError (state unknown).

- `krw_markets()` (행 83): KRW markets with a warning flag (투자유의 또는 주의 항목).

- `tickers(markets)` (행 94): 

- `orderbooks(markets)` (행 103): 

- `completed_candles(market, minutes, count=200, now=None)` (행 111): Chronological candles whose period has fully ended. Each bar: t (UTC start), o, h, l, c.

- `_keys()` (행 126): 

- `_b64(data)` (행 139): 

- `accounts()` (행 143): Read-only balance lookup (GET /v1/accounts).

- `_auth(params=None)` (행 154): 

- `get_order(order_uuid)` (행 168): 

- `get_order_by_identifier(identifier)` (행 173): 

- `open_orders()` (행 178): 

- `market_buy(market, krw, identifier=None)` (행 183): 시장가 매수: 원화 금액 지정.

- `vol8(volume)` (행 190): 수량을 소수 8자리 문자열로(repr 기준 가장 가까운 값). 거래소 잔고는 8자리라 float을 거쳐 37.430000009999998이 된 값도 37.43000001로 정확히 돌아온다. 내림이면 마지막 1e-08개가 남는다(2026-11-06 자투리 원인).

- `market_sell(market, volume, identifier=None)` (행 197): 시장가 매도: 수량 지정.

- `_post_order(params)` (행 204): 

## 테스트 파일 색인

검증용 코드는 모두 소스 보관본에 포함되어 있다. 이번 문서 작업에서는 실거래·외부 기록 부작용을 피하기 위해 전체 테스트를 실행하지 않았다.

- test_account_sync.py

- test_auto_protection.py

- test_bot_explained_inherited.py

- test_caution_study.py

- test_chase_override.py

- test_concurrent_warn.py

- test_continuity.py

- test_daily_equity.py

- test_entry_context.py

- test_entry_flags.py

- test_exit_study.py

- test_exit_t1.py

- test_live.py

- test_live_adoption.py

- test_live_reliability.py

- test_loss_excludes_protected.py

- test_manual_buy.py

- test_manual_liquidate.py

- test_market_exit_watch.py

- test_missed_ledger.py

- test_notices_review.py

- test_observations.py

- test_open_page.py

- test_ownership_ui.py

- test_plan_slot.py

- test_pnl.py

- test_pnl_service.py

- test_position_popup.py

- test_profit_guard.py

- test_proposal.py

- test_protected_assets.py

- test_ranking_view.py

- test_reconcile.py

- test_reconciliation_guard.py

- test_reconciliation_session.py

- test_refresh_request.py

- test_regime_filter.py

- test_review_fixes.py

- test_rotation_rollout.py

- test_same_plan_generator.py

- test_same_plan_receiver.py

- test_session_diagnostics.py

- test_session_guard.py

- test_session_outage.py

- test_signal_freshness.py

- test_strategy.py

- test_timeframes.py

- test_trade_history.py

- test_volume_caution_tradable.py

- test_warning_details.py

- test_warning_exits.py