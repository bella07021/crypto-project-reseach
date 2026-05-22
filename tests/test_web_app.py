import json
import tempfile
import unittest
from pathlib import Path

from web_app import (
    dashboard_rows,
    exchange_progress,
    exchange_progress_from_cmc,
    parse_score_payload,
    score_payload,
)


class WebAppTests(unittest.TestCase):
    def test_parse_score_payload_normalizes_form_inputs(self):
        payload = parse_score_payload(
            {
                "x_handle": "@NexusLabs",
                "rootdata_url": "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D",
                "bucket": "infra",
                "tge_signals": ["tokenomics", "airdrop"],
                "listing_signals": "binance_alpha",
                "no_live": True,
            }
        )

        self.assertEqual(payload.x_handle, "NexusLabs")
        self.assertEqual(payload.bucket, "infra")
        self.assertEqual(payload.tge_signal, ["tokenomics", "airdrop"])
        self.assertEqual(payload.listing_signal, ["binance_alpha"])
        self.assertTrue(payload.no_live)

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

            result = score_payload(
                {
                    "x_handle": "DemoX",
                    "rootdata_url": "https://rootdata.example/demo",
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
            self.assertIn("total_score", result["assessment"])
            self.assertTrue(workbook.exists())
            json.loads(json.dumps(result))

    def test_dashboard_rows_keep_latest_per_project(self):
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
        self.assertEqual(rows[0]["total_score"], 48.02)
        self.assertEqual(rows[0]["exchange_score"], 26.67)
        self.assertEqual(rows[0]["exchange_raw_score"], 8.0)
        self.assertEqual(rows[0]["listed_exchanges"], ["Coinbase"])

    def test_exchange_progress_sums_matching_exchange_scores(self):
        progress = exchange_progress(
            [
                {"type": "Coinbase", "name": "Coinbase listed Demo"},
                {"type": "Binance 合约", "name": "Binance Futures will launch Demo perpetual"},
                {"type": "TGE", "name": "Demo is live for trading"},
            ]
        )

        self.assertEqual(progress["exchange_raw_score"], 13.0)
        self.assertEqual(progress["exchange_score"], 43.33)
        self.assertEqual(progress["exchange_progress"], 43.33)
        self.assertEqual(progress["listed_exchanges"], ["Coinbase", "BN 合约"])

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
        self.assertEqual(progress["exchange_raw_score"], 30.0)
        self.assertEqual(progress["exchange_score"], 100.0)
        self.assertEqual(progress["listed_exchanges"], ["BN 现货", "Coinbase", "Upbit 韩元现货", "KuCoin", "Bybit"])

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

        self.assertEqual(progress["exchange_raw_score"], 12.5)
        self.assertEqual(progress["exchange_score"], 41.67)
        self.assertEqual(progress["listed_exchanges"], ["Coinbase", "Bitget", "KuCoin", "MEXC", "Kraken"])


if __name__ == "__main__":
    unittest.main()
