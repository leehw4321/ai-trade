"""이익 실현 규칙 비교 (매시간 같은 시점 진입, 최대 12시간 보유, 1분봉 종가 평가).

진입 규칙은 고정하고 청산 규칙만 바꾼다. 비용: 왕복 수수료 0.1% + 가격 비용 0.2%(기타 코인 편도 0.1%×2).
"""
import datetime as dt
import json
import sys
import time

sys.path.insert(0, r'C:\Users\User\Desktop\코인 트레이딩\v5-app\v5')
import upbit
import strategy as st

COINS = ['MPLX', 'WAVES', 'IRYS', 'DKA', 'MEGA', 'TRUST', 'SLX', 'POD', 'CAP', 'AXIS', 'BIRB', 'BEAM', 'SOPH', 'G', 'TRUMP']
COST = 0.003
HOLD = 720


def mins(m, n=1500):
    out, to = [], None
    while len(out) < n:
        params = {'market': m, 'count': 200}
        if to:
            params['to'] = to
        rows = upbit._get('/v1/candles/minutes/1', params)
        if not rows:
            break
        out += rows
        to = rows[-1]['candle_date_time_utc'] + 'Z'
        time.sleep(0.12)
    return [{'t': dt.datetime.fromisoformat(r['candle_date_time_utc']).timestamp(), 'h': r['high_price'],
             'l': r['low_price'], 'c': r['trade_price']} for r in reversed(out)]


def atr_at(bars, t, minutes):
    """t 이전에 끝난 봉들로 Wilder ATR14 (분봉을 묶어 만든 봉)."""
    agg, cur = [], None
    for b in bars:
        if b['t'] + 60 > t:
            break
        k = int(b['t'] // (minutes * 60))
        if cur is None or cur['k'] != k:
            cur = {'k': k, 'h': b['h'], 'l': b['l'], 'c': b['c']}
            agg.append(cur)
        else:
            cur['h'] = max(cur['h'], b['h']); cur['l'] = min(cur['l'], b['l']); cur['c'] = b['c']
    return st.wilder_atr(agg[:-1]) if len(agg) > 16 else None


def simulate(path, E, rule):
    """path: 진입 후 1분봉 종가 목록. 반환: (순수익률, 보유분, 사유)."""
    stop = E * (1 - 0.045)
    peak, part, part_px = E, 0.0, 0.0
    for i, px in enumerate(path):
        peak = max(peak, px)
        if rule.get('tp') and px >= E * (1 + rule['tp']):
            return finish(E, px, part, part_px), i, '익절'
        R = rule['R'](E)
        g = (peak - E) / R
        if g >= rule['lock']:
            stop = max(stop, E * 1.003)
        if g >= rule['trail_start']:
            stop = max(stop, peak - rule['trail'](E, peak))
            if rule.get('giveback') and (peak - px) / (peak - E) >= rule['giveback']:
                return finish(E, px, part, part_px), i, '반납'
        if rule.get('partial') and not part and g >= rule['partial']:
            part, part_px = rule.get('partial_frac', 1 / 3), px
        if px <= stop:
            return finish(E, px, part, part_px), i, '추적' if stop > E * 0.955 + 1e-12 else '손절'
    return finish(E, path[-1], part, part_px), len(path), '시간'


def finish(E, px, part, part_px):
    gross = part * (part_px / E - 1) + (1 - part) * (px / E - 1)
    return gross - COST


def main():
    data = {}
    for c in COINS:
        try:
            data[c] = mins('KRW-' + c)
        except Exception as ex:
            print('skip', c, ex)
    res = {}
    for c, bars in data.items():
        if len(bars) < 900:
            continue
        closes = [b['c'] for b in bars]
        for i in range(240, len(bars) - 60, 60):
            t = bars[i]['t']
            a15 = atr_at(bars[:i + 1], t + 60, 15)
            a60 = atr_at(bars[:i + 1], t + 60, 60)
            if not a15 or not a60:
                continue
            E = closes[i] * 1.0
            path = closes[i + 1:i + 1 + HOLD]
            rules = {
                '현재(1R=3×1h ATR 근사, 고점−2ATR, 반납50%)': dict(R=lambda E: 3 * a60, lock=1, trail_start=2,
                                                       trail=lambda E, pk: 2 * a60, giveback=.5, partial=2),
                'A: 1R=4.5%(실제 손실폭), 고점−2×15분ATR': dict(R=lambda E: .045 * E, lock=1, trail_start=1,
                                                       trail=lambda E, pk: 2 * a15, giveback=.5),
                'B: +4.5%부터 고점 대비 −2.5%면 매도': dict(R=lambda E: .045 * E, lock=1, trail_start=1,
                                                   trail=lambda E, pk: .025 * pk),
                'C: +3%부터 고점 대비 −2%면 매도': dict(R=lambda E: .03 * E, lock=1, trail_start=1,
                                                trail=lambda E, pk: .02 * pk),
                'D: B + +4.5%에 1/2 먼저 익절': dict(R=lambda E: .045 * E, lock=1, trail_start=1,
                                              trail=lambda E, pk: .025 * pk, partial=1, partial_frac=.5),
                'E: 고정 익절 +6% / 손절 −4.5%': dict(R=lambda E: .045 * E, lock=99, trail_start=99,
                                                trail=lambda E, pk: 0, tp=.06),
            }
            for name, rule in rules.items():
                r, held, why = simulate(path, E, rule)
                res.setdefault(name, []).append((c, r, held, why))
    print(f'코인 {len(data)}개, 진입 {len(next(iter(res.values())))}건 (매시간, 최대 12시간 보유, 왕복 비용 0.3% 포함)\n')
    for name, rows in res.items():
        rs = [r for _, r, _, _ in rows]
        mp = [r for c, r, _, _ in rows if c == 'MPLX']
        ex = [r for c, r, _, _ in rows if c != 'MPLX']
        win = sum(r > 0 for r in rs) / len(rs)
        print(f"{name}\n   평균 {sum(rs)/len(rs)*100:+.2f}% · 승률 {win:.0%} · 평균 보유 {sum(h for *_, h, _ in rows)/len(rows):.0f}분"
              f" · MPLX만 {sum(mp)/max(1,len(mp))*100:+.2f}%({len(mp)}건) · MPLX 제외 {sum(ex)/max(1,len(ex))*100:+.2f}%")


if __name__ == '__main__':
    main()
