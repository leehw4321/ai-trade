# 집 PC에서 오늘 작업 이어서 하기

2026-10-29 작업본 · 코인봇 V3.2 · Windows / Python 3.13

## 파일 받기

GitHub `leehw4321/coin` 저장소에서 최신 작업이 반영된 브랜치를 받습니다. **Code → Download ZIP**을 눌러 압축을 풀거나 Git이 설치돼 있다면 다음을 실행합니다.

```powershell
git clone https://github.com/leehw4321/coin.git
cd coin
```

기존 집 PC에 수정한 코드가 있으면 별도 폴더에 새로 받습니다. 기존 코드를 강제로 덮어쓰지 않습니다. 받은 폴더에 `BREAKOUT_V3_2.md`와 이 안내서가 있는지 확인하세요.

## 설치와 실행

Python 3.13 설치 시 PATH 추가를 선택합니다. 받은 폴더에서 PowerShell을 열고 실행합니다.

```powershell
python --version
python -m pip install -r requirements.txt
python install_trend_shortcut.py
python launch_trend_web.py
```

이후 바탕화면 **코인봇 V3 실행**을 누르면 됩니다. 화면 주소는 `http://127.0.0.1:8767`입니다. Python 명령을 찾지 못하면 설치 위치 또는 `py -3.13`을 사용하세요.

## API 키와 모의 시작

API 키는 전달하지 않습니다. 실제 잔고 조회가 필요하면 집 PC에서 다음 입력창으로 직접 설정합니다.

```powershell
python setup_upbit_keys.py
```

업비트에서 집 PC의 허용 IP와 필요한 권한을 확인하세요. 출금 권한은 사용하지 않습니다. 키가 없어도 공개 시세와 독립 자금 V3 모의를 사용할 수 있지만 실제 잔고는 조회 실패로 표시될 수 있습니다.

V3 운용자금을 설정하고 **V3 자동 신규 매수 시작**을 누릅니다. 재실행 후 신규 매수는 정지 상태로 시작합니다. 돌파 신호는 체결 데이터를 최소 60초 수집하고 완료 봉이 준비된 다음 판단합니다.

집 PC는 새 모의 장부로 시작합니다. 사무실의 계좌·주문·위임·모의 원금·손익은 자동 이전되지 않습니다. V3 신규 매수는 모의 전용입니다. 포트폴리오의 실제 보유분을 명시적으로 위임하면 실제 청산이 가능하므로, 처음에는 위임하지 말고 모의부터 확인합니다.

## 오늘 작업 내용과 주요 파일

| 기능 | 파일 |
|---|---|
| 초기 돌파 감지·체결 집계 | `breakout_strategy.py` |
| 눌림 전략·공통 청산 | `scalping_strategy.py` |
| V3 모의 주문·자금·위험 관리 | `auto_trader.py` |
| 실시간 체결·호가 | `realtime_sync.py` |
| 화면·세 가지 테마·자산 현황 | `trend_dashboard.html`, `portfolio_ui.js` |
| 종목명 정밀 분석 상세창 | `scalp_tooltip.js`, `scalp_details.py` |
| 주요 지표·보유시간 설정 | `market_indicators.py`, `holding_settings.py` |

실제·모의 보유 전용 보기, 단타·스윙·장기 분류, 뉴스 아래 주요 지표, 원/엔(100엔), 프로그램 종료 기능도 포함합니다. [돌파 전략 설명](BREAKOUT_V3_2.md), [투자 기간 기준](INVESTMENT_PROFILES.md), [지표·시간 설정](MARKET_INDICATORS.md)을 참고하세요.

VS Code 또는 Codex에서 이 저장소 폴더를 열어 작업을 이어갑니다. 검증 명령:

```powershell
python -m unittest discover -q
```

자동 테스트와 공개 데이터 수신 확인은 수익성 검증 완료를 뜻하지 않습니다. 과거 아이리스 급등 순간의 체결 이력은 코드만으로 복원되지 않습니다. 두 모의 전략 경로는 V3 자금·위험 한도를 공유합니다.

이전 날짜의 분석 도구를 실행하려면 별도의 원본 과거 데이터와 설정 스냅샷이 필요합니다. 해당 자료는 개인 거래 기록과 함께 이동시키지 않으며, 단위 테스트는 예제 설정으로 독립 실행됩니다.

## PC 전환과 종료

화면의 **프로그램 종료**를 누릅니다. 브라우저만 닫으면 서버는 계속 실행됩니다. 종료하면 실제 위임 보유분의 감시도 중단되며, 실제 거래소의 미체결 주문은 유지됩니다. 사무실·집에서 같은 실제 보유분을 동시에 위임 관리하지 마세요.

`.env`, `trend_data/`, `settings.json`, 계좌·거래 기록·로그와 분석 실행 결과는 GitHub와 전달 압축본에서 제외합니다. 사무실의 실제 주문·위임 상태를 확인한 뒤 이동하세요.
