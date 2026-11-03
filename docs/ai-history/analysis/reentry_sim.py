"""일부 익절·재진입 비교. exit_sim의 자료·규칙을 재사용한다."""
import exit_sim as X

DIP = 0.03       # 익절가보다 3% 낮아지면 재진입
WAIT = 180       # 익절 후 3시간 안에만


def legs(path, E, rule, reentry):
    """청산 후 조건이 되면 다시 사는 연속 거래. 같은 원금을 굴린 합산 수익률."""
    total, i, n, entry = 0.0, 0, 0, E
    while i < len(path) and n < 6:
        r, held, why = X.simulate(path[i:], entry, rule)
        total += r
        n += 1
        i += max(held, 1)
        if not reentry or why == '손절' or r <= 0 or i >= len(path):
            break
        exit_px = path[min(i, len(path) - 1)]
        j = next((k for k in range(i, min(i + WAIT, len(path))) if path[k] <= exit_px * (1 - DIP)), None)
        if j is None:
            break
        entry, i = path[j], j + 1
    return total, n


def partial_rebuy(path, E, wide, frac=1/3, tp=.045, dip=.03):
    """+tp에서 frac만 팔고 나머지는 넓은 규칙. 판 부분은 판 가격보다 dip 낮아지면 다시 사서 나머지와 같이 청산."""
    r_wide, held, _ = X.simulate(path, E, wide)
    k = next((i for i, px in enumerate(path[:held]) if px >= E * (1 + tp)), None)
    if k is None:
        return r_wide, r_wide
    sold = path[k]
    exit_px = path[min(held, len(path)) - 1]
    part_only = frac * (sold / E - 1 - X.COST) + (1 - frac) * r_wide
    j = next((i for i in range(k + 1, held) if path[i] <= sold * (1 - dip)), None)
    if j is None:
        return part_only, part_only
    rebuy = frac * ((sold / E - 1 - X.COST) + (exit_px / path[j] - 1 - X.COST))
    return part_only, rebuy + (1 - frac) * r_wide


def main():
    data = {c: X.mins('KRW-' + c) for c in X.COINS}
    out = {}
    for c, bars in data.items():
        if len(bars) < 900:
            continue
        closes = [b['c'] for b in bars]
        for i in range(240, len(bars) - 60, 60):
            t = bars[i]['t']
            a15, a60 = X.atr_at(bars[:i + 1], t + 60, 15), X.atr_at(bars[:i + 1], t + 60, 60)
            if not a15 or not a60:
                continue
            E, path = closes[i], closes[i + 1:i + 1 + X.HOLD]
            wide = dict(R=lambda E: 3 * a60, lock=1, trail_start=2, trail=lambda E, pk: 2 * a60, giveback=.5)
            tight = dict(R=lambda E: .03 * E, lock=1, trail_start=1, trail=lambda E, pk: .02 * pk)
            p_only, p_rebuy = partial_rebuy(path, E, wide)
            rows = {
                '현재(넓게 보유)': X.simulate(path, E, wide)[0],
                'C: 빨리 매도(+3%부터 −2%)': X.simulate(path, E, tight)[0],
                'C + 3% 조정 재진입(3시간 안)': legs(path, E, tight, True)[0],
                'F: +4.5%에 1/3 익절, 나머지 넓게': p_only,
                'F + 판 1/3을 3% 조정에 재매수': p_rebuy,
            }
            for k, v in rows.items():
                out.setdefault(k, []).append((c, v))
    n = len(next(iter(out.values())))
    print(f'진입 {n}건 · 코인 {len(data)}개 · 최대 12시간 · 비용 포함\n')
    print(f"{'규칙':<34}{'전체':>8}{'MPLX':>8}{'WAVES':>8}{'그 외':>8}  승률")
    for k, rows in out.items():
        avg = lambda xs: sum(xs) / len(xs) * 100 if xs else 0
        al = [v for _, v in rows]
        print(f"{k:<34}{avg(al):>+7.2f}%{avg([v for c, v in rows if c == 'MPLX']):>+7.2f}%"
              f"{avg([v for c, v in rows if c == 'WAVES']):>+7.2f}%{avg([v for c, v in rows if c not in ('MPLX', 'WAVES')]):>+7.2f}%"
              f"  {sum(v > 0 for v in al) / len(al):.0%}")


if __name__ == '__main__':
    main()
