from pathlib import Path
from docx import Document
from docx.shared import Cm, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

OUT = Path(__file__).resolve().parents[1] / '가상자산_거래모델_토론정리와_방향선택.docx'
d = Document()
s = d.sections[0]
s.page_width, s.page_height = Cm(21), Cm(29.7)
s.top_margin = s.bottom_margin = Cm(1.9)
s.left_margin = s.right_margin = Cm(2)
for name in ['Normal', 'Title', 'Heading 1', 'Heading 2']:
    st = d.styles[name]
    st.font.name = '맑은 고딕'
    st.font.color.rgb = RGBColor(0,0,0)
    st.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), '맑은 고딕')
d.styles['Normal'].font.size = Pt(10.5)
d.styles['Normal'].paragraph_format.line_spacing = 1.17
d.styles['Normal'].paragraph_format.space_after = Pt(7)
d.styles['Title'].font.size = Pt(23)
d.styles['Heading 1'].font.size = Pt(15)
d.styles['Heading 1'].paragraph_format.space_before = Pt(13)
d.styles['Heading 1'].paragraph_format.space_after = Pt(7)
def p(t): d.add_paragraph(t)
def h(t): d.add_heading(t, 1)
def table(headers, rows, widths):
    t=d.add_table(rows=1, cols=len(headers)); t.autofit=False
    for c,w in zip(t.columns,widths): c.width=Cm(w)
    for c,x in zip(t.rows[0].cells,headers): c.text=x
    for row in rows:
        for c,x in zip(t.add_row().cells,row): c.text=x
    for i,row in enumerate(t.rows):
        trpr=row._tr.get_or_add_trPr()
        e=OxmlElement('w:cantSplit'); trpr.append(e)
        if i==0: trpr.append(OxmlElement('w:tblHeader'))
        for c,w in zip(row.cells,widths):
            c.width=Cm(w)
            tc=c._tc.get_or_add_tcPr()
            borders=OxmlElement('w:tcBorders')
            for edge in ['top','left','bottom','right']:
                e=OxmlElement('w:'+edge); e.set(qn('w:val'),'single'); e.set(qn('w:sz'),'4'); e.set(qn('w:color'),'D9D9D9'); borders.append(e)
            tc.append(borders)
            shade=OxmlElement('w:shd'); shade.set(qn('w:fill'),'E7EDF3' if i==0 else 'FFFFFF'); tc.append(shade)
            for par in c.paragraphs:
                par.paragraph_format.space_before=Pt(5); par.paragraph_format.space_after=Pt(5)
                for run in par.runs: run.font.size=Pt(9.5); run.bold=(i==0)
    d.add_paragraph().paragraph_format.space_after=Pt(0)

d.add_heading('가상자산 거래모델 개발 방향',0)
p('ChatGPT와 Claude 토론 정리 및 선택 가이드\n2026년 11월 2일')
p('권고 방향은 추세추종 B를 기준 모델로 삼고, 4시간봉 상대강도 순환 C를 주력 비교 후보로 검증하는 것입니다. 새 매매 엔진을 만들기 전에 공통 평가 도구로 비용 후 수익과 위험을 확인합니다. 현재까지 채택할 만큼 수익성이 검증된 모델은 없습니다.')
h('1 목표와 현재 상황')
p('목표는 거래비용을 제외하고 수익을 내면서 기회를 지나치게 제한하지 않는 것입니다. 거래 횟수는 목표 할당량이 아니라 수익성 있는 규칙을 적용한 결과로 평가합니다.')
table(['구분','토론에서 확인한 내용','판단'],[
('첫 실거래','공유된 10월 25일 기록은 40건 중 11승 29패, 손익 −68,323원','거래 빈도가 수익으로 연결되지 않음'),
('V3와 V4','신규 자동 매수는 모의 전용이며 여러 조건에서 진입을 제한','기능 제한과 전략 성과를 구분해야 함'),
('기존 후보 연구','16개 후보 모두 선정 기준 미통과','기존 후보 안에서 채택 모델을 찾지 못함'),
('개발 구조','여러 매매 엔진과 부가기능이 확장됨','다음 작업은 전략 평가를 우선')],[2.5,8.8,5.7])
p('첫 실거래의 원인 비율과 일부 차단 횟수는 공유된 진단에 따른 내용이며 추가 원장 대조가 필요합니다. 16개 후보의 성과는 기존 모델 비교 보고서에서 확인했습니다. 기능 테스트 통과는 수익성 입증과 다릅니다.')
h('2 기존 결과를 읽는 방법')
p('1시간 돌파 후보는 2026년 7~10월 별도 평가에서 75건, +3.26%였고, 같은 기간 10종목에 자산의 5%씩 투자한 단순 보유 비교는 +6.14%였습니다. 두 방식의 손실 제한은 달라 수익률만으로 우열을 확정할 수 없습니다.')
p('해당 후보의 연속 운용은 −4.41%였으며 3월 낙폭 잠금 이후 진입이 중단됐습니다. 따라서 신호의 수익성과 계좌 중단 정책의 영향을 따로 측정해야 합니다. 이미 반복해서 본 7~10월은 새 최종 시험 자료로 취급하지 않습니다.')

