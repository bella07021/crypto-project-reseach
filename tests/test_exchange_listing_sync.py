import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from exchange_listings import db
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
        self.assertEqual(("backfill", "success", 0), row)

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

    def test_second_identical_sync_reports_listing_event_noop(self):
        from exchange_listings.sync import run_sync

        def fake_fetcher(exchange, *, mode, months):
            return [
                {
                    "exchange": "coinbase",
                    "source_type": "official_x",
                    "source_url": "https://x.com/CoinbaseMarkets/status/789",
                    "title": "GHI added to roadmap",
                    "raw_text": "GHI Network (GHI) has been added to our listing roadmap.",
                    "published_at": "2026-05-29T00:00:00Z",
                    "fetched_at": "2026-05-29T00:01:00Z",
                }
            ]

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "exchange_listings.sqlite"
            first = run_sync(
                db_path,
                trigger_type="manual",
                mode="incremental",
                exchanges=["coinbase"],
                fetcher=fake_fetcher,
            )
            second = run_sync(
                db_path,
                trigger_type="manual",
                mode="incremental",
                exchanges=["coinbase"],
                fetcher=fake_fetcher,
            )

            with sqlite3.connect(db_path) as conn:
                runs = conn.execute(
                    """
                    SELECT events_created, events_updated
                    FROM sync_runs
                    ORDER BY id
                    """
                ).fetchall()
                exchange_results = conn.execute(
                    """
                    SELECT events_created, events_updated
                    FROM sync_run_exchange_results
                    ORDER BY id
                    """
                ).fetchall()
                event_count = conn.execute("SELECT COUNT(*) FROM listing_events").fetchone()[0]

        self.assertEqual(1, first["events_created"])
        self.assertEqual(0, first["events_updated"])
        self.assertEqual(0, second["events_created"])
        self.assertEqual(0, second["events_updated"])
        self.assertEqual([(1, 0), (0, 0)], runs)
        self.assertEqual([(1, 0), (0, 0)], exchange_results)
        self.assertEqual(1, event_count)

    def test_failed_exchange_rolls_back_partial_rows(self):
        from exchange_listings.sync import run_sync

        def fake_fetcher(exchange, *, mode, months):
            return [
                {
                    "exchange": "coinbase",
                    "source_type": "official_x",
                    "source_url": "https://x.com/CoinbaseMarkets/status/partial",
                    "title": "JKL added to roadmap",
                    "raw_text": "JKL Network (JKL) has been added to our listing roadmap.",
                    "published_at": "2026-05-29T00:00:00Z",
                    "fetched_at": "2026-05-29T00:01:00Z",
                }
            ]

        def exploding_parse_events(raw_source):
            raise RuntimeError("parser broke")

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "exchange_listings.sqlite"

            with patch("exchange_listings.sync.parse_events", exploding_parse_events):
                summary = run_sync(
                    db_path,
                    trigger_type="manual",
                    mode="incremental",
                    exchanges=["coinbase"],
                    fetcher=fake_fetcher,
                )

            with sqlite3.connect(db_path) as conn:
                raw_count = conn.execute("SELECT COUNT(*) FROM raw_sources").fetchone()[0]
                asset_count = conn.execute("SELECT COUNT(*) FROM normalized_assets").fetchone()[0]
                event_count = conn.execute("SELECT COUNT(*) FROM listing_events").fetchone()[0]
                result = conn.execute(
                    """
                    SELECT exchange, status, sources_found, events_created, events_updated, error
                    FROM sync_run_exchange_results
                    """
                ).fetchone()

        self.assertFalse(summary["ok"])
        self.assertEqual(0, raw_count)
        self.assertEqual(0, asset_count)
        self.assertEqual(0, event_count)
        self.assertEqual(("coinbase", "failed", 0, 0, 0, "parser broke"), result)

    def test_cli_skipped_sync_exits_zero(self):
        def empty_fetcher(exchange, *, mode, months):
            return []

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "exchange_listings.sqlite"
            db.init_db(db_path)
            with db.connect(db_path) as conn:
                db.start_sync_run(conn, trigger_type="manual", exchanges=("coinbase",))

            stdout = StringIO()
            import exchange_listing_sync

            with patch("exchange_listing_sync.default_fetcher", empty_fetcher):
                with redirect_stdout(stdout):
                    exit_code = exchange_listing_sync.main(
                        ["--mode", "incremental", "--db", str(db_path), "--exchange", "coinbase"]
                    )

        summary = json.loads(stdout.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual("skipped", summary["status"])

    def test_unknown_exchange_is_rejected(self):
        from exchange_listings.sync import run_sync

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "exchange_listings.sqlite"

            with self.assertRaisesRegex(ValueError, "Unknown exchange: notarealexchange"):
                run_sync(
                    db_path,
                    trigger_type="manual",
                    mode="incremental",
                    exchanges=["notarealexchange"],
                    fetcher=lambda exchange, *, mode, months: [],
                )

    def test_cli_unknown_exchange_exits_nonzero_with_json_error(self):
        stdout = StringIO()
        import exchange_listing_sync

        with redirect_stdout(stdout):
            exit_code = exchange_listing_sync.main(["--exchange", "notarealexchange"])

        summary = json.loads(stdout.getvalue())
        self.assertEqual(1, exit_code)
        self.assertFalse(summary["ok"])
        self.assertEqual("failed", summary["status"])
        self.assertEqual("Unknown exchange: notarealexchange", summary["error"])

    def test_cli_live_mode_uses_live_fetcher_with_limit_and_max_pages(self):
        calls = []

        def fake_live_fetcher(exchange, *, mode, months, limit, max_pages):
            calls.append((exchange, mode, months, limit, max_pages))
            return []

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "exchange_listings.sqlite"
            stdout = StringIO()
            import exchange_listing_sync

            with patch("exchange_listing_sync.fetch_live_sources", fake_live_fetcher):
                with redirect_stdout(stdout):
                    exit_code = exchange_listing_sync.main(
                        [
                            "--mode",
                            "incremental",
                            "--db",
                            str(db_path),
                            "--exchange",
                            "okx",
                            "--live",
                            "--limit",
                            "2",
                            "--max-pages",
                            "1",
                        ]
                    )

        summary = json.loads(stdout.getvalue())
        self.assertEqual(0, exit_code)
        self.assertTrue(summary["ok"])
        self.assertEqual([("okx", "incremental", 3, 2, 1)], calls)
