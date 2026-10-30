/* V3 independent funded simulation. All values rendered through escaping helpers. */
let v3Busy=false,v3Data=null;
document.querySelectorAll('#auto-v3-panel button').forEach(b=>b.disabled=false);
async function v3Command(body){
  if(v3Busy)return;v3Busy=true;
  document.querySelectorAll('#auto-v3-panel button').forEach(b=>b.disabled=true);
  try{const message=await portfolioRequest({...body,mode:'paper'});toast(typeof message==='string'?message:'V3 적용 완료');await refreshPortfolio()}
  catch(e){toast(e.message,true)}
  finally{v3Busy=false;document.querySelectorAll('#auto-v3-panel button').forEach(b=>b.disabled=false)}
}
function drawAutoV3(d){
  if(d?.allocation)drawAllocation(d.allocation);
  if(!d||!$('v3-status'))return;v3Data=d;drawBreakoutComparison(d);
  const s=d.state,positions=Object.entries(s.positions),roi=s.funded?(d.equity/s.funded-1)*100:0,u=d.universe||{};
  if($('v3-universe-status'))$('v3-universe-status').textContent='전체 감시 '+s.markets.length+'종목 · 유동성 1차 통과 '+(u.eligible??'확인 중')+'종목 · 정밀 후보 '+(u.candidates?.length??0)+'종목'+(u.time?' · 전체 시세 갱신 '+tm(u.time):' · 목록 수집 중')+(u.error?' · '+u.error:'');
  $('v3-status').textContent=(d.running?'V3 신규 매수 실행':'V3 신규 매수 정지')+' · 호가 '+d.feed_state+' · '+(s.review?'운용 검토 잠금 · ':s.day_stop?'당일 신규 매수 중단 · ':'')+d.status+(d.feed_error?' · '+d.feed_error:'')+(d.scan_error?' · '+d.scan_error:'');
  $('v3-status').className='msg '+(d.feed_state==='정상'&&!s.day_stop&&!s.review?'mu':'warn');
  $('v3-summary').innerHTML=cards([['단타 감시 종목',s.markets.length+'종목'],['부여 원금',n(s.funded)+'원'],['손익 반영 운용자산',n(d.equity)+'원'],['순손익 / 수익률',sg(d.pnl)+'원 / '+sg(roi,2)+'%',cl(d.pnl)],['예약 제외 현금',n(d.free_cash)+'원'],['완료 포지션 / 승률',n(d.stats.closed)+'건 / '+(d.stats.closed?n(d.stats.wins/d.stats.closed*100,1)+'%':'—')]]);
  const deep=u.candidates||s.markets.slice(0,12);
  // Keep the open popup and keyboard focus stable across the one-second redraw.
  if(!$('v3-signals').querySelector?.('.scalp-name:hover, .scalp-name:focus-within')) $('v3-signals').innerHTML=deep.map(m=>{
    const sig=d.signals[m]||{},q=d.quotes[m]||{};
    return '<div class="c scalp-card"><div class="row between">'+scalpTooltip(m,d)+'<span class="tag">정밀 분석</span></div><div class="small '+(sig.ok?'good':'mu')+'">'+esc(sig.reason||'마감 봉 준비 중')+'</div><div class="small mu">15분 방향 → 5분 눌림 → 1분 확인</div><div class="small">ATR '+n(sig.atr||0,2)+' · ADX '+n(sig.adx||0,1)+' · VWAP '+n(sig.vwap||0,2)+'</div><div class="small mu">'+esc(sig.vwap_method||'VWAP 준비 중')+' · 호가 '+(q.age!=null?n(q.age,2)+'초 / '+esc(q.source):'대기')+'</div>'+scalpBreakoutStatus(m,d)+'</div>'
  }).join('');
  const visibleMarkets=[...new Set([...s.markets,...Object.keys(s.positions),...s.orders.filter(o=>o.status==='wait').map(o=>o.market)])].filter(m=>u.rows?.[m]?.eligible!==false||s.positions[m]?.volume>0||s.orders.some(o=>o.market===m&&o.status==='wait'));
  $('v3-positions').innerHTML=table(['종목','상태','수량','평균 매수가','진입 예정·대기 가격','매도 예약 가격','매도 예약 총액 (잔량)','손절·추적선','청산 상태'],visibleMarkets.map(m=>{
    const p=s.positions[m],orders=s.orders.filter(o=>o.market===m&&o.status==='wait'),buys=orders.filter(o=>o.side==='bid'),sells=orders.filter(o=>o.side==='ask');
    const prices=list=>list.map(o=>priceText(o.price)+'원').join('<br>');
    const row=u.rows?.[m],excluded=row?.eligible===false;
    return [esc(m)+(row?.name?' · '+esc(row.name):''),p?'모의 보유':buys.length?'매수 예약 대기':excluded?'진입 제외':deep.includes(m)?'정밀 진입 대기':'전체 감시·순환 대기',n(p?.volume||0,8),p?priceText(p.entry):'—',buys.length?prices(buys):p?'추가 매수 예약 없음':'미정 · 진입 신호 대기',sells.length?prices(sells):'예약 없음',sells.length?n(sells.reduce((sum,o)=>sum+o.price*Math.max(0,o.volume-o.filled),0),2)+'원':'예약 없음',p?priceText(p.stop):'—',esc(p?.exiting||(p?'보호 감시':(s.funded?'':'운용자금 미설정 · ')+(excluded?row.reason:d.signals[m]?.reason||row?.reason||'신호 확인 중')))]
  }));
  $('v3-orders').innerHTML=table(['예약 시각','종목','방향','예약 가격','수량 / 체결','종류','조작'],s.orders.filter(o=>o.status==='wait').map(o=>[tm(o.created),esc(o.market),o.side==='bid'?'매수':'매도',priceText(o.price),n(o.volume,8)+' / '+n(o.filled,8),esc(o.kind==='entry'?'눌림 매수':o.kind==='partial'?'30% 분할익절':'IOC 청산'),'<button class="bt small" data-v3-cancel="'+esc(o.id)+'">취소</button>']));
  $('v3-fills').innerHTML=table(['체결 시각','종목','방향','가격','수량','수수료'],s.fills.slice(-30).reverse().map(f=>[tm(f.time),esc(f.market)+'<br><span class="small mu">'+(f.strategy==='breakout'?'초기 돌파':'눌림')+'</span>',f.side==='bid'?'매수':'매도',priceText(f.price),n(f.volume,8),n(f.fee,2)]));
  $('v3-closed').innerHTML=table(['완료 시각','종목','순손익','MFE / MAE','청산 이유'],s.closed.slice(-20).reverse().map(r=>[tm(r.time),esc(r.market)+'<br><span class="small mu">'+(r.strategy==='breakout'?'초기 돌파':'눌림')+'</span>','<span class="'+cl(r.net)+'">'+sg(r.net)+'원</span>',sg(r.mfe_pct,2)+'% / '+sg(r.mae_pct,2)+'%',esc(r.reason)]));
  const names={monitor_ms:'실제 감시 간격',data_age_ms:'호가 나이',decision_ms:'호가 수신→모의 청산 주문 기록'};
  $('v3-metrics').textContent=Object.entries(names).map(([k,name])=>name+' p95 '+(d.metrics[k]?.p95==null?'대기':n(d.metrics[k].p95,1)+'ms')).join(' · ')+' · 기록 누락 '+n(d.record_dropped||0)+'건 · 실제 거래소 송신/체결 시간은 모의에서 측정하지 않습니다.';
  $('v3-events').textContent=s.events.slice(-35).map(e=>tm(e.time)+' '+e.message).join('\n')||'V3 새 이벤트 없음';
}
$('v3-config').onclick=()=>v3Command({action:'auto_v3_config',budget:$('v3-budget').value,scope:'all'});
$('v3-start').onclick=()=>v3Command({action:'auto_v3_start'});
$('v3-pause').onclick=()=>v3Command({action:'auto_v3_pause'});
$('v3-flatten').onclick=()=>v3Command({action:'auto_v3_flatten'});
$('v3-reset').onclick=()=>{if(confirm('V3 완료 기록을 별도 파일로 보관하고 모의 장부를 초기화할까요?'))v3Command({action:'auto_v3_reset'})};
$('v3-orders').addEventListener('click',event=>{const b=event.target.closest('[data-v3-cancel]');if(b)v3Command({action:'auto_v3_cancel',id:b.dataset.v3Cancel})});
