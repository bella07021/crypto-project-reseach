# Exchange Listing Database Design

## Goal

Build a standalone SQLite database for upcoming spot listing signals across major exchanges. This database is separate from the existing CoinMarketCap-based exchange coverage workflow and is not connected to the scoring system in the first release.

## Scope

The first release collects upcoming or recently announced spot listing information for the previous 3 months, then supports daily incremental refreshes and manual refreshes. It covers:

- Binance
- OKX
- Bybit
- Coinbase
- Upbit
- Bithumb
- KuCoin
- Gate
- MEXC
- Bitget
- Kraken

Only spot-related listing signals are in scope. Launchpool, Launchpad, Megadrop, HODLer Airdrops, Jumpstart, Pre-market, roadmap, and similar official signals are included when they point to eventual spot trading. They are normalized as `listing_type = "spot"`.

Futures listings are out of scope for the first release. The schema keeps `listing_type` so Binance futures support can be added later without changing the database shape.

## Source Strategy

Sources are grouped by how stable their listing pages are expected to be.

Standard announcement sources:

- Binance: `https://www.binance.com/en/support/announcement/list/48`
- OKX: `https://www.okx.com/en-us/help/section/announcements-new-listings`
- Bybit: `https://announcements.bybit.com/en/?category=new_crypto&page=1`
- KuCoin: `https://www.kucoin.com/announcement/new-listings`
- Gate: `https://www.gate.com/announcements/newlisted`
- MEXC: `https://www.mexc.fm/announcements/new-listings`
- Bitget: `https://www.bitget.com/zh-CN/support/sections/5955813039257`

Notice sources that require title/category filtering:

- Upbit: `https://www.upbit.com/service_center/notice`
- Bithumb: `https://feed.bithumb.com/notice`

Combined sources:

- Coinbase:
  - `https://x.com/search?q=from%3ACoinbaseMarkets%20roadmap&src=typed_query`
  - `https://www.coinbase.com/zh-cn/blog/increasing-transparency-for-new-asset-listings-on-coinbase`
- Kraken:
  - `https://www.kraken.com/zh-cn/listings`
  - `https://x.com/krakenlistings`

Official exchange pages are preferred when they contain structured timing. Official X posts are accepted as early signals for Coinbase and Kraken. If an official X post only says a token is on a roadmap and does not include a trading time, the parsed event status is `TBD`.

## Data Model

The database is stored at `data/exchange_listings.sqlite`.

### `normalized_assets`

Stores the conservative asset identity used to connect multiple listing events without relying only on ticker symbols.

Columns:

- `id`: integer primary key
- `canonical_symbol`: uppercase token symbol used as the first-pass grouping key
- `project_name`: best known project name
- `slug`: normalized lowercase project slug when available
- `coingecko_id`: optional CoinGecko asset id for future enrichment
- `rootdata_url`: optional RootData project URL for future enrichment
- `contract_addresses_json`: JSON object keyed by chain name when official contract addresses are available
- `aliases_json`: JSON array of alternate names and symbols observed in sources
- `identity_confidence`: `high`, `medium`, or `low`
- `first_seen_at`: first source time or fetch time in UTC ISO 8601
- `last_seen_at`: latest source time or fetch time in UTC ISO 8601
- `created_at`: row creation time in UTC ISO 8601
- `updated_at`: last update time in UTC ISO 8601

Uniqueness:

- `canonical_symbol + slug` is unique when `slug` is present
- `canonical_symbol + project_name` is unique when `slug` is missing and `project_name` is present
- If only a symbol is available, create a low-confidence asset and allow later enrichment to merge or update it manually

### `raw_sources`

Stores one raw source item per exchange announcement, official blog post, or official X post.

Columns:

