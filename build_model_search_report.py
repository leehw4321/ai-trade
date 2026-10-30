"""Readable report for the fixed-catalog, split-period candidate experiment."""
import datetime as dt
import html
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'analysis_runs'/'model_search'


def main():
    r=json.loads((OUT/'results.json').read_text(encoding='utf-8'))
    selected=next(x for x in r['train_validation'] if x['config']['id']==r['selected_config']['id'])
    full=r['selected_full_year_replay']['base']['summary']
    test=r['held_out']['base']['summary'];stress=r['held_out']['stress']['summary']
    kst=dt.timezone(dt.timedelta(hours=9))
    halted=dt.datetime.fromtimestamp(full['peak_halt_t'],kst).isoformat() if full['peak_halt_t'] else '없음'
    lines=[
        '# 거래 횟수·수익률 개선 후보 16개 검증 결과', '',
        '**이번 후보군에서는 승격 기준을 통과한 수익 모델을 찾지 못했습니다. 기존 자동거래 v4를 새 모델로 교체하거나 자동 주문을 시작하지 않았습니다.**', '',
        '2026-01-01~2026-10-30 한국시간, 대표10종목의 공식 업비트 분봉을 사용했습니다. BTC·ETH·XRP·SOL·DOGE·SUI·ADA·HBAR·LINK·AVAX 표본이며 전체299종목 결과가 아닙니다.', '',
        '## 핵심 결과', '',
        f"검증 구간에서 상대적으로 손실이 작았던 후보: **{r['selected_name']}**. 양수 수익률과 비용 스트레스 기준을 충족하지 못한 실험용 후보입니다.", '',
        '| 구간 | 기준 비용 수익률 | 거래 수 | 최대 낙폭 |', '|---|---:|---:|---:|',
    ]
    for label,s in [('기초 검사 1~4월',selected['train']['base']),('모델 선택 5~6월',selected['validation']['base']),('선택 미사용 평가 7~10월',test),('올해 전체 연속 운용',full)]:
        lines.append(f"| {label} | {s['net_return_pct']:+.2f}% | {s['trade_count']} | {s['max_drawdown_pct']:.2f}% |")
    lines += ['', f"7~10월의 체결 가격 비용을 2배로 늘린 경우 **{stress['net_return_pct']:+.2f}%**, **{stress['trade_count']}건**입니다. 하지만 올해 전체를 연속 운용하면 **{full['net_return_pct']:+.2f}%**, 원금100만 원 기준 **{full['net_profit']:+,.0f}원**입니다. **{halted}**에 고점 대비5% 검토 잠금이 걸려 이후 신규 진입이 중지됐습니다. 구간별 계산은 각각 원금100만 원·무보유로 시작하며 구간말 비용을 내고 청산하므로, 구간별 수익률을 이어 붙여 전체 연속 성과로 해석하면 안 됩니다.", '',
        f"같은 7~10월에 10종목을 각각 전체자산5%씩 보유하고 나머지50%를 현금으로 둔 단순 비교는 **{r['buy_hold_test']['net_return_pct']:+.2f}%**였습니다. 따라서 선택 후보의 일부 구간 양수 성과만으로 매매 능력이 더 낫다고 주장할 수 없습니다. 단순 보유 비교에는 매매 모델의 손실 제한이 적용되지 않아 위험 구조도 다릅니다.", '',
        '## 비교한 후보 전부', '',
        '아래16개 후보와 수치는 결과에 따라 삭제하거나 숨기지 않았습니다. 고정 후보를 정한 후 검증을 시작했으며, 마지막 평가 구간으로 후보를 다시 고르지 않았습니다.', '',
        '| 모델 | 1~4월 수익률 | 5~6월 수익률 | 5~6월 비용 스트레스 | 5~6월 거래 수 |', '|---|---:|---:|---:|---:|',
    ]
    for x in r['train_validation']:
        a=x['train']['base'];v=x['validation']['base'];s=x['validation']['stress']
        lines.append(f"| {x['name']} | {a['net_return_pct']:+.2f}% | {v['net_return_pct']:+.2f}% | {s['net_return_pct']:+.2f}% | {v['trade_count']} |")
    lines += ['', '## 모델·집행 구현', '',
        '- 추세 돌파: 직전20/24/48봉 고점을 넘고 EMA 방향·거래량 조건을 만족하면 다음5분봉 시가에 진입합니다.',
        '- 추세 눌림 회복: 상승 방향에서 EMA20/EMA50을 회복하는 조건을 사용합니다.',
        '- 평균회귀: 볼린저 하단 회복 또는 RSI 과매도 회복 조건입니다.',
        '- 시간대는5분·15분·1시간이며, 각 후보에 ATR 손절·고정 손익비 목표·최대 보유시간을 미리 정했습니다. 상세수치는 candidates.json에 보존했습니다.',
        '- 미래 봉·현재 봉 종가로 진입하지 않고 다음5분봉 시가로 진입합니다. 같은 봉에서 손절·익절이 모두 닿으면 손절을 먼저 처리하고, 갭 손절은 불리한 실제 시가를 사용합니다.',
        '- 최소200개 연속 해당시간대 봉이 필요합니다.5분 자료는 공식1분을 합산하며 빈5분봉을 가상으로 만들지 않습니다.15분·1시간은 각 구성5분봉이 모두 있는 경우만 완성합니다.', '',
        '## 고정 자금·비용·모델 선정 기준', '',
        '- 원금100만 원, 거래당 계획 위험0.25%, 합산1%, 종목당25%, 총투자50%, 최대4종목, 최소주문5천 원입니다. 거래당 위험을 높여 수익을 부풀리지 않았습니다.',
        '- 기본 편도 수수료0.05%+불리한 가격충격0.05%+반스프레드0.02%=약0.12%입니다. 스트레스는 수수료를 유지하고 가격충격·반스프레드를2배로 늘립니다.',
        '- 일간1.5% 신규진입 정지·2% 청산 예약, 고점 대비5% 이후 해당 연구 구간에서 신규진입 중지입니다. 실제 손실은 갭 때문에 한도를 넘을 수 있습니다.',
        '- 기초 검사에서 최소10건, 검증에서 최소5건·기본/비용스트레스 모두 양수·낙폭5%이하가 필요합니다. 통과 후보가 없으면 검증 손실이 가장 작은 후보만 실험용으로 보존합니다.',
        '- 최종 평가는 위 선택을 파일로 고정한 뒤 딱 한 후보에만 수행했습니다. 마지막 평가에서 양수여도 앞선 선정 기준 실패를 지우지 않았습니다.', '',
        '## 해석', '',
        '거래 횟수를 늘리는 것은 가능했지만, 짧은 시간대 후보들은 비용을 부담할 만큼의 이익을 만들지 못했습니다. 좋은 과거 수익률을 만들기 위해 손실 잠금을 해제하거나, 최종 평가를 보고 파라미터를 바꾸지 않았습니다. 이번 실험은 전 세계 모든 모델의 수익 극대화를 찾았다는 뜻이 아니며, 후보16개 내 검증 결과입니다. 미래 기대수익률은 산출하지 않았습니다.', '',
        '기존 v4 -1.09% 결과와는 체결 방식·최대종목수·합산위험·청산 방식이 달라 정확히 같은 조건의 우열 비교가 아닙니다. 단기 신호를 완화하면 거래는 늘지만 손익도 악화될 수 있다는 실험 근거로 보아야 합니다.', '',
        '## 구현 파일·재실행', '',
        '후보: model_candidates.py · 공통 원화장부: research_portfolio.py · 전체 실험: ../../run_model_search.py · 설정고정: frozen_selection_before_test.json · 상세결과: results.json · 화면 요약: selection.json · 실행 당시 코드: frozen_sources/', '',
        '```powershell', 'python run_model_search.py', 'python build_model_search_report.py', '```', '',
        '새 연구 패널은 결과 조회만 합니다. 기존 자동 신규 매수 버튼이나 실계좌 권한과 연결하지 않았습니다. 격리 모의실험 CLI는 승인되지 않은 모델에 --experimental을 명시해야 하며, 계좌 키·실제 주문을 사용하지 않습니다.', '',
        '```powershell',
        '# 공개 시세로 현재 신호만 확인: 장부 생성·주문 없음',
        'python paper_selected_model.py --once',
        '# 미검증 후보를 별도 가상 원금으로 관찰할 때만 직접 실행',
        'python paper_selected_model.py --watch --experimental',
        '```', '',
        '관찰 장부는 trend_data/research_model_paper.json, 공개 봉 저장소는 research_model_paper_bars.sqlite입니다. 기존 모의·실제 장부와 별개입니다. 시작 이후의 완료5분봉만 재현하며 과거 수익을 장부에 소급하지 않습니다. 봉이 마감된 뒤 체결을 재현하는 지연 모의 방식으로 실제 체결 보장이 아닙니다. 미청산 보유분은 다음 조회로 유지합니다. 이번 작업에서 관찰 프로그램을 자동 시작하지 않았습니다.', '',
        '## 한계·검증', '',
        '과거 호가·대기열·체결 지연을 재현하지 못한 OHLC 시가체결 가정입니다. 현재 상장된10개 표본은 생존·선택 편향이 있고, 과거 전체 상장목록·시장 경보·세금은 반영하지 못했습니다. 봉 마감 기준 계좌낙폭은 봉 안의 손실을 놓칠 수 있습니다. 구간말 청산도 연구상 가정입니다. 모든 체결 수량·수수료·원화 잔고는 독립 계산해 검사했습니다.', '',
        '[업비트 공식 과거 데이터](https://www.upbit.com/historical_data/main) · [Freqtrade 미래정보 참조 검증](https://www.freqtrade.io/en/stable/lookahead-analysis/) · [Freqtrade 지표 재귀 검증](https://www.freqtrade.io/en/stable/recursive-analysis/)', '',
    ]
    report='\n'.join(lines)
    (OUT/'report.md').write_text(report,encoding='utf-8')
    (OUT/'report.html').write_text('<!doctype html><html lang="ko"><meta charset="utf-8"><title>자동거래 모델 비교</title><style>body{max-width:1100px;margin:36px auto;padding:0 20px;color:#182535;background:#f7f9fc;font:16px/1.8 Malgun Gothic,sans-serif}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}</style><pre>'+html.escape(report)+'</pre></html>',encoding='utf-8')
    print(json.dumps({'report':str(OUT/'report.md'),'qualified':r['qualified_validation'],'selected':r['selected_config']['id'],'test_return':test['net_return_pct'],'full_return':full['net_return_pct']},ensure_ascii=True))


if __name__=='__main__':main()
