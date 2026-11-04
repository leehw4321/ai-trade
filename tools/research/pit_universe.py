"""시점별(point-in-time) 거래대금 상위 N 유니버스 재구성. 미래 봉을 절대 쓰지 않는다.

시간 규약(2026-11-04 ChatGPT 검증 수정 반영):
- 모든 시각은 **실제 UTC 에포크(초)** 로 다룬다. 업비트 15분봉의 KST 벽시계 문자열은 +09:00으로 해석해 변환한다.
- 판단 시각 T는 15분(900초) 경계의 UTC 에포크 초다. 경계가 아니면 거부한다(조용히 내림하지 않는다).
- 봉의 시작 시각 s에 대해 마감 = s + 900초. 마감 ≤ T 인 봉만 사용한다. 마지막 대상 봉의 시작은 T − 900.
- 순위 = 직전 `window`(기본 96)개 슬롯의 거래대금(val) 합. 슬롯 중 `min_bars` 이상 봉이 있는 종목만 후보. 결측은 0으로 채우지 않는다
  (합산에는 기여하지 않고 존재 개수 조건에만 반영). 무거래·수집 누락·정지는 이 파일만으로 구별할 수 없다.
- 수집 자료의 최신 쪽에는 진행 중이던 미완성 봉이 있고 종목마다 마지막 수집 시각이 다르다. 분석은 반드시 **공통 컷오프 C**를 정해
  load_dir(..., cutoff=C)로 '마감 ≤ C' 봉만 읽는다(C는 모든 종목 수집이 시작되기 전 15분 경계여야 한다).
- 제외 목록은 V5 전략(strategy.Params.exclude)과 같은 5종이며 역사적 분류를 재현하지 않는다. 마켓 목록이 현재 상장 종목뿐이라 상장폐지 종목이 빠진
  생존 편향이 있다. 결과에 이 한계를 고정 기록한다.
"""
import json
import math
from calendar import timegm
from datetime import datetime, timedelta, timezone
from pathlib import Path

BAR = 15 * 60
KST = timezone(timedelta(hours=9))
EXCLUDE = frozenset({'KRW-USDT', 'KRW-USDC', 'KRW-USDE', 'KRW-USD1', 'KRW-DAI'})


def to_epoch(kst_text):
    """'YYYY-MM-DDTHH:MM:SS'(KST 벽시계)를 실제 UTC 에포크 초로."""
    return int(datetime.fromisoformat(kst_text).replace(tzinfo=KST).timestamp())


def from_epoch_kst(epoch):
    return datetime.fromtimestamp(epoch, KST).strftime('%Y-%m-%dT%H:%M:%S')


def load_dir(folder, cutoff=None, stats=None):
    """{종목: {봉 시작 에포크: (o, h, l, c, val)}}.

    cutoff(에포크 초, 15분 경계)가 있으면 마감(시작+900)이 cutoff를 넘는 봉은 버린다(미완성·늦게 수집된 봉).
    stats(dict)가 있으면 불량 행 개수를 센다: bad_json, bad_value(비유한·음수·0 이하 가격), off_grid, conflict_dup."""
    if cutoff is not None and cutoff % BAR:
        raise ValueError('cutoff는 15분 경계여야 합니다')
    st = stats if stats is not None else {}
    for k in ('bad_json', 'bad_value', 'off_grid', 'conflict_dup', 'after_cutoff', 'rows'):
        st.setdefault(k, 0)
    data = {}
    for path in sorted(Path(folder).glob('KRW-*.jsonl')):
        bars = {}
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                r = json.loads(line)
                s = to_epoch(r['t'])
                row = (float(r['o']), float(r['h']), float(r['l']), float(r['c']), float(r['val']))
            except (ValueError, KeyError, TypeError):
                st['bad_json'] += 1
                continue
            st['rows'] += 1
            if not all(math.isfinite(x) for x in row) or row[4] < 0 or min(row[:4]) <= 0:
                st['bad_value'] += 1
                continue
            if s % BAR:
                st['off_grid'] += 1
                continue
            if cutoff is not None and s + BAR > cutoff:
                st['after_cutoff'] += 1
                continue
            if s in bars and bars[s] != row:
                st['conflict_dup'] += 1
            bars[s] = row
        if bars:
            data[path.stem] = bars
    return data


def universe_at(data, T, top_n=30, window=96, min_bars=90, exclude=EXCLUDE):
    """T(봉 마감 시각, UTC 에포크 초) 시점의 상위 top_n 종목 [(종목, 거래대금합)] 내림차순(동률은 종목 코드순).

    미래 정보 금지: 시작 시각 s가 T - BAR 이하인(=마감이 T 이하인) 봉만 읽는다."""
    if type(T) is not int or T % BAR:
        raise ValueError('T는 15분 경계의 정수 UTC 에포크 초여야 합니다')
    for name, v in (('top_n', top_n), ('window', window), ('min_bars', min_bars)):
        if type(v) is not int or v < 1:
            raise ValueError(f'{name}은 1 이상의 정수여야 합니다')
    if min_bars > window:
        raise ValueError('min_bars는 window보다 클 수 없습니다')
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
