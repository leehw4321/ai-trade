"""봇 실거래 보유 종목을 클릭하면 상세 정보를 보여 주는 읽기 전용 팝업.

주문·설정·장부를 바꾸지 않는다. 화면은 정적 HTML(file://)이라 서버가 없으므로 버튼으로 주문을 보낼 수 없다.
상세 값은 페이지 안에 JSON으로 넣고, 30초마다 자동 새로고침되어도 열린 창이 유지되도록 sessionStorage에 종목을 기억한다.
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


def data_script(positions, prices, last_sells=None, now=None):
    """종목별 상세를 담은 JSON 스크립트 태그. '<' 를 이스케이프해 태그가 깨지지 않게 한다."""
    last_sells = last_sells or {}
    rows = {m: detail(m, p, prices.get(m), last_sells.get(m), now) for m, p in positions.items()}
    text = json.dumps(rows, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return f'<script type="application/json" id="posdata">{text}</script>'


def row_attrs(market, dust=False):
    cls = 'clk dust' if dust else 'clk'
    return f' data-m="{html.escape(market, quote=True)}" class="{cls}" title="클릭하면 상세 정보"'


MODAL = """<div id="posmodal" hidden><div class="pm-card" role="dialog" aria-modal="true" aria-labelledby="pm-title">
<button type="button" class="pm-x" aria-label="닫기">×</button><h3 id="pm-title"></h3><div class="pm-sub" id="pm-sub"></div>
<table class="pm-t"><tbody id="pm-body"></tbody></table>
<div class="pm-note">조회 전용 창입니다. 이 화면에서는 주문을 낼 수 없습니다.</div></div></div>
<style>
tr.clk{cursor:pointer}tr.clk:hover td{background:rgba(120,140,170,.14)}
#posmodal{position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;z-index:50;padding:16px}
#posmodal[hidden]{display:none}
.pm-card{background:var(--card,#fff);color:inherit;border-radius:12px;padding:18px 20px;max-width:420px;width:100%;position:relative;box-shadow:0 10px 40px rgba(0,0,0,.35);max-height:90vh;overflow:auto}
.pm-card h3{margin:0 0 2px;font-size:18px}.pm-sub{font-size:12.5px;opacity:.7;margin-bottom:10px}
.pm-x{position:absolute;top:8px;right:10px;border:0;background:none;font-size:24px;line-height:1;cursor:pointer;color:inherit}
.pm-t{width:100%;border-collapse:collapse;font-size:14px}.pm-t td{padding:6px 2px;border-bottom:1px solid rgba(128,140,160,.22)}
.pm-t td:last-child{text-align:right;font-variant-numeric:tabular-nums}.pm-note{font-size:12px;opacity:.65;margin-top:10px}
.pm-up{color:#c34b43}.pm-dn{color:#2f6fd0}
</style>
<script>
(function(){
var el=document.getElementById('posdata'),box=document.getElementById('posmodal');
if(!el||!box)return;
var D={};try{D=JSON.parse(el.textContent)}catch(e){}
function n(v,d){return v==null?'-':Number(v).toLocaleString('ko-KR',{maximumFractionDigits:d==null?0:d})}
function px(v){return v==null?'-':n(v,v<100?4:(v<1000?2:0))}
function sg(v,d){return v==null?'-':(v>0?'+':'')+n(v,d)}
function put(k,v,cls){var tr=document.createElement('tr'),a=document.createElement('td'),b=document.createElement('td');
a.textContent=k;b.textContent=v;if(cls)b.className=cls;tr.appendChild(a);tr.appendChild(b);document.getElementById('pm-body').appendChild(tr)}
function show(m){var x=D[m];if(!x)return;
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
var tr=ev.target.closest&&ev.target.closest('tr.clk');if(tr)show(tr.getAttribute('data-m'))});
document.addEventListener('keydown',function(ev){if(ev.key==='Escape')hide()});
try{var o=sessionStorage.getItem('posOpen');if(o&&D[o])show(o)}catch(e){}
})();
</script>"""
