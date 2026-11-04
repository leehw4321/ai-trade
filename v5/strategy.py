"""C안: 상대강도 순환 (AI 토론 합의 15·16·18·27번). 판단 봉 길이는 tf(분)로 정한다.

모두 순수 함수다. 시세 조회·장부·시간 처리는 bot.py와 paper.py가 맡는다.
"""
from dataclasses import dataclass, field
import math
import rotation_policy as rotation

MAJORS = ('KRW-BTC', 'KRW-ETH')


@dataclass
class Params:
    tf: int = 15                # 판단 봉 길이(분). 사용자 의견으로 짧은 주기 적용
    stop_seconds: int = 30      # 실시간 손절 확인 간격(초)
    L: int = 72                 # 15분봉 72개 = 18시간. 기존 4시간봉 연구와 다른 미검증 설정
    M: int = 5                  # 유지 순위. 후보 {5,8}, 임시값
    k: float = 3.0              # 손절 ATR 배수. 후보 {2,3}, 임시값
    slots: int = 3              # 최대 보유 종목
    entry_top: int = 3          # 빈자리는 상위 3위 안에서만 충원
    universe: int = 30          # 24시간 거래대금 상위 N
    risk: float = 0.002         # 거래당 계획 위험 (전략 순자산 대비)
    coin_cap: float = 0.25      # 종목당 상한
    total_cap: float = 0.50     # 총투입 상한
    min_order: float = 5000.0
    fee: float = 0.0005         # 편도 수수료
    cost_major: float = 0.0010  # 편도 가격 비용 c (BTC·ETH), 29번 제안값
    cost_other: float = 0.0020  # 편도 가격 비용 c (기타)
    reentry_hours: float = 24.0
    require_positive_rs: bool = True  # 임시: 상대강도 0 이하 종목은 신규 진입하지 않음
    # 과열 추격 방지와 손절 상한 (결정 D-12, 사용자 요청 21:50). 0이면 끔. 값은 탐색 분석에 근거한 미검증 가설이다.
    chase_rs_cap: float = 0.15    # 기간 상승률이 이 값 이상이면 신규 매수 보류
    stop_max_pct: float = 0.08    # 손절가는 진입가(또는 인수 시점 가격)보다 이 비율 이상 멀리 두지 않음
    # 매수 후보 규칙(결정 D-42): 0이면 현재 동작(상위 entry_top 칸만 본다). entry_scan>entry_top이면 막힌 자리를 다음 순위 적격 종목으로 채운다.
    entry_risk: float = 0.0   # 실거래 매수 위험(계좌 대비). 0이면 risk 사용. 모의 장부의 risk는 건드리지 않는다(D-47)
    size_by_risk: bool = False  # True면 1회 매수 금액 ≤ risk×계좌 ÷ 최대 손절폭(stop_max_pct): 손절 시 손실을 계좌의 risk로 제한(D-45)
    entry_want: int = 0    # 한 번에 사려는 후보 수 (0 = entry_top)
    entry_scan: int = 0    # 후보를 찾아보는 최대 순위 (0 = entry_top)
    # 실거래 재진입 규칙(결정 D-38): 직전 매도 뒤 대기 시간과 조건. 0이면 해당 대기 없음.
    reentry_profit_hours: float = 2.0    # 수익으로 청산한 종목은 이 시간 뒤부터, 단 아래 눌림 조건 충족 시
    reentry_loss_hours: float = 6.0      # 손실로 청산한 종목은 이 시간 뒤부터
    reentry_dip_atr: float = 1.0         # 수익 청산 후 재진입: 현재가가 매도가보다 이 ATR 배수 이상 낮아야 함
    reentry_window_hours: float = 24.0   # 이 시간이 지나면 일반 매수 조건만 본다
    # 익절·추적 손절 EXIT-T1 (토론 근거 3, 합의 전까지 꺼짐). 모의·그림자 전용이며 live.py에는 연결하지 않는다.
    trail_enabled: bool = False
    trail_lock_r: float = 1.0     # 이 R배수 이익 도달 시 손절가를 본전(비용 포함)으로
    trail_start_r: float = 2.0    # 이 R배수부터 추적·반납 상한 적용
    trail_m: float = 2.0          # 추적 손절 = 고점 − m×ATR
    giveback: float = 0.5         # 고점 이익 중 이 비율 이상 반납하면 청산 (0이면 끔)
    # 장세 필터(2026-11-04 사용자 지시·ChatGPT 합의 요청): 켜면 시장 균등지수가 상승일 때만 신규 매수한다. 매도·손절·이익보호는 그대로.
    regime_filter: bool = False
    regime_bars: int = 96         # 균등지수 기간(15분봉 96개 = 24시간)
    regime_min_markets: int = 20  # 지수 계산에 필요한 최소 종목 수. 모자라면 신규 매수 보류
    halt_new_buys: bool = False  # preserve latest other-session temporary buy halt
    regime_expires: float = 0.0   # 0이 아니면 이 에포크 시각 이후 신규 매수를 전부 보류한다(자동으로 필터 없는 거래로 돌아가지 않는다). 재결정 필요
    rotation_enabled: bool = False  # reviewed hourly rotation; opt-in
    rotation_confirmations: int = 1  # 2 is research-only, not the rollout value
    exclude: list = field(default_factory=lambda: ['KRW-USDT', 'KRW-USDC', 'KRW-USDE', 'KRW-USD1', 'KRW-DAI'])

    @classmethod
    def from_dict(cls, d):
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def cost(market, p):
    return p.cost_major if market in MAJORS else p.cost_other


