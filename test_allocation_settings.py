import tempfile
import unittest
from pathlib import Path
from allocation_settings import AllocationSettings, DEFAULTS, validate
from auto_trader import AutoTrader
from research_v4 import adjust_plan


class AllocationTests(unittest.TestCase):
    def test_persist_without_global_mutation(self):
        from scalping_strategy import RULES
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a=AllocationSettings(root)
            a.update(DEFAULTS | {'max_position': .1, 'max_positions': 4})
            self.assertEqual(AllocationSettings(root).value['max_positions'],4)
            self.assertEqual(a.rules(RULES)['max_position'],.1)
            self.assertEqual(RULES['max_position'],.25)

    def test_invalid_and_inconsistent_values(self):
        for value in ({'risk':float('nan')},{'max_positions':2.5},{'max_positions':True},
                      {'max_position':.8,'max_total':.5},{'risk':.01,'max_open_risk':.005},
                      {'volatility_target':0},{'risk':.02}):
            with self.assertRaises(ValueError):validate(DEFAULTS | value)

    def test_server_action_does_not_change_holdings(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=AutoTrader(Path(tmp));before=a.state.copy()
            a.action(DEFAULTS | {'action':'auto_v3_allocation','max_positions':3})
            self.assertEqual(a.snapshot()['allocation']['max_positions'],3)
            self.assertEqual(a.state['positions'],before['positions'])
            a.running=True
            with self.assertRaises(ValueError):a.action(DEFAULTS | {'action':'auto_v3_allocation'})

    def test_volatility_setting_changes_amount(self):
        p=dict(amount=100000,atr=1,stop=98,cost_rate=.002)
        self.assertEqual(adjust_plan(p,100,.002)['amount'],20000)
        self.assertEqual(adjust_plan(p,100,.01)['amount'],100000)