- `id`: integer primary key
- `exchange`: exchange key such as `binance`, `okx`, or `coinbase`
- `source_type`: `exchange_announcement`, `official_blog`, or `official_x`
- `source_url`: canonical source URL when available
- `title`: source title or post text headline
- `published_at`: source publication time in UTC ISO 8601 when available
- `raw_text`: extracted source text
- `raw_payload_json`: source-specific metadata as JSON
- `official_account`: official X account handle when `source_type = "official_x"`
- `external_id`: tweet id, announcement id, or other source-native id when available
- `detection_reason`: short parser reason such as `roadmap_keyword`, `spot_listing_title`, or `launchpool_spot_followup`
- `parser_version`: adapter/parser version that produced the row
- `fetched_at`: fetch time in UTC ISO 8601
- `content_hash`: SHA-256 hash of stable source fields

Uniqueness:

- Unique partial index on `exchange, source_url` where `source_url` is not empty
- Unique partial index on `exchange, source_type, external_id` where `external_id` is not empty
- Unique index on `exchange, content_hash` as the fallback for sources with no stable URL or id

### `listing_events`

Stores normalized token listing events parsed from raw sources. One source can produce multiple events.

Columns:

- `id`: integer primary key
- `exchange`: exchange key
- `normalized_asset_id`: foreign key to `normalized_assets.id`
- `project_name`: normalized project name when available
- `token_symbol`: normalized token symbol
- `listing_type`: always `spot` in the first release
- `event_family`: lifecycle family for the exchange asset signal, initially `spot_listing`
- `event_kind`: `roadmap`, `launch_related`, `listing_announcement`, or `trading_start`
- `status`: `TBD`, `announced`, `trading_soon`, `trading_started`, or `unknown`
- `announcement_url`: best official URL for the listing signal
- `announcement_title`: source title
- `announcement_published_at`: source publication time in UTC ISO 8601
- `trading_start_time`: spot trading start time in UTC ISO 8601 when available
- `deposit_start_time`: deposit start time in UTC ISO 8601 when available
- `withdrawal_start_time`: withdrawal start time in UTC ISO 8601 when available
- `pairs`: JSON array of trading pairs such as `["ABC/USDT"]`
- `source_type`: copied from `raw_sources.source_type`
- `confidence`: `high`, `medium`, or `low`
- `source_precedence`: integer where a stronger source has a higher number
- `parser_version`: parser version that produced the event
- `raw_source_id`: foreign key to `raw_sources.id`
- `created_at`: row creation time in UTC ISO 8601
- `updated_at`: last update time in UTC ISO 8601

Uniqueness:

- Unique index on `exchange, normalized_asset_id, listing_type, event_family`
- A roadmap event and a later listing announcement for the same exchange/token update the same row because both belong to `event_family = "spot_listing"`
- Multiple trading pairs for the same source stay in the `pairs` JSON array
- Relistings create a new event only when the parser can identify a distinct listing cycle; otherwise the existing row is updated
- Changed trading times update the existing row and keep the latest official timing in the timing fields

Source precedence:

- `30`: official exchange announcement or listing page with timing
- `20`: official blog post or listing page without timing
- `10`: official X post without timing

When a new event matches an existing unique key, update the row if the new source precedence is higher, if it fills an empty timing field, or if it advances `status` from `TBD` to `announced`, `trading_soon`, or `trading_started`.

### `sync_runs`

Records every backfill, scheduled refresh, and manual refresh.

Columns:

- `id`: integer primary key
- `trigger_type`: `backfill`, `scheduled`, or `manual`
- `started_at`: run start time in UTC ISO 8601
- `finished_at`: run finish time in UTC ISO 8601
- `status`: `running`, `success`, or `failed`
- `exchanges_requested`: JSON array of exchange keys
- `raw_sources_found`: integer count
- `events_created`: integer count
- `events_updated`: integer count
- `error`: error text when failed

### `sync_run_exchange_results`

Stores per-exchange run results so one failed source does not hide successful sources.

Columns:

- `id`: integer primary key
- `sync_run_id`: foreign key to `sync_runs.id`
- `exchange`: exchange key
- `source_type`: source type attempted
- `status`: `success`, `failed`, or `skipped`
- `sources_found`: integer count
- `events_created`: integer count
- `events_updated`: integer count
- `pages_fetched`: integer count
- `error`: source-specific error text when failed

