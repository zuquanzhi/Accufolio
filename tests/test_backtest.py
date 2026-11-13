import unittest
from datetime import date
from packages.core.backtest import run
from packages.core.rules import calm_signal, movement_level

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

class RuleTests(unittest.TestCase):
    def test_calm(self):
        self.assertTrue(calm_signal(90, [100]*20, 22, 30))
        self.assertFalse(calm_signal(99, [100]*20, 22, 30))
        self.assertFalse(calm_signal(90, [100]*20, 29, 30))
        with self.assertRaises(ValueError): calm_signal(90, [], 22, 30)
    def test_move(self):
        self.assertEqual(movement_level(-2), 1)
        self.assertEqual(movement_level(1.9), 0)
