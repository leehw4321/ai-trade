"""시점별(point-in-time) 거래대금 상위 N 유니버스 재구성. 미래 봉을 절대 쓰지 않는다.

판단 시각 T(분 단위 에포크, '봉 마감 시각')에 대해, 그 시각까지 완전히 마감된 봉만 사용한다.
봉의 시작 시각 s에 대해 마감 = s + 15분이므로 마감 ≤ T 인 봉만 대상이다.
순위 = 직전 `window`(기본 96)개 시각 슬롯의 거래대금(val) 합. 슬롯 중 `min_bars` 이상 봉이 있는 종목만 후보(상장 직후·결측 종목 제외).
스테이블 코인 등 제외 목록은 V5 전략과 같은 값을 쓴다.
"""
import json
from calendar import timegm
from datetime import datetime
from pathlib import Path

BAR = 15 * 60
EXCLUDE = {'KRW-USDT', 'KRW-USDC', 'KRW-USDE', 'KRW-USD1', 'KRW-DAI'}


def to_epoch(kst_text):
    """'YYYY-MM-DDTHH:MM:SS'(KST 벽시계)를 같은 눈금의 정수 초로. 시간대 변환 없이 일관된 눈금만 필요하다."""
    return timegm(datetime.fromisoformat(kst_text).timetuple())


def load_dir(folder):
    """{종목: {봉 시작 에포크: (o, h, l, c, val)}}"""
    data = {}
    for path in sorted(Path(folder).glob('KRW-*.jsonl')):
        bars = {}
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                r = json.loads(line)
                bars[to_epoch(r['t'])] = (r['o'], r['h'], r['l'], r['c'], r['val'])
            except (ValueError, KeyError, TypeError):
                continue
        if bars:
            data[path.stem] = bars
    return data


def universe_at(data, T, top_n=30, window=96, min_bars=90, exclude=EXCLUDE):
    """T(봉 마감 시각, 에포크 초) 시점의 상위 top_n 종목 [(종목, 거래대금합)] 내림차순.

    미래 정보 금지: 시작 시각 s가 T - BAR 이하인(=마감이 T 이하인) 봉만 읽는다."""
    last_start = T - BAR
    first_start = last_start - (window - 1) * BAR
    rows = []
    for market, bars in data.items():
        if market in exclude:
            continue
        total, count = 0.0, 0
        for s in range(first_start, last_start + 1, BAR):
            b = bars.get(s)
            if b is not None:
                total += b[4]
                count += 1
        if count >= min_bars:
            rows.append((market, total))
    rows.sort(key=lambda x: (-x[1], x[0]))
    return rows[:top_n]


def completeness(data, start, end):
    """각 종목의 [start, end) 봉 개수 비율. 결측 점검용."""
    slots = (end - start) // BAR
    return {m: sum(1 for s in range(start, end, BAR) if s in bars) / slots for m, bars in data.items()}
