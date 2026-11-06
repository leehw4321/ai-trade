"""시장 자세 바(폭락·약세·횡보·상승·폭등)와 공포·탐욕 지수. 화면 표시와 기록 전용이며 주문 판단에 쓰지 않는다.

자세는 봇의 장세 필터와 같은 '거래대금 상위 종목 균등지수 24시간 수익률'로 정한다. 구간 경계(±2%, ±6%)는 보기 위한 눈금이다.
공포·탐욕은 Alternative.me의 일간 BTC 중심 지표(0 극도의 공포 ~ 100 극도의 탐욕)이며 v4 화면과 같은 출처다.
단기 브레이크 기록: 4시간 균등지수 ≤ −2%면 '브레이크 발동(기록만)'으로 남긴다(2026-11-06 정체·장세 제안 안 A, 실거래 미적용).
"""
import html
import json
import math
import os
import threading
import time
import urllib.request

ZONES = (('폭락', -1e9, -0.06, '#b42318'), ('약세', -0.06, -0.02, '#e8833a'), ('횡보', -0.02, 0.02, '#9aa6af'),
         ('상승', 0.02, 0.06, '#5fae4b'), ('폭등', 0.06, 1e9, '#1a7f37'))
SPAN = 0.10            # 바의 양 끝 = ±10%
BRAKE_4H = -0.02
FNG_URL = 'https://api.alternative.me/fng/?limit=1'
FNG_EVERY = 3600
FNG_LABEL = {'Extreme Fear': '극도의 공포', 'Fear': '공포', 'Neutral': '중립', 'Greed': '탐욕', 'Extreme Greed': '극도의 탐욕'}


def zone(x):
    for name, lo, hi, color in ZONES:
        if lo <= x < hi:
            return name, color
    return ZONES[-1][0], ZONES[-1][3]


def _ret(bars, n):
    try:
        if len(bars) <= n: return None
        a, b = float(bars[-1 - n]['c']), float(bars[-1]['c'])
        return b / a - 1 if a > 0 and b > 0 and math.isfinite(a) and math.isfinite(b) else None
    except (KeyError, TypeError, ValueError):
        return None


def compute(bars_uni, index_24h, index_4h, at, tf=15, index_6h=None, filter_hours=24):
    """15분 판단마다 부른다. bars_uni = 거래대금 상위 종목 봉."""
    n24 = 24 * 60 // tf
    r24 = [r for r in (_ret(b, n24) for b in bars_uni.values()) if r is not None]
    btc = _ret(bars_uni.get('KRW-BTC', []), n24)
    return {'at': at, 'index_24h': index_24h, 'index_4h': index_4h, 'index_6h': index_6h, 'filter_hours': filter_hours,
            'up': sum(r > 0 for r in r24), 'total': len(r24), 'btc_24h': btc,
            'zone': zone(index_24h)[0] if isinstance(index_24h, (int, float)) else None,
            'brake_4h': isinstance(index_4h, (int, float)) and index_4h <= BRAKE_4H}


def save(path, state):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, path)


def load(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def record_brake(path, state):
    """정시마다 단기 브레이크가 발동했을지 기록만 한다(주문 무영향)."""
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps({k: state.get(k) for k in ('at', 'index_24h', 'index_4h', 'brake_4h', 'up', 'total')}, ensure_ascii=False) + '\n')


def fetch_fng(path, now=None, opener=urllib.request.urlopen):
    """공포·탐욕을 받아 저장한다. 실패하면 이전 값을 그대로 둔다."""
    req = urllib.request.Request(FNG_URL, headers={'User-Agent': 'v5-status'})
    with opener(req, timeout=10) as r:
        row = json.loads(r.read())['data'][0]
    v = int(row['value'])
    if not 0 <= v <= 100:
        raise ValueError('범위 밖')
    save(path, {'value': v, 'label': FNG_LABEL.get(row.get('value_classification'), row.get('value_classification')),
                'day': int(row.get('timestamp', 0)), 'fetched': now or time.time()})


def fng_background(path, last):
    """마지막 수신 뒤 FNG_EVERY가 지났으면 별도 스레드로 받는다. 화면·거래 루프를 기다리게 하지 않는다."""
    old = load(path) or {}
    if time.time() - max(old.get('fetched', 0), last) < FNG_EVERY:
        return last
    threading.Thread(target=lambda: _quiet(fetch_fng, path), daemon=True).start()
    return time.time()


def _quiet(fn, *a):
    try: fn(*a)
    except Exception: pass


