# 회사 PC 할 일 (집 PC Claude가 2026-11-15 11:00에 작성)

회사 PC에서 Claude에게 "회사PC 할일 해"라고 하면 아래를 순서대로 한다. 실행권은 가져오지 않는다(집 PC가 실거래 중).

- [ ] 1. `git pull` (ai-trade)
- [ ] 2. `python tools/deploy_v5.py --apply --patch tools/patches/ledger_relay_20261111.json`
      → 매수 쉬기·다시 시작 버튼, 가상 거래 내역, ATR 부족 종목 건너뛰기(11-15 21:57) + 설정 session.ledger_relay=true. 집 PC와 같은 버전이 된다.
- [ ] 3. `python tools/take_over.py --enroll` → 회사 PC를 암호화 장부 수신 PC로 등록(PC마다 한 번).
      등록 전에는 집 PC가 실행권을 넘길 때 회사 PC가 장부를 풀 수 없다(기존처럼 계좌 새 인수).
- [ ] 4. `python tools/ai_session.py status` 로 실행권이 집 PC(DESKTOP-DLN2HJJ)에 그대로인지 확인.
- [ ] 5. 회사 봇 화면 맨 위 「⏸ 매수 쉬기」 표시 확인(실행권 없는 PC는 버튼 비활성 안내가 정상). 사용자_지시_목록 '두 PC 반영'에 회사 시각 추가.

장부는 공개 저장소에 평문으로 올리지 않는다. 실행권을 넘기는 순간(반납·양도) 봇이 live.json·orders.jsonl을 등록된 PC 인증서로 암호화해 `ledger-relay` 브랜치에 올리고, 받는 PC가 체크섬 확인 뒤 설치한다.
