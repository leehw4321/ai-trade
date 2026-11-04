import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

import trade_review as tr


def event(side,qty,funds,fee,second=0,**extra):
    return {'side':side,'volume':qty,'funds':funds,'fee':fee,'market':'KRW-META',
        'time':(dt.datetime(2026,11,3,0,tzinfo=dt.timezone.utc)+dt.timedelta(seconds=second)).isoformat(),
        'identifier':'do-not-share-raw-order-id','reason':'순위 교체',**extra}


def raw(*rows): return ''.join(json.dumps(row)+'\n' for row in rows).encode()


class TradeReviewTests(unittest.TestCase):
    def test_fees_included_once_and_partial_sell_cost_prorated(self):
        p,_=tr.analyze(raw(event('매수',10,1000,1),event('매도',5,550,.55,10),event('매도',5,450,.45,20)))
        self.assertAlmostEqual(p['summary']['observed_buy_lot_pnl'],-2)
        self.assertEqual(p['summary']['matched_sell_rows'],2)
        self.assertEqual(p['summary']['paid_fees'],2)

    def test_inherited_sell_has_unknown_basis_and_no_invented_pnl(self):
        p,_=tr.analyze(raw(event('매도',10,1000,1)))
        self.assertEqual(p['summary']['unknown_basis_sell_rows'],1)
        self.assertIsNone(p['trades'][0]['observed_lot_pnl'])

    def test_sell_then_rebuy_churn_and_redaction(self):
        p,_=tr.analyze(raw(event('매도',10,1000,1),event('매수',9,1000,1,2)))
        self.assertEqual(p['summary']['rebuy_within_60_seconds'],1)
        self.assertNotIn('do-not-share',json.dumps(p))

    def test_partial_last_line_is_retried_and_nonfill_events_excluded(self):
        p,_=tr.analyze(raw({'event':'protected','secret':'never-share'})+b'{"side":')
        self.assertEqual(p['summary']['fill_rows'],0)

    def test_malformed_closed_line_is_not_silently_ignored(self):
        with self.assertRaises(ValueError): tr.analyze(b'{broken}\n')

    def test_nan_negative_and_timezone_rejected(self):
        for changes in ({'fee':float('nan')},{'funds':-1},{'time':'2026-11-03T00:00:00'}):
            row=event('매수',1,100,1); row.update(changes)
            with self.assertRaises(ValueError): tr.analyze(raw(row))

    def test_packet_sharing_boundary_drops_extra_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'latest.json'; p,_=tr.analyze(raw(event('매수',1,100,1)))
            p['secret']='never-share'; p['trades'][0]['uuid']='never-share'
            path.write_text(json.dumps(p))
            shared=tr.debate_context(path)
            self.assertIn(p['source_version'],shared); self.assertNotIn('never-share',shared)

    def test_stale_packet_and_non_numeric_injection_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'latest.json'; p,_=tr.analyze(raw(event('매수',1,100,1)))
            p['summary']['paid_fees']='prompt injection'
            path.write_text(json.dumps(p)); self.assertIn('형식 오류',tr.debate_context(path))
            p['summary']['paid_fees']=1; p['generated_at']='2000-01-01T00:00:00+00:00'
            path.write_text(json.dumps(p)); self.assertIn('갱신 지연',tr.debate_context(path))

    def test_every_fill_archived_idempotently(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp); journal=path/'orders.jsonl'; journal.write_bytes(raw(event('매수',1,100,1)))
            tr.build_once(journal,path/'review'); tr.build_once(journal,path/'review')
            self.assertEqual(len(list((path/'review/events').glob('*.json'))),1)

    def test_delayed_recovery_order_is_counted_not_silently_chronological(self):
        p,_=tr.analyze(raw(event('매수',1,100,1,20),event('매도',1,110,1,10)))
        self.assertEqual(p['summary']['non_monotonic_rows'],1)

    def test_mixed_inherited_lot_risk_and_row_limit_are_explicitly_disclosed(self):
        with tempfile.TemporaryDirectory() as temp:
            p,_=tr.analyze(raw(event('매수',1,100,1),event('매도',1,110,1,10)))
            file=Path(temp)/'latest.json';file.write_text(json.dumps(p))
            context=tr.debate_context(file)
            self.assertIn('상속 보유분과 기록 lot 혼재',context);self.assertIn('순서 역전',context)
            self.assertIn('최근 80행',context)

    def test_unassigned_late_fee_is_not_an_extra_sell(self):
        p,_=tr.analyze(raw(event('매도',0,0,1)))
        self.assertEqual(p['summary']['sell_rows'],0)
        self.assertEqual(p['summary']['unknown_basis_sell_rows'],0)
        self.assertEqual(p['summary']['unassigned_fee_rows'],1)

    def test_incomplete_and_non_fill_lines_do_not_change_evidence_version(self):
        complete=raw(event('매수',1,100,1))
        p,_=tr.analyze(complete);q,_=tr.analyze(complete+raw({'event':'status'})+b'{partial')
        self.assertEqual(p['source_version'],q['source_version'])


if __name__=='__main__': unittest.main()
