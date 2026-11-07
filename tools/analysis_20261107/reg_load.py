import json, glob, os, numpy as np
D = r'C:\Users\User\Desktop\research_data\candles15_long'
S = os.path.dirname(os.path.abspath(__file__))
fs = sorted(glob.glob(D + r'\*.jsonl'))
data = {}
for f in fs:
    m = os.path.basename(f)[:-6]
    rows = [json.loads(l) for l in open(f, encoding='utf-8') if l.strip()]
    data[m] = {r['t']: (r['o'], r['h'], r['l'], r['c'], r['val']) for r in rows}
T = sorted(set.intersection(*[set(v) for v in data.values()]))
allT = sorted(set(t for v in data.values() for t in v))
print('코인', len(data), '교집합 봉', len(T), '전체 봉', len(allT), allT[0], allT[-1])
# 코인별 가장 늦게 시작한 코인 확인
starts = sorted((min(v), m) for m, v in data.items())
print('늦게 시작한 코인:', starts[-5:])
# 전체 시간축(allT) 기준 배열, 없는 곳은 nan
ix = {t: i for i, t in enumerate(allT)}
N = len(allT)
A = {}
for m, v in data.items():
    a = np.full((N, 5), np.nan)
    for t, x in v.items(): a[ix[t]] = x
    A[m] = a
np.savez_compressed(S + r'\c15.npz', times=np.array(allT), **{m: a for m, a in A.items()})
print('saved')
