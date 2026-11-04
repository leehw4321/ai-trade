"""Read-only executed-fill analysis. No exchange, credentials, orders or code mutation.

Accounting below covers only buy lots visible in this journal, not account PnL.
The bounded allowlisted packet is suitable for the tools-disabled debate broker.
"""
import argparse
import datetime as dt
from decimal import Decimal,InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import time

VERSION='executed-fill-review-v1'
MARKET=re.compile(r'^KRW-[A-Z0-9]{1,20}$')


def number(value):
    try:result=Decimal(str(value))
    except InvalidOperation:raise ValueError('Invalid fill number') from None
    if not result.is_finite() or result<0: raise ValueError('Invalid fill number')
    return result


def timestamp(value):
    result=dt.datetime.fromisoformat(value)
    if result.tzinfo is None: raise ValueError('Timezone required')
    return result


def reason_category(text):
    text=str(text)
    for word,kind in [('손절','stop'),('이익 보호','profit_guard'),('이익보호','profit_guard'),
                      ('익절','take_profit'),('추적','trailing'),('수동','user_ui'),('화면','user_ui'),
                      ('순환','rotation'),('교체','rotation'),('순위','rank'),('주문안','proposal')]:
        if word in text: return kind
    return 'other'


def rows_from_bytes(raw):
    # The bot may be appending the last line. Only newline-terminated rows are complete.
    rows=[]
    for index,line in enumerate(raw.split(b'\n')[:-1],1):
        if not line.strip(): continue
        try:row=json.loads(line)
        except ValueError:raise ValueError('Invalid journal JSON at line '+str(index)) from None
        if row.get('side') not in ('매수','매도'): continue
        market=row.get('market')
        if not isinstance(market,str) or not MARKET.fullmatch(market): raise ValueError('Invalid market')
        when=timestamp(row['time'])
        qty,funds,fee=(number(row.get(k,0)) for k in ('volume','funds','fee'))
        if qty==0 and funds!=0: raise ValueError('Funds without fill volume')
        # Fingerprint is local only. Identifiers and arbitrary reason text never leave this parser.
        fingerprint=hashlib.sha256(line).hexdigest()
        rows.append({'index':index,'time':when.isoformat(),'epoch':when.timestamp(),'market':market,
            'side':'buy' if row['side']=='매수' else 'sell','qty':qty,'funds':funds,'fee':fee,
            'reason':reason_category(row.get('reason')),'fingerprint':fingerprint})
    return rows


def analyze(raw):
    rows=rows_from_bytes(raw)
    lots={}; last_sell={}; events=[]; matched=Decimal(0); fees=Decimal(0)
    covered=unknown=churn=unassigned_fee=non_monotonic=0
    previous_time=None
    for row in rows:
        if previous_time is not None and row['epoch']<previous_time:non_monotonic+=1
        previous_time=row['epoch']
        market=row['market']; q,f,fee=row['qty'],row['funds'],row['fee']
        pos=lots.setdefault(market,{'qty':Decimal(0),'cost':Decimal(0),'since':row['epoch']})
        event={k:row[k] for k in ('index','time','market','side','reason')}
        event.update(volume=float(q),funds=float(f),fee=float(fee),
            average_fill=float(f/q) if q else None,
            time_basis='journal_log_time_exchange_fill_time_unverified',
            fee_bps=float(fee/f*10000) if f else None,
            signal_to_fill_seconds=None,post_entry_price_path='not_collected',
            observed_lot_pnl=None,covered_by_observed_buys=False)
        fees+=fee
        if row['side']=='buy':
            if not pos['qty']: pos['since']=row['epoch']
            pos['qty']+=q; pos['cost']+=f+fee
            gap=row['epoch']-last_sell.get(market,-1e20)
            event['rebuy_seconds_after_sell']=gap if 0<=gap<=86400 else None
            if q>0 and 0<=gap<=60: churn+=1
        else:
            if q>0: last_sell[market]=row['epoch']
            if q>0 and q<=pos['qty']:
                basis=pos['cost']*q/pos['qty']; pnl=f-fee-basis
                pos['qty']-=q; pos['cost']-=basis
                matched+=pnl; covered+=1
                event.update(observed_lot_pnl=float(pnl),covered_by_observed_buys=True,
                    observed_basis=float(basis),holding_seconds=max(0,row['epoch']-pos['since']))
            elif q>0:
                unknown+=1
                # Never carry an invented quantity or basis after an unmatched inherited sell.
                pos.update(qty=Decimal(0),cost=Decimal(0))
            elif fee>0:
                # A late sell fee is real cash expense but cannot be assigned to a buy lot here.
                unassigned_fee+=1
        events.append(event)
    packet={'schema':VERSION,'source_version':hashlib.sha256(''.join(r['fingerprint'] for r in rows).encode()).hexdigest()[:20],
        'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        'coverage':'only_complete_executed_fill_rows_in_local_orders_journal',
        'limits':['not_account_pnl','inherited_cost_and_missing_history_excluded',
                  'no_signal_or_intratrade_price_path_yet','not_a_profitability_test',
                  'holding_and_rebuy_intervals_use_log_times_not_verified_exchange_times',
                  'mixed_inherited_and_recorded_lots_may_misattribute_cost',
                  'file_order_may_differ_from_exchange_execution_order',
                  'latest_80_rows_in_packet_full_history_local'],
        'summary':{'fill_rows':len(events),'buy_rows':sum(r['side']=='buy' and r['qty']>0 for r in rows),
            'sell_rows':sum(r['side']=='sell' and r['qty']>0 for r in rows),
            'matched_sell_rows':covered,'unknown_basis_sell_rows':unknown,
            'unassigned_fee_rows':unassigned_fee,'non_monotonic_rows':non_monotonic,
            'observed_buy_lot_pnl':float(matched),'paid_fees':float(fees),'rebuy_within_60_seconds':churn},
        'trades':events[-80:]}
    return packet,[(r['fingerprint'],e) for r,e in zip(rows,events)]


