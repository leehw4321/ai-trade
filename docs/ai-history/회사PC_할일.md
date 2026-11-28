# 회사 PC 할 일

## ★ 2026-11-28 집 PC → 회사 PC (집 PC Claude 작성, 실행권은 집 PC가 계속 보유 — 가져오지 말 것)
회사 PC에서 Claude에게 "회사PC 할일 해" 또는 "동기화"라고 하면 아래를 순서대로 한다. (11-27 항목이 안 끝났으면 그것부터.)
- [ ] 1. `git pull` (ai-trade) 후 [사용자_지시_목록](사용자_지시_목록.md)의 11-28 줄들을 읽는다(화면·주문 기능 8건).
- [ ] 2. `python tools/deploy_v5.py --apply` (설정 조각 없음, config.json 그대로). tools/APPROVED_COMMIT = 24828c6(집 PC 22:54 배포본, 매도량 지정량 포함).
      tools/deploy_pause.txt가 있어 회사 자동 배포 감시는 멈춰 있으므로 직접 실행한다.
- [ ] 3. `python tools/take_over.py --config-diff` → "차이 없음"이어야 한다. 다르면 고치지 말고 사용자에게 보고.
- [ ] 4. `python tools/ai_session.py status`로 실행권이 집 PC(DESKTOP-DLN2HJJ)에 그대로인지 확인. 회사 화면에서 순위표 [직접 매수] 버튼은 실행권 PC에서만 나온다(정상).
- [ ] 5. 회사 PC 자동 시작에 모의 운용(paper_vc)·체결 수집(flow_collect)은 넣지 않는다(집 PC에서만, 12-12까지).
- 오늘(11-28) 바뀐 것 요약: 봇 장부로 올리기(보호 유지)·매매 권한 넘기기 분리, 권한 변경 때 손실 집계 유지, 순위표 [직접 매수](현재가·희망가 ▲▼·지정가/시장가), 상세 창 직접 매도(희망가 ▲▼·지정량), 사용자 주문은 보호·봇 손실 집계 분리·user_trades.jsonl 기록, 매일 보고서에 사용자 vs 봇 승패 비교.

## ★ 2026-11-27 집 PC → 회사 PC (집 PC Claude 작성, 실행권은 집 PC가 계속 보유 — 가져오지 말 것)
회사 PC에서 Claude에게 "회사PC 할일 해" 또는 "동기화"라고 하면 아래를 순서대로 한다.
- [ ] 1. `git pull` (ai-trade) 후 **[2026-11-27 기록](2026-11-27.md)부터 읽는다**: 11-26 21:54~11-27 08:52 거래 중단 사고(주문안·봇 설정 지문 어긋남, 약 11시간 주문 0건)의 정확한 경과·원인·재발 방지.
- [ ] 2. `python tools/deploy_v5.py --apply` (설정 조각 없음, config.json 그대로) → 코드: 일일 리포트·최근 실현손익 접기(fc9c94f), 지문 어긋남 경보·shadow 자가 재시작(396aecc). 집 PC는 11-27 09:20·09:52 반영.
      tools/deploy_pause.txt가 있어 회사 자동 배포 감시는 멈춰 있으므로 직접 실행한다.
- [ ] 3. `python tools/take_over.py --config-diff` → "차이 없음"이어야 한다(집 PC는 11-27 "차이 없음"). 다르면 고치지 말고 사용자에게 보고.
- [ ] 4. 중복 pnl.py 확인: `Get-CimInstance Win32_Process | ? CommandLine -match 'pnl.py'` 가 2개 이상이면 사용자에게 알리고 승인 뒤 정리(집 PC는 4개 중복이었음). 이후 take_over는 중복으로 켜지 않는다.
- [ ] 5. `python tools/ai_session.py status`로 실행권이 집 PC(DESKTOP-DLN2HJJ)에 그대로인지, 회사 봇 화면에 「주문 멈춤: 주문안 설정 지문…」 경보가 없는지 확인.
- [ ] 6. 사용자에게 [이익보호 매도 뒤 재상승 시뮬레이션 리포트](2026-11-27-profit-rebuy-sim.md) 요약 보고(결론: 시험한 규칙 모두 손실, 규칙 추가 비권장, 실거래 변경 없음).
- 주의: `tools/test_take_over.py`는 11-27 전까지 실제 운용 폴더를 건드렸다(테스트가 운용 shadow를 멈춤). 지금 판은 임시 폴더로 가려졌지만, pull 전 옛 판으로 tools 테스트를 돌리지 말 것.

## 2026-11-15 항목 (집 PC Claude가 2026-11-15 11:00에 작성)

회사 PC에서 Claude에게 "회사PC 할일 해"라고 하면 아래를 순서대로 한다. 실행권은 가져오지 않는다(집 PC가 실거래 중).

- [x] 1. `git pull` (ai-trade)
- [x] 2. `python tools/deploy_v5.py --apply --patch tools/patches/ledger_relay_20261111.json`
      → 매수 쉬기·다시 시작 버튼, 가상 거래 내역, ATR 부족 종목 건너뛰기(11-15 21:57) + 설정 session.ledger_relay=true. 집 PC와 같은 버전이 된다.
- [ ] 3. `python tools/take_over.py --enroll` → 회사 PC를 암호화 장부 수신 PC로 등록(PC마다 한 번).
      등록 전에는 집 PC가 실행권을 넘길 때 회사 PC가 장부를 풀 수 없다(기존처럼 계좌 새 인수).
- [x] 4. `python tools/ai_session.py status` 로 실행권이 집 PC(DESKTOP-DLN2HJJ)에 그대로인지 확인.
- [x] 5. 회사 봇 화면 맨 위 「⏸ 매수 쉬기」 표시 확인(실행권 없는 PC는 버튼 비활성 안내가 정상). 사용자_지시_목록 '두 PC 반영'에 회사 시각 추가.

장부는 공개 저장소에 평문으로 올리지 않는다. 실행권을 넘기는 순간(반납·양도) 봇이 live.json·orders.jsonl을 등록된 PC 인증서로 암호화해 `ledger-relay` 브랜치에 올리고, 받는 PC가 체크섬 확인 뒤 설치한다.

## 11-16 12:04 회사 PC 진행 결과
- 1·2 완료: 772c7d6까지 받고 `deploy_v5.py --apply --patch ledger_relay_20261111.json` (백업 _backup/20261116_120405), 전체 테스트 687개 통과, 코드 어긋남 없음.
- 4 완료: 실행권은 집 PC(DESKTOP-DLN2HJJ) 그대로.
- 5 완료: 회사 화면 「▶ 매수 중 · (버튼은 실행권이 있는 PC의 봇 화면에서만 동작)」, 「실험·관찰 창 모두 접기/펼치기」, 관찰창 재시작 확인.
- 3 **미완료**: `take_over.py --enroll`이 openssl을 `C:/Program Files/Git/usr/bin/openssl.exe`에서 찾는데, 회사 PC Git은 `D:\Git`이라 그 경로에 없음(FileNotFoundError). 회사 PC openssl은 `D:\Git\ucrt64\bin\openssl.exe`. 개인키 생성은 사용자 승인 후 진행. 그 전에는 집이 실행권을 넘겨도 회사 PC는 장부를 못 풀고 계좌 새 인수로 시작.
