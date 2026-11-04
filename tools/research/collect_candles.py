"""업비트 공개 API 15분봉 장기 수집기 (읽기 전용·이어받기 가능).

개인 키·계좌·주문을 전혀 쓰지 않는다. 시점별 거래대금 순위를 재구성하는 검증(pit_universe.py)의 원자료를 만든다.
종목마다 한 파일(JSON Lines)이며 한 줄이 한 봉이다: {"t": 봉 시작 KST, "o","h","l","c","v","val"}.
거래가 없던 봉은 업비트가 돌려주지 않으므로 결측으로 남는다(채우지 않는다).
상장폐지 종목은 마켓 목록에 없어 수집할 수 없다 → 생존 편향이 남는다. 결과 해석에 반드시 밝힌다.
최신 쪽에는 진행 중이던 미완성 봉이 저장되고 종목마다 마지막 수집 시각이 다르다(manifest.json에 종목별 fetched_at). 분석은 공통 컷오프 이전에 마감된 봉만 쓴다(pit_universe.load_dir cutoff).
`to`는 UTC 배타적 상한이고 응답은 최신→과거 순이다(실제 응답으로 검증: test_collect_contract.py). 기존 구간 내부의 누락·손상은 자동 복구하지 않는다.

사용: python collect_candles.py --out <폴더> [--days 180] [--markets KRW-BTC,KRW-ETH]
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = 'https://api.upbit.com/v1'
KST = timezone(timedelta(hours=9))
PAUSE = 0.12  # 공개 시세 호출 간격(초). 업비트 한도(초당 10회)보다 넉넉하게


def get(url, retries=6):
    delay = 1.0
    for _ in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(delay); delay = min(delay * 2, 30); continue
            if e.code == 404:
                return None
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(delay); delay = min(delay * 2, 30)
    raise RuntimeError('조회 실패: ' + url.split('?')[0])


def list_markets():
    rows = get(f'{API}/market/all?is_details=false') or []
    return sorted(r['market'] for r in rows if r['market'].startswith('KRW-'))


def load_existing(path):
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            row = json.loads(line)
            out[row['t']] = row
        except (ValueError, KeyError):
            continue  # 쓰는 도중 끊긴 마지막 줄은 버리고 다시 받는다
    return out


def save(path, rows):
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        for t in sorted(rows):
            f.write(json.dumps(rows[t], ensure_ascii=False, separators=(',', ':')) + '\n')
    tmp.replace(path)


FMT = '%Y-%m-%dT%H:%M:%S'


def collect_market(market, out_dir, since):
    path = out_dir / f'{market}.jsonl'
    rows = load_existing(path)
    since_kst = since.astimezone(KST).strftime(FMT)

    info = {'pages': 0, 'reasons': []}

    def sweep(upper, stop_at=None):
        """upper(UTC, 배타적 상한)부터 과거로 200개씩. 이미 가진 봉(stop_at)이나 since에 닿으면 멈춘다.
        커서가 엄격히 과거로 가는지 검사하고 종료 사유를 info에 기록한다."""
        to = upper
        while True:
            batch = get(f"{API}/candles/minutes/15?market={market}&count=200&to={to.strftime('%Y-%m-%dT%H:%M:%SZ')}")
            time.sleep(PAUSE)
            info['pages'] += 1
            if not batch:
                info['reasons'].append('empty_or_404')
                return
            for c in batch:
                t = c['candle_date_time_kst']
                rows[t] = {'t': t, 'o': c['opening_price'], 'h': c['high_price'], 'l': c['low_price'], 'c': c['trade_price'],
                           'v': c['candle_acc_trade_volume'], 'val': c['candle_acc_trade_price']}
            earliest = batch[-1]['candle_date_time_kst']
            new_to = datetime.fromisoformat(batch[-1]['candle_date_time_utc']).replace(tzinfo=timezone.utc)
            if new_to >= to:
                info['reasons'].append('cursor_not_decreasing')
                raise RuntimeError('커서가 과거로 가지 않음: ' + market)
            to = new_to
            if len(batch) < 200:
                info['reasons'].append('short_page')
                return
            if earliest <= since_kst:
                info['reasons'].append('reached_since')
                return
            if stop_at is not None and earliest <= stop_at:
                info['reasons'].append('reached_existing')
                return

    newest = max(rows) if rows else None
    oldest = min(rows) if rows else None
    fetched_at = datetime.now(timezone.utc)
    sweep(fetched_at, stop_at=newest)  # 최신 쪽 이어받기(처음이면 since까지). 기존 최신 봉(미완성일 수 있음)도 다시 받아 갱신한다
    if oldest is not None and oldest > since_kst:  # 과거 쪽 확장
        sweep(datetime.fromisoformat(oldest).replace(tzinfo=KST).astimezone(timezone.utc))
    save(path, rows)
    ts = sorted(rows)
    return {'market': market, 'fetched_at_utc': fetched_at.strftime('%Y-%m-%dT%H:%M:%SZ'), 'count': len(rows),
            'first': ts[0] if ts else None, 'last': ts[-1] if ts else None, 'pages': info['pages'], 'reasons': info['reasons']}


def write_manifest(out_dir, entries, run_started):
    """종목별 수집 시각·범위·종료 사유. 분석의 공통 컷오프 결정과 재현성 근거. 기존 항목은 유지하고 갱신한다."""
    path = out_dir / 'manifest.json'
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        doc = {'runs': [], 'markets': {}}
    doc.setdefault('runs', [])
    if not doc['runs'] or doc['runs'][-1] != run_started:
        doc['runs'].append(run_started)
    doc['markets'].update(entries)
    tmp = path.with_name('manifest.json.tmp')
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--days', type=int, default=180)
    ap.add_argument('--markets', default='')
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    since = datetime.now(timezone.utc) - timedelta(days=a.days)
    markets = [m for m in a.markets.split(',') if m] or list_markets()
    print(f'{len(markets)}개 종목, {a.days}일, 출력 {out}', flush=True)
    done = 0
    run_started = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    pending = {}
    for m in markets:
        try:
            entry = collect_market(m, out, since)
        except Exception as e:  # 한 종목의 실패가 전체를 멈추지 않게 한다
            print(f'{m} 실패: {type(e).__name__}: {str(e)[:80]}', flush=True)
            continue
        done += 1
        pending[m] = entry
        if len(pending) >= 10:
            write_manifest(out, pending, run_started); pending = {}
        print(f"[{done}/{len(markets)}] {m} {entry['count']}봉", flush=True)
    if pending:
        write_manifest(out, pending, run_started)
    print('완료', flush=True)


if __name__ == '__main__':
    sys.exit(main())
