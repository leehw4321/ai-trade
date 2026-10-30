import fs from 'node:fs/promises';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';

export async function runDashboardTests(root=fileURLToPath(new URL('.',import.meta.url))){
  const html=await fs.readFile(root+'/trend_dashboard.html','utf8');
  const ui=await fs.readFile(root+'/portfolio_ui.js','utf8')+'\n'+await fs.readFile(root+'/recommendations_ui.js','utf8')+'\n'+await fs.readFile(root+'/selection_ui.js','utf8')+'\n'+await fs.readFile(root+'/exchange_ui.js','utf8')+'\n'+await fs.readFile(root+'/scalp_tooltip.js','utf8')+'\n'+await fs.readFile(root+'/auto_v3_ui.js','utf8')+'\n'+await fs.readFile(root+'/market_ui.js','utf8')+'\n'+await fs.readFile(root+'/research_ui.js','utf8');
  const source=html.match(/<script>([\s\S]*?)<\/script>/)[1].replace('__PORTFOLIO_JS__',()=>ui);
  new vm.Script(source);
  const elements=new Map();
  const el=id=>{if(!elements.has(id))elements.set(id,{id,dataset:{},style:{},hidden:false,value:'',textContent:'',innerHTML:'',className:'',scrollTop:0,
    events:{},setAttribute(k,v){this[k]=v},addEventListener(k,f){this.events[k]=f},replaceChildren(){this.innerHTML=''},
    showModal(){this.open=true},close(){this.open=false}});return elements.get(id)};
  const percents=['.25','.5','.75','1'].map(p=>({dataset:{percent:p}}));
  const tabs=['all','long','swing','short'].map(p=>({dataset:{recTab:p},setAttribute(){}}));
  const view={cash:100000,locked:0,total:200000,pnl:10000,roi:10,holdings:[],unpriced:[],time:Date.now()/1000,stale:false};
  const profiles={long:{name:'장기투자',description:'일봉'},swing:{name:'스윙',description:'4시간'},short:{name:'단타',description:'15분'}};
  const comparison={paper:{total:200000,initial:200000,return_pct:0,fees:0,fills:0,stats:{closed:0,wins:0,profit_factor:null},running:true,trial:{},samples:[]},
                    live:{total:200000,pnl:10000,holding_roi:10,fills:0,fees:0,unpriced:[]}};
  const fixture={now:Date.now()/1000,mode:'paper',running:false,status:'idle',states:{paper:{positions:{},trades:[],cash:1000000,peak:1000000,day_start:1000000,cooldown:{}}},
    equity:{paper:1000000},signals:{},quotes:{},events:[],portfolio:{mode:'paper',running:true,seeded:true,error:'',views:{paper:view,live:view},
      settings:{paper:{'KRW-NEW':{bucket:'short',auto:true}},live:{}},orders:{paper:[],live:[]},journal:{paper:[],live:[]},watch:['KRW-NEW'],prices:{'KRW-NEW':100000},
      catalog:[{market:'KRW-NEW',name:'새 종목'},{market:'KRW-ONG',name:'온톨로지가스'}],profiles,comparison}};
  fixture.portfolio.views.live={...view,holdings:[{currency:'ONG',market:'KRW-ONG',qty:20,available:20,avg:5000,price:5000,value:100000,pnl:0,roi:0,tradable:true}]};
  fixture.portfolio.views.live.holdings.push({currency:'DUST',market:'KRW-DUST',qty:1,available:1,avg:100,price:100,value:100,pnl:0,roi:0,tradable:true},{currency:'OLD',market:'KRW-OLD',qty:1,available:1,avg:100,price:null,value:null,pnl:null,roi:null,tradable:false});
  fixture.portfolio.exchange={orders:[{uuid:'external-test',market:'KRW-ONG',side:'bid',ord_type:'limit',state:'wait',price:'5000',volume:20,filled:5,remaining:15,locked:75037.5,created:Date.now()/1000,owned:false}],requests:[],time:Date.now()/1000,stale:false,error:''};
  const row={market:'KRW-NEW',name:'새 종목',bucket:'short',score:75,price:100000,turnover:1e10,day_change:1,daily:{return7:2,return30:5,volume_growth:10,history:199,atr_pct:1},
             fundamentals:{},project:{summary:'<img src=x onerror=alert(1)>'},status:'관찰',components:{liquidity:80},spread:.05,risks:['자료 미확인'],outlook:'조건 확인'};
  const research={time:Date.now()/1000,stale:false,rankings:{all:[row],long:[],swing:[],short:[row]},weights:{short:{liquidity:100}},labels:{liquidity:'거래대금'},counts:{universe:298,eligible:130,daily_compared:120,deep_candidates:30,onchain:80,daily_failed:10}};
  const requests=[];
  let selectionItems=[],allowConfirm=false,shutdownSupported=true;const confirmations=[];
  const ctx=vm.createContext({document:{getElementById:el,querySelector:()=>({content:'test'}),querySelectorAll:q=>q==='[data-percent]'?percents:q==='[data-rec-tab]'?tabs:[],documentElement:{dataset:{}},activeElement:null},
    localStorage:{getItem:()=>null,setItem(){}},setInterval(){},setTimeout(){return 1},clearTimeout(){},URL,Date,Number,Math,console,confirm:q=>{confirmations.push(q);return allowConfirm},
    fetch:async(url,options={})=>{
      let data;if(url==='/api/data')data=fixture;else if(url==='/api/health')data={shutdown_supported:shutdownSupported};else if(url==='/api/recommendations')data=research;else if(url==='/api/news')return new Promise(()=>{});
      else {const body=JSON.parse(options.body);requests.push(body);if(body.action==='portfolio_quote')data={ok:true,message:{price:'100000',current:100000,bid:100000,ask:100050,tick:'100',cash:body.mode==='live'?50000:100000,available:1,bid_fee:.0005,ask_fee:.0005}};
        else if(body.action==='portfolio_preview')data={ok:true,message:{...body,ticket:'ticket-'+body.mode,total:10000,estimated_fee:5}};
        else if(body.action==='portfolio_submit')data={ok:true,message:'접수'};
        else if(body.action==='portfolio_config'){fixture.portfolio.settings[body.mode][body.market]={bucket:body.bucket,auto:body.auto,stop:4500};data={ok:true,message:'가짜 계좌 설정 적용'}}
        else if(body.action==='portfolio_exchange_preview')data={ok:true,message:{...body,ticket:'amend-fake',market:'KRW-ONG',side:'bid',volume:'15',remaining:'15',old_price:'5000',total:73500,estimated_fee:36.75}};
        else if(body.action==='portfolio_exchange_submit')data={ok:true,message:'가짜 주문 수정 전송'};
        else if(body.action==='portfolio_exchange_cancel')data={ok:true,message:'가짜 주문 취소 전송'};
        else if(body.action==='portfolio_selection_preview'){selectionItems=body.items;data={ok:true,message:{group:'group-test',items:body.items.map(i=>({...i,volume:Number(i.amount)/Number(i.price),total:Number(i.amount),estimated_fee:Number(i.amount)*.0005,observation:{value:i.period_value,unit:i.period_unit}}))}}}
        else if(body.action==='portfolio_selection_submit')data={ok:true,message:{requested:selectionItems.length,results:selectionItems.map(i=>({market:i.market,mode:i.mode,identifier:'fake-'+i.market,status:'done',message:'가짜 거래소 접수'}))}};
        else if(body.action.startsWith('auto_v3_'))data={ok:true,message:'V3 모의 적용'};
        else if(body.action==='app_shutdown')data={ok:true,message:'기록 저장 완료 · 종료 중'};
        else throw Error('Unexpected action '+body.action)}
      return {ok:true,json:async()=>data};
    }});
  new vm.Script(source).runInContext(ctx);await new Promise(resolve=>setImmediate(resolve));
  ctx.draw(fixture);ctx.renderResearch(research);
  ctx.drawModelResearch({available:false,status:'not_ready',message:'연구 결과 대기'});
  assert.equal(el('model-research-periods').innerHTML,'');
  assert.equal(el('model-research-report').hidden,true);
  assert.equal(ctx.researchMetric(null),'—');
  const modelResearch={available:true,status:'selected',selected_config:{id:'test',name:'<img src=x>'},approved_paper:true,
    report_available:true,selected_metrics:{train:{return_pct:1,trades:20,max_drawdown_pct:3},validation:{return_pct:2,trades:10,max_drawdown_pct:2},test:{return_pct:null,trades:null,max_drawdown_pct:null}},selection_rule:'검증 구간 선택',limitations:['10종목 표본']};
  ctx.drawModelResearch(modelResearch);
  assert.ok(el('model-research-summary').textContent.includes('<img src=x>'));
  assert.ok(!el('model-research-periods').innerHTML.includes('<img'));
  assert.ok(el('model-research-periods').innerHTML.includes('1.00%'));
  assert.ok(el('model-research-periods').innerHTML.includes('미사용 평가'));
  assert.ok(el('model-research-periods').innerHTML.includes('—'));
  assert.equal(el('model-research-report').hidden,false);
  ctx.drawModelResearch({...modelResearch,status:'no_qualified_model',approved_paper:false,selected_config:null});
  assert.ok(el('model-research-summary').textContent.includes('승인 없음'));
  assert.equal(requests.length,0);
  assert.equal(el('portfolio-view-live')['aria-pressed'],'true');
  ctx.drawAllocation({max_position:.25,max_total:.5,max_positions:2,risk:.0025,max_open_risk:.005,volatility_target:.005});
  el('allocation-open').onclick();assert.equal(el('allocation-dialog').open,true);
  assert.equal(el('allocation-max_position').value,25);
  el('allocation-max_position').value=15;
  await el('allocation-save').onclick();
  assert.equal(requests.at(-1).action,'auto_v3_allocation');
  assert.equal(requests.at(-1).max_position,.15);
  el('allocation-close').onclick();assert.equal(el('allocation-dialog').open,false);
  assert.ok(ctx.fearVisual({id:'fear',value:0,detail:'fear'}).includes('hsl(0 75% 42%)'));
  assert.ok(ctx.fearVisual({id:'fear',value:50,detail:'neutral'}).includes('hsl(60 75% 42%)'));
  assert.ok(ctx.fearVisual({id:'fear',value:100,detail:'greed'}).includes('hsl(120 75% 42%)'));
  assert.ok(!ctx.fearVisual({id:'fear',value:null}).includes('role="meter"'));
  assert.equal(ctx.document.documentElement.dataset.theme,'gray');
  el('theme-ivory').onclick();assert.equal(ctx.document.documentElement.dataset.theme,'ivory');
  el('theme-slate').onclick();assert.equal(ctx.document.documentElement.dataset.theme,'slate');
  el('theme-toggle').onclick();assert.equal(ctx.document.documentElement.dataset.theme,'gray');
  vm.runInContext("setTheme('unknown')",ctx);assert.equal(ctx.document.documentElement.dataset.theme,'gray');
  assert.ok(el('recommendations-list').innerHTML.includes('새 종목'));
  assert.ok(el('recommendations-list').innerHTML.includes('&lt;img'));
  assert.ok(!el('recommendations-list').innerHTML.includes('<img'));
  assert.ok(el('parallel-comparison').innerHTML.includes('실제 투자'));
  assert.ok(el('parallel-comparison').innerHTML.includes('독립 가상 계좌'));
  assert.ok(el('live-holdings').innerHTML.includes('온톨로지가스'));
  assert.ok(el('buckets').innerHTML.includes('온톨로지가스'));
  assert.ok(!el('buckets').innerHTML.includes('새 종목'));
  el('portfolio-view-paper').onclick();assert.ok(!el('buckets').innerHTML.includes('온톨로지가스'));
  assert.equal(fixture.portfolio.mode,'paper');
  el('portfolio-view-live').onclick();assert.ok(el('buckets').innerHTML.includes('온톨로지가스'));
  assert.equal(fixture.portfolio.mode,'paper');
  el('portfolio-view-all').onclick();
  assert.ok(el('accountstatus').textContent.includes('실제 계좌 상시'));
  assert.ok(html.indexOf('<h2>시황 시그널')<html.indexOf('투자 우선순위 TOP 10'));
  assert.match(html,/<details id="recommendations-panel"[^>]*>/);assert.match(html,/<details id="comparison-panel"[^>]*>/);
  assert.ok(!html.match(/<details id="(?:recommendations|comparison)-panel"[^>]*\bopen\b/));
  assert.ok(html.indexOf('id="comparison-panel"')>html.indexOf('id="portfolio-trades"'));
  assert.ok(el('live-holdings').innerHTML.includes('data-live-bucket="KRW-ONG"'));
  assert.ok(el('live-holdings').innerHTML.includes('자동 관리 위임'));
  assert.ok(!el('live-holdings').innerHTML.includes('DUST'));assert.ok(!el('live-holdings').innerHTML.includes('OLD'));
  assert.ok(el('live-small-holdings').innerHTML.includes('DUST'));assert.ok(el('live-small-holdings').innerHTML.includes('OLD'));
  assert.ok(!html.match(/<details id="live-small-panel"[^>]*\bopen\b/));
  assert.ok(el('exchange-open-orders').innerHTML.includes('업비트 앱·웹'));
  assert.ok(el('exchange-open-orders').innerHTML.includes('가격·수량 수정'));
  assert.equal(requests.filter(r=>r.action==='portfolio_config').length,0);
  el('live-holdings').events.change({target:{dataset:{liveBucket:'KRW-ONG'},value:'long',blur(){}}});
  assert.equal(requests.filter(r=>r.action==='portfolio_config').length,0);
  await ctx.changeLiveAuthority('KRW-ONG','delegate');
  assert.equal(requests.filter(r=>r.action==='portfolio_config').length,0);
  assert.ok(confirmations.at(-1).includes('장기투자'));assert.ok(confirmations.at(-1).includes('실제 매도'));
  allowConfirm=true;await ctx.changeLiveAuthority('KRW-ONG','delegate');
  assert.equal(requests.at(-1).mode,'live');assert.equal(requests.at(-1).bucket,'long');assert.equal(requests.at(-1).auto,true);
  assert.equal(fixture.portfolio.mode,'paper');assert.equal(fixture.portfolio.running,true);
  assert.ok(el('live-holdings').innerHTML.includes('직접 관리로 회수'));
  el('live-holdings').events.change({target:{dataset:{liveBucket:'KRW-ONG'},value:'short',blur(){}}});
  await ctx.changeLiveAuthority('KRW-ONG','revoke');
  assert.equal(requests.at(-1).auto,false);assert.equal(requests.at(-1).bucket,'long');
  assert.equal(fixture.portfolio.settings.paper['KRW-NEW'].auto,true);
  // Moving the same real holding repeatedly must replace its former card.
  const assertSingleLiveBucket=bucket=>{
    const markup=el('buckets').innerHTML;
    assert.equal((markup.match(/data-market="KRW-ONG" data-portfolio-mode="live"/g)||[]).length,1);
    for(const section of markup.matchAll(/<section class="bucket" data-bucket="([^"]+)">([\s\S]*?)<\/section>/g)){
      assert.equal(section[2].includes('data-market="KRW-ONG" data-portfolio-mode="live"'),section[1]===bucket);
    }
  };
  el('portfolio-view-live').onclick();
  // Apply must work even when the currently selected classification is unchanged.
  assert.ok(!el('live-holdings').innerHTML.match(/data-live-authority="classify"[^>]*\bdisabled/));
  const unchangedRequests=requests.length;
  await el('live-holdings').events.click({target:{closest:()=>({dataset:{authorityMarket:'KRW-ONG',liveAuthority:'classify'}})}});
  assert.equal(requests.length,unchangedRequests+1);
  assert.equal(requests.at(-1).action,'portfolio_config');
  assert.equal(requests.at(-1).bucket,'long');
  assert.equal(requests.at(-1).auto,false);
  assert.ok(el('toast').textContent.includes('분류 적용 완료'));
  for(const bucket of ['swing','short','long','swing']){
    el('live-holdings').events.change({target:{dataset:{liveBucket:'KRW-ONG'},value:bucket,blur(){}}});
    await ctx.changeLiveAuthority('KRW-ONG','classify');
    assert.equal(fixture.portfolio.settings.live['KRW-ONG'].auto,false);
    assertSingleLiveBucket(bucket);
  }
  for(const bucket of ['short','long','swing']){
    ctx.document.activeElement={closest:()=>({})};
    await el('buckets').events.change({target:{value:bucket,blur(){},matches:q=>q==='[data-bucket-select]',closest:()=>({dataset:{market:'KRW-ONG',portfolioMode:'live'}})}});
    ctx.document.activeElement=null;
    assertSingleLiveBucket(bucket);
    assert.ok(el('live-holdings').innerHTML.includes('value="'+bucket+'" selected'));
  }
  fixture.portfolio.views.live.holdings.push({...fixture.portfolio.views.live.holdings[0]});
  ctx.draw(fixture);assertSingleLiveBucket('swing');
  fixture.portfolio.views.live.holdings.pop();
  el('portfolio-view-all').onclick();
  allowConfirm=false;
  await ctx.cancelExchangeOrder('external-test');assert.equal(requests.filter(r=>r.action==='portfolio_exchange_cancel').length,0);
  allowConfirm=true;await ctx.cancelExchangeOrder('external-test');assert.equal(requests.at(-1).uuid,'external-test');allowConfirm=false;
  ctx.openExchangeEdit('external-test');assert.equal(el('exchange-price').value,'5000');assert.equal(el('exchange-volume').value,15);
  assert.equal(el('exchange-remain').checked,true);assert.equal(el('exchange-volume').disabled,true);
  el('exchange-price').value='4900';await el('exchange-preview').onclick();
  assert.equal(requests.at(-1).action,'portfolio_exchange_preview');assert.equal(requests.at(-1).remain_only,true);
  assert.ok(el('exchange-review').textContent.includes('잔량 유지'));
  el('exchange-price').value='4800';el('exchange-price').oninput();assert.equal(el('exchange-submit').hidden,true);
  await el('exchange-preview').onclick();await el('exchange-submit').onclick();await el('exchange-submit').onclick();
  assert.equal(requests.filter(r=>r.action==='portfolio_exchange_submit').length,1);assert.equal(fixture.portfolio.mode,'paper');
  await ctx.openOrder('KRW-NEW','bid',{mode:'live',bucket:'short',amount:'10000'});
  assert.equal(el('order-mode-select').value,'live');assert.equal(el('order-bucket-select').value,'short');
  assert.equal(el('order-volume').value,'0.10000000');
  assert.ok(el('order-balance').textContent.includes('50,000'));
  await el('order-preview').onclick();assert.equal(el('order-submit').hidden,false);
  assert.equal(requests.at(-1).mode,'live');assert.equal(requests.at(-1).bucket,'short');
  el('order-price').value='99900';el('order-price').oninput();assert.equal(el('order-submit').hidden,true);
  await el('order-preview').onclick();await el('order-submit').onclick();
  assert.equal(requests.filter(b=>b.action==='portfolio_submit').length,1);
  assert.equal(requests.at(-1).ticket,'ticket-live');assert.equal(fixture.portfolio.mode,'paper');
  await ctx.openOrder('KRW-NEW','bid',{mode:'paper',bucket:'swing',amount:'10000'});
  await el('order-preview').onclick();assert.equal(requests.at(-1).mode,'paper');
  const form=ctx.selectionForm(row);assert.ok(form.includes('type="checkbox"'));assert.ok(form.includes('data-plan-field="amount"'));assert.ok(form.includes('value="month"'));
  const wrapper={dataset:{selectionMarket:'KRW-NEW'}};
  ctx.changeSelection({target:{dataset:{planField:'checked'},checked:true,closest:()=>wrapper}});
  ctx.changeSelection({target:{dataset:{planField:'mode'},value:'live',closest:()=>wrapper}});
  await el('selection-preview').onclick();assert.equal(requests.at(-1).action,'portfolio_selection_preview');assert.equal(selectionItems[0].mode,'live');
  assert.ok(el('selection-review').innerHTML.includes('실제 계좌'));assert.equal(selectionItems[0].period_unit,'hour');
  await el('selection-submit').onclick();assert.equal(requests.filter(r=>r.action==='portfolio_selection_submit').length,1);
  assert.equal(ctx.selectedItems().length,0);
  const simHTML=await fs.readFile(root+'/simulation_dashboard.html','utf8');
  const sim=vm.createContext({document:ctx.document,fetch:async()=>({ok:true,json:async()=>fixture}),setTimeout(){},Date,Number,Math});
  new vm.Script(simHTML.match(/<script>([\s\S]*?)<\/script>/)[1]).runInContext(sim);sim.draw(fixture);
  assert.ok(el('positions').innerHTML.includes('새 종목'));assert.ok(!el('positions').innerHTML.includes('온톨로지가스'));
  const v3={running:false,feed_state:'정상',equity:100000,pnl:0,free_cash:90000,status:'모의 대기',stats:{closed:0,wins:0},metrics:{},signals:{'KRW-BTC':{reason:'<img src=x onerror=alert(1)>'}},quotes:{},state:{funded:100000,positions:{},markets:['KRW-BTC'],orders:[{id:'v3-test',market:'KRW-BTC',side:'bid',price:100000,volume:.1,filled:0,created:Date.now()/1000,kind:'entry',status:'wait'}],fills:[],closed:[],events:[]}};
  // Exercise the real dashboard dispatcher, so renamed calls cannot hide all markets.
  el('v3-summary').innerHTML='';el('v3-signals').innerHTML='';
  fixture.auto_v3=v3;ctx.draw(fixture);
  assert.ok(el('v3-summary').innerHTML.includes('100,000'));
  assert.ok(el('v3-signals').innerHTML.includes('KRW-BTC'));
  ctx.drawAutoV3(v3);assert.ok(el('v3-signals').innerHTML.includes('&lt;img'));assert.ok(!el('v3-signals').innerHTML.includes('<img'));
  assert.ok(el('v3-orders').innerHTML.includes('v3-test'));assert.ok(el('v3-summary').innerHTML.includes('100,000'));
  v3.state.orders.push({id:'v3-sell',market:'KRW-BTC',side:'ask',price:110000,volume:.3,filled:.1,created:Date.now()/1000,kind:'partial',status:'wait'});
  ctx.drawAutoV3(v3);
  assert.ok(el('v3-positions').innerHTML.includes('100,000원'));
  assert.ok(el('v3-positions').innerHTML.includes('110,000원'));
  assert.ok(el('v3-positions').innerHTML.includes('22,000.00원'));
  fixture.auto_v3=v3;sim.draw(fixture);
  assert.ok(el('monitor-count').textContent.includes('단타 감시 2종목'));
  assert.ok(el('positions').innerHTML.includes('진입 예정·대기 가격'));
  assert.ok(el('positions').innerHTML.includes('110,000원'));
  assert.ok(el('positions').innerHTML.includes('22,000원'));
  assert.ok(el('positions').innerHTML.includes('미정 · 진입 신호 대기'));
  v3.state.positions['KRW-BTC']={volume:.7,entry:100000,cost:70035,stop:99000,bucket:'short'};
  ctx.draw(fixture);assert.ok(el('buckets').innerHTML.includes('시뮬레이션 보유 · V3'));
  el('portfolio-view-live').onclick();assert.ok(!el('buckets').innerHTML.includes('시뮬레이션 보유 · V3'));
  el('portfolio-view-paper').onclick();assert.ok(el('buckets').innerHTML.includes('시뮬레이션 보유 · V3'));assert.ok(!el('buckets').innerHTML.includes('온톨로지가스'));
  el('portfolio-view-all').onclick();
  const liveAsset={dataset:{market:'KRW-ONG',portfolioMode:'live'}};
  el('buckets').events.change({target:{matches:q=>q==='[data-bucket-select]',closest:()=>liveAsset,value:'swing',blur(){}}});
  await new Promise(resolve=>setImmediate(resolve));assert.equal(requests.at(-1).mode,'live');assert.equal(requests.at(-1).bucket,'swing');assert.equal(fixture.portfolio.mode,'paper');
  el('v3-budget').value='100000';el('v3-btc').checked=true;el('v3-eth').checked=false;
  await el('v3-config').onclick();assert.equal(requests.at(-1).action,'auto_v3_config');assert.equal(requests.at(-1).mode,'paper');assert.equal(requests.at(-1).scope,'all');
  await el('v3-start').onclick();assert.equal(requests.at(-1).action,'auto_v3_start');
  await el('v3-pause').onclick();assert.equal(requests.at(-1).action,'auto_v3_pause');
  await el('v3-flatten').onclick();assert.equal(requests.at(-1).action,'auto_v3_flatten');
  await el('v3-orders').events.click({target:{closest:()=>({dataset:{v3Cancel:'v3-test'}})}});
  // Delegated listener starts an async command; wait for its request and refresh.
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.at(-1).action,'auto_v3_cancel');assert.equal(requests.at(-1).id,'v3-test');
  allowConfirm=false;await el('app-shutdown').onclick();assert.equal(requests.filter(r=>r.action==='app_shutdown').length,0);
  allowConfirm=true;shutdownSupported=false;await el('app-shutdown').onclick();
  assert.ok(el('app-shutdown-status').textContent.includes('한 번 재실행'));assert.equal(requests.filter(r=>r.action==='app_shutdown').length,0);
  shutdownSupported=true;const shutting=el('app-shutdown').onclick();await el('app-shutdown').onclick();await shutting;
  assert.equal(requests.filter(r=>r.action==='app_shutdown').length,1);assert.equal(requests.at(-1).confirm,true);
  assert.ok(el('app-shutdown-status').textContent.includes('기록 저장 완료'));assert.equal(el('app-shutdown').textContent,'종료 요청 완료');
  ctx.offline('connection lost');assert.ok(el('status').textContent.includes('자동 감시 중단'));
  const result={syntax:true,escapedResearch:true,parallelComparison:true,collapsedPanels:true,foldedDustAndUnsupported:true,exchangeCancelConfirmation:true,exchangeEditReview:true,exchangeEditOnce:true,explicitLiveDelegation:true,cancelledDelegationNoRequest:true,revokeUsesActiveDirection:true,paperTrialPreserved:true,orderModeIsolation:true,editingInvalidatesReview:true,oneSubmit:true,actualONGAlwaysVisible:true,selectionReview:true,separateSimulationWindow:true,v3Escaping:true,v3BudgetSelection:true,v3StartPauseFlattenCancel:true,monitorCount:true,entryAndSellReservationPrices:true,remainingReservationAmount:true,shutdownConfirmation:true,shutdownOnce:true,shutdownStatusPreserved:true,holdingsOnly:true,viewDoesNotSwitchTradingMode:true,v3HoldingsIncluded:true,liveCardClassificationRouting:true};
  return result;
}