d.add_page_break()
h('3 비교할 세 가지 전략')
table(['후보','핵심 규칙','보유 기간','확인할 문제'],[
('A 장중 순환','상대강도와 거래대금이 강한 종목의 단기 눌림 진입','수십 분~3시간','거래비용과 급등 추격 위험'),
('B 추세추종','일봉·4시간봉 상승 추세 유지 시 보유하고 약화 시 현금화','수일~수주','횡보장 손절과 진입 지연'),
('C 4시간봉 순환','추세가 유지되는 강한 종목으로 교체','약 1~5일','교체 비용 대비 추가 수익')],[2.7,7,2.5,4.8])
p('보유 기간과 빈도는 설계상 예상입니다. B가 수익을 낸다는 근거가 확정된 것은 아니며 C 역시 절충 가설입니다. A와 C는 가능한 한 상대강도 원리를 공유해 시간대와 교체 주기의 차이를 비교합니다.')
h('4 의견이 모인 부분')
for x in [
'공통 평가 도구 하나에서 원금, 투자 한도, 비용과 체결 가정, 평가 기간을 통일합니다.',
'계좌가 손실 한도로 멈춰도 별도 가상 기록에서 신호를 관찰합니다. 신호 성과와 실제 운용 가능한 계좌 성과는 구분해 보고합니다.',
'규칙과 합격 기준을 평가 전에 고정하고 실패한 설정 및 변경 이력도 남깁니다.',
'BTC 단순 보유, 미리 정의한 분산 보유, 현금을 비교 기준으로 둡니다.',
'시간순 검증과 규칙 고정 이후 실시간 모의 운용으로 확인합니다.'
]: p('• '+x)
h('5 토론 중 바로잡은 내용')
p('부분 익절 자체가 잘못된 것은 아닙니다. 여러 청산 규칙을 합친 결과의 승률, 평균 이익, 평균 손실과 비용을 함께 봐야 합니다. 장기 봉으로 바꾸는 것만으로 수익성이 생기는 것도 아닙니다.')
p('자산의 20%를 매번 투입하고 왕복 비용 0.24%, 하루 5회를 365일 유지하면 연간 누적 비용의 단순 추정은 자산 대비 87.6%입니다. 이는 규모와 빈도가 일정하다는 가정의 비용 부담이며 확정 연간 손실률은 아닙니다.')
p('50~100건의 양수 성과만으로 증액하지 않습니다. 몇 번의 큰 수익이나 특정 상승장 의존 여부도 확인해야 합니다. 후보가 3개여도 내부 설정을 반복 변경하면 과최적화 위험이 커집니다.')

d.add_page_break()
h('6 시작 전에 고정할 검증 조건')
p('데이터 범위  당시 상장 목록, 상장폐지 종목, 과거 거래대금 순위를 어디까지 복원할 수 있는지 먼저 확인합니다. 현재 생존 종목만으로 과거 대상을 구성하면 결과가 유리하게 왜곡될 수 있습니다.')
p('거래비용  과거 실제 호가가 확보되지 않으면 추정 비용이라고 명시합니다. 기본 비용과 비용 증가 조건을 함께 비교하며, 현재 호가를 과거 실제 호가처럼 사용하지 않습니다.')
p('기간 분리  과거로 개발하고 다음 기간에 적용하는 시간순 검증을 중심으로 합니다. 2025년으로 개발하고 2022~2024년으로 시험하는 방식은 보조 검증으로 취급합니다. 과거 구간의 미사용 여부도 확인합니다.')
p('손실 중단  영구 중단과 일정 대기 후 위험을 줄여 재개하는 정책을 사전에 정의합니다. 계좌가 멈춘 뒤의 신호 기록을 계좌 수익으로 합산하지 않습니다.')
p('평가 기준  비용 후 수익, 최대 낙폭, 손익계수, 시장 노출, 단순 보유 대비 위험과 수익, 비용 스트레스 결과를 함께 봅니다. 상승·하락·횡보장별 결과도 공개합니다.')
p('토론에 나온 손익계수 1.2, 낙폭 8%, 모의 30일·200건은 제안값이며 아직 확정 기준이 아닙니다. 저빈도 B와 고빈도 A에 동일 거래 수만 요구하기보다 관찰 기간과 표본 수를 함께 정해야 합니다.')
h('7 사용자 방향 선택 가이드')
table(['우선하는 목표','적합한 방향'],[
('거래가 적어도 수익 가능성부터 확인','B를 먼저 검증하고 단순 보유와 비교'),
('수일 단위로 꾸준한 교체 기회 확보','C를 주력 비교 후보로 두고 B 대비 개선 확인'),
('매일 여러 번 거래하는 방식 연구','A를 후순위 실험으로 유지하고 비용 후 탈락 기준 고정')],[7.3,9.7])
p('추천은 B를 기준 모델, C를 주력 도전 모델로 두는 것입니다. A는 B와 C 결과를 확인한 뒤 연구합니다. 이는 검증 우선순위이며 예상 수익 순위가 아닙니다.')
h('8 다음 작업과 결정 시점')
p('① 데이터 확보 현황 확인 → ② B·C 규칙과 합격 기준을 짧은 명세서로 고정 → ③ 공통 평가 도구에서 비교 → ④ 통과한 후보만 실시간 모의 운용 → ⑤ 결과를 검토한 뒤 소액 실거래 여부 결정')
p('두 AI에는 같은 명세와 평가 도구를 공유합니다. 평가는 설명의 자신감이나 코드 양보다 비용 후 성과, 낙폭, 거래 빈도와 재현 여부로 판단합니다. 어느 후보도 통과하지 못하면 엔진을 확장하기 전에 수익 가설을 바꿉니다.')
p('참고 자료  기존 연구 analysis_runs/model_search/report.md 및 본 대화의 공유 진단과 제안. 업비트 공식 과거 자료 www.upbit.com/historical_data/download 및 호가 문서 docs.upbit.com/kr/reference/websocket-orderbook. 본 문서는 개발 방향 선택을 위한 정리이며 미래 수익을 보장하지 않습니다.')
d.save(OUT)
print(OUT)