def _filter_note(state):
    """봇이 실제로 쓰는 매수 필터 기간과 그 값(24시간이 아닐 때만 따로 보인다)."""
    h = state.get('filter_hours') or 24
    v = state.get('index_24h') if h == 24 else state.get(f'index_{h}h')
    if not isinstance(v, (int, float)) or h == 24:
        return ''
    ok = v > 0
    return (f"<b style='font-size:12.5px;color:{'#1a7f37' if ok else '#b42318'}'>매수 필터 {h}h {v:+.2%} → {'매수 가능' if ok else '매수 보류'}</b>")


def _pos(x):
    return max(1.0, min(99.0, (x + SPAN) / (2 * SPAN) * 100))


def render(state, fng, now=None, esc=html.escape):
    """머리글의 새로고침 버튼 옆에 들어가는 한 줄짜리 자세 바와 공포·탐욕."""
    now = now or time.time()
    if not state or not isinstance(state.get('index_24h'), (int, float)):
        bar = "<span class=sub>시장 자세: 첫 판단 뒤 표시</span>"
    else:
        x = state['index_24h']; name, color = zone(x)
        segs = ''.join(f"<div style='flex:{(min(hi, SPAN) - max(lo, -SPAN)) / (2 * SPAN):.3f};background:{c};opacity:{1 if n == name else .55};"
                       f"color:#fff;font-size:10.5px;text-align:center;line-height:18px;font-weight:{800 if n == name else 400}'>{n}</div>"
                       for n, lo, hi, c in ZONES)
        x4 = state.get('index_4h')
        m4 = (f"<span title='4시간 균등지수 {x4:+.2%}' style='position:absolute;left:{_pos(x4):.1f}%;top:17px;transform:translateX(-50%);"
              f"font-size:9px;line-height:9px;color:var(--muted)'>▲</span>") if isinstance(x4, (int, float)) else ''
        btc = state.get('btc_24h')
        tip = (f"거래대금 상위 균등지수(봇 장세 필터와 같은 값, 0 이하면 신규 매수 보류) · 양 끝 ±10% · 눈금 ±2%·±6%는 보기용 · "
               f"▲ = 4시간 · {str(state.get('at', ''))[11:16]} 판단" + (f" · BTC 24시간 {btc:+.2%}" if isinstance(btc, (int, float)) else ''))
        bar = (f"<span title='{esc(tip)}' style='display:inline-flex;align-items:center;gap:8px'>"
               f"<b style='font-size:16px;color:{color}'>{esc(name)}</b>"
               f"<span style='position:relative;display:inline-block;width:250px;height:26px'>"
               f"<span style='display:flex;height:18px;border-radius:4px;overflow:hidden'>{segs}</span>"
               f"<span style='position:absolute;left:{_pos(x):.1f}%;top:-3px;width:3px;height:24px;background:var(--ink);transform:translateX(-50%)'></span>{m4}</span>"
               f"<span class=sub>24h <b>{x:+.2%}</b>" + (f" · 4h {x4:+.2%}" if isinstance(x4, (int, float)) else '')
               + f" · 오름 {state.get('up', 0)}/{state.get('total', 0)}</span>"
               + _filter_note(state)
               + ("<b class=warn style='font-size:12px'>4h 급락: 브레이크 기록</b>" if state.get('brake_4h') else '') + "</span>")
    if fng and isinstance(fng.get('value'), int):
        v = fng['value']; old = now - fng.get('fetched', 0) > 48 * 3600
        c = '#dc2626' if v < 25 else '#f97316' if v < 45 else '#eab308' if v < 55 else '#84cc16' if v < 75 else '#16a34a'
        g = (f"<span title='공포·탐욕 지수(Alternative.me 일간, BTC 중심) 0 극도의 공포 ~ 100 극도의 탐욕' "
             f"style='display:inline-flex;align-items:center;gap:6px'><span class=sub>공포·탐욕</span>"
             f"<b style='font-size:18px;color:{c}'>{v}</b><span class=sub>{esc(str(fng.get('label') or ''))}{' (오래됨)' if old else ''}</span>"
             f"<span style='position:relative;display:inline-block;width:80px;height:8px;border-radius:4px;"
             f"background:linear-gradient(90deg,#dc2626,#f97316,#eab308,#84cc16,#16a34a)'>"
             f"<span style='position:absolute;left:{max(1, min(99, v))}%;top:-3px;width:3px;height:14px;background:var(--ink);transform:translateX(-50%)'></span></span></span>")
    else:
        g = "<span class=sub>공포·탐욕 수신 대기</span>"
    return f"<div style='display:flex;flex-wrap:wrap;align-items:center;gap:6px 20px;font-weight:400'>{bar}{g}</div>"
