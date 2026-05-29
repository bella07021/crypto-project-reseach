# Exchange Listing Database Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone SQLite database and sync skeleton for upcoming spot listing signals, without changing existing scoring behavior.

**Architecture:** Add separate exchange-listing modules for schema, repository upserts, parser primitives, sync orchestration, and CLI. `web_app.py` gets only a thin manual-sync route that delegates to the new sync module. Network adapters start behind a fetch/parse boundary with fixture-driven parser tests so live page issues can be handled after the data core is proven.

**Tech Stack:** Python standard library (`sqlite3`, `argparse`, `dataclasses`, `json`, `datetime`, `hashlib`), existing `unittest` test style, existing `ThreadingHTTPServer` route style, macOS LaunchAgent pattern already used in `launchd/`.

---

## File Structure

- Create `exchange_listings/__init__.py`: package marker.
- Create `exchange_listings/models.py`: dataclasses and constants for raw sources, listing events, exchange keys, statuses, source precedence, and timezone map.
- Create `exchange_listings/db.py`: SQLite schema initialization, connection helper, asset/raw/event/sync-run upsert functions, concurrency guard.
- Create `exchange_listings/parsers.py`: pure parser helpers for source text to event dictionaries, including Coinbase/Kraken roadmap text.
- Create `exchange_listings/sync.py`: orchestration for backfill/incremental/manual runs using adapter boundaries.
- Create `exchange_listing_sync.py`: CLI entry point for `--mode incremental` and `--mode backfill --months 3`.
- Create `launchd/com.bella.exchange-listing-sync.plist`: daily LaunchAgent definition.
- Modify `web_app.py`: add `POST /api/exchange-listings/sync` delegating to `exchange_listings.sync.run_manual_sync`.
- Add tests in `tests/test_exchange_listings_db.py`, `tests/test_exchange_listings_parsers.py`, `tests/test_exchange_listing_sync.py`, and focused additions to `tests/test_web_app.py`.

## Task 1: SQLite Schema and Repository

**Files:**
- Create: `exchange_listings/__init__.py`
- Create: `exchange_listings/models.py`
- Create: `exchange_listings/db.py`
- Test: `tests/test_exchange_listings_db.py`

- [ ] **Step 1: Write failing schema initialization test**

Add `tests/test_exchange_listings_db.py` with a test that opens a temporary SQLite file, calls `init_db(path)`, and asserts these tables exist: `normalized_assets`, `raw_sources`, `listing_events`, `sync_runs`, `sync_run_exchange_results`, `source_cursors`.

Run: `python3 -m unittest tests.test_exchange_listings_db.ExchangeListingDbTests.test_init_db_creates_expected_tables -v`

Expected: FAIL because `exchange_listings.db` does not exist yet.

- [ ] **Step 2: Implement minimal schema**

Create `exchange_listings/models.py` with constants:

```python
EXCHANGES = ("binance", "okx", "bybit", "coinbase", "upbit", "bithumb", "kucoin", "gate", "mexc", "bitget", "kraken")
LISTING_TYPE_SPOT = "spot"
EVENT_FAMILY_SPOT_LISTING = "spot_listing"
STATUS_TBD = "TBD"
STATUS_ANNOUNCED = "announced"
STATUS_TRADING_SOON = "trading_soon"
STATUS_TRADING_STARTED = "trading_started"
STATUS_UNKNOWN = "unknown"
SOURCE_PRECEDENCE_X = 10
SOURCE_PRECEDENCE_BLOG = 20
SOURCE_PRECEDENCE_ANNOUNCEMENT = 30
```

Create `exchange_listings/db.py` with `init_db(path: Path | str) -> None` that executes `CREATE TABLE IF NOT EXISTS` statements for all six tables from the spec.

- [ ] **Step 3: Verify schema test passes**

Run: `python3 -m unittest tests.test_exchange_listings_db.ExchangeListingDbTests.test_init_db_creates_expected_tables -v`

Expected: PASS.

- [ ] **Step 4: Write failing raw source upsert dedupe tests**

Add tests for `upsert_raw_source`:

- Same `exchange + source_url` returns the same row id.
- Same `exchange + source_type + external_id` returns the same row id.
- Same `exchange + content_hash` returns the same row id when URL/id are absent.

Run: `python3 -m unittest tests.test_exchange_listings_db.ExchangeListingDbTests -v`

Expected: FAIL because `upsert_raw_source` is missing.

- [ ] **Step 5: Implement raw source hashing and upsert**

Add `stable_content_hash(raw_source: dict) -> str` and `upsert_raw_source(conn, raw_source) -> int`. Use partial unique indexes for URL and external id plus a unique fallback index on `exchange, content_hash`.

