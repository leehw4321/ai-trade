# ai-trade — 업비트 자동거래 봇 V5

15분봉 상대강도 순환 전략의 모의·그림자 검증과 제한된 실거래를 하는 Windows용 Python 봇이다. 두 AI(Claude, ChatGPT)가 문서로 토론하고 합의한 명세를 코드에 반영하는 방식으로 개발한다.

> **수익성은 검증되지 않았다.** 지금까지의 테스트는 기능 테스트이며 비용을 뺀 수익을 보장하지 않는다. 실거래는 기본 **꺼짐**이다. 실제 돈이 걸린 기능은 사용자의 명시적 승인 후에만 켠다.

## 구조

| 경로 | 내용 |
|---|---|
| [v5/](v5/README.md) | 봇 본체. 전략·모의 장부·그림자 비교·실거래·계좌 조회·PC 간 실행권 |
| [debate_tools/](debate_tools) | Claude ↔ ChatGPT 연속 토론 진행기와 규칙·근거·합의 표 |
| [docs/ai-history/](docs/ai-history/README.md) | 날짜별 작업 기록, 결정 근거 대장, 토론 스냅샷 |
| [docs/](docs) | 작업 인계, 전략 조사 자료(`reference/`) |
| [tools/](tools) | 이력 도구(`ai_session.py`), 코드 사본 동기화(`sync_v5.py`) |
| [HANDOFF.md](HANDOFF.md) | **지금 상태·사용자 지시·미해결 항목. 작업을 시작하기 전에 가장 먼저 읽는다** |

이전 버전(V3·V4: 추세·돌파 봇과 대시보드)은 삭제했다. 필요하면 태그 `legacy-v3-v4-2026-11-02`로 복구할 수 있다.
```powershell
git checkout legacy-v3-v4-2026-11-02 -- <파일>
```

## 전략 요약 (V5, 모두 미검증 가설)

- 판단 주기 15분봉, 관측 72봉(18시간), 거래대금 상위 30종목.
- 순위 = 18시간 상승률. 상위 3위 안·상승률 양수인 종목을 매수 후보로 하고, 순위가 5위 밖으로 밀리면 순환 매도.
- 최대 3종목, 종목당 25%·총투입 50% 상한, 거래당 계획 위험 0.2%.
- 손절: 진입가 −3×ATR, 단 진입가에서 8%보다 멀지 않게 제한. 손절가는 내려가지 않는다.
- 과열 추격 방지: 18시간 상승률이 15% 이상이면 신규 매수 보류.
- 익절(EXIT-T1): 본전 잠금 → 고점−2×ATR 추적 → 고점 이익 50% 반납 시 청산. 그림자 비교군으로만 검증 중이며 실거래는 꺼짐.
- 모의 장부 `P10`·`ALL`과 그림자 변형(L·k·M 격자 + EXIT 비교군)을 같은 신호로 비교한다.

수치의 근거와 불확실성은 [결정 근거 대장](docs/ai-history/decision-log.md)에 있다.

## 설치와 실행 (Windows, Python 3.10+)

외부 패키지는 필요 없다(표준 라이브러리만 사용).

```powershell
git clone https://github.com/leehw4321/ai-trade.git
cd ai-trade\v5
Copy-Item ..\.env.example .env      # 업비트 키를 직접 입력. 파일을 Git에 올리지 않는다
python -m unittest discover -q      # 기능 테스트
python account_sync.py              # 계좌 조회 전용 (주문 없음) -> data\account_live.html
python bot.py                       # 모의 운영 + 전체 화면 -> data\status.html
python shadow.py                    # 그림자 비교 (별도 프로세스)
```

종료는 `v5\stop.txt`(봇), `v5\stop_shadow.txt`(그림자), `v5\account_sync_stop.txt`(조회기) 파일 생성. 자세한 실행·실거래 조건·PC 간 실행권 양도는 [v5/README.md](v5/README.md).

## 안전 규칙

- 실거래 설정은 `v5/config.json`의 `live`. GitHub의 기본값은 `enabled=false`, `auto_proposal=false`다. 실제 운용 PC의 설정은 Git에 올리지 않는다.
- 같은 계좌를 두 PC가 동시에 실거래하지 않는다. PC를 바꿀 때는 실행권 양도(`session_guard`)와 `data/` 비공개 이전이 필요하다.
- API 키·시크릿·토큰, 계좌번호, 주문 UUID, `data/`, `.env`는 저장소에 올리지 않는다. 종목·금액·손익 기록은 사용자 허용으로 문서에 남긴다. **이 저장소는 공개(Public)다.**
- 키는 출금 권한 없이 만들고, 업비트 허용 IP를 확인한다.

## AI 협업과 이력

두 AI와 여러 PC를 오가며 작업하므로 이력을 한곳에서 관리한다.

```powershell
python tools/ai_session.py start    # 최신 코드, 인계 요약, 운용 상태, 코드 어긋남 확인
python tools/ai_session.py status   # 지금의 실제 운용 상태 (상태를 말하기 전에 항상 확인)
python tools/ai_session.py end -m "요약"   # 비밀정보 검사 -> 토론 스냅샷 -> 커밋 -> 푸시
```

- Claude Code는 [CLAUDE.md](CLAUDE.md), Codex·ChatGPT는 [AGENTS.md](AGENTS.md)를 세션 시작 때 읽는다.
- 코드는 이 저장소(`v5/`)에서만 고친다. 실행 중인 사본에는 `python tools/deploy_v5.py`(점검)와 `--apply`(테스트 → 정상 종료 → 백업 → 적용 → 재시작 → 실패 시 롤백)를 쓴다. `--watch`는 `tools/APPROVED_COMMIT`에 적힌 승인 커밋만 자동 배포한다. 봇이 꺼져 있을 때의 단순 복사는 `tools/sync_v5.py --apply`.
