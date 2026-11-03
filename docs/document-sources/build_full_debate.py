from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
from docx import Document
from docx.shared import Cm,Pt,RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT=Path('C:/Users/User/Desktop/AI토론장')
WORK=Path(__file__).resolve().parent/'full_debate';WORK.mkdir(exist_ok=True)
text=(ROOT/'토론.md').read_text(encoding='utf-8-sig')
stamp=dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
(WORK/'토론_고정원문.md').write_text(text,encoding='utf-8')
items=[{'id':m[1],'name':m[2].strip(),'status':m[3].strip(),'content':m[4].strip()} for m in re.finditer(r'^\| (\d+) \| ([^|]+) \| ([^|]+) \| ([^\n]+) \|\s*$',text,re.M)]
speeches=list(re.finditer(r'^### \[회차 (\d+)\] ([^\n]+)\n(.*?)(?=\n---\n###|\Z)',text,re.M|re.S))
ids=[int(m[1]) for m in speeches]
assert ids==list(range(max(ids)+1)),ids
counts=Counter(i['status'] for i in items)
doc=Document();sec=doc.sections[0]
sec.page_width,sec.page_height=Cm(21),Cm(29.7)
sec.top_margin=sec.bottom_margin=Cm(1.9);sec.left_margin=sec.right_margin=Cm(2.1)
sec.header_distance=sec.footer_distance=Cm(.8)
for name in ['Normal','Title','Subtitle','Heading 1','Heading 2','Heading 3']:
    st=doc.styles[name];st.font.name='맑은 고딕';st.font.color.rgb=RGBColor(0,0,0)
    st.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'맑은 고딕')
doc.styles['Normal'].font.size=Pt(10)
doc.styles['Normal'].paragraph_format.line_spacing=1.12
doc.styles['Normal'].paragraph_format.space_after=Pt(5)
doc.styles['Title'].font.size=Pt(25)
for name,size in [('Heading 1',17),('Heading 2',13),('Heading 3',11)]:
    st=doc.styles[name];st.font.size=Pt(size);st.paragraph_format.space_before=Pt(12);st.paragraph_format.space_after=Pt(6)
header=sec.header.paragraphs[0];header.text='업비트 계좌 운용 토론과 합의 기록';header.runs[0].font.size=Pt(8)
footer=sec.footer.paragraphs[0];footer.alignment=WD_ALIGN_PARAGRAPH.RIGHT
for instruction in ['PAGE','NUMPAGES']:
    if instruction=='NUMPAGES':footer.add_run(' / ')
    field=OxmlElement('w:fldSimple');field.set(qn('w:instr'),instruction);footer._p.append(field)

def rich(par,value):
    for token in re.split(r'(\*\*.*?\*\*|`[^`]+`)',value):
        if token.startswith('**') and token.endswith('**'):par.add_run(token[2:-2]).bold=True
        elif token.startswith('`') and token.endswith('`'):par.add_run(token[1:-1])
        else:par.add_run(token)
    return par
def p(value):return rich(doc.add_paragraph(),value)
def heading(value,level=1):doc.add_heading(value,level)
def lines_md(value):
    code=False
    for line in value.splitlines():
        if line.strip().startswith('```'):code=not code;continue
        if not line.strip():continue
        if line.strip()=='---':continue
        level=re.match(r'^(#{1,6})\s+(.*)',line)
        if level:
            # Original labels remain literal; document hierarchy stays shallow.
            par=doc.add_paragraph();par.paragraph_format.keep_with_next=True
            run=par.add_run(re.sub(r'\*\*|`','',level[2]));run.bold=True
            continue
        par=doc.add_paragraph()
        if re.match(r'^\s*[-*]\s',line):
            par.paragraph_format.left_indent=Cm(.35);par.paragraph_format.first_line_indent=Cm(-.25)
            line='• '+re.sub(r'^\s*[-*]\s+','',line)
        if code:
            par.paragraph_format.left_indent=Cm(.35)
            run=par.add_run(line);run.font.name='Consolas';run.font.size=Pt(9)
        else:rich(par,line)

