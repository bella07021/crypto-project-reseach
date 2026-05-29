import unittest
from datetime import datetime, timezone

from exchange_listings.models import (
    LISTING_TYPE_SPOT,
    SOURCE_PRECEDENCE_X,
    STATUS_ANNOUNCED,
    STATUS_TBD,
    STATUS_TRADING_SOON,
    STATUS_TRADING_STARTED,
)
from exchange_listings.parsers import parse_events


class ExchangeListingParserTests(unittest.TestCase):
    fixed_now = datetime(2026, 5, 29, tzinfo=timezone.utc)

    def test_coinbase_roadmap_x_post_produces_tbd_spot_roadmap_event(self):
        raw_source = {
            "exchange": "coinbase",
            "source_type": "official_x",
            "source_url": "https://x.com/CoinbaseMarkets/status/100",
            "title": "Asset added to roadmap",
            "raw_text": "Coinbase will add support for Example Token (EXT) on the Ethereum network to our listing roadmap.",
            "published_at": "2026-05-28T12:00:00Z",
        }

        events = parse_events(raw_source)

        self.assertEqual(1, len(events))
        event = events[0]
        self.assertEqual("coinbase", event["exchange"])
        self.assertEqual("EXT", event["token_symbol"])
        self.assertEqual(STATUS_TBD, event["status"])
        self.assertEqual(LISTING_TYPE_SPOT, event["listing_type"])
        self.assertEqual("roadmap", event["event_kind"])
        self.assertEqual(SOURCE_PRECEDENCE_X, event["source_precedence"])

    def test_future_trading_time_produces_trading_soon(self):
        raw_source = {
            "exchange": "binance",
            "source_type": "exchange_announcement",
            "source_url": "https://www.binance.com/en/support/announcement/300",
            "title": "Binance Will List Example Token (EXT)",
            "raw_text": "Binance will list Example Token (EXT) and open trading at 2026-05-30 12:00 UTC.",
            "published_at": "2026-05-29T00:00:00Z",
        }

        events = parse_events(raw_source, now=self.fixed_now)

        self.assertEqual(1, len(events))
        self.assertEqual(STATUS_TRADING_SOON, events[0]["status"])
        self.assertEqual("2026-05-30T12:00:00Z", events[0]["trading_start_time"])

    def test_past_trading_time_produces_trading_started(self):
        raw_source = {
            "exchange": "okx",
            "source_type": "exchange_announcement",
            "source_url": "https://www.okx.com/help/400",
            "title": "OKX to List Past Token (PST) for Spot Trading",
            "raw_text": "Spot trading for Past Token (PST) will start at 2026-05-28T09:30:00Z.",
            "published_at": "2026-05-28T00:00:00Z",
        }

        events = parse_events(raw_source, now=self.fixed_now)

        self.assertEqual(1, len(events))
        self.assertEqual(STATUS_TRADING_STARTED, events[0]["status"])
        self.assertEqual("2026-05-28T09:30:00Z", events[0]["trading_start_time"])

    def test_missing_trading_time_with_listing_language_produces_announced(self):
        raw_source = {
            "exchange": "kucoin",
            "source_type": "exchange_announcement",
            "source_url": "https://www.kucoin.com/announcement/500",
            "title": "KuCoin Will List No Time Token (NTT)",
            "raw_text": "KuCoin is extremely proud to announce the listing of No Time Token (NTT).",
            "published_at": "2026-05-29T00:00:00Z",
        }

        events = parse_events(raw_source, now=self.fixed_now)

        self.assertEqual(1, len(events))
        self.assertEqual(STATUS_ANNOUNCED, events[0]["status"])
        self.assertIsNone(events[0]["trading_start_time"])

    def test_multi_token_announcement_title_extracts_multiple_event_rows(self):
        raw_source = {
            "exchange": "binance",
            "source_type": "exchange_announcement",
            "source_url": "https://www.binance.com/en/support/announcement/600",
            "title": "Binance Will List Alpha Token (ALP) and Beta Token (BET)",
            "raw_text": "Binance will list Alpha Token (ALP) and Beta Token (BET) and open spot trading.",
            "published_at": "2026-05-29T00:00:00Z",
        }

        events = parse_events(raw_source, now=self.fixed_now)

        self.assertEqual(["ALP", "BET"], [event["token_symbol"] for event in events])
        self.assertEqual([STATUS_ANNOUNCED, STATUS_ANNOUNCED], [event["status"] for event in events])

    def test_korean_notice_extracts_parenthesized_symbol_and_preserves_project_name(self):
        raw_source = {
            "exchange": "upbit",
            "source_type": "exchange_announcement",
            "source_url": "https://www.upbit.com/service_center/notice?id=700",
            "title": "거래지원 안내: 모나드 (MON)",
            "raw_text": "모나드 (MON) 신규 거래지원 안내",
            "project_name": "Monad",
            "published_at": "2026-05-29T00:00:00Z",
        }

        events = parse_events(raw_source, now=self.fixed_now)

        self.assertEqual(1, len(events))
        self.assertEqual("MON", events[0]["token_symbol"])
        self.assertEqual("Monad", events[0]["project_name"])

    def test_kraken_listing_x_post_without_timing_produces_tbd_spot_event(self):
        raw_source = {
            "exchange": "kraken",
            "source_type": "official_x",
            "source_url": "https://x.com/krakenlistings/status/200",
            "title": "New listing",
            "raw_text": "$ABC is coming to Kraken spot markets.",
            "published_at": "2026-05-28T13:00:00Z",
        }

        events = parse_events(raw_source)

        self.assertEqual(1, len(events))
        event = events[0]
        self.assertEqual("kraken", event["exchange"])
        self.assertEqual("ABC", event["token_symbol"])
        self.assertEqual(STATUS_TBD, event["status"])
        self.assertEqual(LISTING_TYPE_SPOT, event["listing_type"])
