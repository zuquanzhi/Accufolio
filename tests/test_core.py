import unittest
from datetime import date
from packages.core.portfolio import Lot, summarize
from packages.core.metrics import twr, max_drawdown, xirr

class PortfolioTests(unittest.TestCase):
    def test_positions(self):
        result = summarize([Lot(1, 100), Lot(2, 130)], 150)
        self.assertEqual((result.shares, result.cost, result.average_price, result.value, result.pnl), (3, 360, 120, 450, 90))
        self.assertAlmostEqual(result.return_rate, .25)

    def test_empty_and_bad_values(self):
        self.assertIsNone(summarize([], 0).average_price)
        with self.assertRaises(ValueError): Lot(0, 1)
        with self.assertRaises(ValueError): summarize([], float('nan'))

class MetricsTests(unittest.TestCase):
    def test_twr(self):
        self.assertAlmostEqual(twr(100, 110), .1)
        with self.assertRaises(ValueError): twr(0, 10)

    def test_drawdown(self):
        rows = [(date(2024, 1, 1), 100), (date(2024, 1, 2), 120), (date(2024, 1, 3), 90), (date(2024, 1, 4), 100)]
        self.assertEqual(max_drawdown(rows), (-.25, date(2024, 1, 3)))
        with self.assertRaises(ValueError): max_drawdown([])
        with self.assertRaises(ValueError): max_drawdown(rows[::-1])

    def test_xirr(self):
        self.assertAlmostEqual(xirr([(date(2023, 1, 1), -100), (date(2024, 1, 1), 110)]), .1)
        self.assertAlmostEqual(xirr([(date(2023, 1, 1), -100), (date(2024, 1, 1), 90)]), -.1)
        self.assertIsNone(xirr([(date(2024, 1, 1), -100), (date(2024, 2, 1), 110)]))
        with self.assertRaises(ValueError): xirr([(date(2023, 1, 1), -100), (date(2024, 1, 1), 2000)])
        with self.assertRaises(ValueError): xirr([(date(2023, 1, 1), 100), (date(2024, 1, 1), 110)])

if __name__ == '__main__': unittest.main()
