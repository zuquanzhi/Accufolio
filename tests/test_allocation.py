import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from apps.api.main import app, db
from apps.api.allocation import allocation, save_targets


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'ACCUFOLIO_DB':str(Path(self.tmp.name)/'db'),
                                      'ACCUFOLIO_USER':'admin','ACCUFOLIO_PASSWORD':'test-password'})
        self.env.start();self.client=TestClient(app);self.auth=('admin','test-password')

    def tearDown(self):
        self.client.close();self.env.stop();self.tmp.cleanup()

    def test_http_readonly_explicit_scopes_and_no_money_fields(self):
        self.assertEqual(self.client.get('/api/allocation').status_code,401)
        result=self.client.get('/api/allocation',auth=self.auth).json()
        self.assertEqual(result,{'securities':{},'net_worth':{},'revision':0,'updated_at':None})
        for method in ('post','put','delete'):
            self.assertEqual(self.client.request(method,'/api/allocation',auth=self.auth).status_code,405)

    def test_preview_then_commit_preserves_other_scope_and_audits(self):
        self.assertEqual(save_targets('securities',{' qqqm ':100},0,'first')['preview']['targets'],{'QQQM':100})
        self.assertEqual(allocation()['revision'],0)
        first=save_targets('securities',{'QQQM':100},0,'first',False)
        second=save_targets('net_worth',{'account:1':100},1,'second',False)
        self.assertEqual(second['securities'],first['securities'])
        self.assertEqual(second['net_worth'],{'account:1':100})
        with db() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM audit WHERE action='allocation_targets'").fetchone()[0],2)

    def test_idempotency_revision_and_global_key_conflicts(self):
        result=save_targets('securities',{'QQQM':100},0,'first',False)
        self.assertEqual(save_targets('securities',{'QQQM':100},0,'first',False),result)
        with self.assertRaises(HTTPException):save_targets('securities',{'VOO':100},0,'first',False)
        with self.assertRaises(HTTPException):save_targets('securities',{'VOO':100},0,'second',False)

    def test_invalid_weights_keys_and_missing_accounts(self):
        for scope,targets in [('other',{}),('securities',{'A':99}),('securities',{'A':float('nan')}),
                              ('securities',{'A':True}),('securities',{'__RESERVE__':100}),
                              ('net_worth',{'account:999':100}),('net_worth',{'CASH':100})]:
            with self.subTest(scope=scope,targets=targets),self.assertRaises(ValueError):
                save_targets(scope,targets,0,'bad')
        for revision in (True,-1,1.5):
            with self.assertRaises(ValueError):save_targets('securities',{},revision,'bad')

    def test_empty_clears_only_requested_scope(self):
        save_targets('securities',{'CASH':100},0,'first',False)
        save_targets('net_worth',{'account:1':100},1,'second',False)
        result=save_targets('securities',{},2,'clear',False)
        self.assertEqual(result['securities'],{})
        self.assertEqual(result['net_worth'],{'account:1':100})


if __name__=='__main__':unittest.main()
