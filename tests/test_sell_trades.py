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
    def test_legacy_audit_undo_after_side_migration(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"ACCUFOLIO_DB": str(Path(tmp)/"db")}):
            created=mutate_trade('add',Trade(symbol='QQQM',trade_date='2025-01-01',shares=1,price=100),None,'test')
            with db() as con:
                row=con.execute('SELECT details FROM audit WHERE id=?',(created['audit_id'],)).fetchone()
                details=json.loads(row['details']); details['after'].pop('side')
                con.execute('UPDATE audit SET details=? WHERE id=?',(json.dumps(details),created['audit_id']))
            self.assertTrue(undo(created['audit_id'],actor='test')['ok'])

    def test_migration_mcp_average_cost_and_undo(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"ACCUFOLIO_DB": str(Path(tmp)/"db"), "ACCUFOLIO_MCP_SCOPE": "write"}):
            con=sqlite3.connect(Path(tmp)/"db")
            con.execute("CREATE TABLE trades(id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, trade_date TEXT NOT NULL, shares REAL NOT NULL, price REAL NOT NULL, note TEXT NOT NULL DEFAULT '')")
            con.execute("INSERT INTO trades VALUES (1,'QQQM','2025-01-01',2,100,'')")
            con.commit(); con.close()
            with db() as con: self.assertEqual(con.execute('SELECT side FROM trades').fetchone()[0], 'buy')
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

if __name__ == '__main__': unittest.main()
