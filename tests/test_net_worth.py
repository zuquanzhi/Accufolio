"""Independent accounting examples for net assets, settlements and daily returns."""
from decimal import Decimal
import unittest

from packages.core.net_worth import build_net_worth


def account(id=1, currency='USD', archived=False):
    return {'id': id, 'currency': currency, 'archived': archived}


def cash(id, day, amount, kind='deposit', account_id=1, currency='USD', **extra):
    return {'id': id, 'entry_date': day, 'amount': amount, 'kind': kind,
            'account_id': account_id, 'currency': currency, **extra}


def trade(id=1, day='2026-01-05', side='buy', price=100, fee=0, account_id=1):
    return {'id': id, 'trade_date': day, 'symbol': 'AAA', 'shares': 1,
            'price': price, 'fee': fee, 'side': side, 'account_id': account_id, 'currency': 'USD'}


def history(*prices):
    return {'AAA': [{'day': f'2026-01-{day:02}', 'close': price} for day, price in prices]}


class NetWorthTests(unittest.TestCase):
    def build(self, accounts=None, trades=(), entries=(), links=(), histories=None, fx=(),
              start='2026-01-05', end='2026-01-09', currency='USD'):
        return build_net_worth(accounts or [account()], list(trades), list(entries), list(links),
                               histories or {}, list(fx), start, end, currency)

    def test_salary_and_external_withdrawal_change_assets_not_nav(self):
        result = self.build(entries=[cash(1,'2026-01-05',1000,'balance_open'),
                                    cash(2,'2026-01-06',500),cash(3,'2026-01-07',-200,'withdraw')])
        self.assertEqual([p['value'] for p in result['series']], [1000,1500,1300,1300,1300])
        self.assertEqual([p['nav'] for p in result['series']], [1,1,1,1,1])
        self.assertEqual(result['summary']['net_external_flow'], 300)
        self.assertEqual(result['summary']['gain'], 0)
        self.assertEqual(result['summary']['twr'], 0)

    def test_trade_day_payable_and_later_settlement_do_not_double_assets(self):
        result = self.build(trades=[trade(fee=1)],
            entries=[cash(1,'2026-01-05',1000,'balance_open'),cash(2,'2026-01-06',-101,'withdraw')],
            links=[{'trade_id':1,'cash_entry_id':2}], histories=history((5,100),(6,100),(7,110),(8,105),(9,120)))
        self.assertEqual([p['unsettled'] for p in result['series']], [-101,0,0,0,0])
        self.assertEqual([p['value'] for p in result['series']], [999,999,1009,1004,1019])
        self.assertEqual(result['summary']['net_external_flow'], 0)
        self.assertEqual(result['summary']['gain'], 20)
        self.assertAlmostEqual(result['summary']['twr'], float(Decimal(1019)/Decimal(999)-1))
        self.assertAlmostEqual(result['summary']['max_drawdown'], float(Decimal(1004)/Decimal(1009)-1))
        self.assertEqual(result['summary']['drawdown_date'],'2026-01-08')

    def test_sale_receivable_survives_until_cash_arrives(self):
        result = self.build(trades=[trade(),trade(2,'2026-01-06','sell',120,1)],
            entries=[cash(1,'2026-01-05',1000,'balance_open'),cash(2,'2026-01-05',-100,'withdraw'),
                     cash(3,'2026-01-07',119)],links=[{'trade_id':1,'cash_entry_id':2},{'trade_id':2,'cash_entry_id':3}],
            histories=history((5,100)))
        self.assertEqual([p['value'] for p in result['series']], [1000,1019,1019,1019,1019])
        self.assertEqual(result['series'][1]['unsettled'],119)
        self.assertAlmostEqual(result['summary']['twr'],.019)
        self.assertEqual(result['coverage']['missing_symbols'],[])

    def test_sale_with_fee_above_proceeds_uses_outgoing_settlement(self):
        result=self.build(trades=[trade(),trade(2,'2026-01-06','sell',100,120)],
            entries=[cash(1,'2026-01-05',1000,'balance_open'),cash(2,'2026-01-05',-100,'withdraw'),
                     cash(3,'2026-01-07',-20,'withdraw')],
            links=[{'trade_id':1,'cash_entry_id':2},{'trade_id':2,'cash_entry_id':3}],histories=history((5,100)))
        self.assertEqual([p['value'] for p in result['series']],[1000,880,880,880,880])
        self.assertAlmostEqual(result['summary']['twr'],-.12)
        self.assertTrue(result['coverage']['complete'])

    def test_cash_dividend_interest_fees_and_reversal_are_return(self):
        result = self.build(entries=[cash(1,'2026-01-05',1000,'balance_open'),
            cash(2,'2026-01-06',20,'dividend'),cash(3,'2026-01-07',-20,'withdraw',reverses_entry_id=2),
            cash(4,'2026-01-08',5,'interest'),cash(5,'2026-01-09',-2,'fee')])
        self.assertEqual([p['external_flow'] for p in result['series']], [0,0,0,0,0])
        self.assertEqual(result['summary']['gain'],3)
        self.assertAlmostEqual(result['summary']['twr'],.003)
        self.assertAlmostEqual(result['summary']['max_drawdown'], float(Decimal(1000)/Decimal(1020)-1))

    def test_external_reversal_retains_original_classification(self):
        result = self.build(entries=[cash(1,'2026-01-05',1000,'balance_open'),
            cash(2,'2026-01-06',50),cash(3,'2026-01-07',-50,'withdraw',reverses_entry_id=2)])
        self.assertEqual(result['summary']['twr'],0)
        self.assertEqual(result['summary']['gain'],0)

    def test_incomplete_transfer_cannot_masquerade_as_a_return(self):
        result=self.build(accounts=[account(),account(2)], entries=[
            cash(1,'2026-01-05',1000,'balance_open'),
            cash(2,'2026-01-06',100,'transfer_in',transfer_group='missing',counter_account_id=2)])
        self.assertEqual(result['summary']['end_value'],1100)
        self.assertIsNone(result['summary']['twr'])
        self.assertIsNone(result['summary']['gain'])
        self.assertIn('unpaired_transfer',[i['code'] for i in result['coverage']['issues']])

    def test_internal_transfers_cancel_even_for_archived_accounts(self):
        result = self.build(accounts=[account(),account(2,archived=True)],entries=[
            cash(1,'2026-01-05',1000,'balance_open',account_id=2),
            cash(2,'2026-01-06',-1000,'transfer_out',account_id=2,transfer_group='pair',counter_account_id=1),
            cash(3,'2026-01-06',1000,'transfer_in',transfer_group='pair',counter_account_id=2)])
        self.assertEqual([p['value'] for p in result['series']], [1000]*5)
        self.assertEqual(result['summary']['twr'],0)

    def test_fx_conversion_cost_and_display_currency_change_return_basis(self):
        entries=[cash(1,'2026-01-05',100,'balance_open'),
                 cash(2,'2026-01-06',-100,'fx_out',transfer_group='fx',counter_account_id=2),
                 cash(3,'2026-01-06',700,'fx_in',account_id=2,currency='CNY',transfer_group='fx',counter_account_id=1)]
        fx=[{'day':f'2026-01-{day:02}', 'rates':{'USD':1,'CNY':rate}} for day,rate in [(5,7),(6,7.2)]]
        args=dict(accounts=[account(),account(2,'CNY')], entries=entries, fx=fx,end='2026-01-06')
        usd=self.build(**args);cny=self.build(**args,currency='CNY')
        self.assertAlmostEqual(usd['summary']['twr'],float(Decimal(700)/Decimal('7.2')/Decimal(100)-1))
        self.assertEqual(cny['summary']['twr'],0)
        self.assertEqual(cny['summary']['net_external_flow'],0)

    def test_weekend_flow_uses_actual_date_fx_not_monday_rate(self):
        result=self.build(accounts=[account(),account(2,'CNY')],
            entries=[cash(1,'2026-01-09',1000,'balance_open'),cash(2,'2026-01-10',700,account_id=2,currency='CNY')],
            fx=[{'day':'2026-01-10','rates':{'CNY':7}},{'day':'2026-01-12','rates':{'CNY':7.2}}],
            start='2026-01-09',end='2026-01-12')
        self.assertEqual([p['day'] for p in result['series']], ['2026-01-09','2026-01-12'])
        self.assertEqual(result['summary']['net_external_flow'],100)
        expected=(Decimal(1000)+Decimal(700)/Decimal('7.2'))/Decimal(1100)-1
        self.assertAlmostEqual(result['summary']['twr'],float(expected))

    def test_missing_flow_fx_cannot_be_hidden_by_complete_valuations(self):
        result=self.build(accounts=[account(),account(2,'CNY')],
            entries=[cash(1,'2026-01-09',1000,'balance_open'),cash(2,'2026-01-10',700,account_id=2,currency='CNY')],
            fx=[{'day':'2026-01-12','rates':{'CNY':7.2}}],start='2026-01-09',end='2026-01-12')
        self.assertTrue(result['coverage']['valuation_complete'])
        self.assertFalse(result['coverage']['performance_complete'])
        self.assertIsNone(result['summary']['twr'])
        self.assertIsNone(result['summary']['net_external_flow'])
        self.assertIsNone(result['summary']['gain'])

    def test_unmatched_or_invalid_settlement_does_not_guess_external_funding(self):
        kwargs=dict(trades=[trade()],entries=[cash(1,'2026-01-05',1000,'balance_open'),
                    cash(2,'2026-01-06',-99,'withdraw')],histories=history((5,100),(6,100)),end='2026-01-06')
        for links in [[],[{'trade_id':1,'cash_entry_id':2}]]:
            result=self.build(**kwargs,links=links)
            self.assertEqual(result['coverage']['unmatched_trade_ids'],[1])
            self.assertFalse(result['coverage']['valuation_complete'])
            self.assertIsNotNone(result['summary']['end_value'])
            for field in ['twr','max_drawdown','gain','net_external_flow']:
                self.assertIsNone(result['summary'][field])
            self.assertTrue(all(p['nav'] is None for p in result['series']))

    def test_missing_price_breaks_nav_without_filling_or_restarting(self):
        result=self.build(trades=[trade()],entries=[cash(1,'2026-01-05',1000,'balance_open'),
                    cash(2,'2026-01-05',-100,'withdraw')],links=[{'trade_id':1,'cash_entry_id':2}],
                    histories=history((5,100),(6,110),(8,120),(9,125)))
        self.assertIsNone(result['series'][2]['value'])
        self.assertEqual(result['series'][3]['value'],1020)
        self.assertTrue(all(p['nav'] is None for p in result['series'][2:]))
        self.assertIsNone(result['summary']['twr'])
        self.assertIsNone(result['summary']['max_drawdown'])

    def test_window_uses_old_balances_without_demanding_old_fx_or_closes(self):
        result=self.build(accounts=[account(),account(2,'CNY')],trades=[trade(day='2010-01-04')],
            entries=[cash(1,'2010-01-04',1000,'balance_open'),cash(2,'2010-01-05',-100,'withdraw'),
                     cash(3,'2010-01-04',700,'balance_open',account_id=2,currency='CNY')],
            links=[{'trade_id':1,'cash_entry_id':2}],histories=history((5,100),(6,110)),
            fx=[{'day':'2026-01-05','rates':{'CNY':7}},{'day':'2026-01-06','rates':{'CNY':7}}],end='2026-01-06')
        self.assertEqual(result['summary']['start_value'],1100)
        self.assertEqual(result['summary']['gain'],10)
        self.assertAlmostEqual(result['summary']['twr'],float(Decimal(1110)/Decimal(1100)-1))
        self.assertTrue(result['coverage']['complete'])

    def test_zero_capital_and_future_entries(self):
        result=self.build(entries=[cash(1,'2026-01-05',0,'balance_open'),cash(2,'2026-01-06',100),
            cash(3,'2026-01-07',-100,'withdraw'),cash(4,'2026-01-12',999)])
        self.assertEqual([p['value'] for p in result['series']],[0,100,0,0,0])
        self.assertEqual(result['summary']['twr'],0)
        invalid=self.build(entries=[cash(1,'2026-01-05',0,'balance_open'),cash(2,'2026-01-06',1,'interest')])
        self.assertIsNone(invalid['summary']['twr'])
        self.assertIn('zero_capital',[i['code'] for i in invalid['coverage']['issues']])

    def test_actual_rounded_settlement_has_no_artificial_settlement_day_return(self):
        result=self.build(trades=[trade(price=100.004)], entries=[cash(1,'2026-01-05',1000,'balance_open'),
             cash(2,'2026-01-06',-100,'withdraw')],links=[{'trade_id':1,'cash_entry_id':2}],
             histories=history((5,100),(6,100)),end='2026-01-06')
        self.assertEqual([p['value'] for p in result['series']],[1000,1000])
        self.assertEqual(result['summary']['twr'],0)

    def test_empty_and_one_valuation_do_not_claim_performance(self):
        result=self.build()
        self.assertEqual(result['series'],[])
        self.assertIsNone(result['summary']['twr'])
        one=self.build(entries=[cash(1,'2026-01-05',100,'balance_open')],end='2026-01-05')
        self.assertEqual(one['series'][0]['nav'],1)
        self.assertIsNone(one['summary']['twr'])
        self.assertFalse(one['coverage']['performance_complete'])
        zero=self.build(entries=[cash(1,'2026-01-05',0,'balance_open')])
        self.assertIsNone(zero['summary']['twr'])
        self.assertIn('no_capital',[i['code'] for i in zero['coverage']['issues']])


if __name__=='__main__':
    unittest.main()