def wilder_atr(bars, n=14):
    """Wilder ATR: 첫 n개 TR 평균으로 시작해 (이전×(n−1)+TR)/n."""
    trs = [max(b['h'] - b['l'], abs(b['h'] - a['c']), abs(b['l'] - a['c'])) for a, b in zip(bars, bars[1:])]
    if len(trs) < n:
        return None
    atr = sum(trs[:n]) / n
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    return atr


def relative_strength(bars, L):
    if len(bars) <= L or bars[-1 - L]['c'] <= 0:
        return None
    return bars[-1]['c'] / bars[-1 - L]['c'] - 1


def market_index(bars_by_market, n, min_markets=20):
    """종목들의 최근 n개 봉 상승률의 단순 평균(균등지수 수익률). 자료가 부족·불일치하면 None.

    비정상 값(NaN·무한대·0 이하)은 제외한다. 대다수 종목과 마지막 봉 시각 또는 n봉 전 봉 시각이 다른 종목(결측·정지)도 제외한다.
    """
    if type(n) is not int or n < 1 or type(min_markets) is not int or min_markets < 1:
        raise ValueError('regime_bars와 regime_min_markets는 1 이상의 정수여야 합니다')
    rows = []
    for bars in bars_by_market.values():
        if len(bars) <= n:
            continue
        try:
            last, first = float(bars[-1]['c']), float(bars[-1 - n]['c'])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(last) and math.isfinite(first)) or last <= 0 or first <= 0:
            continue
        rows.append(((bars[-1].get('t'), bars[-1 - n].get('t')), last / first - 1))
    if not rows:
        return None
    counts = {}
    for key, _ in rows:
        counts[key] = counts.get(key, 0) + 1
    common = max(counts, key=counts.get)
    values = [r for key, r in rows if key == common]
    return sum(values) / len(values) if len(values) >= min_markets else None


def rank(bars_by_market, p):
    """적격 종목을 상대강도 내림차순(동률은 코드순)으로 정렬. 자료 부족 종목은 제외."""
    rows = []
    for m, bars in bars_by_market.items():
        rs, atr = relative_strength(bars, p.L), wilder_atr(bars)
        if rs is None or atr is None:
            continue
        rows.append({'market': m, 'rs': rs, 'atr': atr, 'close': bars[-1]['c'], 'bar': bars[-1]['t']})
    rows.sort(key=lambda r: (-r['rs'], r['market']))
    for i, r in enumerate(rows, 1):
        r['rank'] = i
    return rows


def chase_block(r, p):
    """과열 추격 매수 보류 사유. 후보 r(rs 포함)가 기간 상승률 상한 이상이면 문자열, 아니면 None."""
    if p.chase_rs_cap > 0 and r['rs'] >= p.chase_rs_cap:
        return f'기간 상승 {r["rs"]:+.1%} ≥ 과열 상한 {p.chase_rs_cap:.0%}'
    return None


def cap_stop(entry, stop, p):
    """손절가가 기준가에서 stop_max_pct보다 멀면 그 거리로 당긴다(올리기만 함)."""
    if p.stop_max_pct > 0:
        return max(stop, entry * (1 - p.stop_max_pct))
    return stop


