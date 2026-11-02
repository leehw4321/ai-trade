"""합의 게이트: 토론 합의 표에서 '합의'가 된 배포 항목을 찾아 사전 승인된 범위 안에서 자동 적용한다.

사용자 사전 승인(2026-11-02 23:00): "합의 후 합의가 되면 사전 승인을 해줄게. 묻지 말고 적용해."
적용 조건(모두 만족해야 한다)
  1. 토론 표의 한 줄이 상태 '합의'이고 내용에 배포 표지 `[배포:ID]` 가 들어 있다(상대 AI가 같은 버전을 명시 수용했다는 뜻).
  2. `tools/deploy_manifest.json` 에 그 ID가 등록돼 있다(무엇을 적용하는지 사람이 미리 정해 둔 목록).
  3. 설정 변경은 허용 키 목록 안의 값만 가능하다. 주문 금액 한도·손실 중단선·실거래 켜기/끄기·계좌 인수 범위는 이 게이트로 바꿀 수 없다.
  4. 정본 코드의 HEAD가 매니페스트의 커밋을 포함하고, v5 테스트가 모두 통과하고, 미확정 주문이 없다(deploy_v5가 점검).
  5. 같은 ID는 한 번만 적용한다(tools/deploy_state.json).
  6. 사용자 규칙(2026-11-03 00:00): 토론이 결론 나지 않으면 합의된 부분만 적용하고 나머지는 Claude 추론대로 '잠정' 적용한 뒤 ChatGPT와 함께 평가·수정한다.
     매니페스트 항목에 `provisional_after`(ISO 시각)가 있고 그 시각이 지났으며 합의가 없으면 잠정 적용한다. 허용 키·테스트·미확정 주문 점검은 그대로다.

  python tools/consensus_gate.py            점검만(적용하지 않음)
  python tools/consensus_gate.py --apply    조건이 맞는 항목을 적용
  python tools/consensus_gate.py --watch    2분마다 점검해 자동 적용 (일시 정지: tools/deploy_pause.txt)
"""
import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deploy_v5 as dp  # noqa: E402

ROOT = dp.ROOT
DEBATE = ROOT / 'debate_tools' / '토론.md'
MANIFEST = ROOT / 'tools' / 'deploy_manifest.json'
STATE = ROOT / 'tools' / 'deploy_state.json'
TAG = re.compile(r'\[배포:([A-Za-z0-9._-]+)\]')
ALLOWED = {  # 이 게이트가 바꿀 수 있는 설정 키(섹션.키). 한도·실거래 on/off·인수 범위는 의도적으로 없다.
    'live.trail_enabled', 'live.trail_adopted', 'live.adopted_trail_start_r',
    'live.partial_enabled', 'live.partial_r', 'live.partial_frac',
    'live.loss_daily', 'strategy.size_by_risk', 'strategy.entry_top', 'strategy.entry_want', 'strategy.entry_scan',
    'strategy.chase_rs_cap', 'strategy.stop_max_pct',
    'strategy.trail_enabled', 'strategy.trail_m', 'strategy.giveback', 'strategy.trail_start_r', 'strategy.trail_lock_r',
}
WATCH_SECONDS = 120


def agreed_tags(text):
    """합의 표에서 상태가 '합의'이고 배포 표지가 있는 줄의 (표지 -> 안건 번호)."""
    out = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip('|').split('|', 3)]
        if len(cells) == 4 and cells[0].isdigit() and cells[2] == '합의':
            for tag in TAG.findall(cells[3]):
                out[tag] = int(cells[0])
    return out


def config_allowed(patch):
    bad = [f'{sec}.{k}' for sec, vals in patch.items() for k in vals if f'{sec}.{k}' not in ALLOWED]
    return not bad, bad


def notify_user(title, message):
    """바탕화면에 알림창을 띄운다(기다리지 않고 별도 프로세스로). 실패해도 적용 결과에는 영향이 없다."""
    try:
        code = 'import ctypes,sys;ctypes.windll.user32.MessageBoxW(0,sys.argv[2],sys.argv[1],0x40)'
        subprocess.Popen([sys.executable, '-c', code, title, message], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:
        pass


def load_json(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def head_contains(commit):
    r = subprocess.run(['git', 'merge-base', '--is-ancestor', commit, 'HEAD'], cwd=ROOT, capture_output=True)
    return r.returncode == 0


def run_once(apply, debate=None, manifest=None, state=None, now=None):
    debate, manifest, state = debate or DEBATE, manifest or MANIFEST, state or STATE
    now = now or dt.datetime.now()
    tags = agreed_tags(debate.read_text(encoding='utf-8')) if debate.exists() else {}
    items = load_json(manifest, {})
    done = load_json(state, {})
    result = []
    # 합의가 없어도 사용자 규칙에 따라 기한이 지난 항목은 '잠정' 후보(row 0)로 올린다. 합의가 있으면 합의가 우선.
    cands = dict(tags)
    for tag, spec in items.items():
        when = spec.get('provisional_after')
        if tag not in cands and when and now >= dt.datetime.fromisoformat(when):
            cands[tag] = 0
    for tag, row in sorted(cands.items()):
        if tag in done:
            continue
        spec = items.get(tag)
        if not spec:
            result.append((tag, row, '매니페스트에 없음: 적용하지 않음'))
            continue
        ok, bad = config_allowed(spec.get('config', {}))
        if not ok:
            result.append((tag, row, f'허용되지 않은 설정 키 {bad}: 거부'))
            continue
        if not head_contains(spec['commit']):
            result.append((tag, row, f'정본 HEAD에 커밋 {spec["commit"][:7]}가 없음: 보류'))
            continue
        if not apply:
            result.append((tag, row, '적용 가능(점검 모드)'))
            continue
        label = f'합의 게이트 {tag}(토론 안건 {row})' if row else f'잠정 적용 {tag}(합의 없음, 사용자 규칙)'
        code = dp.deploy(label, config_patch=spec.get('config') or None)
        if code == 0:
            done[tag] = {'time': dt.datetime.now().isoformat(timespec='seconds'), 'row': row, 'commit': spec['commit'], 'provisional': not row}
            state.write_text(json.dumps(done, ensure_ascii=False, indent=2), encoding='utf-8')
            result.append((tag, row, '적용 완료'))
            notify_user('봇 설정 변경 적용', f"{tag} ({'잠정, 합의 없음' if not row else '토론 안건 ' + str(row) + ' 합의'})\n{spec.get('note', '')}\n설정: {spec.get('config') or '코드 기본값'}\n되돌리기: v5-app/v5/_backup 최신 폴더")
        else:
            result.append((tag, row, f'적용 실패/보류(코드 {code}): 다음 점검에서 재시도'))
    return result


def watch():
    dp.log('합의 게이트 감시 시작 (조건 충족 시 사전 승인 범위에서 자동 적용)')
    while True:
        try:
            if not dp.PAUSE.exists():
                for tag, row, msg in run_once(True):
                    dp.log(f'[게이트] {tag} (안건 {row}): {msg}')
        except Exception as e:
            dp.log(f'[게이트] 오류: {type(e).__name__}: {e}')
        time.sleep(WATCH_SECONDS)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--watch', action='store_true')
    a = ap.parse_args(argv)
    if a.watch:
        watch()
        return 0
    res = run_once(a.apply)
    for tag, row, msg in res:
        print(f'{tag} (안건 {row}): {msg}')
    if not res:
        print('합의된 배포 항목 없음')
    return 0


if __name__ == '__main__':
    sys.exit(main())
