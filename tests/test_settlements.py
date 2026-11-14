import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from apps.api.main import app, db, Trade, mutate_trade, delete_trade
from apps.api.accounts import CashEntry, add_cash_entry, reverse_cash_entry, Account, save_account
from apps.api.extended import undo, export
from apps.api.settlements import list_settlements, set_trade_settlement


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'ACCUFOLIO_DB':str(Path(self.temp.name)/'test.db'), 'ACCUFOLIO_PASSWORD':'test'})
        self.env.start(); self.sequence = 0
        self.cash(1000, 'balance_open', '2025-01-02')
        self.trade = self.buy()
        self.entry = self.cash(-201, 'withdraw')

    def tearDown(self):
        self.env.stop(); self.temp.cleanup()

    def key(self):
        self.sequence += 1
        return 'settlement-test-'+str(self.sequence)

    def revision(self, account=1):
        with db() as con:
            return con.execute('SELECT revision FROM accounts WHERE id=?',(account,)).fetchone()[0]

    def buy(self):
        return mutate_trade('add',Trade(symbol='AAA',trade_date='2025-01-03',shares=2,price=100,fee=1),None,'test')

    def cash(self,amount,kind,day='2025-01-06',account=1,symbol=None):
        return add_cash_entry(CashEntry(account_id=account,entry_date=day,kind=kind,amount=amount,currency='USD',symbol=symbol),
                              self.revision(account),self.key(),False)['entry']

    def link(self,entry=None,trade=None,**kwargs):
        return set_trade_settlement(trade or self.trade['id'],entry or self.entry['id'],self.revision(),self.key(),**kwargs)

    def test_preview_commit_idempotency_and_export(self):
        revision=self.revision()
        self.assertIn('preview',self.link())
        self.assertEqual(list_settlements(actor='test'),[])
        self.assertEqual(self.revision(),revision)
        key=self.key()
        result=set_trade_settlement(self.trade['id'],self.entry['id'],revision,key,False)
        self.assertEqual(result,set_trade_settlement(self.trade['id'],self.entry['id'],revision,key,False))
        self.assertEqual(self.revision(),revision+1)
        self.assertEqual(list_settlements(actor='test')[0]['cash_entry_id'],self.entry['id'])
        import json
        data=json.loads(export(actor='test').body)
        self.assertEqual(data['trade_settlements'][0]['trade_id'],self.trade['id'])
        with self.assertRaises(HTTPException):
            set_trade_settlement(self.trade['id'],None,revision,key,False)

    def test_rejects_amount_direction_account_symbol_and_predated_cash(self):
        candidates=[self.cash(-200,'withdraw'), self.cash(201,'deposit'),
                    self.cash(-201,'withdraw','2025-01-02'), self.cash(-201,'withdraw',symbol='BBB')]
        other=save_account(Account(name='Other',kind='broker',purpose='investing',currency='USD'),None,0,self.key(),False)
        self.cash(500,'balance_open','2025-01-02',other['id'])
        candidates.append(self.cash(-201,'withdraw',account=other['id']))
        for entry in candidates:
            with self.subTest(entry=entry),self.assertRaises(ValueError):self.link(entry=entry['id'])
        self.assertEqual(list_settlements(actor='test'),[])

    def test_same_cash_cannot_settle_two_trades_and_stale_revision_rejected(self):
        second=self.buy()
        old_revision=self.revision()
        self.link(dry_run=False)
        with self.assertRaises(HTTPException) as duplicate:self.link(trade=second['id'],dry_run=False)
        self.assertEqual(duplicate.exception.status_code,409)
        with self.assertRaises(HTTPException):
            set_trade_settlement(self.trade['id'],None,old_revision,self.key(),False)

    def test_link_protects_trade_edits_delete_undo_and_cash_reversal(self):
        self.link(dry_run=False)
        with self.assertRaises(HTTPException):
            mutate_trade('update',Trade(symbol='AAA',trade_date='2025-01-03',shares=2,price=100),self.trade['id'],'test')
        with self.assertRaises(HTTPException):delete_trade(self.trade['id'],actor='test')
        with self.assertRaises(HTTPException):undo(self.trade['audit_id'],actor='test')
        with self.assertRaises(HTTPException):
            reverse_cash_entry(self.entry['id'],'2025-01-07',self.revision(),self.key(),dry_run=False)
        set_trade_settlement(self.trade['id'],None,self.revision(),self.key(),False)
        reverse_cash_entry(self.entry['id'],'2025-01-07',self.revision(),self.key(),dry_run=False)
        with self.assertRaises(ValueError):self.link(dry_run=False)
        self.assertEqual(list_settlements(actor='test'),[])

    def test_sell_fees_can_require_cash_outflow_and_rounding_tolerance(self):
        sale=mutate_trade('add',Trade(symbol='AAA',trade_date='2025-01-07',shares=1,price=100,fee=101,side='sell'),None,'test')
        entry=self.cash(-1,'withdraw','2025-01-08')
        self.link(entry['id'],sale['id'],dry_run=False)
        rounded=mutate_trade('add',Trade(symbol='BBB',trade_date='2025-01-03',shares=1,price=10.004),None,'test')
        entry=self.cash(-10,'withdraw')
        self.link(entry['id'],rounded['id'],dry_run=False)

    def test_http_is_read_only_authenticated_and_mcp_write_scoped(self):
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/settlements').status_code,401)
            self.assertEqual(client.get('/api/settlements',auth=('admin','test')).json(),[])
            self.assertEqual(client.post('/api/settlements',auth=('admin','test')).status_code,405)
        from packages.mcp.server import set_trade_settlement as mcp_set
        with patch.dict(os.environ,{'ACCUFOLIO_MCP_SCOPE':'read'}):
            with self.assertRaises(PermissionError):mcp_set(self.trade['id'],self.entry['id'],self.revision(),self.key())
        with patch.dict(os.environ,{'ACCUFOLIO_MCP_SCOPE':'write'}):
            self.assertIn('preview',mcp_set(self.trade['id'],self.entry['id'],self.revision(),self.key()))


if __name__=='__main__':unittest.main()
