"""Display-only Upbit KRW breadth summary. This is not a trading signal/index."""
import math
import statistics
import time

STABLE = {'KRW-USDT', 'KRW-USDC', 'KRW-DAI', 'KRW-USDE', 'KRW-USDD',
          'KRW-USDS', 'KRW-USD1', 'KRW-FDUSD', 'KRW-TUSD', 'KRW-EURC'}
ZONES = (('폭락', '#b93b55'), ('하락', '#bd7540'), ('횡보', '#536785'),
         ('상승', '#198968'), ('급등', '#146c9c'))


def unavailable(reason='시황 데이터 수집 중'):
    return {'available': False, 'label': '확인 불가', 'reason': reason, 'time': 0}


def summarize(rows, now=None):
    now = time.time() if now is None else now
    midnight = math.floor(now / 86400) * 86400
    valid = {}
    for row in rows:
        try:
            m = row['market']
            current, previous = float(row['trade_price']), float(row['prev_closing_price'])
            turnover = float(row['acc_trade_price_24h'])
            stamp = float(row['trade_timestamp']) / 1000
            # Source quotes must belong to today's UTC session and have recent trades.
            if (not m.startswith('KRW-') or m in STABLE or current <= 0 or previous <= 0
                    or not all(math.isfinite(x) for x in (current, previous, turnover, stamp))
                    or turnover <= 0 or not 0 <= now - stamp <= 300 or stamp < midnight):
                continue
            valid[m] = {'market': m, 'change': current / previous - 1, 'turnover': turnover}
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            continue
    if not all(m in valid for m in ('KRW-BTC', 'KRW-ETH')) or len(valid) < 10:
        return unavailable('최근 BTC·ETH 및 원화 종목 10개 이상 데이터 필요')
    liquid = sorted(valid.values(), key=lambda r: r['turnover'], reverse=True)[:50]
    changes = [r['change'] for r in liquid]
    up = sum(c > .003 for c in changes)
    down = sum(c < -.003 for c in changes)
    count = len(changes)
    btc, eth = valid['KRW-BTC']['change'], valid['KRW-ETH']['change']
    median = statistics.median(changes)
    clip = lambda x: max(-1, min(1, x / .05))
    score = 100 * (.4 * clip(btc) + .2 * clip(eth) + .25 * clip(median) + .15 * (up-down)/count)
    # Broad selloffs / a large BTC loss get an explicit crash override.
    crash = btc <= -.06 or (median <= -.05 and down / count >= .8)
    if crash:
        score = min(-60, score)
    score = round(score, 1)
    index = 0 if score <= -60 else 1 if score < -20 else 2 if score <= 20 else 3 if score < 60 else 4
    return {'available': True, 'label': ZONES[index][0], 'zone': index, 'score': round(score, 1),
            'btc': btc, 'eth': eth, 'median': median, 'up': up, 'down': down,
            'flat': count-up-down, 'count': count, 'time': now, 'session': midnight}


def current_view(summary, now=None):
    now = time.time() if now is None else now
    if not summary.get('available'):
        return summary
    if not 0 <= now - summary['time'] <= 90 or summary.get('session') != math.floor(now/86400)*86400:
        return unavailable('시황 갱신 지연 · 연결 확인 필요')
    return summary
