import json
from pathlib import Path
import re
from docx import Document

root=Path(__file__).resolve().parent/'full_debate'
manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
doc=Document(manifest['output'])
content='\n'.join(p.text for p in doc.paragraphs)
compact=re.sub(r'\s+','',content)
source=(root/'토론_고정원문.md').read_text(encoding='utf-8')
tail=source[source.index('### [회차 0]'):]
missing=[];checked=0
for line in tail.splitlines():
    line=line.strip()
    if not line or line=='---' or line.startswith('```') or line.startswith('### [회차') or line.startswith('### [사용자]'):continue
    line=re.sub(r'^#{1,6}\s+','',line)
    line=re.sub(r'\*\*|`','',line)
    line=re.sub(r'^[-*]\s+','• ',line)
    checked+=1
    if re.sub(r'\s+','',line) not in compact:missing.append(line[:150])
assert not missing,missing[:20]
headings=[p.text for p in doc.paragraphs if p.text.startswith('회차 ')]
for n in manifest['rounds']:
    assert any(value.startswith(f'회차 {n} ') for value in headings),n
assert '42' in str(manifest['rounds'])
print(json.dumps({'transcript_lines_verified':checked,'missing_lines':len(missing),'rounds':len(manifest['rounds']),
                  'docx_bytes':Path(manifest['output']).stat().st_size,'paragraphs':len(doc.paragraphs)},ensure_ascii=False))
