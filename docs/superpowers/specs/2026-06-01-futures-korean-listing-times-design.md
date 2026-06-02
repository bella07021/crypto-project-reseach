# Futures and Korean Listing Times Design

## Goal

Extend the standalone exchange listing database so it can identify:

- Whether a token has a Binance Futures or perpetual market.
- The best known Binance Futures listing time.
- Korean exchange listing times for Upbit and Bithumb.

This remains an ingestion/storage feature. It does not change the scoring model or main score display until a later integration step is requested.

## Current Context

The existing listing database stores normalized assets, raw source rows, normalized listing events, sync runs, and source cursors. It currently treats spot listings as the primary event family and keeps `listing_type` available for future futures support.

Existing useful behavior:

- `trading_start_time`, `deposit_start_time`, `withdrawal_start_time`, and `pairs` already exist on `listing_events`.
- Source precedence already decides which source can update an existing event.
- Upbit and Bithumb are already part of the live source set.
- CoinMarketCap web scraping already detects current market-pair coverage but currently labels scraped rows as spot only.

## Scope

In scope:

- Binance USD-M and COIN-M perpetual/futures listing detection.
- Binance Futures listing time extraction from official Binance announcements when available.
- ChainCatcher flash/article extraction as a secondary timing source for Binance Futures and Korean listings.
- CoinMarketCap current-market confirmation for Binance futures/perpetual pairs.
- Upbit and Bithumb trading start time extraction from official announcements.
- UTC-normalized timing fields stored in existing `listing_events` columns.

Out of scope:

- Changing project scores.
- Ranking the importance of futures listings in scoring.
- Tracking non-Binance futures exchanges.
- Full historical backfill beyond the existing 3-month default.
- Using unofficial social posts as high-confidence timing sources.

## Source Strategy

### Binance Futures

Use three tiers of evidence:

1. Binance official futures announcements.
   - Highest confidence.
   - Source type: `exchange_announcement`.
   - Expected to provide the actual listing time in UTC or local time text.
2. ChainCatcher.
   - Medium confidence.
   - Source type: `news_flash`.
   - Useful because it often republishes official Binance futures launch times in a compact format.
3. CoinMarketCap market pairs.
   - Confirmation only.
   - Source type: `market_snapshot`.
   - Confirms that a Binance futures/perpetual market currently exists, but should not be used as the listing time source.

CMC rows should set `trading_start_time = null` unless CMC exposes a source timestamp in a future version. They can still create or update an event with `status = "trading_started"` and `confidence = "low"` when no stronger event exists.

### Korean Exchanges

Use official exchange notices first:

- Upbit: list page plus notice detail page.
- Bithumb: current notice list payload plus detail page when title/list payload does not include enough timing.

Use ChainCatcher only as a fallback timing source when the official notice cannot be fetched or does not expose a specific time.

Korean exchange times must be interpreted as Korea Standard Time unless the source explicitly says otherwise. Store final values in UTC ISO 8601.

## Data Model Changes

Add listing type and event family constants:

- `LISTING_TYPE_FUTURES = "futures"`
- `LISTING_TYPE_PERPETUAL = "perpetual"`
- `EVENT_FAMILY_FUTURES_LISTING = "futures_listing"`

The existing `listing_events` table can store the new events without schema changes because its uniqueness key includes `listing_type` and `event_family`.

Expected event rows:

- Binance spot listing remains `listing_type = "spot"`, `event_family = "spot_listing"`.
- Binance Futures/Perpetual listing becomes `listing_type = "perpetual"` when the source says perpetual, otherwise `listing_type = "futures"`, and `event_family = "futures_listing"`.
- Upbit/Bithumb KRW listings remain `listing_type = "spot"`, `event_family = "spot_listing"`, with more complete `trading_start_time`.

Add raw source type constants:

- `SOURCE_TYPE_MARKET_SNAPSHOT = "market_snapshot"`
- `SOURCE_TYPE_NEWS_FLASH = "news_flash"`

No new tables are required for the first version.

## Source Precedence

Use the existing numeric precedence pattern:

- `40`: official exchange announcement with explicit timing.
- `30`: official exchange announcement without explicit timing.
- `25`: ChainCatcher or similar news source with explicit timing and a source attribution to an official announcement.
- `15`: ChainCatcher or similar news source without explicit timing.
- `5`: CMC market snapshot.

Update rules:

- Higher precedence source updates all non-empty fields.
- Same-precedence source may correct timing if it has a different explicit time.
- Lower-precedence source can only fill empty timing fields if the existing source has no timing and no higher-confidence source has already set the field.
- CMC must not overwrite official or ChainCatcher timing.

