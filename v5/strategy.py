"""C안: 상대강도 순환 (AI 토론 합의 15·16·18·27번). 판단 봉 길이는 tf(분)로 정한다.

모두 순수 함수다. 시세 조회·장부·시간 처리는 bot.py와 paper.py가 맡는다.
"""
from dataclasses import dataclass, field
import math

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
    # 익절·추적 손절 EXIT-T1 (토론 근거 3, 합의 전까지 꺼짐). 모의·그림자 전용이며 live.py에는 연결하지 않는다.
    trail_enabled: bool = False
    trail_lock_r: float = 1.0     # 이 R배수 이익 도달 시 손절가를 본전(비용 포함)으로
    trail_start_r: float = 2.0    # 이 R배수부터 추적·반납 상한 적용
    trail_m: float = 2.0          # 추적 손절 = 고점 − m×ATR
    giveback: float = 0.5         # 고점 이익 중 이 비율 이상 반납하면 청산 (0이면 끔)
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
    for r in ranked[:p.entry_top]:
        if len(buys) >= free:
            break
        m = r['market']
        if m in held or m in banned or m not in entry_ok:
            continue
        if p.require_positive_rs and r['rs'] <= 0:
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
    E, A = pos['entry'], pos['atr']
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
