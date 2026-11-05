import json
import unittest

import manual_orders as mo


class RefreshRequest(unittest.TestCase):
    def test_refresh_needs_token_and_confirm_but_no_market(self):
        s = mo.ManualOrderServer(8765, token='t', allowed_kinds={'refresh'})
        host = '127.0.0.1:8765'
        self.assertEqual(s.check(host, 'null', json.dumps({'token': 't', 'confirm': True}).encode(), 'refresh'), (200, (None, None)))
        self.assertEqual(s.check(host, 'null', json.dumps({'token': 'x', 'confirm': True}).encode(), 'refresh')[0], 403)
        self.assertEqual(s.check(host, 'null', json.dumps({'token': 't'}).encode(), 'refresh')[0], 400)
        self.assertEqual(mo.KINDS['/refresh'], 'refresh')


if __name__ == '__main__':
    unittest.main()
