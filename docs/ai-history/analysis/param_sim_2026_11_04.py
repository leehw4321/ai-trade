import json
import statistics as S
import sys
from pathlib import Path

D = Path(r'C:\Users\User\AppData\Local\Temp\claude\c--Users-User-Desktop--------\3a83eec9-0744-4cc0-adb5-73dd529a2f9a\scratchpad\c15.json')
raw = json.loads(D.read_text(encoding='utf-8'))
T = sorted({t for m in raw.values() for t in m})
# 거의 모든 종목이 가진 시각만 사용
T = [t for t in T if sum(t in m for m in raw.values()) >= 25]
IDX = {t: i for i, t in enumerate(T)}
coins = list(raw)
O = {c: [None] * len(T) for c in coins}
H, L, C = ({c: [None] * len(T) for c in coins} for _ in range(3))
for c, m in raw.items():
    for t, (o, h, l, cl) in m.items():
        if t in IDX:
            i = IDX[t]; O[c][i], H[c][i], L[c][i], C[c][i] = o, h, l, cl
COST = 0.002  # 왕복 수수료+미끄러짐 가정


def rs(c, i):
    a, b = C[c][i], C[c][i - 72]
    return a / b - 1 if a and b else None


def run(cap=0.15, stop_x=0.045, arm=0.02, give=0.4, hold_rank=5, scan=8, slots=3, cool_loss=24, cool_win=8, start=96, plan_every=4):
    pos = {}
    cooldown = {}
    trades = []
    for i in range(start, len(T)):
        r = {c: rs(c, i) for c in coins}
        ranked = [c for c in sorted((c for c in coins if r[c] is not None), key=lambda c: -r[c])]
        rank = {c: k + 1 for k, c in enumerate(ranked)}
        for c in list(pos):
            p = pos[c]
            if H[c][i] is None:
                continue
            exit_px, why = None, ''
            if p['stop'] is not None and L[c][i] <= p['stop']:
                exit_px = min(p['stop'], O[c][i]); why = 'stop'
            if exit_px is None:
                p['peak'] = max(p['peak'], H[c][i])
                if give is not None and p['peak'] / p['entry'] - 1 >= arm:
                    p['stop'] = max(p['stop'], p['entry'] + (1 - give) * (p['peak'] - p['entry']))
                    p['guard'] = True
                    if L[c][i] <= p['stop']:  # 비관적 가정: 같은 봉에서 고점 뒤 저점이 왔다고 본다
                        exit_px = max(p['stop'], L[c][i]); why = 'stop'
            if exit_px is None and i % plan_every == 0:
                if r[c] is not None and (rank[c] > hold_rank or r[c] < 0):
                    exit_px = C[c][i]; why = 'rank'
            if exit_px is not None:
                ret = exit_px / p['entry'] - 1 - COST
                trades.append(dict(c=c, ret=ret, why=why + ('+guard' if why == 'stop' and p.get('guard') else ''), held=i - p['i'], i=i, entry=p['entry'], px=exit_px))
                cooldown[c] = i + (cool_win if ret > 0 else cool_loss)
                del pos[c]
        free = slots - len(pos)
        for c in ranked[:scan]:
            if free <= 0 or i % plan_every:
                break
            if c in pos or r[c] is None or r[c] <= 0 or C[c][i] is None or cooldown.get(c, -1) > i:
                continue
            if cap is not None and r[c] >= cap:
                continue
            pos[c] = dict(entry=C[c][i], stop=C[c][i] * (1 - stop_x), peak=C[c][i], i=i)
            free -= 1
    for c, p in pos.items():
        last = next((C[c][j] for j in range(len(T) - 1, -1, -1) if C[c][j]), p['entry'])
        trades.append(dict(c=c, ret=last / p['entry'] - 1 - COST, why='open', held=len(T) - p['i'], i=len(T), entry=p['entry'], px=last))
    return trades


def summ(tr):
    if not tr:
        return 'n=0'
    rets = [t['ret'] * 100 for t in tr]
    w = [x for x in rets if x > 0]; l = [x for x in rets if x <= 0]
    return (f"n={len(tr):3d} 합={sum(rets):+7.1f}% 평균={S.mean(rets):+.2f}% 중앙={S.median(rets):+.2f}% 승률={len(w)/len(rets)*100:3.0f}% "
            f"평균이익={S.mean(w) if w else 0:+.2f} 평균손실={S.mean(l) if l else 0:+.2f} 보유={S.mean(t['held'] for t in tr)/4:.1f}h")


if __name__ == '__main__':
    print('bars', len(T), 'coins', len(coins), T[0], '->', T[-1])
    print('\n[기준] 현재 규칙(cap15% 손절4.5% 가드40%)')
    base = run()
    print(summ(base))
    for why in ('stop', 'stop+guard', 'rank', 'open'):
        print('  ', why, summ([t for t in base if t['why'] == why]))
