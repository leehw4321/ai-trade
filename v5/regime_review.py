"""장세 필터 해제 연구(2026-11-08): 24시간 시장 지수가 음수일 때 연 거래가 양수일 때보다 나쁜지 실거래로 모아 본다.

배경: 장세 필터(regime_filter)는 일봉·15분봉 백테스트 가설로 넣었고 실거래에서 증명된 적이 없다(2026-11-06 180일 15분봉: 막힌 진입 −0.240% vs 통과 −0.115%, 차이 신뢰구간이 0을 포함).
필터를 끄고 거래를 늘려 근거를 모은다. 프로젝트 문서: docs/ai-history/2026-11-08-regime-off-project.md

기준(결과를 보기 전에 고정, REVIEW_VERSION이 같은 동안 바꾸지 않는다):
  대상: SINCE 이후 연 V5 왕복(청산까지 끝난 것). 진입 시각의 직전 정시 24시간 지수(data/regime_watch.jsonl)로 음수군(지수 ≤ 0)과 양수군(> 0)을 나눈다.
  모으기: 두 군 모두 MIN_GROUP건 이상이고 SINCE부터 MIN_DAYS일이 지나면 판정한다. MAX_DAYS일이 지나도 모자라면 가진 만큼으로 판정(extend 표시).
  판정: 차이 = 음수군 평균 − 양수군 평균(비용 포함 수익률 %p), Welch t.
    restore(필터 복원 검토): 차이 ≤ −BAD_DIFF 이고 t ≤ −2
    keep_off(끈 채 유지):    음수군이 양수군과 같거나 낫다(차이 ≥ −KEEP_DIFF)
    extend(더 모으기):       그 밖(차이가 −KEEP_DIFF보다 나쁘지만 restore 기준은 못 채움)
판정과 안내만 한다. 주문·설정은 바꾸지 않고, 설정 변경은 사용자 승인 뒤에만 한다.
"""
import datetime as dt
import json
import math
import statistics as st
from pathlib import Path

import rebuy_review

REVIEW_VERSION = 'v1'
SINCE = '2026-11-08T00:55:00+09:00'
MIN_GROUP, MIN_DAYS, MAX_DAYS = 30, 3, 14
BAD_DIFF, KEEP_DIFF = 0.3, 0.1


def _t(s):
    return dt.datetime.fromisoformat(s)


def index_series(watch_jsonl):
    """[(시각, 24시간 지수)] 시각순. 읽지 못하면 빈 목록."""
    out = []
    try:
        for line in Path(watch_jsonl).read_text(encoding='utf-8').splitlines():
            try:
                r = json.loads(line)
                out.append((_t(r['at']), float(r['index_24h'])))
            except (ValueError, KeyError, TypeError):
                continue
    except OSError:
        return []
    out.sort()
    return out


def index_before(series, when):
    """when 이전(포함)의 마지막 기록. 없거나 6시간보다 오래됐으면 None."""
    last = None
    for t, v in series:
        if t.replace(tzinfo=when.tzinfo) <= when:
            last = (t, v)
        else:
            break
    if last is None or (when - last[0].replace(tzinfo=when.tzinfo)).total_seconds() > 6 * 3600:
        return None
    return last[1]


def welch(a, b):
    """(평균 차 a−b, t). 표본이 모자라면 (차, None)."""
    ma, mb = st.mean(a), st.mean(b)
    if len(a) < 2 or len(b) < 2:
        return ma - mb, None
    se = math.sqrt(st.variance(a) / len(a) + st.variance(b) / len(b))
    return ma - mb, ((ma - mb) / se if se > 0 else None)


def evaluate(orders_json, watch_jsonl, since=SINCE, now=None):
    now = now or dt.datetime.now(_t(since).tzinfo)
    series = index_series(watch_jsonl)
    s = _t(since)
    neg, pos, unknown = [], [], 0
    for x in rebuy_review.round_trips(orders_json):
        if _t(x['open']) < s or x['cost'] <= 0:
            continue
        ix = index_before(series, _t(x['open']))
        if ix is None:
            unknown += 1
            continue
        (neg if ix <= 0 else pos).append(x['pnl'] / x['cost'] * 100)
    days = (now - s).total_seconds() / 86400
    diff, t = welch(neg, pos) if neg and pos else (0.0, None)
    enough = len(neg) >= MIN_GROUP and len(pos) >= MIN_GROUP and days >= MIN_DAYS
    if not enough and days < MAX_DAYS:
        state = 'collecting'
    elif t is not None and diff <= -BAD_DIFF and t <= -2:
        state = 'restore'
    elif diff >= -KEEP_DIFF:
        state = 'keep_off'
    else:
        state = 'extend'
    f = lambda v: round(st.mean(v), 3) if v else None
    wr = lambda v: round(sum(1 for z in v if z > 0) / len(v) * 100) if v else None
    return {'version': REVIEW_VERSION, 'since': since, 'days': round(days, 2), 'neg_n': len(neg), 'pos_n': len(pos), 'unknown': unknown,
            'neg_avg': f(neg), 'pos_avg': f(pos), 'neg_win': wr(neg), 'pos_win': wr(pos), 'diff': round(diff, 3),
            't': None if t is None else round(t, 2), 'state': state, 'short': not enough}


def notice_text(r):
    """(제목, 본문, 수준). 아직 모으는 중이면 None."""
    if r['state'] == 'collecting':
        return None
    head = (f"음수장 진입 {r['neg_n']}건 평균 {r['neg_avg']}% 승률 {r['neg_win']}% / 양수장 진입 {r['pos_n']}건 평균 {r['pos_avg']}% 승률 {r['pos_win']}% "
            f"· 차이 {r['diff']:+.2f}%p (t {r['t']}) · {r['days']}일")
    if r['short']:
        head += f" · {MAX_DAYS}일이 지났지만 표본이 모자라 참고용"
    todo = {
        'keep_off': '할 일: 필터를 끈 채로 둡니다. 음수장 진입이 양수장보다 나쁘다는 근거가 없습니다. 따로 바꿀 것은 없습니다.',
        'restore': f'할 일: 음수장 진입이 양수장보다 {BAD_DIFF}%p 이상 나쁘고 통계적으로도 뚜렷합니다. Claude 새 대화에서 "장세 필터 연구 결과 보고"라고 말해 거래별 내역을 보고, 필터를 24시간으로 다시 켤지 정하세요.',
        'extend': '할 일: 차이가 애매합니다. 설정은 그대로 두고 표본이 더 쌓일 때까지 기다립니다.',
    }[r['state']]
    body = f"{head}\n{todo}\n설정 변경은 사용자가 승인한 뒤에만 합니다(이 알림은 주문·설정을 바꾸지 않습니다)."
    return ('장세 필터 해제 연구 결과가 나왔습니다', body, 'action' if r['state'] == 'restore' else 'info')
