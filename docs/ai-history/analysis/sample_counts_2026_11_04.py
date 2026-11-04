"""표본 규모 가늠(설명용): 수익 성과가 아니라 '라벨 빈도'와 '패턴 발생 횟수'만 센다. 전략 성과·진입 규칙 선택에는 쓰지 않는다."""
import sys
from collections import defaultdict
from statistics import median

sys.path.insert(0, r'C:\Users\User\Desktop\ai-trade\tools\research')
import pit_universe as pit

CUT = pit.to_epoch('2026-11-04T08:15:00')
data = pit.load_dir(r'C:\Users\User\Desktop\research_data\candles15', cutoff=CUT)
B = pit.BAR
allmin = min(min(b) for b in data.values())
start = (allmin + 100 * B) // 3600 * 3600 + 3600
H = 16  # 4시간
COST = 0.003

labels = {0.01: 0, 0.02: 0, 0.03: 0}
n_events = 0
days = defaultdict(int)
per_market = defaultdict(int)
scalp = 0
scalp_days = set()
last_scalp = {}
T = start
while T < CUT - (H + 2) * B:
    uni = pit.universe_at(data, T, top_n=30)
    for m, _ in uni:
        bars = data[m]
        s0 = T - B
        b0 = bars.get(s0)
        bf = bars.get(s0 + H * B)
        if b0 is None or bf is None:
            continue
        r = bf[3] / b0[3] - 1 - COST
        n_events += 1
        days[T // 86400] += 1
        per_market[m] += 1
        for k in labels:
            if r >= k:
                labels[k] += 1
        # 단타 패턴 후보 정의(설계 문서): 1h 수익률>0 & 최근 완료 15분봉 거래대금 / 직전 20봉 거래대금 중앙값 >= 2 (정시 슬롯에서만 평가)
        b1 = bars.get(s0 - 4 * B)
        prev = [bars[s][4] for s in range(s0 - 20 * B, s0, B) if s in bars]
        if b1 and len(prev) >= 15 and median(prev) > 0 and b0[3] / b1[3] - 1 > 0 and b0[4] / median(prev) >= 2:
            if T - last_scalp.get(m, 0) >= 4 * 3600:  # 같은 종목은 4시간에 한 번만 세어 중복(겹침) 제거
                scalp += 1
                scalp_days.add(T // 86400)
                last_scalp[m] = T
    T += 6 * 3600  # 6시간 간격 표집(겹치는 라벨 줄이기)
print('표집 시점당 상위30 x 6시간 간격 이벤트 수:', n_events, '| 이벤트가 있는 날 수:', len(days), '| 종목 수:', len(per_market))
for k, v in labels.items():
    print(f'  4시간 뒤 비용 0.3% 차감 후 수익률 >= {k:.0%} 인 이벤트: {v} ({v / n_events:.1%})')
print('단타 후보 패턴(1h 상승 + 거래대금 2배, 종목당 4시간 중복 제거) 발생(6시간 표집 기준):', scalp, '| 발생한 날 수:', len(scalp_days))
