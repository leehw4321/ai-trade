"""Export the morning analysis to readable local HTML and CSV, without credentials."""
import csv
import html
import json
import re

from morning_analysis import OUT, dump


def inline(text):
    text = html.escape(text)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    return re.sub(r'\[([^\]]+)\]\((https://[^)]+)\)', r'<a href="\2">\1</a>', text)


def render_markdown(text):
    output, table = [], False
    for line in text.splitlines():
        if line.startswith('|'):
            if re.fullmatch(r'[|\s:\-]+', line):
                continue
            if not table:
                output.append('<div class="table-wrap"><table>')
                table = True
                tag = 'th'
            else:
                tag = 'td'
            output.append('<tr>' + ''.join(f'<{tag}>{inline(c.strip())}</{tag}>' for c in line.strip('|').split('|')) + '</tr>')
            continue
        if table:
            output.append('</table></div>')
            table = False
        if not line:
            continue
        if line.startswith('### '):
            output.append('<h3>' + inline(line[4:]) + '</h3>')
        elif line.startswith('## '):
            output.append('<h2>' + inline(line[3:]) + '</h2>')
        elif line.startswith('# '):
            output.append('<h1>' + inline(line[2:]) + '</h1>')
        else:
            output.append('<p>' + inline(line) + '</p>')
    if table:
        output.append('</table></div>')
    return '\n'.join(output)


def main():
    results = json.loads((OUT/'current_event_replay.json').read_text(encoding='utf-8'))
    fields = ['scenario','market','in','out','entry','exit','gross_pct','net_krw','why',
              'age_minutes','peak_pct','gap_pct','rise_pct','accel','r10_pct','r30_pct','t10_krw']
    with (OUT/'event_trades.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore')
        writer.writeheader()
        for scenario,r in results.items():
            assert r['net_krw']==sum(t['net_krw'] for t in r['rows'])
            for row in r['rows']:
                assert abs(sum(t['fraction'] for t in row['exit_events'])-1)<1e-8
                expected=round(r['slot_krw']*(row['exit']*.9995/(row['entry']*1.0005)-1))
                assert expected==row['net_krw']
                writer.writerow(dict(scenario=scenario,**row))
    outcomes=json.loads((OUT/'market_outcomes.json').read_text(encoding='utf-8'))
    with (OUT/'market_outcomes.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(outcomes[0]))
        writer.writeheader()
        writer.writerows(outcomes)
    method=json.loads((OUT/'methodology.json').read_text(encoding='utf-8'))
    method['not_replayed']=[s for s in method['not_replayed'] if 'Live event' not in s]
    method['event_model']='Separate approximate replay of current event branch, paper-day buying block lifted hypothetically; virtual cash assumed available.'
    method['event_model_limitations']=['Synthetic historical quotes', 'No serial API latency or limit-order queue simulation',
        'Closed 1-second candles add up to 1 second observation delay', 'No new regular 5m/15m entries',
        'No reconstruction of historical cash balance']
    dump(OUT/'methodology.json',method)
    report=next(OUT.glob('morning_report.md')).read_text(encoding='utf-8')
    page='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>미친말 프로젝트 오전 재현 분석</title><style>
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f2f5f9;color:#172438;font:15px/1.8 system-ui,"Malgun Gothic",sans-serif}
main{max-width:1100px;margin:auto;padding:28px 26px 80px}header{background:#132844;color:#fff;padding:20px 26px;border-radius:16px;margin-bottom:28px}
header a{color:#c5e3ff}header b{font-size:23px}h1{font-size:28px;line-height:1.4}h2{font-size:21px;margin-top:38px;border-bottom:2px solid #dbe3ee;padding-bottom:9px}h3{font-size:17px;margin-top:26px}
p{margin:10px 0}strong{color:#102c52}a{color:#225cb3}code{background:#e7edf6;border-radius:4px;padding:2px 5px;font-size:13px;overflow-wrap:anywhere}
.table-wrap{overflow:auto;margin:18px 0;background:white;border:1px solid #dbe3ee;border-radius:10px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:11px 14px;border-bottom:1px solid #e9eef5;text-align:left;min-width:90px}th{background:#e7eef8;font-weight:650}tr:last-child td{border-bottom:0}
.notice{padding:14px 18px;border-left:4px solid #bf8400;background:#fff5db;border-radius:5px;margin:20px 0}
@media(max-width:640px){main{padding:16px 14px 50px}h1{font-size:23px}th,td{padding:9px}}
</style><main><header><b>미친말 · 오전 재현 분석</b><div>2026.10.26 · 기준 08:50 / 성과 구간 09:00–10:00 KST</div><a href="/">← 단타봇 대시보드</a></header>
<div class="notice">실제 체결과 과거 데이터 가상 재현을 구분한 보고서입니다. 과거 호가와 API 지연은 재현하지 못했습니다. 생산용 전략은 변경하지 않았습니다.</div>
'''+render_markdown(report)+'</main></html>'
    (OUT/'morning_report.html').write_text(page,encoding='utf-8')
    assessment_path = OUT/'overall_assessment.md'
    if assessment_path.exists():
        assessment = assessment_path.read_text(encoding='utf-8')
        assessment_page = page.replace(render_markdown(report), render_markdown(assessment))
        assessment_page = assessment_page.replace('<title>미친말 프로젝트 오전 재현 분석</title>', '<title>단타봇 종합 진단</title>')
        assessment_page = assessment_page.replace('미친말 · 오전 재현 분석', '단타봇 · 종합 진단')
        assessment_page = assessment_page.replace('2026.10.26 · 기준 08:50 / 성과 구간 09:00–10:00 KST',
                                                  '2026.10.26 · 어제 손실 / 오늘 모의거래 / 모델·코드 점검')
        (OUT/'overall_assessment.html').write_text(assessment_page,encoding='utf-8')
    assert '<table>' in page and 'PRIVATE' not in page
    print('HTML/CSV exported; all event PnL and partial-exit totals verified')


if __name__=='__main__':
    main()
