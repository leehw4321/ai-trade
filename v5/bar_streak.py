"""순위표 종목별 분봉 연속 상승·하락 수 (사용자 지시 2026-11-27: 1분·5분·15분 등 참고할 시간 단위별로 몇 봉째 연속 오르는지/내리는지).

표시 전용. 주문·판단에는 쓰지 않는다. 업비트 공개 봉(GET)만 쓴다.
- 단위: 1분(지금 흐름) · 5분(짧은 추세) · 15분(매수 판단 봉) · 60분(큰 흐름).
- 연속 수: 마감된 봉의 종가를 직전 봉 종가와 비교해 같은 방향이 몇 번 이어졌는지. 같으면(보합) 거기서 끊는다. +3 = 3봉 연속 상승, −2 = 2봉 연속 하락, 0 = 직전 봉 보합.
- 조회는 새 봉이 마감됐을 때만 하고(종목·단위별 캐시), 한 번 그릴 때 조회 수를 budget으로 제한해 화면 갱신을 늦추지 않는다.
"""
import datetime as dt
import html
import time

UNITS = ((1, '1분'), (5, '5분'), (15, '15분'), (60, '1시간'))
RETRY_SECONDS = 30


def streak(closes):
    """종가 목록(시간순) → 연속 수(+상승/−하락)."""
    n, way = 0, 0
    for i in range(len(closes) - 1, 0, -1):
        d = (closes[i] > closes[i - 1]) - (closes[i] < closes[i - 1])
        if d == 0 or (way and d != way):
            break
        way, n = d, n + 1
    return way * n


class Streaks:
    def __init__(self, fetch=None, clock=time.time):
        if fetch is None:
            import upbit
            fetch = upbit.completed_candles
        self.fetch, self.clock, self.cache = fetch, clock, {}

    def get(self, markets, budget=12):
        """{종목: {단위(분): 연속 수}}. 아직 못 받은 단위는 빠진다."""
        now, out = self.clock(), {}
        for m in markets:
            row = {}
            for unit, _ in UNITS:
                c = self.cache.get((m, unit))
                if (c is None or now >= c['next']) and budget > 0:
                    budget -= 1
                    try:
                        bars = self.fetch(m, unit, count=40)
                        end = dt.datetime.fromisoformat(bars[-1]['t']).timestamp() + unit * 60
                        c = {'s': streak([b['c'] for b in bars]), 'next': end + unit * 60 + 2}
                    except Exception:
                        c = dict(c or {}, next=now + RETRY_SECONDS) if c else {'next': now + RETRY_SECONDS}
                    self.cache[(m, unit)] = c
                if c and 's' in c:
                    row[unit] = c['s']
            out[m] = row
        return out


LIGHT = {'green': ('#2e9e5b', '초록'), 'orange': ('#e8960c', '주황'), 'red': ('#d64545', '빨강')}


def light15(state, s15):
    """15분 신호등 = 지금 매수 규칙(15분봉 18시간 상승률 순위 7위 안·상승률 0 초과·막는 조건 없음)에 맞춘다.
    state: 'buy'(매수 후보) | 'wait'(관찰·재매수 대기·과열 보류·보유 등) | 'block'(매수 불가 경보·상승률 0 이하).
    매수 후보라도 15분봉이 2연속 하락 중이면 빨강."""
    if state == 'block' or (isinstance(s15, int) and s15 <= -2):
        return 'red'
    return 'green' if state == 'buy' else 'orange'


def light5(s5):
    """5분 신호등(참고): 2연속 하락 = 빨강(과거 2주 후보에서 이때 산 것은 평균 −1.20%), 상승 중 = 초록, 보합·1봉 하락 = 주황."""
    if not isinstance(s5, int):
        return None
    return 'red' if s5 <= -2 else 'green' if s5 >= 1 else 'orange'


def lights_html(row, state):
    a, b = light5(row.get(5)), light15(state, row.get(15))
    if a is None:
        return ''
    dot = lambda c, label: (f"<span title='{label} {LIGHT[c][1]}' style='display:inline-block;width:11px;height:11px;border-radius:50%;"
                            f"background:{LIGHT[c][0]};vertical-align:-1px;margin:0 2px'></span>")
    sig = " <b class=up>매수 신호</b>" if a == b == 'green' else ''
    return f"5분{dot(a, '5분')} 15분{dot(b, '15분')}{sig}<br>"   # 신호등 한 줄, 연속 수 한 줄(칸이 넓어지지 않게)


def render(row, state=None):
    """'5분● 15분● · 1분 ▲3 · 5분 ▼2 …' (색: 상승 up, 하락 dn). state가 있으면 앞에 신호등. 비어 있으면 ''."""
    parts = []
    for unit, label in UNITS:
        s = row.get(unit)
        if s is None:
            continue
        mark = f"<span class=up>▲{s}</span>" if s > 0 else f"<span class=dn>▼{-s}</span>" if s < 0 else '<span class=muted>–</span>'
        parts.append(f'{html.escape(label)} {mark}')
    if not parts:
        return ''
    tip = ('신호등 15분: 초록=지금 매수 후보, 주황=대기(관찰·재매수 대기·과열·보유), 빨강=매수 불가·상승률 0 이하·15분봉 2연속 하락. '
           '5분(참고, 아직 매수 규칙 아님): 초록=상승 중, 주황=보합·1봉 하락, 빨강=2연속 하락. 둘 다 초록이면 매수 신호. '
           '▲▼ = 마감된 봉 종가를 직전 봉과 비교한 연속 상승·하락 수')
    return f" <div class=sub title='{tip}'>" + (lights_html(row, state) if state else '') + ' · '.join(parts) + '</div>'
