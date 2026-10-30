let exchangeDraft=null,exchangeTicket=null,exchangeBusy=false;
function renderExchangeOrders(p){
  const x=p.exchange||{orders:[],requests:[],time:0,stale:true};
  $('exchange-status').textContent=(x.time?'업비트 전체 미체결 '+x.orders.length+'건 · '+tm(x.time)+' 조회':'첫 조회 대기')+' · 5초 주기'+(x.error?' · '+x.error:x.stale?' · 갱신 지연':'');
  $('exchange-status').className=(x.error||x.stale?'warn':'mu')+' small';
  $('exchange-open-orders').innerHTML=table(['주문 시각','종목 / 출처','방향 / 유형','지정가','요청 / 체결 / 남은 수량','묶인 자산','상태','작업'],x.orders.map(o=>{
    const pending=x.requests.some(r=>r.uuid===o.uuid&&['submitting','unknown','accepted'].includes(r.status));
    const editable=o.market.startsWith('KRW-')&&o.ord_type==='limit'&&o.state==='wait';
    const unit=o.market.split('-')[0],type={limit:'지정가',price:'시장가 매수',market:'시장가 매도',best:'최유리'}[o.ord_type]||o.ord_type;
    return [tm(o.created),esc(o.market)+'<div class="mu small">'+(o.owned?'이 봇에서 접수':'업비트 앱·웹 / 외부 주문')+'</div>',(o.side==='bid'?'<span class="up">매수</span>':'<span class="dn">매도</span>')+' / '+esc(type),o.price==null?'—':esc(o.price)+' '+esc(unit),n(o.volume||0,8)+' / '+n(o.filled,8)+' / <b>'+n(o.remaining,8)+'</b>',n(o.locked,8)+' '+esc(o.side==='bid'?unit:o.market.split('-')[1]),pending?'요청 결과 확인 중':o.state==='watch'?'예약 대기':'미체결·부분 체결',
      '<button class="bt small danger" data-exchange-cancel="'+esc(o.uuid)+'"'+(pending?' disabled':'')+'>취소</button> '+(editable?'<button class="bt small" data-exchange-edit="'+esc(o.uuid)+'"'+(pending?' disabled':'')+'>가격·수량 수정</button>':'<span class="mu small">수정: 원화 지정가만</span>')]
  }));
  const statuses={submitting:'접수 확인 중',unknown:'응답 미확인 · 재전송 없음',accepted:'거래소 최종 결과 조회 중',done:'처리 확인',rejected:'요청 거절·미접수'};
  $('exchange-requests').innerHTML=x.requests.length?table(['요청 시각','종목','처리','결과'],x.requests.slice(-12).reverse().map(r=>[tm(r.created),esc(r.market),r.kind==='replace'?'주문 수정':'주문 취소',esc(statuses[r.status]||r.status)+'<div class="mu small">'+esc(r.message||'')+'</div>'])):'';
}
function invalidateExchange(){exchangeTicket=null;$('exchange-submit').hidden=true;$('exchange-preview').hidden=false;$('exchange-error').textContent='';$('exchange-review').textContent='입력 후 수정 내용을 다시 검토하세요.'}
function setExchangeBusy(value){exchangeBusy=value;['exchange-minus','exchange-plus','exchange-current','exchange-price','exchange-remain','exchange-close','exchange-preview','exchange-submit'].forEach(id=>$(id).disabled=value);$('exchange-volume').disabled=value||$('exchange-remain').checked}
function openExchangeEdit(orderUuid){
  if(busy||orderBusy||exchangeBusy)return;
  const o=portfolioData?.exchange?.orders.find(o=>o.uuid===orderUuid);if(!o)return;
  exchangeDraft={...o};$('exchange-description').textContent='실제 '+o.market+' · '+(o.side==='bid'?'매수':'매도')+' · 기존 '+o.price+'원 · 현재 미체결 '+n(o.remaining,8)+'개';
  $('exchange-price').value=o.price;$('exchange-volume').value=o.remaining;$('exchange-remain').checked=true;
  setExchangeBusy(false);invalidateExchange();$('exchange-dialog').showModal();
}
async function cancelExchangeOrder(orderUuid){
  if(busy||exchangeBusy)return;const o=portfolioData?.exchange?.orders.find(o=>o.uuid===orderUuid);if(!o)return;
  if(!confirm('실제 업비트 '+o.market+' '+(o.side==='bid'?'매수':'매도')+' 주문의 남은 '+n(o.remaining,8)+'개를 취소할까요? 이미 체결된 수량은 유지됩니다.'))return;
  await portfolioCommand({action:'portfolio_exchange_cancel',uuid:orderUuid});
}
$('exchange-open-orders').addEventListener('click',e=>{const b=e.target.closest('button');if(b?.dataset.exchangeEdit)return openExchangeEdit(b.dataset.exchangeEdit);if(b?.dataset.exchangeCancel)return cancelExchangeOrder(b.dataset.exchangeCancel)});
$('exchange-sync-now').onclick=()=>portfolioCommand({action:'portfolio_exchange_sync'});
['exchange-price','exchange-volume'].forEach(id=>$(id).oninput=invalidateExchange);
$('exchange-remain').onchange=()=>{$('exchange-volume').disabled=$('exchange-remain').checked;invalidateExchange()};
async function stepExchange(direction,current=false){
  if(exchangeBusy||!exchangeDraft)return;invalidateExchange();setExchangeBusy(true);
  try{const body={action:'portfolio_quote',mode:'live',market:exchangeDraft.market,direction};if(!current)body.price=$('exchange-price').value;
    const q=await portfolioRequest(body);$('exchange-price').value=q.price;
  }catch(e){$('exchange-error').textContent=e.message}finally{setExchangeBusy(false)}
}
$('exchange-minus').onclick=()=>stepExchange(-1);$('exchange-plus').onclick=()=>stepExchange(1);$('exchange-current').onclick=()=>stepExchange(0,true);
$('exchange-preview').onclick=async()=>{
  if(exchangeBusy||!exchangeDraft)return;setExchangeBusy(true);exchangeTicket=null;$('exchange-submit').hidden=true;
  try{const review=await portfolioRequest({action:'portfolio_exchange_preview',uuid:exchangeDraft.uuid,price:$('exchange-price').value,volume:$('exchange-volume').value,remain_only:$('exchange-remain').checked});
    exchangeTicket=review.ticket;$('exchange-error').textContent='';
    $('exchange-review').textContent='실제 '+review.market+' '+(review.side==='bid'?'매수':'매도')+'\n기존 '+review.old_price+'원 → '+review.price+'원\n'+(review.remain_only?'취소 시점 미체결 잔량 유지 (현재 '+n(review.volume,8)+'개)':'새 주문 수량 '+n(review.volume,8)+'개\n처리 중 기존 주문이 추가 체결되어도 새 주문 수량은 이 입력값으로 고정됩니다.')+'\n예상 금액 '+n(review.total,2)+'원 + 수수료 '+n(review.estimated_fee,2)+'원\n30초 이내 최종 제출하세요. 기존 주문 취소 전 체결된 수량은 유지됩니다. 취소 전에 전량 체결되면 새 주문은 생성되지 않습니다.';
    $('exchange-submit').hidden=false;$('exchange-preview').hidden=true;
  }catch(e){$('exchange-error').textContent=e.message}finally{setExchangeBusy(false)}
};
$('exchange-submit').onclick=async()=>{
  if(exchangeBusy||!exchangeTicket)return;const ticket=exchangeTicket;exchangeTicket=null;setExchangeBusy(true);
  try{const message=await portfolioRequest({action:'portfolio_exchange_submit',ticket});$('exchange-review').textContent=message;$('exchange-submit').hidden=true;await refreshPortfolio();toast(message)}
  catch(e){$('exchange-error').textContent=e.message+' · 결과를 조회한 뒤 다시 검토하세요. 자동 재전송하지 않습니다.';$('exchange-submit').hidden=true}
  finally{setExchangeBusy(false)}
};
$('exchange-close').onclick=()=>{if(!exchangeBusy){exchangeTicket=null;exchangeDraft=null;$('exchange-dialog').close()}};
$('exchange-dialog').addEventListener('cancel',e=>{if(exchangeBusy)e.preventDefault();else{exchangeTicket=null;exchangeDraft=null}});