doc.add_heading('업비트 계좌 운용 토론과 합의 기록',0)
p('전체 토론 원문과 합의 현황')
p('기록 기준  '+stamp.strftime('%Y년 %m월 %d일 %H시 %M분 KST'))
p(f'완료 발언 0회차부터 {max(ids)}회차까지 총 {len(speeches)}건. 이후 발언은 이 문서에 포함되지 않습니다. 자동 토론은 별도로 계속됩니다.')
p('이 문서는 두 AI의 논의 결과를 확인하기 위한 기록입니다. 합의는 해당 명세에 대한 의견 일치이며, 구현 완료·독립 시험 통과·수익성 입증·실거래 실행을 뜻하지 않습니다.')
heading('문서 구성')
p('1 현재 목표와 핵심 정리\n2 기록상 합의된 사항\n3 제안과 남은 이견\n4 전체 토론 원문\n5 최신 사용자 지시와 개발 요청 원문\n6 출처와 기록 범위')
heading('1 현재 목표와 핵심 정리')
p('사용자의 목표는 현재 업비트 계좌의 원화와 기존 보유자산을 출발점으로, 필요한 매도와 신규 편입을 포함해 수익을 얻을 운용 구조를 만드는 것입니다. 사용자는 기술적 선택을 반복해서 요구받기보다 두 AI가 구체적인 권고안과 봇 개선안을 제시하기를 원합니다.')
p('개발 흐름은 매도 대상·수량 판단, 주문 상태와 부분체결 확인, 실제 확보 원화 대사, 매수 종목·금액 재산정입니다. 매도 예정액을 현금으로 가정하거나 기존 자산을 가상 현금으로 바꾸어 계산하는 문제를 구분해 검토했습니다.')
p('비교 대상은 장중 상대강도 순환 A, 추세추종 B, 4시간봉 상대강도 순환 C와 단순 보유·현금입니다. 이후 짧은 주기 요청으로 설정과 연구 경로가 나뉘었습니다. 초기 연구 명세와 현재 프로그램 설정을 같은 검증 결과로 해석하지 않아야 합니다.')
p('합의 표에는 과거의 최대6회차, 4시간봉, 1시간봉, 가상 원금100만 원 조건도 남아 있습니다. 회차 제한 폐지 등 최신 사용자 지시가 우선합니다. 원문 보존을 위해 아래 합의 항목은 임의로 삭제·정정하지 않고 기록 시점의 표현을 담았습니다.')
p('현재 설정 파일 기록: '+Path('D:/자동거래 v5/config.json').read_text(encoding='utf-8').replace('\n',' '))
p('설정 파일의 값은 구현 상태의 일부이며 실거래 실행이나 전략 검증 완료를 증명하지 않습니다. 특히 기록상 연구 기본 경로와 15분봉 미검증 변형은 분리해 읽어야 합니다.')
p('합의 현황 분류: '+', '.join(f'{k} {v}건' for k,v in counts.items())+'. ‘사용자 결정’은 당시 표의 상태명이며 이후 지시에 따라 두 AI가 계속 검토하는 항목도 포함합니다.')
heading('읽을 때 구분할 사항',2)
for value in [
'합의 항목은 해당 판본에 대한 의견 일치입니다. 제안·이견·미확인은 합의로 승격하지 않았습니다.',
'계좌·가격·미체결 기록은 발언 당시의 관측입니다. 현재 잔고나 주문 상태를 대신하지 않습니다.',
'초기 스냅샷의 ONG 잠금과 이후 잠금 해제 기록은 서로 다른 시점의 기록입니다.',
'미평가 자산은 잔고와 평가 공백을 유지하며 가치0으로 확정하지 않습니다.',
'기능 테스트 통과와 비용 후 수익성, 두 AI 합의와 실제 주문 실행은 서로 구분합니다.'
]:p('• '+value)

doc.add_page_break();heading('2 기록상 합의된 사항')
p('토론장 합의 현황에서 상태가 합의인 항목을 모두 수록했습니다. 긴 설명을 표에 압축하지 않고 안건별로 구분했습니다.')
for item in items:
    if item['status']!='합의':continue
    heading('안건 '+item['id']+' '+item['name'],2)
    p(item['content'])
doc.add_page_break();heading('3 제안과 남은 이견')
for item in items:
    if item['status']=='합의':continue
    heading('안건 '+item['id']+' '+item['status'],2);p(item['content'])

doc.add_page_break();heading('4 전체 토론 원문')
p('완료된 발언을 회차순으로 수록했습니다. 강조·목록·코드 표시는 Word 형식으로 옮겼으며 발언 내용은 요약하지 않았습니다. 인용한 과거 계획과 이후 철회·수정 발언을 함께 읽어야 합니다.')
# Preserve every block after the first speech, including intervening user instructions and summaries.
tail=text[text.index('### [회차 0]'):]
blocks=re.split(r'(?=^### \[(?:회차 \d+|사용자)\])',tail,flags=re.M)
for block in blocks:
    if not block.strip():continue
    title,_,body=block.partition('\n')
    if title.startswith('### [회차 '):
        label=re.sub(r'^### \[회차 (\d+)\]\s*',r'회차 \1 ',title)
        heading(label,2)
    elif title.startswith('### [사용자]'):heading('사용자 발언 '+title[len('### [사용자]'):].strip(),2)
    else:lines_md(title)
    lines_md(body)

doc.add_page_break();heading('5 최신 사용자 지시와 개발 요청 원문')
for label,path in [
('운용 목표',ROOT/'운용목표.md'),('자동 토론 규칙',ROOT/'토론규칙.md'),
('매도 후 매수 개선 요청',Path('D:/자동거래 v5/개선요청_사용자의견.md')),
('V5 개발과 독립 검토 기록',Path('D:/자동거래 v5/검토요청_ChatGPT.md')),
('Claude 작업 조율',ROOT/'작업조율_Claude.md'),('ChatGPT 작업 조율',ROOT/'작업조율_ChatGPT.md')]:
    if path.exists():
        heading(label,2);lines_md(path.read_text(encoding='utf-8-sig'))
heading('6 출처와 기록 범위')
p('주 토론 원문: '+str(ROOT/'토론.md'))
p('원문 SHA256: '+hashlib.sha256(text.encode('utf-8')).hexdigest())
p('완료 회차: '+str(min(ids))+'~'+str(max(ids))+' / 발언 수 '+str(len(speeches)))
p('합의 현황 항목 수: '+str(len(items))+' / 분류 '+json.dumps(counts,ensure_ascii=False))
p('부록에 명시한 운영 목표·규칙·작업 조율·개발 검토 요청을 함께 수록했습니다. 채팅 서비스의 전체 대화 내보내기 파일이나 도구 실행 로그를 수록한 문서는 아닙니다. 현재 진행 중인 응답은 완료 발언으로 포함하지 않았습니다.')

out=Path('C:/Users/User/Desktop/AI토론장/전체토론과_합의내용_'+stamp.strftime('%Y%m%d_%H%M')+'.docx')
doc.save(out)
manifest={'output':str(out),'cutoff':stamp.isoformat(),'rounds':ids,'counts':dict(counts),'items':len(items),'paragraphs':len(doc.paragraphs),'source_hash':hashlib.sha256(text.encode()).hexdigest()}
(WORK/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(manifest,ensure_ascii=False))
