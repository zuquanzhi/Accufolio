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
            result=self.client.get('/api/summary', auth=self.auth).json()
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
        for name in ['assets', 'records', 'discover', 'settings']:
            self.assertEqual(self.client.get('/'+name).status_code, 401)
            response = self.client.get('/'+name, auth=self.auth)
            self.assertEqual(response.status_code, 200)
            self.assertIn('no-store', response.headers['cache-control'])
            self.assertEqual(response.text.count('class="app-view"'), 1)
            for other in ['assets', 'records', 'discover', 'settings']:
                self.assertEqual(('id="view-'+other+'"') in response.text, other == name)
            self.assertIn('src="/pages/'+name+'.js"', response.text)
            self.assertEqual(self.client.get('/pages/'+name+'.js').status_code, 200)
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
        self.assertEqual(self.client.get('/api/export',auth=self.auth).json()['format'],'accufolio-trades-v1')

    def test_monitoring_deduplication(self):
        from packages.providers import Quote
        h={'X-Accufolio':'1'}
        self.assertEqual(self.client.put('/api/watchlist',auth=self.auth,headers=h,json={'symbol':'QQQM','threshold':2}).status_code,200)
        with patch('apps.api.extended.get_quote',return_value=QuoteResult(Quote('QQQM',100,-3,'test',0),False,())):
            for _ in range(2): self.client.post('/api/scan',auth=self.auth,headers=h)
        self.assertEqual(len(self.client.get('/api/alerts',auth=self.auth).json()),1)
        self.assertEqual(self.client.get('/api/daily',auth=self.auth).json()[0]['scans'],2)
        self.assertEqual(self.client.get('/api/health',auth=self.auth).json()['database'],'ok')
