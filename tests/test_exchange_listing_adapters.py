import json
import unittest
from datetime import datetime, timezone

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
from exchange_listings.parsers import parse_events


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
          <p>Trading: 13:00 on May 28, 2026 (UTC)</p>
          <p><bdi>05/27/2026, 19:06:00</bdi></p>
        </a>
        """

        sources = parse_kucoin_sources(html, limit=5)

        self.assertEqual(1, len(sources))
        self.assertEqual("World Premiere: QAIT (QAIT) Listed on KuCoin", sources[0]["title"])
        self.assertEqual("2026-05-27T19:06:00Z", sources[0]["published_at"])

    def test_live_fetcher_clamps_lookback_to_three_months_and_fetches_kucoin_pages(self):
        calls = []
        html = """
        <a href="/announcement/en-old-old-listed-on-kucoin">
          <h3>Old Token (OLD) Listed on KuCoin</h3>
          <p><bdi>01/01/2026, 00:00:00</bdi></p>
        </a>
        <a href="/announcement/en-new-new-listed-on-kucoin">
          <h3>New Token (NEW) Listed on KuCoin</h3>
          <p><bdi>05/20/2026, 00:00:00</bdi></p>
        </a>
        """

        def fake_fetch(url):
            calls.append(url)
            return html

        sources = fetch_live_sources(
            "kucoin",
            months=12,
            limit=10,
            fetch_text=fake_fetch,
            now=datetime(2026, 5, 29, tzinfo=timezone.utc),
            max_pages=2,
        )

        self.assertEqual(
            [
                "https://www.kucoin.com/announcement/new-listings",
                "https://www.kucoin.com/announcement/new-listings/page/2",
            ],
            calls,
        )
        self.assertEqual(["New Token (NEW) Listed on KuCoin"], [source["title"] for source in sources])

    def test_live_fetcher_reaches_late_kucoin_pages_within_three_month_window(self):
        calls = []

        def fake_fetch(url):
            calls.append(url)
            if url.endswith("/page/6"):
                return """
                <a href="/announcement/en-world-premiere-based-based-listed-on-kucoin">
                  <h3>World Premiere: Based (BASED) Listed on KuCoin</h3>
                  <p><bdi>03/30/2026, 10:00:00</bdi></p>
                </a>
                """
            if url.endswith("/page/7"):
                return """
                <a href="/announcement/en-old-old-listed-on-kucoin">
                  <h3>Old Token (OLD) Listed on KuCoin</h3>
                  <p><bdi>02/01/2026, 00:00:00</bdi></p>
                </a>
                """
            return """
            <a href="/announcement/en-recent-recent-listed-on-kucoin">
              <h3>Recent Token (RECENT) Listed on KuCoin</h3>
              <p><bdi>05/20/2026, 00:00:00</bdi></p>
            </a>
            """

        sources = fetch_live_sources(
            "kucoin",
            months=3,
            limit=10,
            fetch_text=fake_fetch,
            now=datetime(2026, 5, 29, tzinfo=timezone.utc),
        )

        self.assertIn("https://www.kucoin.com/announcement/new-listings/page/6", calls)
        self.assertNotIn("https://www.kucoin.com/announcement/new-listings/page/8", calls)
        self.assertIn("World Premiere: Based (BASED) Listed on KuCoin", [source["title"] for source in sources])

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

    def test_mexc_sources_extract_next_section_articles(self):
        html = r"""
        <script>self.__next_f.push([1,"{\"_sectionArticles\":[{\"id\":17827791534508,\"title\":\"First in Market: MEXC to List Based (BASED) in Innovation Zone With Convert Feature\",\"displayTime\":1774864800000,\"displayTimeLocale\":\"Mar 30, 2026\",\"labelList\":[{\"name\":\"Spot\"}]}]}"])</script>
        """

        sources = parse_mexc_sources(html, limit=5)

        self.assertEqual(1, len(sources))
        self.assertEqual("First in Market: MEXC to List Based (BASED) in Innovation Zone With Convert Feature", sources[0]["title"])
        self.assertEqual("https://www.mexc.com/announcements/article/first-in-market-17827791534508", sources[0]["source_url"])
        self.assertEqual("2026-03-30T10:00:00Z", sources[0]["published_at"])

    def test_live_fetcher_uses_mexc_query_pagination(self):
        calls = []
        first_page = """
        <a title="First in Market: MEXC to List Citrea (CTR) in Innovation Zone"
           href="/announcements/article/first-in-market-178">
        </a><time dateTime="2026-06-01T04:00:42.000Z"></time>
        """
        second_page = r"""
        <script>self.__next_f.push([1,"{\"_sectionArticles\":[{\"id\":17827791534508,\"title\":\"First in Market: MEXC to List Based (BASED) in Innovation Zone With Convert Feature\",\"displayTime\":1774864800000,\"displayTimeLocale\":\"Mar 30, 2026\",\"labelList\":[{\"name\":\"Spot\"}]}]}"])</script>
        """

        def fake_fetch(url):
            calls.append(url)
            return second_page if "page=2" in url else first_page

        sources = fetch_live_sources(
            "mexc",
            months=3,
            limit=3,
            fetch_text=fake_fetch,
            now=datetime(2026, 5, 29, tzinfo=timezone.utc),
            max_pages=2,
        )

        self.assertEqual(
            ["https://www.mexc.fm/announcements/new-listings", "https://www.mexc.fm/announcements/new-listings/spot-18?page=2"],
            calls,
        )
        self.assertIn("First in Market: MEXC to List Based (BASED) in Innovation Zone With Convert Feature", [source["title"] for source in sources])

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
        self.assertIn("Kraken will list token (AF)", sources[0]["raw_text"])

    def test_kraken_uppercase_project_name_does_not_create_extra_symbol(self):
        html = '<h2><span>SODAX</span></h2><h3><span>SODA</span></h3>'

        sources = parse_kraken_sources(html, limit=5)
        events = parse_events(sources[0])

        self.assertEqual(["SODA"], [event["token_symbol"] for event in events])

    def test_live_fetcher_raises_for_unimplemented_sources(self):
        with self.assertRaises(SourceUnavailable):
            fetch_live_sources("coinbase", limit=1, fetch_text=lambda url: "")
