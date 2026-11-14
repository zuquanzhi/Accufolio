"""Independent Decimal ledger examples for complete-account performance.

Expected paths are calculated from explicitly known transactions, not by
calling the implementation's cash classifier or valuation helpers.
"""
from decimal import Decimal as D
import random
import unittest

from packages.core.net_worth import build_net_worth


DAYS = [f'2026-01-{day:02}' for day in range(5, 10)]


def account(id, currency='USD', archived=False):
    return {'id': id, 'currency': currency, 'archived': archived}


def entry(id, day, amount, kind='deposit', account_id=1, currency='USD', **extra):
    return {'id': id, 'entry_date': day, 'amount': amount, 'kind': kind,
            'account_id': account_id, 'currency': currency, **extra}


def trade(id, day, side, price, fee=1):
    return {'id': id, 'account_id': 1, 'currency': 'USD', 'symbol': 'AAA',
            'shares': 1, 'price': price, 'fee': fee, 'side': side, 'trade_date': day}


class NetWorthOracleTests(unittest.TestCase):
    def test_hand_calculated_mixed_assets_payroll_fx_income_reversal_and_t_plus_one(self):
        cash = [
            entry(1,DAYS[0],1000,'balance_open'),
            entry(2,DAYS[0],7000,'balance_open',2,'CNY'),
            entry(3,DAYS[1],700,'deposit',2,'CNY'),  # Salary: USD 100 external capital.
            entry(4,DAYS[2],-101,'withdraw'),  # Buy settlement; expense is the USD 1 fee.
            entry(5,DAYS[2],10,'dividend',symbol='AAA'),
            entry(6,DAYS[3],-100,'fx_out',transfer_group='fx',counter_account_id=2,fx_rate=7),
            entry(7,DAYS[3],700,'fx_in',2,'CNY',transfer_group='fx',counter_account_id=1,fx_rate=1/7),
            entry(8,DAYS[3],70,'interest',2,'CNY'),
            entry(9,DAYS[3],-2,'fee'),
            entry(10,DAYS[3],-10,'withdraw',reverses_entry_id=5),
            entry(11,DAYS[4],-200,'withdraw'),  # An actual external withdrawal.
            entry(12,'2026-01-12',119,'deposit'),  # Sale receivable at the final close.
        ]
        result=build_net_worth([account(1),account(2,'CNY')],
            [trade(1,DAYS[1],'buy',100),trade(2,DAYS[4],'sell',120)], cash,
            [{'trade_id':1,'cash_entry_id':4},{'trade_id':2,'cash_entry_id':12}],
            {'AAA':[{'day':day,'close':price} for day,price in zip(DAYS,[100,105,90,110,120])]},
            [{'day':day,'rates':{'USD':1,'CNY':rate}} for day,rate in zip(DAYS,[7,7,7.7,7,7])],
            DAYS[0],DAYS[-1])
        expected_values=[D(2000),D(2104),D(1999),D(2117),D(1926)]
        expected_nav=[D(1),D(2104)/D(2100),D(1999)/D(2100),D(2117)/D(2100),D(2126)/D(2100)]
        for point,value,nav in zip(result['series'],expected_values,expected_nav):
            self.assertAlmostEqual(point['value'],float(value),places=9)
            self.assertAlmostEqual(point['nav'],float(nav),places=12)
        self.assertEqual([point['external_flow'] for point in result['series']],[0,100,0,0,-200])
        self.assertEqual([point['unsettled'] for point in result['series']],[0,-101,0,0,119])
        self.assertAlmostEqual(result['summary']['gain'],26)
        self.assertAlmostEqual(result['summary']['twr'],float(D(26)/D(2100)))
        self.assertAlmostEqual(result['summary']['max_drawdown'],float(D(1999)/D(2104)-1))
        self.assertEqual(result['summary']['drawdown_date'],DAYS[2])
        self.assertTrue(result['coverage']['complete'])

    def test_deterministic_decimal_cash_oracle_over_external_and_income_changes(self):
        rng=random.Random(270013)
        for case in range(50):
            balance=D('1000.00'); nav=D(1); peak=D(1); drawdown=D(0)
            expected_values=[balance]; expected_nav=[nav]; net_flow=D(0)
            rows=[entry(1,DAYS[0],float(balance),'balance_open')]
            for day in DAYS[1:]:
                incoming=D(rng.randint(0,50000))/100
                outgoing=D(rng.randint(0,40000))/100
                interest=D(rng.randint(0,1000))/100
                fee=D(rng.randint(0,1000))/100
                before=balance
                balance+=incoming-outgoing+interest-fee
                nav*= (balance+outgoing)/(before+incoming)
                peak=max(peak,nav);drawdown=min(drawdown,nav/peak-1)
                net_flow+=incoming-outgoing
                for amount,kind in [(incoming,'deposit'),(-outgoing,'withdraw'),(interest,'interest'),(-fee,'fee')]:
                    if amount:
                        rows.append(entry(len(rows)+1,day,float(amount),kind))
                expected_values.append(balance);expected_nav.append(nav)
            result=build_net_worth([account(1)],[],rows,[],{},[],DAYS[0],DAYS[-1])
            with self.subTest(case=case):
                for point,value,expected in zip(result['series'],expected_values,expected_nav):
                    self.assertAlmostEqual(point['value'],float(value),places=8)
                    self.assertAlmostEqual(point['nav'],float(expected),places=12)
                self.assertAlmostEqual(result['summary']['net_external_flow'],float(net_flow))
                self.assertAlmostEqual(result['summary']['gain'],float(balance-D(1000)-net_flow))
                self.assertAlmostEqual(result['summary']['twr'],float(nav-1))
                self.assertAlmostEqual(result['summary']['max_drawdown'],float(drawdown))

    def test_gap_keeps_later_values_but_never_restarts_nav(self):
        result=build_net_worth([account(1,'CNY')],[],[entry(1,DAYS[0],700,'balance_open',currency='CNY')],[],{},
            [{'day':day,'rates':{'CNY':7}} for day in [DAYS[0],DAYS[1],DAYS[3],DAYS[4]]],DAYS[0],DAYS[-1])
        self.assertEqual([p['value'] for p in result['series']],[100,100,None,100,100])
        self.assertEqual([p['nav'] for p in result['series']],[1,1,None,None,None])
        self.assertIsNone(result['summary']['twr'])
        self.assertIsNone(result['summary']['max_drawdown'])

    def test_window_baseline_does_not_need_old_fx_and_archived_account_stays_in_scope(self):
        result=build_net_worth([account(1),account(2,'CNY',True)],[],[
            entry(1,'2025-12-01',700,'balance_open',2,'CNY'),
            entry(2,DAYS[2],-700,'fx_out',2,'CNY',transfer_group='archive',counter_account_id=1,fx_rate=1/7),
            entry(3,DAYS[2],100,'fx_in',transfer_group='archive',counter_account_id=2,fx_rate=7)],[],{},
            [{'day':day,'rates':{'CNY':7}} for day in DAYS],DAYS[0],DAYS[-1])
        self.assertEqual([p['value'] for p in result['series']],[100]*5)
        self.assertEqual([p['nav'] for p in result['series']],[1]*5)
        self.assertEqual(result['summary']['net_external_flow'],0)
        self.assertTrue(result['coverage']['complete'])

    def test_zero_net_sale_settlement_recognizes_fee_without_fake_external_flow(self):
        result=build_net_worth([account(1)],
            [trade(1,DAYS[0],'buy',100,0),trade(2,DAYS[1],'sell',100,100)],
            [entry(1,DAYS[0],1000,'balance_open'),entry(2,DAYS[0],-100,'withdraw')],
            [{'trade_id':1,'cash_entry_id':2}],{'AAA':[{'day':DAYS[0],'close':100}]},[],DAYS[0],DAYS[-1])
        self.assertEqual([p['value'] for p in result['series']],[1000,900,900,900,900])
        self.assertEqual([p['external_flow'] for p in result['series']],[0]*5)
        self.assertEqual(result['summary']['gain'],-100)
        self.assertAlmostEqual(result['summary']['twr'],-.1)

    def test_interest_and_fee_corrections_are_returns_but_salary_correction_is_capital(self):
        result=build_net_worth([account(1)],[],[
            entry(1,DAYS[0],1000,'balance_open'),entry(2,DAYS[1],50,'interest'),
            entry(3,DAYS[1],100,'deposit'),entry(4,DAYS[2],-50,'withdraw',reverses_entry_id=2),
            entry(5,DAYS[2],-100,'withdraw',reverses_entry_id=3),
            entry(6,DAYS[3],-10,'fee'),entry(7,DAYS[4],10,'deposit',reverses_entry_id=6)],
            [],{},[],DAYS[0],DAYS[-1])
        # Salary and its correction are capital; only the income/fee pair changes NAV.
        expected_nav=[D(1),D(1150)/D(1100),D(1),D(99)/D(100),D(1)]
        self.assertEqual([p['value'] for p in result['series']],[1000,1150,1000,990,1000])
        self.assertEqual([p['external_flow'] for p in result['series']],[0,100,-100,0,0])
        for p,expected in zip(result['series'],expected_nav):
            self.assertAlmostEqual(p['nav'],float(expected),places=12)
        self.assertAlmostEqual(result['summary']['twr'],0)
        self.assertAlmostEqual(result['summary']['max_drawdown'],float(D('0.99')/(D(1150)/D(1100))-1))

    def test_raw_link_with_different_symbol_or_counterparty_is_not_settlement_evidence(self):
        for extra in [{'symbol':'BBB'},{'counter_account_id':1}]:
            with self.subTest(extra=extra):
                result=build_net_worth([account(1)],[trade(1,DAYS[0],'buy',100,0)],
                    [entry(1,DAYS[0],1000,'balance_open'),entry(2,DAYS[0],-100,'withdraw',**extra)],
                    [{'trade_id':1,'cash_entry_id':2}],
                    {'AAA':[{'day':day,'close':100} for day in DAYS]},[],DAYS[0],DAYS[-1])
                self.assertFalse(result['coverage']['complete'])
                self.assertIn(1,result['coverage']['unmatched_trade_ids'])
                self.assertIsNone(result['summary']['twr'])


if __name__=='__main__':unittest.main()
