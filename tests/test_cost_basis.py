import unittest

from packages.core.dashboard import build_dashboard, summarize_account


def trade(trade_id, day, side, shares, price, fee=0, account=1, symbol='TEST'):
    return {'id': trade_id, 'trade_date': day, 'account_id': account, 'symbol': symbol,
            'currency': 'USD', 'side': side, 'shares': shares, 'price': price, 'fee': fee}


TRADES = [
    trade(1, '2025-01-02', 'buy', 5, 20, 1),
    trade(2, '2025-01-06', 'sell', 5, 18, 1),
    trade(3, '2025-01-06', 'buy', 4, 23, .40),
]
QUOTES = {'TEST': {'price': 27}}
CALIBRATION = {'account_id': 1, 'symbol': 'TEST', 'as_of': '2025-01-07', 'realized_baseline_usd': -16.50}


class CostBasisTests(unittest.TestCase):
    def check_fields(self, row, **expected):
        for key, value in expected.items():
            self.assertAlmostEqual(row[key], value, msg=key)

    def test_round_trip_with_fees_and_broker_calibration(self):
        dividend = {'id': 8, 'account_id': 1, 'symbol': 'TEST', 'kind': 'dividend',
                    'entry_date': '2025-01-07', 'currency': 'USD', 'amount': 2}
        for price in (10, 21.975, 24, 26.1, 27):
            with self.subTest(price=price):
                result = build_dashboard(TRADES, {}, {'TEST': {'price': price}}, '2025-01-02',
                                         '2025-01-07', include_history=False, cash_entries=[dividend],
                                         calibrations=[CALIBRATION])
                holding = result['holdings'][0]
                for row in (holding, holding['accounts'][0], result['summary']):
                    self.check_fields(row, shares=4, cost=92.4, net_cost=104.4, realized_pnl=-12,
                                      realized_baseline=-16.5, diluted_cost=87.9,
                                      average_price=21.975, breakeven_price=26.1,
                                      unrealized_pnl=4*price-87.9, pnl=4*price-87.9,
                                      net_pnl=4*price-104.4, total_return=4*price-104.4+2)
                    self.assertAlmostEqual(row['unrealized_pnl'] + row['realized_baseline'], row['net_pnl'])
                self.assertEqual(result['summary']['total_pnl'], holding['net_pnl'])

    def test_cutoff_is_inclusive_and_later_realizations_continue(self):
        # A blank calibration uses the transaction accounting. A zero broker
        # balance is an explicit statement value and must not take that fallback.
        end = CALIBRATION['as_of']
        for date, calibrations, expected in [
                (end, [], -12), ('2025-01-06', [CALIBRATION], -12),
                (end, [CALIBRATION], -16.5), (end, [dict(CALIBRATION, realized_baseline_usd=0)], 0)]:
            summary = summarize_account(TRADES, QUOTES, date, calibrations=calibrations)['summary']
            self.assertAlmostEqual(summary['realized_baseline'], expected)
        trades = TRADES + [trade(4, '2025-01-08', 'sell', 1, 25, 1)]
        result = summarize_account(trades, QUOTES, '2025-01-08', calibrations=[CALIBRATION])
        self.check_fields(result['holdings'][0], realized_baseline=-15.6, realized_pnl=-11.1,
                          diluted_cost=64.8, net_cost=80.4, shares=3, average_price=21.6, net_pnl=.6)

    def test_scope_open_and_closed_accounts_and_second_symbol(self):
        extra = [
            trade(4, '2025-01-02', 'buy', 1, 12, account=2),
            trade(5, '2025-01-07', 'sell', 1, 15, account=2),
            trade(6, '2025-01-03', 'buy', 2, 10, account=1, symbol='ABC'),
        ]
        result = summarize_account(TRADES + extra, {**QUOTES, 'ABC': {'price': 11}}, '2025-01-07',
                                   calibrations=[CALIBRATION])
        other, holding = result['holdings']
        self.check_fields(holding['accounts'][0], net_cost=104.4, realized_baseline=-16.5)
        self.assertEqual([p['account_id'] for p in holding['accounts']], [1])
        self.check_fields(holding, net_cost=101.4, realized_baseline=-13.5, net_pnl=6.6, diluted_cost=87.9)
        self.check_fields(other, net_cost=20, realized_baseline=0, net_pnl=2)
        self.check_fields(result['summary'], net_cost=121.4, realized_baseline=-13.5, net_pnl=8.6)
        self.assertIsNone(result['summary']['average_price'])
        self.assertIsNone(result['summary']['breakeven_price'])

    def test_missing_quote_and_closed_position_have_no_invented_share_prices(self):
        result = summarize_account(TRADES, {}, '2025-01-07', calibrations=[CALIBRATION])
        holding = result['holdings'][0]
        self.check_fields(holding, diluted_cost=87.9, average_price=21.975)
        for field in ('price', 'value', 'net_pnl', 'pnl', 'unrealized_pnl', 'total_return'):
            self.assertIsNone(holding[field])
        sales = TRADES + [trade(4, '2025-01-08', 'sell', 4, 27)]
        closed = summarize_account(sales, {}, '2025-01-08', calibrations=[CALIBRATION])
        self.assertEqual(closed['holdings'], [])
        self.check_fields(closed['summary'], value=0, net_pnl=3.6, realized_baseline=3.6, unrealized_pnl=0)
        self.assertIsNone(closed['summary']['average_price'])
        self.assertIsNone(closed['summary']['breakeven_price'])

        reopened = sales + [trade(5, '2025-01-08', 'buy', 1, 26)]
        result = summarize_account(reopened, QUOTES, '2025-01-08', calibrations=[CALIBRATION])
        self.check_fields(result['holdings'][0], diluted_cost=26, realized_baseline=3.6, unrealized_pnl=1,
                          net_pnl=4.6, breakeven_price=22.4)
