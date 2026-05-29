import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from exchange_listings.sync import run_manual_sync


class ExchangeListingSyncTests(unittest.TestCase):
    def test_manual_sync_records_run_and_events(self):
        def fake_fetcher(exchange, *, mode, months):
            self.assertEqual("coinbase", exchange)
            self.assertEqual("incremental", mode)
            self.assertEqual(3, months)
            return [
                {
                    "exchange": "coinbase",
                    "source_type": "official_x",
                    "source_url": "https://x.com/CoinbaseMarkets/status/123",
                    "title": "ABC added to roadmap",
                    "raw_text": "ABC Network (ABC) has been added to our listing roadmap.",
                    "published_at": "2026-05-29T00:00:00Z",
                    "fetched_at": "2026-05-29T00:01:00Z",
                }
            ]

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "exchange_listings.sqlite"

            summary = run_manual_sync(db_path, exchanges=["coinbase"], fetcher=fake_fetcher)

            with sqlite3.connect(db_path) as conn:
                sync_runs = conn.execute(
                    "SELECT trigger_type, status, raw_sources_found, events_created, events_updated FROM sync_runs"
                ).fetchall()
                raw_sources = conn.execute(
                    "SELECT exchange, source_type, source_url FROM raw_sources"
                ).fetchall()
                events = conn.execute(
                    "SELECT exchange, token_symbol, event_kind, raw_source_id FROM listing_events"
                ).fetchall()
                exchange_results = conn.execute(
                    """
                    SELECT exchange, source_type, status, sources_found, events_created, events_updated
                    FROM sync_run_exchange_results
                    """
                ).fetchall()

        self.assertTrue(summary["ok"])
        self.assertEqual([("manual", "success", 1, 1, 0)], sync_runs)
        self.assertEqual(
            [("coinbase", "official_x", "https://x.com/CoinbaseMarkets/status/123")],
            raw_sources,
        )
        self.assertEqual([("coinbase", "ABC", "roadmap", 1)], events)
        self.assertEqual([("coinbase", "official_x", "success", 1, 1, 0)], exchange_results)

    def test_cli_accepts_backfill_months_and_db_path(self):
        def empty_fetcher(exchange, *, mode, months):
            return []

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "exchange_listings.sqlite"
            stdout = StringIO()

            import exchange_listing_sync

            with patch("exchange_listing_sync.default_fetcher", empty_fetcher):
                with redirect_stdout(stdout):
                    exit_code = exchange_listing_sync.main(
                        ["--mode", "backfill", "--months", "3", "--db", str(db_path)]
                    )

            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT trigger_type, status, raw_sources_found FROM sync_runs"
                ).fetchone()

        self.assertEqual(0, exit_code)
        self.assertIn('"ok": true', stdout.getvalue())
        self.assertEqual(("scheduled", "success", 0), row)

    def test_sync_records_exchange_failure_without_losing_successful_exchange(self):
        from exchange_listings.sync import run_sync

        def fake_fetcher(exchange, *, mode, months):
            if exchange == "kraken":
                raise RuntimeError("kraken unavailable")
            return [
                {
                    "exchange": "coinbase",
                    "source_type": "official_x",
                    "source_url": "https://x.com/CoinbaseMarkets/status/456",
                    "title": "DEF added to roadmap",
                    "raw_text": "DEF Network (DEF) has been added to our listing roadmap.",
                    "published_at": "2026-05-29T00:00:00Z",
                    "fetched_at": "2026-05-29T00:01:00Z",
                }
            ]

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "exchange_listings.sqlite"

            summary = run_sync(
                db_path,
                trigger_type="scheduled",
                mode="incremental",
                exchanges=["coinbase", "kraken"],
                fetcher=fake_fetcher,
            )

            with sqlite3.connect(db_path) as conn:
                event_count = conn.execute("SELECT COUNT(*) FROM listing_events").fetchone()[0]
                exchange_results = conn.execute(
                    """
                    SELECT exchange, status, sources_found, events_created, error
                    FROM sync_run_exchange_results
                    ORDER BY exchange
                    """
                ).fetchall()

        self.assertTrue(summary["ok"])
        self.assertEqual(1, event_count)
        self.assertEqual(
            [
                ("coinbase", "success", 1, 1, None),
                ("kraken", "failed", 0, 0, "kraken unavailable"),
            ],
            exchange_results,
        )
