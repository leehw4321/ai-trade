"""화면 맨 위 큰 안내 배너(공지). 개선 결과·검토 결과를 사용자가 놓치지 않게 알린다.

data/notices.json 에 쌓이고, 사용자가 '확인했습니다'를 누르기 전까지 화면 맨 위에 큰 글씨로 남는다.
level 'action'은 사용자 결정이 필요한 알림(노란 큰 배너 + 한 번 알림창), 'info'는 알림만(파란 배너).
"""
import html
import json
import os
from pathlib import Path

LEVELS = ('action', 'info')


def _read(path):
    try:
        d = json.loads(Path(path).read_text(encoding='utf-8'))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _write(path, items):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + '.tmp')
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, p)


def add(path, nid, title, body, level='info', at='', popup=False):
    """같은 id가 이미 있으면 다시 만들지 않는다(확인한 공지가 되살아나지 않는다). 새로 만들면 True."""
    items = _read(path)
    if any(i.get('id') == nid for i in items):
        return False
    items.append({'id': nid, 'title': title, 'body': body, 'level': level if level in LEVELS else 'info', 'at': at,
                  'ack': False, 'popup': bool(popup), 'popped': False})
    _write(path, items[-100:])
    return True


def ack(path, nid):
    items = _read(path)
    hit = False
    for i in items:
        if i.get('id') == nid and not i.get('ack'):
            i['ack'] = True; hit = True
    if hit:
        _write(path, items)
    return hit


def active(path):
    return [i for i in _read(path) if not i.get('ack')]


def take_popups(path):
    """알림창을 아직 안 띄운 공지를 돌려주고 띄운 것으로 표시한다."""
    items = _read(path)
    out = [i for i in items if i.get('popup') and not i.get('popped') and not i.get('ack')]
    if out:
        for i in items:
            if i in out:
                i['popped'] = True
        _write(path, items)
    return out


def banner(items, action=None):
    """맨 위 배너 HTML. action={'port','token'}이 있으면 '확인했습니다' 버튼이 동작한다."""
    if not items:
        return ''
    e = html.escape
    rows = []
    for i in items:
        big = i['level'] == 'action'
        style = ('background:#fff1b8;color:#2b2100;border:3px solid #e0a800;font-size:21px' if big
                 else 'background:#dcecff;color:#10253d;border:2px solid #5b92d6;font-size:16px')
        btn = (f"<button type=button data-nid='{e(i['id'])}' style='margin-top:10px;padding:10px 20px;font-size:17px;font-weight:700;"
               "border:2px solid #2b2100;border-radius:8px;background:#fff;color:#2b2100;cursor:pointer'>확인했습니다</button>") if action else ''
        body = ''.join(f'<div>{e(line)}</div>' for line in str(i.get('body', '')).split('\n'))
        rows.append(f"<div style='{style};padding:16px 20px;border-radius:10px;line-height:1.55'><b style='font-size:1.15em'>"
                    f"{'📢 ' if big else 'ℹ️ '}{e(i['title'])}</b>"
                    f"<div style='margin-top:6px;font-weight:{600 if big else 400}'>{body}</div>"
                    f"<div style='font-size:12px;opacity:.7;margin-top:4px'>{e(str(i.get('at', '')))}</div>{btn}</div>")
    js = ''
    if action:
        js = ("<script>(function(){var A=" + json.dumps({'port': action['port'], 'token': action['token']}) + ";"
              "document.querySelectorAll('button[data-nid]').forEach(function(b){b.onclick=function(){b.disabled=true;"
              "fetch('http://127.0.0.1:'+A.port+'/notice_ack',{method:'POST',headers:{'Content-Type':'text/plain'},"
              "body:JSON.stringify({token:A.token,id:b.dataset.nid,confirm:true})}).then(function(r){return r.json()}).then(function(j){if(!j.ok){throw 0}"
              "var c=b.parentNode;c.parentNode.removeChild(c)})"
              ".catch(function(){b.disabled=false;b.textContent='실패 - 다시 누르세요'})}})})()</script>")
    return f"<div id=notices style='display:flex;flex-direction:column;gap:12px'>{''.join(rows)}</div>{js}"
