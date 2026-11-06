"""Render a frozen Markdown bot specification without importing trading code."""
from pathlib import Path
import argparse
import html
import re


def inline(text):
    value = html.escape(text)
    value = re.sub(r'`([^`]+)`', r'<code>\1</code>', value)
    value = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', value)
    value = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', value)
    return value


def render(markdown):
    out, para, table, code, fenced = [], [], [], [], False
    headings = []

    def flush():
        if para:
            out.append('<p>' + '<br>'.join(inline(x) for x in para) + '</p>')
            para.clear()
        if table:
            rows = [re.split(r'(?<!\\)\|', x.strip().strip('|')) for x in table]
            rows = [r for r in rows if not all(re.fullmatch(r'\s*:?-+:?\s*', c) for c in r)]
            out.append('<div class="table-wrap"><table>')
            for i, row in enumerate(rows):
                tag = 'th' if i == 0 else 'td'
                out.append('<tr>' + ''.join('<' + tag + '>' + inline(c.strip()) + '</' + tag + '>' for c in row) + '</tr>')
            out.append('</table></div>')
            table.clear()

    for line in markdown.splitlines():
        if line.strip().startswith('```'):
            flush()
            if fenced:
                out.append('<pre><code>' + html.escape('\n'.join(code)) + '</code></pre>')
                code.clear()
            fenced = not fenced
            continue
        if fenced:
            code.append(line)
            continue
        if line.startswith('|') and line.rstrip().endswith('|'):
            if para:
                flush()
            table.append(line)
            continue
        if table:
            flush()
        match = re.match(r'^(#{1,6})\s+(.+)', line)
        if match:
            flush()
            level, title = len(match[1]), match[2]
            anchor = 'section-' + str(len(headings) + 1)
            headings.append((level, title, anchor))
            out.append(f'<h{level} id="{anchor}">{inline(title)}</h{level}>')
        elif not line.strip() or re.fullmatch(r'\s*[-*_]{3,}\s*', line):
            flush()
        elif re.match(r'^\s*([-*]|\d+[.)])\s+', line):
            flush()
            out.append('<p class="item">' + inline(line.strip()) + '</p>')
        else:
            para.append(line)
    flush()
    if code:
        out.append('<pre>' + html.escape('\n'.join(code)) + '</pre>')
    nav = ''.join(f'<li><a href="#{a}">{inline(t)}</a></li>' for level, t, a in headings if level <= 2)
    return '<nav><details><summary>목차 펼치기</summary><ul>' + nav + '</ul></details></nav>' + '\n'.join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('markdown', type=Path)
    args = ap.parse_args()
    text = args.markdown.read_text(encoding='utf-8')
    body = render(text)
    css = '''body{margin:0;background:#f2f4f6;color:#18232d;font:16px/1.8 "Malgun Gothic",sans-serif}
main{max-width:1020px;margin:30px auto;background:white;padding:48px 60px;box-sizing:border-box}
h1{font-size:30px;color:#000;line-height:1.45;margin:0 0 24px}h2{font-size:23px;margin:42px 0 15px;line-height:1.5}
h3{font-size:19px;margin-top:28px}h4{font-size:17px}p{margin:12px 0}.item{padding-left:16px}
nav{margin:0 0 32px}a{color:#155e91}code{font:0.9em Consolas,monospace;overflow-wrap:anywhere}
pre{background:#f5f6f8;padding:18px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;line-height:1.65}
table{border-collapse:collapse;width:100%;font-size:14px;line-height:1.65;margin:18px 0}th,td{border:1px solid #d8dfe5;padding:10px 12px;vertical-align:top;overflow-wrap:anywhere}
th{background:#eef2f5;text-align:left}.table-wrap{overflow-x:auto}summary{cursor:pointer}
@media(max-width:800px){main{margin:0;padding:24px 18px}h1{font-size:25px}table{font-size:13px}}
@media print{@page{size:Letter;margin:18mm}body{background:white;font-size:10.5pt}main{max-width:none;margin:0;padding:0}nav{display:none}h1,h2,h3{break-after:avoid}pre{font-size:8pt}table{font-size:8.5pt}tr{break-inside:avoid}a{color:inherit;text-decoration:none}}'''
    result = '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>업비트 자동거래 봇 재현 명세서</title><style>' + css + '</style></head><body><main>' + body + '</main></body></html>'
    target = args.markdown.with_suffix('.html')
    target.write_text(result, encoding='utf-8')
    print(target)


if __name__ == '__main__':
    main()
