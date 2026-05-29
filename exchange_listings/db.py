import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from exchange_listings.models import STATUS_ANNOUNCED, STATUS_TBD, STATUS_TRADING_SOON, STATUS_TRADING_STARTED


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS normalized_assets (
    id INTEGER PRIMARY KEY,
    canonical_symbol TEXT NOT NULL,
    project_name TEXT,
    slug TEXT,
    coingecko_id TEXT,
    rootdata_url TEXT,
    contract_addresses_json TEXT,
    aliases_json TEXT,
    identity_confidence TEXT NOT NULL,
    first_seen_at TEXT,
    last_seen_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_normalized_assets_symbol_slug
ON normalized_assets(canonical_symbol, slug)
WHERE slug IS NOT NULL AND slug != '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_normalized_assets_symbol_name
ON normalized_assets(canonical_symbol, project_name)
WHERE (slug IS NULL OR slug = '') AND project_name IS NOT NULL AND project_name != '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_normalized_assets_symbol_only
ON normalized_assets(canonical_symbol)
WHERE (slug IS NULL OR slug = '') AND (project_name IS NULL OR project_name = '');

CREATE TABLE IF NOT EXISTS raw_sources (
    id INTEGER PRIMARY KEY,
    exchange TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_url TEXT,
    title TEXT,
    published_at TEXT,
    raw_text TEXT,
    raw_payload_json TEXT,
    official_account TEXT,
    external_id TEXT,
    detection_reason TEXT,
    parser_version TEXT,
    fetched_at TEXT NOT NULL,
    content_hash TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_sources_exchange_url
ON raw_sources(exchange, source_url)
WHERE source_url IS NOT NULL AND source_url != '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_sources_exchange_type_external_id
ON raw_sources(exchange, source_type, external_id)
WHERE external_id IS NOT NULL AND external_id != '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_sources_exchange_content_hash
ON raw_sources(exchange, content_hash);

CREATE TABLE IF NOT EXISTS listing_events (
    id INTEGER PRIMARY KEY,
    exchange TEXT NOT NULL,
    normalized_asset_id INTEGER NOT NULL REFERENCES normalized_assets(id),
    project_name TEXT,
    token_symbol TEXT NOT NULL,
    listing_type TEXT NOT NULL,
    event_family TEXT NOT NULL,
    event_kind TEXT,
    status TEXT NOT NULL,
    announcement_url TEXT,
    announcement_title TEXT,
    announcement_published_at TEXT,
    trading_start_time TEXT,
    deposit_start_time TEXT,
    withdrawal_start_time TEXT,
    pairs TEXT,
    source_type TEXT,
    confidence TEXT,
    source_precedence INTEGER NOT NULL DEFAULT 0,
    parser_version TEXT,
    raw_source_id INTEGER REFERENCES raw_sources(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_listing_events_unique_event
ON listing_events(exchange, normalized_asset_id, listing_type, event_family);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY,
    trigger_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    exchanges_requested TEXT,
    raw_sources_found INTEGER NOT NULL DEFAULT 0,
    events_created INTEGER NOT NULL DEFAULT 0,
    events_updated INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS sync_run_exchange_results (
    id INTEGER PRIMARY KEY,
    sync_run_id INTEGER NOT NULL REFERENCES sync_runs(id),
    exchange TEXT NOT NULL,
    source_type TEXT NOT NULL,
    status TEXT NOT NULL,
    sources_found INTEGER NOT NULL DEFAULT 0,
    events_created INTEGER NOT NULL DEFAULT 0,
    events_updated INTEGER NOT NULL DEFAULT 0,
    pages_fetched INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS source_cursors (
    exchange TEXT NOT NULL,
    source_type TEXT NOT NULL,
    cursor_value TEXT,
    last_success_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (exchange, source_type)
);

CREATE TABLE IF NOT EXISTS sync_locks (
    lock_name TEXT PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES sync_runs(id),
    locked_at TEXT NOT NULL
);
"""

SYNC_LOCK_NAME = "exchange_listing_sync"


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(path))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: Path | str) -> None:
    db_path = Path(path)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _to_utc_iso(value) -> str:
    if value is None:
        return _utc_now()
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _clean_optional(value):
    if value == "":
        return None
    return value


def _json_dumps(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def stable_content_hash(raw_source: dict) -> str:
    stable_fields = {
        key: raw_source.get(key)
        for key in (
            "exchange",
            "source_type",
            "source_url",
            "title",
            "published_at",
            "raw_text",
            "raw_payload_json",
            "official_account",
            "external_id",
            "detection_reason",
            "parser_version",
        )
    }
    payload = json.dumps(stable_fields, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def upsert_raw_source(conn, raw_source: dict) -> int:
    exchange = raw_source["exchange"]
    source_type = raw_source["source_type"]
    source_url = _clean_optional(raw_source.get("source_url"))
    external_id = _clean_optional(raw_source.get("external_id"))
    content_hash = raw_source.get("content_hash") or stable_content_hash(raw_source)
    fetched_at = raw_source.get("fetched_at") or _utc_now()

    matches = []
    if source_url:
        row = conn.execute(
            "SELECT id FROM raw_sources WHERE exchange = ? AND source_url = ?",
            (exchange, source_url),
        ).fetchone()
        if row:
            matches.append(row[0])
    if external_id:
        row = conn.execute(
            """
            SELECT id
            FROM raw_sources
            WHERE exchange = ? AND source_type = ? AND external_id = ?
            """,
            (exchange, source_type, external_id),
        ).fetchone()
        if row:
            matches.append(row[0])
    if not matches:
        row = conn.execute(
            "SELECT id FROM raw_sources WHERE exchange = ? AND content_hash = ?",
            (exchange, content_hash),
        ).fetchone()
        if row:
            matches.append(row[0])

    existing_id = matches[0] if matches else None

    values = {
        "exchange": exchange,
        "source_type": source_type,
        "source_url": source_url,
        "title": raw_source.get("title"),
        "published_at": raw_source.get("published_at"),
        "raw_text": raw_source.get("raw_text"),
        "raw_payload_json": _json_dumps(raw_source.get("raw_payload_json")),
        "official_account": raw_source.get("official_account"),
        "external_id": external_id,
        "detection_reason": raw_source.get("detection_reason"),
        "parser_version": raw_source.get("parser_version"),
        "fetched_at": fetched_at,
        "content_hash": content_hash,
    }

    if existing_id is not None:
        for duplicate_id in sorted(set(matches) - {existing_id}):
            conn.execute(
                """
                UPDATE raw_sources
                SET source_url = NULL,
                    external_id = NULL
                WHERE id = ?
                """,
                (duplicate_id,),
            )
        conn.execute(
            """
            UPDATE raw_sources
            SET source_type = :source_type,
                source_url = :source_url,
                title = :title,
                published_at = :published_at,
                raw_text = :raw_text,
                raw_payload_json = :raw_payload_json,
                official_account = :official_account,
                external_id = :external_id,
                detection_reason = :detection_reason,
                parser_version = :parser_version,
                fetched_at = :fetched_at,
                content_hash = :content_hash
            WHERE id = :id
            """,
            {**values, "id": existing_id},
        )
        return existing_id

    cursor = conn.execute(
        """
        INSERT INTO raw_sources (
            exchange,
            source_type,
            source_url,
            title,
            published_at,
            raw_text,
            raw_payload_json,
            official_account,
            external_id,
            detection_reason,
            parser_version,
            fetched_at,
            content_hash
        )
        VALUES (
            :exchange,
            :source_type,
            :source_url,
            :title,
            :published_at,
            :raw_text,
            :raw_payload_json,
            :official_account,
            :external_id,
            :detection_reason,
            :parser_version,
            :fetched_at,
            :content_hash
        )
        """,
        values,
    )
    return cursor.lastrowid


def slugify_project_name(project_name: str | None) -> str | None:
    if not project_name:
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", project_name.lower()).strip("-")
    return slug or None


def upsert_normalized_asset(conn, asset: dict) -> int:
    canonical_symbol = (asset.get("canonical_symbol") or asset.get("token_symbol") or asset["symbol"]).upper()
    project_name = asset.get("project_name") or asset.get("name")
    slug = asset.get("slug") or slugify_project_name(project_name)
    now = _utc_now()
    first_seen_at = asset.get("first_seen_at") or now
    last_seen_at = asset.get("last_seen_at") or first_seen_at

    existing_id = None
    if slug:
        row = conn.execute(
            """
            SELECT id
            FROM normalized_assets
            WHERE canonical_symbol = ? AND slug = ?
            """,
            (canonical_symbol, slug),
        ).fetchone()
        existing_id = row[0] if row else None
    elif project_name:
        row = conn.execute(
            """
            SELECT id
            FROM normalized_assets
            WHERE canonical_symbol = ?
              AND project_name = ?
              AND (slug IS NULL OR slug = '')
            """,
            (canonical_symbol, project_name),
        ).fetchone()
        existing_id = row[0] if row else None
    else:
        row = conn.execute(
            """
            SELECT id
            FROM normalized_assets
            WHERE canonical_symbol = ?
              AND (slug IS NULL OR slug = '')
              AND (project_name IS NULL OR project_name = '')
            """,
            (canonical_symbol,),
        ).fetchone()
        existing_id = row[0] if row else None

    values = {
        "canonical_symbol": canonical_symbol,
        "project_name": project_name,
        "slug": slug,
        "coingecko_id": asset.get("coingecko_id"),
        "rootdata_url": asset.get("rootdata_url"),
        "contract_addresses_json": _json_dumps(asset.get("contract_addresses_json")),
        "aliases_json": _json_dumps(asset.get("aliases_json")),
        "identity_confidence": asset.get("identity_confidence", "low"),
        "first_seen_at": first_seen_at,
        "last_seen_at": last_seen_at,
        "created_at": now,
        "updated_at": now,
    }

    if existing_id is not None:
        conn.execute(
            """
            UPDATE normalized_assets
            SET project_name = COALESCE(:project_name, project_name),
                slug = COALESCE(:slug, slug),
                coingecko_id = COALESCE(:coingecko_id, coingecko_id),
                rootdata_url = COALESCE(:rootdata_url, rootdata_url),
                contract_addresses_json = COALESCE(:contract_addresses_json, contract_addresses_json),
                aliases_json = COALESCE(:aliases_json, aliases_json),
                identity_confidence = COALESCE(:identity_confidence, identity_confidence),
                last_seen_at = COALESCE(:last_seen_at, last_seen_at),
                updated_at = :updated_at
            WHERE id = :id
            """,
            {**values, "id": existing_id},
        )
        return existing_id

    cursor = conn.execute(
        """
        INSERT INTO normalized_assets (
            canonical_symbol,
            project_name,
            slug,
            coingecko_id,
            rootdata_url,
            contract_addresses_json,
            aliases_json,
            identity_confidence,
            first_seen_at,
            last_seen_at,
            created_at,
            updated_at
        )
        VALUES (
            :canonical_symbol,
            :project_name,
            :slug,
            :coingecko_id,
            :rootdata_url,
            :contract_addresses_json,
            :aliases_json,
            :identity_confidence,
            :first_seen_at,
            :last_seen_at,
            :created_at,
            :updated_at
        )
        """,
        values,
    )
    return cursor.lastrowid


def _status_rank(status: str | None) -> int:
    return {
        STATUS_TBD: 1,
        STATUS_ANNOUNCED: 2,
        STATUS_TRADING_SOON: 3,
        STATUS_TRADING_STARTED: 4,
    }.get(status, 0)


def _copy_non_empty_incoming(merged: dict, values: dict, columns: tuple[str, ...]) -> None:
    for column in columns:
        if column in ("created_at", "updated_at"):
            continue
        if values[column] is not None:
            merged[column] = values[column]


def upsert_listing_event(conn, event: dict) -> int:
    now = _utc_now()
    values = {
        "exchange": event["exchange"],
        "normalized_asset_id": event["normalized_asset_id"],
        "project_name": event.get("project_name"),
        "token_symbol": (event.get("token_symbol") or event.get("symbol")).upper(),
        "listing_type": event["listing_type"],
        "event_family": event["event_family"],
        "event_kind": event.get("event_kind"),
        "status": event["status"],
        "announcement_url": event.get("announcement_url"),
        "announcement_title": event.get("announcement_title"),
        "announcement_published_at": event.get("announcement_published_at"),
        "trading_start_time": event.get("trading_start_time"),
        "deposit_start_time": event.get("deposit_start_time"),
        "withdrawal_start_time": event.get("withdrawal_start_time"),
        "pairs": _json_dumps(event.get("pairs")),
        "source_type": event.get("source_type"),
        "confidence": event.get("confidence"),
        "source_precedence": event.get("source_precedence", 0),
        "parser_version": event.get("parser_version"),
        "raw_source_id": event.get("raw_source_id"),
        "created_at": now,
        "updated_at": now,
    }

    columns = tuple(values.keys())
    existing = conn.execute(
        f"""
        SELECT id, {", ".join(columns)}
        FROM listing_events
        WHERE exchange = ?
          AND normalized_asset_id = ?
          AND listing_type = ?
          AND event_family = ?
        """,
        (
            values["exchange"],
            values["normalized_asset_id"],
            values["listing_type"],
            values["event_family"],
        ),
    ).fetchone()

    if existing is None:
        placeholders = ", ".join(f":{column}" for column in columns)
        cursor = conn.execute(
            f"""
            INSERT INTO listing_events ({", ".join(columns)})
            VALUES ({placeholders})
            """,
            values,
        )
        return cursor.lastrowid

    existing_id = existing[0]
    existing_values = dict(zip(columns, existing[1:]))
    fills_timing = any(
        existing_values[field] is None and values[field] is not None
        for field in ("trading_start_time", "deposit_start_time", "withdrawal_start_time")
    )
    higher_precedence = values["source_precedence"] > existing_values["source_precedence"]
    same_precedence = values["source_precedence"] == existing_values["source_precedence"]
    corrects_timing = any(
        same_precedence and values[field] is not None and values[field] != existing_values[field]
        for field in ("trading_start_time", "deposit_start_time", "withdrawal_start_time")
    )
    advances_status = _status_rank(values["status"]) > _status_rank(existing_values["status"])

    if higher_precedence or fills_timing or corrects_timing or advances_status:
        merged = existing_values.copy()
        if higher_precedence:
            _copy_non_empty_incoming(merged, values, columns)
        else:
            if advances_status:
                merged["status"] = values["status"]
            for field in ("trading_start_time", "deposit_start_time", "withdrawal_start_time"):
                if values[field] is not None and (existing_values[field] is None or same_precedence):
                    merged[field] = values[field]
        merged["created_at"] = existing_values["created_at"]
        merged["updated_at"] = now
        conn.execute(
            """
            UPDATE listing_events
            SET project_name = :project_name,
                token_symbol = :token_symbol,
                event_kind = :event_kind,
                status = :status,
                announcement_url = :announcement_url,
                announcement_title = :announcement_title,
                announcement_published_at = :announcement_published_at,
                trading_start_time = :trading_start_time,
                deposit_start_time = :deposit_start_time,
                withdrawal_start_time = :withdrawal_start_time,
                pairs = :pairs,
                source_type = :source_type,
                confidence = :confidence,
                source_precedence = :source_precedence,
                parser_version = :parser_version,
                raw_source_id = :raw_source_id,
                updated_at = :updated_at
            WHERE id = :id
            """,
            {**merged, "id": existing_id},
        )

    return existing_id


def start_sync_run(conn, trigger_type: str, exchanges=(), now=None) -> dict:
    started_at = _to_utc_iso(now)
    current_time = _parse_utc_iso(started_at)
    started_transaction = not conn.in_transaction
    if started_transaction:
        conn.execute("BEGIN IMMEDIATE")

    try:
        lock_row = conn.execute(
            """
            SELECT sync_locks.run_id, sync_locks.locked_at, sync_runs.status, sync_runs.started_at
            FROM sync_locks
            LEFT JOIN sync_runs ON sync_runs.id = sync_locks.run_id
            WHERE sync_locks.lock_name = ?
            """,
            (SYNC_LOCK_NAME,),
        ).fetchone()
        if lock_row is not None:
            run_id, locked_at, status, run_started_at = lock_row
            reference_time = run_started_at or locked_at
            age = current_time - _parse_utc_iso(reference_time)
            if status == "running" and age < timedelta(hours=2):
                if started_transaction:
                    conn.commit()
                return {
                    "status": "skipped",
                    "run_id": run_id,
                    "skipped_reason": "fresh_running_sync",
                }
            if status == "running":
                conn.execute(
                    """
                    UPDATE sync_runs
                    SET status = 'failed',
                        finished_at = ?,
                        error = ?
                    WHERE id = ?
                    """,
                    (started_at, "stale running sync exceeded 2 hours", run_id),
                )
            conn.execute("DELETE FROM sync_locks WHERE lock_name = ?", (SYNC_LOCK_NAME,))

        running_rows = conn.execute(
            """
            SELECT id, started_at
            FROM sync_runs
            WHERE status = 'running'
            ORDER BY started_at ASC
            """
        ).fetchall()

        for run_id, row_started_at in running_rows:
            age = current_time - _parse_utc_iso(row_started_at)
            if age < timedelta(hours=2):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO sync_locks (lock_name, run_id, locked_at)
                    VALUES (?, ?, ?)
                    """,
                    (SYNC_LOCK_NAME, run_id, row_started_at),
                )
                if started_transaction:
                    conn.commit()
                return {
                    "status": "skipped",
                    "run_id": run_id,
                    "skipped_reason": "fresh_running_sync",
                }
            conn.execute(
                """
                UPDATE sync_runs
                SET status = 'failed',
                    finished_at = ?,
                    error = ?
                WHERE id = ?
                """,
                (started_at, "stale running sync exceeded 2 hours", run_id),
            )

        cursor = conn.execute(
            """
            INSERT INTO sync_runs (
                trigger_type,
                started_at,
                status,
                exchanges_requested
            )
            VALUES (?, ?, 'running', ?)
            """,
            (trigger_type, started_at, _json_dumps(list(exchanges))),
        )
        run_id = cursor.lastrowid
        conn.execute(
            """
            INSERT INTO sync_locks (lock_name, run_id, locked_at)
            VALUES (?, ?, ?)
            """,
            (SYNC_LOCK_NAME, run_id, started_at),
        )
        if started_transaction:
            conn.commit()
        return {"status": "running", "run_id": run_id}
    except Exception:
        if started_transaction:
            conn.rollback()
        raise


def finish_sync_run(
    conn,
    run_id: int,
    status: str,
    *,
    raw_sources_found: int = 0,
    events_created: int = 0,
    events_updated: int = 0,
    error: str | None = None,
    now=None,
) -> None:
    conn.execute(
        """
        UPDATE sync_runs
        SET finished_at = ?,
            status = ?,
            raw_sources_found = ?,
            events_created = ?,
            events_updated = ?,
            error = ?
        WHERE id = ?
        """,
        (
            _to_utc_iso(now),
            status,
            raw_sources_found,
            events_created,
            events_updated,
            error,
            run_id,
        ),
    )
    conn.execute("DELETE FROM sync_locks WHERE run_id = ?", (run_id,))


def record_exchange_result(
    conn,
    sync_run_id: int,
    *,
    exchange: str,
    source_type: str,
    status: str,
    sources_found: int = 0,
    events_created: int = 0,
    events_updated: int = 0,
    pages_fetched: int = 0,
    error: str | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO sync_run_exchange_results (
            sync_run_id,
            exchange,
            source_type,
            status,
            sources_found,
            events_created,
            events_updated,
            pages_fetched,
            error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sync_run_id,
            exchange,
            source_type,
            status,
            sources_found,
            events_created,
            events_updated,
            pages_fetched,
            error,
        ),
    )
    return cursor.lastrowid
