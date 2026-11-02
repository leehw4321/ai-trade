"""마크다운 문서를 한글(HWPX) 문서로 만든다. 한컴오피스 2014 이상에서 열린다(한글에서 '다른 이름으로 저장'으로 .hwp 변환 가능).

  python tools/make_hwpx.py --skeleton <뼈대 .hwpx> --out <결과 .hwpx> <입력1.md> [입력2.md ...]

뼈대: 한글에서 저장한 아무 .hwpx 한 개(글꼴·스타일·쪽 설정을 그대로 가져온다). 뼈대의 본문·미리보기 그림은 쓰지 않는다.
표는 한글 표 개체가 아니라 '칸 │ 칸' 형태의 글로 바꾼다. 굵은 글씨·링크 서식은 제거한다.
"""
import argparse
import datetime as dt
import re
import sys
import zipfile
from xml.sax.saxutils import escape

H1, H2 = 8, 8      # 제목용 글자 모양(굵게). 뼈대의 charPr id
BODY = 6           # 본문용 글자 모양(11pt)
TITLE = 8


def inline(text):
    text = re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)', r'\1 (\2)', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'`([^`]*)`', r'\1', text)
    return text.replace('&nbsp;', ' ')


def blocks(md):
    """마크다운 → [(종류, 글)]. 종류: title, h1, h2, body, blank"""
    out, first = [], True
    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            out.append(('blank', ''))
            continue
        if re.fullmatch(r'\s*-{3,}\s*', line) or re.fullmatch(r'\s*\|[\s:|-]+\|\s*', line):
            continue
        if line.startswith('# '):
            out.append(('title' if first else 'h1', inline(line[2:].strip()))); first = False
        elif line.startswith('## '):
            out.append(('h1', inline(line[3:].strip())))
        elif line.startswith('### '):
            out.append(('h2', inline(line[4:].strip())))
        elif line.lstrip().startswith('|'):
            cells = [inline(c.strip()) for c in line.strip().strip('|').split('|')]
            out.append(('body', '  │  '.join(c for c in cells)))
        else:
            m = re.match(r'^(\s*)([-*])\s+(.*)$', line)
            if m:
                out.append(('body', ' ' * min(len(m.group(1)), 6) + '• ' + inline(m.group(3))))
            else:
                out.append(('body', inline(re.sub(r'^>\s?', '', line))))
    # 연속 빈 줄 줄이기
    cleaned = []
    for b in out:
        if b[0] == 'blank' and cleaned and cleaned[-1][0] == 'blank':
            continue
        cleaned.append(b)
    return cleaned


def left_para_id(header):
    """왼쪽 맞춤·줄 간격 160%인 문단 모양 id (없으면 0)."""
    for m in re.finditer(r'<hh:paraPr id="(\d+)"[^>]*>(.*?)</hh:paraPr>', header, flags=re.S):
        body = m.group(2)
        if 'horizontal="LEFT"' in body and 'value="160"' in body and 'intent="0"' in body.replace(' ', ''):
            return int(m.group(1))
    return 0


def build_section(skeleton_sec, items, para_left):
    paras = re.findall(r'<hp:p .*?</hp:p>', skeleton_sec, flags=re.S)
    head = skeleton_sec[:skeleton_sec.index('<hp:p ')]
    first = paras[0]
    sec_run = first[first.index('<hp:run'):first.index('</hp:run>') + len('</hp:run>')]  # 쪽 설정(secPr)을 담은 첫 run
    tail = skeleton_sec[skeleton_sec.rindex('</hp:p>') + len('</hp:p>'):]
    heights = {TITLE: 1500, H1: 1500, H2: 1500, BODY: 1100}
    xml, vpos, n = [], 0, 0
    for kind, text in items:
        cp = {'title': TITLE, 'h1': H1, 'h2': H2}.get(kind, BODY)
        h = heights[cp]
        seg = (f'<hp:linesegarray><hp:lineseg textpos="0" vertpos="{vpos}" vertsize="{h}" textheight="{h}" '
               f'baseline="{int(h * 0.85)}" spacing="{int(h * 0.6)}" horzpos="0" horzsize="42520" flags="393216"/></hp:linesegarray>')
        run = f'<hp:run charPrIDRef="{cp}"><hp:t>{escape(text)}</hp:t></hp:run>' if text else f'<hp:run charPrIDRef="{cp}"/>'
        prefix = sec_run if n == 0 else ''
        pid = 3121190098 if n == 0 else 2147483648
        xml.append(f'<hp:p id="{pid}" paraPrIDRef="{para_left}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">{prefix}{run}{seg}</hp:p>')
        vpos += int(h * 1.6) + (500 if kind in ('h1', 'h2', 'title') else 0)
        n += 1
    return head + ''.join(xml) + tail


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--skeleton', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--title', default='')
    ap.add_argument('inputs', nargs='+')
    a = ap.parse_args(argv)
    items = []
    for i, path in enumerate(a.inputs):
        text = open(path, encoding='utf-8').read()
        b = blocks(text)
        if i > 0:
            b = [('blank', ''), ('blank', '')] + [(('h1' if k == 'title' else k), t) for k, t in b]
        items += b
    zin = zipfile.ZipFile(a.skeleton)
    header = zin.read('Contents/header.xml').decode('utf-8')
    section = build_section(zin.read('Contents/section0.xml').decode('utf-8'), items, left_para_id(header))
    title = a.title or next((t for k, t in items if k == 'title'), '문서')
    now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    hpf = zin.read('Contents/content.hpf').decode('utf-8')
    hpf = re.sub(r'(<opf:title[^>]*>)[^<]*(</opf:title>)', lambda m: m.group(1) + escape(title) + m.group(2), hpf)
    hpf = re.sub(r'(<opf:meta name="creator" content="text">)[^<]*', r'\1Claude', hpf)
    hpf = re.sub(r'(<opf:meta name="lastsaveby" content="text">)[^<]*', r'\1Claude', hpf)
    hpf = re.sub(r'(<opf:meta name="CreatedDate" content="text">)[^<]*', r'\g<1>' + now, hpf)
    hpf = re.sub(r'(<opf:meta name="ModifiedDate" content="text">)[^<]*', r'\g<1>' + now, hpf)
    hpf = re.sub(r'(<opf:meta name="date" content="text">)[^<]*', r'\g<1>' + dt.datetime.now().strftime('%Y년 %m월 %d일'), hpf)
    settings = re.sub(r'<ha:CaretPosition [^>]*/>', '<ha:CaretPosition listIDRef="0" paraIDRef="0" pos="0"/>', zin.read('settings.xml').decode('utf-8'))
    preview = '\n'.join(t for k, t in items if t)[:1500]
    with zipfile.ZipFile(a.out, 'w') as zout:
        zout.writestr(zipfile.ZipInfo('mimetype'), zin.read('mimetype'), compress_type=zipfile.ZIP_STORED)
        for name, data, ctype in (
            ('version.xml', zin.read('version.xml'), zipfile.ZIP_DEFLATED),
            ('Contents/header.xml', header.encode('utf-8'), zipfile.ZIP_DEFLATED),
            ('Contents/section0.xml', section.encode('utf-8'), zipfile.ZIP_DEFLATED),
            ('Preview/PrvText.txt', preview.encode('utf-8'), zipfile.ZIP_DEFLATED),
            ('settings.xml', settings.encode('utf-8'), zipfile.ZIP_DEFLATED),
            ('META-INF/container.rdf', zin.read('META-INF/container.rdf'), zipfile.ZIP_DEFLATED),
            ('Contents/content.hpf', hpf.encode('utf-8'), zipfile.ZIP_DEFLATED),
            ('META-INF/container.xml', zin.read('META-INF/container.xml'), zipfile.ZIP_DEFLATED),
            ('META-INF/manifest.xml', zin.read('META-INF/manifest.xml'), zipfile.ZIP_DEFLATED),
        ):
            zout.writestr(name, data, compress_type=ctype)
    print(f'작성: {a.out} · 문단 {len(items)}개')
    return 0


if __name__ == '__main__':
    sys.exit(main())
