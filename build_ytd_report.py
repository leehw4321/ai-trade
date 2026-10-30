"""Build the Korean research report from frozen, independently saved results."""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'analysis_runs' / 'ytd_v4'


def main():
    summary = json.loads((OUT/'signals'/'summary.json').read_text(encoding='utf-8'))
    proxy = json.loads((OUT/'proxy_execution.json').read_text(encoding='utf-8'))
    protocol = json.loads((OUT/'protocol.json').read_text(encoding='utf-8'))
    scenarios = proxy['scenarios']
    valid = summary['valid_market_minutes']/summary['market_minutes']*100
    lines = [
        '# 자동거래 v4 올해 조건 발생·손익 재현 결과', '',
        f"검사 기간: **{summary['start_kst']} ~ {summary['end_kst']}**. 한국시간 기준이며 10월은 부분 월입니다.", '',
        '**이 결과는 현재 대표 10종목의 눌림 전략 연구입니다. 전체 299종목·초기 돌파 경로를 포함한 실제 v4 연간 수익률이나 미래 기대수익률이 아닙니다.**', '',
        '## 조건 발생 횟수', '',
        f"총 **{summary['total_candle_signals']:,}개 분 단위 봉 신호**가 발생했습니다. 같은 돌파 구간에서 여러 분에 조건이 충족될 수 있어 이 수치를 서로 독립적인 거래 건수로 해석하면 안 됩니다.", '',
        '| 종목 | 봉 검증 통과 구간 | 봉 조건 충족 시점 |', '|---|---:|---:|',
    ]
    for r in sorted(summary['markets'], key=lambda r: -r['candle_signal_count']):
        lines.append(f"| {r['market']} | {r['valid_candle_minutes']/r['minute_slots']*100:.2f}% | {r['candle_signal_count']:,}회 |")
    lines += ['', f"전체 {summary['market_minutes']:,}개 종목·분 판단 중 봉 검증 통과는 **{valid:.2f}%**였습니다. 나머지 **{100-valid:.2f}%**는 주로 무거래 분봉·공백이 최근 전체 봉 창 검사에 걸려 평가를 막았습니다. 공백을 가상 봉으로 채우지 않았습니다. 데이터 없는 경우를 거래 0회로 처리하지 않았습니다.", '',
        '## 원금 100만 원의 조건부 모의 손익', '',
        '현행 원금·종목당25%·총50%·최대2종목·거래위험0.25%·합산위험0.5%·ATR기준0.5%, 보유45분·진행확인15분을 사용했습니다. 실제 저장된 배분 설정 파일이 없어 코드 기본값을 고정했습니다. 권장값이나 최적화 결과가 아닙니다.', '',
        '수수료는 편도0.05%, 불리한 가격 충격도 편도0.05%입니다. 호가 스프레드는 기준0.04%와 불리한0.0999%를 가정했습니다. 실제 호가 잔량과 대기열을 알 수 없으므로 충분한 잔량과 지정가 접촉 체결을 가정했습니다. 체결 가능성을 낙관적으로 가정한 연구입니다.', '',
        '| 가정 스프레드·분봉 내 순서 | 매수 체결 | 완료 거래 | 기간 손익률 | 순손익 | 최대 낙폭 |', '|---|---:|---:|---:|---:|---:|',
    ]
    for s in scenarios:
        lines.append(f"| {s['spread_ratio']*100:.4f}% · {s['intrabar_path']} | {s['entry_fill_count']} | {s['closed_trade_count']} | {s['return_pct']:+.4f}% | {s['pnl']:+,.0f}원 | {s['max_drawdown_pct']:.4f}% |")
    audited=[s for s in scenarios if s.get('ledger_audit')]
    if audited:
        lines += ['', '장부 검증: 일부 시나리오에서 기존 엔진이 0.00000001코인 미만 잔량을 지운 뒤 원본 검증기는 그 잔량을 남기는 반올림 불일치가 확인됐습니다. 연구 전용 감사로 실제 엔진의 잔량 제거를 재현하고, 체결 내역에서 현금을 독립 재구성했습니다. 원본 검증 통과라고 주장하지 않으며 원화 손익을 맞추기 위해 현금을 조정하지 않았습니다. 상세 잔량·원가는 각 시나리오 ledger_audit에 기록했습니다. 운영 코드는 수정하지 않았습니다.']
    lines += ['', 'O=시가, H=고가, L=저가, C=종가입니다. 이 순서와 지정가 체결 시각은 분봉만으로 확인할 수 없어 두 순서를 시험했습니다. 네 결과의 범위는 통계적 신뢰구간이 아닙니다. 미청산분이 있으면 마지막 가정 호가에서 수수료·가격 충격을 반영해 평가하며 수익을 좋게 보이기 위한 강제 청산을 하지 않았습니다.', '',
        '## 무엇이 진입을 줄였나', '',
        '봉 공백 검사 외에도 진입 직전 손절 거리0.4~1.5%, 비용이 손절 거리의25% 이하, 추격 차단, 호가 차이·잔량, 보유 슬롯·계획 위험 한도를 통과해야 합니다. 기본 비용은 스프레드가0이어도 왕복수수료0.1%+가격충격충당0.1%=0.2%입니다. 비용 조건 때문에 손절 거리는 사실상 최소0.8%, 스프레드0.04% 가정이면 약0.96% 이상이어야 합니다.', '',
        '기준 가정의 신호별 예약 전 차단 사유:', '', '| 사유 | 시점 수 |', '|---|---:|',
    ]
    for reason, count in sorted(scenarios[0]['signal_rejection_reasons'].items(), key=lambda r: -r[1]):
        lines.append(f'| {reason} | {count:,} |')
    lines += ['', '## 월별 봉 신호', '', '| 월 | 조건 충족 시점 |', '|---|---:|']
    for month, count in summary['monthly_signal_counts'].items():
        lines.append(f'| {month} | {count} |')
    lines += ['', '기준 스프레드0.04% 가정의 월별 손익:', '', '| 월 | 완료 거래 | 월 손익률 | 월말 운용자산 |', '|---|---:|---:|---:|']
    import datetime as dt
    kst=dt.timezone(dt.timedelta(hours=9))
    for r in scenarios[0]['monthly']:
        count=sum(dt.datetime.fromtimestamp(t['time'],kst).strftime('%Y-%m')==r['month'] for t in scenarios[0]['trades'])
        lines.append(f"| {r['month']} | {count} | {r['return_pct']:+.4f}% | {r['end_equity']:,.0f}원 |")
    lines += ['', '스프레드를 높이면 더 많은 신호가 비용·추격 필터에서 차단되어 거래 구성 자체가 달라집니다. 불리한 스프레드에서 손실이 작게 나온 것을 더 좋은 수익 모델이나 신뢰구간의 하단으로 해석하면 안 됩니다.']
    lines += ['', '## 해석과 재현의 한계', '',
        '- 시장이 항상 거래 기회를 주지 않은 것만이 원인은 아닙니다. 현재 코드의 엄격한 연속봉 검사와 비용·손절 거리 조합이 거래를 크게 줄였습니다.',
        '- 이 신호 검사에는 현행 evaluate_intraday 원함수를 그대로 사용했습니다. 미래 봉을 사용하지 않고, 1분200개·5분300개·15분200개 창과 거래대금 VWAP을 적용했습니다.',
        '- 5분·15분은 공식1분 자료를 합산했습니다. 부동소수점 합산의 극미한 차이가 있을 수 있습니다. 무거래 분봉이 없는 것은 업비트 자료의 특성이며 수집 누락과 항상 같지는 않습니다.',
        '- 초기 돌파 경로의 최근10초 매수비율·고점5초 유지·체결 지연은 연간 분봉으로 재현할 수 없어 제외했습니다. 이 경로의 실제 신호가0개였다는 뜻이 아닙니다.',
        '- 실제 서버가 당시 선택했을12개 후보, 시장 경보, 상장·폐지 이력, 데이터 도착 지연, 호가 깊이는 재현하지 않았습니다. 선택한10종목이 모두 후보라고 놓은 조건부 계산입니다.',
        '- 대표10개는 현재 상장종목 표본으로 생존·선택 편향이 있습니다. 결과를 전체 원화마켓 포트폴리오로 일반화할 수 없습니다.',
        '- 일일1.5% 신규진입 정지·2%청산·고점대비5%검토 잠금·3회연속손실 휴식·종목별 재진입 대기는 원래 엔진을 유지했습니다.',
        '- 과거 재현 손익은 미래 기대수익이 아닙니다. 체결 가정과 적은 거래 표본 때문에 이 결과만으로 연 수익률을 예측할 수 없습니다.',
        '- 계좌 키·실제 주문·운영 설정은 변경하지 않았습니다. 전체 주문은 오프라인 임시 장부에서 처리했습니다.', '',
        '## 자료·재실행', '',
        '[업비트 공식 Historical Market Data](https://www.upbit.com/historical_data/main) · [최근 체결 REST의 최대7일 범위](https://docs.upbit.com/kr/kr/reference/체결trade)', '',
        '원본 ZIP 390개·체크섬·DB 무결성 검증은 historical/manifest.json 및 quality_report.json, 코드 해시는 protocol.json과 proxy_execution.json에 저장했습니다. 분석 대상 분봉은 한국 연초 기준 약370만 행이며 워밍업 자료를 별도로 포함합니다.', '',
        '```powershell',
        'python ytd_proxy_sim.py --db analysis_runs/ytd_v4/historical/candles_1m.sqlite --signals analysis_runs/ytd_v4/signals/all_signals.jsonl --end 2026-10-30T12:11:00+09:00 --output analysis_runs/ytd_v4/proxy_execution.json',
        'python build_ytd_report.py', '```', '',
        '상세 신호: signals/summary.json · 합본 신호: signals/all_signals.jsonl · 거래·원화장부·월별손익·가정: proxy_execution.json', '',
    ]
    report = '\n'.join(lines)
    (OUT/'결과보고서.md').write_text(report, encoding='utf-8')
    # Plain standalone report for convenient browser reading, preserving Markdown.
    page='<!doctype html><html lang="ko"><meta charset="utf-8"><title>자동거래 v4 연간 검증</title><style>body{max-width:1100px;margin:40px auto;padding:0 24px;background:#f7f8fa;color:#172033;font:16px/1.8 Malgun Gothic,sans-serif}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}</style><body><pre>'+html.escape(report)+'</pre></body></html>'
    (OUT/'결과보고서.html').write_text(page, encoding='utf-8')
    print(json.dumps({'report':str(OUT/'결과보고서.md'),'signals':summary['total_candle_signals'],
                      'returns':[s['return_pct'] for s in scenarios]},ensure_ascii=True))


if __name__=='__main__':
    main()
