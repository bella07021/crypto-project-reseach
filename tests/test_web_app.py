import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from exchange_listings import db as exchange_listing_db
from web_app import (
    append_github_history,
    combined_dashboard_rows,
    create_project_request,
    dashboard_rows,
    request_status_payload,
    request_dashboard_rows,
    delete_project_data,
    exchange_progress,
    exchange_listing_details,
    exchange_progress_from_cmc,
    fetch_cmc_data_api_market_pairs,
    pre_tge_exchange_progress_from_db,
    apply_icodrops_tge_signal,
    project_exchange_progress,
    apply_cmc_chain_override,
    parse_score_payload,
    score_payload,
    handle_post_api,
    ROOT,
)


class WebAppTests(unittest.TestCase):
    def test_exchange_listing_sync_endpoint_delegates(self):
        sync_body = {"exchanges": ["coinbase", "kraken"]}
        score_body = {
            "x_handle": "DemoX",
            "rootdata_url": "https://rootdata.example/demo",
        }

        with patch(
            "web_app.run_exchange_listing_manual_sync",
            return_value={"ok": True, "status": "success"},
        ) as sync_mock, patch(
            "web_app.score_payload",
            return_value={"ok": True, "assessment": {"x_handle": "DemoX"}},
        ) as score_mock:
            sync_status, sync_payload = handle_post_api("/api/exchange-listings/sync", sync_body)
            score_status, score_response = handle_post_api("/api/score", score_body)

        self.assertEqual(sync_status, 200)
        self.assertTrue(sync_payload["ok"])
        sync_mock.assert_called_once_with(sync_body)
        self.assertEqual(score_status, 200)
        self.assertTrue(score_response["ok"])
        score_mock.assert_called_once_with(score_body)

    def test_exchange_listing_manual_sync_wrapper_uses_default_db_path_and_exchanges(self):
        from web_app import run_exchange_listing_manual_sync

        body = {"exchanges": ["coinbase"]}

        with patch("web_app.run_sync", return_value={"ok": True}) as mock:
            result = run_exchange_listing_manual_sync(body)

        self.assertTrue(result["ok"])
        _, kwargs = mock.call_args
        self.assertEqual(ROOT / "data" / "exchange_listings.sqlite", mock.call_args.args[0])
        self.assertEqual("manual", kwargs["trigger_type"])
        self.assertEqual("incremental", kwargs["mode"])
        self.assertEqual(3, kwargs["months"])
        self.assertEqual(["coinbase"], kwargs["exchanges"])
        self.assertIsNotNone(kwargs["fetcher"])

    def test_score_endpoint_delegates_to_score_payload(self):
        body = {
            "x_handle": "DemoX",
            "rootdata_url": "https://rootdata.example/demo",
        }

        with patch("web_app.score_payload", return_value={"ok": True, "assessment": {"x_handle": "DemoX"}}) as mock:
            status, payload = handle_post_api("/api/score", body)

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        mock.assert_called_once_with(body)

    def test_request_endpoint_delegates_to_create_project_request(self):
        body = {
            "x_handle": "DemoX",
            "rootdata_url": "https://rootdata.example/demo",
        }

        with patch("web_app.create_project_request", return_value={"ok": True, "created": True}) as mock:
            status, payload = handle_post_api("/api/request", body)

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        mock.assert_called_once_with(body)

    def test_delete_project_endpoint_delegates_to_delete_project_data(self):
        body = {"token_ticker": "NEX", "project_name": "Nexus"}

        with patch("web_app.delete_project_data", return_value={"ok": True, "deleted_history_count": 1}) as mock:
            status, payload = handle_post_api("/api/project/delete", body)

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        mock.assert_called_once_with(body)

    def test_parse_score_payload_normalizes_form_inputs(self):
        payload = parse_score_payload(
            {
                "x_handle": "@NexusLabs",
                "rootdata_url": "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D",
                "token_ticker": "nex",
                "project_name": "Nexus",
                "bucket": "infra",
                "tge_signals": ["tokenomics", "airdrop"],
                "listing_signals": "binance_alpha",
                "no_live": True,
            }
        )

        self.assertEqual(payload.x_handle, "NexusLabs")
        self.assertEqual(payload.token_ticker, "NEX")
        self.assertEqual(payload.project_name, "Nexus")
        self.assertEqual(payload.bucket, "infra")
        self.assertEqual(payload.tge_signal, ["tokenomics", "airdrop"])
        self.assertEqual(payload.listing_signal, ["binance_alpha"])
        self.assertTrue(payload.no_live)

    def test_parse_score_payload_accepts_x_url(self):
        payload = parse_score_payload(
            {
                "x_handle": "https://x.com/NexusLabs/status/123",
                "rootdata_url": "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D",
            }
        )

        self.assertEqual(payload.x_handle, "NexusLabs")

    def test_parse_score_payload_accepts_rootdata_html(self):
        payload = parse_score_payload(
            {
                "x_handle": "@NexusLabs",
                "rootdata_url": "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D",
                "rootdata_html": "<html>RootData</html>",
            }
        )

        self.assertEqual(payload.rootdata_html, "<html>RootData</html>")

    def test_score_payload_returns_assessment_and_writes_workbook(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            benchmark = tmp_path / "benchmark.csv"
            workbook = tmp_path / "scores.xlsx"
            benchmark.write_text(
                "bucket,project_name,project_url,x_handle,x_followers\n"
                "infra,Demo,https://rootdata.example/demo,DemoX,1000\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
                result = score_payload(
                    {
                        "x_handle": "DemoX",
                        "rootdata_url": "https://rootdata.example/demo",
                        "token_ticker": "demo",
                        "project_name": "Demo Project",
                        "team_raw_score": "80",
                        "team_background": "international",
                        "funding_amount_usd": "500000000",
                        "funding_date": "2026-05-01",
                        "bucket": "infra",
                        "no_live": True,
                        "benchmark_csv": str(benchmark),
                        "workbook": str(workbook),
                        "today": "2026-05-22",
                    }
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["assessment"]["x_handle"], "DemoX")
            self.assertEqual(result["assessment"]["token_ticker"], "DEMO")
            self.assertEqual(result["assessment"]["project_name"], "Demo Project")
            self.assertIn("total_score", result["assessment"])
            self.assertTrue(workbook.exists())
            json.loads(json.dumps(result))

    def test_dashboard_rows_keep_latest_per_project(self):
        with patch(
            "web_app.pre_tge_exchange_progress_from_db",
            return_value={
                "pre_tge_exchange_score": 10.0,
                "pre_tge_exchange_source": "exchange_listings_db",
                "pre_tge_listing_signals": [],
            },
        ):
            rows = dashboard_rows(
                [
                    {
                        "assessed_at": "2026-05-22T01:00:00Z",
                        "token_ticker": "NEX",
                        "rootdata_url": "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D",
                        "total_score": 0,
                    },
                    {
                        "assessed_at": "2026-05-22T02:00:00Z",
                        "token_ticker": "NEX",
                        "rootdata_url": "https://www.rootdata.com/Projects/detail/Nexus?k=MTE3NDI%3D",
                        "total_score": 48.02,
                        "team_score": 85,
                        "funding_score": 2.5,
                        "social_score": 71.73,
                        "tge_status": "已 TGE",
                        "roadmap_events": [{"type": "Coinbase", "date": "2026-05-20"}],
                    },
                ]
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["token_ticker"], "NEX")
        self.assertEqual(rows[0]["total_score"], 37.6)
        self.assertEqual(rows[0]["exchange_score"], 95.0)
        self.assertEqual(rows[0]["exchange_raw_score"], 95.0)
        self.assertEqual(rows[0]["pre_tge_exchange_score"], 10.0)
        self.assertEqual(rows[0]["pre_tge_exchange_source"], "exchange_listings_db")
        self.assertEqual(rows[0]["listed_exchanges"], ["Coinbase"])

    def test_dashboard_rows_backfills_investor_highlights_for_cached_scores(self):
        rows = dashboard_rows(
            [
                {
                    "assessed_at": "2026-06-02T09:44:22+00:00",
                    "token_ticker": "BILL",
                    "project_name": "Billions Network",
                    "x_handle": "billions_ntwk",
                    "rootdata_url": "https://cn.rootdata.com/projects/detail/Billions?k=M",
                    "total_score": 91.93,
                    "investors": ["Founders Fund*", "Delphi", "dao5", "Nick White"],
                }
            ]
        )

        self.assertEqual(rows[0]["assessment"]["investor_highlights"], ["Founders Fund*", "Delphi"])

    def test_dashboard_rows_refreshes_cached_cmc_exchange_progress(self):
        with patch(
            "web_app.project_exchange_progress",
            return_value={
                "exchange_score": 95.0,
                "exchange_progress": 95.0,
                "exchange_raw_score": 95.0,
                "pre_tge_exchange_score": 95.0,
                "exchange_source": "CoinMarketCap Data API",
                "listed_exchanges": ["Coinbase", "Bithumb 韩元现货", "Kraken", "BN 合约"],
            },
        ), patch(
            "web_app.pre_tge_exchange_progress_from_db",
            return_value={
                "pre_tge_exchange_score": 95.0,
                "pre_tge_exchange_source": "exchange_listings_db",
                "pre_tge_listing_signals": [],
            },
        ):
            rows = dashboard_rows(
                [
                    {
                        "assessed_at": "2026-06-03T01:00:00Z",
                        "token_ticker": "BILL",
                        "project_name": "Billions Network",
                        "rootdata_url": "https://www.rootdata.com/Projects/detail/Billions-Network?k=1",
                        "exchange_score": 95.0,
                        "exchange_source": "CoinMarketCap Web",
                        "listed_exchanges": ["Coinbase", "Bybit", "Bitget", "KuCoin", "MEXC"],
                    }
                ]
            )

        self.assertEqual(rows[0]["exchange_source"], "CoinMarketCap Data API")
        self.assertEqual(rows[0]["listed_exchanges"], ["Coinbase", "Bithumb 韩元现货", "Kraken", "BN 合约"])

    def test_exchange_progress_uses_quality_tiers_and_ignores_alpha(self):
        progress = exchange_progress(
            [
                {"type": "Coinbase", "name": "Coinbase listed Demo"},
                {"type": "Binance 合约", "name": "Binance Futures will launch Demo perpetual"},
                {"type": "Binance Alpha", "name": "Binance Alpha Airdrop"},
                {"type": "TGE", "name": "Demo is live for trading"},
            ]
        )

        self.assertEqual(progress["exchange_raw_score"], 95.0)
        self.assertEqual(progress["exchange_score"], 95.0)
        self.assertEqual(progress["exchange_progress"], 95.0)
        self.assertEqual(progress["listed_exchanges"], ["Coinbase", "BN 合约"])

    def test_exchange_listing_details_attach_timing_from_roadmap_events(self):
        details = exchange_listing_details(
            {
                "listed_exchanges": ["Upbit 韩元现货", "Bithumb 韩元现货", "Bitget", "Gate", "MEXC"],
                "roadmap_events": [
                    {"type": "TGE", "date": "2026-05-20"},
                    {"type": "韩所", "name": "Upbit listed Demo", "date": "2026-05-22", "days_after_tge": 2},
                    {"type": "Gate", "name": "Gate listed Demo", "date": "2026-05-24"},
                ],
            }
        )

        self.assertEqual(details[0], {"exchange": "Upbit 韩元现货", "listed_at": "2026-05-22", "days_after_tge": 2})
        self.assertEqual(details[1], {"exchange": "Bithumb 韩元现货", "listed_at": "", "days_after_tge": None})
        self.assertEqual(details[2], {"exchange": "Bitget", "listed_at": "", "days_after_tge": None})
        self.assertEqual(details[3], {"exchange": "Gate", "listed_at": "2026-05-24", "days_after_tge": 4})

    def test_exchange_listing_details_attach_timing_from_listing_signals(self):
        details = exchange_listing_details(
            {
                "tge_date": "2026-05-04",
                "listed_exchanges": ["Coinbase", "BN 合约", "BN 现货", "Upbit 韩元现货", "Bithumb 韩元现货"],
                "roadmap_events": [],
                "pre_tge_listing_signals": [
                    {
                        "exchange": "Coinbase",
                        "announcement_published_at": "2026-05-03T08:00:00Z",
                        "trading_start_time": "2026-05-04T08:00:00Z",
                    },
                    {
                        "exchange": "BN 合约",
                        "announcement_published_at": "2026-05-04T00:00:00Z",
                        "trading_start_time": "2026-05-05T00:00:00Z",
                    },
                    {
                        "exchange": "BN 现货",
                        "announcement_published_at": "2026-05-06T00:00:00Z",
                    },
                    {
                        "exchange": "Upbit 韩元现货",
                        "trading_start_time": "2026-05-06T00:00:00Z",
                    },
                    {
                        "exchange": "Bithumb 韩元现货",
                        "trading_start_time": "2026-05-28T08:00:00Z",
                    },
                ],
            }
        )

        self.assertEqual(details[0], {"exchange": "Coinbase", "listed_at": "2026-05-04", "days_after_tge": 0})
        self.assertEqual(details[1], {"exchange": "BN 合约", "listed_at": "2026-05-05", "days_after_tge": 1})
        self.assertEqual(details[2], {"exchange": "BN 现货", "listed_at": "2026-05-06", "days_after_tge": 2})
        self.assertEqual(details[3], {"exchange": "Upbit 韩元现货", "listed_at": "2026-05-06", "days_after_tge": 2})
        self.assertEqual(details[4], {"exchange": "Bithumb 韩元现货", "listed_at": "2026-05-28", "days_after_tge": 24})

    def test_exchange_listing_details_uses_binance_futures_onboard_date(self):
        with patch("web_app.fetch_binance_futures_onboard_date", return_value="2026-05-07"):
            details = exchange_listing_details(
                {
                    "token_ticker": "BILL",
                    "tge_date": "2026-05-04",
                    "listed_exchanges": ["BN 合约"],
                    "roadmap_events": [],
                    "pre_tge_listing_signals": [],
                }
            )

        self.assertEqual(details[0], {"exchange": "BN 合约", "listed_at": "2026-05-07", "days_after_tge": 3})

    def test_exchange_progress_from_cmc_pairs_uses_cmc_exchange_names(self):
        progress = exchange_progress_from_cmc(
            [
                {
                    "exchange": {"name": "Binance", "slug": "binance"},
                    "market_pair": "DEMO/USDT",
                    "category": "spot",
                    "source": "CoinMarketCap Web",
                },
                {
                    "exchange": {"name": "Coinbase Exchange", "slug": "coinbase-exchange"},
                    "market_pair": "DEMO/USD",
                    "category": "spot",
                },
                {
                    "exchange": {"name": "Upbit", "slug": "upbit"},
                    "market_pair": "DEMO/KRW",
                    "category": "spot",
                },
                {
                    "exchange": {"name": "KuCoin", "slug": "kucoin"},
                    "market_pair": "DEMO/USDT",
                    "category": "spot",
                },
                {
                    "exchange": {"name": "Bybit", "slug": "bybit"},
                    "market_pair": "DEMO/USDT",
                    "category": "spot",
                },
                {
                    "exchange": {"name": "Binance Alpha", "slug": "binance-alpha"},
                    "market_pair": "DEMO/USDT",
                    "category": "spot",
                },
            ]
        )

        self.assertEqual(progress["exchange_source"], "CoinMarketCap Web")
        self.assertEqual(progress["exchange_raw_score"], 95.0)
        self.assertEqual(progress["exchange_score"], 95.0)
        self.assertEqual(progress["listed_exchanges"], ["Coinbase", "Upbit 韩元现货", "BN 现货", "Bybit", "KuCoin"])

    def test_pre_tge_exchange_progress_reads_exchange_listing_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "exchange_listings.sqlite"
            exchange_listing_db.init_db(db_path)
            with exchange_listing_db.connect(db_path) as conn:
                asset_id = exchange_listing_db.upsert_normalized_asset(
                    conn,
                    {
                        "symbol": "NEX",
                        "project_name": "Nexus",
                        "identity_confidence": "high",
                    },
                )
                exchange_listing_db.upsert_listing_event(
                    conn,
                    {
                        "exchange": "coinbase",
                        "normalized_asset_id": asset_id,
                        "project_name": "Nexus",
                        "token_symbol": "NEX",
                        "listing_type": "spot",
                        "event_family": "spot_listing",
                        "event_kind": "roadmap",
                        "status": "TBD",
                        "announcement_url": "https://x.com/CoinbaseMarkets/status/1",
                        "announcement_title": "Coinbase roadmap: Nexus (NEX)",
                        "announcement_published_at": "2026-05-28T00:00:00Z",
                        "source_type": "official_x",
                        "source_precedence": 10,
                    },
                )

            progress = pre_tge_exchange_progress_from_db(
                {"token_ticker": "NEX", "project_name": "Nexus"},
                db_path,
            )

        self.assertEqual(progress["pre_tge_exchange_source"], "exchange_listings_db")
        self.assertEqual(progress["pre_tge_exchange_score"], 95.0)
        self.assertEqual(progress["pre_tge_listing_signals"][0]["exchange"], "Coinbase")
        self.assertEqual(progress["pre_tge_listing_signals"][0]["announcement_title"], "Coinbase roadmap: Nexus (NEX)")

    def test_pre_tge_exchange_progress_reads_binance_perpetual_listing_database_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "exchange_listings.sqlite"
            exchange_listing_db.init_db(db_path)
            with exchange_listing_db.connect(db_path) as conn:
                asset_id = exchange_listing_db.upsert_normalized_asset(
                    conn,
                    {
                        "symbol": "BILL",
                        "project_name": "Billions Network",
                        "identity_confidence": "high",
                    },
                )
                exchange_listing_db.upsert_listing_event(
                    conn,
                    {
                        "exchange": "binance",
                        "normalized_asset_id": asset_id,
                        "project_name": "Billions Network",
                        "token_symbol": "BILL",
                        "listing_type": "perpetual",
                        "event_family": "futures_listing",
                        "event_kind": "futures_listing",
                        "status": "trading_started",
                        "announcement_url": "https://www.binance.com/en/support/announcement/1",
                        "announcement_title": "Binance Futures Will Launch BILLUSDT Perpetual Contract",
                        "announcement_published_at": "2026-05-04T00:00:00Z",
                        "trading_start_time": "2026-05-05T10:00:00Z",
                        "source_type": "exchange_announcement",
                        "source_precedence": 30,
                    },
                )

            progress = pre_tge_exchange_progress_from_db(
                {"token_ticker": "BILL", "project_name": "Billions Network"},
                db_path,
            )

        self.assertEqual(progress["pre_tge_listing_signals"][0]["exchange"], "BN 合约")
        self.assertEqual(progress["pre_tge_listing_signals"][0]["listing_type"], "perpetual")
        self.assertEqual(progress["pre_tge_listing_signals"][0]["trading_start_time"], "2026-05-05T10:00:00Z")

    def test_mainstream_spot_exchange_tier_scores_once(self):
        progress = exchange_progress_from_cmc(
            [
                {"exchange": {"name": "Bitget", "slug": "bitget"}, "market_pair": "NEX/USDT", "category": "spot"},
                {"exchange": {"name": "KuCoin", "slug": "kucoin"}, "market_pair": "NEX/USDT", "category": "spot"},
                {"exchange": {"name": "MEXC", "slug": "mexc"}, "market_pair": "NEX/USDT", "category": "spot"},
                {"exchange": {"name": "Kraken", "slug": "kraken"}, "market_pair": "NEX/USD", "category": "spot"},
                {"exchange": {"name": "Coinbase Exchange", "slug": "coinbase-exchange"}, "market_pair": "NEX/USD", "category": "spot"},
            ]
        )

        self.assertEqual(progress["exchange_raw_score"], 95.0)
        self.assertEqual(progress["exchange_score"], 95.0)
        self.assertEqual(progress["listed_exchanges"], ["Coinbase", "Kraken", "Bitget", "KuCoin", "MEXC"])

    def test_cmc_progress_includes_second_page_spot_and_binance_perpetual(self):
        progress = exchange_progress_from_cmc(
            [
                {"exchange": {"name": "Kraken", "slug": "kraken"}, "market_pair": "BILL/USD", "category": "spot"},
                {"exchange": {"name": "Bithumb", "slug": "bithumb"}, "market_pair": "BILL/KRW", "category": "spot"},
                {"exchange": {"name": "Binance", "slug": "binance"}, "market_pair": "BILL/USDT", "category": "perpetual"},
                {"exchange": {"name": "Binance Alpha", "slug": "binance-alpha"}, "market_pair": "BILL/USDT", "category": "spot"},
            ]
        )

        self.assertEqual(progress["exchange_score"], 95.0)
        self.assertEqual(progress["listed_exchanges"], ["Bithumb 韩元现货", "Kraken", "BN 合约"])

    def test_cmc_data_api_market_pairs_fetches_multiple_pages(self):
        captured_starts = []

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def fake_urlopen(request, timeout=0, context=None):
            query = __import__("urllib.parse").parse.parse_qs(__import__("urllib.parse").parse.urlparse(request.full_url).query)
            start = int(query["start"][0])
            captured_starts.append(start)
            if start == 1:
                page_pairs = [
                    {
                        "exchangeName": "Bithumb",
                        "exchangeSlug": "bithumb",
                        "marketPair": "BILL/KRW",
                        "category": "spot",
                    }
                ] * 100
            else:
                page_pairs = [
                    {
                        "exchangeName": "Binance",
                        "exchangeSlug": "binance",
                        "marketPair": "BILL/USDT",
                        "category": "perpetual",
                    }
                ]
            return FakeResponse({"data": {"numMarketPairs": 101, "marketPairs": page_pairs}})

        with patch("web_app.urlopen", fake_urlopen):
            pairs = fetch_cmc_data_api_market_pairs("billions-network", "BILL")

        self.assertEqual(captured_starts, [1, 101])
        self.assertEqual(pairs[0]["exchange"], {"name": "Bithumb", "slug": "bithumb"})
        self.assertEqual(pairs[-1]["market_pair"], "BILL/USDT")
        self.assertEqual(pairs[-1]["category"], "perpetual")

    def test_project_exchange_progress_does_not_filter_cmc_by_project_name_when_ticker_missing(self):
        captured = {}

        def fake_fetch(project_name, token_ticker):
            captured["project_name"] = project_name
            captured["token_ticker"] = token_ticker
            return [
                {"exchange": {"name": "Bitget", "slug": "bitget"}, "market_pair": "SLX/USDT", "category": "spot"},
                {"exchange": {"name": "Gate", "slug": "gate"}, "market_pair": "SLX/USDT", "category": "spot"},
                {"exchange": {"name": "MEXC", "slug": "mexc"}, "market_pair": "SLX/USDT", "category": "spot"},
            ]

        with patch("web_app.fetch_cmc_web_market_pairs", side_effect=fake_fetch):
            progress = project_exchange_progress({"project_name": "Solstice", "token_ticker": ""})

        self.assertEqual(captured["project_name"], "Solstice")
        self.assertEqual(captured["token_ticker"], "")
        self.assertEqual(progress["exchange_raw_score"], 40.0)
        self.assertEqual(progress["exchange_score"], 40.0)
        self.assertEqual(progress["listed_exchanges"], ["Bitget", "Gate", "MEXC"])

    def test_apply_cmc_chain_override_uses_token_platforms_over_rootdata_text(self):
        with patch(
            "web_app.fetch_cmc_token_detail",
            return_value={
                "symbol": "NEX",
                "platforms": [
                    {"contractPlatform": "Ethereum"},
                    {"contractPlatform": "BNB Smart Chain (BEP20)"},
                ],
            },
        ):
            assessment = apply_cmc_chain_override(
                {
                    "project_name": "Nexus",
                    "token_ticker": "NEX",
                    "chains": ["Base", "Linea", "Scroll"],
                    "chain_score": 100,
                    "evidence_notes": ["Chains: Base, Linea, Scroll"],
                }
            )

        self.assertEqual(assessment["chains"], ["Ethereum", "BNB Chain"])
        self.assertEqual(assessment["chain_score"], 85.0)
        self.assertEqual(assessment["evidence_notes"], ["CMC chains: Ethereum, BNB Chain"])

    def test_apply_icodrops_tge_signal_marks_binance_alpha_airdrop_as_tge(self):
        assessment = {
            "tge_status": "未 TGE",
            "tge_probability": 80,
            "tge_method": "未 TGE",
            "tge_date": "",
            "listed_exchanges": ["Bitget"],
            "evidence_notes": [],
        }
        html = """
        <h1>Solstice</h1>
        <h2>Binance Alpha Airdrop</h2>
        <p>Active from <span>May</span> <strong>25,</strong> 2026</p>
        <h2>TGE and Distribution</h2>
        <p>Upcoming</p>
        """

        apply_icodrops_tge_signal(assessment, html, "https://icodrops.com/solstice/")

        self.assertEqual(assessment["tge_status"], "已 TGE")
        self.assertEqual(assessment["tge_probability"], 100)
        self.assertEqual(assessment["tge_method"], "Binance Alpha Airdrop")
        self.assertEqual(assessment["tge_date"], "2026-05-25")
        self.assertEqual(
            assessment["tge_evidence_links"],
            [{"text": "Binance Alpha Airdrop active from May 25, 2026", "url": "https://icodrops.com/solstice/"}],
        )

    def test_apply_icodrops_tge_signal_without_exchange_stays_untge(self):
        assessment = {
            "tge_status": "未 TGE",
            "tge_probability": 80,
            "tge_method": "未 TGE",
            "tge_date": "",
            "listed_exchanges": [],
            "evidence_notes": [],
        }
        html = """
        <h1>Citrea</h1>
        <h2>Binance Alpha Airdrop</h2>
        <p>Active from May 26, 2026</p>
        """

        apply_icodrops_tge_signal(assessment, html, "https://icodrops.com/citrea/")

        self.assertEqual(assessment["tge_status"], "未 TGE")
        self.assertEqual(assessment["tge_probability"], 95)
        self.assertEqual(assessment["tge_method"], "Binance Alpha Airdrop")
        self.assertEqual(assessment["tge_date"], "2026-05-26")

    def test_apply_icodrops_tge_signal_keeps_upcoming_distribution_without_airdrop_untge(self):
        assessment = {
            "tge_status": "未 TGE",
            "tge_probability": 80,
            "tge_method": "未 TGE",
            "tge_date": "",
            "evidence_notes": [],
        }
        html = "<h2>TGE and Distribution</h2><p>Upcoming</p>"

        apply_icodrops_tge_signal(assessment, html, "https://icodrops.com/demo/")

        self.assertEqual(assessment["tge_status"], "未 TGE")
        self.assertEqual(assessment["tge_probability"], 80)
        self.assertEqual(assessment["tge_method"], "未 TGE")

    def test_apply_icodrops_tge_signal_uses_known_date_when_active_date_missing(self):
        assessment = {
            "project_name": "Solstice",
            "tge_status": "未 TGE",
            "tge_probability": 80,
            "tge_method": "未 TGE",
            "tge_date": "",
            "evidence_notes": [],
        }
        html = "<h2>Binance Alpha Airdrop</h2>"

        apply_icodrops_tge_signal(assessment, html, "https://icodrops.com/solstice/")

        self.assertEqual(assessment["tge_date"], "2026-05-25")

    def test_dashboard_rows_backfills_known_icodrops_airdrop_date(self):
        rows = dashboard_rows(
            [
                {
                    "project_name": "Solstice",
                    "x_handle": "solsticefi",
                    "rootdata_url": "https://cn.rootdata.com/projects/detail/Solstice?k=MTQ0NjI%3D",
                    "total_score": 46.1,
                    "tge_status": "已 TGE",
                    "tge_method": "Binance Alpha Airdrop",
                    "tge_date": "",
                    "listed_exchanges": ["Bitget"],
                    "tge_evidence_links": [
                        {"text": "Binance Alpha Airdrop", "url": "https://icodrops.com/solstice/"}
                    ],
                }
            ]
        )

        self.assertEqual(rows[0]["tge_date"], "2026-05-25")
        self.assertEqual(rows[0]["assessment"]["tge_date"], "2026-05-25")

    def test_dashboard_rows_downgrades_tge_without_exchange_and_prunes_foreign_x_links(self):
        rows = dashboard_rows(
            [
                {
                    "project_name": "Citrea",
                    "x_handle": "citrea_xyz",
                    "rootdata_url": "https://cn.rootdata.com/projects/detail/Citrea?k=MTEyNTk%3D",
                    "total_score": 50,
                    "tge_status": "已 TGE",
                    "tge_probability": 100,
                    "tge_method": "Binance Alpha Airdrop",
                    "tge_date": "2026-05-26",
                    "listed_exchanges": [],
                    "tge_evidence_links": [
                        {"text": "出现代币经济模型相关表述", "url": "https://x.com/other_project/status/1"},
                        {"text": "Binance Alpha Airdrop", "url": "https://icodrops.com/citrea/"},
                    ],
                }
            ]
        )

        self.assertEqual(rows[0]["tge_status"], "未 TGE")
        self.assertEqual(rows[0]["tge_probability"], 95)
        self.assertEqual(rows[0]["assessment"]["tge_status"], "未 TGE")
        self.assertEqual(
            rows[0]["assessment"]["tge_evidence_links"],
            [{"text": "Binance Alpha Airdrop", "url": "https://icodrops.com/citrea/"}],
        )

    def test_github_history_append_creates_contents_payload(self):
        captured = {}

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def fake_urlopen(request, timeout=0, context=None):
            if request.get_method() == "GET":
                raise OSError("not found")
            captured["url"] = request.full_url
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse({"content": {}, "sha": "new-sha"})

        with patch.dict(
            os.environ,
            {
                "GITHUB_TOKEN": "token",
                "GITHUB_REPO_OWNER": "bella07021",
                "GITHUB_REPO_NAME": "crypto-project-reseach",
                "GITHUB_BRANCH": "main",
                "GITHUB_HISTORY_PATH": "data/project_scores.jsonl",
            },
        ), patch("web_app.urlopen", fake_urlopen):
            rows = append_github_history({"x_handle": "DemoX", "token_ticker": "DEMO"})

        self.assertEqual(rows, [{"x_handle": "DemoX", "token_ticker": "DEMO"}])
        self.assertEqual(captured["payload"]["branch"], "main")
        decoded = __import__("base64").b64decode(captured["payload"]["content"]).decode("utf-8")
        self.assertIn('"token_ticker": "DEMO"', decoded)
        self.assertIn("/repos/bella07021/crypto-project-reseach/contents/data/project_scores.jsonl", captured["url"])

    def test_delete_project_data_removes_local_history_and_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workbook = tmp_path / "scores.xlsx"
            history_path = workbook.with_suffix(".jsonl")
            request_path = tmp_path / "requests.jsonl"
            history_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "token_ticker": "NEX",
                                "project_name": "Nexus",
                                "x_handle": "NexusLabs",
                                "rootdata_url": "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D",
                            }
                        ),
                        json.dumps(
                            {
                                "token_ticker": "SUI",
                                "project_name": "Sui",
                                "x_handle": "SuiNetwork",
                                "rootdata_url": "https://cn.rootdata.com/projects/detail/Sui?k=Mjc5Nw%3D%3D",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            request_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "request_id": "nex",
                                "token_ticker": "NEX",
                                "project_name": "Nexus",
                                "rootdata_url": "https://www.rootdata.com/Projects/detail/Nexus?k=MTE3NDI%3D",
                            }
                        ),
                        json.dumps(
                            {
                                "request_id": "sui",
                                "token_ticker": "SUI",
                                "project_name": "Sui",
                                "rootdata_url": "https://cn.rootdata.com/projects/detail/Sui?k=Mjc5Nw%3D%3D",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "GITHUB_TOKEN": "",
                    "GITHUB_REQUESTS_PATH": str(request_path),
                },
            ):
                result = delete_project_data(
                    {
                        "token_ticker": "NEX",
                        "project_name": "Nexus",
                        "rootdata_url": "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D",
                    },
                    workbook,
                )

            history_rows = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines()]
            request_rows = [json.loads(line) for line in request_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(result["deleted_history_count"], 1)
        self.assertEqual(result["deleted_request_count"], 1)
        self.assertEqual([row["token_ticker"] for row in history_rows], ["SUI"])
        self.assertEqual([row["request_id"] for row in request_rows], ["sui"])

    def test_create_project_request_deduplicates_active_rootdata_url(self):
        writes = []
        existing = {
            "request_id": "abc123",
            "status": "pending",
            "x_handle": "NexusLabs",
            "rootdata_url": "https://www.rootdata.com/Projects/detail/Nexus?k=MTE3NDI%3D",
            "request_key": "rootdata.com/projects/detail/nexus?k=mte3ndi%3d",
        }

        with patch("web_app.read_github_requests_with_sha", return_value=([existing], "sha")), patch(
            "web_app.write_github_requests",
            side_effect=lambda rows, message, sha=None: writes.append((rows, message, sha)),
        ):
            result = create_project_request(
                {
                    "x_handle": "@NexusLabs",
                    "rootdata_url": "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D",
                }
            )

        self.assertFalse(result["created"])
        self.assertEqual(result["request"]["request_id"], "abc123")
        self.assertEqual(writes, [])

    def test_create_project_request_appends_pending_request(self):
        writes = []

        with patch("web_app.read_github_requests_with_sha", return_value=([], None)), patch(
            "web_app.write_github_requests",
            side_effect=lambda rows, message, sha=None: writes.append((rows, message, sha)),
        ):
            result = create_project_request(
                {
                    "x_handle": "@NexusLabs",
                    "rootdata_url": "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D",
                    "token_ticker": "nex",
                    "project_name": "Nexus",
                }
            )

        self.assertTrue(result["created"])
        self.assertEqual(result["request"]["status"], "pending")
        self.assertEqual(result["request"]["x_handle"], "NexusLabs")
        self.assertEqual(result["request"]["token_ticker"], "NEX")
        self.assertEqual(result["request"]["project_name"], "Nexus")
        self.assertEqual(len(writes[0][0]), 1)
        self.assertEqual(writes[0][1], "Add project request for NEX")

    def test_create_project_request_refreshes_completed_project_with_new_request_id(self):
        writes = []
        existing = {
            "request_id": "old_done",
            "status": "done",
            "x_handle": "NexusLabs",
            "rootdata_url": "https://www.rootdata.com/Projects/detail/Nexus?k=MTE3NDI%3D",
            "request_key": "rootdata.com/projects/detail/nexus?k=mte3ndi%3d",
            "requested_at": "2026-05-24T08:00:00+00:00",
        }

        with patch("web_app.read_github_requests_with_sha", return_value=([existing], "sha")), patch(
            "web_app.write_github_requests",
            side_effect=lambda rows, message, sha=None: writes.append((rows, message, sha)),
        ):
            result = create_project_request(
                {
                    "x_handle": "@NexusLabs",
                    "rootdata_url": "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D",
                }
            )

        self.assertTrue(result["created"])
        self.assertNotEqual(result["request"]["request_id"], "old_done")
        self.assertEqual(result["request"]["status"], "pending")
        self.assertEqual(len(writes[0][0]), 2)

    def test_request_dashboard_rows_excludes_projects_with_scores(self):
        requests = [
            {
                "request_id": "pending1",
                "status": "pending",
                "token_ticker": "NEW",
                "project_name": "New Project",
                "x_handle": "NewProject",
                "rootdata_url": "https://cn.rootdata.com/projects/detail/New?k=MQ%3D%3D",
                "requested_at": "2026-05-25T09:00:00+00:00",
            },
            {
                "request_id": "done1",
                "status": "pending",
                "x_handle": "NexusLabs",
                "rootdata_url": "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D",
                "requested_at": "2026-05-25T08:00:00+00:00",
            },
        ]
        history = [
            {
                "x_handle": "NexusLabs",
                "rootdata_url": "https://www.rootdata.com/Projects/detail/Nexus?k=MTE3NDI%3D",
                "token_ticker": "NEX",
            }
        ]

        rows = request_dashboard_rows(requests, history)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["request_status"], "pending")
        self.assertEqual(rows[0]["token_ticker"], "NEW")
        self.assertEqual(rows[0]["project_name"], "New Project")

    def test_request_dashboard_rows_shows_active_refresh_request_over_old_score(self):
        requests = [
            {
                "request_id": "refresh1",
                "status": "processing",
                "token_ticker": "BILL",
                "project_name": "Billions Network",
                "x_handle": "billions_ntwk",
                "rootdata_url": "https://cn.rootdata.com/projects/detail/Billions?k=M",
                "requested_at": "2026-06-02T09:00:00+00:00",
            }
        ]
        history = [
            {
                "x_handle": "billions_ntwk",
                "rootdata_url": "https://cn.rootdata.com/projects/detail/Billions?k=M",
                "token_ticker": "BILL",
                "project_name": "Billions Network",
                "total_score": 55.19,
                "assessed_at": "2026-06-02T08:00:00+00:00",
            }
        ]

        rows = request_dashboard_rows(requests, history)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["request_status"], "processing")
        self.assertEqual(rows[0]["token_ticker"], "BILL")
        self.assertEqual(rows[0]["total_score"], "")

    def test_request_status_payload_returns_done_assessment(self):
        requests = [
            {
                "request_id": "req1",
                "status": "done",
                "x_handle": "NexusLabs",
                "rootdata_url": "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D",
            }
        ]
        history = [
            {
                "x_handle": "NexusLabs",
                "rootdata_url": "https://www.rootdata.com/Projects/detail/Nexus?k=MTE3NDI%3D",
                "token_ticker": "NEX",
                "total_score": 48.02,
            }
        ]

        payload = request_status_payload("req1", requests, history)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["request"]["status"], "done")
        self.assertEqual(payload["assessment"]["token_ticker"], "NEX")

    def test_request_status_payload_overrides_assessment_identity_from_request(self):
        requests = [
            {
                "request_id": "req1",
                "status": "done",
                "token_ticker": "CTR",
                "project_name": "Citrea",
                "x_handle": "citrea_xyz",
                "rootdata_url": "https://cn.rootdata.com/projects/detail/Citrea?k=MTEyNTk%3D",
            }
        ]
        history = [
            {
                "x_handle": "citrea_xyz",
                "rootdata_url": "https://www.rootdata.com/Projects/detail/Citrea?k=MTEyNTk%3D",
                "project_name": "Citrea",
                "token_ticker": "",
                "total_score": 35.88,
            }
        ]

        payload = request_status_payload("req1", requests, history)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["assessment"]["token_ticker"], "CTR")
        self.assertEqual(payload["assessment"]["project_name"], "Citrea")

    def test_combined_dashboard_rows_overrides_history_identity_from_done_request(self):
        rows = combined_dashboard_rows(
            [
                {
                    "x_handle": "citrea_xyz",
                    "rootdata_url": "https://www.rootdata.com/Projects/detail/Citrea?k=MTEyNTk%3D",
                    "project_name": "Citrea",
                    "token_ticker": "",
                    "total_score": 35.88,
                }
            ],
            [
                {
                    "request_id": "req1",
                    "status": "done",
                    "token_ticker": "CTR",
                    "project_name": "Citrea",
                    "x_handle": "citrea_xyz",
                    "rootdata_url": "https://cn.rootdata.com/projects/detail/Citrea?k=MTEyNTk%3D",
                }
            ],
        )

        self.assertEqual(rows[0]["token_ticker"], "CTR")
        self.assertEqual(rows[0]["project_name"], "Citrea")
        self.assertEqual(rows[0]["assessment"]["token_ticker"], "CTR")

    def test_request_status_payload_does_not_return_old_assessment_for_refresh_request(self):
        requests = [
            {
                "request_id": "refresh1",
                "status": "pending",
                "x_handle": "NexusLabs",
                "rootdata_url": "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D",
                "requested_at": "2026-05-26T08:00:00+00:00",
            }
        ]
        history = [
            {
                "x_handle": "NexusLabs",
                "rootdata_url": "https://www.rootdata.com/Projects/detail/Nexus?k=MTE3NDI%3D",
                "token_ticker": "NEX",
                "total_score": 48.02,
                "assessed_at": "2026-05-25T08:00:00+00:00",
            }
        ]

        payload = request_status_payload("refresh1", requests, history)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["request"]["status"], "pending")
        self.assertIsNone(payload["assessment"])

    def test_request_status_payload_returns_newer_assessment_for_refresh_request(self):
        requests = [
            {
                "request_id": "refresh1",
                "status": "processing",
                "x_handle": "NexusLabs",
                "rootdata_url": "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D",
                "requested_at": "2026-05-26T08:00:00+00:00",
            }
        ]
        history = [
            {
                "x_handle": "NexusLabs",
                "rootdata_url": "https://www.rootdata.com/Projects/detail/Nexus?k=MTE3NDI%3D",
                "token_ticker": "NEX",
                "total_score": 49.50,
                "assessed_at": "2026-05-26T08:01:00+00:00",
            }
        ]

        payload = request_status_payload("refresh1", requests, history)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["request"]["status"], "done")
        self.assertEqual(payload["assessment"]["total_score"], 49.50)


if __name__ == "__main__":
    unittest.main()
