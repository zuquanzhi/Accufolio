import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from apps.api.main import app
from packages.providers import QuoteResult

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'ACCUFOLIO_PASSWORD':'test-password', 'ACCUFOLIO_DB':str(Path(self.tmp.name)/'test.db')})
        self.env.start()
        self.client = TestClient(app)
        self.auth = ('admin', 'test-password')
    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.tmp.cleanup()
    def test_auth_and_csrf(self):
        self.assertEqual(self.client.get('/api/trades').status_code, 401)
        self.assertEqual(self.client.get('/api/trades', auth=('admin','wrong')).status_code, 401)
        self.assertEqual(self.client.post('/api/trades', auth=self.auth, json={}).status_code, 403)
    def test_trade_lifecycle_and_audit(self):
        trade = {'symbol':'qqqm','trade_date':'2026-01-01','shares':2,'price':100,'note':'test'}
        response = self.client.post('/api/trades', auth=self.auth, headers={'X-Accufolio':'1'}, json=trade)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.client.get('/api/trades', auth=self.auth).json()[0]['symbol'], 'QQQM')
        with patch('apps.api.main.get_quote', return_value=QuoteResult(None, True, ('offline',))):
            result=self.client.get('/api/summary?symbol=QQQM', auth=self.auth).json()
            self.assertEqual(result['cost'], 200)
            self.assertTrue(result['degraded'])
        self.assertEqual(self.client.delete('/api/trades/1', auth=self.auth, headers={'X-Accufolio':'1'}).status_code, 200)
        self.assertEqual(self.client.get('/api/trades', auth=self.auth).json(), [])
        import sqlite3
        db = sqlite3.connect(Path(self.tmp.name)/'test.db')
        try:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM audit').fetchone()[0], 2)
        finally:
            db.close()
    def test_edit_and_backtest(self):
        headers={'X-Accufolio':'1'}
        trade={'symbol':'QQQM','trade_date':'2025-01-01','shares':1,'price':100}
        self.assertEqual(self.client.post('/api/trades', auth=self.auth, headers=headers, json=trade).status_code,201)
        trade['price']=120
        self.assertEqual(self.client.put('/api/trades/1', auth=self.auth, headers=headers, json=trade).status_code,200)
        self.assertEqual(self.client.get('/api/trades', auth=self.auth).json()[0]['price'],120)
        audit=self.client.get('/api/audit', auth=self.auth).json()
        self.assertEqual(audit[0]['action'],'update_trade')
        payload={'monthly':100,'bars':[{'day':'2025-01-02','close':100},{'day':'2025-02-03','close':50},{'day':'2025-03-03','close':100}]}
        response=self.client.post('/api/backtest', auth=self.auth, headers=headers, json=payload)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['strategies']['fixed']['value'],400)
        payload['bars'].reverse()
        self.assertEqual(self.client.post('/api/backtest', auth=self.auth, headers=headers, json=payload).status_code,422)

    def test_bad_trade_and_assets(self):
        response=self.client.post('/api/trades', auth=self.auth, headers={'X-Accufolio':'1'}, json={'symbol':'qqqm','trade_date':'2026-01-01','shares':-1,'price':1})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get('/', auth=self.auth).status_code, 200)
        self.assertEqual(self.client.get('/manifest.webmanifest').status_code, 200)
        self.assertEqual(self.client.get('/sw.js').status_code, 200)
        self.assertEqual(self.client.get('/api/version', auth=self.auth).status_code, 200)

    def test_independent_authenticated_pages(self):
        root = self.client.get('/', auth=self.auth, follow_redirects=False)
        self.assertEqual(root.status_code, 307)
        self.assertEqual(root.headers['location'], '/assets')
        for name in ['assets', 'investments', 'records', 'discover', 'settings']:
            self.assertEqual(self.client.get('/'+name).status_code, 401)
            response = self.client.get('/'+name, auth=self.auth)
            self.assertEqual(response.status_code, 200)
            self.assertIn('no-store', response.headers['cache-control'])
            self.assertEqual(response.text.count('class="app-view'), 1)
            for other in ['assets', 'investments', 'records', 'discover', 'settings']:
                self.assertEqual(('id="view-'+other+'"') in response.text, other == name)
            self.assertIn('src="/pages/'+name+'.js"', response.text)
            self.assertEqual(self.client.get('/pages/'+name+'.js').status_code, 200)
        self.assertEqual(self.client.get('/api/ui/pages').status_code, 401)
        fragments = self.client.get('/api/ui/pages', auth=self.auth).json()
        self.assertEqual(set(fragments), {'assets', 'investments', 'records', 'discover', 'settings'})
        for name, html in fragments.items():
            self.assertEqual(html.count('class="app-view'), 1)
            self.assertIn('id="view-'+name+'"', html)
        for path in ['/pages/assets.html', '/pages/unknown.js', '/pages/../main.py']:
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_idempotency_undo_and_backup(self):
        headers={'X-Accufolio':'1','Idempotency-Key':'once'}
        trade={'symbol':'QQQM','trade_date':'2025-01-01','shares':1,'price':100}
        first=self.client.post('/api/trades',auth=self.auth,headers=headers,json=trade).json()
        second=self.client.post('/api/trades',auth=self.auth,headers=headers,json=trade).json()
        self.assertEqual(first,second)
        trade['price']=101
        self.assertEqual(self.client.post('/api/trades',auth=self.auth,headers=headers,json=trade).status_code,409)
        self.assertEqual(len(self.client.get('/api/trades',auth=self.auth).json()),1)
        h={'X-Accufolio':'1'}
        edited=self.client.put('/api/trades/1',auth=self.auth,headers=h,json=trade).json()
        self.assertEqual(self.client.post(f'/api/audit/{first["audit_id"]}/undo',auth=self.auth,headers=h).status_code,409)
        self.assertEqual(self.client.post(f'/api/audit/{edited["audit_id"]}/undo',auth=self.auth,headers=h).status_code,200)
        self.assertEqual(self.client.get('/api/trades',auth=self.auth).json()[0]['price'],100)
        backup=self.client.post('/api/backup',auth=self.auth,headers=h).json()
        self.assertTrue((Path(self.tmp.name)/'backups'/backup['filename']).exists())
        exported=self.client.get('/api/export',auth=self.auth).json()
        self.assertEqual(exported['format'],'accufolio-v2')
        self.assertEqual(set(exported),{'format','exported_at','accounts','cash_entries','trades','fx_rates','settings','allocation_targets'})
        self.assertEqual(exported['trades'][0]['account_id'],exported['accounts'][0]['id'])
        self.assertEqual(exported['trades'][0]['price'],100)
        self.assertNotIn('test-password',str(exported))

    def test_retired_monitoring_endpoints_are_absent(self):
        for method, path in [('get','watchlist'),('put','watchlist'),('delete','watchlist/QQQM'),
                             ('post','scan'),('get','alerts'),('get','signals'),('get','daily'),
                             ('get','notifications/status'),('post','notifications/retry')]:
            with self.subTest(method=method,path=path):
                response = getattr(self.client,method)('/api/'+path,auth=self.auth,headers={'X-Accufolio':'1'})
                self.assertEqual(response.status_code,404)

    def test_health_reports_backups_and_requires_authentication(self):
        self.assertEqual(self.client.get('/api/health').status_code,401)
        result=self.client.get('/api/health',auth=self.auth).json()
        self.assertEqual(result['database'],'ok')
        self.assertEqual(result['backup']['status'],'not_created')
        self.assertEqual(result['worker']['status'],'not_started')
        self.assertNotIn('last_scan',result)
        self.assertNotIn('notifications',result)
