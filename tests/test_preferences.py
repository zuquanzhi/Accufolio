"""Shared defaults stay separate from asset data and local device overrides."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from apps.api.preferences import router, get_settings, set_settings
from apps.api.main import db


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'ACCUFOLIO_DB':str(Path(self.temp.name)/'test.db'),
                                       'ACCUFOLIO_PASSWORD':'prefs'})
        self.env.start()
        app=FastAPI()
        app.include_router(router)
        self.client=TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def test_read_defaults_and_no_http_mutation(self):
        self.assertEqual(self.client.get('/api/settings').status_code,401)
        result=self.client.get('/api/settings',auth=('admin','prefs')).json()
        self.assertEqual(result['display_currency'],'USD')
        self.assertEqual(result['market_timezone'],'America/New_York')
        self.assertEqual(result['revision'],0)
        for method in ['post','put','patch','delete']:
            self.assertEqual(getattr(self.client,method)('/api/settings',auth=('admin','prefs')).status_code,405)

    def test_preview_commit_audit_revision_and_retry(self):
        result=set_settings('CNY','Asia/Shanghai',0,'settings-test')
        self.assertEqual(result['preview']['after']['display_currency'],'CNY')
        self.assertEqual(get_settings()['display_currency'],'USD')
        result=set_settings('CNY','Asia/Shanghai',0,'settings-test',False)
        self.assertEqual(result['revision'],1)
        self.assertEqual(set_settings('CNY','Asia/Shanghai',0,'settings-test',False),result)
        self.assertEqual(get_settings()['market_timezone'],'Asia/Shanghai')
        with self.assertRaises(HTTPException) as stale:
            set_settings('USD','America/New_York',0,'stale',False)
        self.assertEqual(stale.exception.status_code,409)
        with db() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM audit WHERE action='set_settings'").fetchone()[0],1)
        with self.assertRaises(HTTPException):
            set_settings('EUR','Asia/Shanghai',0,'settings-test',False)

    def test_invalid_currency_timezone_revision_and_key(self):
        for args in [('GBP','Asia/Shanghai',0,'key'),('CNY','badzone',0,'key'),
                     ('CNY','Asia/Shanghai',True,'key'),('CNY','Asia/Shanghai',0,' ')]:
            with self.assertRaises(ValueError):
                set_settings(*args)


if __name__=='__main__':
    unittest.main()
