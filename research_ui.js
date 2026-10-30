function researchMetric(value, suffix='', digits=2){
  return Number.isFinite(value)?n(value,digits)+suffix:'—';
}
function drawModelResearch(value){
  const target=$('model-research-status');
  if(!target)return;
  const ready=value?.available===true;
  target.textContent=value?.message||'모델 비교 결과 대기';
  target.className='small '+(ready&&value.status==='selected'?'good':'warn');
  $('model-research-summary').textContent='';
  $('model-research-periods').innerHTML='';
  $('model-research-rule').textContent='';
  $('model-research-report').hidden=!value?.report_available;
  $('model-research-scope').textContent=typeof value?.scope==='string'?value.scope:Object.entries(value?.scope||{}).map(([key,item])=>key+': '+(Array.isArray(item)?item.join(', '):item)).join(' · ');
  const limitations=value?.limitations||['과거 10종목 표본 연구이며 전체 원화 종목과 미래 수익률은 검증되지 않았습니다.'];
  $('model-research-limitations').textContent=limitations.join(' ');
  if(!ready)return;
  const config=value.selected_config;
  const approval=value.approved_paper?'격리 모의 검증 후보 승인':'격리 모의 검증 승인 없음';
  $('model-research-summary').textContent=(config?(config.name||config.id)+' · ':'')+approval+' · 주문 실행 연동 없음'+(value.created_kst?' · 결과 '+value.created_kst:'');
  const labels={train:'학습 · 1~4월',validation:'선택 검증 · 5~6월',test:'미사용 평가 · 7~10월'};
  $('model-research-periods').innerHTML=table(['구간','비용 반영 수익률','완료 거래','최대 낙폭'],['train','validation','test'].map(key=>{
    const metric=value.selected_metrics?.[key];
    const change=Number.isFinite(metric?.return_pct)?'<span class="'+cl(metric.return_pct)+'">'+researchMetric(metric.return_pct,'%')+'</span>':'—';
    return [labels[key],change,researchMetric(metric?.trades,'건',0),researchMetric(metric?.max_drawdown_pct,'%')];
  }));
  $('model-research-rule').textContent='선정 기준: '+(value.selection_rule||'결과 보고서 참조');
}
async function loadModelResearch(){
  if(appStopped)return;
  try{
    const response=await fetch('/api/research-model',{headers:{'X-Session-Token':token},cache:'no-store'});
    if(!response.ok)throw Error('연구 결과 연결 실패');
    const data=await response.json();if(!appStopped)drawModelResearch(data);
  }catch(error){
    $('model-research-status').textContent='연구 결과 연결 지연 · 서버 재시작 후 확인하세요.';
    $('model-research-status').className='small warn';
  }finally{if(!appStopped)setTimeout(loadModelResearch,30000)}
}
setTimeout(loadModelResearch,0);
