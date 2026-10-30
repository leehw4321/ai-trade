/* No network or order actions: explain cached screening and technical signals. */
function scalpTooltip(m,d){
  const info=d.universe?.details?.[m]||{},row=d.universe?.rows?.[m]||{},sig=d.signals[m]||{},q=d.quotes[m]||{};
  const burst=d.breakout_signals?.[m]||{};
  const selectionReason=(d.universe?.fast_candidates||[]).includes(m)
    ?'최근 60초 체결대금 급증으로 우선 편입 · 완료 봉으로 실제 돌파 조건 재검사'
    :info.selection_reason;
  const val=(v,suffix='',digits=2)=>Number.isFinite(v)?n(v,digits)+suffix:'미수신';
  const stamp=info.timestamp||info.trade_timestamp;
  const age=stamp?(Date.now()-stamp)/1000:null;
  const atrPct=Number.isFinite(sig.atr)&&sig.close>0?sig.atr/sig.close*100:null;
  const spread=q.bid>0&&q.ask>=q.bid?(q.ask/q.bid-1)*100:null;
  let checks=Object.entries(sig.checks||{}).map(([name,ok])=>'<span class="scalp-pop-line">'+ (ok?'✓ 충족 · ':'○ 대기 · ')+esc(name)+'</span>').join('');
  const burstChecks=Object.entries(burst.checks||{}).map(([name,ok])=>'<span class="scalp-pop-line">'+(ok?'✓ 충족 · ':'○ 대기 · ')+esc(name)+'</span>').join('');
  checks='<strong>기존 눌림 경로</strong>'+checks+'<strong>초기 돌파 경로 · 모의 전용</strong>'+(burstChecks||'<span class="scalp-pop-line">'+esc(burst.reason||'실시간 체결 60초·기준 봉 준비 중')+'</span>');
  const data=[['현재가',val(info.price,'원')],['오늘 시가',val(info.opening,'원')],['오늘 최고가',val(info.high,'원')],['오늘 최저가',val(info.low,'원')],['오늘 누적 거래대금',val(info.turnover_day,'원',0)],['최근 24시간 거래대금',val(info.turnover_24h,'원',0)],['당일 가격 진폭 / 시가',val(info.range_pct,'%')],['5분 ATR / 기준 종가',val(atrPct,'%')],['5분 ATR',val(sig.atr,'원')],['5분 ADX (추세 강도)',val(sig.adx)],['VWAP',val(sig.vwap,'원')],['돌파봉 거래량 배수',val(sig.volume_ratio,'배')],['매수·매도 호가 간격',val(spread,'%')],['돌파 감지 거래대금 배수',val(burst.volume_ratio,'배')],['10초 매수 체결대금 비중',val(Number.isFinite(burst.buy_ratio)?burst.buy_ratio*100:null,'%')],['돌파 유지',val(burst.held_seconds,'초')],['초기 돌파선',val(burst.breakout,'원')]];
  return '<span class="scalp-name" tabindex="0" aria-label="'+esc(m)+' 정밀 분석 상세"><b>'+esc(row.name||m)+' <span class="small mu">'+esc(m)+' ⓘ</span></b><span class="scalp-popover" role="tooltip"><strong>정밀 분석 선정 이유</strong><span class="scalp-pop-line">'+esc(selectionReason||'선정 정보 수신 대기 · 서버 재실행 필요')+'</span><span class="scalp-pop-line">'+esc(row.reason||'유동성 상태 확인 중')+'</span><span class="scalp-pop-note">기본 필터: 24시간 거래대금 10억 원 이상 · 최근 60초 내 거래 · 당일 ±30% 이내 · 유의/주의·스테이블 제외. 보유·주문 보호는 별도 우선 관리합니다.</span><span class="scalp-pop-grid">'+data.map(([name,value])=>'<span>'+esc(name)+'</span><strong>'+esc(value)+'</strong>').join('')+'</span><span class="scalp-pop-note">오늘 = 업비트 거래일 UTC 00시(한국 09시)부터. 진폭=(고가−저가)/시가×100, ATR%=5분 ATR/전략 기준 종가×100. 종목별 변동성 참고값이며 VIX가 아닙니다.</span><strong>진입 조건 확인</strong><span class="scalp-pop-line">'+(checks||'마감 봉 분석 대기')+'</span><span class="scalp-pop-note">'+esc(sig.reason||'분석 대기')+'<br>기술 분석 '+(sig.closed_at?esc(tm(sig.closed_at)):'미수신')+' · 시세 '+(stamp?esc(new Date(stamp).toLocaleString('ko-KR')):'미수신')+(age!==null&&age>60?' · 시세 지연':'')+'<br>정밀 분석 대상 선정은 매수 확정을 뜻하지 않습니다.</span></span></span>';
}

function scalpBreakoutStatus(m,d){
  const b=d.breakout_signals?.[m];
  const block=d.entry_blocks?.[m];
  const warning=block&&Date.now()/1000-block.time<30?'<br>주문 차단: '+esc(block.reason):'';
  return '<div class="small '+(b?.ok?'good':'mu')+'" style="border-top:1px solid var(--line);margin-top:8px;padding-top:7px">돌파 경로: '+esc(b?.reason||'실시간 체결·기준 봉 준비 중')+warning+'</div>';
}

function drawBreakoutComparison(d){
  if(!$('v3-strategy-comparison'))return;
  const stats=d.strategy_stats||{};
  $('v3-strategy-comparison').innerHTML=['pullback','breakout'].map(k=>{
    const s=stats[k]||{};
    return '<span class="tag">'+(k==='pullback'?'눌림':'초기 돌파')+' · 완료 '+n(s.closed||0)+'건 · 실현 '+sg(s.net||0)+'원</span>';
  }).join(' ')+'<span class="small mu">두 경로는 V3 모의 자금·위험 한도를 공유합니다. 독립 계좌 비교가 아니며 미청산 손익은 제외합니다.</span>';
}
