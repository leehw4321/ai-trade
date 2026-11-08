import unittest
from strategy import Params
import shadow

TIERS = [[0.03, 5], [0.01, 4], [-0.01, 3], [-0.03, 2], [-9, 1]]


class RegimeSlots(unittest.TestCase):
    def test_tiers(self):
        b = Params(regime_slots=TIERS)
        got = [shadow.effective_slots(b, x) for x in (0.05, 0.02, 0.0, -0.02, -0.05)]
        self.assertEqual(got, [5, 4, 3, 2, 1])

    def test_off_or_unknown_index(self):
        self.assertEqual(shadow.effective_slots(Params(), -0.05), Params().slots)
        self.assertEqual(shadow.effective_slots(Params(regime_slots=TIERS), None), Params().slots)

    def test_cap_only(self):
        b = Params(regime_slots=TIERS, regime_slots_cap_only=True)
        self.assertEqual(shadow.effective_slots(b, 0.05), b.slots)
        self.assertEqual(shadow.effective_slots(b, -0.05), 1)


if __name__ == '__main__':
    unittest.main()
