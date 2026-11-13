from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from packages.providers import Quote, sina_quote, tencent_quote, get_quote, MAX_RESPONSE_BYTES, MAX_QUOTE_AGE_SECONDS


NOW = datetime(2026, 11, 13, 18, tzinfo=timezone.utc).timestamp()


def sina_response(symbol='QQQM', price='201.25', change='-1.5', stamp='2026-11-14 02:00:00'):
    return f'var hq_str_gb_{symbol.lower()}="{symbol},{price},{change},{stamp}";'.encode()


def tencent_response(symbol='QQQM', price='201.25', change='-1.5', stamp='2026-11-13 13:00:00', currency='USD', previous='200'):
    fields = [''] * 73
    fields[0:5] = ['200', symbol, symbol + '.OQ', price, previous]
    fields[30], fields[32], fields[35] = stamp, change, currency
    return ('v_us' + symbol + '="' + '~'.join(fields) + '";\r\n').encode()


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch('packages.providers.time', return_value=NOW)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()

    def test_sina_parse_real_source_timestamp(self):
        urls = []
        def fetch(url):
            urls.append(url)
            return sina_response()
        quote = sina_quote(' qqqm ', fetch)
        self.assertEqual((quote.symbol, quote.price, quote.change_percent), ('QQQM', 201.25, -1.5))
        self.assertEqual(quote.timestamp, NOW)
        self.assertEqual(quote.fetched_at, NOW)
        self.assertEqual(urls, ['https://hq.sinajs.cn/list=gb_qqqm'])

    def test_tencent_parse_and_eastern_standard_time(self):
        urls = []
        def fetch(url):
            urls.append(url)
            return tencent_response()
        quote = tencent_quote(' qqqm ', fetch)
        self.assertEqual((quote.symbol, quote.price, quote.change_percent, quote.source), ('QQQM', 201.25, -1.5, 'tencent'))
        self.assertEqual(quote.timestamp, NOW)
        self.assertEqual(urls, ['https://qt.gtimg.cn/q=usQQQM'])

    def test_tencent_timestamp_respects_daylight_saving(self):
        summer = datetime(2026, 7, 10, 17, tzinfo=timezone.utc).timestamp()
        with patch('packages.providers.time', return_value=summer):
            quote = tencent_quote('QQQM', lambda _: tencent_response(stamp='2026-07-10 13:00:00'))
        self.assertEqual(quote.timestamp, summer)

    def test_tencent_calculates_missing_change_from_previous_close(self):
        quote = tencent_quote('QQQM', lambda _: tencent_response(price='210', change=''))
        self.assertAlmostEqual(quote.change_percent, 5)
        with self.assertRaises(ValueError):
            tencent_quote('QQQM', lambda _: tencent_response(change='', previous='0'))

    def test_failure_is_visible(self):
        def fail(symbol):
            raise TimeoutError('timeout')
        result = get_quote('QQQM', (fail,))
        self.assertTrue(result.degraded)
        self.assertIsNone(result.quote)
        self.assertIn('timeout', result.errors[0])

    def test_default_sina_failure_uses_tencent_and_preserves_error(self):
        actual = tencent_quote('QQQM', lambda _: tencent_response())
        with patch('packages.providers.sina_quote', side_effect=TimeoutError('Sina unavailable')) as first, \
             patch('packages.providers.tencent_quote', return_value=actual) as second:
            result = get_quote(' qqqm ')
        first.assert_called_once_with('QQQM')
        second.assert_called_once_with('QQQM')
        self.assertTrue(result.degraded)
        self.assertEqual(result.quote.source, 'tencent')
        self.assertIn('Sina unavailable', result.errors[0])

    def test_healthy_primary_does_not_call_fallback(self):
        actual = sina_quote('QQQM', lambda _: sina_response())
        with patch('packages.providers.sina_quote', return_value=actual), patch('packages.providers.tencent_quote') as fallback:
            result = get_quote('QQQM')
        fallback.assert_not_called()
        self.assertFalse(result.degraded)
        self.assertEqual(result.errors, ())

    def test_stale_primary_skipped_for_valid_fallback_and_both_failures_visible(self):
        old = Quote('QQQM', 999, 1, 'old', NOW - MAX_QUOTE_AGE_SECONDS - 1)
        good = Quote('QQQM', 200, 1, 'new', NOW - 600)
        result = get_quote('QQQM', (lambda _: old, lambda _: good))
        self.assertEqual(result.quote, good)
        self.assertTrue(result.degraded)
        self.assertIn('stale', result.errors[0])
        result = get_quote('QQQM', (lambda _: old, lambda _: None))
        self.assertIsNone(result.quote)
        self.assertEqual(len(result.errors), 2)
        self.assertTrue(result.degraded)

    def test_never_replaces_source_timestamp_with_fetch_time(self):
        quote = tencent_quote('QQQM', lambda _: tencent_response(stamp='2026-11-12 16:00:00'))
        self.assertEqual(quote.timestamp, datetime(2026, 11, 12, 21, tzinfo=timezone.utc).timestamp())
        self.assertGreater(quote.fetched_at, quote.timestamp)

    def test_rejects_incomplete_wrong_symbol_currency_and_bad_values(self):
        for raw in [b'v_pv_none_match="1";', tencent_response(symbol='AAPL'),
                    tencent_response(currency='CNY'), tencent_response(price='0'),
                    tencent_response(price='NaN'), tencent_response(change='Infinity'),
                    tencent_response(stamp='2026-11-13 13:06:00'), tencent_response(stamp='2025-11-13 13:00:00'),
                    tencent_response(stamp=''), b' ' * (MAX_RESPONSE_BYTES + 1)]:
            with self.subTest(raw=raw[:70]), self.assertRaises(ValueError):
                tencent_quote('QQQM', lambda _: raw)
        for raw in [b'var hq_str_gb_qqqm="";', b'var hq_str_gb_qqqm="QQQM,200,1";',
                    sina_response(symbol='AAPL'), sina_response(price='Infinity'), sina_response(stamp='garbage'),
                    sina_response(stamp='2026-11-14 03:00:00')]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                sina_quote('QQQM', lambda _: raw)

    def test_bad_symbols_never_fetch(self):
        for symbol in ['../bad', '', 'A' * 16, 'QQQM&x=1', ' a/b ']:
            for provider in [sina_quote, tencent_quote]:
                with self.subTest(symbol=symbol, provider=provider), self.assertRaises(ValueError):
                    provider(symbol, lambda _: self.fail('invalid symbol must not make a request'))


if __name__ == '__main__':
    unittest.main()
