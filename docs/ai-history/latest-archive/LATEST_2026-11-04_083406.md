# 한 장 인계 (LATEST)
작성: Claude · 2026-11-04 08:40 (집 PC → 사무실 인계). 이 장만 읽으면 이어서 작업할 수 있다. 상태는 말하기 전에 `python tools/ai_session.py status`로 다시 확인한다.

## 1. 지금 상태 (08:40 확인)
- 실거래 봇은 **집 PC에서 실행 중, 실행권도 집 PC**. 사무실에서 봇·실거래·배포를 시작하지 않는다(같은 계좌 동시 주문 금지).
- **신규 매수 전면 중단 중**(halt_new_buys, 08:14~). 보유 종목의 매도·손절·이익보호만 작동. 해제는 사전 등록 검증 통과 또는 사용자의 명시 지시로만.
- 설정: 손절 상한 4.5%, 이익보호 시작 +3%, 건당 약 7.7만 원(entry_risk 0.00184, 매수 재개 시 적용), 장세 필터 켜짐(11-07 07:51 만료 → 그 뒤 신규 매수 전부 보류), 정시 슬롯 주문안(재시작해도 추가 주문안 없음).
- 사용자 보호: DOGE는 봇 주문 제외(사용자 직접 주문 보호 규칙 유지). 손실 기준·장부는 초기화하지 않았다.
- 연구용 15분봉 수집이 집 PC에서 진행 중(299개 종목 180일, 08:30에 55개 완료, 약 1시간 더). 자료는 `C:\Users\User\Desktop\research_data\candles15`(Git 제외, 공개 API만 사용).

## 2. 바뀐 것 (11-04, 중요한 것만)
1. 봇 종료 원인 수정: GitHub 실행권 확인이 일시 오류에 한 번 걸려도 봇이 죽던 구조 → 재시도·확인 불가 유예(만료 전까지 주문만 차단), 확인 간격 30초. (v5/session_guard.py)
2. 정시 슬롯 주문안: 재시작이 추가 주문안을 만들지 않음, 슬롯 기록을 생성 전에 저장. (v5/shadow.py, 설정 live.slot_plans)
3. "눈속임" 지적 정정: 건당 금액 축소·장세 필터는 수익 개선이 아니라 위험 축소. 신규 매수 중단은 ChatGPT와 일치(원문 originals/halt-and-validation-*).
4. 연구 도구: tools/research/collect_candles.py(수집), pit_universe.py(시점별 거래대금 순위 재구성, 미래 봉 금지), 테스트 7개.
5. 설계 합의 4원칙(단타·스윙 2중 구조): docs/ai-history/2026-11-04-dual-bucket-design.md. 청산 후보: 2026-11-04-exit-rule-candidates.md. 수치는 가설·미동결.
6. 인계 방식 변경: 이 LATEST.md 한 장 + `start`가 긴 문서 대신 이것만 출력 + `tools/handoff_digest.py check`.

## 3. 하지 말 것
- 사무실에서 봇 실행·배포·설정 변경, 신규 매수 중단 해제, 임계값 동결 전 실거래 반영.
- 수집 자료(`research_data`)·키·계좌 정보를 Git에 올리기. 연속 토론 진행기 재개(사용자 지시: 명령한 안건만 단발로 ChatGPT에게 묻는다).
- 휴대폰 알림·앱 주제를 먼저 꺼내기(사용자가 보류, 언급 전까지 금지).
- ChatGPT 응답이 없는 내용을 합의로 기록하기. 모의 결과를 개선 입증으로 말하기(8일 자료 기준 우위 미입증).

## 4. 다음 할 일 (순서대로)
1. 사용자 결정 대기: 신규 매수 재개 여부(권장: 검증 전 유지), 단타 vs 스윙 방향.
2. 수집기·시점별 순위 재구성기를 ChatGPT에 검증 의뢰(미래정보 누출·결측·순위 재현성). 작성 Claude, 검증 ChatGPT. Codex CLI 단발 호출 방법은 docs/ai-history/originals/*-request.txt 형식 참고.
3. 수집 완료 후 사전 등록 프로토콜 동결 문서 작성(데이터 결과를 보기 전에): 180일, 비용 0.30%/0.60%, 워크포워드, 합격 수치, 최근 60일 잠금 구간(ChatGPT 확인 대기).
4. 첫 실험: 기존 진입 신호의 보유기간별(1h/6h/24h) 비용 후 기대값 진단.
5. 09:01 슬롯 주문안이 정각에 1회만 생성됐는지 확인(data/proposal.json 시각·plan_slot.json), 11-05 07:57경 장세 필터 24시간 검토.
- 사무실에서 자료가 필요하면: `python tools/research/collect_candles.py --out <폴더> --days 180`(이어받기 가능, 키 불필요, 약 1시간). 집 PC 자료는 Git에 없다.

## 5. 더 읽을 곳 (필요할 때만)
- 결정 근거·ChatGPT 원문: docs/ai-history/2026-11-03-claude-verification.md(코인 폴더 저장소) · docs/ai-history/originals/
- 권한과 한계: docs/ai-history/permissions.md · 전체 인계(길다): HANDOFF.md, 집에서_이어서_2026-11-03.md
- 이전 판 보관: docs/ai-history/latest-archive/
