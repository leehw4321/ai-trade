"""봇 실거래 보유 종목을 클릭하면 상세 정보를 보여 주고, 설정으로 켠 경우에만 '강제 청산'·'추가 매수' 버튼을 둔다.

화면은 정적 HTML(file://)이다. 상세 값은 페이지 안에 JSON으로 넣고, 30초마다 자동 새로고침되어도 열린 창이 유지되도록
sessionStorage에 종목을 기억한다. 청산·추가 매수는 확인 질문 '[예][아니오]'를 거쳐 manual_orders의 127.0.0.1 서버로만 보낸다.
"""
import html
import json
import time


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float('inf') else None


def detail(market, pos, price, last_sell=None, now=None):
    """한 종목의 상세 값. 가격이 없으면 가격 의존 값은 None으로 두고 0으로 꾸미지 않는다."""
    now = time.time() if now is None else now
    qty, cost = _num(pos.get('qty')), _num(pos.get('cost'))
    price = _num(price)
    price = price if price and price > 0 else None
    stop = _num(pos.get('stop'))
    value = qty * price if qty is not None and price else None
    avg = cost / qty if cost is not None and qty else None
    out = {
        'name': market[4:], 'qty': qty, 'cost': cost, 'avg': avg, 'price': price, 'value': value,
        'pnl': value - cost if value is not None and cost is not None else None,
        'pnl_pct': (value / cost - 1) * 100 if value is not None and cost else None,
        'stop': stop,
        'stop_gap_pct': (price / stop - 1) * 100 if price and stop else None,
        'peak': _num(pos.get('peak')), 'atr': _num(pos.get('atr')),
        'trail': bool(pos.get('trail')), 'inherited': bool(pos.get('inherited')), 'user': bool(pos.get('user_trade')),
        'adoption_price': _num(pos.get('adoption_price')),
        'entered': str(pos.get('time') or '')[:16].replace('T', ' '),
        'held_hours': None, 'last_sell': None,
        'sellable': value is not None and value >= 5000,  # 업비트 최소 주문금액
    }
    try:
        import datetime as dt
        t0 = dt.datetime.fromisoformat(str(pos.get('time')))
        out['held_hours'] = max((now - t0.timestamp()) / 3600, 0)
    except (TypeError, ValueError):
        pass
    if isinstance(last_sell, dict) and _num(last_sell.get('time')):
        out['last_sell'] = {'ago_min': max((now - last_sell['time']) / 60, 0),
                            'price': _num(last_sell.get('price')), 'profit': bool(last_sell.get('profit')),
                            'reason': str(last_sell.get('reason') or '')}
    return out


