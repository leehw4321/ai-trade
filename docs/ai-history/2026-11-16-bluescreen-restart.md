# 2026-11-16 집 PC 블루스크린 뒤 재시작 사고와 수정 (Claude 단독, 교차 검증 없음 — 11-11 지시)

## 경과 (KST)
- 05:41:52 직후 블루스크린 0x133(DPC_WATCHDOG_VIOLATION) → 05:44 자동 재부팅. 원인 드라이버 미확정(C:\Windows\MEMORY.DMP 미분석, WinDbg 없음). 후보: Mirage Driver(2008), Realtek NIC(2019), imation NVMe 펌웨어.
- 봇 자동 시작 없음 → 05:41~09:48 손절 감시 중단(약 4시간).
- 09:42 사용자 '이 PC에서 시작' → 봇 시작 실패: 남은 bot.lock 확인이 tasklist cp949 출력을 UTF-8(Python 3.15 기본)로 읽어 즉시 종료.
- 09:48 잠금 정리 후 재시작 → take_over.start_bot이 키 환경을 넘기지 않아 봇이 키 없이 실행권 보유, 09:58~10:01 SNT 본전 매도 등 주문 실패.
- 10:01 launch_v5로 재시작(키 포함) → SNT 매도 체결 10:01:47.
- 10:09 수정 배포(f4b869b, 사용자 "배포"), APPROVED_COMMIT 갱신으로 회사 PC도 감시 배포 대상.

## 자료 점검
- data 258개 파일 검사: 실거래 장부·주문·계좌 자료 정상. NUL 손상 4개(모두 05:41:51~53 쓰기 중): shadow EXIT_T1/EXIT_T1T2/EXIT_T1T2T3 ledger.json, exit_study/alive.json.
- EXIT 장부 3개는 trades.jsonl 재생으로 복원(같은 방법으로 정상 장부 12개 재구성 시 현금·보유·수량·초기손절·재진입금지 일치). H·A0·last_4h는 정상 장부에서(15개 장부 H 시계열 전부 동일). ATR은 형제 장부·rotation_audit(정시 일치 확인). 추적 손절 peak/stop은 strategy.ratchet에 진입~05:41 1분봉 재생(청산 신호 없음 = 보유 기록과 일치). 공개 시세 재계산이라 원본과 완전 동일 보장은 아님.
- 백업: 코인 트레이딩/bot_data_backup_20261116_094618 (손상 원본 shadow_broken/).

## 수정 (f4b869b)
- take_over.start_bot: key_env() 전달. upbit.ENV_FILES에 두 단계 위 .env 추가.
- bot.initial_claim: 실거래인데 키 없으면 실행권을 잡지 않고 사유 표시.
- bot.py·shadow.py 잠금 확인 encoding='mbcs'. 코인 트레이딩/launch_v5.py 동일 수정(f786994).
- 시험 V5 682 / 도구 163 통과(신규 10).
- 집 PC 시작프로그램 '코인봇 V5 자동 시작'(autostart_v5.py) 추가, 바탕화면 아이콘 정리(시작/관찰창/중지 3개).

## 남은 일
- live.json 저장 PermissionError(04:28, 10:09:48 연속성 복원): 다른 프로세스가 읽는 중 os.replace 실패로 추정, 미조사.
- 블루스크린 원인 드라이버 확정, 자동 로그인 여부, 회사 PC 바탕화면 정리.
