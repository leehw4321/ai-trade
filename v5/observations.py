"""봇 화면의 '관찰·그림자 관리' 창: 앞으로 확인할 일, 그림자 후보, 사용자 결정 대기 항목을 기록하고 상기시킨다.

주문과 무관하다. 파일 하나(data/observations.json)에 항목을 두고, 화면의 버튼(완료·보류·다시 진행·메모)과 tools/obs.py가 같은 파일을 고친다.
기한이 지나거나 24시간 안으로 다가오면 접힌 제목 줄에 빨간 표시가 나와서, 사용자가 잊어도 화면을 열면 보인다.
"""
import datetime as dt
import html
import json
import re
import threading
from pathlib import Path

KST = dt.timezone(dt.timedelta(hours=9))
LOCK = threading.Lock()
ID = re.compile(r'^[a-z0-9_-]{1,40}$')
ACTIONS = {'done': '완료', 'hold': '보류', 'open': '진행'}
KINDS = {'deadline': '기한', 'shadow': '그림자', 'decision': '결정 대기', 'observe': '관찰'}
MAX_NOTE = 300
SOON = dt.timedelta(hours=24)


def _item(id, title, kind, due=None, status='진행', criteria='', source=''):
    return {'id': id, 'title': title, 'kind': kind, 'due': due, 'status': status, 'criteria': criteria, 'source': source,
            'created': '2026-11-05', 'notes': []}


SEED = [
    _item('regime-expiry', '장세 필터 만료 결정 (유지·해제·만료 중 선택)', 'deadline', '2026-11-07T07:51:00+09:00',
          criteria='만료되면 자동으로 필터 없는 거래가 아니라 신규 매수 전체 중단. 11-05 검토: 24개 슬롯 모두 지수 양수, 차단 0회라 효과 판단 불가.',
          source='2026-11-03-claude-verification.md 끝(11-05 07:57)'),
    _item('week-compare', '1주일 비교: 순위 5→8→(진입7/유지9), 이익보호 반납 40→30%, 동시 보유 경보 기록', 'deadline', '2026-11-11T18:17:00+09:00',
          criteria='시장 균등 평균·같은 금액 보유와 나란히 비교. 그림자 L32/L72/L168×M5/M8(비용 0.85% 반영). 단기 수익만으로 효과를 선언하지 않음.',
          source='LATEST.md 3-2'),
    _item('r1-shadow', '손절 뒤 48시간 같은 종목 재진입 금지(R1) — 그림자 후보', 'shadow', status='보류',
          criteria='3년 재생에서 막힌 거래 평균 −0.03/−0.12/−0.10%로 3구간 일관 음수. 실거래 52건 효과는 약 +1.6천 원. 실거래 적용 전 그림자 기록 필요.',
          source='2026-11-05-regime-exit-results.md 및 대화 11-05'),
    _item('limit-order', '지정가 매수·정리 매도 — 호가·체결 기록기(미구현, 사용자 결정 대기)', 'decision',
          criteria='챗 의견: 신규 매수 먼저, 체결 낙관·보수 두 경우, 최소 30일·청산 100건, 취소 중 추가 체결·중복 주문 위험 대비. 손절·이익보호는 시장가 유지.',
          source='originals/limit-order-idea-2026-11-05.*'),
    _item('liquidity-cap', '자금 확대 안전장치(유동성 상한·큰 주문 분할·체결 기록) — 미구현, 사용자 결정 대기', 'decision',
          criteria='호가창 측정: 100배(780만 원)까지 평균 슬리피지 +0.1%p, 얇은 종목(LSK·ARX)은 10배에서 급증. 상한은 슬리피지 0.3%·24시간 거래대금 0.05% 후보.',
          source='대화 11-05'),
    _item('knife-entry', '직전 1시간 급락 후 진입(ARX 유형) 3년 자료 검증 — 미시행', 'decision',
          criteria='ARX는 직전 60분 −7.4% 뒤 진입해 손절. 이전 분석에서 낙폭 큰 진입이 나빴으나 사전 등록 검증은 아직 없음.',
          source='대화 11-05'),
    _item('align-config-bug', 'take_over의 설정 패치 적용 순서 결함(이익보호 3%→2% 되돌림 가능) — 미수정', 'decision',
          criteria='패치를 파일명 순으로 덮어씀. 새 패치는 정렬상 뒤에 오도록 이름을 정해 우회 중.', source='LATEST.md 4-2'),
]