- [ ] **Step 6: Write failing asset/event upsert tests**

Add tests that:

- `upsert_normalized_asset` creates a low-confidence asset from only symbol/name.
- `upsert_listing_event` creates one event for a Coinbase roadmap row.
- A later official announcement with higher `source_precedence` updates the same event and advances status from `TBD` to `trading_soon`.

Run: `python3 -m unittest tests.test_exchange_listings_db.ExchangeListingDbTests -v`

Expected: FAIL because asset/event upserts are missing.

- [ ] **Step 7: Implement asset/event upserts**

Add `slugify_project_name`, `upsert_normalized_asset`, and `upsert_listing_event`. Use the unique event key `exchange, normalized_asset_id, listing_type, event_family`, and update when the new event has higher precedence, fills timing, or advances status.

- [ ] **Step 8: Write and pass sync run guard tests**

Add tests for `start_sync_run`:

- Creates a `running` row when no fresh run exists.
- Returns skipped when a running row started less than 2 hours ago.
- Marks a stale running row failed when older than 2 hours.

Implement `start_sync_run`, `finish_sync_run`, and `record_exchange_result`.

Run: `python3 -m unittest tests.test_exchange_listings_db -v`

Expected: PASS.

- [ ] **Step 9: Commit Task 1**

Run:

```bash
git add exchange_listings tests/test_exchange_listings_db.py
git commit -m "feat: add exchange listing sqlite repository"
```

## Task 2: Pure Parser Primitives and Fixtures

**Files:**
- Modify: `exchange_listings/parsers.py`
- Modify: `exchange_listings/models.py`
- Test: `tests/test_exchange_listings_parsers.py`

- [ ] **Step 1: Write failing Coinbase and Kraken roadmap parser tests**

Add parser tests with raw official X text dictionaries:

- Coinbase roadmap-only text produces `status = "TBD"`, `listing_type = "spot"`, `event_kind = "roadmap"`, `source_precedence = 10`.
- Kraken listing post without timing produces `status = "TBD"`, `listing_type = "spot"`.

Run: `python3 -m unittest tests.test_exchange_listings_parsers -v`

Expected: FAIL because parser module is missing.

- [ ] **Step 2: Implement minimal roadmap parser**

Create `exchange_listings/parsers.py` with `parse_events(raw_source: dict, now: datetime | None = None) -> list[dict]`. Start with official X parser rules for Coinbase and Kraken. Extract token symbols from parenthesized symbols like `(ABC)` and simple `$ABC` tokens.

- [ ] **Step 3: Verify roadmap parser tests pass**

Run: `python3 -m unittest tests.test_exchange_listings_parsers -v`

Expected: PASS.

- [ ] **Step 4: Write failing future/past trading time tests**

Add tests using announcement text with explicit trading start times:

- Future trading time produces `trading_soon`.
- Past trading time produces `trading_started`.
- Missing time but listing language produces `announced`.

Use fixed `now=datetime(2026, 5, 29, tzinfo=timezone.utc)`.

- [ ] **Step 5: Implement timing parser**

Add conservative regex parsing for ISO-like UTC strings and common `YYYY-MM-DD HH:MM UTC` strings. Store UTC ISO strings and leave fields empty when timezone is ambiguous.

- [ ] **Step 6: Add multi-token and Korean notice tests**

Add tests for:

- Multi-token announcement title extracts multiple event rows.
- Korean notice with token symbol in parentheses extracts the symbol and preserves `project_name` if present.

Implement only the parsing needed for the fixtures.

- [ ] **Step 7: Commit Task 2**

Run:

```bash
git add exchange_listings/parsers.py exchange_listings/models.py tests/test_exchange_listings_parsers.py
git commit -m "feat: parse exchange listing signals"
```

## Task 3: Sync Orchestration and CLI

**Files:**
- Create: `exchange_listings/sync.py`
- Create: `exchange_listing_sync.py`
- Test: `tests/test_exchange_listing_sync.py`

- [ ] **Step 1: Write failing manual sync orchestration test**

Test `run_manual_sync(db_path, exchanges=["coinbase"], fetcher=fake_fetcher)`:

- Creates a `manual` sync run.
- Upserts one raw source and one listing event from the fake fetcher.
- Records one `sync_run_exchange_results` row.

Run: `python3 -m unittest tests.test_exchange_listing_sync.ExchangeListingSyncTests.test_manual_sync_records_run_and_events -v`

Expected: FAIL because sync module is missing.

- [ ] **Step 2: Implement sync orchestrator**

Implement:

- `run_sync(db_path, trigger_type, mode, months=3, exchanges=None, fetcher=None)`
- `run_manual_sync(db_path, exchanges=None, fetcher=None)`
- default fetcher returning an empty list per exchange for now

Ensure one exchange failure records a failed exchange result without failing successful exchanges.

- [ ] **Step 3: Write failing CLI tests**

Test CLI argument parsing by calling `exchange_listing_sync.main(["--mode", "backfill", "--months", "3", "--db", tmp_path])` with a patched empty fetcher.

Expected: FAIL until CLI supports args and db path.

- [ ] **Step 4: Implement CLI**

Create `exchange_listing_sync.py` with:

```bash
python3 exchange_listing_sync.py --mode incremental
python3 exchange_listing_sync.py --mode backfill --months 3
```

Support `--db data/exchange_listings.sqlite`, `--exchange` repeated, and JSON stdout summary.

- [ ] **Step 5: Verify sync tests pass**

Run: `python3 -m unittest tests.test_exchange_listing_sync -v`

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

Run:

```bash
git add exchange_listings/sync.py exchange_listing_sync.py tests/test_exchange_listing_sync.py
git commit -m "feat: add exchange listing sync cli"
```

## Task 4: Manual Web Endpoint and Scoring Isolation

**Files:**
- Modify: `web_app.py`
- Modify: `tests/test_web_app.py`

- [ ] **Step 1: Write failing web endpoint test**

Add a test that instantiates `CryptoScoringHandler` through a lightweight HTTP server or tests a helper function if introduced. It should patch `web_app.run_exchange_listing_manual_sync` and assert:

- `POST /api/exchange-listings/sync` returns `ok: true`.
- Request body exchanges are forwarded.
- Existing `/api/score` path still calls `score_payload`.

Run: `python3 -m unittest tests.test_web_app.WebAppTests.test_exchange_listing_sync_endpoint_delegates -v`

Expected: FAIL because route is missing.

- [ ] **Step 2: Add thin route delegation**

In `web_app.py`, import only the manual sync wrapper from `exchange_listings.sync`. Add a helper `run_exchange_listing_manual_sync(data: dict) -> dict` if needed for easy testing. Do not modify scoring functions, exchange progress functions, workbook output, or dashboard logic.

- [ ] **Step 3: Add regression tests for existing scoring routes**

Add focused tests that existing `/api/score` and `/api/request` routing still delegates to `score_payload` and `create_project_request` as before.

- [ ] **Step 4: Verify web tests pass**

Run: `python3 -m unittest tests.test_web_app -v`

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

Run:

```bash
git add web_app.py tests/test_web_app.py
git commit -m "feat: add manual exchange listing sync endpoint"
```

## Task 5: LaunchAgent and Full Verification

**Files:**
- Create: `launchd/com.bella.exchange-listing-sync.plist`
- Modify: `README.md`

- [ ] **Step 1: Add LaunchAgent file**

Create a LaunchAgent that runs:

```bash
python3 exchange_listing_sync.py --mode incremental
```

It should run once per day and write logs to:

- `logs/exchange_listing_sync.out.log`
- `logs/exchange_listing_sync.err.log`

- [ ] **Step 2: Document local commands**

Update `README.md` with:

```bash
python3 exchange_listing_sync.py --mode backfill --months 3
python3 exchange_listing_sync.py --mode incremental
```

Include install/check commands mirroring the existing watcher section.

- [ ] **Step 3: Run full tests**

Run: `python3 -m unittest discover -v`

Expected: PASS.

- [ ] **Step 4: Run a local empty incremental sync smoke**

Run: `python3 exchange_listing_sync.py --mode incremental --db /tmp/exchange_listings_smoke.sqlite`

Expected: JSON summary with `ok: true` and no crash.

- [ ] **Step 5: Final code review subagent**

Dispatch a review subagent with the spec, plan, git diff, and test output. Fix Critical and Important issues before final response.

- [ ] **Step 6: Commit Task 5**

Run:

```bash
git add launchd/com.bella.exchange-listing-sync.plist README.md
git commit -m "docs: add exchange listing sync schedule"
```

## Self-Review

- Spec coverage: The plan covers SQLite schema, raw/event upserts, normalized assets, Coinbase/Kraken TBD parsing, manual endpoint, CLI, daily LaunchAgent, sync run state, and scoring isolation.
- Placeholder scan: No implementation step relies on TODO/TBD placeholders. Live exchange scraping adapters are intentionally behind a default empty fetcher in this implementation phase, which matches the first safe database/sync skeleton.
- Type consistency: Function names are stable across tasks: `init_db`, `upsert_raw_source`, `upsert_normalized_asset`, `upsert_listing_event`, `run_sync`, `run_manual_sync`, and `parse_events`.