### `source_cursors`

Stores incremental pagination or time cursors per exchange/source.

Columns:

- `exchange`: exchange key
- `source_type`: source type
- `cursor_value`: source-specific cursor, URL, page marker, tweet id, or timestamp
- `last_success_at`: last successful fetch time in UTC ISO 8601
- `updated_at`: row update time in UTC ISO 8601

Primary key:

- `exchange, source_type`

## Project-Level Summary

The first release stores source and event tables only. A project-level summary can be generated from `listing_events` without becoming a separate persisted table at first.

Summary fields:

- `project_name`
- `token_symbol`
- `first_announcement_at`
- `earliest_trading_start_time`
- `exchanges`
- `source_count`
- `highest_confidence`
- `statuses`

The summary groups by `normalized_asset_id`. `token_symbol` and `project_name` are displayed as supporting context because tickers can collide.

## Status Rules

- `TBD`: official source indicates a roadmap or upcoming spot listing, but no trading time is available
- `announced`: official source announces the listing and includes enough token identity to track it
- `trading_soon`: official source includes a future trading start time
- `trading_started`: trading start time is in the past at parse time
- `unknown`: source text is official but does not contain enough timing or status language to classify confidently

## Confidence Rules

- `high`: official exchange announcement or official exchange listing page with token and timing
- `medium`: official X or blog source with token identity but missing full timing
- `low`: official source matched by title or category but parser could not confidently extract all fields

## Coinbase and Kraken X Rules

X search URLs are discovery inputs only. They are not stored as canonical source URLs. For each accepted official X post, persist the tweet/status URL or tweet id in `source_url` or `external_id`, the official account handle in `official_account`, the post timestamp in `published_at`, the full post text in `raw_text`, and a `detection_reason`.

Coinbase rules:

- `@CoinbaseMarkets` roadmap posts without a trading time produce `event_kind = "roadmap"` and `status = "TBD"`.
- Coinbase blog or official trading posts that add trading time update the same `spot_listing` event to `trading_soon` or `trading_started`.
- If an official Coinbase source says an asset was removed from or is no longer planned for the roadmap, keep the source row and update the event status to `unknown` unless a later listing source supersedes it.

Kraken rules:

- `@krakenlistings` posts without a trading time produce `event_kind = "roadmap"` or `listing_announcement` based on wording, and `status = "TBD"` when timing is missing.
- Kraken listings page entries with timing or availability update the same `spot_listing` event to the stronger status.
- If X and listings-page sources disagree, prefer the source with higher `source_precedence` and keep both raw source rows for audit.

## Collection Flow

Backfill:

1. Run a 3-month backfill across all configured exchanges.
2. Fetch source list pages or official X/blog sources.
3. Filter source items to the 3-month window.
4. Upsert raw source rows.
5. Parse listing events from each raw source.
6. Upsert listing event rows.
7. Record run metrics in `sync_runs`.

Daily scheduled refresh:

1. Run once per day.
2. Fetch recent source items for each exchange.
3. Use database upserts to avoid duplicates.
4. Update existing events when a later official source adds trading time or stronger confirmation.

Concurrency:

- Before any run starts, check for a `sync_runs` row with `status = "running"` that started less than 2 hours ago.
- If such a row exists, a manual or scheduled run returns a skipped result instead of starting a second run.
- A stale running row older than 2 hours can be marked `failed` with an explanatory error before a new run starts.

Backfill stop rules:

- Stop paginating a source when all items on the current page are older than the 3-month cutoff and the source is ordered newest-first.
- Stop after a source-specific maximum page count when publication dates are missing.
- Record missing-date sources in `sync_run_exchange_results.error` when the adapter cannot prove it covered the requested window.

Manual refresh:

1. Expose a local manual trigger in the app.
2. Run the same incremental refresh as the daily scheduled job.
3. Return run status, counts, and errors to the caller.

## Manual Trigger

The first implementation should add a small local endpoint to the existing app without wiring listing data into scoring:

