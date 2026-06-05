import json
import subprocess
import unittest
from datetime import datetime, timezone

from exchange_listings.adapters import (
    SourceUnavailable,
    fetch_live_sources,
    parse_binance_sources,
    parse_bithumb_sources,
    parse_bitget_sources,
    parse_bybit_sources,
    parse_coinbase_sources,
    parse_gate_sources,
    parse_kraken_sources,
    parse_kucoin_sources,
    parse_mexc_sources,
    parse_okx_sources,
    parse_upbit_sources,
)
from exchange_listings.parsers import parse_events


class ExchangeListingAdapterTests(unittest.TestCase):
    def test_binance_api_sources_keep_spot_and_futures_listing_titles(self):
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

        self.assertEqual(2, len(sources))
        self.assertEqual("binance", sources[0]["exchange"])
        self.assertEqual("spot-code", sources[0]["external_id"])
        self.assertIn("ALP/USDT", sources[0]["raw_text"])
        self.assertEqual("futures-code", sources[1]["external_id"])
        self.assertEqual("Binance Futures Will Launch ALPUSDT Perpetual Contract", sources[1]["raw_text"])

    def test_binance_live_sources_enrich_announcement_body_from_detail_api(self):
        list_payload = {
            "data": {
                "articles": [
                    {
                        "code": "detail-code",
                        "title": "Binance Futures Will Launch USDⓈ-Margined CTRUSDT Perpetual Contract (2026-05-28)",
                        "body": None,
                    },
                ]
            }
        }
        detail_payload = {
            "data": {
                "body": json.dumps(
                    {
                        "node": "root",
                        "child": [
                            {
                                "node": "text",
                                "text": "2026-05-28 09:30 (UTC): CTRUSDT Perpetual Contract",
                            }
                        ],
                    }
                )
            }
        }

        def fake_fetch(url):
            if "article/detail/query" in url:
                return json.dumps(detail_payload)
            return json.dumps(list_payload)

        sources = fetch_live_sources("binance", fetch_text=fake_fetch, limit=1, max_pages=1)

        self.assertEqual(1, len(sources))
        self.assertIn("2026-05-28 09:30 (UTC)", sources[0]["raw_text"])

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
            max_pages=7,
        )

        self.assertIn("https://www.kucoin.com/announcement/new-listings/page/6", calls)
        self.assertNotIn("https://www.kucoin.com/announcement/new-listings/page/8", calls)
        self.assertIn("World Premiere: Based (BASED) Listed on KuCoin", [source["title"] for source in sources])

    def test_live_fetcher_defaults_to_first_three_pages_for_daily_sync(self):
        calls = []

        def fake_fetch(url):
            calls.append(url)
            return """
            <a href="/announcement/en-recent-recent-listed-on-kucoin">
              <h3>Recent Token (RECENT) Listed on KuCoin</h3>
              <p><bdi>05/20/2026, 00:00:00</bdi></p>
            </a>
            """

        fetch_live_sources(
            "kucoin",
            months=3,
            limit=100,
            fetch_text=fake_fetch,
            now=datetime(2026, 5, 29, tzinfo=timezone.utc),
        )

        self.assertEqual(
            [
                "https://www.kucoin.com/announcement/new-listings",
                "https://www.kucoin.com/announcement/new-listings/page/2",
                "https://www.kucoin.com/announcement/new-listings/page/3",
            ],
            calls,
        )

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

    def test_bybit_live_fetcher_falls_back_to_browser_fetch_when_curl_fails(self):
        calls = []
        next_data = {
            "props": {
                "pageProps": {
                    "articleInitEntity": {
                        "list": [
                            {
                                "title": "New Spot Listing: Example Token (EXT)",
                                "description": "Bybit will list Example Token (EXT) for spot trading.",
                                "topics": ["Spot", "Spot Listings"],
                                "url": "/article/spot",
                                "objectID": "s1",
                                "publish_time": 1780000100,
                            }
                        ]
                    }
                }
            }
        }
        html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'

        def fake_fetch(url):
            calls.append(("curl", url))
            raise subprocess.CalledProcessError(56, ["curl", url])

        def fake_browser_fetch(url):
            calls.append(("browser", url))
            return html

        sources = fetch_live_sources(
            "bybit",
            limit=2,
            max_pages=1,
            fetch_text=fake_fetch,
            fetch_browser_text=fake_browser_fetch,
        )

        self.assertEqual(
            [
                ("curl", "https://announcements.bybit.com/en/?category=new_crypto&page=1"),
                ("browser", "https://announcements.bybit.com/en/?category=new_crypto&page=1"),
            ],
            calls,
        )
        self.assertEqual(["s1"], [source["external_id"] for source in sources])

    def test_coinbase_sources_extract_roadmap_posts_from_x_search_html(self):
        html = """
        <article>
          <a href="/CoinbaseMarkets/status/2057085756120748167">May 30</a>
          <div>Assets added to the roadmap today: Example Token (EXT) on Base.</div>
        </article>
        <article>
          <a href="https://x.com/CoinbaseMarkets/status/2057000000000000000">May 29</a>
          <div>Trading will begin for Old Token (OLD) today. This is not a roadmap update.</div>
        </article>
        """

        sources = parse_coinbase_sources(html, limit=5)

        self.assertEqual(1, len(sources))
        self.assertEqual("coinbase", sources[0]["exchange"])
        self.assertEqual("official_x", sources[0]["source_type"])
        self.assertEqual("https://x.com/CoinbaseMarkets/status/2057085756120748167", sources[0]["source_url"])
        self.assertEqual("2057085756120748167", sources[0]["external_id"])
        self.assertIn("Example Token (EXT)", sources[0]["raw_text"])

    def test_coinbase_sources_extract_assets_from_official_roadmap_blog(self):
        html = """
        <h2>Roadmap</h2>
        <p>Updates to the roadmap will be made here and announced via our official Twitter account.</p>
        <p>Assets on the Ethereum network (ERC-20 tokens)</p>
        <p>Nexus (NEX) - Contract address: 0xf57D49646621F563b0B905aFc8336923AC569Ec5</p>
        <p>Assets on the Base network</p>
        <p>Citrea (CTR) - Contract address: 0x11030f79109269d796fd0fb956d6244e502757f7</p>
        <p>o1.exchange (O) - Contract address: 0x1185cB5122Edad199BdBC0cbd7a0457E448f23c7</p>
        <p>* This is not an exhaustive list of all assets which we have decided to list.</p>
        """

        sources = parse_coinbase_sources(html, limit=5)

        self.assertEqual(["NEX", "CTR", "O"], [source["external_id"] for source in sources])
        self.assertEqual(
            ["Coinbase roadmap: Nexus (NEX)", "Coinbase roadmap: Citrea (CTR)", "Coinbase roadmap: o1.exchange (O)"],
            [source["title"] for source in sources],
        )
        self.assertEqual(["official_blog", "official_blog", "official_blog"], [source["source_type"] for source in sources])
        self.assertIn("Nexus (NEX)", sources[0]["raw_text"])

    def test_upbit_sources_extract_trade_category_market_additions(self):
        html = """
        <a href="/service_center/notice?id=6255">거래아이오넷(IO) KRW 마켓 디지털 자산 추가</a>
        <a href="/service_center/notice?id=6254">거래오키드(OXT) 거래지원 종료 안내 (6/29 15:00)</a>
        """

        sources = parse_upbit_sources(html, limit=5)

        self.assertEqual(1, len(sources))
        self.assertEqual("upbit", sources[0]["exchange"])
        self.assertEqual("아이오넷(IO) KRW 마켓 디지털 자산 추가", sources[0]["title"])
        self.assertEqual("https://www.upbit.com/service_center/notice?id=6255", sources[0]["source_url"])

    def test_bithumb_sources_use_next_notice_list_market_add_category(self):
        data = {
            "props": {
                "pageProps": {
                    "noticeList": [
                        {
                            "id": 1653423,
                            "categoryName1": "이벤트",
                            "title": "총 3억원 상당, 빌리언즈(BILL) 원화마켓 추가 기념 이벤트",
                            "publicationDateTime": "2026-05-28 17:00:00",
                        },
                        {
                            "id": 1653420,
                            "categoryName1": "마켓 추가",
                            "title": "빌리언즈(BILL) 원화 마켓 추가",
                            "publicationDateTime": "2026-05-28 14:16:31",
                        },
                    ]
                }
            }
        }
        html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'

        sources = parse_bithumb_sources(html, limit=5)

        self.assertEqual(1, len(sources))
        self.assertEqual("bithumb", sources[0]["exchange"])
        self.assertEqual("빌리언즈(BILL) 원화 마켓 추가", sources[0]["title"])
        self.assertEqual("https://feed.bithumb.com/notice/1653420", sources[0]["source_url"])

    def test_gate_sources_use_next_list_data_and_skip_futures_only_titles(self):
        data = {
            "props": {
                "pageProps": {
                    "listData": {
                        "list": [
                            {
                                "id": 51373,
                                "title": "首发上线：Gate 将上线 Citrea (CTR) 合约交易、杠杆借贷交易",
                                "brief": "Gate 将于 2026 年 5 月 26 日首发上线永续合约",
                                "release_timestamp": "1779772468",
                                "url": "/announcements/article/51373",
                            },
                            {
                                "id": 51335,
                                "title": "首发上线：Gate 将上线 Citrea (CTR) 现货交易与闪兑交易",
                                "brief": "Gate 将上线 CTR/USDT 现货交易与闪兑交易",
                                "release_timestamp": "1779674402",
                                "url": "/announcements/article/51335",
                            },
                        ]
                    }
                }
            }
        }
        html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'

        sources = parse_gate_sources(html, limit=5)

        self.assertEqual(1, len(sources))
        self.assertEqual("gate", sources[0]["exchange"])
        self.assertEqual("首发上线：Gate 将上线 Citrea (CTR) 现货交易与闪兑交易", sources[0]["title"])
        self.assertEqual("https://www.gate.com/zh/announcements/article/51335", sources[0]["source_url"])

    def test_bitget_sources_use_section_article_state(self):
        html = """
        <script>
        window.__STATE__={"sectionArticle":{"items":[
          {"contentId":"12560603884389","title":"【首发上币】Solstice（SLX）将上线 Bitget Solana 生态专区","showTime":"1779706825000"},
          {"contentId":"12560603883519","title":"KAIO（KAIO）将上线 Bitget Launchpool，参与瓜分 14,120,000 KAIO","showTime":"1778054428000"}
        ]}};
        </script>
        """

        sources = parse_bitget_sources(html, limit=5)

        self.assertEqual(2, len(sources))
        self.assertEqual("bitget", sources[0]["exchange"])
        self.assertEqual("【首发上币】Solstice（SLX）将上线 Bitget Solana 生态专区", sources[0]["title"])
        self.assertEqual("https://www.bitget.com/zh-CN/support/articles/12560603884389", sources[0]["source_url"])

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

    def test_live_fetcher_supports_all_configured_listing_sources(self):
        html_by_exchange = {
            "upbit": '<a href="/service_center/notice?id=6255">거래아이오넷(IO) KRW 마켓 디지털 자산 추가</a>',
            "bithumb": '<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"noticeList":[{"id":1,"categoryName1":"마켓 추가","title":"테스트(TST) 원화 마켓 추가","publicationDateTime":"2026-05-28 14:16:31"}]}}}</script>',
            "gate": '<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"listData":{"list":[{"id":1,"title":"Gate 将上线 Test (TST) 现货交易","brief":"Gate 将上线 TST/USDT 现货交易","release_timestamp":"1779674402","url":"/announcements/article/1"}]}}}}</script>',
            "bitget": '{"sectionArticle":{"items":[{"contentId":"1","title":"Test（TST）将上线 Bitget","showTime":"1779706825000"}]}}',
            "coinbase": """
                <article>
                  <a href="/CoinbaseMarkets/status/2057085756120748167">May 30</a>
                  <div>Assets added to the roadmap today: Example Token (EXT) on Base.</div>
                </article>
            """,
        }
        calls = []

        for exchange, html in html_by_exchange.items():
            with self.subTest(exchange=exchange):
                calls.clear()

                def fake_fetch(url):
                    calls.append(url)
                    return html

                sources = fetch_live_sources(exchange, limit=2, max_pages=1, fetch_text=fake_fetch)

                self.assertEqual(1, len(calls))
                self.assertGreaterEqual(len(sources), 1)

    def test_coinbase_live_fetcher_uses_x_roadmap_search_url(self):
        calls = []

        def fake_fetch(url):
            calls.append(url)
            return """
                <article>
                  <a href="/CoinbaseMarkets/status/2057085756120748167">May 30</a>
                  <div>Assets added to the roadmap today: Example Token (EXT) on Base.</div>
                </article>
            """

        sources = fetch_live_sources("coinbase", limit=2, fetch_text=fake_fetch)

        self.assertEqual(["https://x.com/search?q=from%3ACoinbaseMarkets%20roadmap&src=typed_query"], calls)
        self.assertEqual(["2057085756120748167"], [source["external_id"] for source in sources])

    def test_coinbase_live_fetcher_falls_back_to_official_roadmap_blog_when_x_is_blocked(self):
        calls = []
        blog_html = """
        <h2>Roadmap</h2>
        <p>Assets on the Ethereum network (ERC-20 tokens)</p>
        <p>Nexus (NEX) - Contract address: 0xf57D49646621F563b0B905aFc8336923AC569Ec5</p>
        <p>* This is not an exhaustive list of all assets which we have decided to list.</p>
        """

        def fake_fetch(url):
            calls.append(url)
            return "<html>X login wall</html>" if "x.com/search" in url else blog_html

        sources = fetch_live_sources("coinbase", limit=2, fetch_text=fake_fetch)

        self.assertEqual(
            [
                "https://x.com/search?q=from%3ACoinbaseMarkets%20roadmap&src=typed_query",
                "https://www.coinbase.com/zh-cn/blog/increasing-transparency-for-new-asset-listings-on-coinbase",
            ],
            calls,
        )
        self.assertEqual(["NEX"], [source["external_id"] for source in sources])

    def test_coinbase_live_fetcher_falls_back_to_blog_when_x_fetch_raises(self):
        calls = []
        blog_html = """
        <h2>Roadmap</h2>
        <p>Assets on the Ethereum network (ERC-20 tokens)</p>
        <p>Nexus (NEX) - Contract address: 0xf57D49646621F563b0B905aFc8336923AC569Ec5</p>
        <p>* This is not an exhaustive list of all assets which we have decided to list.</p>
        """

        def fake_fetch(url):
            calls.append(url)
            if "x.com/search" in url:
                raise RuntimeError("x login wall")
            return blog_html

        sources = fetch_live_sources("coinbase", limit=2, fetch_text=fake_fetch)

        self.assertEqual(
            [
                "https://x.com/search?q=from%3ACoinbaseMarkets%20roadmap&src=typed_query",
                "https://www.coinbase.com/zh-cn/blog/increasing-transparency-for-new-asset-listings-on-coinbase",
            ],
            calls,
        )
        self.assertEqual(["NEX"], [source["external_id"] for source in sources])
