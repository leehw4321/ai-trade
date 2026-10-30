const allocationFields=['max_position','max_total','max_positions','risk','max_open_risk','volatility_target'];
let allocationCurrent=null;
function drawAllocation(value){
  if(!value)return;allocationCurrent=value;
  $('allocation-summary').textContent=`종목당 ${(value.max_position*100).toFixed(1)}% · 전체 ${(value.max_total*100).toFixed(1)}% · 최대 ${value.max_positions}종목 · 거래 위험 ${(value.risk*100).toFixed(2)}%`;
}
$('allocation-open').onclick=()=>{
  if(!allocationCurrent){toast('서버를 새 버전으로 다시 실행해 주세요.',true);return;}
  for(const key of allocationFields)$('allocation-'+key).value=key==='max_positions'?allocationCurrent[key]:Number((allocationCurrent[key]*100).toFixed(4));
  $('allocation-status').textContent='';$('allocation-dialog').showModal();
};
$('allocation-close').onclick=()=>$('allocation-dialog').close();
$('allocation-save').onclick=async()=>{
  const button=$('allocation-save');button.disabled=true;
  try{
    const body={action:'auto_v3_allocation',mode:'paper'};
    for(const key of allocationFields)body[key]=Number($('allocation-'+key).value)/(key==='max_positions'?1:100);
    const result=await portfolioRequest(body);
    $('allocation-status').textContent=typeof result==='string'?result:'저장 완료';
    drawAllocation(body);await refreshPortfolio();
  }catch(e){$('allocation-status').textContent=e.message;}
  finally{button.disabled=false;}
};
function fearVisual(row){
  if(row.id!=='fear')return '';
  const v=row.value;
  if(!Number.isFinite(v)||v<0||v>100)return '<div class="small mu">공포·탐욕 지수 수신 대기</div>';
  const color=`hsl(${v*1.2} 75% 42%)`;
  return `<div class="fear-value" style="color:${color};font-size:36px;font-weight:800;margin:8px 0">${n(v,0)} <span class="small mu">/ 100</span></div><div class="small" style="font-weight:700;color:${color}">${esc(row.detail||'시장 심리')}</div><div role="meter" aria-label="공포·탐욕 지수" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${v}" style="position:relative;height:14px;margin:14px 0 8px;border-radius:8px;background:linear-gradient(90deg,#dc2626,#f97316,#eab308,#84cc16,#16a34a)"><span style="position:absolute;left:${Math.max(1,Math.min(99,v))}%;top:-4px;width:4px;height:22px;background:var(--tx);border:1px solid var(--bg);transform:translateX(-50%)"></span></div><div class="row between small mu"><span>0 · 극도의 공포</span><span>50 · 중립</span><span>100 · 극도의 탐욕</span></div>`;
}
let holdingSettingsKey='';
function drawHoldingSettings(s){
  if(!s||!$('short-max-minutes'))return;
  const key=JSON.stringify(s);
  if(key!==holdingSettingsKey){
    $('short-max-minutes').value=s.max_minutes;
    $('short-progress-minutes').value=s.progress_minutes;
    holdingSettingsKey=key;
  }
  $('short-time-current').textContent=`저장된 기준: 최대 ${s.max_minutes}분 · 진행 확인 ${s.progress_minutes? s.progress_minutes+'분':'해제'}`;
}
$('short-time-save').onclick=async()=>{
  const button=$('short-time-save');button.disabled=true;
  try{
    const body={action:'holding_settings',max_minutes:Number($('short-max-minutes').value),progress_minutes:Number($('short-progress-minutes').value)};
    const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Session-Token':token},body:JSON.stringify(body)});
    const result=await response.json();if(!response.ok)throw Error(result.error||'저장 실패');
    $('short-time-status').textContent=result.message||result.result||'저장 완료 · 다음 신규 진입·분류 변경·관리 위임부터 적용';
    holdingSettingsKey='';drawHoldingSettings(body);
  }catch(e){$('short-time-status').textContent=e.message}
  finally{button.disabled=false}
};
async function loadIndicators(){
  if(appStopped)return;
  try{
    const r=await fetch('/api/indicators',{headers:{'X-Session-Token':token},cache:'no-store'});
    if(!r.ok)throw Error('지표 서버 연결 실패');
    const data=await r.json();if(appStopped)return;
    $('market-indicators').innerHTML=data.rows.map(row=>{
      const stamp=row.time?new Date(row.time*1000).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}):'기준 시각 없음';
      const change=Number.isFinite(row.change_pct)?` · 전일 대비 ${row.change_pct>0?'+':''}${n(row.change_pct,2)}%`:'';
      return `<div class="c"><div class="small mu">${esc(row.name)}</div>${row.id==='fear'?fearVisual(row):`<div style="font-size:22px;margin:7px 0">${row.value===null?'—':n(row.value,2)} <span class="small mu">${esc(row.unit||'')}</span></div><div class="small">${esc(row.detail||'')}${esc(change)}</div>`}<div class="small mu">${esc(row.frequency||'수신 대기')}<br>기준 ${esc(stamp)}${row.old?' · 이전 관측값 / 휴장·지연 확인':''}${row.error?' · 수신 실패 (마지막 값 유지)':''}<br>${row.url?`<a href="${esc(row.url)}" target="_blank" rel="noopener noreferrer">${esc(row.source)}</a>`:''}</div></div>`;
    }).join('');
    $('market-indicators-status').textContent=data.checked?'60초마다 조회 · 마지막 조회 '+new Date(data.checked*1000).toLocaleTimeString('ko-KR'):'최초 시세 수신 중…';
  }catch(e){$('market-indicators-status').textContent='지표 연결 지연 · 서버를 새 버전으로 다시 실행해 주세요.'}
  finally{if(!appStopped)setTimeout(loadIndicators,15000)}
}
setTimeout(loadIndicators,0);
