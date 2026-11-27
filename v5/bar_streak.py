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


def render(row):
    """'1분 ▲3 · 5분 ▼2 …' (색: 상승 up, 하락 dn). 비어 있으면 ''."""
    parts = []
    for unit, label in UNITS:
        s = row.get(unit)
        if s is None:
            continue
        mark = f"<span class=up>▲{s}</span>" if s > 0 else f"<span class=dn>▼{-s}</span>" if s < 0 else '<span class=muted>–</span>'
        parts.append(f'{html.escape(label)} {mark}')
    return " <div class=sub title='봉별 연속 상승(▲)·하락(▼) 수: 마감된 봉 종가를 직전 봉과 비교'>" + ' · '.join(parts) + '</div>' if parts else ''
