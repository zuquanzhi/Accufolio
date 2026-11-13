import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from packages.mcp.server import mcp, list_trades
from apps.api.main import Trade, add_trade

class McpTests(unittest.TestCase):
    def test_tool_registration_and_local_reads(self):
        names = {tool.name for tool in asyncio.run(mcp.list_tools())}
        self.assertTrue({'get_summary','list_trades','get_market_quote','add_trade','undo_trade_change'}.issubset(names))
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'ACCUFOLIO_DB': str(Path(tmp)/'test.db')}):
            add_trade(Trade(symbol='qqqm', trade_date='2024-01-01', shares=1, price=100), actor='test')
            self.assertEqual(len(list_trades('qqqm')), 1)
            self.assertEqual(list_trades('other'), [])

    def test_write_scope_and_retry(self):
        from packages.mcp.server import add_trade as write_trade
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'ACCUFOLIO_DB': str(Path(tmp)/'test.db'), 'ACCUFOLIO_MCP_SCOPE':'read'}):
            with self.assertRaises(PermissionError): write_trade('QQQM','2025-01-01',1,100,'key')
            with patch.dict(os.environ,{'ACCUFOLIO_MCP_SCOPE':'write'}):
                self.assertIn('preview',write_trade('QQQM','2025-01-01',1,100,'key'))
                self.assertEqual(list_trades(),[])
                first=write_trade('QQQM','2025-01-01',1,100,'key',dry_run=False)
                self.assertEqual(first,write_trade('QQQM','2025-01-01',1,100,'key',dry_run=False))
                self.assertEqual(len(list_trades()),1)
