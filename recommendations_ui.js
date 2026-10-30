let researchData=null,researchTab='all',researchKey='';
const bucketName=b=>({long:'장기투자',swing:'스윙',short:'단타'}[b]||b);
const safeResearchLink=url=>{try{const u=new URL(url);return u.protocol==='https:'?u.href:''}catch(e){return ''}};
const sourceLink=(url,label)=>{const href=safeResearchLink(url);return href?'<a class="rec-source" href="'+esc(href)+'" target="_blank" rel="noopener noreferrer">'+esc(label)+' ↗</a>':''};
function renderResearch(d){
  researchData=d;const c=d.counts||{},when=d.time?new Date(d.time*1000).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}):'첫 조사 전';
  $('research-status').textContent=(d.refreshing?d.progress:d.error||'최근 조사 '+when)+(d.next_check?' · 다음 조사 '+tm(d.next_check):'')+(d.stale&&d.time?' · 이전 결과 / 갱신 대기':'');$('research-status').className=(d.stale||d.error?'warn':'mu')+' small';
  $('research-coverage').textContent=c.universe?'전체 '+c.universe+'종목 → 적격 '+c.eligible+'종목 → 일봉 비교 '+c.daily_compared+'종목 → 기간별 정밀 후보 '+c.deep_candidates+'종목 · 온체인 자료 '+c.onchain+'종목 · 봉 부족/조회 실패 '+c.daily_failed+'종목':'전체 원화 종목의 거래대금·경보·마감 봉을 조사합니다. 첫 조사는 수 분 걸릴 수 있습니다.';
  $('research-method').innerHTML='원화 전 종목을 대상으로 거래소 경보·유동성·최근 거래를 확인한 뒤 적격 종목의 일봉을 비교합니다. 각 기간 1차 상위 20개를 추려 스윙 4시간봉·단타 15분봉과 호가를 추가 검사합니다. 장기는 120개 이상 일봉과 기초 자료가 있는 후보만 표시합니다. 충분한 후보가 없으면 10개를 억지로 채우지 않습니다.<br><br>'+Object.entries(d.weights||{}).map(([b,w])=>'<b>'+bucketName(b)+'</b>: '+Object.entries(w).map(([key,val])=>esc(d.labels[key])+' '+val+'%').join(' · ')).join('<br>')+'<br><br>절대 일중 등락률 10% 초과는 감점합니다. 종합은 기간별 점수 중 가장 높은 분류로 한 종목씩 추립니다. 서로 다른 기간 점수는 통계적으로 보정한 기대수익이 아닙니다.<br>제외: '+esc(Object.entries(c.excluded||{}).map(([k,v])=>k+' '+v+'종목').join(' · '))+'<br><br>시장·경보: '+sourceLink('https://docs.upbit.com/kr/reference/list-quote-tickers','업비트')+' · 온체인: '+sourceLink('https://api-docs.defillama.com/','DefiLlama')+' · 프로젝트 기본 자료는 카드의 검토일 기준이며 미래 성장 보증이 아닙니다.';
  document.querySelectorAll('[data-rec-tab]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.recTab===researchTab)));
  $('trial-start').disabled=d.stale||!(d.rankings[researchTab]||[]).length||busy;
  const key=[d.time,researchTab].join('|');if(key===researchKey)return;
  if(document.activeElement?.closest?.('#recommendations-list'))return;
  researchKey=key;
  const rows=d.rankings[researchTab]||[];
  $('recommendations-list').innerHTML=rows.map((r,i)=>{
    const f=r.daily,fund=r.fundamentals||{},project=r.project||{},period=r[r.bucket+'_features']||f;
    const growth=fund.change30??fund.change7;
    const fundDate=fund.data_time?' · 집계 '+new Date(fund.data_time*1000).toLocaleDateString('ko-KR',{timeZone:'Asia/Seoul'}):fund.retrieved?' · 집계시각 미제공 / 조회 '+tm(fund.retrieved):'';
    const fundText=fund.tvl!=null?esc(fund.kind)+' $'+n(fund.tvl/1e6,2)+'백만 · '+(fund.change30!=null?'30일':fund.change7!=null?'7일':'변화')+' '+(growth==null?'미확인':sg(growth,2)+'%')+fundDate:'실사용·온체인 성장 자료 미확인';
    const reason=Object.entries(r.components).map(([k,val])=>'<span>'+esc(d.labels[k])+' '+n(val,1)+'/100</span>').join(' · ');
    return '<article class="rec-card"><div class="row between"><div class="row"><b class="rec-rank">'+String(i+1).padStart(2,'0')+'</b><div><b>'+esc(r.name)+' <span class="mu small">'+esc(r.market.slice(4))+'</span></b><div class="mu small">'+bucketName(r.bucket)+' · '+esc(r.status)+'</div></div></div><div class="rec-score">'+n(r.score,1)+'<span class="mu small"> / 100</span></div></div><div class="rec-meter"><i style="width:'+r.score+'%"></i></div><div class="rec-metrics"><div><span>조사 시 현재가</span>'+priceText(r.price)+'원</div><div><span>24시간 거래대금</span>'+n(r.turnover/1e8,1)+'억원</div><div><span>일봉 7일 / 30일 등락</span><b class="'+cl(f.return7)+'">'+sg(f.return7,1)+'%</b> / '+sg(f.return30,1)+'%</div><div><span>7일 거래대금 / 이전 7일</span>'+(f.volume_growth==null?'미확인':sg(f.volume_growth,1)+'%')+'</div></div><details><summary>성장·전망·선정 근거 펼치기</summary><p><b>기초 자료</b> '+esc(project.summary||fund.description||'프로젝트 사업·토큰 권리의 직접 검토 자료 없음')+'</p><p><b>성장 자료</b> '+fundText+' '+sourceLink(fund.source,'온체인 출처')+'</p><p><b>조건부 전망</b> '+esc(project.outlook||r.outlook)+'</p><p class="warn">'+esc(r.risks.join(' · '))+'</p><p class="mu">기준 봉 ATR '+n(period.atr_pct,2)+'% · 호가 차이 '+n(r.spread,3)+'% · 일봉 '+f.history+'개<br>'+reason+'</p>'+sourceLink(project.url,'프로젝트 원문')+(project.reviewed?'<span class="mu small"> · 기본 자료 검토 '+esc(project.reviewed)+'</span>':'')+'</details><div class="rec-controls"><span class="mu small">매매는 사용자 결정</span><div class="row"><button class="bt buy" data-rec-order="bid" data-rec-market="'+esc(r.market)+'">매수 주문</button><button class="bt sell" data-rec-order="ask" data-rec-market="'+esc(r.market)+'">매도 주문</button></div></div></article>';
  }).join('')||'<div class="c mu">확인된 추천 후보가 없습니다. 최신 조사 완료를 기다리세요.</div>';
  renderSelectionForms(rows);
}
document.querySelectorAll('[data-rec-tab]').forEach(b=>b.onclick=()=>{researchTab=b.dataset.recTab;if(researchData)renderResearch(researchData)});
$('recommendations-list').addEventListener('click',event=>{
  const b=event.target.closest('[data-rec-order]');if(!b||!researchData||!portfolioData||busy||orderBusy)return;
  if(researchData.stale)return toast('추천 자료가 오래됐습니다. 최신 조사를 기다리세요.',true);
  const r=(researchData.rankings[researchTab]||[]).find(r=>r.market===b.dataset.recMarket);if(!r)return;
  openOrder(r.market,b.dataset.recOrder,{mode:$('recommend-order-mode').value,bucket:r.bucket,amount:$('recommend-order-amount').value});
});
function comparisonChart(samples,initial){
  if(samples.length<2||!initial)return '<span class="mu small">5분마다 모의 자산 추이를 기록합니다.</span>';
  const returns=samples.map(s=>(s.total/initial-1)*100),low=Math.min(-.2,...returns),high=Math.max(.2,...returns),first=samples[0].time,last=samples.at(-1).time;
  const y=v=>80-(v-low)/(high-low)*70,x=t=>10+(t-first)/Math.max(1,last-first)*480;
  const points=samples.map((s,i)=>x(s.time).toFixed(2)+','+y(returns[i]).toFixed(2)).join(' ');
  return '<svg viewBox="0 0 500 90" role="img" aria-label="모의 원금 대비 자산 수익률 추이"><line x1="10" x2="490" y1="'+y(0)+'" y2="'+y(0)+'" stroke="var(--line)"/><polyline fill="none" stroke="var(--green)" stroke-width="2" points="'+points+'"/><title>수익률 '+sg(returns.at(-1),2)+'% · '+new Date(last*1000).toLocaleString('ko-KR')+'</title></svg>';
}
function renderComparison(p){
  const c=p.comparison;if(!c)return;const a=c.paper,b=c.live,stats=a.stats,trial=a.trial;
  const win=stats.closed?n(stats.wins/stats.closed*100,1)+'% ('+stats.closed+'건)':'완료된 왕복 거래 없음';
  $('parallel-comparison').innerHTML='<div class="c"><div class="row between"><b>시뮬레이션 · 독립 가상 계좌</b><span class="tag">'+(a.running?'자동 진입 검사 중':'자동 신규 매수 정지')+'</span></div><div class="compare-value">'+n(a.total)+'원 <span class="small '+cl(a.return_pct)+'">'+(a.return_pct==null?'—':sg(a.return_pct,2)+'%')+'</span></div><div class="small mu">기준 원금 '+n(a.initial)+'원 · 체결 '+a.fills+'회 · 수수료 '+n(a.fees,2)+'원<br>'+(trial.cash_start?'추천 검증 시작 '+new Date(trial.started*1000).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})+' · '+trial.markets.length+'종목<br>완료 왕복 승률 '+win+' · 이익계수 '+(stats.profit_factor==null?'—':n(stats.profit_factor,2)):'현재 계좌를 복사한 모의 장부 · 자동 검증 시작 시 가상 원화로 새 출발')+'</div><div class="compare-chart">'+comparisonChart(a.samples||[],a.initial)+'</div></div><div class="c"><div class="row between"><b>실제 투자 · 업비트 계좌</b><span class="badge live">직접 주문</span></div><div class="compare-value">'+n(b.total)+'원 <span class="small '+cl(b.holding_roi)+'">보유 '+(b.holding_roi==null?'—':sg(b.holding_roi,2)+'%')+'</span></div><div class="small mu">현재 보유 평가손익 '+sg(b.pnl)+'원<br>앱 체결 '+b.fills+'회 · 앱 기록 수수료 '+n(b.fees,2)+'원<br>실거래 자동 신규 매수 차단 · 위임한 보유분 청산 관리 가능'+(b.unpriced.length?'<br><span class="warn">평가 제외 '+esc(b.unpriced.join(', '))+'</span>':'')+'</div><div class="research-note">추천 창의 주문 방식을 실제 투자로 선택하면 모의 검증을 중지하지 않고 직접 주문할 수 있습니다.</div></div>';
}
$('trial-start').onclick=async()=>{
  if(!researchData||researchData.stale||busy||!portfolioData)return;
  const rows=researchData.rankings[researchTab]||[];
  if(!confirm('선택 추천군 '+rows.map(r=>r.market).join(', ')+'\n현재 모의 장부를 보관하고 실제 평가자산 '+n(portfolioData.views.live.total)+'원을 가상 원화로 새로 시작합니다.\n추천·봉 검증을 통과한 종목만 기간별 규칙으로 모의 자동 매매합니다. 실제 계좌에는 주문하지 않습니다. 시작할까요?'))return;
  await portfolioCommand({action:'portfolio_trial_start',bucket:researchTab});
};
$('trial-pause').onclick=()=>portfolioCommand({action:'portfolio_pause',mode:'paper'});
$('trial-resume').onclick=()=>portfolioCommand({action:'portfolio_start',mode:'paper'});
async function loadResearch(){try{const r=await fetch('/api/recommendations',{headers:{'X-Session-Token':token},cache:'no-store'});if(!r.ok)throw Error();renderResearch(await r.json())}catch(e){$('research-status').textContent='추천 서버 연결 지연 · 주문 전 최신 시세를 확인하세요.';$('research-status').className='warn small'}finally{setTimeout(loadResearch,5000)}}
loadResearch();
