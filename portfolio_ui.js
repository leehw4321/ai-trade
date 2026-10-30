/* Included inside the session-protected local page. No credentials in the UI. */
let portfolioData=null, orderDraft=null, orderTicket=null, orderBusy=false;
let portfolioViewMode='live',portfolioV3=null,portfolioStructureKey='';
const priceText=v=>v==null?'평가 불가':n(v,Math.abs(v)<100?Math.min(8,Math.max(2,Math.ceil(-Math.log10(Math.max(v,1e-8)))+3)):0);
async function portfolioRequest(body){
  const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Session-Token':token},body:JSON.stringify(body)});
  const d=await r.json();if(!r.ok||d.error)throw Error(d.error||'요청 실패');return d.message;
}
async function refreshPortfolio(){const r=await fetch('/api/data',{headers:{'X-Session-Token':token},cache:'no-store'});if(!r.ok)throw Error('화면 갱신 실패');draw(await r.json())}
async function portfolioCommand(body){
  if(busy)return;busy=true;
  try{const result=await portfolioRequest(body);toast(typeof result==='string'?result:'적용했습니다.');await refreshPortfolio();return true}
  catch(e){toast(e.message||'응답 확인 중입니다. 주문 내역을 확인하세요.',true);return false}finally{busy=false}
}
function drawPortfolio(p,v3=portfolioV3){
  if(!p)return;portfolioData=p;portfolioV3=v3;const mode=p.mode,live=mode==='live',v=p.views[mode];
  const displayAccounts=portfolioViewMode==='all'?['live','paper']:[portfolioViewMode];
  renderComparison(p);
  $('mode').textContent=(live?'실거래 · 직접 주문':'시뮬레이션')+' · 모의 자동 검증 '+(p.running?'실행':'정지');$('mode').className='badge'+(live?' live':'');
  $('status').textContent=(p.error?p.error+' · ':'')+(live?'직접 주문 가능 · 자동 신규 매수 차단':p.running?'위임 종목만 자동 신규 매수 검사':'직접 모의 주문 가능')+' · 위임한 보유분은 모드 전환 후에도 청산 관리';$('status').className='msg '+(p.error?'warn':'mu');
  renderActualAccount(p);
  renderExchangeOrders(p);
  const catalog=p.catalog||[];const optionsKey=catalog.map(x=>x.market).join(',');if($('coin-options').dataset.key!==optionsKey){$('coin-options').innerHTML=catalog.map(x=>'<option value="'+esc(x.market)+'">'+esc(x.name)+'</option>').join('');$('coin-options').dataset.key=optionsKey}
  // Settings changes must move the card even when a control still has focus.
  const structureKey=JSON.stringify([portfolioViewMode,p.settings,Object.fromEntries(Object.entries(p.views).map(([a,v])=>[a,v.holdings.map(h=>h.market)])),Object.entries(v3?.state?.positions||{}).map(([m,h])=>[m,h.bucket])]);
  if(structureKey!==portfolioStructureKey||!document.activeElement?.closest?.('#buckets')){
    portfolioStructureKey=structureKey;
    $('buckets').innerHTML=Object.entries(p.profiles).map(([bucket,rule])=>{
      const accounts=displayAccounts;
      const held=accounts.flatMap(account=>Array.from(new Map((p.views[account]?.holdings||[]).map(h=>[h.market,h])).values()).filter(h=>h.qty>0&&(p.settings[account][h.market]?.bucket||'swing')===bucket).map(h=>({h,account})));
      const v3Held=portfolioViewMode!=='live'?Object.entries(v3?.state?.positions||{}).filter(([m,h])=>h.volume>0&&(h.bucket||'short')===bucket):[];
      const value=held.reduce((a,r)=>a+(r.h.value||0),0)+v3Held.reduce((a,[m,h])=>a+h.volume*(v3.quotes[m]?.bid||h.entry),0);
      const visible=held.filter(r=>!foldedHolding(p,r.h,r.account)),folded=held.filter(r=>foldedHolding(p,r.h,r.account));
      const content=visible.map(r=>assetCard(r.h.market,p,p.views[r.account],r.account)).join('')+v3Held.map(([m,h])=>v3PortfolioCard(m,h,p,v3)).join('');
      return '<section class="bucket" data-bucket="'+bucket+'"><div class="bucket-head"><div class="row between"><h3>'+esc(rule.name)+'</h3><span class="mu small">'+(held.length+v3Held.length)+'개 보유</span></div><div class="small mu">'+esc(rule.horizon||rule.description)+'</div><div class="row between" style="margin-top:6px"><b>'+n(value)+'원</b><span class="mu small">'+(held.filter(r=>p.settings[r.account][r.h.market]?.auto).length+v3Held.length)+'개 자동 관리</span></div></div>'+(content||'<div class="empty-bucket">보유 종목 없음</div>')+(folded.length?'<details data-small-bucket="'+bucket+'"'+(smallBucketOpen[bucket]?' open':'')+'><summary>소액 보유 '+folded.length+'종목</summary>'+folded.map(r=>assetCard(r.h.market,p,p.views[r.account],r.account)).join('')+'</details>':'')+'</section>'
    }).join('');
  }
  const actualCount=(p.views.live.holdings||[]).filter(h=>h.qty>0).length,paperCount=(p.views.paper.holdings||[]).filter(h=>h.qty>0).length,v3Count=Object.values(v3?.state?.positions||{}).filter(h=>h.volume>0).length;
  $('portfolio-holdings-status').textContent='실제 보유 '+actualCount+'개 · 기존 시뮬레이션 '+paperCount+'개 · V3 시뮬레이션 '+v3Count+'개 · 미보유 감시·진입 대기 종목은 숨김';
  ['all','live','paper'].forEach(account=>$('portfolio-view-'+account).setAttribute('aria-pressed',String(portfolioViewMode===account)));
  const unsupported=displayAccounts.flatMap(account=>(p.views[account]?.holdings||[]).filter(h=>h.qty>0&&!h.tradable).map(h=>({...h,account})));$('unsupported-count').textContent=unsupported.length+'개 보유';$('unsupported').innerHTML=table(['계좌','코인','보유 수량','평가액','상태'],unsupported.map(h=>[h.account==='live'?'실제':'시뮬레이션',esc(h.currency),n(h.qty,8),h.value==null?'평가 불가':n(h.value)+'원','원화 주문 미지원 · 분류는 위 포트폴리오 소액 보유에서 변경 가능']));
  const statusNames={submitting:'접수 확인 중',unknown:'응답 미확인',wait:'미체결·부분 체결',done:'체결 완료',cancel:'취소',rejected:'미접수·거절'};
  const orderFilter=$('orders-filter').value||'all',selectedModes=orderFilter==='all'?['paper','live']:[orderFilter];
  $('portfolio-orders').innerHTML=table(['시각','계좌','종목','주문','지정가','요청 / 체결 수량','상태','작업'],selectedModes.flatMap(m=>p.orders[m]).sort((a,b)=>b.created-a.created).slice(0,40).map(o=>{
    const active=['submitting','unknown','wait'].includes(o.status);return [tm(o.created),o.mode==='live'?'<span class="up">실거래</span>':'모의',esc(o.market),o.side==='bid'?'<span class="up">매수</span>':'<span class="dn">매도</span>',priceText(o.price),n(o.volume,8)+' / '+n(o.filled,8),'<span title="'+esc(o.message)+'">'+esc(statusNames[o.status]||o.status)+(o.automatic?' · 자동':' · 직접')+'</span>',active?'<button class="bt small" data-order-mode="'+o.mode+'" data-cancel="'+esc(o.identifier)+'">취소</button>'+(o.mode==='live'&&o.status==='unknown'?'<button class="bt small" data-order-mode="live" data-recover="'+esc(o.identifier)+'">미접수 확인</button>':''):'—']
  }));
  $('portfolio-trades').innerHTML=table(['시각','계좌','종목','구분','체결 수량','체결 금액','수수료','매도 추정손익'],selectedModes.flatMap(m=>p.journal[m].map(t=>({...t,mode:m}))).sort((a,b)=>b.time-a.time).slice(0,40).map(t=>[tm(t.time),t.mode==='live'?'<span class="up">실거래</span>':'모의',esc(t.market),t.side==='bid'?'매수':'매도',n(t.volume,8),n(t.funds)+'원',n(t.fee,2)+'원',t.pnl==null?'—':'<span class="'+cl(t.pnl)+'">'+sg(t.pnl)+'원</span>']));
  $('profile-rules').innerHTML=Object.values(p.profiles).map(r=>'<div class="c"><b>'+esc(r.name)+'</b><p>'+esc(r.horizon||'')+'</p><p class="mu small">'+esc(r.description)+'</p><span class="tag">거래당 계획 위험 0.25% · 신규 진입: 모의만 허용</span></div>').join('');
  document.querySelectorAll('[data-paction]').forEach(b=>{b.disabled=busy||!p.seeded;b.setAttribute('aria-pressed',String(b.dataset.mode===mode))});
}
function assetCard(m,p,v,account=p.mode){
  const cfg=p.settings[account][m]||{bucket:'swing',auto:false};const h=v.holdings.find(h=>h.market===m)||{qty:0,available:0,value:0,pnl:null,roi:null,avg:0};
  const price=p.prices[m]??h.price,name=p.catalog.find(x=>x.market===m)?.name||m;
  return '<article class="asset" data-market="'+esc(m)+'" data-portfolio-mode="'+account+'"><div class="row between"><div class="asset-top">'+esc(name)+' <span class="mu small">'+esc(m.slice(4))+'</span></div><span class="manage-label '+(cfg.auto?'auto':'')+'">'+(cfg.auto?'자동 관리':'직접 관리')+'</span></div><div class="small '+(account==='live'?'up':'good')+'">'+(account==='live'?'실제 보유':'시뮬레이션 보유 · 기존 계좌')+'</div><div class="asset-price">'+priceText(price)+' <span class="mu small">원</span></div><div class="asset-stats"><div><span>보유 평가액</span>'+n(h.value||0)+'원</div><div><span>평가손익 / 수익률</span><b class="'+cl(h.pnl)+'">'+(h.pnl==null?'—':sg(h.pnl)+'원 / '+sg(h.roi,2)+'%')+'</b></div><div><span>보유 / 주문 가능</span>'+n(h.qty,8)+' / '+n(h.available,8)+'</div><div><span>매수평균가</span>'+(h.avg?priceText(h.avg)+'원':'—')+'</div></div><div class="asset-controls"><select data-bucket-select aria-label="'+esc(name)+' 투자 분류">'+Object.entries(p.profiles).map(([k,r])=>'<option value="'+k+'"'+(cfg.bucket===k?' selected':'')+'>'+esc(r.name)+'</option>').join('')+'</select><button class="bt '+(cfg.auto?'auto':'')+'" data-delegate'+(h.tradable===false?' disabled':'')+'>'+(cfg.auto?'직접 관리로 회수':'자동 관리 위임')+'</button><button class="bt buy" data-order="bid"'+(h.tradable===false?' disabled':'')+'>매수</button><button class="bt sell" data-order="ask"'+(!h.available||h.tradable===false?' disabled':'')+'>매도</button></div><div class="asset-note small '+(cfg.auto?'good':'mu')+'">'+(cfg.auto?'추적 손절 '+priceText(cfg.stop)+'원 · '+esc(cfg.message||'위임 확인'):'사용자가 매매 결정')+'</div></article>';
}
const smallBucketOpen={};
function v3PortfolioCard(m,h,p,v3){
  const price=v3.quotes[m]?.bid??h.entry,value=price*h.volume,pnl=value-h.cost,cfg=h.bucket||'short';
  return '<article class="asset" data-market="'+esc(m)+'" data-portfolio-source="v3"><div class="row between"><b>'+esc(p.catalog.find(r=>r.market===m)?.name||m)+'</b><span class="tag">시뮬레이션 보유 · V3</span></div><div class="asset-price">'+priceText(price)+'원</div><div class="asset-stats"><div><span>보유 평가액</span>'+n(value)+'원</div><div><span>평가손익</span><b class="'+cl(pnl)+'">'+sg(pnl)+'원</b></div><div><span>보유 수량</span>'+n(h.volume,8)+'</div><div><span>매수평균가</span>'+priceText(h.entry)+'원</div></div><div class="asset-controls"><select data-v3-profile aria-label="V3 투자 분류">'+Object.entries(p.profiles).map(([k,r])=>'<option value="'+k+'"'+(k===cfg?' selected':'')+'>'+esc(r.name)+'</option>').join('')+'</select><a class="bt" href="#auto-v3-panel">V3 청산 관리</a></div><div class="small mu">손절선 '+priceText(h.stop)+'원 · '+(h.profile_pending?'새 기간 ATR 준비 중 · 기존 보호선 유지':esc(p.profiles[cfg]?.horizon||'자동 관리'))+'</div></article>';
}
['all','live','paper'].forEach(account=>$('portfolio-view-'+account).onclick=()=>{portfolioViewMode=account;if(portfolioData)drawPortfolio(portfolioData)});
$('buckets').addEventListener('toggle',e=>{if(e.target.dataset?.smallBucket)smallBucketOpen[e.target.dataset.smallBucket]=e.target.open},true);
document.querySelectorAll('[data-paction]').forEach(b=>b.onclick=()=>{
  if(!portfolioData||busy)return;const a=b.dataset.paction,mode=b.dataset.mode;
  if(a==='portfolio_mode'&&mode==='live'&&!confirm('실제 계좌 전체 자산으로 직접 주문할 수 있는 실거래 화면으로 전환합니다. 전환만으로 신규 주문하지 않습니다. 계속할까요?'))return;
  if(a==='portfolio_reset'&&!confirm('현재 실제 계좌 전체 잔고를 다시 복사하고 모의 주문·체결 기록과 위임 설정을 초기화할까요?'))return;
  portfolioCommand({action:a,mode});
});
$('add-coin').onclick=()=>{const text=$('coin-search').value.trim(),p=portfolioData;if(!p||busy)return;const match=p.catalog.find(x=>x.market===text.toUpperCase()||x.market.slice(4)===text.toUpperCase()||x.name===text);if(!match)return toast('원화 종목명 또는 코드를 선택하세요.',true);openOrder(match.market,'bid',{mode:p.mode})};
$('buckets').addEventListener('change',event=>{
  if(!portfolioData||busy)return;
  const asset=event.target.closest('[data-market]');if(!asset)return;
  const market=asset.dataset.market,p=portfolioData;
  if(event.target.matches('[data-v3-profile]')){
    const bucket=event.target.value;event.target.blur();
    if(confirm('V3 '+market+'을 '+p.profiles[bucket].name+' 기준으로 관리할까요? 기존 손절선을 낮추지 않고 새 기간 ATR을 준비합니다. 미체결 분할익절 예약은 취소 후 새 기준으로 재평가합니다.'))v3Command({action:'auto_v3_position_profile',market,bucket});
    else refreshPortfolio();return;
  }
  if(!event.target.matches('[data-bucket-select]'))return;
  const mode=asset.dataset.portfolioMode||p.mode,cfg=p.settings[mode][market]||{auto:false};
  if(cfg.auto&&!confirm((mode==='live'?'실제':'모의')+' 보유분을 '+p.profiles[event.target.value].name+' 기준으로 관리합니다. 기존 손절선은 낮추지 않으며 보유 시간 기준은 지금부터 새로 시작합니다. 계속할까요?')){event.target.blur();refreshPortfolio();return}
  event.target.blur();return portfolioCommand({action:'portfolio_config',mode,market,bucket:event.target.value,auto:cfg.auto});
});
$('buckets').addEventListener('click',event=>{
  const button=event.target.closest('button'),asset=button?.closest('[data-market]');if(!asset||!portfolioData||busy)return;
  const market=asset.dataset.market,p=portfolioData,mode=asset.dataset.portfolioMode||p.mode,cfg=p.settings[mode][market]||{bucket:'swing',auto:false};
  if(button.hasAttribute('data-order'))return openOrder(market,button.dataset.order,{mode});
  if(button.hasAttribute('data-delegate')){const auto=!cfg.auto;const question=auto?(mode==='live'?'실제':'모의')+' '+market+' 전체 주문 가능 보유량의 자동 청산 관리를 위임합니다.\n'+p.profiles[cfg.bucket].description+'\n위임 시점 현재가로 초기 손절선을 설정합니다. 기간별 수익성은 아직 검증하지 않았으며 실거래 자동 신규 매수는 차단됩니다. 계속할까요?':'이후 자동 매매 권한을 회수할까요? 이미 접수된 주문은 별도로 취소해야 합니다.';if(confirm(question))portfolioCommand({action:'portfolio_config',mode,market,bucket:cfg.bucket,auto})}
});
$('orders-filter').onchange=()=>{if(portfolioData)drawPortfolio(portfolioData)};
$('portfolio-orders').addEventListener('click',event=>{const b=event.target.closest('button');if(!b||!portfolioData||busy)return;const id=b.dataset.cancel||b.dataset.recover;if(!id)return;const recovery=!!b.dataset.recover;if(confirm(recovery?'거래소에서 두 차례 미접수를 확인한 경우에만 잠금을 해제합니다. 자동 재주문하지 않습니다. 확인할까요?':'이 주문의 미체결 잔량을 취소할까요? 이미 체결한 수량은 유지됩니다.'))portfolioCommand({action:recovery?'portfolio_recover':'portfolio_cancel',mode:b.dataset.orderMode,identifier:id})});
function setTheme(theme){
  if(!['gray','ivory','slate'].includes(theme))theme='gray';
  document.documentElement.dataset.theme=theme;
  try{localStorage.setItem('coinbot-theme',theme)}catch(e){}
  for(const [id,name] of [['theme-toggle','gray'],['theme-ivory','ivory'],['theme-slate','slate']]){
    $(id)?.setAttribute('aria-pressed',String(name===theme));
  }
}
let savedTheme='gray';try{savedTheme=localStorage.getItem('coinbot-theme')||'gray'}catch(e){}setTheme(savedTheme);
$('theme-toggle').onclick=()=>setTheme('gray');
if($('theme-ivory'))$('theme-ivory').onclick=()=>setTheme('ivory');
if($('theme-slate'))$('theme-slate').onclick=()=>setTheme('slate');
function invalidateOrder(){orderTicket=null;$('order-submit').hidden=true;$('order-preview').hidden=false;$('order-error').textContent='';$('order-review').textContent='예상 주문 금액 '+n(Number($('order-price').value)*Number($('order-volume').value),2)+'원 · 검토 후 최종 제출';}
function quantityChanged(){const value=Number($('order-volume').value)*Number($('order-price').value);$('order-amount').value=Number.isFinite(value)?value.toFixed(2):'';invalidateOrder()}
function amountChanged(){const price=Number($('order-price').value),amount=Number($('order-amount').value);$('order-volume').value=price>0?(Math.floor(amount/price*1e8)/1e8).toFixed(8):'';invalidateOrder()}
$('order-price').oninput=quantityChanged;$('order-volume').oninput=quantityChanged;$('order-amount').oninput=amountChanged;
function setOrderBusy(value){orderBusy=value;['order-preview','order-submit','price-minus','price-plus','price-current','order-close','order-mode-select','order-bucket-select','order-price','order-volume','order-amount'].forEach(id=>$(id).disabled=value);document.querySelectorAll('[data-percent]').forEach(b=>b.disabled=value)}
async function orderQuote(direction=0,reset=false){
  if(orderBusy||!orderDraft)return;setOrderBusy(true);invalidateOrder();
  try{const body={action:'portfolio_quote',market:orderDraft.market,mode:orderDraft.mode,direction};if(!reset)body.price=$('order-price').value;const q=await portfolioRequest(body);$('order-price').value=q.price;orderDraft.info=q;$('order-quote').textContent='현재가 '+priceText(q.current)+' · 매수호가 '+priceText(q.bid)+' / 매도호가 '+priceText(q.ask)+' · 1틱 '+q.tick+'원';$('order-balance').textContent='주문 가능 원화 '+n(q.cash)+'원 · '+orderDraft.market.slice(4)+' '+n(q.available,8)+'개 · 수수료 '+n((orderDraft.side==='bid'?q.bid_fee:q.ask_fee)*100,3)+'%';quantityChanged()}
  catch(e){$('order-error').textContent=e.message}finally{setOrderBusy(false)}
}
async function openOrder(market,side,options={}){
  if(orderBusy||!portfolioData)return;const mode=options.mode||portfolioData.mode;
  const cfg=portfolioData.settings[mode]?.[market];
  orderDraft={market,side,mode,bucket:cfg?.bucket||options.bucket||'swing'};orderTicket=null;
  $('order-mode-select').value=mode;$('order-bucket-select').value=orderDraft.bucket;
  $('order-title').textContent=market+' '+(side==='bid'?'매수':'매도');$('order-mode').textContent=orderDraft.mode==='live'?'실거래':'시뮬레이션';$('order-mode').className='badge'+(orderDraft.mode==='live'?' live':'');
  $('order-volume').value='';$('order-amount').value='';$('order-price').value='';$('order-dialog').showModal();await orderQuote(0,true);
  if(options.amount&&side==='bid'){$('order-amount').value=options.amount;amountChanged()}
}
$('order-mode-select').onchange=async()=>{if(orderBusy||!orderDraft)return;orderDraft.mode=$('order-mode-select').value;const cfg=portfolioData.settings[orderDraft.mode]?.[orderDraft.market];if(cfg){orderDraft.bucket=cfg.bucket;$('order-bucket-select').value=cfg.bucket}$('order-mode').textContent=orderDraft.mode==='live'?'실거래':'시뮬레이션';$('order-mode').className='badge'+(orderDraft.mode==='live'?' live':'');await orderQuote(0,true)};
$('order-bucket-select').onchange=()=>{if(orderDraft){orderDraft.bucket=$('order-bucket-select').value;invalidateOrder()}};
$('price-minus').onclick=()=>orderQuote(-1);$('price-plus').onclick=()=>orderQuote(1);$('price-current').onclick=()=>orderQuote(0,true);
document.querySelectorAll('[data-percent]').forEach(b=>b.onclick=()=>{
  if(orderBusy||!orderDraft?.info)return;const q=orderDraft.info,ratio=Number(b.dataset.percent),price=Number($('order-price').value);
  const qty=orderDraft.side==='bid'?Math.max(0,q.cash-.01)*ratio/(1+q.bid_fee)/price:q.available*ratio;
  $('order-volume').value=(Math.floor(qty*1e8)/1e8).toFixed(8);quantityChanged();
});
$('order-close').onclick=()=>{if(!orderBusy){$('order-dialog').close();orderDraft=null;orderTicket=null}};
$('order-dialog').addEventListener('cancel',event=>{if(orderBusy)event.preventDefault();else{orderDraft=null;orderTicket=null}});
$('order-preview').onclick=async()=>{
  if(orderBusy||!orderDraft)return;setOrderBusy(true);$('order-error').textContent='';
  try{const t=await portfolioRequest({action:'portfolio_preview',...orderDraft,price:$('order-price').value,volume:$('order-volume').value});orderTicket=t.ticket;$('order-review').textContent=(t.mode==='live'?'실제 계좌':'모의 계좌')+' · '+t.market+' '+(t.side==='bid'?'매수':'매도')+'\n지정가 '+priceText(t.price)+'원 × '+n(t.volume,8)+'개\n주문 금액 '+n(t.total,2)+'원\n예상 수수료 '+n(t.estimated_fee,2)+'원\n'+(t.side==='bid'?'필요 원화 '+n(t.total+t.estimated_fee,2)+'원':'전량 체결 시 예상 수령 '+n(t.total-t.estimated_fee,2)+'원')+'\n30초 안에 최종 제출하세요.';$('order-submit').hidden=false;$('order-preview').hidden=true}
  catch(e){$('order-error').textContent=e.message}finally{setOrderBusy(false)}
};
$('order-submit').onclick=async()=>{
  if(orderBusy||!orderTicket)return;setOrderBusy(true);const ticket=orderTicket;orderTicket=null;
  try{const result=await portfolioRequest({action:'portfolio_submit',ticket});toast(result);$('order-dialog').close();orderDraft=null;await refreshPortfolio()}
  catch(e){$('order-error').textContent=e.message+' · 같은 요청을 다시 제출하지 말고 주문 내역을 확인하세요.';$('order-submit').hidden=true;$('order-preview').hidden=false;try{await refreshPortfolio()}catch(_){} }
  finally{setOrderBusy(false)}
};
