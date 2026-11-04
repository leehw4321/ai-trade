import hashlib
import json
import random
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, r'C:\Users\User\Desktop\ai-trade\tools\research')
import collect_candles as cc
import pit_universe as pit

D = Path(r'C:\Users\User\Desktop\research_data\candles15')
CUTOFF = pit.to_epoch('2026-11-04T08:15:00')  # 최초 종목 수집 시작(08:15:5x)보다 이른 15분 경계
print('cutoff', pit.from_epoch_kst(CUTOFF))

stats = {}
data = pit.load_dir(D, cutoff=CUTOFF, stats=stats)
print('markets', len(data), 'stats', stats)
allmin = min(min(b) for b in data.values())
allmax = max(max(b) for b in data.values())
print('range', pit.from_epoch_kst(allmin), '->', pit.from_epoch_kst(allmax + pit.BAR), 'days', round((allmax + pit.BAR - allmin) / 86400, 1))

# 공통 컷오프에서의 종목별 마지막 봉 편차(무거래 vs 수집 편차 분리)
lasts = {m: max(b) + pit.BAR for m, b in data.items()}
behind = sorted((CUTOFF - l) // 3600 for l in lasts.values() if l < CUTOFF)
print('markets whose last completed bar ends before cutoff:', len(behind), '| >1h:', sum(1 for h in behind if h >= 1), '| >24h:', sum(1 for h in behind if h >= 24))

# 시점별 적격·유니버스 안정성 (공통 컷오프 포함)
for label, T in (('start+30d', allmin + 30 * 86400), ('mid', (allmin + CUTOFF) // 2), ('cutoff', CUTOFF)):
    T = T // pit.BAR * pit.BAR
    elig = pit.universe_at(data, T, top_n=10_000)
    u = elig[:30]
    print(label, pit.from_epoch_kst(T), 'eligible', len(elig), 'top5', [m[4:] for m, _ in u[:5]])

# 인접 시점 유니버스 교체율(안정성)
T = CUTOFF - 7 * 86400
a = {m for m, _ in pit.universe_at(data, T, top_n=30)}
b = {m for m, _ in pit.universe_at(data, T + 24 * 3600 // pit.BAR * pit.BAR, top_n=30)}
print('top30 overlap one day apart:', len(a & b), '/30')

# 독립 재수집 대조: 표본 종목의 최근 3일을 새로 받아 저장 자료와 완결 봉 값을 비교
rng = random.Random(5)
markets = ['KRW-BTC', 'KRW-ETH', 'KRW-XRP'] + rng.sample(sorted(set(data) - {'KRW-BTC', 'KRW-ETH', 'KRW-XRP'}), 5)
since = datetime.now(timezone.utc) - timedelta(days=3)
mism = checked = 0
with tempfile.TemporaryDirectory() as tmp:
    for m in markets:
        cc.collect_market(m, Path(tmp), since)
    fresh = pit.load_dir(tmp, cutoff=CUTOFF)
    for m in markets:
        if m not in fresh:
            continue
        common = set(fresh[m]) & set(data.get(m, {}))
        for s in common:
            checked += 1
            if fresh[m][s] != data[m][s]:
                mism += 1
        print(m, 'common bars', len(common), 'mismatch', sum(1 for s in common if fresh[m][s] != data[m][s]),
              'fresh-only', len(set(fresh[m]) - set(data[m])), 'stored-only(in window)', len([s for s in data[m] if s >= min(fresh[m]) and s not in fresh[m]]))
print('independent re-collection: checked', checked, 'mismatches', mism)

# 데이터 지문
h = hashlib.sha256()
files = {}
for p in sorted(D.glob('KRW-*.jsonl')):
    fh = hashlib.sha256(p.read_bytes()).hexdigest()
    files[p.name] = fh
    h.update(p.name.encode() + fh.encode())
fp = {'created_kst': datetime.now(cc.KST).strftime('%Y-%m-%dT%H:%M:%S'), 'cutoff_kst': pit.from_epoch_kst(CUTOFF),
      'markets': len(files), 'overall_sha256': h.hexdigest(), 'load_stats': stats,
      'collector_note': '수집 시점 코드는 manifest 도입 전 판(종목별 fetched_at 없음). 공통 컷오프는 최초 파일 수정 시각(08:16:04)보다 이른 08:15로 정함.',
      'files': files}
(D / 'dataset_fingerprint.json').write_text(json.dumps(fp, ensure_ascii=False, indent=1), encoding='utf-8')
print('overall sha256', h.hexdigest())