def _now():
    return dt.datetime.now(KST)


def load(path):
    path = Path(path)
    with LOCK:
        try:
            doc = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(doc, dict) and isinstance(doc.get('items'), list):
                return doc
        except (OSError, ValueError):
            pass
    doc = {'items': json.loads(json.dumps(SEED, ensure_ascii=False))}
    save(path, doc)
    return doc


def save(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with LOCK:
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(path)


def apply(path, id, payload):
    """화면 버튼·CLI 공통 처리. payload: {'action': done|hold|open|note, 'note': 글(선택)}. (성공 여부, 안내)"""
    if not isinstance(id, str) or not ID.match(id) or not isinstance(payload, dict):
        return False, '항목 형식 오류'
    action, note = payload.get('action'), payload.get('note')
    if action not in ACTIONS and action != 'note':
        return False, '알 수 없는 동작'
    if note is not None and (not isinstance(note, str) or len(note) > MAX_NOTE):
        return False, f'메모는 {MAX_NOTE}자 이내 글이어야 합니다'
    doc = load(path)
    item = next((x for x in doc['items'] if x.get('id') == id), None)
    if item is None:
        return False, '없는 항목입니다. 화면을 새로고침하세요'
    stamp = _now().isoformat(timespec='minutes')
    if action in ACTIONS:
        item['status'] = ACTIONS[action]
        item.setdefault('notes', []).append({'t': stamp, 'text': f'상태 → {ACTIONS[action]}' + (f' · {note}' if note else '')})
    elif note:
        item.setdefault('notes', []).append({'t': stamp, 'text': note})
    else:
        return False, '메모 내용이 없습니다'
    save(path, doc)
    return True, f"{item['title'][:24]} … 저장했습니다"


def add(path, id, title, kind='observe', due=None, criteria='', source=''):
    if not ID.match(id) or kind not in KINDS:
        raise ValueError('id 또는 종류가 올바르지 않습니다')
    if due:
        dt.datetime.fromisoformat(due)
    doc = load(path)
    if any(x.get('id') == id for x in doc['items']):
        raise ValueError('이미 있는 id입니다')
    doc['items'].append({'id': id, 'title': title, 'kind': kind, 'due': due, 'status': '진행', 'criteria': criteria, 'source': source,
                         'created': _now().date().isoformat(), 'notes': []})
    save(path, doc)


def _due_info(item, now):
    """(정렬 키, 표시 문구, 등급 'over'|'soon'|'later'|'none')"""
    if not item.get('due'):
        return (1, 0), '기한 없음', 'none'
    try:
        due = dt.datetime.fromisoformat(item['due'])
        if due.tzinfo is None:
            due = due.replace(tzinfo=KST)
    except ValueError:
        return (1, 0), '기한 형식 오류', 'none'
    left = due - now
    when = due.astimezone(KST).strftime('%m-%d %H:%M')
    if left.total_seconds() < 0:
        h = int(-left.total_seconds() // 3600)
        return (0, left.total_seconds()), f'{when} · 기한 지남 {h // 24}일 {h % 24}시간' if h >= 24 else f'{when} · 기한 지남 {h}시간', 'over'
    h = int(left.total_seconds() // 3600)
    txt = f'{when} · {h // 24}일 {h % 24}시간 남음' if h >= 24 else f'{when} · {h}시간 남음'
    return (0, left.total_seconds()), txt, ('soon' if left <= SOON else 'later')


def summary(doc, now=None):
    """(진행 중 건수, 기한 지남, 24시간 이내)"""
    now = now or _now()
    live = [x for x in doc['items'] if x.get('status') == '진행']
    over = sum(1 for x in live if _due_info(x, now)[2] == 'over')
    soon = sum(1 for x in live if _due_info(x, now)[2] == 'soon')
    return len(live), over, soon


def render(path, action=None, now=None):
    """접어 둘 수 있는 창 HTML. action = {'port':…, 'token':…} 또는 None(버튼 비활성)."""
    now = now or _now()
    e = html.escape
    doc = load(path)
    live, over, soon = summary(doc, now)
    chip = ''
    if over:
        chip = f"<span class=obs-chip style='background:#d64545;color:#fff'>기한 지남 {over}건</span>"
    elif soon:
        chip = f"<span class=obs-chip style='background:#d9a400;color:#17212b'>24시간 안 {soon}건</span>"
    order = {'진행': 0, '보류': 1, '완료': 2}
    items = sorted(doc['items'], key=lambda x: (order.get(x.get('status'), 3), _due_info(x, now)[0]))
    rows = []
    for x in items:
        key, due_txt, grade = _due_info(x, now)
        state = x.get('status', '진행')
        color = {'over': '#d64545', 'soon': '#b8860b'}.get(grade, 'inherit') if state == '진행' else 'inherit'
        notes = ''.join(f"<div class=sub>{e(n.get('t', '')[5:16].replace('T', ' '))} {e(n.get('text', ''))}</div>" for n in (x.get('notes') or [])[-2:])
        btns = ''
        for act, label in (('done', '완료'), ('hold', '보류'), ('open', '다시 진행'), ('note', '메모')):
            if act in ACTIONS and ACTIONS[act] == state:
                continue
            btns += f"<button type=button class=obs-btn data-id='{e(x['id'])}' data-act='{act}'>{label}</button>"
        rows.append(f"<tr style='opacity:{1 if state != '완료' else .55}'><td><b>{e(x.get('title', ''))}</b><div class=sub>{e(KINDS.get(x.get('kind'), x.get('kind', '')))}"
                    f" · 근거: {e(x.get('source', '') or '-')}</div><div class=sub>{e(x.get('criteria', ''))}</div>{notes}</td>"
                    f"<td style='color:{color};white-space:nowrap'>{e(due_txt)}</td><td>{e(state)}</td><td>{btns}</td></tr>")
    act = json.dumps(action).replace('<', '\\u003c') if action else 'null'
    return (f"<section id=obs-board><details id=obs-fold><summary class=lg>관찰·그림자 관리 {chip} · 진행 중 {live}건 (눌러서 열기·닫기)</summary>"
            "<p class=sub>앞으로 확인할 날짜, 그림자로 지켜보는 후보, 사용자 결정을 기다리는 일을 기록합니다. 주문과는 무관하며 기한이 지나면 제목 줄에 빨간 표시가 납니다.</p>"
            f"<table><tr><th>내용</th><th>기한</th><th>상태</th><th>선택</th></tr>{''.join(rows)}</table><p id=obs-res role=status class=sub></p></details>"
            "<style>#obs-board{border:2px solid var(--acc,#0f6e5c)}.obs-chip{font-size:12px;padding:3px 9px;border-radius:14px;margin-left:6px}"
            "#obs-board button{padding:5px 9px;margin:2px;border:1px solid var(--line,#ddd);border-radius:7px;background:var(--card,#fff);color:inherit;cursor:pointer}"
            "#obs-board button:disabled{opacity:.5;cursor:wait}#obs-board td{vertical-align:top}</style>"
            "<script>(function(){var d=document.getElementById('obs-fold');if(!d)return;try{d.open=localStorage.getItem('obsOpen')==='1'}catch(x){}"
            "d.addEventListener('toggle',function(){try{localStorage.setItem('obsOpen',d.open?'1':'0')}catch(x){}});"
            "var A=" + act + ",r=document.getElementById('obs-res');"
            "Array.prototype.forEach.call(document.querySelectorAll('.obs-btn'),function(b){if(!A){b.disabled=true;b.title='봇 제어 연결을 기다리는 중';return}"
            "b.addEventListener('click',function(){var act=b.getAttribute('data-act'),note=null;"
            "if(act==='note'){note=prompt('메모를 입력하세요(300자 이내)');if(!note)return}"
            "else if(act==='done'||act==='hold'){note=prompt('남길 메모(없으면 비워 두세요)')||null}"
            "b.disabled=true;r.textContent='저장 중…';"
            "fetch('http://127.0.0.1:'+A.port+'/obs',{method:'POST',headers:{'Content-Type':'text/plain'},body:JSON.stringify({token:A.token,confirm:true,id:b.getAttribute('data-id'),action:act,note:note})})"
            ".then(function(x){return x.json()}).then(function(j){r.textContent=(j.ok?'✔ ':'✘ ')+j.message;if(j.ok)setTimeout(function(){location.reload()},700);else b.disabled=false})"
            ".catch(function(){r.textContent='✘ 봇에 연결하지 못했습니다.';b.disabled=false})})})})();</script></section>")
