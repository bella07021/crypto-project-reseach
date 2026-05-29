import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from exchange_listings.db import init_db
from exchange_listings import db
from exchange_listings.models import (
    EVENT_FAMILY_SPOT_LISTING,
    LISTING_TYPE_SPOT,
    SOURCE_PRECEDENCE_ANNOUNCEMENT,
    SOURCE_PRECEDENCE_BLOG,
    SOURCE_PRECEDENCE_X,
    STATUS_ANNOUNCED,
    STATUS_TBD,
    STATUS_TRADING_SOON,
)


class ExchangeListingDbTests(unittest.TestCase):
    def open_initialized_db(self, tmpdir):
        db_path = Path(tmpdir) / "exchange_listings.sqlite"
        init_db(db_path)
        return sqlite3.connect(db_path)

    def test_init_db_creates_expected_tables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "exchange_listings.sqlite"

            init_db(db_path)

            with sqlite3.connect(db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                    """
                ).fetchall()

        tables = {row[0] for row in rows}
        self.assertTrue(
            {
                "normalized_assets",
                "raw_sources",
                "listing_events",
                "sync_runs",
                "sync_run_exchange_results",
                "source_cursors",
            }.issubset(tables)
        )

    def test_upsert_raw_source_dedupes_by_exchange_and_source_url(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.open_initialized_db(tmpdir) as conn:
                raw_source = {
                    "exchange": "coinbase",
                    "source_type": "official_x",
                    "source_url": "https://x.com/CoinbaseMarkets/status/123",
                    "title": "Asset added to roadmap",
                    "raw_text": "Asset ABC has been added to our roadmap.",
                    "fetched_at": "2026-05-29T00:00:00Z",
                }

                first_id = db.upsert_raw_source(conn, raw_source)
                second_id = db.upsert_raw_source(
                    conn,
                    {
                        **raw_source,
                        "title": "Updated title",
                        "raw_text": "Updated source text",
                    },
                )

        self.assertEqual(first_id, second_id)

    def test_upsert_raw_source_dedupes_by_exchange_source_type_and_external_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.open_initialized_db(tmpdir) as conn:
                raw_source = {
                    "exchange": "kraken",
                    "source_type": "official_x",
                    "external_id": "456",
                    "title": "Kraken listing signal",
                    "raw_text": "ABC is coming to Kraken.",
                    "fetched_at": "2026-05-29T00:00:00Z",
                }

                first_id = db.upsert_raw_source(conn, raw_source)
                second_id = db.upsert_raw_source(
                    conn,
                    {
                        **raw_source,
                        "raw_text": "ABC is coming to Kraken soon.",
                    },
                )

        self.assertEqual(first_id, second_id)

    def test_upsert_raw_source_dedupes_by_exchange_and_content_hash_without_url_or_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.open_initialized_db(tmpdir) as conn:
                raw_source = {
                    "exchange": "okx",
                    "source_type": "exchange_announcement",
                    "title": "OKX will list ABC",
                    "published_at": "2026-05-29T00:00:00Z",
                    "raw_text": "OKX will list ABC spot trading.",
                    "fetched_at": "2026-05-29T00:01:00Z",
                }

                first_id = db.upsert_raw_source(conn, raw_source)
                second_id = db.upsert_raw_source(conn, {**raw_source})

        self.assertEqual(first_id, second_id)

    def test_upsert_normalized_asset_creates_low_confidence_asset_from_symbol_and_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.open_initialized_db(tmpdir) as conn:
                asset_id = db.upsert_normalized_asset(
                    conn,
                    {
                        "token_symbol": "abc",
                        "project_name": "ABC Network",
                        "first_seen_at": "2026-05-29T00:00:00Z",
                    },
                )
                row = conn.execute(
                    """
                    SELECT canonical_symbol, project_name, slug, identity_confidence
                    FROM normalized_assets
                    WHERE id = ?
                    """,
                    (asset_id,),
                ).fetchone()

        self.assertEqual(("ABC", "ABC Network", "abc-network", "low"), row)

    def test_upsert_listing_event_creates_coinbase_roadmap_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.open_initialized_db(tmpdir) as conn:
                raw_source_id = db.upsert_raw_source(
                    conn,
                    {
                        "exchange": "coinbase",
                        "source_type": "official_x",
                        "source_url": "https://x.com/CoinbaseMarkets/status/123",
                        "title": "ABC added to roadmap",
                        "raw_text": "ABC Network (ABC) has been added to our roadmap.",
                        "fetched_at": "2026-05-29T00:00:00Z",
                    },
                )
                asset_id = db.upsert_normalized_asset(
                    conn,
                    {"token_symbol": "ABC", "project_name": "ABC Network"},
                )

                event_id = db.upsert_listing_event(
                    conn,
                    {
                        "exchange": "coinbase",
                        "normalized_asset_id": asset_id,
                        "project_name": "ABC Network",
                        "token_symbol": "ABC",
                        "listing_type": LISTING_TYPE_SPOT,
                        "event_family": EVENT_FAMILY_SPOT_LISTING,
                        "event_kind": "roadmap",
                        "status": STATUS_TBD,
                        "announcement_url": "https://x.com/CoinbaseMarkets/status/123",
                        "announcement_title": "ABC added to roadmap",
                        "source_type": "official_x",
                        "confidence": "medium",
                        "source_precedence": SOURCE_PRECEDENCE_X,
                        "raw_source_id": raw_source_id,
                    },
                )
                row = conn.execute(
                    """
                    SELECT exchange, normalized_asset_id, listing_type, event_family, event_kind, status
                    FROM listing_events
                    WHERE id = ?
                    """,
                    (event_id,),
                ).fetchone()

        self.assertEqual(
            (
                "coinbase",
                asset_id,
                LISTING_TYPE_SPOT,
                EVENT_FAMILY_SPOT_LISTING,
                "roadmap",
                STATUS_TBD,
            ),
            row,
        )

    def test_upsert_listing_event_updates_same_event_with_higher_precedence_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.open_initialized_db(tmpdir) as conn:
                asset_id = db.upsert_normalized_asset(
                    conn,
                    {"token_symbol": "ABC", "project_name": "ABC Network"},
                )
                roadmap_source_id = db.upsert_raw_source(
                    conn,
                    {
                        "exchange": "coinbase",
                        "source_type": "official_x",
                        "external_id": "123",
                        "title": "ABC added to roadmap",
                        "raw_text": "ABC Network (ABC) has been added to our roadmap.",
                        "fetched_at": "2026-05-29T00:00:00Z",
                    },
                )
                announcement_source_id = db.upsert_raw_source(
                    conn,
                    {
                        "exchange": "coinbase",
                        "source_type": "exchange_announcement",
                        "source_url": "https://www.coinbase.com/blog/coinbase-will-list-abc",
                        "title": "Coinbase will list ABC Network",
                        "raw_text": "Trading will begin on or after 9AM PT on May 30, 2026.",
                        "fetched_at": "2026-05-29T01:00:00Z",
                    },
                )
                first_event_id = db.upsert_listing_event(
                    conn,
                    {
                        "exchange": "coinbase",
                        "normalized_asset_id": asset_id,
                        "project_name": "ABC Network",
                        "token_symbol": "ABC",
                        "listing_type": LISTING_TYPE_SPOT,
                        "event_family": EVENT_FAMILY_SPOT_LISTING,
                        "event_kind": "roadmap",
                        "status": STATUS_TBD,
                        "announcement_title": "ABC added to roadmap",
                        "source_type": "official_x",
                        "confidence": "medium",
                        "source_precedence": SOURCE_PRECEDENCE_X,
                        "raw_source_id": roadmap_source_id,
                    },
                )
                second_event_id = db.upsert_listing_event(
                    conn,
                    {
                        "exchange": "coinbase",
                        "normalized_asset_id": asset_id,
                        "project_name": "ABC Network",
                        "token_symbol": "ABC",
                        "listing_type": LISTING_TYPE_SPOT,
                        "event_family": EVENT_FAMILY_SPOT_LISTING,
                        "event_kind": "listing_announcement",
                        "status": STATUS_TRADING_SOON,
                        "announcement_url": "https://www.coinbase.com/blog/coinbase-will-list-abc",
                        "announcement_title": "Coinbase will list ABC Network",
                        "trading_start_time": "2026-05-30T16:00:00Z",
                        "source_type": "exchange_announcement",
                        "confidence": "high",
                        "source_precedence": SOURCE_PRECEDENCE_ANNOUNCEMENT,
                        "raw_source_id": announcement_source_id,
                    },
                )
                row = conn.execute(
                    """
                    SELECT event_kind, status, source_precedence, trading_start_time, raw_source_id
                    FROM listing_events
                    WHERE id = ?
                    """,
                    (first_event_id,),
                ).fetchone()
                event_count = conn.execute("SELECT COUNT(*) FROM listing_events").fetchone()[0]

        self.assertEqual(first_event_id, second_event_id)
        self.assertEqual(1, event_count)
        self.assertEqual(
            (
                "listing_announcement",
                STATUS_TRADING_SOON,
                SOURCE_PRECEDENCE_ANNOUNCEMENT,
                "2026-05-30T16:00:00Z",
                announcement_source_id,
            ),
            row,
        )

    def test_upsert_listing_event_advances_status_by_rank_without_higher_precedence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.open_initialized_db(tmpdir) as conn:
                asset_id = db.upsert_normalized_asset(
                    conn,
                    {"token_symbol": "ABC", "project_name": "ABC Network"},
                )
                event_id = db.upsert_listing_event(
                    conn,
                    {
                        "exchange": "coinbase",
                        "normalized_asset_id": asset_id,
                        "project_name": "ABC Network",
                        "token_symbol": "ABC",
                        "listing_type": LISTING_TYPE_SPOT,
                        "event_family": EVENT_FAMILY_SPOT_LISTING,
                        "event_kind": "listing_announcement",
                        "status": STATUS_ANNOUNCED,
                        "source_type": "official_blog",
                        "confidence": "medium",
                        "source_precedence": SOURCE_PRECEDENCE_BLOG,
                    },
                )
                same_event_id = db.upsert_listing_event(
                    conn,
                    {
                        "exchange": "coinbase",
                        "normalized_asset_id": asset_id,
                        "project_name": "ABC Network",
                        "token_symbol": "ABC",
                        "listing_type": LISTING_TYPE_SPOT,
                        "event_family": EVENT_FAMILY_SPOT_LISTING,
                        "event_kind": "listing_announcement",
                        "status": STATUS_TRADING_SOON,
                        "source_type": "official_blog",
                        "confidence": "medium",
                        "source_precedence": SOURCE_PRECEDENCE_BLOG,
                    },
                )
                row = conn.execute(
                    "SELECT status, source_precedence FROM listing_events WHERE id = ?",
                    (event_id,),
                ).fetchone()

        self.assertEqual(event_id, same_event_id)
        self.assertEqual((STATUS_TRADING_SOON, SOURCE_PRECEDENCE_BLOG), row)

    def test_upsert_listing_event_fills_timing_without_downgrading_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.open_initialized_db(tmpdir) as conn:
                asset_id = db.upsert_normalized_asset(
                    conn,
                    {"token_symbol": "ABC", "project_name": "ABC Network"},
                )
                announcement_source_id = db.upsert_raw_source(
                    conn,
                    {
                        "exchange": "coinbase",
                        "source_type": "exchange_announcement",
                        "source_url": "https://www.coinbase.com/blog/coinbase-will-list-abc",
                        "title": "Coinbase will list ABC Network",
                        "raw_text": "Trading will begin soon.",
                        "fetched_at": "2026-05-29T00:00:00Z",
                    },
                )
                lower_precedence_source_id = db.upsert_raw_source(
                    conn,
                    {
                        "exchange": "coinbase",
                        "source_type": "official_x",
                        "external_id": "123",
                        "title": "ABC deposit timing",
                        "raw_text": "Deposits are now open for ABC.",
                        "fetched_at": "2026-05-29T01:00:00Z",
                    },
                )
                event_id = db.upsert_listing_event(
                    conn,
                    {
                        "exchange": "coinbase",
                        "normalized_asset_id": asset_id,
                        "project_name": "ABC Network",
                        "token_symbol": "ABC",
                        "listing_type": LISTING_TYPE_SPOT,
                        "event_family": EVENT_FAMILY_SPOT_LISTING,
                        "event_kind": "listing_announcement",
                        "status": STATUS_TRADING_SOON,
                        "announcement_url": "https://www.coinbase.com/blog/coinbase-will-list-abc",
                        "announcement_title": "Coinbase will list ABC Network",
                        "source_type": "exchange_announcement",
                        "confidence": "high",
                        "source_precedence": SOURCE_PRECEDENCE_ANNOUNCEMENT,
                        "raw_source_id": announcement_source_id,
                    },
                )
                same_event_id = db.upsert_listing_event(
                    conn,
                    {
                        "exchange": "coinbase",
                        "normalized_asset_id": asset_id,
                        "project_name": "ABC Network",
                        "token_symbol": "ABC",
                        "listing_type": LISTING_TYPE_SPOT,
                        "event_family": EVENT_FAMILY_SPOT_LISTING,
                        "event_kind": "roadmap",
                        "status": STATUS_TBD,
                        "announcement_title": "ABC deposit timing",
                        "deposit_start_time": "2026-05-29T01:00:00Z",
                        "source_type": "official_x",
                        "confidence": "low",
                        "source_precedence": SOURCE_PRECEDENCE_X,
                        "raw_source_id": lower_precedence_source_id,
                    },
                )
                row = conn.execute(
                    """
                    SELECT event_kind, status, announcement_url, announcement_title,
                           deposit_start_time, source_type, confidence, source_precedence,
                           raw_source_id
                    FROM listing_events
                    WHERE id = ?
                    """,
                    (event_id,),
                ).fetchone()

        self.assertEqual(event_id, same_event_id)
        self.assertEqual(
            (
                "listing_announcement",
                STATUS_TRADING_SOON,
                "https://www.coinbase.com/blog/coinbase-will-list-abc",
                "Coinbase will list ABC Network",
                "2026-05-29T01:00:00Z",
                "exchange_announcement",
                "high",
                SOURCE_PRECEDENCE_ANNOUNCEMENT,
                announcement_source_id,
            ),
            row,
        )

    def test_start_sync_run_creates_running_row_when_no_fresh_run_exists(self):
        now = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.open_initialized_db(tmpdir) as conn:
                result = db.start_sync_run(
                    conn,
                    trigger_type="manual",
                    exchanges=("coinbase", "kraken"),
                    now=now,
                )
                row = conn.execute(
                    """
                    SELECT trigger_type, status, exchanges_requested
                    FROM sync_runs
                    WHERE id = ?
                    """,
                    (result["run_id"],),
                ).fetchone()

        self.assertEqual("running", result["status"])
        self.assertEqual(('manual', 'running', '["coinbase","kraken"]'), row)

    def test_start_sync_run_returns_skipped_when_running_row_is_fresh(self):
        now = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.open_initialized_db(tmpdir) as conn:
                running = db.start_sync_run(conn, trigger_type="manual", exchanges=("coinbase",), now=now)
                skipped = db.start_sync_run(
                    conn,
                    trigger_type="manual",
                    exchanges=("coinbase",),
                    now=now + timedelta(minutes=30),
                )
                row_count = conn.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0]

        self.assertEqual("running", running["status"])
        self.assertEqual(
            {
                "status": "skipped",
                "run_id": running["run_id"],
                "skipped_reason": "fresh_running_sync",
            },
            skipped,
        )
        self.assertEqual(1, row_count)

    def test_start_sync_run_marks_stale_running_row_failed(self):
        now = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.open_initialized_db(tmpdir) as conn:
                stale = db.start_sync_run(
                    conn,
                    trigger_type="scheduled",
                    exchanges=("coinbase",),
                    now=now - timedelta(hours=3),
                )
                fresh = db.start_sync_run(
                    conn,
                    trigger_type="scheduled",
                    exchanges=("coinbase",),
                    now=now,
                )
                stale_row = conn.execute(
                    "SELECT status, error FROM sync_runs WHERE id = ?",
                    (stale["run_id"],),
                ).fetchone()
                fresh_row = conn.execute(
                    "SELECT status FROM sync_runs WHERE id = ?",
                    (fresh["run_id"],),
                ).fetchone()

        self.assertEqual("failed", stale_row[0])
        self.assertEqual("stale running sync exceeded 2 hours", stale_row[1])
        self.assertEqual("running", fresh_row[0])
        self.assertNotEqual(stale["run_id"], fresh["run_id"])
