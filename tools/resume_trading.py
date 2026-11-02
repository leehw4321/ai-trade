"""중단된 신규 매수를 수동으로 재개하도록 봇에 요청한다(주문은 내지 않는다).

  python tools/resume_trading.py [--app <봇 폴더>] [--reason "사유"]

봇이 30초 안에 data/live/RESUME_BUY를 읽어 중단을 해제하고, 그 시점 손실을 새 기준으로 삼아 손실 집계를 다시 시작한다.
손절·이익 보호는 중단 중에도 계속 동작한다. 같은 선에 다시 닿으면 다시 중단된다.
"""
import argparse
import datetime as dt
from pathlib import Path

DEFAULT_APP = Path(__file__).resolve().parents[2] / '코인 트레이딩' / 'v5-app' / 'v5'


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--app', default=str(DEFAULT_APP))
    ap.add_argument('--reason', default='사용자 지시')
    a = ap.parse_args(argv)
    folder = Path(a.app) / 'data' / 'live'
    if not folder.exists():
        print(f'봇 폴더를 찾지 못함: {folder}')
        return 1
    (folder / 'RESUME_BUY').write_text(f'{a.reason} ({dt.datetime.now():%m-%d %H:%M})', encoding='utf-8')
    print(f'재개 요청 기록: {folder / "RESUME_BUY"} — 봇이 30초 안에 처리합니다.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
