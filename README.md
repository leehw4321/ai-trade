# 자동거래 v4

[신규 모델 16개 비교 결과](analysis_runs/model_search/report.md): 1~4월 기초 검사·5~6월 모델 선택·7~10월 선택 미사용 평가를 분리했습니다. 이번 후보 중 승격 기준을 통과한 모델은 없습니다. 기존 자동매매 전략은 교체하지 않았으며 연구 결과 조회 패널과 오프라인 재실행 코드를 추가했습니다.

현재 버전의 실행·변경·제한은 [V4 적용내용](V4_적용내용.md)을 참고하세요. 포트는 **8768**, 바탕화면 이름은 **자동거래 v4 실행**입니다. 아래는 V3.2 원본 기록입니다.

2026-10-29 Windows / Python 3.13 working version.

[집 PC 설치·실행·작업 이어가기](HOME_PC_SETUP.md)

```powershell
python -m pip install -r requirements.txt
python install_trend_shortcut.py
python launch_trend_web.py
```

Open http://127.0.0.1:8767 or the desktop V3 shortcut. Use the in-app shutdown button to stop the server. New automatic entries never resume across restart.

Today: early-breakout paper route alongside pullback; all KRW screening; trade-flow acceleration; position-only portfolio views; investment horizons; adjustable short holding times; market indicators; hover analysis; three readable themes; compact account summary.

[Breakout V3.2](BREAKOUT_V3_2.md) | [Investment profiles](INVESTMENT_PROFILES.md) | [Market indicators and holding settings](MARKET_INDICATORS.md)

API keys and local account/order/delegation/paper state are excluded. Configure keys separately with `python setup_upbit_keys.py`. The home PC starts a new paper ledger. Public V3 paper trading does not require private API keys; account queries do. V3 entries remain paper-only. Explicitly delegated real holdings may place real exit orders.

The current entry point is `launch_trend_web.py`. Older modules remain as compatibility and analysis sources; do not use their legacy live-trading commands to launch V3.2.

```powershell
python -m unittest discover -q
```

297 tests and a public trade-feed observation passed on 2026-10-29. This is not profitability validation. Both strategy routes share the V3 paper budget and risk limits.
