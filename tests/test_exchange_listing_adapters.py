import json
import unittest

from exchange_listings.adapters import (
    SourceUnavailable,
    fetch_live_sources,
    parse_binance_sources,
    parse_bybit_sources,
    parse_kraken_sources,
    parse_kucoin_sources,
    parse_mexc_sources,
    parse_okx_sources,
)


class ExchangeListingAdapterTests(unittest.TestCase):
    def test_binance_api_sources_keep_spot_listing_titles(self):
        payload = {
            "data": {
                "articles": [
                    {
                        "code": "spot-code",
                        "title": "Binance Will List Alpha (ALP) with Seed Tag Applied",
                        "body": "<p>Binance will list Alpha (ALP). Trading pairs: ALP/USDT.</p>",
                    },
                    {
                        "code": "futures-code",
                        "title": "Binance Futures Will Launch ALPUSDT Perpetual Contract",
                    },
                ]
            }
        }

        sources = parse_binance_sources(json.dumps(payload), limit=5)

        self.assertEqual(1, len(sources))
        self.assertEqual("binance", sources[0]["exchange"])
        self.assertEqual("spot-code", sources[0]["external_id"])
        self.assertIn("ALP/USDT", sources[0]["raw_text"])

    def test_okx_sources_are_extracted_from_help_links(self):
        html = """
        <a href="/en-us/help/okx-to-list-irys-usdt-irys-for-spot-trading">
          OKX to list IRYS/USDT (Irys) for spot trading Published on May 27, 2026
        </a>
        """

        sources = parse_okx_sources(html, limit=5)

        self.assertEqual(1, len(sources))
        self.assertEqual("OKX to list IRYS/USDT (Irys) for spot trading", sources[0]["title"])
        self.assertEqual("2026-05-27T00:00:00Z", sources[0]["published_at"])

    def test_kucoin_sources_skip_futures_and_keep_listed_on_titles(self):
        html = """
        <a href="/announcement/en-futures"><h3>KuCoin Futures New Listing: ABCUSDT Perpetual Contract</h3></a>
        <a href="/announcement/en-world-premiere-qait-qait-listed-on-kucoin">
          <h3>World Premiere: QAIT (QAIT) Listed on KuCoin</h3>
        </a>
        """

        sources = parse_kucoin_sources(html, limit=5)

        self.assertEqual(1, len(sources))
        self.assertEqual("World Premiere: QAIT (QAIT) Listed on KuCoin", sources[0]["title"])

    def test_mexc_sources_extract_article_titles_and_datetimes(self):
        html = """
        <a title="First in Market: MEXC to List Citrea (CTR) in Innovation Zone"
           href="/announcements/article/first-in-market-178">
           <h2>First in Market: MEXC to List Citrea (CTR) in Innovation Zone</h2>
        </a><time dateTime="2026-06-01T04:00:42.000Z"></time>
        <a title="MEXC to List QNTSTOCKUSDT Futures" href="/announcements/article/futures">
           <h2>MEXC to List QNTSTOCKUSDT Futures</h2>
        </a><time dateTime="2026-05-29T03:50:29.000Z"></time>
        """

        sources = parse_mexc_sources(html, limit=5)

        self.assertEqual(1, len(sources))
        self.assertEqual("CTR", sources[0]["external_id"])
        self.assertEqual("2026-06-01T04:00:42Z", sources[0]["published_at"])

    def test_bybit_sources_use_next_article_data(self):
        next_data = {
            "props": {
                "pageProps": {
                    "articleInitEntity": {
                        "list": [
                            {
                                "title": "New listing: ABCUSDT Perpetual Contract",
                                "description": "Derivatives only",
                                "topics": ["Derivatives"],
                                "url": "/article/derivative",
                                "objectID": "d1",
                                "publish_time": 1780000000,
                            },
                            {
                                "title": "New Spot Listing: Example Token (EXT)",
                                "description": "Bybit will list Example Token (EXT) for spot trading.",
                                "topics": ["Spot", "Spot Listings"],
                                "url": "/article/spot",
                                "objectID": "s1",
                                "publish_time": 1780000100,
                            },
                        ]
                    }
                }
            }
        }
        html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'

        sources = parse_bybit_sources(html, limit=5)

        self.assertEqual(1, len(sources))
        self.assertEqual("s1", sources[0]["external_id"])
        self.assertIn("spot trading", sources[0]["raw_text"])

    def test_kraken_sources_extract_upcoming_symbols(self):
        html = """
        <h2><span>AmericanFortress</span></h2><h3><span>AF</span></h3>
        <h2><span>Solstice</span></h2><h3><span>SLX</span></h3>
        """

        sources = parse_kraken_sources(html, limit=1)

        self.assertEqual(1, len(sources))
        self.assertEqual("AF", sources[0]["external_id"])
        self.assertEqual("AmericanFortress", sources[0]["project_name"])
        self.assertIn("Kraken will list AmericanFortress (AF)", sources[0]["raw_text"])

    def test_live_fetcher_raises_for_unimplemented_sources(self):
        with self.assertRaises(SourceUnavailable):
            fetch_live_sources("coinbase", limit=1, fetch_text=lambda url: "")
