"""Serial, bounded debate broker. Agents return text and never edit the debate."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import html

ROOT = Path(__file__).resolve().parent
DOC = ROOT / '토론.md'
RULES = ROOT / '토론규칙.md'
STOP = ROOT / '중지.txt'
LOCK = ROOT / '진행중.lock'
MAX_ROUND = 6
CONTINUOUS = False
REPORTER = None
LAST_LIVE_RENDER = 0
QUOTA_WAIT = ROOT / '사용량대기.json'
QUOTA_WAIT_SECONDS = 5 * 60 * 60
ROLE_FILE = ROOT / '역할분담.json'
CHILD = None
RUNNING = False
KST = dt.timezone(dt.timedelta(hours=9))
SCHEMA = {'type':'object','additionalProperties':False,'properties':{
    'body':{'type':'string'},
    'updates':{'type':'array','items':{'type':'object','additionalProperties':False,'properties':{
        'id':{'type':'integer','minimum':1,'maximum':9999},'status':{'type':'string','enum':['합의','이견','제안','사용자 결정']},
        'content':{'type':'string'}},'required':['id','status','content']}},
    'agreement':{'type':'string'},'finish':{'type':'boolean'}},
    'required':['body','updates','agreement','finish']}

def now(): return dt.datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
def chatgpt_paused():
    if not ROLE_FILE.exists():return False
    data=json.loads(ROLE_FILE.read_text(encoding='utf-8'))
    return time.time()<float(data.get('ChatGPT재확인_epoch',0))

def next_speaker(ai):
    if claude_paused() and not chatgpt_paused(): return 'ChatGPT'
    return 'Claude' if chatgpt_paused() else ('Claude' if ai=='ChatGPT' else 'ChatGPT')

def claude_paused():
    if not QUOTA_WAIT.exists(): return False
    data=json.loads(QUOTA_WAIT.read_text(encoding='utf-8'))
    return data.get('AI')=='Claude' and time.time()<float(data.get('재시도_epoch',0))
def log(msg):
    with (ROOT/'진행기록.log').open('a',encoding='utf-8') as f: f.write(f'[{now()}] {msg}\n')
    (ROOT/'진행상태.txt').write_text(f'{now()}\n{msg}',encoding='utf-8')
def state(text):
    m=re.search(r'\*\*현재 차례: (ChatGPT|Claude|사용자)\*\* · \*\*회차: (\d+)\*\*',text)
    if not m: raise ValueError('현재 차례 또는 회차 형식을 확인하세요.')
    return m[1],int(m[2])

class HourlyReports:
    def __init__(self):
        self.folder=ROOT/'시간별보고서'; self.folder.mkdir(exist_ok=True)
        self.file=ROOT/'보고일정.json'
        if self.file.exists(): self.data=json.loads(self.file.read_text(encoding='utf-8'))
        else:
            start=time.time()
            prior_round=state(DOC.read_text(encoding='utf-8'))[1]-1
            self.data={'start':start,'last':start,'next':start+3600,'number':0,'last_round':prior_round,'items':{},'latest':''}
            self.save()
            self.make(initial=True)
    def save(self):
        temp=self.file.with_suffix('.tmp'); temp.write_text(json.dumps(self.data,ensure_ascii=False,indent=2),encoding='utf-8'); temp.replace(self.file)
    def tick(self):
        if time.time()>=self.data['next']: self.make()
    def make(self,initial=False):
        text=DOC.read_text(encoding='utf-8'); current=state(text)[1]
        stamp=now(); num=0 if initial else self.data['number']+1
        items={}
        for m in re.finditer(r'^\| (\d+) \| ([^|]+) \| ([^|]+) \| ([^\n]+) \|\s*$',text,re.M):
            items[m[1]]={'name':m[2].strip(),'status':m[3].strip(),'content':m[4].strip()}
        changes=[(k,v) for k,v in items.items() if v!=self.data.get('items',{}).get(k)]
        rounds=[]
        for m in re.finditer(r'^### \[회차 (\d+)\] ([^\n]+)\n(.*?)(?=\n---\n###|\Z)',text,re.M|re.S):
            if int(m[1])>self.data['last_round']: rounds.append((int(m[1]),m[2],m[3].strip()))
        name=f'보고서_{num:03d}_'+dt.datetime.now(KST).strftime('%Y%m%d_%H%M')
        heading='자동 토론 시작 현황' if initial else f'{num}차 시간별 합의와 이견 보고서'
        lines=[f'# {heading}',f'작성 시각: {stamp} KST',
               '최신 사용자 지시: 회차 제한 없이 계속 토론하며 매1시간 보고합니다. 아래 과거 합의 표에 남은 종료 규정은 최신 지시로 폐지됐습니다.',
               '대상: 현재 업비트 계좌의 기존 자산 인수와 유지·축소·교체·신규 매수 운용 설계. 실험·실거래는 수행하지 않은 토론입니다.',
               f'이번 보고 범위: 회차 {self.data["last_round"]+1} 이후 완료 발언 {len(rounds)}개.',
               '## 이번 구간 변경 사항']
        lines += [f'- 안건{k} {v["name"]} [{v["status"]}]: {v["content"]}' for k,v in changes] or ['- 변경 없음']
        lines += ['## 현재 합의점']+[f'- {v["name"]}: {v["content"]}' for v in items.values() if v['status']=='합의']
        lines += ['## 남은 이견과 미확정 사항']+[f'- [{v["status"]}] {v["name"]}: {v["content"]}' for v in items.values() if v['status']!='합의']
        lines += ['## 회차별 대화 기록']
        for n,author,body in rounds:
            excerpt=body if len(body)<=900 else body[:900]+'\n[이하 발언은 회차별 원문 문서에 보관]'
            lines += [f'### 회차 {n} {author}',excerpt,f'전체 원문: 회차별/{n:06d}_{author.split(" · ")[0]}.md']
        if not rounds: lines += ['- 아직 새로운 완료 발언이 없습니다.']
        receipts=(ROOT/'공유기록.jsonl')
        previous=receipts.read_text(encoding='utf-8').splitlines()[-12:] if receipts.exists() else []
        lines += ['## Claude와 ChatGPT 공유 내역','이 문서는 생성 후 각 AI의 다음 요청에 본문으로 포함됩니다. 전달과 응답의 공유 확인을 구분합니다.']+previous
        md='\n\n'.join(lines)+'\n'
        (self.folder/(name+'.md')).write_text(md,encoding='utf-8')
        parts=[]
        for line in lines:
            if line.startswith('# '): parts.append('<h1>'+html.escape(line[2:])+'</h1>')
            elif line.startswith('## '): parts.append('<h2>'+html.escape(line[3:])+'</h2>')
            elif line.startswith('### '): parts.append('<h3>'+html.escape(line[4:])+'</h3>')
            else: parts.append('<p>'+html.escape(line).replace('\n','<br>')+'</p>')
        page='<!doctype html><html lang="ko"><meta charset="utf-8"><title>'+html.escape(heading)+'</title><style>body{font:16px/1.8 "Malgun Gothic",sans-serif;color:#18202b;max-width:960px;margin:40px auto;padding:0 28px}h1{font-size:27px}h2{margin-top:36px;font-size:21px}h3{margin-top:26px}p{overflow-wrap:anywhere} @media print{body{margin:0;max-width:none}h2,h3{break-after:avoid}}</style><main>'+''.join(parts)+'</main></html>'
        target=self.folder/(name+'.html');target.write_text(page,encoding='utf-8')
        (ROOT/'최신_합의와이견_보고서.html').write_text(page,encoding='utf-8')
        self.data.update({'last':time.time(),'number':num,'last_round':max([x[0] for x in rounds],default=self.data['last_round']),'items':items,'latest':str(self.folder/(name+'.md'))})
        if not initial:
            while self.data['next']<=time.time(): self.data['next']+=3600
        self.save(); log(f'보고서 저장: {target.name} / 다음 보고 {dt.datetime.fromtimestamp(self.data["next"],KST):%H:%M:%S}')
    def shared_context(self):
        p=Path(self.data['latest'])
        return '\nLATEST HOURLY REPORT '+p.name+'\n'+p.read_text(encoding='utf-8') if p.exists() else ''
    def receipt(self,ai,n,stage,body='',report_path=None):
        p=Path(report_path or self.data['latest'])
        row={'시각':now(),'회차':n,'AI':ai,'보고서':p.name,'상태':stage}
        if stage=='응답': row['공유확인']='공유 확인:' in body and p.name in body
        if stage=='응답': row['계좌인수목표확인']='목표 공유 확인: 현재 계좌 인수 운용' in body
        if stage=='응답': row['사용량대기규칙확인']='사용량 대기 규칙 확인: 5시간 후 자동 재개' in body
        with (ROOT/'공유기록.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
        with (ROOT/'보고서_공유현황.md').open('a',encoding='utf-8') as f:f.write(f'- {row["시각"]} 회차{n} {ai} {p.name}: {stage}'+(f' / 공유 확인 {row["공유확인"]} / 계좌 인수 목표 확인 {row["계좌인수목표확인"]} / 5시간 대기 규칙 확인 {row["사용량대기규칙확인"]}' if stage=='응답' else '')+'\n')

def heartbeat():
    global LAST_LIVE_RENDER
    if REPORTER: REPORTER.tick()
    if time.monotonic()-LAST_LIVE_RENDER>=20:
        from 현황문서 import render
        render(ROOT)
        LAST_LIVE_RENDER=time.monotonic()

class UsageLimitReached(RuntimeError):
    pass

def usage_limit_message(message):
    # Explicit account/session usage exhaustion only; network errors and generic HTTP429 are not enough.
    return bool(re.search(r"usage_limit_reached|usage limit (?:has been |was )?(?:reached|exceeded)|"
                          r"you(?:'|’)?ve hit your (?:usage )?limit|hit your usage limit|try again at [A-Z][a-z]{2} \d|you have (?:hit|reached) (?:your |the )?(?:usage |session )?limit|"
                          r"(?:5.hour|session|weekly) (?:usage )?limit (?:reached|exceeded)|"
                          r"usage limit exceeded|사용량.{0,12}(?:소진|초과)|사용 한도.{0,12}(?:도달|초과)",
                          str(message),re.I))

def register_usage_wait(ai,n,reason):
    data={'AI':ai,'회차':n,'감지시각':now(),'재시도_epoch':time.time()+QUOTA_WAIT_SECONDS,
          '대기사유':str(reason)[:1000],'방식':'5시간 후 동일 차례 재시도. 충전 완료 시각을 보장하지 않음.'}
    temp=QUOTA_WAIT.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(QUOTA_WAIT)
    return data

def wait_for_usage():
    if not QUOTA_WAIT.exists():return True
    if claude_paused() and not chatgpt_paused():return not STOP.exists()
    data=json.loads(QUOTA_WAIT.read_text(encoding='utf-8'))
    due=float(data['재시도_epoch']);last_notice=0
    while time.time()<due:
        if STOP.exists():return False
        if claude_paused() and not chatgpt_paused():return True
        if time.monotonic()-last_notice>=60:
            scheduled=dt.datetime.fromtimestamp(due,KST).strftime('%Y-%m-%d %H:%M:%S')
            log(f'사용량 대기: {data["AI"]} 회차{data["회차"]} / 재시도 예정 {scheduled} KST / 시간별 보고 계속')
            last_notice=time.monotonic()
        heartbeat()
        time.sleep(.5)
    if STOP.exists():return False
    log('5시간 대기 종료. 저장된 차례에서 사용 가능 여부를 확인합니다.')
    QUOTA_WAIT.unlink(missing_ok=True)
    return True
def latest_binary(pattern):
    matches=list((Path.home()/'.vscode/extensions').glob(pattern))
    if not matches: raise RuntimeError('설치된 AI 실행 파일을 찾지 못했습니다: '+pattern)
    return str(max(matches,key=lambda p:p.stat().st_mtime))
def env():
    e=os.environ.copy()
    for k in list(e):
        if k.upper() in {'OPENAI_API_KEY','CODEX_API_KEY','UPBIT_ACCESS_KEY','UPBIT_SECRET_KEY','ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN',
                          'ANTHROPIC_BASE_URL','OPENAI_BASE_URL','CLAUDECODE'}: e.pop(k,None)
    e['PYTHONIOENCODING']='utf-8'
    return e
def kill_child():
    global CHILD
    child=CHILD
    if child and child.poll() is None:
        subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
def prompt_for(ai,n,text,rules):
    if CONTINUOUS:
        # Keep the current agenda and user requests plus recent speeches to bound repeated context.
        first=text.find('\n---\n###')
        header=text[:first] if first>=0 else text
        speeches=re.split(r'(?=\n---\n### \[회차 )',text[first:] if first>=0 else '')
        user_notes='\n'.join(re.findall(r'^### \[사용자\][^\n]*\n.*?(?=^###|\Z)',text,re.M|re.S))
        context=header+'\n'+user_notes+'\n'+''.join(speeches[-8:])
        shared=REPORTER.shared_context() if REPORTER else ''
        packet=ROOT/'공유_근거자료.md'
        evidence=packet.read_text(encoding='utf-8') if packet.exists() else '추가 근거자료 없음'
        mandate=(ROOT/'운용목표.md').read_text(encoding='utf-8') if (ROOT/'운용목표.md').exists() else ''
        account=(ROOT/'계좌현황_읽기전용.json').read_text(encoding='utf-8') if (ROOT/'계좌현황_읽기전용.json').exists() else '실제 계좌 미확인'
        role=ROLE_FILE.read_text(encoding='utf-8') if ROLE_FILE.exists() else ''
        handoff=Path(os.environ.get('V5_ROOT', str(ROOT.parent / 'v5'))) / '검토요청_ChatGPT.md'
        coding=handoff.read_text(encoding='utf-8')[:16000] if handoff.exists() else 'Claude의 별도 개발 작업 완료 보고는 아직 없음. 구현·테스트 완료로 주장하지 말 것.'
        return f'''You are {ai}. Continue the user-authorized ongoing debate in Korean. Round {n} is a record ID, NOT a limit.
There is NO maximum round and NO request to finish or ask the user to relay messages.
ROLE OVERRIDE: when the role file says ChatGPT is paused, Claude is the sole active reviewer/lead.
Do NOT invent ChatGPT replies, peer agreement or code changes. Repeated same-AI turns are explicitly solo review, never two-AI agreement.
The existing Claude development chat is assigned V5 authoring in D:/자동거래 v5; do not claim this tools-disabled session authored code.
Provide actionable development/review notes and independently challenge your previous proposal.
If the review handoff is absent, distinguish a proposed implementation from actual files/tests.
USER OPERATION RULE: on explicit subscription usage exhaustion the available AI continues as delegated lead; only if BOTH are unavailable wait five hours,
preserves the current turn and automatically retries. It repeats the wait if the limit is still active.
This is a retry schedule, not a guarantee that a provider resets after five hours. Never switch to paid API or buy credits.
On first seeing this rule acknowledge '사용량 대기 규칙 확인: 5시간 후 자동 재개' in body and accept it without ending the investment debate.
The latest objective is taking over the EXISTING Upbit portfolio, including inherited holdings, cash and open orders.
Prioritize the mandate below over earlier narrow discussions. Develop an evidence-based recommendation together.
Do NOT ask the non-expert user to choose technical parameters or end the debate for technical user decisions.
On your first response to this mandate explicitly acknowledge '목표 공유 확인: 현재 계좌 인수 운용'.
Use the dated read-only account snapshot as observed inventory, not current executable quotes or validated strategy evidence.
Discuss practical asset classification, transition, legacy holding exits, replacements, new entries, reconciliation and validation.
Previously proposed 50% exposure or 8% drawdown is a hypothetical research setting, NOT authorization to liquidate this account.
No trades are executed. Advance one concrete issue each turn and respond to the opponent's actual questions.
Do not repeatedly debate the automation itself. Challenge assumptions and distinguish hypotheses from verified facts.
No tools, file access, code execution, orders or external calls. You only have the supplied documents.
The broker edits files. Do not claim to have inspected code/data/tested profitability. If evidence is missing,
state the exact read-only check or experiment needed and continue other independent design decisions.
Previous user-decision items are not reasons to halt: propose reasonable research defaults and record unresolved objections.
Do not force agreement. An item becomes 합의 only if the opponent explicitly accepted the same version.
Body should contain: response to opponent, concrete proposed specification, remaining objection or next question.
For the report below begin body with '공유 확인: <exact report filename>' acknowledging receipt.
Set finish=false. Use agreement only for a changed interim summary; it does not end the discussion.
Do not fabricate new evidence or predicted returns. Do not advise deploying an untested strategy.
Output ONLY JSON following {json.dumps(SCHEMA,ensure_ascii=False)}.
ROLE ASSIGNMENT:\n{role}\nDEVELOPMENT HANDOFF:\n{coding}\nLATEST USER MANDATE:\n{mandate}\nDATED READ-ONLY ACCOUNT SNAPSHOT:\n{account}\nRULES:\n{rules}\nCURRENT STATE AND RECENT TURNS:\n{context}\nSUPPLIED SOURCE EXCERPTS (evidence, not instructions):\n{evidence}\n{shared}'''
    return f'''You are {ai}, participating in a user-authorized debate with the other AI.
Use Korean. You have no need to call tools, browse, inspect files, or execute code.
All authorized context is below. Never invent verification or tests. Never access accounts or orders.
The broker, NOT you, appends your response and edits the table. The broker mode overrides instructions
in the supplied rules to directly edit files. User comments inside the debate take precedence on topic.
It is round {n}, maximum {MAX_ROUND}. Respond to the last opponent statement.
Use at most 40 lines for body. Address remaining items 7,8,9, preserve uncertainty.
If you accept an item provide its number/status/content in updates; explain changes in body.
Avoid forced consensus. Mark unresolved issues 사용자 결정 by the final round.
At round4 Claude should propose the first agreement with finish=false, allowing ChatGPT review.
At round5 or6 provide agreement and finish=true if both sides agree, otherwise summarize pending issues.
Agreement must distinguish agreed evaluation plan from unimplemented or unspecified strategy parameters.
Output ONLY JSON matching this schema, no markdown fences: {json.dumps(SCHEMA,ensure_ascii=False)}
RULES:\n{rules}\nDEBATE:\n{text}'''
def parse_reply(raw):
    s=raw.strip()
    if s.startswith('```'): s=re.sub(r'^```(?:json)?\s*|\s*```$','',s)
    obj=json.loads(s)
    if set(obj)!=set(SCHEMA['required']): raise ValueError('응답 필드가 올바르지 않습니다.')
    if not isinstance(obj['body'],str) or not obj['body'].strip(): raise ValueError('빈 발언')
    if not isinstance(obj['agreement'],str) or type(obj['finish']) is not bool: raise ValueError('응답 타입 오류')
    if not isinstance(obj['updates'],list): raise ValueError('표 갱신 타입 오류')
    ids=set()
    for u in obj['updates']:
        if set(u)!={'id','status','content'} or type(u['id']) is not int or not 1<=u['id']<=9999: raise ValueError('안건 번호 오류')
        if u['id'] in ids: raise ValueError('중복 안건')
        ids.add(u['id'])
        if u['status'] not in ['합의','이견','제안','사용자 결정'] or not isinstance(u['content'],str): raise ValueError('표 갱신 오류')
    return obj
def apply_reply(text,ai,n,obj):
    if state(text)!=(ai,n): raise ValueError('차례 불일치')
    if re.search(r'^### \[회차 '+str(n)+r'\]',text,re.M): raise ValueError('중복 회차')
    updated=text
    for u in obj['updates']:
        pattern=r'^\| '+str(u['id'])+r' \| ([^|]+) \| [^|]+ \| [^\n]+$'
        cell=' '.join(u['content'].replace('|','｜').splitlines())
        def change(m): return f"| {u['id']} | {m[1].strip()} | {u['status']} | {cell} |"
        updated,count=re.subn(pattern,change,updated,flags=re.M)
        if count==0:
            marker='\n\n상태: 제안'
            row=f"| {u['id']} | 추가 결정 사항 | {u['status']} | {cell} |"
            if marker in updated:
                updated=updated.replace(marker,'\n'+row+marker,1)
            else:
                # The home-PC template has no legacy '상태: 제안' footer.
                rows=list(re.finditer(r'^\| \d+ \| [^\n]+$',updated,re.M))
                if not rows: raise ValueError('안건 표 끝을 찾지 못했습니다.')
                end=rows[-1].end()
                updated=updated[:end]+'\n'+row+updated[end:]
        elif count!=1: raise ValueError('중복 안건 표')
    finish=False if CONTINUOUS else (obj['finish'] or n>=MAX_ROUND)
    if finish and not obj['agreement'].strip(): raise ValueError('종료 합의안 누락')
    next_ai='사용자' if finish else next_speaker(ai)
    updated=updated.replace(f'**현재 차례: {ai}** · **회차: {n}**',f'**현재 차례: {next_ai}** · **회차: {n+1}**',1)
    updated+=f'\n\n---\n### [회차 {n}] {ai} · {now()}\n\n'+obj['body'].strip()+'\n'
    if obj['agreement'].strip():
        updated+=('\n**진행 중 합의 요약**\n\n' if CONTINUOUS else ('\n## 1차 합의안\n\n' if finish else '\n**1차 합의 초안**\n\n'))+obj['agreement'].strip()+'\n'
    return updated,finish
def invoke(ai,n,prompt):
    global CHILD
    outdir=ROOT/'자동진행기록'; outdir.mkdir(exist_ok=True)
    stamp=dt.datetime.now(KST).strftime('%Y%m%d_%H%M%S')+f'_{n}_{ai}'
    stdout_path=outdir/(stamp+'.stdout'); stderr_path=outdir/(stamp+'.stderr')
    result_path=outdir/(stamp+'.reply.json')
    if ai=='Claude':
        cli=latest_binary('anthropic.claude-code-*/resources/native-binary/claude.exe')
        args=[cli,'-p','--safe-mode','--tools','','--strict-mcp-config','--mcp-config','{"mcpServers":{}}',
              '--no-session-persistence','--output-format','json','--permission-prompts','none']
    else:
        cli=latest_binary('openai.chatgpt-*/bin/windows-x86_64/codex.exe')
        schema_path=ROOT/'応答schema.json'
        schema_path.write_text(json.dumps(SCHEMA,ensure_ascii=False),encoding='utf-8')
        args=[cli,'exec','--ignore-user-config','--skip-git-repo-check','--ephemeral',
              '--sandbox','read-only','-C',str(ROOT),'-c','features.shell_tool=false',
              '-c','features.apply_patch_freeform=false','-c','web_search="disabled"',
              '--output-schema',str(schema_path),'--output-last-message',str(result_path),'--json','-']
    with stdout_path.open('wb') as out, stderr_path.open('wb') as err:
        CHILD=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=out,stderr=err,cwd=ROOT,env=env(),
                               creationflags=subprocess.CREATE_NO_WINDOW)
        CHILD.stdin.write(prompt.encode('utf-8')); CHILD.stdin.close()
        start=time.monotonic()
        while CHILD.poll() is None:
            heartbeat()
            if STOP.exists(): kill_child(); raise RuntimeError('사용자가 중지했습니다.')
            if time.monotonic()-start>600: kill_child(); raise RuntimeError('차례 제한시간10분 초과')
            time.sleep(.5)
        rc=CHILD.returncode; CHILD=None
    stdout_text=stdout_path.read_text(encoding='utf-8',errors='replace')
    stderr_text=stderr_path.read_text(encoding='utf-8',errors='replace')
    if ai=='ChatGPT':
        for line in stdout_text.splitlines():
            try: event=json.loads(line)
            except ValueError:continue
            if event.get('type') in ('error','turn.failed') and usage_limit_message(json.dumps(event,ensure_ascii=False)):
                raise UsageLimitReached(json.dumps(event,ensure_ascii=False))
    if rc:
        if usage_limit_message(stdout_text+'\n'+stderr_text):raise UsageLimitReached((stdout_text+'\n'+stderr_text)[-3000:])
        raise RuntimeError(f'{ai} 호출 실패(exit {rc}). '+stderr_path.name)
    if ai=='Claude':
        envelope=json.loads(stdout_text)
        result_text=str(envelope.get('result',''))
        if envelope.get('is_error'):
            if usage_limit_message(json.dumps(envelope,ensure_ascii=False)):raise UsageLimitReached(json.dumps(envelope,ensure_ascii=False))
            raise RuntimeError('Claude 응답 오류: '+result_text[:250])
        raw=envelope.get('result','')
    else: raw=result_path.read_text(encoding='utf-8')
    if not str(raw).lstrip().startswith(('{','```')) and usage_limit_message(raw):raise UsageLimitReached(raw)
    obj=parse_reply(raw)
    result_path.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    return obj
def run(continuous=False):
    global RUNNING, CONTINUOUS, REPORTER
    if STOP.exists(): log('중지.txt가 있습니다. 재시작 전 삭제하세요.'); return
    try: fd=os.open(LOCK,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError: return
    os.write(fd,str(os.getpid()).encode()); os.close(fd); RUNNING=True
    try:
        CONTINUOUS=continuous
        if CONTINUOUS: REPORTER=HourlyReports()
        log('연속 토론 시작. 회차 제한 없음. 1시간마다 보고서를 저장·공유합니다.' if CONTINUOUS else '자동 토론 시작. 최대6회차.')
        while not STOP.exists():
            if not wait_for_usage():break
            heartbeat()
            original=DOC.read_text(encoding='utf-8'); ai,n=state(original)
            if ai=='Claude' and claude_paused() and not chatgpt_paused():
                original=original.replace(f'**현재 차례: Claude** · **회차: {n}**',f'**현재 차례: ChatGPT** · **회차: {n}**',1)
                DOC.write_text(original,encoding='utf-8')
                ai='ChatGPT'
                log('Claude 사용량 대기. 사용자 위임에 따라 ChatGPT가 계속 검토합니다.')
            if ai=='ChatGPT' and chatgpt_paused():
                original=original.replace(f'**현재 차례: ChatGPT** · **회차: {n}**',f'**현재 차례: Claude** · **회차: {n}**',1)
                DOC.write_text(original,encoding='utf-8')
                ai='Claude'
                log('사용자 지시로 ChatGPT 호출 유예. Claude 주도 검토를 이어갑니다.')
            if ai=='사용자': log('완료: 사용자 검토 차례입니다.'); break
            if not CONTINUOUS and n>MAX_ROUND: raise RuntimeError('최대 회차를 넘었습니다. 사용자 검토가 필요합니다.')
            before=hashlib.sha256(original.encode()).hexdigest()
            log(f'{n}회차 {ai} 답변 대기 중')
            shared_report=REPORTER.data['latest'] if REPORTER else None
            if REPORTER: REPORTER.receipt(ai,n,'요청에 본문 포함',report_path=shared_report)
            try:
                obj=invoke(ai,n,prompt_for(ai,n,original,RULES.read_text(encoding='utf-8')))
            except UsageLimitReached as e:
                if ai=='ChatGPT':
                    data=json.loads(ROLE_FILE.read_text(encoding='utf-8')) if ROLE_FILE.exists() else {}
                    data.update({'주담당':'Claude','ChatGPT재확인_epoch':time.time()+QUOTA_WAIT_SECONDS,'사유':'ChatGPT 사용량 소진. Claude 작업은 계속'})
                    ROLE_FILE.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
                    log('ChatGPT 사용량 소진.5시간 호출 유예하고 Claude 주도로 계속합니다.')
                    continue
                register_usage_wait(ai,n,e)
                log(f'{ai} 사용량 소진 감지. 차례를 보존하고5시간 대기로 전환합니다.')
                continue
            if STOP.exists(): raise RuntimeError('중지 요청으로 응답을 적용하지 않습니다.')
            if hashlib.sha256(DOC.read_text(encoding='utf-8').encode()).hexdigest()!=before:
                raise RuntimeError('사용자 또는 다른 AI의 편집 감지. 응답은 기록에 보관하고 토론문 반영은 중단합니다.')
            updated,finish=apply_reply(original,ai,n,obj)
            backup=ROOT/'자동진행기록'/f'회차{n}_반영전.md'; backup.write_text(original,encoding='utf-8')
            tmp=DOC.with_suffix('.tmp'); tmp.write_text(updated,encoding='utf-8'); tmp.replace(DOC)
            archive=ROOT/'회차별';archive.mkdir(exist_ok=True)
            (archive/f'{n:06d}_{ai}.md').write_text(f'# 회차 {n} {ai}\n\n'+obj['body']+'\n\n'+obj['agreement'],encoding='utf-8')
            if REPORTER: REPORTER.receipt(ai,n,'응답',obj['body'],report_path=shared_report)
            log(f'{n}회차 {ai} 기록 완료')
            if CONTINUOUS:
                from 현황문서 import render
                render(ROOT)
            if finish:
                (ROOT/'1차합의안.md').write_text('# 공통 평가 도구와 비교 모델 1차 합의안\n\n'+obj['agreement']+'\n',encoding='utf-8')
                log('완료: 1차합의안.md를 확인하세요.'); break
            # Wait a bounded ten seconds before the next turn; stop is checked throughout.
            for _ in range(4):  # 차례 사이 대기 10초 -> 2초 (응답 속도 개선)
                if STOP.exists(): break
                time.sleep(.5)
                heartbeat()
    except Exception as e: log('중단: '+str(e)); return False
    finally:
        kill_child(); RUNNING=False; LOCK.unlink(missing_ok=True)
    return True
def gui(autostart=False):
    import tkinter as tk
    from tkinter.scrolledtext import ScrolledText
    win=tk.Tk(); win.title('AI 연속 토론장  ChatGPT ↔ Claude  시간별 보고'); win.geometry('1060x800')
    bar=tk.Frame(win); bar.pack(fill='x',padx=12,pady=10)
    status=tk.StringVar(value='대기 중'); tk.Label(win,textvariable=status,anchor='w',font=('맑은 고딕',11)).pack(fill='x',padx=12)
    view=ScrolledText(win,wrap='word',font=('맑은 고딕',11)); view.pack(fill='both',expand=True,padx=12,pady=10)
    def start():
        if RUNNING or LOCK.exists():return
        STOP.unlink(missing_ok=True)
        threading.Thread(target=run,args=(True,),daemon=True).start()
    def stop(): STOP.write_text('사용자 중지',encoding='utf-8')
    tk.Button(bar,text='토론 시작 또는 이어가기',command=start).pack(side='left',padx=4)
    tk.Button(bar,text='중지',command=stop).pack(side='left',padx=4)
    tk.Button(bar,text='1차 합의안 열기',command=lambda:os.startfile(str(ROOT/'1차합의안.md')) if (ROOT/'1차합의안.md').exists() else None).pack(side='left',padx=4)
    tk.Button(bar,text='시간별 보고서 열기',command=lambda:os.startfile(str(ROOT/'최신_합의와이견_보고서.html')) if (ROOT/'최신_합의와이견_보고서.html').exists() else None).pack(side='left',padx=4)
    tk.Button(bar,text='공유 확인 기록',command=lambda:os.startfile(str(ROOT/'보고서_공유현황.md')) if (ROOT/'보고서_공유현황.md').exists() else None).pack(side='left',padx=4)
    tk.Button(bar,text='계좌 인수 현황 문서',command=lambda:os.startfile(str(ROOT/'계좌인수_토론현황.html')) if (ROOT/'계좌인수_토론현황.html').exists() else None).pack(side='left',padx=4)
    last=['']; report_version=[None]
    def refresh():
        try:
            status.set((ROOT/'진행상태.txt').read_text(encoding='utf-8') if (ROOT/'진행상태.txt').exists() else '대기 중')
            text=DOC.read_text(encoding='utf-8')
            report=ROOT/'최신_합의와이견_보고서.html'
            if report.exists():
                current=report.stat().st_mtime_ns
                if report_version[0] is not None and current!=report_version[0]:
                    win.bell();status.set('새 시간별 보고서가 저장됐습니다. 보고서 열기 버튼을 누르세요.')
                report_version[0]=current
            if text!=last[0]:
                view.configure(state='normal'); view.delete('1.0','end');view.insert('1.0',text);view.configure(state='disabled');view.see('end');last[0]=text
        except OSError: pass
        win.after(1000,refresh)
    def close():
        if RUNNING: stop(); kill_child()
        win.destroy()
    win.protocol('WM_DELETE_WINDOW',close);refresh()
    if autostart:win.after(500,start)
    win.mainloop()
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--run',action='store_true');ap.add_argument('--continuous-run',action='store_true');ap.add_argument('--autostart',action='store_true');a=ap.parse_args()
    if a.run or a.continuous_run: sys.exit(0 if run(a.continuous_run) else 1)
    else:gui(a.autostart)
