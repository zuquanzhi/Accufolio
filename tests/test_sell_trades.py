import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from apps.api.main import Trade, db, mutate_trade, summary
from apps.api.extended import undo
from packages.mcp.server import add_trade
from packages.providers import Quote, QuoteResult

class SellTradesTest(unittest.TestCase):
    def test_mcp_average_cost_and_undo(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"ACCUFOLIO_DB": str(Path(tmp)/"db"), "ACCUFOLIO_MCP_SCOPE": "write"}):
            add_trade('QQQM','2025-01-01',2,100,'buy1',dry_run=False)
            add_trade('QQQM','2025-02-01',2,200,'buy2',dry_run=False)
            sale=add_trade('QQQM','2025-03-01',1,220,'sell',dry_run=False,side='sell')
            with patch('apps.api.main.get_quote',return_value=QuoteResult(Quote('QQQM',180,0,'test',0),False,())):
                result=summary('QQQM',actor='test')
            self.assertEqual(result['shares'],3)
            self.assertEqual(result['cost'],450)
            self.assertEqual(result['realized_pnl'],70)
            self.assertEqual(result['position']['pnl'],90)
            self.assertIsNotNone(result['xirr'])
            self.assertIsNone(result['xirr_reason'])
            with self.assertRaises(HTTPException):
                add_trade('QQQM','2025-02-15',5,220,'oversell',dry_run=False,side='sell')
            # Undoing the second buy would make a later sale invalid.
            more=add_trade('QQQM','2025-04-01',3,220,'sell2',dry_run=False,side='sell')
            with self.assertRaises(HTTPException): mutate_trade('update',Trade(symbol='QQQM',trade_date='2026-01-01',shares=2,price=100),1,'test')
            with self.assertRaises(HTTPException): mutate_trade('delete',None,1,'test')
            with self.assertRaises(HTTPException): undo(sale['audit_id']-1,actor='test')
            undo(more['audit_id'],actor='test')
            undo(sale['audit_id'],actor='test')
            with db() as con: self.assertEqual(con.execute("SELECT count(*) FROM trades WHERE deleted=0").fetchone()[0],2)

    def test_account_cost_basis_fees_preview_and_cross_account_sell(self):
        from packages.core.dashboard import summarize_account, reconstruct
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"ACCUFOLIO_DB": str(Path(tmp)/"db"), "ACCUFOLIO_MCP_SCOPE":"write"}):
            with db() as con:
                con.execute("INSERT INTO accounts(id,name,kind,purpose,currency) VALUES(2,'Second broker','broker','investing','USD')")
            add_trade('QQQM','2025-01-02',2,100,'buy1',dry_run=False,fee=2,strategy='dca')
            add_trade('QQQM','2025-01-03',2,200,'buy2',dry_run=False,account_id=2,fee=4)
            preview = add_trade('QQQM','2025-01-06',1,220,'sale',side='sell',fee=1)
            self.assertEqual(preview['preview']['after']['fee'],1)
            with db() as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM trades').fetchone()[0],2)
                self.assertIsNone(con.execute("SELECT 1 FROM requests WHERE key='sale'").fetchone())
            add_trade('QQQM','2025-01-06',1,220,'sale',side='sell',fee=1,dry_run=False)
            with self.assertRaises(HTTPException):
                # The other account's two shares cannot cover this sale, even in preview.
                add_trade('QQQM','2025-01-07',2,220,'invalid',side='sell')
            with db() as con:
                rows = [dict(row) for row in con.execute('SELECT * FROM trades')]
            quote = Quote('QQQM',180,0,'test',0)
            with patch('apps.api.main.get_quote',return_value=QuoteResult(quote,False,())):
                result=summary('QQQM',actor='test')
            account=summarize_account(rows,{'QQQM':quote.__dict__},'2025-06-01')
            self.assertEqual(result['shares'],3)
            self.assertEqual(result['cost'],505)  # account1 101 + account2 404
            self.assertEqual(result['fees'],7)
            self.assertEqual(result['realized_pnl'],118)  # 220-1-101
            self.assertEqual(result['position']['pnl'],35)
            self.assertEqual(account['summary']['total_pnl'],153)  # 540-(202+404-219)
            self.assertEqual(account['summary']['cost'],result['cost'])
            history=reconstruct(rows,{'QQQM':[{'day':'2025-01-06','close':180}]},'2025-01-06','2025-01-06')
            self.assertEqual(history['series'][0]['net_invested'],387)
            self.assertEqual(history['series'][0]['pnl'],153)

    def test_trade_account_constraints_and_undo_cannot_restore_archived_account(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"ACCUFOLIO_DB": str(Path(tmp)/"db")}):
            trade=Trade(symbol='QQQM',trade_date='2025-01-02',shares=1,price=100)
            first=mutate_trade('add',trade,None,'test')
            deleted=mutate_trade('delete',None,first['id'],'test')
            with db() as con:
                con.execute('UPDATE accounts SET archived=1 WHERE id=1')
            with self.assertRaises(HTTPException):undo(deleted['audit_id'],actor='test')
            with self.assertRaises(HTTPException):mutate_trade('add',trade,None,'test',dry_run=True)
            with db() as con:
                self.assertEqual(con.execute('SELECT deleted FROM trades').fetchone()[0],1)
                self.assertEqual(con.execute('SELECT COUNT(*) FROM reversals').fetchone()[0],0)

if __name__ == '__main__': unittest.main()
