import unittest
from packages.providers import sina_quote, get_quote

class ProviderTests(unittest.TestCase):
    def test_sina_parse(self):
        quote = sina_quote('QQQM', lambda url: b'var hq_str_gb_qqqm="QQQM,201.25,-1.5";')
        self.assertEqual((quote.symbol, quote.price, quote.change_percent), ('QQQM', 201.25, -1.5))
    def test_failure_is_visible(self):
        def fail(symbol): raise TimeoutError('timeout')
        result = get_quote('QQQM', (fail,))
        self.assertTrue(result.degraded)
        self.assertIsNone(result.quote)
        self.assertIn('timeout', result.errors[0])
    def test_fallback_is_visible(self):
        def fail(symbol): raise TimeoutError('timeout')
        def success(symbol): return sina_quote(symbol, lambda url: b'x="QQQM,200,1"')
        result = get_quote('QQQM', (fail, success))
        self.assertTrue(result.degraded)
        self.assertEqual(result.quote.price, 200)
    def test_bad_symbol(self):
        with self.assertRaises(ValueError): sina_quote('../bad')