def atomic_json(path,value):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    os.replace(temp,path)


def build_once(journal,output):
    raw=Path(journal).read_bytes(); packet,events=analyze(raw)
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    archive=output/'events'; archive.mkdir(exist_ok=True)
    for fingerprint,event in events:
        target=archive/(fingerprint+'.json')
        if not target.exists(): atomic_json(target,event)
    atomic_json(output/'latest.json',packet)
    return packet


def debate_context(path):
    """Enforce a second allowlist at the external sharing boundary."""
    path=Path(path)
    if not path.exists(): return '체결 분석 자료 아직 없음. 수익 검증을 주장하지 말 것.'
    if path.stat().st_size>120000: return '체결 분석 자료 크기 초과: 사용 보류'
    try:
        packet=json.loads(path.read_text(encoding='utf-8'))
        if packet.get('schema')!=VERSION: raise ValueError()
        if not re.fullmatch('[a-f0-9]{20}',packet['source_version']): raise ValueError()
        generated=timestamp(packet['generated_at'])
        age=(dt.datetime.now(dt.timezone.utc)-generated).total_seconds()
        if age>120 or age < -30: return '체결 분석 자료 갱신 지연: 현재 상태로 간주하지 말 것.'
        summary_keys=('fill_rows','buy_rows','sell_rows','matched_sell_rows','unknown_basis_sell_rows',
                      'observed_buy_lot_pnl','paid_fees','rebuy_within_60_seconds','unassigned_fee_rows','non_monotonic_rows')
        clean={'schema':VERSION,'source_version':packet['source_version'],'generated_at':generated.isoformat(),
            'summary':{},'trades':[],'limits':['계좌 손익 아님','기록된 매수 lot만 원가 추적',
                '과거 누락 이력·상속 원가·봉 내 경로·신호 시각 미검증',
                '상속 보유분과 기록 lot 혼재 시 원가 오귀속 가능','복구 지연 행은 순서 역전 가능',
                'trades는 최근 80행, summary는 전체 기록이며 0수량 정산 행 포함',
                '시각/보유기간/재매수간격은 로그시각 기준이며 거래소 실제체결시각 미확정','수익성 입증 아님']}
        for key in summary_keys:
            value=packet['summary'][key]
            if type(value) not in (int,float) or not __import__('math').isfinite(value): raise ValueError()
            clean['summary'][key]=value
        if not isinstance(packet['trades'],list) or len(packet['trades'])>80: raise ValueError()
        for event in packet['trades']:
            if not MARKET.fullmatch(event['market']) or event['side'] not in ('buy','sell'): raise ValueError()
            row={'market':event['market'],'side':event['side'],'time':timestamp(event['time']).isoformat()}
            if event['reason'] not in ('stop','profit_guard','take_profit','trailing','user_ui','rotation','rank','proposal','other'): raise ValueError()
            row['reason']=event['reason']
            for key in ('index','volume','funds','fee','average_fill','fee_bps','observed_lot_pnl',
                        'holding_seconds','rebuy_seconds_after_sell'):
                value=event.get(key)
                if value is not None and (type(value) not in (int,float) or not __import__('math').isfinite(value)): raise ValueError()
                row[key]=value
            clean['trades'].append(row)
        return json.dumps(clean,ensure_ascii=False,allow_nan=False)
    except (OSError,ValueError,KeyError,TypeError,OverflowError):
        return '체결 분석 자료 형식 오류: 사용 보류'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--watch',action='store_true')
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    # OS ownership lock releases on process exit; the file may safely remain.
    lock=(args.output/'watch.lock').open('a+b'); lock.seek(0,2)
    if not lock.tell(): lock.write(b'0'); lock.flush()
    lock.seek(0)
    if os.name=='nt':
        import msvcrt
        try: msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError: return
    else:
        import fcntl
        try: fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError: return
    while not (args.output/'stop.txt').exists():
        try:
            result=build_once(args.journal,args.output)
            atomic_json(args.output/'health.json',{'ok':True,'version':result['source_version'],
                'at':result['generated_at'],'pid':os.getpid()})
        except Exception as error:
            atomic_json(args.output/'health.json',{'ok':False,'error':type(error).__name__,'pid':os.getpid()})
        if not args.watch: break
        for _ in range(15):
            if (args.output/'stop.txt').exists(): break
            time.sleep(1)
    lock.close()


if __name__=='__main__': main()
