import unittest
from datetime import date
from packages.core.backtest import run

class BacktestTests(unittest.TestCase):
    def test_fixed_and_cash(self):
        bars = [(date(2024, 1, 2), 100), (date(2024, 2, 1), 50), (date(2024, 3, 1), 100)]
        fixed = run(bars, 100)
        self.assertEqual(fixed.invested, 300)
        self.assertEqual(fixed.shares, 4)
        self.assertEqual(fixed.value, 400)
        dip = run(bars, 100, 'dip')
        self.assertGreater(dip.cash, 0)
        self.assertAlmostEqual(dip.value, dip.cash + dip.shares * 100)

    def test_invalid(self):
        with self.assertRaises(ValueError): run([], 100)
        with self.assertRaises(ValueError): run([(date.today(), 1)], 0)
        with self.assertRaises(ValueError): run([(date.today(), 1)], 100, 'bad')