def _json_tag(tag_id, value):
    text = json.dumps(value, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return f'<script type="application/json" id="{tag_id}">{text}</script>'


def outside_detail(market, row, protected, revision):
    """봇 장부 밖 직접 보유(사용자 보호 등). 주문 버튼 대신 '봇 장부로 넘기기'만 둔다(2026-11-28 사용자 지시)."""
    qty, cost, price = _num(row.get('qty')), _num(row.get('cost')), _num(row.get('price'))
    value = _num(row.get('value'))
    return {'name': market[4:], 'outside': True, 'protected': bool(protected), 'revision': revision,
            'qty': qty, 'cost': cost, 'avg': cost / qty if cost and qty else None, 'price': price, 'value': value,
            'pnl': value - cost if value is not None and cost else None,
            'pnl_pct': (value / cost - 1) * 100 if value is not None and cost else None,
            'sellable': value is not None and value >= 5000}


def data_script(positions, prices, last_sells=None, now=None, action=None, outside=None, prot=None, rests=None):
    """종목별 상세(와 청산 서버 접속 정보)를 담은 JSON 스크립트 태그. '<' 를 이스케이프해 태그가 깨지지 않게 한다.

    action은 {'port': 포트, 'token': 토큰} 또는 None. None이면 화면에 청산 버튼이 나오지 않는다.
    """
    last_sells = last_sells or {}
    rows = {m: detail(m, p, prices.get(m), last_sells.get(m), now) for m, p in positions.items()}
    for m, (is_prot, rev) in (prot or {}).items():
        if m in rows: rows[m].update(protected=bool(is_prot), revision=rev)
    for m, x in (outside or {}).items():
        if m not in rows: rows[m] = x
    for m, r in (rests or {}).items():
        if m in rows: rows[m]['rest'] = r
    return _json_tag('posdata', rows) + (_json_tag('posaction', action) if action else '')


def row_attrs(market, dust=False):
    cls = 'clk dust' if dust else 'clk'
    return f' data-m="{html.escape(market, quote=True)}" class="{cls}" title="클릭하면 상세 정보"'


MODAL = """<div id="posmodal" hidden><div class="pm-card" role="dialog" aria-modal="true" aria-labelledby="pm-title">
<button type="button" class="pm-x" aria-label="닫기">×</button><h3 id="pm-title"></h3><div class="pm-sub" id="pm-sub"></div>
<table class="pm-t"><tbody id="pm-body"></tbody></table>
<div id="pm-adopt" hidden><button type="button" id="pm-adopt-btn" class="pm-btn" style="background:#0f6e5c;border-color:#0f6e5c;color:#fff">봇 장부에 올리기(보호 유지)</button></div>
<div id="pm-hand" hidden><button type="button" id="pm-hand-btn" class="pm-btn" style="background:#0f6e5c;border-color:#0f6e5c;color:#fff">봇에게 매매 권한 넘기기</button></div>
<div id="pm-act" hidden><div class="pm-row" id="pm-buyrow"><input type="number" id="pm-amt" min="5000" step="1000" aria-label="추가 매수 금액(원)"><span class="pm-won">원</span><button type="button" id="pm-buy" class="pm-btn">추가 매수</button></div>
<div class="pm-seg" id="pm-pct" role="radiogroup" aria-label="매도 비율"><label><input type="radio" name="pmpct" value="25">25%</label><label><input type="radio" name="pmpct" value="50">50%</label><label><input type="radio" name="pmpct" value="75">75%</label><label><input type="radio" name="pmpct" value="100" checked>전량</label><label><input type="radio" name="pmpct" value="0">지정량</label></div>
<div class="pm-row" id="pm-qrow" hidden><span class="pm-won">매도 수량</span><input id="pm-qty" inputmode="decimal" aria-label="매도 수량(개)" style="width:140px;padding:7px 8px;border:1px solid rgba(128,140,160,.5);border-radius:8px;background:none;color:inherit;font-size:14px;text-align:right"><span class="pm-won">개</span><button type="button" id="pm-qmax" class="pm-btn pm-step">전부</button></div>
<div id="pm-qnote" class="pm-note" style="margin-top:4px"></div>
<div class="pm-seg" id="pm-ord" role="radiogroup" aria-label="주문 방식"><label><input type="radio" name="pmord" value="market" checked>시장가</label><label><input type="radio" name="pmord" value="limit">지정가(희망 매도가)</label></div>
<div class="pm-row" id="pm-lp" hidden><span class="pm-won">희망 매도가</span><button type="button" id="pm-dn" class="pm-btn pm-step">▼</button><input id="pm-px" inputmode="decimal" aria-label="희망 매도가"><button type="button" id="pm-up" class="pm-btn pm-step">▲</button></div>
<div id="pm-diff" class="pm-note" style="margin-top:4px"></div>
<div class="pm-row" id="pm-restrow" hidden><span id="pm-rest" style="align-self:center;font-size:13.5px"></span><button type="button" id="pm-rcancel" class="pm-btn">지정가 취소</button></div>
<button type="button" id="pm-liq" class="pm-btn pm-danger" style="margin-top:8px">매도</button>
<div id="pm-ask" hidden><div id="pm-q"></div><div class="pm-row"><button type="button" id="pm-yes" class="pm-btn pm-danger">예, 청산합니다</button><button type="button" id="pm-no" class="pm-btn">아니오</button></div></div>
<div id="pm-res" role="status"></div></div>
<div class="pm-note" id="pm-note">조회 전용 창입니다. 이 화면에서는 주문을 낼 수 없습니다.</div></div></div>
<style>
tr.clk{cursor:pointer}tr.clk:hover td{background:rgba(120,140,170,.14)}
#posmodal{position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;z-index:50;padding:16px}
#posmodal[hidden],#posmodal [hidden]{display:none!important}
.pm-card{background:var(--card,#fff);color:inherit;border-radius:12px;padding:18px 20px;max-width:420px;width:100%;position:relative;box-shadow:0 10px 40px rgba(0,0,0,.35);max-height:90vh;overflow:auto}
.pm-card h3{margin:0 0 2px;font-size:18px}.pm-sub{font-size:12.5px;opacity:.7;margin-bottom:10px}
.pm-x{position:absolute;top:8px;right:10px;border:0;background:none;font-size:24px;line-height:1;cursor:pointer;color:inherit}
.pm-t{width:100%;border-collapse:collapse;font-size:14px}.pm-t td{padding:6px 2px;border-bottom:1px solid rgba(128,140,160,.22)}
.pm-t td:last-child{text-align:right;font-variant-numeric:tabular-nums}.pm-note{font-size:12px;opacity:.65;margin-top:10px}
.pm-up{color:#c34b43}.pm-dn{color:#2f6fd0}
.pm-seg{display:flex;gap:12px;margin-top:8px;font-size:14px;flex-wrap:wrap}.pm-seg label{cursor:pointer}
#pm-act{margin-top:12px}.pm-row{display:flex;gap:8px;margin-top:8px}
.pm-btn{border:1px solid rgba(128,140,160,.5);background:none;color:inherit;border-radius:8px;padding:8px 14px;font-size:14px;cursor:pointer}
.pm-btn:disabled{opacity:.45;cursor:not-allowed}.pm-danger{background:#c34b43;border-color:#c34b43;color:#fff}
#pm-amt{width:110px;padding:7px 8px;border:1px solid rgba(128,140,160,.5);border-radius:8px;background:none;color:inherit;font-size:14px}.pm-won{align-self:center;font-size:14px}
#pm-px{width:120px;padding:7px 8px;border:1px solid rgba(128,140,160,.5);border-radius:8px;background:none;color:inherit;font-size:14px;text-align:right}.pm-step{padding:6px 10px}
#pm-q{font-size:14px;font-weight:600;line-height:1.5}#pm-res{font-size:13.5px;margin-top:8px;line-height:1.5}
</style>
<script>
(function(){
var el=document.getElementById('posdata'),box=document.getElementById('posmodal');
if(!el||!box)return;
var D={};try{D=JSON.parse(el.textContent)}catch(e){}
var A=null;try{A=JSON.parse(document.getElementById('posaction').textContent)}catch(e){}
var cur=null,busy=false,mode='liq',amt=0,sp=100,so='market';
var TK=[[1000000,1000],[500000,500],[100000,100],[50000,50],[10000,10],[5000,5],[100,1],[10,.1],[1,.01],[.1,.001],[.01,.0001],[.001,.00001],[.0001,.000001],[.00001,.0000001]];
function tk(p){for(var i=0;i<TK.length;i++)if(p>=TK[i][0])return TK[i][1];return 1e-8}
function dg(t){var s=String(t);return s.indexOf('e-')>0?parseInt(s.split('e-')[1],10):(s.split('.')[1]||'').length}
function snap(p){var t=tk(p);return Number((Math.floor(p/t+1e-9)*t).toFixed(dg(t)))}
function fp(p){return Number(p).toLocaleString('ko-KR',{maximumFractionDigits:dg(tk(p))})}
function lpx(){return Number(String($('pm-px').value).replace(/,/g,''))}
function ldiff(){var x=D[cur],p=lpx(),b=x&&x.price;if(!(p>0)||!b){$('pm-diff').textContent='';return}var n=Math.round((p-b)/tk(Math.min(p,b)));
$('pm-diff').textContent='현재가 '+fp(b)+'원 대비 '+(p>=b?'+':'')+((p/b-1)*100).toFixed(2)+'% · '+(n>0?'+':'')+n+'칸'+(x.avg?' · 평단 대비 '+((p/x.avg-1)*100).toFixed(2)+'%':'')}
function lstep(d){var b=D[cur]&&D[cur].price,p=lpx()||b;if(!p)return;var t=d>0?tk(p):tk(p-tk(p)*0.5);var q=snap(p+d*t);if(q>0){$('pm-px').value=fp(q);ldiff()}}
function qv(){return Number(String($('pm-qty').value).replace(/,/g,''))}
function qshow(){var on=pick('pmpct')==='0',x=D[cur];$('pm-qrow').hidden=!on;if(!on||!x){$('pm-qnote').textContent='';return}var q=qv(),p=(pick('pmord')==='limit'?lpx():0)||x.price||0;
$('pm-qnote').textContent=q>0?('약 '+n(q*p)+'원 · 보유 '+n(x.qty,8)+'개의 '+(x.qty?(q/x.qty*100).toFixed(1):'-')+'%'+(q>x.qty?' (보유보다 많아 전량으로 처리)':'')):'보유 '+n(x.qty,8)+'개'}
function lshow(){var lim=pick('pmord')==='limit';$('pm-lp').hidden=!lim;if(lim){if(!lpx()&&D[cur]&&D[cur].price)$('pm-px').value=fp(snap(D[cur].price));ldiff()}else $('pm-diff').textContent=''}
function pick(name){var r=document.querySelectorAll('input[name='+name+']');for(var i=0;i<r.length;i++)if(r[i].checked)return r[i].value;return null}
function $(i){return document.getElementById(i)}
function n(v,d){return v==null?'-':Number(v).toLocaleString('ko-KR',{maximumFractionDigits:d==null?0:d})}
function px(v){return v==null?'-':n(v,v<100?4:(v<1000?2:0))}
function sg(v,d){return v==null?'-':(v>0?'+':'')+n(v,d)}
function put(k,v,cls){var tr=document.createElement('tr'),a=document.createElement('td'),b=document.createElement('td');
a.textContent=k;b.textContent=v;if(cls)b.className=cls;tr.appendChild(a);tr.appendChild(b);document.getElementById('pm-body').appendChild(tr)}
function resetAct(x){busy=false;$('pm-ask').hidden=true;$('pm-res').textContent='';$('pm-liq').hidden=false;$('pm-buyrow').hidden=false;$('pm-pct').hidden=false;$('pm-ord').hidden=false;
$('pm-yes').disabled=false;$('pm-no').disabled=false;$('pm-adopt').hidden=true;$('pm-hand').hidden=true;
if(x.outside&&A){$('pm-adopt').hidden=false;$('pm-adopt-btn').disabled=!x.sellable||x.revision==null}
if(!x.outside&&x.protected&&A){$('pm-hand').hidden=false;$('pm-hand-btn').disabled=x.revision==null}
if(!A){$('pm-act').hidden=true;$('pm-note').textContent='조회 전용 창입니다. 이 화면에서는 주문을 낼 수 없습니다.';return}
$('pm-act').hidden=false;$('pm-note').textContent=(x.outside?'봇 장부 밖 보유입니다. [봇 장부에 올리기]는 주문 없이 업비트 잔고 전부·평균 매수가 그대로 장부에 올리고 손절가를 현재가 기준(최대 -3.5%)으로 잡습니다. 보호는 그대로라 봇은 매매하지 않습니다. ':'')+((x.outside||x.protected)?'사용자 보호 중: 봇은 자동으로 손절·익절·매도하지 않습니다. 아래 추가 매수·매도는 사용자 직접 주문(시장가)으로 처리되고 봇 손익과 따로 기록됩니다. 봇에게 맡기려면 [봇에게 매매 권한 넘기기]. ':'')+'주문은 되돌릴 수 없습니다(지정가는 대기 중 취소 버튼이 없고 기한 만료 때 취소됩니다). 주문은 봇이 실행권을 확인한 뒤 냅니다.';
var cap=A.buy_max||0;$('pm-amt').max=cap;$('pm-amt').value=cap?Math.min(30000,cap):'';$('pm-buy').disabled=!cap;
$('pm-liq').disabled=!x.sellable;if(!x.sellable)$('pm-res').textContent='평가액 5,000원 미만은 최소 주문금액 때문에 청산할 수 없습니다.';
$('pm-px').value=x.price?fp(snap(x.price)):'';$('pm-qty').value='';lshow();qshow();
var r=x.rest;$('pm-restrow').hidden=!r;if(r){var left=Math.max(0,Math.round(((r.deadline||0)*1000-Date.now())/60000));$('pm-rest').textContent='걸어 둔 지정가 '+(r.side==='ask'?'매도':'매수')+' '+fp(r.price)+'원 · '+(left>43200?'취소 전까지 유지':'약 '+left+'분 남음');$('pm-rcancel').hidden=!r.cancel}}
function back(){var x=D[cur];if(x&&x.outside)$('pm-adopt').hidden=false;if(x&&!x.outside&&x.protected)$('pm-hand').hidden=false;$('pm-ask').hidden=true;$('pm-liq').hidden=false;$('pm-buyrow').hidden=false;$('pm-pct').hidden=false;$('pm-ord').hidden=false;$('pm-restrow').hidden=!(x&&x.rest);lshow();qshow()}
function ask(kind){var x=D[cur];if(!x||busy)return;mode=kind;$('pm-res').textContent='';
function pre(){return (x.outside?'봇 장부에 올린 뒤(보호 유지) ':'')+((x.outside||x.protected)?'사용자 직접 주문으로 ':'')}
if(kind==='rcancel'){$('pm-q').textContent=x.name+' 걸어 둔 지정가 주문을 취소할까요? 체결된 부분은 그대로이고 남은 것은 시장가로 바꾸지 않습니다.';$('pm-yes').textContent='예, 취소합니다';$('pm-restrow').hidden=true;$('pm-adopt').hidden=true;$('pm-hand').hidden=true;$('pm-liq').hidden=true;$('pm-buyrow').hidden=true;$('pm-pct').hidden=true;$('pm-ord').hidden=true;$('pm-lp').hidden=true;$('pm-ask').hidden=false;return}
if(kind==='adopt'||kind==='hand'){$('pm-q').textContent=kind==='adopt'?x.name+' '+n(x.qty,8)+'개(평가액 약 '+n(x.value)+'원)를 봇 장부에 올립니다. 주문은 나가지 않고 사용자 보호는 그대로라 봇은 아직 매매하지 않습니다. 올리시겠습니까?':x.name+'의 매수·매도 권한을 봇에게 넘깁니다(보호 해제). 다음 판단부터 봇이 손절·이익보호·순위 매도로 관리하며 곧 팔릴 수 있습니다. 넘기시겠습니까?';$('pm-yes').textContent=kind==='adopt'?'예, 올립니다':'예, 권한을 넘깁니다';$('pm-hand').hidden=true;$('pm-adopt').hidden=true;$('pm-liq').hidden=true;$('pm-buyrow').hidden=true;$('pm-pct').hidden=true;$('pm-ord').hidden=true;$('pm-ask').hidden=false;return}
$('pm-yes').textContent='예, 청산합니다';
if(kind==='buy'){amt=Number($('pm-amt').value);var cap=A.buy_max||0;
if(!Number.isInteger(amt)||amt<5000){$('pm-res').textContent='매수 금액은 5,000원 이상의 정수로 입력하세요.';return}
if(amt>cap){$('pm-res').textContent='1회 추가 매수 상한은 '+n(cap)+'원입니다.';return}
$('pm-q').textContent=pre()+x.name+'을(를) '+n(amt)+'원어치 시장가로 추가 매수합니다.'+(x.trail?' ⚠ 추가 매수하면 이 종목의 추적 손절·이익 보호가 꺼집니다(기존 규칙).':'')+' 추가 매수를 원하십니까?'}
else{sp=Number(pick('pmpct'));if(!(sp>=0))sp=100;so=pick('pmord')==='limit'?'limit':'market';
var sq=qv();if(sp===0&&!(sq>0)){$('pm-res').textContent='매도 수량(개)을 입력하세요.';return}
var lp=lpx();if(so==='limit'&&!(lp>0&&snap(lp)===lp)){$('pm-res').textContent='희망 매도가가 호가 단위에 맞지 않습니다(▲▼로 조절)';return}
$('pm-q').textContent=pre()+x.name+' '+(sp===100?'전량':sp===0?'지정량 '+n(Math.min(sq,x.qty),8)+'개':sp+'%')+'(평가액 약 '+n(sp===0?Math.min(sq,x.qty)*(x.price||0):(x.value||0)*sp/100)+'원)을 '+(so==='limit'?fp(lp)+'원 지정가로 걸어 둡니다('+$('pm-diff').textContent+'). 팔리거나 [지정가 취소]를 누를 때까지 걸려 있습니다(대기 시간 없음, 시장가로 바꾸지 않음).'+(x.protected||x.outside?'':' 봇 관리 종목이라 손절선에 가까워지면 안전을 위해 취소 후 시장가로 팝니다.'):'시장가로 매도합니다.')+' 매도를 원하십니까?'}
$('pm-liq').hidden=true;$('pm-buyrow').hidden=true;$('pm-pct').hidden=true;$('pm-ord').hidden=true;$('pm-lp').hidden=true;$('pm-qrow').hidden=true;$('pm-adopt').hidden=true;$('pm-hand').hidden=true;$('pm-ask').hidden=false}
function send(){if(!A||!cur||busy)return;busy=true;$('pm-yes').disabled=true;$('pm-no').disabled=true;$('pm-res').textContent='주문 요청 중… (최대 45초)';
var buy=mode==='buy',doc={token:A.token,market:cur,confirm:true};if(mode==='adopt')doc.revision=D[cur].revision;else if(buy)doc.krw=amt;else{doc.pct=sp;doc.ord=so;if(so==='limit')doc.price=lpx();if(sp===0)doc.qty=qv()}
function post(path,body){return fetch('http://127.0.0.1:'+A.port+path,{method:'POST',headers:{'Content-Type':'text/plain'},body:JSON.stringify(body)}).then(function(r){return r.json()})}
var x=D[cur],steps=[],done=[];
var rv={token:A.token,market:cur,confirm:true,revision:x.revision};
if(mode==='adopt')steps.push(['/adopt',rv]);
else if(mode==='hand')steps.push(['/unprotect',rv]);
else if(mode==='rcancel')steps.push(['/user_rest_cancel',{token:A.token,market:cur,confirm:true}]);
else{if(x.outside)steps.push(['/adopt',rv]);steps.push(!buy&&so==='limit'?['/user_limit_sell',doc]:(x.outside||x.protected)?[buy?'/user_buy':'/user_sell',doc]:[buy?'/buy':'/liquidate',doc])}
function run(i){if(i>=steps.length)return Promise.resolve();$('pm-res').textContent='처리 중 '+(i+1)+'/'+steps.length+'…';
return post(steps[i][0],steps[i][1]).then(function(d){done.push((d.ok?'✅ ':'⚠️ ')+d.message);if(!d.ok&&i<steps.length-1){done.push('→ 다음 단계는 하지 않았습니다.');return}return run(i+1)})}
run(0).then(function(){$('pm-ask').hidden=true;$('pm-res').textContent=done.join(' ')+' (표는 30초 안에 갱신됩니다)'})
.catch(function(){$('pm-ask').hidden=true;$('pm-res').textContent='⚠️ 봇에 연결하지 못했습니다. 주문 여부를 주문 기록에서 확인하세요.'})}
function show(m){try{D=JSON.parse(el.textContent)}catch(e){}var x=D[m];if(!x)return;var same=(cur===m&&!box.hidden);cur=m;if(!same)resetAct(x);
document.getElementById('pm-title').textContent=x.name+' ('+m+')';
document.getElementById('pm-sub').textContent=x.outside?('봇 장부 밖 직접 보유'+(x.protected?' · 사용자 보호 중(봇이 손대지 않음)':'')):(x.user?'사용자 직접 주문':x.inherited?'계좌 인수 보유분':'봇 신규 매수')+' · '+x.entered+(x.held_hours==null?'':' · 보유 '+n(x.held_hours,1)+'시간');
document.getElementById('pm-body').textContent='';
var c=x.pnl==null?'':(x.pnl>=0?'pm-up':'pm-dn');
put('현재가',px(x.price));put('평균 매수가',px(x.avg));put('수량',n(x.qty,8));
put('매수금액',n(x.cost)+'원');put('평가액',x.value==null?'-':n(x.value)+'원');
put('평가손익',x.pnl==null?'-':sg(x.pnl)+'원 ('+sg(x.pnl_pct,2)+'%)',c);
if(x.outside){box.hidden=false;try{sessionStorage.setItem('posOpen',m)}catch(e){}return}
put('손절가',px(x.stop));put('손절까지 여유',x.stop_gap_pct==null?'-':n(x.stop_gap_pct,2)+'%');
put('보유 중 최고가',px(x.peak));put('추적 손절',x.trail?'켜짐':'꺼짐');
if(x.atr!=null)put('ATR(변동폭)',px(x.atr));
if(x.adoption_price!=null)put('인수 시점 가격',px(x.adoption_price));
if(x.last_sell)put('직전 매도',n(x.last_sell.ago_min,0)+'분 전 · '+px(x.last_sell.price)+' · '+(x.last_sell.profit?'이익':'손실')+(x.last_sell.reason?' · '+x.last_sell.reason:''));
box.hidden=false;try{sessionStorage.setItem('posOpen',m)}catch(e){}}
function hide(){box.hidden=true;try{sessionStorage.removeItem('posOpen')}catch(e){}}
document.addEventListener('click',function(ev){
if(ev.target.closest&&ev.target.closest('.pm-x'))return hide();
if(ev.target===box)return hide();
var id=ev.target.id;if(id==='pm-liq')return ask('liq');if(id==='pm-buy')return ask('buy');if(id==='pm-adopt-btn')return ask('adopt');if(id==='pm-hand-btn')return ask('hand');if(id==='pm-qmax'){var xx=D[cur];if(xx)$('pm-qty').value=xx.qty;return qshow()}if(id==='pm-up')return lstep(1);if(id==='pm-dn')return lstep(-1);if(id==='pm-rcancel')return ask('rcancel');if(id==='pm-no')return back();if(id==='pm-yes')return send();
var tr=ev.target.closest&&ev.target.closest('tr.clk');if(tr)show(tr.getAttribute('data-m'))});
document.addEventListener('keydown',function(ev){if(ev.key==='Escape')hide()});
document.addEventListener('change',function(ev){if(ev.target&&ev.target.name==='pmord'){lshow();qshow()}if(ev.target&&ev.target.name==='pmpct')qshow()});
document.addEventListener('input',function(ev){if(ev.target&&ev.target.id==='pm-px'){ldiff();qshow()}if(ev.target&&ev.target.id==='pm-qty')qshow()});
try{var o=sessionStorage.getItem('posOpen');if(o&&D[o])show(o)}catch(e){}
})();
</script>"""
