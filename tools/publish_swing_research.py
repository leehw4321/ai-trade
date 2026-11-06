"""docs/swing-research/state.json 을 스윙 리서치창(자기완결 HTML)으로 만들어 실행 폴더의 data/swing_research.html 에 쓴다.

봇 코드·설정과 무관하게 이 파일만 다시 만들면 갱신된다. 봇 화면 맨 위 '스윙 리서치창' 링크로 연다.
사용: python tools/publish_swing_research.py [내보낼 HTML 경로]
"""
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'docs' / 'swing-research' / 'state.json'
DEFAULT_OUT = ROOT.parent / '코인 트레이딩' / 'v5-app' / 'v5' / 'data' / 'swing_research.html'

CSS = """:root{--bg:#f5f7fa;--card:#fff;--tx:#17212b;--mu:#64707d;--bd:#d9e0e7;--ac:#1d6fd8}
@media (prefers-color-scheme:dark){:root{--bg:#10151b;--card:#18202a;--tx:#e6ebf0;--mu:#94a1ae;--bd:#2b3643;--ac:#6aa8ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.6 system-ui,'Malgun Gothic',sans-serif}
main{max-width:980px;margin:0 auto;padding:16px}h1{font-size:22px;margin:8px 0}h2{font-size:17px;margin:0 0 8px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px 16px;margin:12px 0}
.mu{color:var(--mu);font-size:13px}.st{background:var(--card);border-left:4px solid var(--ac);padding:8px 12px;border-radius:6px}
table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid var(--bd);padding:6px 8px;text-align:left}th{color:var(--mu);font-weight:600}
.scroll{overflow-x:auto}ul{margin:0;padding-left:20px}a{color:var(--ac)}"""


def render(state):
    e = html.escape
    parts = [f"<h1>{e(state['title'])}</h1><div class=st>{e(state['status'])}</div><div class=mu>갱신 {e(state['updated'])} · <a href='status.html'>← 봇 화면</a></div>"]
    for s in state['sections']:
        if s['type'] == 'list':
            body = '<ul>' + ''.join(f'<li>{e(x)}</li>' for x in s['items']) + '</ul>'
        else:
            body = ("<div class=scroll><table><tr>" + ''.join(f'<th>{e(c)}</th>' for c in s['cols']) + '</tr>'
                    + ''.join('<tr>' + ''.join(f'<td>{e(c)}</td>' for c in r) + '</tr>' for r in s['rows']) + '</table></div>')
        parts.append(f"<div class=card><h2>{e(s['h'])}</h2>{body}</div>")
    return ("<!doctype html><html lang=ko><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>스윙 리서치창</title><style>{CSS}</style></head><body><main>{''.join(parts)}</main></body></html>")


def main(argv):
    out = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT
    state = json.loads(STATE.read_text(encoding='utf-8'))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(state), encoding='utf-8')
    print('작성:', out)


if __name__ == '__main__':
    main(sys.argv)
