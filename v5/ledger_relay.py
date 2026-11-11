"""실행권을 넘길 때 실거래 장부를 암호화해 다른 PC로 이어 주는 전달 통로 (2026-11-11 사용자 지시 "장부가 이어지지 않는 문제를 해결해").

- 넘겨주는 봇: 실행권을 반납(release·양도)하기 직전에 data/live/live.json(정확한 바이트)과 orders.jsonl을 묶어
  등록된 모든 PC 인증서로 OpenSSL CMS(AES-256-GCM, RSA-OAEP-SHA256) 암호화 → GitHub `ledger-relay` 브랜치(고아 커밋 1개)에 올린다.
  평문·키·주문 식별자는 Git에 들어가지 않는다. 공개되는 것은 암호문과 체크섬·PC 이름·시각뿐이다.
- 받는 PC(take_over): 실행권 기록의 체크섬과 같은 장부만 받는다(복호화 후 sha256 재확인). 아니면 아무것도 바꾸지 않는다
  → 기존처럼 계좌 새 인수로 넘어간다.
- 수신 PC 등록: 각 PC의 개인키는 그 PC의 코인 트레이딩/private_handoff/identities/<이름>/ 에만 있다.
  공개 인증서는 같은 브랜치의 recipients/ 에 둔다(enroll). 처음에는 저장소 handoff/recipients 의 고정(sha256 확인) 인증서로 시작한다.
실패는 모두 예외로 알리고, 호출하는 쪽은 실패해도 실행권 반납·인수 흐름을 막지 않는다.
"""
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import zlib

REF = 'refs/heads/ledger-relay'
FORMAT = 1
OPENSSL_CANDIDATES = (Path('C:/Program Files/Git/usr/bin/openssl.exe'), Path('C:/Program Files/Git/mingw64/bin/openssl.exe'))


class RelayError(RuntimeError):
    pass


