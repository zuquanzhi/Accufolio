"""Cash-paid dividends: attributable receipts, corrections, dates and no cash double count."""
from datetime import date
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from apps.api.main import db, summary
from apps.api.dashboard import router
from apps.api.accounts import assets
from packages.core.dashboard import summarize_account, reconstruct, build_dashboard
from packages.providers import Quote, QuoteResult


def trade(account=1, symbol='AAA', day='2025-01-02', side='buy', price=100, shares=1, fee=0, id=1):
    return dict(id=id, account_id=account, symbol=symbol, trade_date=day, side=side, price=price,
                shares=shares, fee=fee, currency='USD')


def dividend(amount=10, day='2026-01-02', account=1, symbol='AAA', currency='USD', id=1):
    return dict(id=id, account_id=account, symbol=symbol, currency=currency, kind='dividend',
                entry_date=day, amount=amount, reverses_entry_id=None)


def reverse(original, day, id=2):
    return dict(original, id=id, kind='withdraw', entry_date=day, amount=-original['amount'], reverses_entry_id=original['id'])


class DividendTests(unittest.TestCase):
    def test_receipt_increases_return_and_xirr_but_not_value_cost_or_trade_profit(self):
        result=summarize_account([trade()], {'AAA':{'price':100}}, '2026-01-02', [dividend()])
        s=result['summary']
        self.assertEqual((s['value'],s['cost'],s['total_pnl'],s['unrealized_pnl'],s['realized_pnl']),(100,100,0,0,0))
        self.assertEqual((s['cash_dividends'],s['total_return']),(10,10))
        self.assertAlmostEqual(s['xirr'],.1)
        self.assertEqual(result['holdings'][0]['cash_dividends'],10)
        self.assertEqual(result['holdings'][0]['total_return'],10)
        self.assertTrue(s['dividends_complete'])

    def test_after_sale_receipt_is_retained_without_quote_and_extends_cashflow_horizon(self):
        rows=[trade(),trade(side='sell',day='2025-06-02',id=2)]
        result=summarize_account(rows, {}, '2026-01-02', [dividend()])
        self.assertEqual(result['holdings'],[])
        self.assertEqual(result['summary']['value'],0)
        self.assertEqual(result['summary']['total_return'],10)
        rate=result['summary']['xirr']
        self.assertAlmostEqual(-100+100/(1+rate)**((date(2025,6,2)-date(2025,1,2)).days/365)+10/(1+rate),0)
        self.assertEqual(result['dividend_breakdown'][0]['symbol'],'AAA')
        self.assertEqual(result['dividend_breakdown'][0]['cash_dividends'],10)

    def test_same_symbol_separate_accounts_and_closed_account_contribution(self):
        rows=[trade(),trade(account=2,id=2),trade(side='sell',day='2025-06-02',price=110,id=3)]
        receipts=[dividend(3),dividend(7,account=2,id=2)]
        result=summarize_account(rows, {'AAA':{'price':105}}, '2026-01-02', receipts)
        h=result['holdings'][0]
        self.assertEqual(h['cash_dividends'],10)
        self.assertEqual(h['total_return'],25)
        self.assertEqual(h['accounts'][0]['account_id'],2)
        self.assertEqual(h['accounts'][0]['cash_dividends'],7)
        self.assertEqual(result['dividend_breakdown'][0]['accounts'],[
            {'account_id':1,'cash_dividends':3},{'account_id':2,'cash_dividends':7}])

    def test_actual_receipt_date_including_weekend_and_window_opening_balance(self):
        rows=[trade(day='2025-01-02')]
        histories={'AAA':[{'day':day,'close':price} for day,price in [('2025-01-03',90),('2025-01-06',90),('2025-01-07',90)]]}
        receipts=[dividend(day='2025-01-04')]
        series=reconstruct(rows,histories,'2025-01-03','2025-01-07',receipts)['series']
        self.assertEqual([p['value'] for p in series],[90,90,90])
        self.assertEqual([p['cash_dividends'] for p in series],[0,10,10])
        self.assertEqual([p['trading_pnl'] for p in series],[-10,-10,-10])
        self.assertEqual([p['pnl'] for p in series],[-10,0,0])
        cropped=reconstruct(rows,histories,'2025-01-07','2025-01-07',receipts)['series']
        self.assertEqual(cropped[0]['cash_dividends'],10)

    def test_reversal_is_signed_cashflow_on_reversal_date_and_future_excluded(self):
        receipt=dividend(day='2025-01-03')
        correction=reverse(receipt,'2025-01-07')
        future=dividend(999,day='2026-01-03',id=3)
        rows=[trade()]
        histories={'AAA':[{'day':day,'close':100} for day in ['2025-01-03','2025-01-06','2025-01-07']]}
        series=reconstruct(rows,histories,'2025-01-03','2025-01-07',[receipt,correction,future])['series']
        self.assertEqual([p['pnl'] for p in series],[10,10,0])
        result=summarize_account(rows,{'AAA':{'price':100}},'2026-01-02',[receipt,correction,future])
        self.assertEqual(result['summary']['cash_dividends'],0)
        self.assertEqual([event['amount'] for event in result['dividend_events']],[10,-10])
        self.assertEqual(result['dividend_events'][1]['reverses_entry_id'],1)

    def test_unattributable_receipts_block_total_return_until_reversed(self):
        for changes in [{'symbol':None},{'currency':'CNY'},{'account_id':2},{'symbol':'BBB'}]:
            with self.subTest(changes=changes):
                receipt={**dividend(day='2025-01-03'),**changes}
                correction=reverse(receipt,'2025-01-07')
                rows=[trade()]
                histories={'AAA':[{'day':day,'close':100} for day in ['2025-01-03','2025-01-06','2025-01-07']]}
                result=build_dashboard(rows,histories,{'AAA':{'price':100}},'2025-01-03','2025-01-06',cash_entries=[receipt,correction])
                self.assertIsNone(result['summary']['total_return'])
                self.assertIsNone(result['summary']['xirr'])
                self.assertFalse(result['coverage']['dividends_complete'])
                self.assertEqual(len(result['dividend_issues']),1)
                self.assertTrue(all(p['pnl'] is None for p in result['series']))
                resolved=build_dashboard(rows,histories,{'AAA':{'price':100}},'2025-01-03','2025-01-07',cash_entries=[receipt,correction])
                self.assertEqual(resolved['summary']['total_return'],0)
                self.assertTrue(resolved['summary']['dividends_complete'])
                self.assertEqual([p['pnl'] for p in resolved['series']],[None,None,0])

    def test_future_buy_cannot_claim_earlier_dividend_and_nondividend_is_excluded(self):
        receipt=dividend(day='2025-01-02')
        result=summarize_account([trade(day='2025-01-03')],{'AAA':{'price':100}},'2026-01-02',[receipt])
        self.assertFalse(result['summary']['dividends_complete'])
        other=[dict(dividend(),kind=kind) for kind in ['interest','deposit','balance_open','fee']]
        result=summarize_account([trade()],{'AAA':{'price':100}},'2026-01-02',other)
        self.assertTrue(result['summary']['dividends_complete'])
        self.assertEqual(result['summary']['cash_dividends'],0)

    def test_missing_quotes_preserve_known_cash_dividends_but_no_total_return(self):
        result=summarize_account([trade()],{},'2026-01-02',[dividend()])
        self.assertEqual(result['summary']['cash_dividends'],10)
        self.assertIsNone(result['summary']['total_return'])
        self.assertIsNone(result['summary']['xirr'])

    def test_malformed_dividend_corrections_remain_visible_even_when_amounts_cancel(self):
        receipt=dividend()
        bad=reverse(receipt,'2026-01-02')
        bad['account_id']=2
        result=summarize_account([trade()],{'AAA':{'price':100}},'2026-01-02',[receipt,bad])
        self.assertFalse(result['summary']['dividends_complete'])
        self.assertIn('不完整',result['dividend_issues'][0]['reason'])

    def test_same_day_correction_of_unassigned_dividend_leaves_no_false_gap(self):
        receipt=dividend(day='2025-01-03',symbol=None)
        rows=[trade()]
        result=build_dashboard(rows,{'AAA':[{'day':'2025-01-03','close':100}]},{'AAA':{'price':100}},
                               '2025-01-03','2025-01-03',cash_entries=[receipt,reverse(receipt,'2025-01-03')])
        self.assertEqual(result['series'][0]['pnl'],0)
        self.assertEqual(result['coverage']['dividend_missing_days'],0)


class DividendApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'ACCUFOLIO_DB':str(Path(self.temp.name)/'dividends.db'),'ACCUFOLIO_PASSWORD':'test'})
        self.env.start()
        app=FastAPI();app.include_router(router);self.client=TestClient(app)
        with db() as con:
            con.execute("INSERT INTO trades(symbol,trade_date,shares,price,side) VALUES('AAA','2025-01-02',1,100,'buy')")
            con.execute("INSERT INTO cash_entries(account_id,entry_date,kind,amount,currency,symbol) VALUES(1,'2026-01-02','dividend',10,'USD','AAA')")
            con.execute("INSERT INTO cash_entries(account_id,entry_date,kind,amount,currency) VALUES(1,'2025-01-02','balance_open',100,'USD')")
            cid=con.execute("INSERT INTO cash_entries(account_id,entry_date,kind,amount,currency) VALUES(1,'2025-01-02','withdraw',-100,'USD')").lastrowid
            con.execute('INSERT INTO trade_settlements(trade_id,cash_entry_id) VALUES(1,?)',(cid,))

    def tearDown(self):
        self.client.close();self.env.stop();self.temp.cleanup()

    def test_dashboard_single_symbol_and_assets_agree_without_double_counting(self):
        quote=QuoteResult(Quote('AAA',100,0,'test',0),False,())
        with patch('apps.api.dashboard.get_quote',return_value=quote),patch('apps.api.dashboard.get_history') as history:
            result=self.client.get('/api/dashboard?include_history=false',auth=('admin','test')).json()
        history.assert_not_called()
        self.assertEqual(result['summary']['cash_dividends'],10)
        self.assertEqual(result['summary']['value'],100)
        with patch('apps.api.main.get_quote',return_value=quote):single=summary('AAA',actor='test')
        self.assertEqual(single['cash_dividends'],10)
        self.assertEqual(single['total_return'],10)
        self.assertEqual(single['xirr'],result['summary']['xirr'])
        with patch('packages.providers.get_quote',return_value=quote):wealth=assets(actor='test')
        self.assertEqual(wealth['securities_value_usd'],100)
        self.assertEqual(wealth['cash_value_usd'],10)
        self.assertEqual(wealth['net_assets_usd'],110)

    def test_single_symbol_does_not_include_other_symbol_dividends(self):
        with db() as con:
            con.execute("INSERT INTO cash_entries(account_id,entry_date,kind,amount,currency,symbol) VALUES(1,'2026-01-02','dividend',20,'USD','BBB')")
        with patch('apps.api.main.get_quote',return_value=QuoteResult(Quote('AAA',100,0,'test',0),False,())):
            result=summary('AAA',actor='test')
        self.assertEqual(result['cash_dividends'],10)
        self.assertTrue(result['dividends_complete'])


if __name__=='__main__':unittest.main()
