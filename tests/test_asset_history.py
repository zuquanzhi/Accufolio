from datetime import date
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from apps.api.main import app, db, Trade, mutate_trade
from apps.api.accounts import Account, CashEntry, save_account, add_cash_entry
from apps.api.settlements import set_trade_settlement
from apps.api.asset_history import asset_history

CLOCK={'date':'2025-01-07','last_completed_session':'2025-01-07','timezone':'America/New_York','phase':'afterhours'}
BARS=[{'day':day,'close':price} for day,price in [('2025-01-02',100),('2025-01-03',110),('2025-01-06',120),('2025-01-07',115)]]


class AssetHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'ACCUFOLIO_DB':str(Path(self.temp.name)/'test.db'),'ACCUFOLIO_PASSWORD':'test'})
        self.env.start();self.sequence=0
        with db():pass
        self.clock=patch('apps.api.asset_history.market_clock',return_value=CLOCK);self.clock.start()

    def tearDown(self):
        self.clock.stop();self.env.stop();self.temp.cleanup()

    def key(self):
        self.sequence+=1;return 'history-'+str(self.sequence)

    def revision(self,account=1):
        with db() as con:return con.execute('SELECT revision FROM accounts WHERE id=?',(account,)).fetchone()[0]

    def cash(self,account,day,kind,amount,currency='USD',symbol=None):
        return add_cash_entry(CashEntry(account_id=account,entry_date=day,kind=kind,amount=amount,currency=currency,symbol=symbol),
                              self.revision(account),self.key(),False)['entry']

    def seed(self,linked=True):
        bank=save_account(Account(name='Salary',kind='bank',purpose='savings',currency='CNY'),None,0,self.key(),False)
        self.cash(1,'2025-01-02','balance_open',1000)
        self.cash(bank['id'],'2025-01-02','balance_open',7000,'CNY')
        self.cash(bank['id'],'2025-01-06','deposit',700,'CNY')
        trade=mutate_trade('add',Trade(symbol='AAA',trade_date='2025-01-03',shares=2,price=100,fee=1),None,'test')
        settlement=self.cash(1,'2025-01-06','withdraw',-201)
        self.cash(1,'2025-01-07','dividend',10,symbol='AAA')
        if linked:set_trade_settlement(trade['id'],settlement['id'],self.revision(),self.key(),False)
        return trade

    def fx(self,days,base='USD'):
        return {'base':'USD','source':'fixture','stale':False,'errors':[],
                'points':[{'day':day.isoformat(),'rates':{'USD':1,'CNY':7},'rate_dates':{'CNY':day.isoformat()}} for day in days]}

    def history(self,*args,**kwargs):
        return {'bars':BARS,'source':'fixture','stale':False,'errors':[],'warnings':[]}

    def test_complete_account_values_pending_settlement_dividend_and_salary(self):
        self.seed()
        with patch('apps.api.asset_history.get_history',side_effect=self.history),patch('apps.api.fx.get_historical_fx',side_effect=self.fx):
            result=asset_history('all','USD',actor='test')
            cny=asset_history('all','CNY',actor='test')
        self.assertEqual([p['value'] for p in result['series']],[2000,2019,2139,2139])
        self.assertEqual([p['unsettled'] for p in result['series']],[0,-201,0,0])
        self.assertEqual(result['summary']['net_external_flow'],100)
        self.assertEqual(result['summary']['gain'],39)
        expected=(2019/2000)*(2139/2119)-1
        self.assertAlmostEqual(result['summary']['twr'],expected)
        self.assertTrue(result['coverage']['performance_complete'])
        self.assertEqual(cny['currency'],'CNY')
        self.assertAlmostEqual(cny['summary']['end_value'],14973)
        self.assertAlmostEqual(cny['summary']['twr'],expected)
        self.assertEqual(result['end'],'2025-01-07')

    def test_no_settlement_fabrication_missing_quotes_and_fx_are_visible(self):
        trade=self.seed(linked=False)
        with patch('apps.api.asset_history.get_history',side_effect=self.history),patch('apps.api.fx.get_historical_fx',side_effect=self.fx):
            result=asset_history('all','USD',actor='test')
        self.assertEqual(result['coverage']['unmatched_trade_ids'],[trade['id']])
        self.assertIsNone(result['summary']['twr'])
        self.assertIsNone(result['summary']['max_drawdown'])
        self.assertTrue(result['warnings'])
        with patch('apps.api.asset_history.get_history',side_effect=RuntimeError('provider down')),patch('apps.api.fx.get_historical_fx',side_effect=RuntimeError('FX down')):
            result=asset_history('all','CNY',actor='test')
        self.assertTrue(all(p['value'] is None for p in result['series']))
        self.assertIsNone(result['summary']['twr'])
        self.assertIn('AAA',result['coverage']['missing_symbols'])
        self.assertTrue(result['coverage']['missing_fx'])

    def test_provider_io_uses_consistent_ledger_snapshot_and_releases_db(self):
        self.seed();writes=[]
        def fetch(*args,**kwargs):
            self.cash(1,'2025-01-07','deposit',50);writes.append(True)
            return self.history()
        with patch('apps.api.asset_history.get_history',side_effect=fetch),patch('apps.api.fx.get_historical_fx',side_effect=self.fx):
            result=asset_history('all','USD',actor='test')
        self.assertEqual(writes,[True])
        self.assertEqual(result['summary']['end_value'],2139)  # excludes concurrently committed deposit
        with patch('apps.api.asset_history.get_history',side_effect=self.history),patch('apps.api.fx.get_historical_fx',side_effect=self.fx):
            refreshed=asset_history('all','USD',actor='test')
        self.assertEqual(refreshed['summary']['end_value'],2189)

    def test_cash_only_window_needs_no_market_or_old_fx_data(self):
        self.cash(1,'2000-01-03','balance_open',1000)
        with patch('apps.api.asset_history.get_history') as history,patch('apps.api.fx.get_historical_fx') as fx:
            result=asset_history('1m','USD',actor='test')
        history.assert_not_called();fx.assert_not_called()
        self.assertTrue(all(p['value']==1000 for p in result['series']))
        self.assertEqual(result['summary']['twr'],0)
        self.assertEqual(result['summary']['max_drawdown'],0)

    def test_http_and_mcp_contract_auth_validation_no_writes(self):
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/assets/history').status_code,401)
            for params in [{'currency':'RMB'},{'period':'3y'}]:
                self.assertEqual(client.get('/api/assets/history',params=params,auth=('admin','test')).status_code,422)
            response=client.get('/api/assets/history',auth=('admin','test'))
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['series'],[])
            self.assertEqual(response.headers['cache-control'],'no-store')
        from packages.mcp.server import get_asset_history
        with patch.dict(os.environ,{'ACCUFOLIO_MCP_SCOPE':'read'}):
            self.assertEqual(get_asset_history('all','USD')['series'],[])
        with db() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM cash_entries').fetchone()[0],0)


if __name__=='__main__':unittest.main()
