"""One automatically refreshed document for the owner's ongoing debate."""
import datetime as dt
import html
import json
from pathlib import Path
import re

def render(root):
    root=Path(root);text=(root/'토론.md').read_text(encoding='utf-8')
    esc=html.escape
    stamp=dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
    def para(t):return '<p>'+esc(str(t)).replace('\n','<br>')+'</p>'
    content=['<h1>업비트 기존 계좌 인수 운용 토론 현황</h1>',para('갱신 '+stamp+' KST  ·  이 문서는 30초마다 새로 표시됩니다.'),
             '<h2>토론 목적과 진행 범위</h2>',
             para('현재 업비트 계좌의 원화와 기존 보유자산을 출발점으로 유지·축소·정리·교체와 신규 매수 기준을 설계합니다. 기술값은 두 AI가 검토하고 권고안을 작성하며 사용자에게 반복해서 결정을 요구하지 않습니다.'),
             para('이 문서는 토론 진행 상황입니다. 합의와 제안을 구분하며, 실제 매도·매수나 수익성 검증이 수행됐다는 뜻은 아닙니다.')]
    status=(root/'진행상태.txt')
    content+=['<h2>현재 진행</h2>',para(status.read_text(encoding='utf-8') if status.exists() else '초기화 중')]
    wait=root/'사용량대기.json'
    if wait.exists():
        data=json.loads(wait.read_text(encoding='utf-8'))
        due=dt.datetime.fromtimestamp(float(data['재시도_epoch']),dt.timezone(dt.timedelta(hours=9)))
        content+=['<h2>사용량 소진으로 대기 중</h2>',para(f'{data["AI"]} 회차{data["회차"]}의 차례를 보존했습니다. 자동 재시도 예정: {due:%Y-%m-%d %H:%M:%S} KST. 아직 한도 소진이면 다시5시간 대기합니다. 보고서 갱신은 계속됩니다.')]
    source=root/'계좌현황_읽기전용.json'
    if source.exists():
        account=json.loads(source.read_text(encoding='utf-8'));view=account.get('평가현황',{})
        content+=['<h2>읽기 전용 계좌 현황</h2>',para('조회 시각 '+account['조회시각']+' · 현재 자동 갱신 시세가 아닌 시점 자료입니다.'),para(account.get('출처',''))]
        if account.get('조회성공'):
            content+=[para(f'현금 {view.get("cash"):,.0f}원 · 주문 잠금 원화 {view.get("locked"):,.0f}원 · 가격이 확인된 자산의 평가 합계 {view.get("total"):,.0f}원 · 조회 당시 지연 표시 {view.get("stale")}'),
                      para('가격이 없는 자산은 가치가 0이라는 뜻이 아닙니다. 잔고 및 미체결 주문의 대사 전에는 종목별 매도·매수 결론이 확정되지 않았습니다.'),
                      '<table><thead><tr><th>자산</th><th>수량</th><th>평가금액 원</th><th>잠금 수량</th><th>확인 사항</th></tr></thead><tbody>']
            for item in view.get('holdings',[]):
                note=('원화 거래 가능' if item.get('tradable') else '원화 거래 가능 여부 확인 필요')+(' · 평가가격 없음' if item.get('price') is None else '')
                content+=['<tr>'+''.join('<td>'+esc(str(v))+'</td>' for v in [item.get('currency'),item.get('qty'),item.get('value'),item.get('locked'),note])+'</tr>']
            content+=['</tbody></table>']
        else: content+=[para('실제 계좌 현황 미확인: '+account.get('오류','연결되지 않음')+' · 모의 장부를 실제 잔고로 대체하지 않습니다.')]
    items=[]
    for m in re.finditer(r'^\| (\d+) \| ([^|]+) \| ([^|]+) \| ([^\n]+) \|\s*$',text,re.M):items.append((m[1],m[2].strip(),m[3].strip(),m[4].strip()))
    for title,selected in [('두 AI가 합의로 기록한 사항',[i for i in items if i[2]=='합의']),('현재 논쟁과 미확정 사항',[i for i in items if i[2]!='합의'])]:
        content+=['<h2>'+title+'</h2>',para('과거 평가 명세의 합의도 포함됩니다. 새 계좌 인수 목적에 맞는 수정 여부는 이후 발언에서 확인합니다.')]
        for n,name,status,body in selected:content+=['<h3>안건 '+n+' · '+esc(name)+' · '+esc(status)+'</h3>',para(body)]
    speeches=list(re.finditer(r'^### \[회차 (\d+)\] ([^\n]+)\n(.*?)(?=\n---\n###|\Z)',text,re.M|re.S))
    content+=['<h2>최근 두 AI의 발언과 제안</h2>']
    for m in speeches[-4:]:content+=['<h3>회차 '+m[1]+' '+esc(m[2])+'</h3>',para(m[3][:7000])]
    share=root/'보고서_공유현황.md'
    content+=['<h2>보고서 공유 확인</h2>',para('\n'.join(share.read_text(encoding='utf-8').splitlines()[-8:]) if share.exists() else '공유 대기'),
              '<p><a href="최신_합의와이견_보고서.html">시간별 보고서</a> · <a href="토론.md">전체 토론 원문</a></p>']
    page='<!doctype html><html lang="ko"><meta charset="utf-8"><meta http-equiv="refresh" content="30"><title>업비트 계좌 인수 토론 현황</title><style>body{font:16px/1.8 "Malgun Gothic",sans-serif;color:#172333;max-width:1050px;margin:35px auto;padding:0 24px}h1{font-size:27px}h2{margin-top:38px}h3{font-size:18px;margin-bottom:8px}p{overflow-wrap:anywhere}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:10px;border:1px solid #d9d9d9;text-align:left}th{background:#eaf0f5}@media print{body{margin:0}h2,h3{break-after:avoid}}</style><main>'+''.join(content)+'</main></html>'
    target=root/'계좌인수_토론현황.html';temp=target.with_suffix('.tmp');temp.write_text(page,encoding='utf-8');temp.replace(target)
    return target
