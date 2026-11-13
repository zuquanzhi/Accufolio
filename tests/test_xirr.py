import unittest
from datetime import date, timedelta

from packages.core.metrics import xirr


class XirrCashflowTests(unittest.TestCase):
    def annual(self, amounts):
        start = date(2020, 1, 1)
        return [(start + timedelta(days=365*i), amount) for i, amount in enumerate(amounts)]

    def test_sales_and_reinvestment_with_unique_root(self):
        # -1000 + 300/1.1 - 200/1.1**2 + 1188/1.1**3 = 0.
        self.assertAlmostEqual(xirr(self.annual([-1000, 300, -200, 1188])), .1, places=11)
        self.assertAlmostEqual(xirr(self.annual([-100, 30, 88])), .1, places=11)
        self.assertAlmostEqual(xirr(self.annual([100, -110])), .1, places=11)

    def test_same_day_aggregation_and_arbitrary_order(self):
        rows = self.annual([-1000, 300, -200, 1188])
        rows[1:2] = [(rows[1][0], 1000), (rows[1][0], -700)]
        rows += [(rows[0][0], 15), (rows[0][0], -15)]
        self.assertAlmostEqual(xirr(rows[::-1]), .1, places=11)

    def test_irregular_dates_use_365_day_year(self):
        start = date(2023, 1, 1)
        days = [0, 93, 257, 500]
        amounts = [-1000, 300, -200]
        last = -sum(amount / 1.15**(day/365) for day, amount in zip(days, amounts)) * 1.15**(500/365)
        rows = [(start + timedelta(days=day), amount) for day, amount in zip(days, [*amounts, last])]
        self.assertAlmostEqual(xirr(rows), .15, places=11)

    def test_two_distinct_roots_are_not_arbitrarily_selected(self):
        with self.assertRaisesRegex(ValueError, 'multiple|uniquely'):
            xirr(self.annual([-100, 230, -132]))  # 10% and 20%.

    def test_three_roots_are_all_isolated(self):
        # Product of (1+r-.9), (1+r-1.1), (1+r-1.2).
        with self.assertRaisesRegex(ValueError, 'multiple|uniquely'):
            xirr(self.annual([-100, 320, -339, 118.8]))

    def test_many_monthly_contributions_remain_well_conditioned(self):
        start = date(1995, 1, 1)
        flows = [(start + timedelta(days=30*i), -100) for i in range(360)]
        end = flows[-1][0] + timedelta(days=30)
        terminal = sum(-amount * 1.07**((end-day).days/365) for day, amount in flows)
        self.assertAlmostEqual(xirr([*flows, (end, terminal)]), .07, places=11)

    def test_close_roots_cannot_hide_between_sampling_points(self):
        with self.assertRaisesRegex(ValueError, 'multiple|uniquely'):
            xirr(self.annual([-100, 200.01, -100.01]))  # 0% and .01%.

    def test_repeated_root_is_rejected_as_ill_conditioned(self):
        with self.assertRaisesRegex(ValueError, 'uniquely'):
            xirr(self.annual([-100, 220, -121]))

    def test_no_root_and_outside_supported_range(self):
        for amounts in ([-100, 50, -100], [-100, 2000], [-100, -20], [100, 20]):
            with self.subTest(amounts=amounts), self.assertRaises(ValueError):
                xirr(self.annual(amounts))

    def test_liquidated_position_does_not_age_with_zero_terminal_value(self):
        flows = self.annual([-100, 110])
        self.assertAlmostEqual(xirr(flows + [(date(2035, 1, 1), 0)]), .1, places=11)
        short = [(date(2023, 1, 1), -100), (date(2023, 2, 1), 110), (date(2035, 1, 1), 0)]
        self.assertIsNone(xirr(short))

    def test_large_amounts_and_range_boundaries(self):
        self.assertAlmostEqual(xirr(self.annual([-1e300, 1.1e300])), .1, places=10)
        self.assertAlmostEqual(xirr(self.annual([-100, .01])), -.9999, places=10)
        self.assertAlmostEqual(xirr(self.annual([-100, 1100])), 10, places=10)

    def test_invalid_and_cancelled_cashflows(self):
        bad = [[], [(date(2023, 1, 1), -100)],
               [(date(2023, 1, 1), -100), (date(2023, 1, 1), 100)],
               self.annual([-100, float('nan')]), self.annual([-100, float('inf')]),
               [('not-a-date', -100), (date(2024, 1, 1), 110)],
               [(date(2023, 1, 1), 1e308), (date(2023, 1, 1), 1e308), (date(2024, 1, 1), -10)]]
        for rows in bad:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                xirr(rows)


if __name__ == '__main__':
    unittest.main()