## Parsing Rules

### Binance Futures Text

Detect futures/perpetual announcements with phrases including:

- `Binance Futures will launch`
- `Binance will launch`
- `USDT perpetual contract`
- `USD-M perpetual`
- `COIN-M perpetual`
- `U-based perpetual contract`
- `币安合约`
- `永续合约`

Extract symbols from:

- `SLXUSDT`
- `SLX/USDT`
- `SLX USDT perpetual`

Normalize pairs as `SLX/USDT` when possible.

Extract listing times from:

- ISO or UTC text.
- Chinese date/time forms such as `2026 年 6 月 1 日 22:30（UTC+8）`.
- English forms such as `on June 1, 2026 at 14:30 UTC`.
- Multi-symbol schedules where each symbol has its own time.

If multiple symbols and times appear in the same article, create one event per symbol with the matching time.

### ChainCatcher

Parse article pages and flash list items into raw sources.

Store:

- `title`
- `published_at`
- source URL
- body text
- linked official source URL when present
- `raw_payload_json` with article id and tags when available

ChainCatcher timing is valid only when the text includes a concrete launch time. If the text only says an exchange will list a token, store the event with `trading_start_time = null`.

### Upbit

Current list parsing finds listing notices but does not reliably include published or trading times. The adapter should fetch the notice detail page for matched listing notices and parse:

- Notice publication time when available.
- Trading market, especially KRW market.
- Trading start time.
- Deposit or withdrawal start time when present.

Korean text patterns to support:

- `거래지원 시작 시점`
- `거래 지원 개시`
- `거래 시작`
- `KRW 마켓`
- `원화 마켓`

### Bithumb

The existing list payload often includes `publicationDateTime` and sometimes includes a trading-open hint in the title. The adapter should additionally parse detail pages when needed.

Korean text patterns to support:

- `거래 오픈`
- `거래 시작`
- `원화 마켓 추가`
- `오후 6시 예정`

When only a date is present and no concrete time is present, leave `trading_start_time = null` and keep `announcement_published_at`.

## CMC Confirmation

Update CMC scraping/API normalization so market pairs can return `category = "spot"`, `category = "derivatives"`, `category = "futures"`, or `category = "perpetual"` when available.

For web scraping:

- Use the CMC Markets tab filters or underlying market-pair endpoint when available.
- Do not infer futures from the visible default spot table.
- Treat Binance Alpha as unrelated to Binance Futures.

For CMC Pro API:

- Query market pairs with `category = "all"` or futures/derivatives category.
- Classify Binance derivatives/futures/perpetual pairs as Binance Futures confirmation.

## Output Shape

Project-level derived summaries can expose:

- `binance_futures_listed`: boolean
- `binance_futures_pairs`: array
- `binance_futures_trading_start_time`: earliest known UTC time
- `binance_futures_time_source`: source URL or source type
- `korean_listings`: array of exchange, pair, trading start time, and source URL

These fields are derived from `listing_events`; they do not need a new persisted summary table in this step.

## Error Handling

- If ChainCatcher is blocked, continue with official sources and CMC confirmation.
- If CMC is blocked, keep official/ChainCatcher events and skip market confirmation.
- If a detail page fails after a list item is found, store the list item as a raw source and create an event without timing.
- Record per-source errors in `sync_run_exchange_results` so one failing source does not fail the entire run.

## Tests

Add focused parser and adapter tests:

- Binance official futures single-symbol listing with UTC time.
- Binance official futures multi-symbol listing with per-symbol times.
- ChainCatcher Binance Futures article with title, body, published time, and source URL.
- CMC Binance futures confirmation does not set `trading_start_time`.
- CMC Binance Alpha is ignored for futures.
- Upbit detail page extracts KST trading time and stores UTC.
- Bithumb title/list payload extracts `오후 6시` when date context is available.
- Lower-precedence CMC source does not overwrite official timing.

Run:

- `python3 -m unittest tests.test_exchange_listings_parsers -v`
- `python3 -m unittest tests.test_exchange_listing_adapters -v`
- `python3 -m unittest tests.test_exchange_listings_db -v`

## Open Questions

- Whether to normalize Binance Futures as only `perpetual` or keep both `futures` and `perpetual`. Recommended: use `perpetual` when explicit, fallback to `futures`.
- Whether ChainCatcher should be a general news source package or only a narrow adapter for listing-time articles. Recommended: narrow adapter first.
- Whether derived summary fields should appear in the web UI immediately. Recommended: keep storage-only for this step, then wire UI/scoring separately after sample data looks right.