def canonical_hash(text):
    """session_guard.checkpoint_hash와 같은 계산."""
    return hashlib.sha256(json.dumps(json.loads(text), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def openssl():
    for p in OPENSSL_CANDIDATES:
        if p.exists():
            return str(p)
    found = shutil.which('openssl')
    if not found:
        raise RelayError('openssl 없음')
    return found


def _run(args, timeout=120, input=None):
    r = subprocess.run(args, capture_output=True, timeout=timeout, input=input,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if r.returncode:
        raise RelayError(f'{Path(args[0]).name} {args[1] if len(args) > 1 else ""} 실패({r.returncode})')
    return r.stdout


class Relay:
    def __init__(self, app, store, workspace=None, repo=None):
        self.app = Path(app)
        self.store = store                      # session_guard.GitStore (같은 원격·같은 재시도·시간 제한)
        self.workspace = Path(workspace) if workspace else self.app.parents[1]   # 코인 트레이딩
        self.repo = Path(repo) if repo else self.workspace.parent / 'ai-trade'

    # ---------- 원격 브랜치 ----------
    def _remote_tree(self):
        """(oid, {경로: bytes}) — 브랜치가 없으면 ('', {})."""
        found = self.store.git('ls-remote', self.store.remote, REF).stdout.strip()
        if not found:
            return '', {}
        self.store.git('fetch', '--quiet', self.store.remote, REF)
        oid = self.store.git('rev-parse', 'FETCH_HEAD').stdout.strip()
        names = self.store.git('ls-tree', '-r', '--name-only', oid).stdout.split()
        files = {}
        for n in names:
            r = subprocess.run(['git', '--git-dir', str(self.store.folder), 'show', f'{oid}:{n}'], capture_output=True, timeout=25,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if r.returncode:
                raise RelayError('브랜치 파일 읽기 실패')
            files[n] = r.stdout
        return oid, files

    def _push_tree(self, expected, files, message):
        entries = []
        subtrees = {}
        for name, data in sorted(files.items()):
            blob = self._hash_object(data)
            if '/' in name:
                d, base = name.split('/', 1)
                subtrees.setdefault(d, []).append(f'100644 blob {blob}\t{base}')
            else:
                entries.append(f'100644 blob {blob}\t{name}')
        for d, lines in subtrees.items():
            sub = self.store.git('mktree', input='\n'.join(lines) + '\n').stdout.strip()
            entries.append(f'040000 tree {sub}\t{d}')
        tree = self.store.git('mktree', input='\n'.join(entries) + '\n').stdout.strip()
        commit = self.store.git('commit-tree', tree, input=message + '\n').stdout.strip()
        lease = f'--force-with-lease={REF}:{expected}' if expected else f'--force-with-lease={REF}:'
        r = self.store.git('push', '--quiet', lease, self.store.remote, f'{commit}:{REF}', check=False)
        if r.returncode:
            raise RelayError('ledger-relay 올리기 실패(' + self.store.failure_category(r.stderr) + ')')
        return commit

    def _hash_object(self, data):
        r = subprocess.run(['git', '--git-dir', str(self.store.folder), 'hash-object', '-w', '--stdin'], input=data,
                           capture_output=True, timeout=25, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if r.returncode:
            raise RelayError('git hash-object 실패')
        return r.stdout.decode().strip()

    # ---------- 수신 PC 인증서 ----------
    def seed_recipients(self):
        """저장소 handoff/recipients/*.json 의 고정 인증서(sha256 일치하는 것만)."""
        out = {}
        folder = self.repo / 'handoff' / 'recipients'
        for meta in sorted(folder.glob('*.json')) if folder.exists() else ():
            try:
                m = json.loads(meta.read_text(encoding='utf-8'))
                cert = (self.repo / m['certificate']).read_bytes()
            except (OSError, ValueError, KeyError):
                continue
            if hashlib.sha256(cert).hexdigest() == m.get('certificate_sha256'):
                out[f'recipients/{Path(m["certificate"]).name}'] = cert
        return out

    @staticmethod
    def _recipients(files):
        return {k: v for k, v in files.items() if k.startswith('recipients/') and k.endswith('.pem') and b'PRIVATE KEY' not in v}

    def identities(self):
        root = self.workspace / 'private_handoff' / 'identities'
        return sorted(p for p in root.glob('*') if (p / 'private-key.pem').exists() and (p / 'public-certificate.pem').exists()) if root.exists() else []

    # ---------- 올리기 ----------
    def publish(self, live_bytes, checkpoint, orders_bytes=None):
        if canonical_hash(live_bytes.decode('utf-8')) != checkpoint:
            raise RelayError('장부 체크섬 불일치(올리지 않음)')
        oid, files = self._remote_tree()
        recips = self._recipients(files) or {}
        recips.update({k: v for k, v in self.seed_recipients().items() if k not in recips})
        if not recips:
            raise RelayError('등록된 수신 PC 인증서 없음')
        bundle = json.dumps({'format': FORMAT, 'checkpoint': checkpoint, 'pc': socket.gethostname(),
                             'time': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
                             'live_json': base64.b64encode(live_bytes).decode(),
                             'orders_jsonl': base64.b64encode(orders_bytes or b'').decode()}).encode()
        with tempfile.TemporaryDirectory(prefix='ledger_relay_') as tmp:
            tmp = Path(tmp)
            plain = tmp / 'bundle.bin'
            plain.write_bytes(zlib.compress(bundle, 9))
            args = [openssl(), 'cms', '-encrypt', '-binary', '-aes-256-gcm', '-in', str(plain), '-out', str(tmp / 'ledger.cms'), '-outform', 'DER']
            for i, (name, pem) in enumerate(sorted(recips.items())):
                f = tmp / f'r{i}.pem'
                f.write_bytes(pem)
                args += ['-recip', str(f), '-keyopt', 'rsa_padding_mode:oaep', '-keyopt', 'rsa_oaep_md:sha256', '-keyopt', 'rsa_mgf1_md:sha256']
            _run(args)
            plain.unlink()
            cipher = (tmp / 'ledger.cms').read_bytes()
        meta = {'format': FORMAT, 'checkpoint': checkpoint, 'pc': socket.gethostname(),
                'time': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
                'ciphertext_sha256': hashlib.sha256(cipher).hexdigest(),
                'recipients': sorted(k.split('/', 1)[1] for k in recips)}
        out = dict(recips)
        out['ledger.cms'] = cipher
        out['meta.json'] = json.dumps(meta, ensure_ascii=False, indent=1).encode()
        self._push_tree(oid, out, 'Encrypted live ledger relay')
        return meta

    # ---------- 받기 ----------
    def fetch(self, checkpoint):
        """체크섬이 정확히 같은 장부 묶음 {'live_bytes','orders_bytes','pc','time'}. 없거나 다르면 RelayError."""
        if not checkpoint:
            raise RelayError('실행권 기록에 체크섬 없음')
        _, files = self._remote_tree()
        if 'meta.json' not in files or 'ledger.cms' not in files:
            raise RelayError('올라온 장부 없음')
        meta = json.loads(files['meta.json'])
        if meta.get('checkpoint') != checkpoint:
            raise RelayError(f'올라온 장부가 실행권 기록과 다른 시점({meta.get("pc")} {meta.get("time")})')
        if hashlib.sha256(files['ledger.cms']).hexdigest() != meta.get('ciphertext_sha256'):
            raise RelayError('암호문 손상')
        ids = self.identities()
        if not ids:
            raise RelayError('이 PC 수신 인증서 미등록(take_over.py --enroll 필요)')
        with tempfile.TemporaryDirectory(prefix='ledger_relay_') as tmp:
            tmp = Path(tmp)
            (tmp / 'ledger.cms').write_bytes(files['ledger.cms'])
            plain = None
            for ident in ids:
                try:
                    _run([openssl(), 'cms', '-decrypt', '-binary', '-inform', 'DER', '-in', str(tmp / 'ledger.cms'),
                          '-recip', str(ident / 'public-certificate.pem'), '-inkey', str(ident / 'private-key.pem'),
                          '-out', str(tmp / 'bundle.bin')])
                    plain = (tmp / 'bundle.bin').read_bytes()
                    break
                except RelayError:
                    continue
            if plain is None:
                raise RelayError('이 PC 인증서로 풀 수 없음(넘겨준 PC가 이 PC 등록 전에 올림)')
        bundle = json.loads(zlib.decompress(plain))
        live = base64.b64decode(bundle['live_json'])
        if bundle.get('checkpoint') != checkpoint or canonical_hash(live.decode('utf-8')) != checkpoint:
            raise RelayError('복호화한 장부 체크섬 불일치')
        return {'live_bytes': live, 'orders_bytes': base64.b64decode(bundle.get('orders_jsonl') or ''),
                'pc': bundle.get('pc'), 'time': bundle.get('time')}

    # ---------- 등록 ----------
    def enroll(self, cert_path):
        pem = Path(cert_path).read_bytes()
        if b'PRIVATE KEY' in pem or b'BEGIN CERTIFICATE' not in pem:
            raise RelayError('공개 인증서가 아님')
        oid, files = self._remote_tree()
        name = f'recipients/{socket.gethostname()}-{hashlib.sha256(pem).hexdigest()[:12]}.pem'
        if files.get(name) == pem:
            return name
        if not self._recipients(files):
            files.update(self.seed_recipients())
        files[name] = pem
        self._push_tree(oid, files, 'Enroll ledger relay recipient')
        return name


def merge_orders(local_bytes, incoming_bytes):
    """두 orders.jsonl을 줄 단위로 합친다(같은 줄은 하나만, 시간순). 읽을 수 없는 줄도 버리지 않는다."""
    seen, rows = set(), []
    for chunk in (local_bytes or b'', incoming_bytes or b''):
        for line in chunk.decode('utf-8', errors='replace').splitlines():
            if not line.strip() or line in seen:
                continue
            seen.add(line)
            try:
                t = str(json.loads(line).get('time') or '')
            except ValueError:
                t = ''
            rows.append((t, len(rows), line))
    rows.sort()
    return ('\n'.join(r[2] for r in rows) + ('\n' if rows else '')).encode('utf-8')


def install(app, bundle, stamp=None):
    """봇이 꺼진 상태에서만 부른다. 이 PC 장부 폴더는 통째로 보관 복사하고, 받은 live.json(정확한 바이트)과 합친 orders.jsonl을 쓴다."""
    live_dir = Path(app) / 'data' / 'live'
    stamp = stamp or dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    keep = Path(app) / 'data' / f'live_이전장부_{stamp}'
    local_orders = b''
    if live_dir.exists():
        shutil.copytree(live_dir, keep)
        f = live_dir / 'orders.jsonl'
        local_orders = f.read_bytes() if f.exists() else b''
    live_dir.mkdir(parents=True, exist_ok=True)
    tmp = live_dir / 'live.json.relay_tmp'
    tmp.write_bytes(bundle['live_bytes'])
    tmp.replace(live_dir / 'live.json')
    (live_dir / 'orders.jsonl').write_bytes(merge_orders(local_orders, bundle['orders_bytes']))
    with (live_dir / 'orders.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps({'time': dt.datetime.now().astimezone().isoformat(timespec='seconds'), 'event': '장부 이어받기',
                            'from_pc': bundle.get('pc'), 'published': bundle.get('time'),
                            'note': '다른 PC가 실행권 반납 때 올린 암호화 장부(체크섬 일치)를 이어받음'}, ensure_ascii=False) + '\n')
    return keep if keep.exists() else None
