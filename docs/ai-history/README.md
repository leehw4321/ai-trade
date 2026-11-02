# AI 작업 이력 — 저장 위치와 불러오는 법

## 원칙
- **저장 위치는 이 저장소(`ai-trade`) 한 곳이다.** 다른 PC·다른 AI 세션이 만든 기록도 여기로 모은다. 흩어진 곳(바탕화면 문서, 다른 폴더, 다른 저장소)에만 두지 않는다.
- GitHub에 있어야 어느 PC에서든 `git pull` 한 번으로 이어진다. 시작할 때 `python tools/ai_session.py start`, 끝낼 때 `python tools/ai_session.py end -m "요약"`.
- 두 AI가 같은 문서를 본다: Claude Code는 `CLAUDE.md`, Codex·ChatGPT는 `AGENTS.md`를 시작 때 읽고, 둘 다 `HANDOFF.md`로 안내한다.

## 문서 구성
| 파일 | 용도 | 갱신 방식 |
|---|---|---|
| `docs/ai-history/내일_회사에서_다시.md` | 두 AI가 같은 이해로 출발하는 종합 인계(타임라인·결정·GitHub 밖 정보·시작 루트) | 큰 전환(PC·AI 교대) 때 새로 쓴다 |
| `docs/ai-history/decision-log.md` | 결정 근거 대장(근거·대안·약한 고리). 상대 AI 복귀 시 제시해 토론 | 추가만 |
| `/HANDOFF.md` | 지금 상태·사용자 지시·미해결·위치 지도. 새 세션이 가장 먼저 읽는다 | 세션 끝마다 현재 값으로 고친다(이력은 아래 날짜 기록에 남는다) |
| `docs/ai-history/YYYY-MM-DD.md` | 그날의 시간순 작업 기록: 요청, 결정, 코드 변경(커밋), 테스트, 미해결 | 추가만 한다. 정정은 새 항목 |
| `docs/ai-history/debates/YYYY-MM-DD_토론.md` | 두 AI 토론 스냅샷(합의 표 + 회차 발언). `ai_session.py end`가 자동 저장 | 날짜별 덮어쓰기 = 그날 마지막 상태 |
| `docs/ai-history/codex-session/` | 다른 세션(Codex)이 만든 기록 모음 | 원본 보존 |
| `docs/ai-history/originals/` | 회사 PC 등에서 확보한 대화 원문(있을 때) | 원문 그대로 |
| `debate_tools/` | 토론 진행기·규칙·근거(`토론규칙.md`, `운용목표.md`, `공유_근거자료.md`, `토론.md`) | 규칙 변경은 날짜 기록에도 적는다 |

## 불러오는 법 (새 PC·새 세션)
1. `git pull` (또는 처음이면 `git clone https://github.com/leehw4321/ai-trade.git`)
2. `python tools/ai_session.py start` — 최신 코드, 인계 요약, 최근 기록, 실행 중인 프로세스, 미커밋 변경을 출력한다.
3. 더 알고 싶으면 날짜 기록 → 토론 스냅샷 순서로 읽는다.
4. 토론을 이어가려면 `v5/README.md`와 `HANDOFF.md` §6을 따른다.

## 남기는 법 (세션 끝)
1. 오늘 날짜 기록 `docs/ai-history/YYYY-MM-DD.md`에 이번 작업을 추가한다(없으면 만든다).
2. `HANDOFF.md`의 '현재 상태'와 '미해결'을 현재 값으로 고친다.
3. `python tools/ai_session.py end -m "요약"` — 비밀정보 검사, 토론 스냅샷 저장, 커밋, 푸시.

## 기록 규칙
- 날짜는 한국시간. 추가만 하고 지우지 않는다.
- 출처를 구분한다: **원문**(그대로 보존) / **요약**(누가 정리) / **재구성**(복원, 원문 아님을 명시).
- 실제 응답을 받은 발언만 해당 AI의 발언으로 적는다. 단독 검토를 합의로, 계획을 완료로 적지 않는다.
- 코드 변경은 커밋 해시와 테스트 결과를 함께 적는다.
- 넣지 않는 것: API 키, 계좌번호, 잔고·보유 수량, 주문 UUID, `.env`, `data/`. 저장소가 공개일 수 있다. `ai_session.py end`가 키 형태 문자열을 검사하지만 잔고 수치는 막지 못하므로 사람이 확인한다.
