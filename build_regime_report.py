"""Render the recorded comparison without selecting/tuning any parameter."""
import html
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'analysis_runs'/'regime_2026-10-27'
NAMES={'baseline':'기존 돌파','defensive_regime':'장세별 방어','regime_with_range_candidate':'횡보 매매 추가 후보'}


def main():
    report=json.loads((OUT/'validation.json').read_text(encoding='utf-8'))
    normal=[r for r in report['results'] if r['slippage_each_side']==.001]
    text='''## 실제 비교 결과 — 2026-10-27

평가 구간: 2025-01-01~2026-10-27 UTC 직전. 시간봉으로 근사한 연속 계좌 결과이며 실제 계좌 수익률이 아니다. 기본 가정은 편도 수수료 0.05%와 불리한 체결 가격 0.1%로, 추정 왕복 비용 약 0.3%다.

| 모델 | 비용 후 수익률 | 최대낙폭 | 완료 포지션 | 이익계수 |
|---|---:|---:|---:|---:|
'''
    for r in normal:
        text+=f"| {NAMES[r['model']]} | {r['return_pct']:+.2f}% | {r['max_drawdown_pct']:.2f}% | {r['completed_positions']} | {r['profit_factor']:.2f} |\n"
    text+='''
장세별 방어는 낙폭과 손실을 줄였지만 순수익 양수·이익계수 1.1 기준을 충족하지 못했다. 횡보 매매 추가 후보는 방어 모델보다 성과가 나빠 실제 엔진에 활성화하지 않았다. 승률이나 정확도가 높다는 결론을 내릴 수 없다.

### 비용 증가 시험

| 모델 | 왕복 약 0.3% | 왕복 약 0.5% | 왕복 약 0.9% |
|---|---:|---:|---:|
'''
    for kind in NAMES:
        values=[r['return_pct'] for r in report['results'] if r['model']==kind]
        text+='| '+NAMES[kind]+' | '+' | '.join(f'{x:+.2f}%' for x in values)+' |\n'
    text+='''
불리한 체결 비용은 주문 시점의 관측 가격으로 진입 조건을 확인한 뒤 적용했다. 체결 뒤에만 알 수 있는 비용을 이용해 불리한 거래를 미리 제외하지 않는다. 비용 변화는 체결 후 손절·자산·검토 잠금에도 영향을 주므로 포지션 수가 달라질 수 있다.

### 분기별 별도 계좌 진단

각 분기는 별도로 100만 원에서 시작한다. 아래 수익률을 연속 계좌 결과로 더하거나 단순 합산하지 않는다. 2026년 4분기는 10월 26일 UTC까지의 부분 구간이다.

| 분기 | 기존 돌파 | 장세별 방어 | 횡보 후보 |
|---|---:|---:|---:|
'''
    qs=report['quarterly_independent_accounts']
    for quarter in dict.fromkeys(r['quarter'] for r in qs):
        values=[next(r['return_pct'] for r in qs if r['quarter']==quarter and r['model']==kind and r['slippage_each_side']==.001) for kind in NAMES]
        text+='| '+quarter+' | '+' | '.join(f'{x:+.2f}%' for x in values)+' |\n'
    text+='''
### 적용 결정

- 장세 판별, 하락·폭락 청산, 급등 추격 금지, 국면별 추적손절과 지속되는 24시간 재진입 금지는 공통 엔진에 적용했다.
- 상승 돌파는 모의 운용에서 계속 관찰할 수 있다. 실거래 신규 매수는 경제성 검증과 실시간 체결 검증 미통과로 차단했다. 이는 사용자 승인을 기다리는 상태가 아니라, 모델의 검증 기준 미달 상태다.
- 기존 실거래 봇 소유분이 있다면 보호·청산 관리를 유지한다. 기존 계좌 코인은 자동 매도하지 않는다.
- 관련 코드·장부·보호·재현·웹 접근 검사는 102개를 통과했다. 테스트 통과는 수익성을 입증하지 않는다. 이 조사에서 실제 주문을 전송하지 않았다.
- 다음 검증은 별도 날짜의 실시간 모의 기록으로 신호·체결 비용·장애 복구를 확인하는 것이다. 결과가 나쁘다는 이유로 이번 평가 구간에 맞춰 숫자를 반복 최적화하지 않는다.
'''
    (OUT/'RESULTS.md').write_text('# 장세별 모델 검증 결과\n\n'+text,encoding='utf-8')
    research=ROOT/'REGIME_RESEARCH.md'
    existing=research.read_text(encoding='utf-8').split('## 실제 비교 결과 —')[0].rstrip()
    research.write_text(existing+'\n\n'+text,encoding='utf-8')
    body='<h1>장세별 모델 검증 결과</h1><p class="warn">방어 효과는 관측됐지만 수익성 기준은 통과하지 못했습니다. 실거래 신규 매수는 차단 상태입니다.</p>'
    body+='<p>2025-01-01~2026-10-27 UTC 직전 · 업비트 BTC/ETH 현물 · 초기 100만 원 · 실제 거래가 아닌 시간봉 근사</p>'
    body+='<table><tr><th>모델</th><th>수익률</th><th>최대낙폭</th><th>포지션</th><th>이익계수</th></tr>'
    for r in normal:
        body+=f"<tr><td>{NAMES[r['model']]}</td><td>{r['return_pct']:+.2f}%</td><td>{r['max_drawdown_pct']:.2f}%</td><td>{r['completed_positions']}</td><td>{r['profit_factor']:.2f}</td></tr>"
    body+='</table><h2>조사·규칙·검증 상세</h2><pre>'+html.escape(research.read_text(encoding='utf-8'))+'</pre>'
    page='<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>장세별 모델 조사·검증</title><style>body{max-width:1050px;margin:30px auto;padding:0 20px;background:#0f1218;color:#e6e9ef;font:15px/1.8 system-ui,"Malgun Gothic",sans-serif}h1{font-size:25px}.warn{color:#e9a454}table{border-collapse:collapse;width:100%}td,th{border:1px solid #384255;padding:10px;text-align:right}td:first-child,th:first-child{text-align:left}pre{white-space:pre-wrap;font:inherit;background:#181d27;border-radius:12px;padding:22px}</style>'+body+'</html>'
    (OUT/'report.html').write_text(page,encoding='utf-8')
    print('Report generated:',OUT/'report.html')


if __name__=='__main__':
    main()
