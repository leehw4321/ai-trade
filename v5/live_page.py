"""현황 화면(status.html) 깜박임 없는 갱신 (2026-11-27 사용자: "주기적으로 화면이 깜박인다, 바뀐 텍스트만 바꿀 수 없나").

이전: 자동 새로고침이 location.reload()로 페이지 전체를 다시 그려 하얗게 깜박이고 스크롤이 튀었다.
지금: 봇이 status.html과 같은 내용을 status_live.js(window.__v5page("...") 한 줄)로도 쓴다. 화면은 주기마다 이 파일을
<script>로 읽어(file:// 화면에서도 동작) 새 문서와 지금 문서를 비교해 바뀐 글자·속성만 고친다.
펼침(open)·숨김(hidden)·입력값·버튼 잠금은 사용자/스크립트 상태라 건드리지 않는다.
버튼·입력칸·실행 스크립트가 새로 생기거나 실행 스크립트 내용이 바뀌면(이벤트 연결이 필요) 예전처럼 전체 새로고침한다.
읽어 오기에 실패해도 전체 새로고침한다. 주문 기능과 무관(표시만).
"""
import json
import os
import time
from pathlib import Path

JS_NAME = 'status_live.js'

# 새로고침 주기 선택(rf-iv)과 함께 쓰는 갱신 스크립트. 내용이 매번 같아야 한다(바뀌면 전체 새로고침으로 판단).
SCRIPT = r"""(function(){
var SKIP={open:1,hidden:1,disabled:1,value:1,checked:1,selected:1};
var ACT='button,input,select,textarea,[onclick],script:not([type="application/json"])';
function keep(e){return e.id==='posmodal'||e.id==='ap-confirm'||e.hasAttribute('data-live-keep')}
function made(e){if(e.nodeType===1&&e.tagName==='SCRIPT'&&(e.getAttribute('src')||'').indexOf('status_live.js')===0)return true;return e.nodeType===1&&e.tagName==='BUTTON'&&e.className==='tg'&&e.parentNode&&e.parentNode.classList&&e.parentNode.classList.contains('wf')}
function kids(e){var a=[];for(var c=e.firstChild;c;c=c.nextSibling){if(c.nodeType===8||made(c))continue;a.push(c)}return a}
function act(n){return n.nodeType===1&&(n.matches(ACT)||!!n.querySelector(ACT))}
function same(a,b){return a.nodeType===b.nodeType&&(a.nodeType!==1||a.tagName===b.tagName)}
function json(e){return (e.getAttribute('type')||'')==='application/json'}
function attrs(a,b,ap){
 if(a.tagName==='HTML')return;var wf=a.classList.contains('wf'),i,n;
 for(i=0;i<b.attributes.length;i++){n=b.attributes[i];if(SKIP[n.name]||(wf&&n.name==='class'))continue;if(a.getAttribute(n.name)!==n.value&&ap)a.setAttribute(n.name,n.value)}
 for(i=a.attributes.length-1;i>=0;i--){n=a.attributes[i].name;if(SKIP[n]||(wf&&n==='class'))continue;if(!b.hasAttribute(n)&&ap)a.removeAttribute(n)}}
function m(a,b,ap){
 if(a.nodeType!==1){if(a.nodeValue!==b.nodeValue&&ap)a.nodeValue=b.nodeValue;return true}
 if(keep(a))return true;
 if(a.tagName==='SCRIPT'){if(json(a)&&json(b)){if(ap&&a.textContent!==b.textContent)a.textContent=b.textContent;return true}return a.textContent===b.textContent}
 attrs(a,b,ap);
 if(a.tagName==='SELECT')return true;
 var x=kids(a),y=kids(b),i,k=Math.min(x.length,y.length);
 for(i=0;i<k;i++){if(same(x[i],y[i])){if(!m(x[i],y[i],ap))return false}else{if(act(x[i])||act(y[i]))return false;if(ap)a.replaceChild(document.importNode(y[i],true),x[i])}}
 for(i=k;i<y.length;i++){if(act(y[i]))return false;if(ap)a.appendChild(document.importNode(y[i],true))}
 for(i=k;i<x.length;i++){if(act(x[i]))return false;if(ap)a.removeChild(x[i])}
 return true}
window.__v5morph=function(h){var d=new DOMParser().parseFromString(h,'text/html'),o=document.documentElement,n=d.documentElement;
 if(!m(o,n,false))return false;m(o,n,true);return true};
window.__v5soft=function(done){var s=document.createElement('script'),fin=false;
 function full(){if(!fin){fin=true;location.reload()}}
 window.__v5page=function(h){if(fin)return;fin=true;s.remove();var ok=false;try{ok=window.__v5morph(h)}catch(e){ok=false}if(!ok){location.reload();return}if(done)done()};
 s.onerror=full;s.onload=function(){setTimeout(function(){if(!fin)full()},0);s.remove()};s.src='status_live.js?t='+Date.now();document.head.appendChild(s)};
})();"""


def write_js(page, data_dir):
    """status.html과 같은 내용을 status_live.js로 쓴다(임시 파일 후 교체). 실패해도 화면은 전체 새로고침으로 대신한다."""
    data_dir = Path(data_dir)
    tmp = data_dir / f'status_live.{os.getpid()}.tmp'
    try:
        tmp.write_text('window.__v5page&&window.__v5page(' + json.dumps(page, ensure_ascii=False) + ');\n', encoding='utf-8')
        for attempt in range(5):
            try:
                os.replace(tmp, data_dir / JS_NAME)
                return True
            except OSError:
                if attempt == 4: raise
                time.sleep(.2)
    except OSError:
        return False
    finally:
        try: tmp.unlink()
        except OSError: pass
