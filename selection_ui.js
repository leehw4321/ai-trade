let selectionDrafts={},selectionReview=null,selectionBusy=false;
const periodName=u=>({hour:'시간',day:'일',week:'주',month:'개월'}[u]||u);
try{const saved=JSON.parse(localStorage.getItem('coinbot-selection-v1')||'{}');if(saved&&typeof saved==='object'&&!Array.isArray(saved))selectionDrafts=saved}catch(e){}
function saveSelections(){try{localStorage.setItem('coinbot-selection-v1',JSON.stringify(selectionDrafts))}catch(e){}}
function selectedItems(){return Object.values(selectionDrafts).filter(d=>d&&d.checked)}
function selectionForm(r){
  let d=selectionDrafts[r.market];
  if(!d||typeof d!=='object')d=selectionDrafts[r.market]={market:r.market,name:r.name,bucket:r.bucket,checked:false,
    amount:$('recommend-order-amount').value||'10000',price:String(r.price),mode:$('recommend-order-mode').value||'paper',
    period_value:r.bucket==='short'?2:1,period_unit:r.bucket==='long'?'month':r.bucket==='swing'?'week':'hour'};
  return '<div class="rec-plan" data-selection-market="'+esc(r.market)+'"><label class="plan-check"><input type="checkbox" data-plan-field="checked"'+(d.checked?' checked':'')+'>이 종목 선택</label><div class="rec-plan-grid"><label>투자금액 (원)<input type="number" min="5000" step="any" data-plan-field="amount" value="'+esc(d.amount)+'"></label><label>매수가격 (원)<span class="price-inputs"><input type="number" min="0.00000001" step="any" data-plan-field="price" value="'+esc(d.price)+'"><button type="button" class="bt" data-plan-current>현재가</button></span></label><label>이번 주문 계좌<select data-plan-field="mode"><option value="paper"'+(d.mode==='paper'?' selected':'')+'>시뮬레이션</option><option value="live"'+(d.mode==='live'?' selected':'')+'>실제 투자</option></select></label><label>관찰기간 (첫 체결부터)<span class="period-inputs"><input type="number" min="1" max="43800" step="1" data-plan-field="period_value" value="'+esc(d.period_value)+'"><select data-plan-field="period_unit">'+['hour','day','week','month'].map(u=>'<option value="'+u+'"'+(d.period_unit===u?' selected':'')+'>'+periodName(u)+'</option>').join('')+'</select></span></label></div></div>';
}
function renderSelectionForms(rows){
  document.querySelectorAll('#recommendations-list .rec-card').forEach((card,i)=>card.insertAdjacentHTML('beforeend',selectionForm(rows[i])));
  renderSelectionSummary();
}
function renderSelectionSummary(){
  const rows=selectedItems();$('selection-count').textContent=rows.length+'종목';
  $('selection-summary').innerHTML=table(['종목','계좌','투자금액','매수가격','관찰기간','작업'],rows.map(d=>[esc(d.name||d.market),d.mode==='live'?'<span class="up">실제 투자</span>':'시뮬레이션',n(d.amount)+'원',priceText(d.price)+'원',esc(d.period_value)+' '+periodName(d.period_unit),'<button class="bt small" data-selection-remove="'+esc(d.market)+'">선택 해제</button>']));
  $('selection-preview').disabled=selectionBusy||!rows.length||rows.length>10;
}
function changeSelection(event){
  const input=event.target,field=input.dataset?.planField,wrapper=input.closest?.('[data-selection-market]');
  if(!field||!wrapper||selectionBusy)return;const d=selectionDrafts[wrapper.dataset.selectionMarket];if(!d)return;
  if(field==='checked'&&input.checked&&selectedItems().length>=10&&!d.checked){input.checked=false;return toast('한 번에 최대 10종목을 선택하세요.',true)}
  d[field]=field==='checked'?input.checked:input.value;
  if(field==='mode'&&portfolioData?.settings[d.mode]?.[d.market])d.bucket=portfolioData.settings[d.mode][d.market].bucket;
  selectionReview=null;saveSelections();renderSelectionSummary();
}
$('recommendations-list').addEventListener('input',changeSelection);
$('recommendations-list').addEventListener('change',changeSelection);
$('recommendations-list').addEventListener('focusout',()=>setTimeout(()=>{if(researchData)renderResearch(researchData)},0));
$('recommendations-list').addEventListener('click',async event=>{
  const button=event.target.closest('[data-plan-current]');if(!button||selectionBusy)return;
  const wrapper=button.closest('[data-selection-market]'),d=selectionDrafts[wrapper.dataset.selectionMarket];if(!d)return;
  button.disabled=true;try{const q=await portfolioRequest({action:'portfolio_quote',mode:d.mode,market:d.market});d.price=q.price;
    wrapper.querySelector('[data-plan-field=price]').value=q.price;selectionReview=null;saveSelections();renderSelectionSummary()}
  catch(e){toast(e.message,true)}finally{button.disabled=false}
});
$('selection-summary').addEventListener('click',event=>{const b=event.target.closest('[data-selection-remove]');if(!b||selectionBusy)return;const d=selectionDrafts[b.dataset.selectionRemove];if(d)d.checked=false;saveSelections();renderSelectionSummary();researchKey='';if(researchData)renderResearch(researchData)});
function setSelectionBusy(value){selectionBusy=value;['selection-preview','selection-submit','selection-close'].forEach(id=>$(id).disabled=value);document.querySelectorAll('[data-plan-field]').forEach(e=>e.disabled=value)}
$('selection-preview').onclick=async()=>{
  if(selectionBusy)return;setSelectionBusy(true);$('selection-error').textContent='';
  const items=selectedItems().map(d=>({market:d.market,mode:d.mode,bucket:portfolioData?.settings[d.mode]?.[d.market]?.bucket||d.bucket,
    amount:d.amount,price:d.price,period_value:d.period_value,period_unit:d.period_unit}));
  try{
    selectionReview=await portfolioRequest({action:'portfolio_selection_preview',items});
    const live=selectionReview.items.filter(t=>t.mode==='live');
    $('selection-review').innerHTML=table(['계좌','종목','단가','수량','금액 + 예상 수수료','관찰기간'],selectionReview.items.map(t=>[
      t.mode==='live'?'<b class="up">실제 투자</b>':'시뮬레이션',esc(t.market),priceText(t.price)+'원',n(t.volume,8),n(t.total,2)+' + '+n(t.estimated_fee,2)+'원',t.observation.value+' '+periodName(t.observation.unit)]))+
      '<p class="'+(live.length?'warn':'good')+'">'+(live.length?'실제 계좌에서 '+live.length+'건, 총 '+n(live.reduce((s,t)=>s+t.total+t.estimated_fee,0),2)+'원의 자금이 필요한 매수 주문입니다.':'모든 주문은 시뮬레이션입니다.')+'</p><p class="mu small">30초 안에 최종 제출하세요. 관찰은 첫 체결 시점부터 시작하며 월 단위는 한국시간 달력 기준입니다. 관찰 수익률은 실제 매도손익과 다릅니다.</p>';
    $('selection-submit-status').textContent='';$('selection-submit').hidden=false;$('selection-dialog').showModal();
  }catch(e){$('selection-error').textContent=e.message}finally{setSelectionBusy(false)}
};
$('selection-submit').onclick=async()=>{
  if(selectionBusy||!selectionReview)return;setSelectionBusy(true);const review=selectionReview;selectionReview=null;
  try{
    const receipt=await portfolioRequest({action:'portfolio_selection_submit',group:review.group});
    $('selection-submit-status').textContent=receipt.results.map(r=>r.error||r.market+' '+(r.mode==='live'?'실거래':'모의')+' · '+r.message).join('\n')+'\n요청 '+receipt.requested+'건 중 처리 결과 '+receipt.results.length+'건. 미처리 항목은 주문 내역을 확인한 뒤 다시 검토하세요.';
    receipt.results.forEach(r=>{if(r.identifier&&selectionDrafts[r.market])selectionDrafts[r.market].checked=false});saveSelections();renderSelectionSummary();researchKey='';if(researchData)renderResearch(researchData);
    $('selection-submit').hidden=true;await refreshPortfolio();toast('선택 주문 처리 결과와 미체결 내역을 확인하세요.');
  }catch(e){$('selection-submit-status').textContent=e.message+' · 재전송하지 말고 주문 내역을 확인하세요.';$('selection-submit').hidden=true}
  finally{setSelectionBusy(false)}
};
$('selection-close').onclick=()=>{if(!selectionBusy){$('selection-dialog').close();selectionReview=null}};
$('selection-dialog').addEventListener('cancel',e=>{if(selectionBusy)e.preventDefault();else selectionReview=null});
const liveManagementDrafts={},liveTableStructureKeys={};
function liveAuthorityControls(p,h){
  const cfg=p.settings.live[h.market]||{bucket:'swing',auto:false};
  let draft=liveManagementDrafts[h.market];
  if(draft&&draft.baseBucket!==cfg.bucket){delete liveManagementDrafts[h.market];draft=null}
  const bucket=draft?.bucket||cfg.bucket,name=p.catalog.find(c=>c.market===h.market)?.name||h.currency;
  const label='<span class="manage-label '+(cfg.auto?'auto':'')+'">'+(cfg.auto?'자동 관리 위임 중':'직접 거래 · 권한 회수 상태')+'</span>';
  if(!h.tradable)return label+'<div class="small mu">원화 거래 미지원</div>';
  return '<div class="live-authority">'+label+'<div class="row"><select class="live-direction" data-live-bucket="'+esc(h.market)+'" aria-label="'+esc(name)+' 실제 투자 방향">'+Object.entries(p.profiles).map(([k,r])=>'<option value="'+k+'"'+(bucket===k?' selected':'')+'>'+esc(r.name)+'</option>').join('')+'</select>'+(!cfg.auto?'<button class="bt small" data-live-authority="classify" data-authority-market="'+esc(h.market)+'">분류 적용</button>':'')+'<button class="bt small" data-live-authority="delegate" data-authority-market="'+esc(h.market)+'"'+(cfg.auto&&bucket===cfg.bucket?' disabled':'')+'>'+(cfg.auto?'투자 방향 변경 적용':'자동 관리 위임')+'</button>'+(cfg.auto?'<button class="bt small danger" data-live-authority="revoke" data-authority-market="'+esc(h.market)+'">직접 관리로 회수</button>':'')+'</div><div class="small '+(cfg.auto?'good':'mu')+'">'+(cfg.auto?esc(p.profiles[cfg.bucket]?.name||cfg.bucket)+' · 추적 손절 '+priceText(cfg.stop)+'원 · '+esc(cfg.message||'위임 보유분 청산 관리'):'분류 적용은 직접 관리를 유지합니다. 자동 청산은 위임 버튼으로 별도 설정합니다.')+'</div></div>';
}
function liveHoldingValues(h){return {
  qty:n(h.qty,8)+' / '+n(h.available,8),avg:h.avg_pending?'확인 중':h.avg?priceText(h.avg)+'원':'—',
  price:priceText(h.price),value:h.value==null?'평가 제외':n(h.value)+'원',
  pnl:h.pnl==null?'—':'<span class="'+cl(h.pnl)+'">'+sg(h.pnl)+'원 / '+sg(h.roi,2)+'%</span>'
}}
function liveHoldingTable(p,holdings){return table(['실제 보유 종목','전체 / 주문 가능 수량','평균매수가','현재가','평가액','평가손익 / 수익률','투자 방향 · 관리 권한','실제 주문'],holdings.map(h=>{
  const name=p.catalog.find(c=>c.market===h.market)?.name||h.currency;
  return [esc(name)+' <span class="mu small">'+esc(h.currency)+'</span>',...Object.entries(liveHoldingValues(h)).map(([field,value])=>'<span data-live-coin="'+esc(h.market)+'" data-live-value="'+field+'">'+value+'</span>'),liveAuthorityControls(p,h),h.tradable?'<button class="bt small buy" data-live-market="'+esc(h.market)+'" data-live-side="bid">매수</button> <button class="bt small sell" data-live-market="'+esc(h.market)+'" data-live-side="ask"'+(!h.available?' disabled':'')+'>매도</button>':'원화 거래 미지원']
}))}
function foldedHolding(p,h,mode){return !p.settings[mode][h.market]?.auto&&(!h.tradable||(h.value!=null&&h.value<5000))}
function renderActualAccount(p){
  const v=p.views.live,sync=p.sync||{},channels=sync.channels||{},realized=(p.journal.live||[]).filter(t=>t.pnl!=null).reduce((a,t)=>a+t.pnl,0);
  $('accountstatus').textContent='실제 계좌 상시 표시 · '+(v.time?tm(v.time)+' 확인':'조회 중')+(v.stale?' · 갱신 지연':'');
  $('portfolio-summary').innerHTML=cards([[v.unpriced.length?'실계좌 가격 확인 자산':'실계좌 전체 운용자산',n(v.total)+'원'],['실제 주문 가능 원화',n(v.cash)+'원'],['보유 코인 평가손익',sg(v.pnl)+'원',cl(v.pnl)],['보유 코인 평가수익률',v.roi==null?'—':sg(v.roi,2)+'%',cl(v.roi)],['주문에 묶인 원화',n(v.locked)+'원'],['앱 기록 매도손익',sg(realized)+'원',cl(realized)]]);
  $('portfolio-warning').textContent=[v.unpriced.length?'평가 제외 '+v.unpriced.join(', '):'',v.stale?'실제 잔고 갱신 지연':'',v.holdings.some(h=>h.avg_pending)?'자산 변동 수신 · 평균매수가 재확인 중':'',sync.account_error||''].filter(Boolean).join(' · ');
  const pub=channels.public||{},priv=channels.private||{};
  $('live-sync-status').textContent='시세 WebSocket '+(pub.connected?'연결됨':'재연결/조회 대체')+' · 자산 WebSocket '+(priv.connected?'연결됨'+(!priv.messages?' / 변동 대기':''):'재연결/조회 대체')+' · REST 잔고 '+(sync.rest_time?tm(sync.rest_time):'조회 중')+' · 화면 1초 갱신'+(priv.error?' · '+priv.error:'');
  // Keep a focused direction selector intact while refreshing streamed values.
  const small=v.holdings.filter(h=>foldedHolding(p,h,'live')),main=v.holdings.filter(h=>!foldedHolding(p,h,'live'));
  $('live-small-count').textContent=small.length+'종목 · 평가액 '+n(small.reduce((sum,h)=>sum+(h.value||0),0))+'원';
  [['live-holdings',main],['live-small-holdings',small]].forEach(([id,rows])=>{
    const key=JSON.stringify(rows.map(h=>[h.market,p.settings.live[h.market],liveManagementDrafts[h.market]]));
    if(liveTableStructureKeys[id]!==key||!document.activeElement?.closest?.('#'+id)){
      liveTableStructureKeys[id]=key;$(id).innerHTML=liveHoldingTable(p,rows);
    }
  });
  document.querySelectorAll('[data-live-value]').forEach(e=>{const h=v.holdings.find(h=>h.market===e.dataset.liveCoin);if(h)e.innerHTML=liveHoldingValues(h)[e.dataset.liveValue]});
  const paperSymbols=new Set([...Object.keys(p.settings.paper),...p.views.paper.holdings.map(h=>h.market)]);$('simulation-count').textContent=paperSymbols.size+'종목';
}
$('account-sync-now').onclick=()=>portfolioCommand({action:'portfolio_sync'});
['live-holdings','live-small-holdings'].forEach(id=>$(id).addEventListener('click',e=>{const b=e.target.closest('[data-live-market]');if(b&&!orderBusy&&!busy)openOrder(b.dataset.liveMarket,b.dataset.liveSide,{mode:'live'})}));
['live-holdings','live-small-holdings'].forEach(id=>$(id).addEventListener('change',e=>{
  const market=e.target.dataset?.liveBucket,p=portfolioData;if(!market||!p||busy)return;
  const cfg=p.settings.live[market]||{bucket:'swing',auto:false};
  if(!p.profiles[e.target.value])return;
  liveManagementDrafts[market]={bucket:e.target.value,baseBucket:cfg.bucket};e.target.blur();renderActualAccount(p);
}));
async function changeLiveAuthority(market,action){
  const p=portfolioData;if(!p||busy||orderBusy)return;
  const h=p.views.live.holdings.find(h=>h.market===market);if(!h?.tradable)return;
  const cfg=p.settings.live[market]||{bucket:'swing',auto:false},auto=action==='delegate';
  if(action==='classify'){
    const draft=liveManagementDrafts[market];
    if(cfg.auto)return;
    const bucket=draft?.baseBucket===cfg.bucket?draft.bucket:cfg.bucket;
    if(!p.profiles[bucket])return;
    if(document.activeElement?.closest?.('#live-holdings, #live-small-holdings'))document.activeElement.blur?.();
    const saved=await portfolioCommand({action:'portfolio_config',mode:'live',market,bucket,auto:false});
    if(saved){
      delete liveManagementDrafts[market];
      toast(market+' · '+p.profiles[bucket].name+' 분류 적용 완료 · 직접 관리 유지');
      drawPortfolio(portfolioData);
    }
    return;
  }
  if(!['delegate','revoke'].includes(action)||(!auto&&!cfg.auto))return;
  const draft=liveManagementDrafts[market],bucket=auto&&draft?.baseBucket===cfg.bucket?draft.bucket:cfg.bucket;
  if(auto&&cfg.auto&&bucket===cfg.bucket)return;
  const rule=p.profiles[bucket];if(!rule)return;
  const name=p.catalog.find(c=>c.market===market)?.name||market;
  const question=auto?'실제 계좌 '+name+' ('+market+')\n투자 방향: '+rule.name+'\n주문 가능 보유량 '+n(h.available,8)+'개에 자동 손절·추적 청산 권한을 위임합니다.\n'+rule.description+'\n현재가로 손절선과 보유 시간을 새로 설정합니다. 조건 도달 시 실제 매도 주문이 접수될 수 있습니다. 실거래 자동 신규 매수는 차단됩니다. 적용할까요?':
    '실제 계좌 '+name+'의 자동 거래 권한을 회수하고 직접 관리로 전환할까요?\n이후 자동 주문을 중단합니다. 이미 접수된 주문은 취소되지 않으며 주문 내역에서 별도로 처리해야 합니다.';
  if(!confirm(question))return;
  if(document.activeElement?.closest?.('#live-holdings, #live-small-holdings'))document.activeElement.blur?.();
  delete liveManagementDrafts[market];
  await portfolioCommand({action:'portfolio_config',mode:'live',market,bucket,auto});
}
['live-holdings','live-small-holdings'].forEach(id=>$(id).addEventListener('click',e=>{const b=e.target.closest('[data-live-authority]');if(b)return changeLiveAuthority(b.dataset.authorityMarket,b.dataset.liveAuthority)}));
renderSelectionSummary();