- `POST /api/exchange-listings/sync`
- Optional request body: `{"exchanges": ["binance", "okx"]}`
- Response includes the `sync_runs` row status and counts

The endpoint should run synchronously for the first release if the refresh is fast enough. If source pages make the run slow, it can create a `sync_runs` row and process asynchronously in a later release.

## Scheduled Task

The daily task should be implemented as a separate CLI entry point and then connected to a macOS LaunchAgent, matching the existing local watcher pattern.

CLI:

```bash
python3 exchange_listing_sync.py --mode incremental
python3 exchange_listing_sync.py --mode backfill --months 3
```

LaunchAgent:

- Runs once per day
- Writes stdout and stderr to `logs/exchange_listing_sync.out.log` and `logs/exchange_listing_sync.err.log`
- Does not require GitHub storage

## Parser Requirements

Each exchange adapter should expose a fetching boundary and a pure parsing boundary:

- `fetch_sources(exchange, mode, cursor)` performs live network or browser collection and returns raw source dictionaries.
- `parse_events(raw_source)` is pure, has no network access, and returns normalized event dictionaries.

Event parsing should be tested with saved HTML/text fixtures so layout changes can be diagnosed without repeatedly hitting live websites. Fixture files should live under `tests/fixtures/exchange_listings/<exchange>/`, with expected parsed events stored as golden JSON next to each fixture.

The parser must preserve:

- Original title
- Original URL
- Publication time when available
- Raw text
- Token symbol
- Project name when extractable
- Trading pairs when extractable
- Deposit, withdrawal, and trading times when extractable

Time values are stored as UTC ISO 8601. If the source timezone is implicit, the adapter must encode the known exchange timezone or leave the field empty rather than guessing silently.

The implementation must define an exchange timezone map. Tests must cover multi-token announcements, Korean notices, implicit timezone handling, missing times, Coinbase official X roadmap-only posts, and Kraken official X roadmap-only posts.

## Review Subagent

Use a review subagent at two gates:

1. Spec and plan review before implementation:
   - Check whether schema fields are sufficient for scoring-system reuse later.
   - Check whether the source strategy handles Coinbase and Kraken roadmap-style signals.
   - Check whether the status and confidence rules are specific enough to test.
2. Implementation review after each major task:
   - Review migrations, upsert logic, and parser tests.
   - Review adapter behavior for duplicate handling and partial timing.
   - Review scheduled/manual sync behavior before enabling the LaunchAgent.

The reviewer should receive only the relevant spec, plan, code diff, and test output. It should not rely on chat history.

## Isolation From Existing Scoring

Implementation must live in separate exchange-listing modules. `web_app.py` may add only a thin route handler that delegates to those modules.

Do not modify:

- `score_project.py`
- `project_scorer.py`
- existing exchange progress functions
- workbook output behavior
- existing dashboard scoring paths

Regression tests must confirm that `/api/score`, `/api/request`, and the current scoring exchange-progress behavior remain unchanged.

## Testing

Minimum tests:

- Database initialization creates all tables and indexes.
- Raw source upsert deduplicates by URL, by external id, and by content hash fallback.
- Listing event upsert updates an existing event when stronger timing appears.
- Coinbase roadmap-only text produces `status = "TBD"` and `listing_type = "spot"`.
- Kraken roadmap-only text produces `status = "TBD"` and `listing_type = "spot"`.
- Spot listing announcement with future trading time produces `status = "trading_soon"`.
- Spot listing announcement with past trading time produces `status = "trading_started"`.
- A roadmap event later confirmed by an official listing announcement updates the existing event instead of creating a duplicate.
- A manual sync request skips when a fresh run is already active.
- Backfill CLI accepts `--months 3`.
- Manual sync endpoint records a `manual` sync run.

## Non-Goals

- Do not connect listing data to the scoring system in the first release.
- Do not modify the existing CoinMarketCap exchange coverage logic.
- Do not implement futures listing collection in the first release.
- Do not persist a project summary table until the event data shape is proven useful.
