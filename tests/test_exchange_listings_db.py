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
    STATUS_UNKNOWN,
)
from exchange_listings.parsers import parse_events


class ExchangeListingDbTests(unittest.TestCase):
    def open_initialized_db(self, tmpdir):
        db_path = Path(tmpdir) / "exchange_listings.sqlite"
        init_db(db_path)
        return db.connect(db_path)

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
                "sync_locks",
            }.issubset(tables)
        )

    def test_repository_connection_enforces_listing_event_foreign_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "exchange_listings.sqlite"
            init_db(db_path)

            with db.connect(db_path) as conn:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.upsert_listing_event(
                        conn,
                        {
                            "exchange": "coinbase",
                            "normalized_asset_id": 999,
                            "project_name": "Missing Network",
                            "token_symbol": "MISS",
                            "listing_type": LISTING_TYPE_SPOT,
                            "event_family": EVENT_FAMILY_SPOT_LISTING,
                            "event_kind": "listing_announcement",
                            "status": STATUS_ANNOUNCED,
                            "source_precedence": SOURCE_PRECEDENCE_BLOG,
                        },
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

    def test_upsert_raw_source_handles_url_and_external_id_collision_deterministically(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.open_initialized_db(tmpdir) as conn:
                url_row_id = db.upsert_raw_source(
                    conn,
                    {
                        "exchange": "coinbase",
                        "source_type": "official_x",
                        "source_url": "https://x.com/CoinbaseMarkets/status/abc",
                        "title": "ABC roadmap by URL",
                        "raw_text": "ABC has been added to the roadmap.",
                        "fetched_at": "2026-05-29T00:00:00Z",
                    },
                )
                external_id_row_id = db.upsert_raw_source(
                    conn,
                    {
                        "exchange": "coinbase",
                        "source_type": "official_x",
                        "external_id": "abc",
                        "title": "ABC roadmap by id",
                        "raw_text": "ABC has been added to the roadmap.",
                        "fetched_at": "2026-05-29T00:01:00Z",
                    },
                )

                canonical_id = db.upsert_raw_source(
                    conn,
                    {
                        "exchange": "coinbase",
                        "source_type": "official_x",
                        "source_url": "https://x.com/CoinbaseMarkets/status/abc",
                        "external_id": "abc",
                        "title": "ABC roadmap unified",
                        "raw_text": "ABC has been added to the roadmap.",
                        "fetched_at": "2026-05-29T00:02:00Z",
                    },
                )
                canonical_row = conn.execute(
                    """
                    SELECT source_url, external_id
                    FROM raw_sources
                    WHERE id = ?
                    """,
                    (canonical_id,),
                ).fetchone()
                other_row = conn.execute(
                    """
                    SELECT source_url, external_id
                    FROM raw_sources
                    WHERE id = ?
                    """,
                    (external_id_row_id,),
                ).fetchone()

        self.assertEqual(url_row_id, canonical_id)
        self.assertNotEqual(url_row_id, external_id_row_id)
        self.assertEqual(("https://x.com/CoinbaseMarkets/status/abc", "abc"), canonical_row)
        self.assertEqual((None, None), other_row)

    def test_upsert_raw_source_handles_url_external_id_and_content_hash_collision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.open_initialized_db(tmpdir) as conn:
                content_hash = "a" * 64
                url_row_id = db.upsert_raw_source(
                    conn,
                    {
                        "exchange": "coinbase",
                        "source_type": "official_x",
                        "source_url": "https://x.com/CoinbaseMarkets/status/abc",
                        "title": "ABC roadmap by URL",
                        "raw_text": "ABC has been added to the roadmap by URL.",
                        "fetched_at": "2026-05-29T00:00:00Z",
                    },
                )
                external_id_row_id = db.upsert_raw_source(
                    conn,
                    {
                        "exchange": "coinbase",
                        "source_type": "official_x",
                        "external_id": "abc",
                        "title": "ABC roadmap by id",
                        "raw_text": "ABC has been added to the roadmap by id.",
                        "fetched_at": "2026-05-29T00:01:00Z",
                    },
                )
                hash_row_id = db.upsert_raw_source(
                    conn,
                    {
                        "exchange": "coinbase",
                        "source_type": "official_x",
                        "title": "ABC roadmap by hash",
                        "raw_text": "ABC has been added to the roadmap by hash.",
                        "content_hash": content_hash,
                        "fetched_at": "2026-05-29T00:02:00Z",
                    },
                )

                canonical_id = db.upsert_raw_source(
                    conn,
                    {
                        "exchange": "coinbase",
                        "source_type": "official_x",
                        "source_url": "https://x.com/CoinbaseMarkets/status/abc",
                        "external_id": "abc",
                        "title": "ABC roadmap unified",
                        "raw_text": "ABC has been added to the roadmap.",
                        "content_hash": content_hash,
                        "fetched_at": "2026-05-29T00:03:00Z",
                    },
                )
                canonical_row = conn.execute(
                    """
                    SELECT source_url, external_id, content_hash
                    FROM raw_sources
                    WHERE id = ?
                    """,
                    (canonical_id,),
                ).fetchone()
                row_count = conn.execute("SELECT COUNT(*) FROM raw_sources").fetchone()[0]

        self.assertEqual(url_row_id, canonical_id)
        self.assertLess(url_row_id, external_id_row_id)
        self.assertLess(external_id_row_id, hash_row_id)
        self.assertEqual(
            ("https://x.com/CoinbaseMarkets/status/abc", "abc", content_hash),
            canonical_row,
        )
        self.assertEqual(3, row_count)

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

    def test_upsert_normalized_asset_dedupes_repeated_symbol_only_asset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.open_initialized_db(tmpdir) as conn:
                first_id = db.upsert_normalized_asset(conn, {"token_symbol": "abc"})
                second_id = db.upsert_normalized_asset(conn, {"token_symbol": "ABC"})
                row_count = conn.execute("SELECT COUNT(*) FROM normalized_assets").fetchone()[0]

        self.assertEqual(first_id, second_id)
        self.assertEqual(1, row_count)

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

    def test_parsed_coinbase_roadmap_removal_updates_existing_tbd_event_to_unknown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.open_initialized_db(tmpdir) as conn:
                roadmap_source = {
                    "exchange": "coinbase",
                    "source_type": "official_x",
                    "external_id": "roadmap-123",
                    "title": "EXT added to roadmap",
                    "raw_text": "Example Token (EXT) has been added to our listing roadmap.",
                    "fetched_at": "2026-05-29T00:00:00Z",
                }
                removal_source = {
                    "exchange": "coinbase",
                    "source_type": "official_x",
                    "external_id": "roadmap-124",
                    "title": "Roadmap update",
                    "raw_text": "We have removed Example Token (EXT) from our listing roadmap.",
                    "fetched_at": "2026-05-29T01:00:00Z",
                }
                asset_id = db.upsert_normalized_asset(
                    conn,
                    {"token_symbol": "EXT", "project_name": "Example Token"},
                )

                roadmap_raw_id = db.upsert_raw_source(conn, roadmap_source)
                roadmap_event = parse_events(roadmap_source)[0]
                first_event_id = db.upsert_listing_event(
                    conn,
                    {
                        **roadmap_event,
                        "normalized_asset_id": asset_id,
                        "raw_source_id": roadmap_raw_id,
                    },
                )
                removal_raw_id = db.upsert_raw_source(conn, removal_source)
                removal_event = parse_events(removal_source)[0]
                second_event_id = db.upsert_listing_event(
                    conn,
                    {
                        **removal_event,
                        "normalized_asset_id": asset_id,
                        "raw_source_id": removal_raw_id,
                    },
                )
                row = conn.execute(
                    """
                    SELECT status, source_precedence, raw_source_id
                    FROM listing_events
                    WHERE id = ?
                    """,
                    (first_event_id,),
                ).fetchone()
                event_count = conn.execute("SELECT COUNT(*) FROM listing_events").fetchone()[0]

        self.assertEqual(first_event_id, second_event_id)
        self.assertEqual(1, event_count)
        self.assertEqual((STATUS_UNKNOWN, SOURCE_PRECEDENCE_X, removal_raw_id), row)

    def test_unknown_status_does_not_downgrade_confirmed_listing_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.open_initialized_db(tmpdir) as conn:
                asset_id = db.upsert_normalized_asset(
                    conn,
                    {"token_symbol": "EXT", "project_name": "Example Token"},
                )
                event_id = db.upsert_listing_event(
                    conn,
                    {
                        "exchange": "coinbase",
                        "normalized_asset_id": asset_id,
                        "project_name": "Example Token",
                        "token_symbol": "EXT",
                        "listing_type": LISTING_TYPE_SPOT,
                        "event_family": EVENT_FAMILY_SPOT_LISTING,
                        "event_kind": "listing_announcement",
                        "status": STATUS_TRADING_SOON,
                        "trading_start_time": "2026-05-30T16:00:00Z",
                        "source_type": "official_x",
                        "confidence": "medium",
                        "source_precedence": SOURCE_PRECEDENCE_X,
                    },
                )
                same_event_id = db.upsert_listing_event(
                    conn,
                    {
                        "exchange": "coinbase",
                        "normalized_asset_id": asset_id,
                        "project_name": "Example Token",
                        "token_symbol": "EXT",
                        "listing_type": LISTING_TYPE_SPOT,
                        "event_family": EVENT_FAMILY_SPOT_LISTING,
                        "event_kind": "roadmap",
                        "status": STATUS_UNKNOWN,
                        "source_type": "official_x",
                        "confidence": "medium",
                        "source_precedence": SOURCE_PRECEDENCE_X,
                    },
                )
                row = conn.execute(
                    "SELECT status, trading_start_time FROM listing_events WHERE id = ?",
                    (event_id,),
                ).fetchone()

        self.assertEqual(event_id, same_event_id)
        self.assertEqual((STATUS_TRADING_SOON, "2026-05-30T16:00:00Z"), row)

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

    def test_upsert_listing_event_updates_corrected_timing_at_same_precedence(self):
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
                        "status": STATUS_TRADING_SOON,
                        "trading_start_time": "2026-05-30T16:00:00Z",
                        "source_type": "exchange_announcement",
                        "confidence": "high",
                        "source_precedence": SOURCE_PRECEDENCE_ANNOUNCEMENT,
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
                        "trading_start_time": "2026-05-30T17:00:00Z",
                        "source_type": "exchange_announcement",
                        "confidence": "high",
                        "source_precedence": SOURCE_PRECEDENCE_ANNOUNCEMENT,
                    },
                )
                row = conn.execute(
                    "SELECT trading_start_time, source_precedence FROM listing_events WHERE id = ?",
                    (event_id,),
                ).fetchone()

        self.assertEqual(event_id, same_event_id)
        self.assertEqual(("2026-05-30T17:00:00Z", SOURCE_PRECEDENCE_ANNOUNCEMENT), row)

    def test_upsert_listing_event_ignores_lower_precedence_corrected_populated_timing(self):
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
                        "status": STATUS_TRADING_SOON,
                        "trading_start_time": "2026-05-30T16:00:00Z",
                        "source_type": "exchange_announcement",
                        "confidence": "high",
                        "source_precedence": SOURCE_PRECEDENCE_ANNOUNCEMENT,
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
                        "status": STATUS_TRADING_SOON,
                        "trading_start_time": "2026-05-30T18:00:00Z",
                        "source_type": "official_x",
                        "confidence": "medium",
                        "source_precedence": SOURCE_PRECEDENCE_X,
                    },
                )
                row = conn.execute(
                    """
                    SELECT trading_start_time, event_kind, source_type, source_precedence
                    FROM listing_events
                    WHERE id = ?
                    """,
                    (event_id,),
                ).fetchone()

        self.assertEqual(event_id, same_event_id)
        self.assertEqual(
            (
                "2026-05-30T16:00:00Z",
                "listing_announcement",
                "exchange_announcement",
                SOURCE_PRECEDENCE_ANNOUNCEMENT,
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

    def test_start_sync_run_creates_single_active_lock_for_running_sync(self):
        now = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.open_initialized_db(tmpdir) as conn:
                running = db.start_sync_run(conn, trigger_type="manual", exchanges=("coinbase",), now=now)
                skipped = db.start_sync_run(
                    conn,
                    trigger_type="manual",
                    exchanges=("kraken",),
                    now=now + timedelta(minutes=10),
                )
                locks = conn.execute("SELECT lock_name, run_id FROM sync_locks").fetchall()
                running_count = conn.execute(
                    "SELECT COUNT(*) FROM sync_runs WHERE status = 'running'"
                ).fetchone()[0]

        self.assertEqual("running", running["status"])
        self.assertEqual(
            {
                "status": "skipped",
                "run_id": running["run_id"],
                "skipped_reason": "fresh_running_sync",
            },
            skipped,
        )
        self.assertEqual([("exchange_listing_sync", running["run_id"])], locks)
        self.assertEqual(1, running_count)

    def test_start_sync_run_allows_only_one_running_sync_across_connections(self):
        now = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "exchange_listings.sqlite"
            init_db(db_path)
            with db.connect(db_path) as first_conn, db.connect(db_path) as second_conn:
                running = db.start_sync_run(
                    first_conn,
                    trigger_type="manual",
                    exchanges=("coinbase",),
                    now=now,
                )
                skipped = db.start_sync_run(
                    second_conn,
                    trigger_type="manual",
                    exchanges=("kraken",),
                    now=now + timedelta(minutes=1),
                )
                running_count = second_conn.execute(
                    "SELECT COUNT(*) FROM sync_runs WHERE status = 'running'"
                ).fetchone()[0]

        self.assertEqual("running", running["status"])
        self.assertEqual(
            {
                "status": "skipped",
                "run_id": running["run_id"],
                "skipped_reason": "fresh_running_sync",
            },
            skipped,
        )
        self.assertEqual(1, running_count)

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

    def test_start_sync_run_replaces_stale_lock_with_fresh_running_sync(self):
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
                    exchanges=("kraken",),
                    now=now,
                )
                locks = conn.execute("SELECT lock_name, run_id FROM sync_locks").fetchall()

        self.assertEqual([("exchange_listing_sync", fresh["run_id"])], locks)
        self.assertNotEqual(stale["run_id"], fresh["run_id"])

    def test_finish_sync_run_updates_counts_and_releases_active_lock(self):
        now = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.open_initialized_db(tmpdir) as conn:
                run = db.start_sync_run(conn, trigger_type="manual", exchanges=("coinbase",), now=now)
                db.finish_sync_run(
                    conn,
                    run["run_id"],
                    "success",
                    raw_sources_found=3,
                    events_created=1,
                    events_updated=2,
                    now=now + timedelta(minutes=5),
                )
                row = conn.execute(
                    """
                    SELECT status, raw_sources_found, events_created, events_updated, finished_at
                    FROM sync_runs
                    WHERE id = ?
                    """,
                    (run["run_id"],),
                ).fetchone()
                lock_count = conn.execute("SELECT COUNT(*) FROM sync_locks").fetchone()[0]

        self.assertEqual(("success", 3, 1, 2, "2026-05-29T12:05:00Z"), row)
        self.assertEqual(0, lock_count)

    def test_record_exchange_result_persists_exchange_metrics(self):
        now = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.open_initialized_db(tmpdir) as conn:
                run = db.start_sync_run(conn, trigger_type="manual", exchanges=("coinbase",), now=now)
                result_id = db.record_exchange_result(
                    conn,
                    run["run_id"],
                    exchange="coinbase",
                    source_type="official_x",
                    status="success",
                    sources_found=5,
                    events_created=2,
                    events_updated=1,
                    pages_fetched=3,
                )
                row = conn.execute(
                    """
                    SELECT sync_run_id, exchange, source_type, status,
                           sources_found, events_created, events_updated, pages_fetched, error
                    FROM sync_run_exchange_results
                    WHERE id = ?
                    """,
                    (result_id,),
                ).fetchone()

        self.assertEqual(
            (run["run_id"], "coinbase", "official_x", "success", 5, 2, 1, 3, None),
            row,
        )
