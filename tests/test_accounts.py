import os
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from apps.api.accounts import Account, CashEntry, save_account, add_cash_entry, transfer_cash, reverse_cash_entry, list_accounts, assets, router
from packages.core.schema_v2 import initialize
from packages.providers import Quote, QuoteResult


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'accounts.db'
        self.env=patch.dict(os.environ,{'ACCUFOLIO_DB':str(self.path),'ACCUFOLIO_PASSWORD':'account-test'})
        self.env.start();self.sequence=0
        app=FastAPI();app.include_router(router);self.client=TestClient(app)

    def tearDown(self):
        self.client.close();self.env.stop();self.tmp.cleanup()

    def key(self):
        self.sequence+=1
        return 'accounts-test-'+str(self.sequence)

    def account(self,name='Daily funds',currency='USD'):
        return save_account(Account(name=name,kind='bank',purpose='savings',currency=currency),None,0,self.key(),dry_run=False)

    def cash(self,account,amount,kind='balance_open',day='2026-01-05',key=None,dry_run=False):
        return add_cash_entry(CashEntry(account_id=account['id'],entry_date=day,kind=kind,amount=amount,currency=account['currency']),account['revision'],key or self.key(),dry_run=dry_run)

    def test_schema_idempotent_without_personal_balances(self):
        with closing(sqlite3.connect(self.path)) as con, con:
            con.row_factory=sqlite3.Row;initialize(con);initialize(con)
            self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0],2)
            self.assertEqual(con.execute('SELECT count(*) FROM accounts').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT count(*) FROM cash_entries').fetchone()[0],0)
            self.assertEqual(con.execute('SELECT count(*) FROM fx_rates').fetchone()[0],0)
        self.assertEqual(list_accounts(actor='test')[0]['balance'],0)

    def test_legacy_database_rejected_without_altering(self):
        with closing(sqlite3.connect(self.path)) as con, con:
            con.row_factory=sqlite3.Row;con.execute('CREATE TABLE trades(id INTEGER PRIMARY KEY,symbol TEXT)')
            con.execute("INSERT INTO trades VALUES(1,'AAA')")
            with self.assertRaisesRegex(RuntimeError,'schema v2'):initialize(con)
            self.assertEqual([r[1] for r in con.execute('PRAGMA table_info(trades)')],['id','symbol'])
            self.assertEqual(con.execute('SELECT symbol FROM trades').fetchone()[0],'AAA')
            self.assertIsNone(con.execute("SELECT name FROM sqlite_master WHERE name='accounts'").fetchone())

    def test_account_preview_update_revision_currency_archive(self):
        model=Account(name='  Account test  ',kind='bank',purpose='emergency',currency='HKD')
        preview=save_account(model,None,0,self.key())
        self.assertEqual(preview['preview']['name'],'Account test');self.assertEqual(len(list_accounts(actor='test')),1)
        created=save_account(model,None,0,self.key(),dry_run=False);funded=self.cash(created,100)['account']
        with self.assertRaises(HTTPException):save_account(model,created['id'],0,self.key(),False)
        with self.assertRaisesRegex(ValueError,'currency'):
            save_account(model.model_copy(update={'currency':'USD'}),created['id'],funded['revision'],self.key(),False)
        with self.assertRaisesRegex(ValueError,'cash balance'):
            save_account(model.model_copy(update={'archived':True}),created['id'],funded['revision'],self.key(),False)
        renamed=save_account(model.model_copy(update={'name':'Savings'}),created['id'],funded['revision'],self.key(),False)
        self.assertEqual(renamed['revision'],funded['revision']+1)

    def test_cash_preview_commit_idempotency_and_audit(self):
        account=self.account();key=self.key();preview=self.cash(account,100,key=key,dry_run=True)
        self.assertEqual(preview['balance_after'],100);self.assertEqual(list_accounts(actor='test')[1]['balance'],0)
        first=self.cash(account,100,key=key);self.assertEqual(first,self.cash(account,100,key=key))
        self.assertEqual(first['account']['balance'],100)
        with self.assertRaises(HTTPException):self.cash(account,99,key=key)
        with closing(sqlite3.connect(self.path)) as con, con:
            self.assertEqual(con.execute('SELECT count(*) FROM cash_entries').fetchone()[0],1)
            self.assertEqual(con.execute("SELECT count(*) FROM audit WHERE action='add_cash_entry'").fetchone()[0],1)

    def test_signed_entries_currency_opening_and_historical_balance(self):
        account=self.account()
        for amount,kind in [(-1,'deposit'),(1,'withdraw'),(0,'interest'),(1,'fee')]:
            with self.subTest(amount=amount,kind=kind),self.assertRaises(ValueError):self.cash(account,amount,kind)
        with self.assertRaisesRegex(ValueError,'currency'):
            add_cash_entry(CashEntry(account_id=account['id'],entry_date='2026-01-05',kind='deposit',amount=10,currency='CNY'),0,self.key(),False)
        account=self.cash(account,100)['account']
        with self.assertRaisesRegex(ValueError,'first'):self.cash(account,10)
        with self.assertRaisesRegex(ValueError,'negative'):self.cash(account,-150,'withdraw')
        with self.assertRaisesRegex(ValueError,'negative'):self.cash(account,-10,'withdraw',day='2026-01-04')
        self.assertEqual(self.cash(account,-40,'withdraw',day='2026-01-06')['account']['balance'],60)

    def test_atomic_cross_currency_transfer_and_replay(self):
        source=self.cash(self.account('USD','USD'),100)['account'];destination=self.account('HKD','HKD');key=self.key()
        args=(source['id'],destination['id'],10,78,'2026-01-06',source['revision'],destination['revision'],key)
        self.assertEqual(len(transfer_cash(*args)['preview']),2)
        result=transfer_cash(*args,dry_run=False);self.assertEqual(result,transfer_cash(*args,dry_run=False))
        self.assertEqual([a['balance'] for a in result['accounts']],[90,78])
        outgoing,incoming=result['entries'];self.assertEqual(outgoing['transfer_group'],incoming['transfer_group'])
        self.assertAlmostEqual(outgoing['fx_rate'],7.8);self.assertAlmostEqual(incoming['fx_rate'],1/7.8)
        self.assertEqual(outgoing['kind'],'fx_out');self.assertEqual(incoming['kind'],'fx_in')
        with self.assertRaisesRegex(ValueError,'negative'):
            transfer_cash(source['id'],destination['id'],1000,7800,'2026-01-07',result['accounts'][0]['revision'],result['accounts'][1]['revision'],self.key(),dry_run=False)
        with closing(sqlite3.connect(self.path)) as con, con:self.assertEqual(con.execute('SELECT count(*) FROM cash_entries').fetchone()[0],3)

    def test_standalone_transfer_denied_and_same_currency_conserved(self):
        account=self.cash(self.account('source'),100)['account'];destination=self.account('destination')
        with self.assertRaisesRegex(ValueError,'atomically'):
            add_cash_entry(CashEntry(account_id=account['id'],entry_date='2026-01-06',kind='transfer_out',amount=-10,currency='USD',counter_account_id=destination['id']),account['revision'],self.key(),False)
        with self.assertRaisesRegex(ValueError,'match'):
            transfer_cash(account['id'],destination['id'],10,9,'2026-01-06',account['revision'],0,self.key(),dry_run=False)
        result=transfer_cash(account['id'],destination['id'],10,10,'2026-01-06',account['revision'],0,self.key(),dry_run=False)
        self.assertEqual(sum(a['balance'] for a in result['accounts']),100)

    def test_reversal_append_only(self):
        account=self.cash(self.account(),100)['account'];withdrawal=self.cash(account,-20,'withdraw',day='2026-01-06')
        result=reverse_cash_entry(withdrawal['entry']['id'],'2026-01-07',withdrawal['account']['revision'],self.key(),dry_run=False)
        self.assertEqual(result['account']['balance'],100);self.assertEqual(result['entry']['reverses_entry_id'],withdrawal['entry']['id'])
        with self.assertRaisesRegex(ValueError,'reversed'):
            reverse_cash_entry(withdrawal['entry']['id'],'2026-01-07',result['account']['revision'],self.key(),dry_run=False)
        response=self.client.get('/api/accounts/'+str(account['id'])+'/cash-entries',auth=('admin','account-test'))
        self.assertEqual(len(response.json()),3)

    def test_assets_sum_native_cash_and_securities_once_missing_fx_unknown(self):
        self.cash(self.account('HKD','HKD'),780)
        with closing(sqlite3.connect(self.path)) as con, con:con.execute("INSERT INTO trades(account_id,symbol,trade_date,shares,price) VALUES(1,'AAA','2026-01-05',3,100)")
        quote=QuoteResult(Quote('AAA',120,1,'test',0),False,())
        rates={'base':'USD','rates':{'USD':1,'HKD':7.8},'as_of':'2026-11-13','source':'test','available':True,'stale':False}
        with patch('packages.providers.get_quote',return_value=quote),patch('apps.api.fx.get_fx',return_value=rates):result=assets(actor='test')
        self.assertEqual(result['securities_value_usd'],360);self.assertEqual(result['cash_value_usd'],100)
        self.assertEqual(result['net_assets_usd'],460);self.assertEqual(result['accounts'][1]['balance'],780)
        with patch('packages.providers.get_quote',return_value=quote),patch('apps.api.fx.get_fx',return_value={**rates,'rates':{'USD':1},'available':False}):result=assets(actor='test')
        self.assertFalse(result['complete']);self.assertIsNone(result['net_assets_usd'])
        self.assertEqual(result['missing_fx'],['HKD']);self.assertEqual(result['accounts'][0]['value_usd'],360)

    def test_http_read_only_auth(self):
        self.assertEqual(self.client.get('/api/accounts').status_code,401)
        for path in ['/api/accounts','/api/assets','/api/accounts/1/cash-entries']:
            for method in ['post','put','patch','delete']:
                self.assertEqual(getattr(self.client,method)(path,auth=('admin','account-test')).status_code,405)
        self.assertEqual(self.client.get('/api/accounts',auth=('admin','account-test')).status_code,200)

    def test_cash_journal_expected_revision_is_optional_and_non_negative(self):
        from apps.api.accounts import list_cash_entries
        account=self.cash(self.account(),100)['account']
        url='/api/accounts/'+str(account['id'])+'/cash-entries'
        auth=('admin','account-test')
        matching=self.client.get(url,params={'expected_revision':account['revision']},auth=auth)
        self.assertEqual(matching.status_code,200)
        self.assertEqual(len(matching.json()),1)
        self.assertEqual(matching.json(),self.client.get(url,auth=auth).json())
        self.assertEqual(matching.json(),list_cash_entries(account['id'],actor='mcp'))
        stale=self.client.get(url,params={'expected_revision':account['revision']-1},auth=auth)
        self.assertEqual(stale.status_code,409)
        self.assertEqual(stale.json()['detail'],'账户数据已更新，请刷新重试')
        for invalid in ['-1','invalid','1.5']:
            with self.subTest(revision=invalid):
                self.assertEqual(self.client.get(url,params={'expected_revision':invalid},auth=auth).status_code,422)

    def test_cash_journal_revision_and_entries_share_one_snapshot(self):
        from apps.api.accounts import _account
        account=self.cash(self.account(),100)['account']
        with closing(sqlite3.connect(self.path)) as con:
            con.execute('PRAGMA journal_mode=WAL')
        committed=[]
        def read_then_commit(con,account_id,*args,**kwargs):
            original=_account(con,account_id,*args,**kwargs)
            # Commit an actual separate writer after the revision SELECT but
            # before the journal SELECT. WAL allows this without blocking.
            with closing(sqlite3.connect(self.path,timeout=.1)) as writer,writer:
                writer.execute("INSERT INTO cash_entries(account_id,entry_date,kind,amount,currency) VALUES(?,'2026-01-06','deposit',50,'USD')",(account_id,))
                writer.execute('UPDATE accounts SET revision=revision+1 WHERE id=?',(account_id,))
            committed.append(True)
            return original
        url='/api/accounts/'+str(account['id'])+'/cash-entries'
        with patch('apps.api.accounts._account',side_effect=read_then_commit):
            response=self.client.get(url,params={'expected_revision':account['revision']},auth=('admin','account-test'))
        self.assertEqual(committed,[True])
        self.assertEqual(response.status_code,200)
        self.assertEqual([row['amount'] for row in response.json()],[100])
        self.assertEqual(self.client.get(url,params={'expected_revision':account['revision']},auth=('admin','account-test')).status_code,409)
        self.assertEqual(len(self.client.get(url,auth=('admin','account-test')).json()),2)

    def test_zero_cash_does_not_require_fx(self):
        self.account('Empty foreign account','JPY')
        rates={'base':'USD','rates':{'USD':1},'as_of':None,'source':'test','available':False,'stale':False}
        with patch('apps.api.fx.get_fx',return_value=rates):result=assets(actor='test')
        self.assertTrue(result['complete']);self.assertEqual(result['net_assets_usd'],0)
        self.assertEqual(result['missing_fx'],[]);self.assertIsNone(result['accounts'][1]['fx_rate_to_usd'])

    def test_concurrent_first_initializers_and_following_writes(self):
        barrier=threading.Barrier(6)
        def first_start(index):
            with closing(sqlite3.connect(self.path,timeout=5)) as con:
                con.row_factory=sqlite3.Row
                barrier.wait(timeout=5);initialize(con)
                con.execute('BEGIN IMMEDIATE')
                con.execute('INSERT INTO audit(actor,action,details) VALUES(?,?,?)',('test','concurrent-start',str(index)))
                con.commit()
        with ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(first_start,range(6)))
        with closing(sqlite3.connect(self.path)) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM accounts').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT count(*) FROM audit').fetchone()[0],6)

    def test_initialized_read_does_not_request_writer_lock(self):
        with closing(sqlite3.connect(self.path)) as writer, closing(sqlite3.connect(self.path,timeout=.1)) as reader:
            writer.row_factory=reader.row_factory=sqlite3.Row
            initialize(writer);writer.execute('BEGIN IMMEDIATE')
            writer.execute("INSERT INTO audit(actor,action,details) VALUES('test','in-progress','{}')")
            initialize(reader)
            self.assertFalse(reader.in_transaction)
            self.assertEqual(reader.execute('SELECT count(*) FROM accounts').fetchone()[0],1)
            writer.rollback()

    def test_first_initializer_commits_during_readiness_check(self):
        # Force the precise interleaving: an empty table list is fetched, then
        # another process commits the complete schema before inspection resumes.
        with closing(sqlite3.connect(self.path)) as reader, closing(sqlite3.connect(self.path)) as writer:
            class InterleavedConnection:
                fired = False
                def __getattr__(self, key):
                    return getattr(reader, key)
                def execute(self, sql, *args):
                    cursor = reader.execute(sql, *args)
                    if 'SELECT name FROM sqlite_master' in sql and not self.fired:
                        self.fired = True
                        rows = cursor.fetchall()
                        initialize(writer)
                        return iter(rows)
                    return cursor
            initialize(InterleavedConnection())
            self.assertEqual(reader.execute('PRAGMA user_version').fetchone()[0],2)
            self.assertEqual(reader.execute('SELECT count(*) FROM accounts').fetchone()[0],1)

    def test_account_opening_date_cannot_contradict_existing_history(self):
        account=self.cash(self.account(),100)['account']
        model=Account(name=account['name'],kind='bank',purpose='savings',currency='USD',opened_on='2026-02-01')
        with self.assertRaisesRegex(ValueError,'recorded history'):
            save_account(model,account['id'],account['revision'],self.key(),False)
        with self.assertRaisesRegex(ValueError,'positive integer'):
            save_account(model,0,0,self.key(),False)

    def test_boolean_money_and_ambiguous_transfer_metadata_rejected(self):
        account=self.account()
        with self.assertRaises(ValueError):
            CashEntry(account_id=account['id'],entry_date='2026-01-01',kind='deposit',amount=True,currency='USD')
        for metadata in [{'counter_account_id':1},{'fx_rate':7.2}]:
            with self.subTest(metadata=metadata),self.assertRaisesRegex(ValueError,'paired transfer'):
                add_cash_entry(CashEntry(account_id=account['id'],entry_date='2026-01-01',kind='deposit',amount=10,currency='USD',**metadata),0,self.key(),False)

    def test_quote_fallback_warnings_and_timestamp_do_not_leak_errors(self):
        list_accounts(actor='test')
        with closing(sqlite3.connect(self.path)) as con,con:
            con.execute("INSERT INTO trades(symbol,trade_date,shares,price) VALUES('AAA','2026-01-05',1,100)")
        quote=QuoteResult(Quote('AAA',120,1,'tencent',1794592259,1794592264),True,('https://private.example/token=SECRET',))
        with patch('packages.providers.get_quote',return_value=quote):result=assets(actor='test')
        self.assertEqual(result['net_assets_usd'],120);self.assertTrue(result['complete'])
        self.assertTrue(any('备用' in w for w in result['warnings']))
        self.assertEqual(result['quote_sources'][0]['source'],'tencent')
        self.assertEqual(result['quote_sources'][0]['quote_timestamp'],1794592259)
        self.assertEqual(result['accounts'][0]['quote_timestamp_min'],1794592259)
        self.assertNotIn('SECRET',json.dumps(result))
        with patch('packages.providers.get_quote',side_effect=RuntimeError('private SECRET')):failed=assets(actor='test')
        self.assertIsNone(failed['net_assets_usd']);self.assertEqual(failed['missing_quotes'],['AAA'])
        self.assertNotIn('SECRET',json.dumps(failed))

    def test_fx_exception_keeps_known_usd_amounts_visible(self):
        self.cash(self.account('Foreign','HKD'),780)
        self.cash(self.account('USD savings','USD'),30)
        with patch('apps.api.fx.get_fx',side_effect=RuntimeError('private SECRET')):result=assets(actor='test')
        self.assertFalse(result['complete']);self.assertEqual(result['missing_fx'],['HKD'])
        self.assertEqual(result['accounts'][-1]['value_usd'],30)
        self.assertNotIn('SECRET',json.dumps(result))

    def test_closed_positions_skip_providers_and_fees_do_not_move_cash(self):
        account=list_accounts(actor='test')[0]
        self.cash(account,50)
        with closing(sqlite3.connect(self.path)) as con,con:
            con.execute("INSERT INTO trades(symbol,trade_date,shares,price,fee,side) VALUES('AAA','2026-01-05',2,100,2,'buy')")
            con.execute("INSERT INTO trades(symbol,trade_date,shares,price,fee,side) VALUES('AAA','2026-02-05',2,130,1,'sell')")
        with patch('packages.providers.get_quote') as quote:result=assets(actor='test')
        quote.assert_not_called()
        self.assertEqual(result['securities_value_usd'],0);self.assertEqual(result['net_assets_usd'],50)
        self.assertEqual(result['accounts'][0]['balance'],50)

    def test_same_stock_multiple_accounts_and_provider_io_outside_database(self):
        second=save_account(Account(name='Second broker',kind='broker',purpose='investing',currency='USD'),None,0,self.key(),False)
        with closing(sqlite3.connect(self.path)) as con,con:
            con.execute("INSERT INTO trades(account_id,symbol,trade_date,shares,price,fee,side) VALUES(1,'AAA','2026-01-05',2,100,2,'buy')")
            con.execute("INSERT INTO trades(account_id,symbol,trade_date,shares,price,fee,side) VALUES(1,'AAA','2026-02-05',1,130,1,'sell')")
            con.execute("INSERT INTO trades(account_id,symbol,trade_date,shares,price,fee,side) VALUES(?,'AAA','2026-01-05',1,200,2,'buy')",(second['id'],))
        def fetch(symbol):
            with closing(sqlite3.connect(self.path,timeout=.1)) as con,con:
                con.execute('BEGIN IMMEDIATE')
                con.execute("INSERT INTO audit(actor,action,details) VALUES('test','provider-io','{}')")
            return QuoteResult(Quote(symbol,120,1,'test',0),False,())
        with patch('packages.providers.get_quote',side_effect=fetch) as quote:result=assets(actor='test')
        quote.assert_called_once_with('AAA')
        self.assertEqual(result['securities_value_usd'],240)
        self.assertEqual([a['securities_value_usd'] for a in result['accounts']],[120,120])
        self.assertEqual(result['cash_value_usd'],0)


if __name__=='__main__':unittest.main()
