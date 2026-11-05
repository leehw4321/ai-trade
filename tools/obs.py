"""봇 화면 '관찰·그림자 관리' 창의 항목을 명령줄에서 보고 고친다(화면 버튼과 같은 파일을 쓴다).

  python tools/obs.py list
  python tools/obs.py add ID "제목" [--kind deadline|shadow|decision|observe] [--due 2026-11-12T09:00:00+09:00] [--criteria "판정 기준"] [--source "근거 문서"]
  python tools/obs.py done|hold|open ID ["메모"]
  python tools/obs.py note ID "메모"

항목 파일은 집 실행 사본의 data/observations.json이다. 이 PC에 실행 사본이 없으면 만들지 않고 알린다.
"""
import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP = REPO.parent / '코인 트레이딩' / 'v5-app' / 'v5'
sys.path.insert(0, str(REPO / 'v5'))
import observations as ob  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list')
    a = sub.add_parser('add')
    a.add_argument('id')
    a.add_argument('title')
    a.add_argument('--kind', default='observe', choices=sorted(ob.KINDS))
    a.add_argument('--due')
    a.add_argument('--criteria', default='')
    a.add_argument('--source', default='')
    for name in ('done', 'hold', 'open', 'note'):
        s = sub.add_parser(name)
        s.add_argument('id')
        s.add_argument('text', nargs='?')
    ns = ap.parse_args(argv)
    path = APP / 'data' / 'observations.json'
    if not APP.exists():
        print(f'실행 사본이 없습니다: {APP}')
        return 1
    if ns.cmd == 'list':
        doc = ob.load(path)
        now = ob._now()
        live, over, soon = ob.summary(doc, now)
        print(f'진행 {live}건 · 기한 지남 {over} · 24시간 안 {soon}')
        for x in doc['items']:
            print(f"  [{x.get('status')}] {x['id']:18s} {ob._due_info(x, now)[1]:28s} {x['title']}")
        return 0
    if ns.cmd == 'add':
        ob.add(path, ns.id, ns.title, ns.kind, ns.due, ns.criteria, ns.source)
        print('추가했습니다.')
        return 0
    ok, msg = ob.apply(path, ns.id, {'action': ns.cmd, 'note': ns.text})
    print(msg)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
