import asyncio
from datetime import date, timedelta
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from apps.api.main import app, db
from apps.api.allocation import AllocationPlan, allocation, save_plan
from packages.mcp.server import add_trade, get_allocation_plan, mcp, set_allocation_plan


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            'ACCUFOLIO_DB': str(Path(self.tmp.name) / 'allocation.db'),
            'ACCUFOLIO_USER': 'admin', 'ACCUFOLIO_PASSWORD': 'test-password',
            'ACCUFOLIO_MCP_SCOPE': 'write', 'ACCUFOLIO_WEBHOOK_URL': '',
        })
        self.env.start()
        self.client = TestClient(app)
        self.auth = ('admin', 'test-password')

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.tmp.cleanup()

    def save(self, revision=0, key='plan', **kwargs):
        return set_allocation_plan(revision, key, dry_run=False, **kwargs)

    def count(self, table):
        with db() as con:
            return con.execute(f'SELECT count(*) FROM {table}').fetchone()[0]

    def test_http_read_requires_auth_and_has_no_write_methods(self):
        self.assertEqual(self.client.get('/api/allocation').status_code, 401)
        self.assertEqual(self.client.get('/api/allocation', auth=('admin', 'wrong')).status_code, 401)
        result = self.client.get('/api/allocation', auth=self.auth)
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.json()['reserve_amount'])
        self.assertEqual(result.headers['cache-control'], 'no-store')
        for method in ('post', 'put', 'patch', 'delete'):
            response = self.client.request(method, '/api/allocation', auth=self.auth,
                                           headers={'X-Accufolio': '1'}, json={'reserve_amount': 100})
            self.assertEqual(response.status_code, 405)
        self.assertEqual(self.count('allocation_plan'), 0)

    def test_other_funds_key_does_not_collide_with_stock_symbols(self):
        plan = AllocationPlan(investment_targets={'CASH': 100},
                              total_targets={'CASH': 60, '__RESERVE__': 40})
        self.assertEqual(plan.investment_targets, {'CASH': 100})
        self.assertEqual(plan.total_targets['__RESERVE__'], 40)
        with self.assertRaises(ValueError):
            AllocationPlan(investment_targets={'__RESERVE__': 100})

    def test_tools_registered_and_default_scope_cannot_write_or_preview(self):
        names = {tool.name for tool in asyncio.run(mcp.list_tools())}
        self.assertTrue({'get_allocation_plan', 'set_allocation_plan', 'get_dashboard'}.issubset(names))
        with patch.dict(os.environ, {'ACCUFOLIO_MCP_SCOPE': 'read'}):
            self.assertEqual(get_allocation_plan()['revision'], 0)
            for dry_run in (True, False):
                with self.assertRaises(PermissionError):
                    set_allocation_plan(0, 'blocked', dry_run=dry_run)
        self.assertEqual(self.count('allocation_plan'), 0)

    def test_dry_run_does_not_write_a_plan_audit_or_idempotency_record(self):
        preview = set_allocation_plan(0, 'preview', reserve_amount=1000,
                    reserve_as_of='2025-01-01', investment_targets={' qqqm ': 100},
                    total_targets={'qqqm': 75, '__reserve__': 25})
        self.assertEqual(preview['preview']['investment_targets'], {'QQQM': 100})
        self.assertEqual(preview['preview']['total_targets'], {'QQQM': 75, '__RESERVE__': 25})
        self.assertEqual(get_allocation_plan()['revision'], 0)
        for table in ('allocation_plan', 'audit', 'requests'):
            self.assertEqual(self.count(table), 0)

    def test_unknown_reserve_is_distinct_from_explicit_zero_and_can_be_cleared(self):
        self.assertIsNone(get_allocation_plan()['reserve_amount'])
        first = self.save(reserve_amount=0, reserve_as_of='2025-01-01')
        self.assertEqual(first['reserve_amount'], 0)
        self.assertEqual(first['reserve_as_of'], '2025-01-01')
        self.assertEqual(first['revision'], 1)
        self.assertIsNotNone(first['updated_at'])
        cleared = self.save(1, 'clear')
        self.assertIsNone(cleared['reserve_amount'])
        self.assertIsNone(cleared['reserve_as_of'])
        self.assertEqual(cleared['revision'], 2)

    def test_replacement_is_audited_and_not_changed_by_trade_writes(self):
        first = self.save(reserve_amount=1000, reserve_as_of='2025-01-01',
                          investment_targets={'QQQM': 100}, note='reported balance')
        add_trade('QQQM', '2025-01-02', 1, 100, 'trade', dry_run=False)
        self.assertEqual(get_allocation_plan(), first)
        second = self.save(1, 'replace', total_targets={'QQQM': 70, '__RESERVE__': 30})
        self.assertEqual(second['investment_targets'], {})
        self.assertIsNone(second['reserve_amount'])
        with db() as con:
            audits = list(con.execute("SELECT actor,details FROM audit WHERE action='allocation' ORDER BY id"))
        self.assertEqual(len(audits), 2)
        self.assertEqual(audits[1]['actor'], 'mcp')
        details = json.loads(audits[1]['details'])
        self.assertEqual(details['before'], first)
        self.assertEqual(details['after'], second)

    def test_retry_returns_original_result_even_after_new_revision(self):
        first = self.save(investment_targets={'QQQM': 100})
        self.assertEqual(self.save(investment_targets={'qqqm': 100}), first)
        second = self.save(1, 'next', total_targets={'QQQM': 100})
        self.assertEqual(self.save(investment_targets={'QQQM': 100}), first)
        self.assertEqual(get_allocation_plan(), second)
        self.assertEqual(self.count('audit'), 2)
        self.assertEqual(self.count('requests'), 2)

    def test_same_key_different_payload_or_revision_rejected(self):
        self.save(investment_targets={'QQQM': 100})
        for kwargs in ({'revision': 0, 'investment_targets': {'AAPL': 100}},
                       {'revision': 1, 'investment_targets': {'QQQM': 100}}):
            with self.assertRaises(HTTPException) as caught:
                self.save(**kwargs)
            self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.count('audit'), 1)

    def test_stale_revision_rejected_for_preview_and_commit(self):
        self.save()
        with self.assertRaises(ValueError):
            set_allocation_plan(0, 'preview')
        with self.assertRaises(HTTPException) as caught:
            self.save(0, 'stale')
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.count('audit'), 1)

    def test_trade_and_allocation_share_idempotency_namespace(self):
        add_trade('QQQM', '2025-01-01', 1, 100, 'trade-key', dry_run=False)
        with self.assertRaises(HTTPException) as caught:
            self.save(key='trade-key')
        self.assertEqual(caught.exception.status_code, 409)
        self.save(key='allocation-key')
        with self.assertRaises(HTTPException) as caught:
            add_trade('QQQM', '2025-01-02', 1, 100, 'allocation-key', dry_run=False)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.count('trades'), 1)
        self.assertEqual(self.count('allocation_plan'), 1)

    def test_invalid_amounts_dates_and_percent_targets_rejected_on_preview_and_write(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        cases = [
            {'reserve_amount': -1, 'reserve_as_of': '2025-01-01'},
            {'reserve_amount': float('nan'), 'reserve_as_of': '2025-01-01'},
            {'reserve_amount': float('inf'), 'reserve_as_of': '2025-01-01'},
            {'reserve_amount': 0}, {'reserve_amount': 0, 'reserve_as_of': tomorrow},
            {'reserve_amount': 0, 'reserve_as_of': 'invalid'}, {'reserve_as_of': '2025-01-01'},
            {'investment_targets': {'QQQM': 99}}, {'investment_targets': {'QQQM': -1, 'AAPL': 101}},
            {'investment_targets': {'QQQM': float('nan')}},
            {'investment_targets': {'__RESERVE__': 100}}, {'investment_targets': {'QQQM&bad': 100}},
            {'investment_targets': {'qqqm': 50, ' QQQM ': 50}},
            {'total_targets': {'__RESERVE__': 101}}, {'note': 'a' * 501},
        ]
        for kwargs in cases:
            for dry_run in (True, False):
                with self.subTest(kwargs=kwargs, dry_run=dry_run), self.assertRaises(ValueError):
                    set_allocation_plan(0, 'invalid', dry_run=dry_run, **kwargs)
        self.assertEqual(get_allocation_plan()['revision'], 0)
        self.assertEqual(self.count('audit'), 0)

    def test_invalid_revision_and_keys_rejected_consistently(self):
        for revision in (-1, 1.0, True, '0'):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                set_allocation_plan(revision, 'key')
            with self.assertRaises(ValueError):
                save_plan(AllocationPlan(), revision, 'key')
        for key in ('', '  ', 'a' * 201, None):
            with self.subTest(key=key), self.assertRaises(ValueError):
                set_allocation_plan(0, key)
        self.assertEqual(get_allocation_plan()['revision'], 0)

    def test_concurrent_writes_only_one_revision_wins(self):
        get_allocation_plan()
        start = threading.Barrier(2)
        outcomes = []
        def write(key):
            start.wait(timeout=5)
            try:
                outcomes.append(save_plan(AllocationPlan(note=key), 0, key))
            except HTTPException as error:
                outcomes.append(error.status_code)
        threads = [threading.Thread(target=write, args=(key,)) for key in ('one', 'two')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sum(isinstance(result, dict) for result in outcomes), 1)
        self.assertIn(409, outcomes)
        self.assertEqual(get_allocation_plan()['revision'], 1)
        self.assertEqual(self.count('audit'), 1)


if __name__ == '__main__':
    unittest.main()
