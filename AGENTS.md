# Codex·ChatGPT 시작 규칙 (이 파일은 세션 시작 때 읽는다)

이 프로젝트는 PC를 옮기며 Claude와 ChatGPT(Codex)가 번갈아 작업한다. 이력을 찾는 데 시간을 쓰지 않도록 아래 순서를 지킨다.

1. `python tools/ai_session.py start` 를 실행한다. 최신 코드를 받고 인계 요약, 최근 기록, 실행 중인 프로세스, 미커밋 변경을 한 번에 보여 준다.
2. [HANDOFF.md](HANDOFF.md)를 읽는다. PC·AI 교대 직후라면 [docs/ai-history/내일_회사에서_다시.md](docs/ai-history/내일_회사에서_다시.md)와 [decision-log.md](docs/ai-history/decision-log.md)를 먼저 읽는다. 현재 상태, 사용자 지시, 미해결 항목, 위치 지도가 있다.
3. 필요하면 [docs/ai-history/README.md](docs/ai-history/README.md)에서 날짜별 기록과 토론 기록을 찾는다.
4. 작업을 마치면 HANDOFF.md의 '현재 상태'와 오늘 날짜 기록(`docs/ai-history/YYYY-MM-DD.md`)에 추가한 뒤 `python tools/ai_session.py end -m "요약"` 을 실행한다. 커밋과 푸시까지 한다.

지킬 것
- 기록은 추가만 한다. 지우거나 덮어쓰지 않고 정정은 새 항목으로 남긴다.
- 실제 응답을 받은 발언만 그 AI의 발언으로 적는다. 단독 검토를 합의로 쓰지 않는다. 원문·요약·재구성을 구분해 표시한다.
- 실거래에 영향을 주는 변경(주문·청산 로직, 매도 권한, 한도, 손실 중단선, 위임)은 사용자의 명시적 승인 없이 켜지 않는다. 기본값은 꺼짐이다.
- 종목·수량·금액·평단·손익은 사용자 허용(2026-11-02)으로 기록해도 된다. API 키·시크릿·토큰·계좌번호·주문 UUID·`.env`·`data/` 파일 자체는 올리지 않는다. 이 저장소는 공개다.
- 같은 계좌를 두 PC가 동시에 실거래하지 않는다.
- **운용 상태(실거래 켜짐 여부, 봇 실행 여부, 보유 종목 수)를 말하기 전에 반드시 `python tools/ai_session.py status`를 실행하고 그 결과만 근거로 삼는다.** 과거 확인 결과를 현재 사실처럼 말하지 않는다. 같은 PC의 다른 세션이 설정을 바꿀 수 있다.
- 실거래 설정(`config.json`의 `live`)을 바꾸는 세션은 한 번에 하나만 둔다. 바꾼 세션은 즉시 `HANDOFF.md`와 날짜 기록에 시각·내용을 적는다.
