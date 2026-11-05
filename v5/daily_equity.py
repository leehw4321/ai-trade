"""하루(한국시간 0시~24시) 총자산 기록. 계좌 총자산의 날짜별 시작·마지막·최고·최저를 data/daily_equity.json에 남긴다.

하루 손익 = 마지막 총자산 - 시작 총자산. 시작은 전날 마지막 기록(전날이 아니면 그날 첫 관측).
입출금은 구분하지 못하므로 그날 입출금이 있으면 손익에 섞인다(화면에 안내).
"""
import datetime as dt
import json
import math
import os
from pathlib import Path

KST = dt.timezone(dt.timedelta(hours=9))
KEEP_DAYS = 400


def day_of(t):
    return t.astimezone(KST).strftime('%Y-%m-%d')


def read(path):
    try:
        d = json.loads(Path(path).read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def record(path, total, now=None):
    """총자산 한 번 관측. 바뀐 게 있으면 저장하고 그날 행을 돌려준다. 값이 이상하면 None."""
    if type(total) not in (int, float) or not math.isfinite(total) or total <= 0:
        return None
    now = now or dt.datetime.now(KST)
    today, stamp = day_of(now), now.astimezone(KST).strftime('%H:%M:%S')
    days = read(path)
    row = days.get(today)
    if row is None:
        prev = days.get(day_of(now - dt.timedelta(days=1)))
        if prev:
            row = {'open': prev['last'], 'open_at': prev['last_at'], 'open_src': '전날 마지막'}
        else:
            row = {'open': total, 'open_at': stamp, 'open_src': '첫 관측'}
        row.update(high=row['open'], low=row['open'])
    row.update(last=total, last_at=stamp, high=max(row['high'], total), low=min(row['low'], total))
    row['pnl'] = round(row['last'] - row['open'])
    row['pct'] = round((row['last'] / row['open'] - 1) * 100, 3) if row['open'] else 0.
    days[today] = row
    for k in sorted(days)[:-KEEP_DAYS]:
        days.pop(k)
    p = Path(path)
    tmp = p.with_name(p.name + '.tmp')
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(days, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, p)
    return row


def recent(path, n=14):
    """최근 n일(최신순) [(날짜, 행)]."""
    days = read(path)
    return [(k, days[k]) for k in sorted(days, reverse=True)[:n]]
