import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types
import tempfile
import unittest
from unittest.mock import patch,MagicMock

import trade_review

spec=importlib.util.spec_from_file_location('candidate_broker',Path(__file__).with_name('토론진행기.py'))
broker=importlib.util.module_from_spec(spec); spec.loader.exec_module(broker)


class TradeBrokerTests(unittest.TestCase):
    def test_each_peer_gets_same_allowlisted_packet_and_latest_priority(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); packet=root/'latest.json'
            doc,_=trade_review.analyze(b''); doc['secret']='NEVER_TRANSFER_THIS'
            packet.write_text(json.dumps(doc))
            with patch.object(broker,'ROOT',root),patch.object(broker,'CONTINUOUS',True), \
                    patch.object(broker,'TRADE_MODULE',trade_review),patch.object(broker,'TRADE_LOAD_ATTEMPTED',True), \
                    patch.dict(broker.os.environ,{'V5_TRADE_REVIEW_PACKET':str(packet)}):
                for peer in ('Claude','ChatGPT'):
                    prompt,meta=broker.prompt_for(peer,500,'latest discussion','rules')
                    self.assertIn(doc['source_version'],prompt)
                    self.assertEqual(meta['source_version'],doc['source_version'])
                    self.assertIn('LATEST PRIORITY',prompt)
                    self.assertNotIn('NEVER_TRANSFER_THIS',prompt)

    def test_missing_packet_is_not_evidence(self):
        self.assertIn('아직 없음',trade_review.debate_context(Path('no-such-trade-file.json')))

    def test_actual_module_path_loads_in_fresh_isolated_subprocess(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'repo';(root/'tools').mkdir(parents=True);(root/'debate_tools').mkdir()
            shutil.copyfile(trade_review.__file__,root/'tools/trade_review.py')
            target=root/'debate_tools/토론진행기.py';shutil.copyfile(broker.__file__,target)
            packet=root/'latest.json';doc,_=trade_review.analyze(b'');packet.write_text(json.dumps(doc))
            code="import importlib.util,sys,json;s=importlib.util.spec_from_file_location('clean_broker',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);before=list(sys.path);c,meta=m.trade_packet();assert before==sys.path;print(json.dumps(meta))"
            result=subprocess.run([sys.executable,'-I','-B','-c',code,str(target)],
                env={**os.environ,'V5_TRADE_REVIEW_PACKET':str(packet)},capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['source_version'],doc['source_version'])

    def test_missing_import_degrades_without_stopping_debate_or_growing_sys_path(self):
        with tempfile.TemporaryDirectory() as temp:
            before=list(sys.path)
            with patch.object(broker,'ROOT',Path(temp)),patch.object(broker,'CONTINUOUS',True), \
                    patch.object(broker,'TRADE_LOAD_ATTEMPTED',False),patch.object(broker,'TRADE_MODULE',None):
                prompt,meta=broker.prompt_for('Claude',1,'discussion','rules')
                self.assertEqual(meta['status'],'unavailable');self.assertIn('모듈 사용 불가',prompt)
            self.assertEqual(before,sys.path)

    def test_receipt_uses_sent_version_after_successful_document_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); packet=root/'latest.json';doc,_=trade_review.analyze(b'')
            packet.write_text(json.dumps(doc));version=doc['source_version']
            (root/'토론.md').write_text('**현재 차례: Claude** · **회차: 1**\n\n---\n',encoding='utf-8')
            (root/'토론규칙.md').write_text('rules',encoding='utf-8')
            reporter=MagicMock();reporter.data={'latest':None};reporter.shared_context.return_value=''
            def invoke(ai,n,prompt):
                self.assertIn(version,prompt)
                (root/'자동진행기록').mkdir(exist_ok=True)
                newer={**doc,'source_version':'f'*20};packet.write_text(json.dumps(newer))
                return {'body':'체결 근거 수신: '+version,'updates':[],'agreement':'','finish':False}
            record=broker.record_trade_receipt
            def receipt(ai,n,obj,meta):
                self.assertIn(obj['body'],(root/'토론.md').read_text(encoding='utf-8'))
                record(ai,n,obj,meta);(root/'stop.txt').write_text('done')
            with patch.multiple(broker,ROOT=root,DOC=root/'토론.md',RULES=root/'토론규칙.md',
                    STOP=root/'stop.txt',LOCK=root/'broker.lock',ROLE_FILE=root/'role.json',
                    QUOTA_WAIT=root/'quota.json',TRADE_MODULE=trade_review,TRADE_LOAD_ATTEMPTED=True), \
                    patch.dict(os.environ,{'V5_TRADE_REVIEW_PACKET':str(packet)}), \
                    patch.object(broker,'HourlyReports',return_value=reporter),patch.object(broker,'heartbeat'), \
                    patch.object(broker,'wait_for_usage',return_value=True),patch.object(broker,'kill_child'), \
                    patch.dict(sys.modules,{'현황문서':types.SimpleNamespace(render=lambda *a,**k:None)}), \
                    patch.object(broker,'invoke',side_effect=invoke),patch.object(broker,'record_trade_receipt',side_effect=receipt):
                self.assertTrue(broker.run(True),(root/'진행기록.log').read_text(encoding='utf-8'))
            saved=json.loads((root/'체결분석_수신.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(saved['source_version'],version);self.assertTrue(saved['acknowledged'])

    def test_unavailable_packet_has_explicit_receipt(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(broker,'ROOT',Path(temp)):
            broker.record_trade_receipt('Claude',1,{'body':'no packet'},{'source_version':None,'status':'unavailable'})
            saved=json.loads((Path(temp)/'체결분석_수신.jsonl').read_text(encoding='utf-8'))
            self.assertIsNone(saved['source_version']);self.assertEqual(saved['status'],'unavailable')


if __name__=='__main__':unittest.main()