def reentry_block(r, last, now, p):
    """직전 매도 기록 last={'time','price','profit'}가 있으면 재진입을 더 까다롭게 본다. 막으면 사유 문자열, 아니면 None.
    수익 청산: reentry_profit_hours 뒤부터, 그리고 reentry_window_hours 안에서는 현재가가 매도가보다 reentry_dip_atr×ATR 이상 낮을 때만(고점에서 팔고 눌리면 다시 잡기).
    손실 청산: reentry_loss_hours 뒤부터."""
    if not last:
        return None
    if rotation.enabled(p):
        why = rotation.fresh_reentry(last, p, rotation.row_close(r, p.tf))
        if why: return why
    hours = (now - last['time']) / 3600
    if hours >= p.reentry_window_hours:
        return None
    if last.get('profit'):
        if hours < p.reentry_profit_hours:
            return f'수익 청산 후 재진입 대기 {hours:.1f}/{p.reentry_profit_hours:g}시간'
        atr_pct = r.get('atr', 0) / r['close'] if r.get('close') else 0
        limit = last['price'] * (1 - p.reentry_dip_atr * atr_pct)
        if p.reentry_dip_atr > 0 and r.get('close', 0) > limit:
            return f'수익 청산 후 재진입은 매도가 {last["price"]:,.4g}보다 {p.reentry_dip_atr:g}ATR 이상 눌려야 함(현재 {r["close"]:,.4g} > {limit:,.4g})'
        return None
    if hours < p.reentry_loss_hours:
        return f'손실 청산 후 재진입 대기 {hours:.1f}/{p.reentry_loss_hours:g}시간'
    return None


def plan(ranked, held, banned, entry_ok, p):
    """15번 교체 규칙. held: 보유 종목, banned: 재진입 금지, entry_ok: 신규 진입 가능한 종목 집합.

    반환: (매도 [(종목, 사유)], 매수 후보 [종목], 판단 보류 [종목])
    """
    pos = {r['market']: r['rank'] for r in ranked}
    sells, unknown = [], []
    for m in held:
        if m not in pos:
            unknown.append(m)          # 자료 부족: 순위로 판단하지 않고 유지 (누락 처리 17번 미합의)
        elif pos[m] > p.M:
            sells.append((m, '순환'))
    keep = [m for m in held if m not in {s[0] for s in sells}]
    free = p.slots - len(keep)
    buys = []
    scan = min(p.entry_scan or p.entry_top, p.M) if rotation.enabled(p) else p.entry_top
    want = p.entry_want or p.entry_top
    for r in ranked[:scan]:
        if rotation.enabled(p) and (type(r.get('rank')) is not int or not 1 <= r['rank'] <= p.M):
            continue
        if len(buys) >= min(free, want):
            break
        m = r['market']
        if m in held or m in banned or m not in entry_ok:
            continue
        if p.require_positive_rs and r['rs'] <= 0:
            continue
        if chase_block(r, p):
            continue
        buys.append(m)
    return sells, buys, unknown


def size(equity, invested, cash, P, S, p):
    """16·27번 수량: min(계획위험/(P−S), 종목 상한, 잔여 총투입, 수수료 포함 가용현금). 최소주문 미달은 0."""
    if S <= 0 or P <= S:
        return 0.0
    qty = min(p.risk * equity / (P - S),
              p.coin_cap * equity / P,
              max(0.0, p.total_cap * equity - invested) / P,
              cash / (P * (1 + p.fee)))
    qty = math.floor(max(qty, 0.0) * 1e8) / 1e8
    return qty if qty * P >= p.min_order else 0.0


def ratchet(market, pos, price, p):
    """EXIT-T1. 고점·손절가를 갱신하고 청산 사유('수익 반납'|'추적 손절'|'손절')를 반환한다. 손절가는 내려가지 않는다.

    pos: entry, stop, atr 필요. 갱신값 peak, stop0(초기 손절가), stop을 pos에 기록한다.
    trail_enabled=False이면 기존 규칙(고정 손절)과 동일하다.
    """
    pos.setdefault('stop0', pos['stop'])
    if not p.trail_enabled:
        return '손절' if price <= pos['stop'] else None
    E, A = pos['entry'], (pos.get('atr_r') or pos['atr'])  # atr_r: 설계대로 15분 ATR로 정의한 R(live.r_from_15m), 없으면 기존 atr
    R = p.k * A
    peak = max(pos.get('peak', E), price)
    pos['peak'] = peak
    if R > 0:
        g = (peak - E) / R
        stop = pos['stop']
        if g >= p.trail_lock_r:
            stop = max(stop, E * (1 + 2 * p.fee + cost(market, p)))
        if g >= p.trail_start_r:
            stop = max(stop, peak - p.trail_m * A)
        pos['stop'] = stop
        if p.giveback > 0 and g >= p.trail_start_r and peak > E and (peak - price) / (peak - E) >= p.giveback:
            return '수익 반납'
    if price <= pos['stop']:
        return '추적 손절' if pos['stop'] > pos['stop0'] else '손절'
    return None


def bar_after_entry(bar, entry_iso):
    """1분봉이 진입 시각 이후에 마감됐는지. 체결 이전·같은 봉 정보를 쓰지 않기 위한 검사."""
    import datetime as dt
    end = dt.datetime.fromisoformat(bar['t']) + dt.timedelta(minutes=1)
    return end > dt.datetime.fromisoformat(entry_iso)
