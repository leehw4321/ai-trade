import statistics as S
import sim
from sim import T, C, coins, rs, summ

# 시장 지표: 30종목 18h 상승률의 중앙값(breadth)과 균등지수 20선(96봉) 위 여부
def breadth(i):
    v = [rs(c, i) for c in coins]
    v = [x for x in v if x is not None]
    return S.median(v) if v else None

def idx(i):
    v = []
    for c in coins:
        a, b = C[c][i], C[c][i - 96] if i >= 96 else None
        if a and b: v.append(a / b - 1)
    return S.mean(v) if v else None

mid = (96 + len(T)) // 2
BR = {i: breadth(i) for i in range(96, len(T))}
IX = {i: idx(i) for i in range(96, len(T))}

orig_rs = sim.rs
def run_with(filter_fn, **kw):
    # 필터가 거짓이면 신규 진입만 막는다(보유 관리는 그대로): rs를 None으로 만들어 후보에서 제외하는 대신 cap을 이용
    import types
    trades = []
    base_run = sim.run
    # 간단히: 진입 금지 구간에서 모든 코인의 rs<=0 처럼 보이도록 하지 않고, run 내부를 복제하지 않기 위해 cap=0 트릭 사용
    return None

# run을 복제 없이 확장: sim.run에 entry_gate 콜백을 주입
import inspect, textwrap
src = inspect.getsource(sim.run)
src = src.replace('plan_every=4):', 'plan_every=4, gate=None):')
src = src.replace('            if cap is not None and r[c] >= cap:', '            if gate is not None and not gate(i):\n                continue\n            if cap is not None and r[c] >= cap:')
ns = dict(sim.__dict__)
exec(src, ns)
run2 = ns['run']

def show(name, **kw):
    a = run2(arm=.03, stop_x=.045, **kw)
    f = [t for t in a if t['i'] < mid]; s = [t for t in a if t['i'] >= mid]
    print(f'{name:28s}', summ(a)); print(' ' * 28, '전반', summ(f)); print(' ' * 28, '후반', summ(s))

show('기준(필터없음)')
show('breadth>0 일 때만', gate=lambda i: (BR.get(i) or -1) > 0)
show('breadth>+1% 일 때만', gate=lambda i: (BR.get(i) or -1) > 0.01)
show('균등지수 96봉 상승일 때만', gate=lambda i: (IX.get(i) or -1) > 0)
for sl in (1, 2):
    show(f'슬롯 {sl}', slots=sl)
show('슬롯2+breadth>0', slots=2, gate=lambda i: (BR.get(i) or -1) > 0)
print('\n시장 상태 분포(breadth>0 비율):', sum(1 for i in BR if (BR[i] or -1) > 0) / len(BR))
