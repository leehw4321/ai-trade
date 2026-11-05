import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import manual_orders as mo
import observations as ob

KST = ob.KST


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'data' / 'observations.json'
        self.addCleanup(self.tmp.cleanup)

    def test_first_load_seeds_the_known_deadlines_and_persists(self):
        doc = ob.load(self.path)
        ids = {x['id'] for x in doc['items']}
        self.assertTrue({'regime-expiry', 'week-compare', 'limit-order'} <= ids)
        self.assertTrue(self.path.exists())
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['items'][0]['id'], doc['items'][0]['id'])

    def test_actions_change_status_and_leave_a_note(self):
        ob.load(self.path)
        ok, _ = ob.apply(self.path, 'limit-order', {'action': 'hold', 'note': '사용자 보류'})
        self.assertTrue(ok)
        item = next(x for x in ob.load(self.path)['items'] if x['id'] == 'limit-order')
        self.assertEqual(item['status'], '보류')
        self.assertIn('사용자 보류', item['notes'][-1]['text'])
        self.assertTrue(ob.apply(self.path, 'limit-order', {'action': 'note', 'note': '추가 메모'})[0])
        self.assertFalse(ob.apply(self.path, 'limit-order', {'action': 'note'})[0])

    def test_bad_input_is_rejected(self):
        ob.load(self.path)
        for bad in (('../x', {'action': 'done'}), ('limit-order', {'action': 'drop'}), ('nope', {'action': 'done'}),
                    ('limit-order', {'action': 'note', 'note': 'x' * 301}), ('limit-order', 'done')):
            self.assertFalse(ob.apply(self.path, *bad)[0], bad)

    def test_add_validates_id_kind_and_due_and_rejects_duplicates(self):
        ob.add(self.path, 'new-one', '새 관찰', 'shadow', '2026-11-20T09:00:00+09:00')
        with self.assertRaises(ValueError):
            ob.add(self.path, 'new-one', 'dup')
        with self.assertRaises(ValueError):
            ob.add(self.path, 'Bad Id', 'x')
        with self.assertRaises(ValueError):
            ob.add(self.path, 'ok-id', 'x', 'nope')
        with self.assertRaises(ValueError):
            ob.add(self.path, 'ok-id', 'x', 'observe', 'not-a-date')

    def test_corrupt_file_is_reseeded_not_fatal(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{broken', encoding='utf-8')
        self.assertTrue(ob.load(self.path)['items'])


class Render(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'o.json'
        self.addCleanup(self.tmp.cleanup)

    def test_overdue_item_turns_the_collapsed_title_red(self):
        now = dt.datetime(2026, 11, 8, 9, 0, tzinfo=KST)   # 장세 필터 만료(11-07 07:51) 이후
        page = ob.render(self.path, {'port': 8765, 'token': 't'}, now=now)
        self.assertIn('기한 지남', page)
        self.assertIn('#d64545', page)
        self.assertIn('id=obs-fold', page)

    def test_soon_and_quiet_states(self):
        soon = ob.render(self.path, None, now=dt.datetime(2026, 11, 6, 20, 0, tzinfo=KST))
        self.assertIn('24시간 안', soon)
        quiet = ob.render(self.path, None, now=dt.datetime(2026, 11, 5, 10, 0, tzinfo=KST))
        self.assertNotIn('기한 지남', quiet)
        self.assertNotIn('24시간 안', quiet)

    def test_done_items_do_not_raise_the_alarm(self):
        ob.load(self.path)
        ob.apply(self.path, 'regime-expiry', {'action': 'done', 'note': '유지 결정'})
        page = ob.render(self.path, None, now=dt.datetime(2026, 11, 8, 9, 0, tzinfo=KST))
        self.assertNotIn('기한 지남 1건', page)

    def test_text_is_escaped_and_token_not_leaked_when_disabled(self):
        ob.add(self.path, 'xss', '<script>alert(1)</script>', 'observe', criteria='<b>x</b>')
        page = ob.render(self.path, None)
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertIn('var A=null', page)


class ServerCheck(unittest.TestCase):
    def setUp(self):
        self.srv = mo.ManualOrderServer(port=8765, token='tok', allowed_kinds={'obs'})

    def call(self, doc, host='127.0.0.1:8765'):
        return self.srv.check(host, 'null', json.dumps(doc).encode(), 'obs')

    def test_valid_request_passes_with_id_and_payload(self):
        code, value = self.call({'token': 'tok', 'confirm': True, 'id': 'limit-order', 'action': 'done', 'note': '확인'})
        self.assertEqual(code, 200)
        self.assertEqual(value, ('limit-order', {'action': 'done', 'note': '확인'}))

    def test_rejects_bad_token_action_id_and_unconfirmed(self):
        base = {'token': 'tok', 'confirm': True, 'id': 'limit-order', 'action': 'done'}
        self.assertEqual(self.call(dict(base, token='x'))[0], 403)
        for patch in ({'action': 'buy'}, {'id': 'KRW-BTC'}, {'confirm': False}, {'note': 'x' * 301}):
            self.assertEqual(self.call(dict(base, **patch))[0], 400, patch)
        self.assertEqual(self.call(base, host='evil.com')[0], 403)

    def test_kind_must_be_allowed(self):
        srv = mo.ManualOrderServer(port=8765, token='tok', allowed_kinds={'grant'})
        body = json.dumps({'token': 'tok', 'confirm': True, 'id': 'a', 'action': 'done'}).encode()
        self.assertEqual(srv.check('127.0.0.1:8765', 'null', body, 'obs')[0], 403)


if __name__ == '__main__':
    unittest.main()
