# 비공개 최종 장부 생성·암호화 송수신

정본 tools/handoff_cli.py와 secure_handoff.py에 구현했다. 합성 검사와 실제 OpenSSL 암호 왕복·변조 거부 검사를 통과했다. 인수인계 요청 때 담당 AI가 아래 명령을 실행하며 실제 전송·수신·복원·거래 시작 결과를 구분해 기록한다. 최초 PC 페어링에는 수신 PC에서 만든 공개 인증서와 신뢰된 별도 경로로 확인한 SHA256이 필요하다. 개인키는 수신 PC의 Git 제외 `private_handoff/identities/` 안에만 보관하고 Windows ACL로 현재 사용자와 SYSTEM만 접근하도록 제한한다. 공개 인증서는 Git에 등록할 수 있지만 개인키를 전송하지 않는다.

## 가짜 자료 실제 암호 검사

`python -B real_crypto_selftest.py`는 설치된 Git OpenSSL을 사용해 가짜 최종 ZIP을 AES-256-GCM/RSA-OAEP-SHA256으로 암호화·복호화하고 변조 거부를 검사한다. 네트워크·실제 장부·주문·실행권을 사용하지 않으며 임시 시험 키와 자료는 종료 시 지운다. 이 모듈의 단위 검사는 후보 폴더에서 `python -B -m unittest test_secure_handoff test_handoff_cli -q`다. 별도로 복사한 exporter 검사는 정본 tools 의존 경로를 지정해 실행한다.

## 수신 PC 한 번 등록

`python handoff_cli.py --private-root "<집 작업폴더>/private_handoff" init-recipient --name home`

출력된 공개 인증서와 SHA256만 송신 PC의 신뢰된 수신자 프로필로 등록한다. 개인키는 위 폴더 밖에 복사하거나 Git에 올리지 않는다. 최초 신뢰 확인을 자동으로 가정하지 않는다.

## 송신: 정상 종료 후 생성·암호화·전송

`python handoff_cli.py --private-root "<회사 작업폴더>/private_handoff" publish --app "<회사 실행폴더>" --export-tool "<정본>/tools/export_home_handoff.py" --recipient-cert "<등록된 집 공개인증서>" --recipient-pin "<확인한 SHA256>" --remote https://github.com/leehw4321/ai-trade.git --credentials-file "<로컬 .env 경로>" --publish --stop-source`

`--publish --stop-source`를 함께 명시할 때만 일반 `stop.txt`·`stop_shadow.txt`를 요청하고 최대180초 정상 종료를 기다린 뒤 최종 생성·암호화·전송을 잇는다. 로컬 일반/위험 미확정 주문이 있으면 정지 요청 자체를 보류한다. 강제 종료·주문 취소·실행권 강제 변경은 하지 않으며, 시간 초과/실패 시 중지 요청을 남기고 자동 재시작하지 않는다. `--stop-source` 없이 실행하면 호스트가 먼저 정상 종료해야 한다. `--publish` 없는 dry-run에서는 `--stop-source`만 있어도 정지시키지 않는다. 이후 미확정 주문·거래소 대기/예약 주문 없음·안정 장부·해제된 실행권 체크포인트를 확인한다. 소스 생성기는 session_git·인증 파일·중첩 인계 폴더/ZIP를 제외해야 한다. 참고용 `live_restore_allowed=false` 묶음은 거부한다. 이미 있는 최종 ZIP는 `--package`로 지정할 수 있다.

독립 임시 Git 저장소에 암호문 `handoff.cms`와 공개 전송 영수증만 추가하고 `private-handoff/<수신자지문>/<암호문SHA256>` 새 브랜치에 강제 옵션 없이 push한다. 원본 ZIP·장부·manifest·키는 staging하지 않는다. 전송 성공은 수신·복원·봇 시작 성공과 다르다.

송신자 신뢰는 사용자가 지정한 Git 원격 저장소의 쓰기 권한·인증과 지정된 브랜치에 의존한다. 수신자 공개키 암호화와 GCM 인증은 별도의 송신자 전자서명이 아니다. 공개 인증서만 아는 다른 사람도 암호문을 만들 수 있으므로 임의 저장소·출처 불명 브랜치를 신뢰하지 않는다. 수신 시 해제된 원격 장부 체크포인트와 실제 계좌도 다시 대조한다.

## 수신: 검증 먼저, 복원은 명시적으로

`python handoff_cli.py --private-root "<집 작업폴더>/private_handoff" receive --app "<집 실행폴더>" --remote https://github.com/leehw4321/ai-trade.git --branch "<전송 결과 브랜치>" --recipient-cert "<집 공개인증서>" --recipient-pin "<확인한 SHA256>" --private-key "<집 private_handoff/identities/home/private-key.pem>" --credentials-file "<집 로컬 .env 경로>"`

기본은 다운로드·인증 복호화·CRC/manifest/원문SHA/정규화체크포인트/현재 계좌·실행권을 점검하는 dry-run이다. `--apply`만 추가해야 stopped 상태에서 백업 후 장부·설정·상태를 복원한다. 실행 코드는 복사하지 않는다. 회사와 집의 보호 목록을 config와 파일 양쪽에서 합집합으로 보존하고 DOGE 보호를 유지한다. 손실 기준·기초일·과거 주문안은 초기화하지 않는다. 봇 시작은 기존 실행권·승인 코드 확인 뒤 호스트가 별도로 수행한다.

`pnl_baseline.json`이 송신 PC에 있으면 ZIP 포함과 SHA 일치를 두 번 확인하고 수신 후에도 동일 기준 파일을 검증한다. 송신 묶음에 없고 수신 PC에만 기존 기준이 있으면 어떤 것이 맞는지 임의 선택하지 않고 대사를 요구한다. 관측·과거 분석 자료는 암호화 ZIP에 남지만 기본 `--apply`는 승인된 런타임 상태 경로만 복원한다. API 조회 묶음이30초를 넘으면 실패하며 실행권은 조회의 마지막에 다시 읽는다.

## 중간 실패와 수동 복구

`data/handoff-transition.lock`은 작업 예외/강제 종료 시 남는다. 복원은 첫 실제 파일 쓰기 전에 `data/handoff-restore-pending.json`에 대상 해시·백업 위치를 남기며, 파일 설치와 최신 계좌/원격 체크포인트 후검사가 모두 통과해야 지운다. 실패하면 설치 파일을 롤백하고 pending 표식을 남긴다. SessionGuard는 두 표식 또는 `data/reconciliation/APPLYING`이 있으면 취득/주문을 막아야 한다.

표식을 맹목적으로 삭제하지 않는다. 양 PC 봇·shadow 정상 종료, 살아 있는 인계 작업 없음, 미확정 주문/거래소 대기 주문 없음, 원격 소유 해제, 백업/복원 대상 해시와 현재 파일의 전수 대사, DOGE·추가 사용자 보호 확인 후 호스트가 승인된 복구 절차로 표식만 정리한다. 원격 체크포인트를 임의 맞추거나 새 계좌 인수로 손실 기준을 초기화하지 않는다. 정상 publish가 끝나면 transition 표식을 지우지만 송신 거래 봇을 자동 재시작하지 않는다.
