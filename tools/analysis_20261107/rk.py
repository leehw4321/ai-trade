import json, datetime as dt, collections
from sim import *
from part import atr_pct
LED='C:/Users/User/Desktop/코인 트레이딩/v5-app/v5/data/missed_ledger.jsonl'
rows=[json.loads(l) for l in open(LED,encoding='utf-8')]
KST=dt.timezone(dt.timedelta(hours=9))
rk=collections.defaultdict(dict); 
for r in rows: rk[r['slot']][r['market']]=(r['rank'],r['rs'])
slots=sorted(rk)
def sdt(s): return dt.datetime.fromtimestamp(s,KST).replace(tzinfo=None)
print('원장 슬롯', len(slots), sdt(slots[0]), '~', sdt(slots[-1]))
W=80000; FEE=0.0005
def sim_rank(e, T=0.05, rank_exit=False, exit_slip=0.001, M=9, stop=0.045, arm=0.03, give=0.30, slip=0.003, lim_slip=0.001, partial=True):
    """r2sim 와 같은 손절·이익보호·분할·추적에 순위 매도(매시 :01)를 더한다. 반환 (수익률, 청산시각, 사유)"""
    pre, post = path(e)
    ep=e['price']; A=atr_pct(e); t2=T if T is not None else 6*A
    floor=ep*(1+2*FEE+0.001); hard=ep*(1-stop); guard=0.0; peak=ep
    rem=1.0; got=0.0; part=False
    t_ent=dt.datetime.fromisoformat(e['t']).replace(tzinfo=None)
    # 순위 판단 시각: 진입 이후 첫 정시 슬롯부터, 판단은 슬롯+1분 봉 시가에서 실행
    ex_times={}
    if rank_exit:
        for s in slots:
            ts=sdt(s)+dt.timedelta(minutes=1)
            if ts<=t_ent: continue
            rr=rk[s].get(e['market'])
            if rr is None or rr[0]>M or (rr[1] is not None and rr[1]<0):
                ex_times[ts.strftime('%Y-%m-%dT%H:%M:%S')]=True
    exit_t=None; why=''
    for k,(o,h,l,c) in post[1:721]:
        lvl=max(hard,guard)
        if l<=lvl:
            got+=rem*min(lvl,o)*(1-slip)/ep; rem=0; exit_t=k; why='손절/이익보호'; break
        if k in ex_times:
            got+=rem*o*(1-exit_slip)/ep; rem=0; exit_t=k; why='순위 매도'; break
        peak=max(peak,h)
        if peak>=ep*(1+arm): guard=max(guard,floor,ep+(peak-ep)*(1-give))
        if peak>=ep*(1+t2):
            if partial and not part and h>=ep*(1+t2):
                q=1/3; got+=q*(1+t2)*(1-lim_slip); rem-=q; part=True
            guard=max(guard,peak*(1-2*A),ep+(peak-ep)*0.5)
    if rem>1e-9:
        last=post[min(len(post)-1,720)]; exit_t=last[0]; why=why or '12h 종료'
        got+=rem*last[1][3]*(1-slip)/ep
    return got-1-2*FEE, exit_t, why
def portfolio(es, slots_max, cd_profit_h, cd_loss_h, rank_exit, T=0.05, exit_slip=0.001):
    """진입 순서대로 훑으며 동시 보유 상한·재진입 금지를 적용(새 진입은 추가하지 않음)."""
    es=sorted(es,key=lambda e:e['t']); open_=[]; last={}; out=[]; skipped=0
    for e in es:
        t=dt.datetime.fromisoformat(e['t']).replace(tzinfo=None)
        open_=[x for x in open_ if x>t]
        if len(open_)>=slots_max: skipped+=1; continue
        lk=last.get(e['market'])
        if lk and t<lk[0]+dt.timedelta(hours=(cd_profit_h if lk[1]>0 else cd_loss_h)): skipped+=1; continue
        r,xt,why=sim_rank(e,T=T,rank_exit=rank_exit,exit_slip=exit_slip)
        xt=dt.datetime.fromisoformat(xt); open_.append(xt); last[e['market']]=(xt,r); out.append((e['t'][:16],e['market'][4:],r,why))
    return out,skipped
if __name__=='__main__':
    A0='2026-11-05T17:00'
    es=[e for e in ents if e['t']>=A0]
    act=[e for e in ents if e['t']>=A0]
    print('대상 진입', len(es), e0:=es[0]['t'][:16], '~', es[-1]['t'][:16])
    # 실제 체결 손익(원)
    act_sum=sum((e['sold_funds']-e['sold_fee']-e['buy_funds']-e['buy_fee']) for e in es if e.get('closed'))
    print('실제 체결 손익 합(청산분)', round(act_sum), '원 / 미청산', sum(1 for e in es if not e.get('closed')),'건')
    cfgs=[('현행 규칙 재현(순위매도 없음, 4종목, 쿨다운 없음)',dict(slots_max=4,cd_profit_h=0,cd_loss_h=0,rank_exit=False)),
          ('①순위 매도만 복귀(시장가 슬리피지 0.3%)',dict(slots_max=4,cd_profit_h=0,cd_loss_h=0,rank_exit=True,exit_slip=0.003)),
          ('②순위 매도 + 지정가 우선(슬리피지 0.1%)',dict(slots_max=4,cd_profit_h=0,cd_loss_h=0,rank_exit=True)),
          ('③ ②+3종목',dict(slots_max=3,cd_profit_h=0,cd_loss_h=0,rank_exit=True)),
          ('④ ③+재진입금지(이익4h/손실24h) = 제안안',dict(slots_max=3,cd_profit_h=4,cd_loss_h=24,rank_exit=True)),
          ('⑤ 재진입금지만(순위 매도 없음)',dict(slots_max=4,cd_profit_h=4,cd_loss_h=24,rank_exit=False)),
          ('⑥ 제안안, 쿨다운 이익2h/손실6h(기존값)',dict(slots_max=3,cd_profit_h=2,cd_loss_h=6,rank_exit=True))]
    for name,kw in cfgs:
        out,sk=portfolio(es,**kw)
        n=len(out);w=sum(1 for x in out if x[2]>0);ro=sum(1 for x in out if x[3]=='순위 매도')
        print(f'{name:46s} 매수 {n:2d}건(건너뜀 {sk}) 승률 {w/n*100 if n else 0:3.0f}% 합 {sum(x[2] for x in out)*W:+8.0f}원 (평균 {sum(x[2] for x in out)/max(n,1)*100:+.2f}%) 순위매도 {ro}건')
