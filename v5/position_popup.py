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
        'trail': bool(pos.get('trail')), 'inherited': bool(pos.get('inherited')),
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


def data_script(positions, prices, last_sells=None, now=None, action=None):
    """종목별 상세(와 청산 서버 접속 정보)를 담은 JSON 스크립트 태그. '<' 를 이스케이프해 태그가 깨지지 않게 한다.

    action은 {'port': 포트, 'token': 토큰} 또는 None. None이면 화면에 청산 버튼이 나오지 않는다.
    """
    last_sells = last_sells or {}
    rows = {m: detail(m, p, prices.get(m), last_sells.get(m), now) for m, p in positions.items()}
    return _json_tag('posdata', rows) + (_json_tag('posaction', action) if action else '')


def row_attrs(market, dust=False):
    cls = 'clk dust' if dust else 'clk'
    return f' data-m="{html.escape(market, quote=True)}" class="{cls}" title="클릭하면 상세 정보"'


MODAL = """<div id="posmodal" hidden><div class="pm-card" role="dialog" aria-modal="true" aria-labelledby="pm-title">
<button type="button" class="pm-x" aria-label="닫기">×</button><h3 id="pm-title"></h3><div class="pm-sub" id="pm-sub"></div>
<table class="pm-t"><tbody id="pm-body"></tbody></table>
<div id="pm-act" hidden><div class="pm-row" id="pm-buyrow"><input type="number" id="pm-amt" min="5000" step="1000" aria-label="추가 매수 금액(원)"><span class="pm-won">원</span><button type="button" id="pm-buy" class="pm-btn">추가 매수</button></div>
<button type="button" id="pm-liq" class="pm-btn pm-danger" style="margin-top:8px">강제 청산</button>
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
#pm-act{margin-top:12px}.pm-row{display:flex;gap:8px;margin-top:8px}
.pm-btn{border:1px solid rgba(128,140,160,.5);background:none;color:inherit;border-radius:8px;padding:8px 14px;font-size:14px;cursor:pointer}
.pm-btn:disabled{opacity:.45;cursor:not-allowed}.pm-danger{background:#c34b43;border-color:#c34b43;color:#fff}
#pm-amt{width:110px;padding:7px 8px;border:1px solid rgba(128,140,160,.5);border-radius:8px;background:none;color:inherit;font-size:14px}.pm-won{align-self:center;font-size:14px}
#pm-q{font-size:14px;font-weight:600;line-height:1.5}#pm-res{font-size:13.5px;margin-top:8px;line-height:1.5}
</style>
<script>
(function(){
var el=document.getElementById('posdata'),box=document.getElementById('posmodal');
if(!el||!box)return;
var D={};try{D=JSON.parse(el.textContent)}catch(e){}
var A=null;try{A=JSON.parse(document.getElementById('posaction').textContent)}catch(e){}
var cur=null,busy=false,mode='liq',amt=0;
function $(i){return document.getElementById(i)}
function n(v,d){return v==null?'-':Number(v).toLocaleString('ko-KR',{maximumFractionDigits:d==null?0:d})}
function px(v){return v==null?'-':n(v,v<100?4:(v<1000?2:0))}
function sg(v,d){return v==null?'-':(v>0?'+':'')+n(v,d)}
function put(k,v,cls){var tr=document.createElement('tr'),a=document.createElement('td'),b=document.createElement('td');
a.textContent=k;b.textContent=v;if(cls)b.className=cls;tr.appendChild(a);tr.appendChild(b);document.getElementById('pm-body').appendChild(tr)}
function resetAct(x){busy=false;$('pm-ask').hidden=true;$('pm-res').textContent='';$('pm-liq').hidden=false;$('pm-buyrow').hidden=false;
$('pm-yes').disabled=false;$('pm-no').disabled=false;
if(!A){$('pm-act').hidden=true;$('pm-note').textContent='조회 전용 창입니다. 이 화면에서는 주문을 낼 수 없습니다.';return}
$('pm-act').hidden=false;$('pm-note').textContent='시장가 주문이며 되돌릴 수 없습니다. 주문은 봇이 실행권을 확인한 뒤 냅니다.';
var cap=A.buy_max||0;$('pm-amt').max=cap;$('pm-amt').value=cap?Math.min(30000,cap):'';$('pm-buy').disabled=!cap;
$('pm-liq').disabled=!x.sellable;if(!x.sellable)$('pm-res').textContent='평가액 5,000원 미만은 최소 주문금액 때문에 청산할 수 없습니다.'}
function back(){$('pm-ask').hidden=true;$('pm-liq').hidden=false;$('pm-buyrow').hidden=false}
function ask(kind){var x=D[cur];if(!x||busy)return;mode=kind;$('pm-res').textContent='';
if(kind==='buy'){amt=Number($('pm-amt').value);var cap=A.buy_max||0;
if(!Number.isInteger(amt)||amt<5000){$('pm-res').textContent='매수 금액은 5,000원 이상의 정수로 입력하세요.';return}
if(amt>cap){$('pm-res').textContent='1회 추가 매수 상한은 '+n(cap)+'원입니다.';return}
$('pm-q').textContent=x.name+'을(를) '+n(amt)+'원어치 시장가로 추가 매수합니다.'+(x.trail?' ⚠ 추가 매수하면 이 종목의 추적 손절·이익 보호가 꺼집니다(기존 규칙).':'')+' 추가 매수를 원하십니까?'}
else{$('pm-q').textContent=x.name+' 전량(평가액 약 '+n(x.value)+'원)을 시장가로 청산합니다. 청산을 원하십니까?'}
$('pm-liq').hidden=true;$('pm-buyrow').hidden=true;$('pm-ask').hidden=false}
function send(){if(!A||!cur||busy)return;busy=true;$('pm-yes').disabled=true;$('pm-no').disabled=true;$('pm-res').textContent='주문 요청 중… (최대 45초)';
var buy=mode==='buy',doc={token:A.token,market:cur,confirm:true};if(buy)doc.krw=amt;
fetch('http://127.0.0.1:'+A.port+(buy?'/buy':'/liquidate'),{method:'POST',headers:{'Content-Type':'text/plain'},body:JSON.stringify(doc)})
.then(function(r){return r.json()}).then(function(d){$('pm-ask').hidden=true;$('pm-res').textContent=(d.ok?'✅ ':'⚠️ ')+d.message+(d.ok?' (표는 30초 안에 갱신됩니다)':'')})
.catch(function(){$('pm-ask').hidden=true;$('pm-res').textContent='⚠️ 봇에 연결하지 못했습니다. 주문 여부를 주문 기록에서 확인하세요.'})}
function show(m){var x=D[m];if(!x)return;var same=(cur===m&&!box.hidden);cur=m;if(!same)resetAct(x);
document.getElementById('pm-title').textContent=x.name+' ('+m+')';
document.getElementById('pm-sub').textContent=(x.inherited?'계좌 인수 보유분':'봇 신규 매수')+' · '+x.entered+(x.held_hours==null?'':' · 보유 '+n(x.held_hours,1)+'시간');
document.getElementById('pm-body').textContent='';
var c=x.pnl==null?'':(x.pnl>=0?'pm-up':'pm-dn');
put('현재가',px(x.price));put('평균 매수가',px(x.avg));put('수량',n(x.qty,8));
put('매수금액',n(x.cost)+'원');put('평가액',x.value==null?'-':n(x.value)+'원');
put('평가손익',x.pnl==null?'-':sg(x.pnl)+'원 ('+sg(x.pnl_pct,2)+'%)',c);
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
var id=ev.target.id;if(id==='pm-liq')return ask('liq');if(id==='pm-buy')return ask('buy');if(id==='pm-no')return back();if(id==='pm-yes')return send();
var tr=ev.target.closest&&ev.target.closest('tr.clk');if(tr)show(tr.getAttribute('data-m'))});
document.addEventListener('keydown',function(ev){if(ev.key==='Escape')hide()});
try{var o=sessionStorage.getItem('posOpen');if(o&&D[o])show(o)}catch(e){}
})();
</script>"""
