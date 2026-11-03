"""Protection choices at the top of the local bot dashboard."""
import html
import json


def render(live, account_markets=(), action=None):
    if not live.cfg.get('protection_ui'): return ''
    try:
        policy=live.protection_policy
        doc=policy.read(); protected=live.protected_markets()
    except Exception:
        return '<section id="asset-protection-board"><h2>사용자 주문 보호</h2><p class="warn">보호 상태 확인 불가 · 새 봇 주문을 보류합니다.</p></section>'
    markets=sorted(set(account_markets)|set(live.d['positions'])|protected|set(doc.get('manual',{})))
    e=html.escape
    rows=[]; data={}
    pending=[]
    for market in markets:
        is_protected=market in protected
        manual=doc.get('manual',{}).get(market,{})
        revision=policy.revision(market,doc)
        data[market]={'protected':is_protected,'revision':revision}
        name=e(market[4:])
        button=(f'<button type="button" data-choice="unprotect" data-market="{e(market)}">보호 해제</button>' if is_protected
                else f'<button type="button" data-choice="protect" data-market="{e(market)}">보호하기</button>')
        keep=f'<button type="button" data-choice="protect" data-market="{e(market)}">보호 유지</button>'
        if is_protected and manual.get('pending'):
            pending.append(f'<div class="ap-pending"><b>{name}</b> 직접 주문/보유 확인 · 보호를 유지할까요? {keep} {button}</div>')
        rows.append(f'<tr><td><b>{name}</b></td><td>{"보호 중" if is_protected else "봇 관리 허용"}</td><td>{e(str(manual.get("kind") or "사용자 지정")) if is_protected else "다음 봇 판단부터 관리"}</td><td>{button}</td></tr>')
    inhibited=len(set(live.d['positions'])&protected)
    warning=f'<p class="warn">봇 장부 보유 {inhibited}개 종목의 매도·손절·익절도 중단됩니다.</p>' if inhibited else ''
    error=getattr(getattr(live,'_manual_detector',None),'error','')
    note='<p class="warn">직접 주문 확인 지연 · 새 주문의 출처를 확인 중입니다.</p>' if error else ''
    health={'checked_at':getattr(getattr(live,'_manual_detector',None),'last_global_success',0),'error':error}
    payload=json.dumps({'rows':data,'action':action,'health':health},ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    return f'''<section id="asset-protection-board"><h2 class="lg">사용자 직접 주문 보호 <span class="ap-chip">기본 자동 보호</span></h2>
<p class="sub">직접 주문 종목은 우선 보호합니다. 해제하면 봇 매수·매도·손절·익절을 허용하며, 새 직접 주문을 감지하면 다시 보호합니다.</p>
{warning}{note}{''.join(pending)}
<details><summary>보호 종목 관리 · {len(protected)}개 보호 중</summary><table><tr><th>종목</th><th>현재 상태</th><th>안내</th><th>선택</th></tr>{''.join(rows)}</table></details>
<div id="ap-confirm" hidden><b id="ap-question"></b><p>해제는 즉시 주문을 넣지 않습니다. 다음 봇 판단부터 관리하며, 보호 중에는 봇 손절도 하지 않습니다.</p><button type="button" id="ap-yes">보호 해제 확인</button> <button type="button" id="ap-no">취소</button></div>
<p id="ap-result" role="status">{'화면에서 보호 유지·해제를 선택할 수 있습니다.' if action else '보호 제어 연결을 기다리고 있습니다. 보호 상태는 유지됩니다.'}</p>
<script type="application/json" id="ap-data">{payload}</script>
<style>#asset-protection-board{{border:2px solid var(--acc,#0f6e5c)}}#asset-protection-board p{{line-height:1.6}}#asset-protection-board button{{padding:7px 12px;margin:3px;border:1px solid var(--line,#ddd);border-radius:7px;background:var(--card,#fff);color:inherit;cursor:pointer}}#asset-protection-board button:disabled{{opacity:.5;cursor:wait}}.ap-chip{{font-size:12px;background:#e0f3dc;color:#17212b;padding:4px 9px;border-radius:14px}}.ap-pending{{padding:8px 0;border-bottom:1px solid var(--line,#ddd)}}#ap-confirm{{padding:12px;background:rgba(220,150,30,.1);margin:10px 0}}#ap-confirm[hidden]{{display:none}}</style>
<script>{SCRIPT}</script></section>'''


SCRIPT=r'''(function(){
const board=document.getElementById('asset-protection-board'),raw=document.getElementById('ap-data');
if(!board||!raw)return;let D;try{D=JSON.parse(raw.textContent)}catch(e){return}
const out=document.getElementById('ap-result'),ask=document.getElementById('ap-confirm');let chosen=null,busy=false;
function clear(){chosen=null;ask.hidden=true;try{sessionStorage.removeItem('protectionAsk')}catch(e){}}
function choose(m){const r=D.rows[m];if(!r)return;chosen={market:m,revision:r.revision};ask.hidden=false;document.getElementById('ap-question').textContent=m.substring(4)+' 보호를 해제하고 봇 관리를 허용할까요?';try{sessionStorage.setItem('protectionAsk',JSON.stringify(chosen))}catch(e){}}
async function send(kind,m,rev){
if(busy)return;if(!D.action){out.textContent='봇의 보호 제어 연결을 확인한 뒤 새로고침하세요.';return}
busy=true;board.querySelectorAll('button').forEach(b=>b.disabled=true);out.textContent='보호 선택을 적용하고 있습니다.';
try{const r=await fetch('http://127.0.0.1:'+D.action.port+'/'+kind,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({market:m,token:D.action.token,confirm:true,revision:rev})});const data=await r.json();out.textContent=data.message||'보호 상태를 확인하세요.';if(data.ok){clear();setTimeout(()=>location.reload(),1500)}}catch(e){out.textContent='연결을 확인하고 화면을 새로고침하세요. 보호 해제가 성공했다고 가정하지 않습니다.'}
finally{busy=false;board.querySelectorAll('button').forEach(b=>b.disabled=false)}
}
board.addEventListener('click',function(e){const b=e.target.closest('button[data-choice]');if(!b)return;const m=b.dataset.market;if(!D.rows[m])return;if(b.dataset.choice==='unprotect')choose(m);else send('protect',m,D.rows[m].revision)});
document.getElementById('ap-no').onclick=clear;
document.getElementById('ap-yes').onclick=function(){if(chosen)send('unprotect',chosen.market,chosen.revision)};
try{const old=JSON.parse(sessionStorage.getItem('protectionAsk')||'null');if(old&&D.rows[old.market]&&D.rows[old.market].protected&&D.rows[old.market].revision===old.revision)choose(old.market);else sessionStorage.removeItem('protectionAsk')}catch(e){}
})();'''
