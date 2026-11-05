"""탐색 연구(2026-11-05 사용자 요청): 4칸 포트폴리오로 보유·교체 규칙 비교. 종목 하나씩이 아니라 '자리가 꽉 찼을 때'를 재현한다.

공통(지금 실거래와 같게): 매시간 판단 · 거래대금 상위 30 · 7위 안 · 0 < 18시간 상승률 < 15% · 장세 필터(24시간 균등지수 > 0) ·
  최대 4종목 · 1회 매수 1칸(봇 자금 38만 원 / 1회 8만 원 = 4.75칸에서 시작) · 손절 −4.5% · 이익보호 +3% 시작·30% 반납 ·
  비용 편도 0.425%(실측 왕복 0.85%). 15분봉 안에서는 손절 → 보호선 → 잠금 순으로 불리하게 먼저 본다.
비교:
  V0 순위 매도(예전: 9위 밖·상승률 음수·상위 30 밖이면 정시 매도)
  V1 순위 매도 없음(지금 실거래)
  V2 V1 + 라: +1.5% 넘으면 손절선을 본전(+0.5%, 비용 포함)으로
  V3~V5 V1 + 나: 묶임(예전 규칙이면 팔았을 상태)이 24/48/72시간 이어지고 이익 +3% 미만이면 정리
  V6 V1 + 다: 매수 후보가 있는데 자리가 없으면 묶임(손실 중) 중 상승률이 가장 약한 종목을 필요한 만큼 매도(남는 양이 0.125칸 미만이면 전량),
     자리는 금액(투입 원가 4칸)으로 센다. 정시 판단 1회당 1종목.
  V7 V6 + 라
  V8 순위 매도는 손실일 때만(R2)
탐색이며 결과를 보고 값을 바꿔 재실행하면 기록한다. 생존 편향(상장폐지 종목 없음)이 있다.
  python study_portfolio.py
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pit_universe as pit
import study_regime_exit as rx

B = pit.BAR
HOUR = 3600
DATA_DIR = Path(r'C:\Users\User\Desktop\research_data\candles15')
OUT = DATA_DIR.parent / 'study_portfolio' / 'result.json'
CUTOFF_KST = '2026-11-05T11:30:00'
FEE = 0.00425
START_CASH = 4.75
SLOTS = 4
STOP, ARM, GIVE = 0.045, 0.03, 0.30
LOCK_AT, LOCK_TO = 0.015, 0.005
STUCK_KEEP = 0.03
MIN_LEFT = 0.125

VARIANTS = {
    'V0 순위 매도(예전)': dict(rank='all'),
    'V1 순위 매도 없음(지금)': dict(rank=None),
    'V2 V1+라 본전잠금': dict(rank=None, lock=True),
    'V3 V1+나 24h': dict(rank=None, stuck_h=24),
    'V4 V1+나 48h': dict(rank=None, stuck_h=48),
    'V5 V1+나 72h': dict(rank=None, stuck_h=72),
    'V6 V1+다 부분교체': dict(rank=None, swap=True),
    'V7 V1+다+라': dict(rank=None, swap=True, lock=True),
    'V8 손실일 때만 순위 매도': dict(rank='loss'),
}


def run(data, state, t0, t1, cfg):
    cash, pos, log = START_CASH, {}, {'buys': 0, 'sells': {}, 'missed': 0, 'swaps': 0, 'curve': []}

    def sell(m, frac, px, why):
        nonlocal cash
        p = pos[m]
        q = p['qty'] * frac
        cash += q * px * (1 - FEE)
        p['qty'] -= q
        p['cost'] *= (1 - frac)
        log['sells'][why] = log['sells'].get(why, 0) + 1
        if p['qty'] <= 1e-12 or frac >= 1:
            del pos[m]

    T = t0
    while T < t1:
        # 지난 1시간의 15분봉으로 손절·보호선·잠금
        for s in range(T - HOUR, T, B):
            for m in list(pos):
                b = data[m].get(s)
                if b is None:
                    continue
                p = pos[m]
                e = p['entry']
                if b[2] <= p['stop']:
                    sell(m, 1, min(p['stop'], b[0]), 'stop' if p['stop'] < e else 'lock')
                    continue
                if p['peak'] >= e * (1 + ARM):
                    g = e + (p['peak'] - e) * (1 - GIVE)
                    if b[2] <= g:
                        sell(m, 1, min(g, b[0]), 'guard')
                        continue
                p['peak'] = max(p['peak'], b[1])
                if cfg.get('lock') and p['peak'] >= e * (1 + LOCK_AT):
                    p['stop'] = max(p['stop'], e * (1 + LOCK_TO))
        snap = state.get(T)
        price = {m: data[m][T - B][3] for m in pos if T - B in data[m]}
        if snap:
            ranks = snap[0]
            # 정시: 순위 매도·묶임 판정
            for m in list(pos):
                info, px = ranks.get(m), price.get(m)
                if px is None:
                    continue
                out = info is None or info[1] > 9 or info[0] < 0
                p = pos[m]
                p['stuck_since'] = (p.get('stuck_since') or T) if out else None
                loss = px < p['entry']
                if out and (cfg['rank'] == 'all' or (cfg['rank'] == 'loss' and loss)):
                    sell(m, 1, px, 'rank')
                elif out and cfg.get('stuck_h') and T - p['stuck_since'] >= cfg['stuck_h'] * HOUR and px < p['entry'] * (1 + STUCK_KEEP):
                    sell(m, 1, px, 'stuck_time')
            # 매수
            if snap[1] >= rx.MIN_UNIVERSE and snap[2] is not None and snap[2] > 0:
                for m, rs in rx.candidates(ranks, rx.SCAN):
                    if m in pos or T - B not in data[m]:
                        continue
                    px = data[m][T - B][3]
                    room = (SLOTS - sum(p['cost'] for p in pos.values())) if cfg.get('swap') else (SLOTS - len(pos))
                    if (room < 1 - 1e-9 or cash < 1) and cfg.get('swap') and not log.get('_swapped') == T:
                        need = max(1 - room, 1 - cash, 0) + 0.01
                        losers = [(ranks.get(k, (-9, 99))[0], k) for k, p in pos.items()
                                  if p.get('stuck_since') and k in price and price[k] < p['entry']]
                        if losers:
                            _, k = min(losers)
                            v = pos[k]['qty'] * price[k]
                            frac = min(1, need / max(v, 1e-9))
                            if v * (1 - frac) < MIN_LEFT:
                                frac = 1
                            sell(k, frac, price[k], 'swap')
                            log['swaps'] += 1
                            log['_swapped'] = T
                            room = (SLOTS - sum(p['cost'] for p in pos.values()))
                    if room < 1 - 1e-9 or cash < 1:
                        log['missed'] += 1
                        continue
                    cash -= 1
                    pos[m] = {'entry': px, 'qty': (1 - FEE) / px, 'cost': 1.0, 'peak': px, 'stop': px * (1 - STOP), 'stuck_since': None}
                    log['buys'] += 1
        if T % (24 * HOUR) == 0:
            val = cash + sum(p['qty'] * price.get(m, p['entry']) for m, p in pos.items())
            log['curve'].append((T, val))
        T += HOUR
    val = cash + sum(p['qty'] * data[m][max(k for k in data[m] if k < t1)][3] for m, p in pos.items())
    log.pop('_swapped', None)
    return val, log


def main():
    cut = pit.to_epoch(CUTOFF_KST)
    data = {m: b for m, b in pit.load_dir(DATA_DIR, cutoff=cut).items() if b}
    allmin = min(min(b) for b in data.values())
    t0 = math.ceil((allmin + 120 * B) / HOUR) * HOUR
    print(f'구간 {pit.from_epoch_kst(t0)} ~ {CUTOFF_KST} · 종목 {len(data)}개 · 시작 {START_CASH}칸', flush=True)
    state = rx.hourly_state(data, t0 - HOUR, cut)
    span = (cut - t0) // 3
    thirds = [(t0 + i * span, t0 + (i + 1) * span) for i in range(3)]
    res = {}
    print(f"\n{'정책':22} {'180일 수익':>9} {'구간1':>7} {'구간2':>7} {'구간3':>7} {'최대낙폭':>7} {'매수':>5} {'놓침':>5} {'교체':>4}  청산 사유")
    for name, cfg in VARIANTS.items():
        val, log = run(data, state, t0, cut, cfg)
        parts = [run(data, state, a, b, cfg)[0] / START_CASH - 1 for a, b in thirds]
        peak, mdd = START_CASH, 0.0
        for _, v in log['curve']:
            peak = max(peak, v)
            mdd = max(mdd, 1 - v / peak)
        ret = val / START_CASH - 1
        res[name] = {'ret': ret, 'thirds': parts, 'mdd': mdd, 'buys': log['buys'], 'missed': log['missed'], 'swaps': log['swaps'], 'sells': log['sells']}
        print(f"{name:22} {ret*100:+8.1f}% " + ' '.join(f'{x*100:+6.1f}%' for x in parts) + f" {mdd*100:6.1f}% {log['buys']:5} {log['missed']:5} {log['swaps']:4}  {log['sells']}", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    print('저장:', OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
