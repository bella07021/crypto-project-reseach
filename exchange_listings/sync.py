from pathlib import Path

from exchange_listings import db
from exchange_listings.models import EXCHANGES
from exchange_listings.parsers import parse_events


def default_fetcher(exchange: str, *, mode: str, months: int) -> list[dict]:
    return []


def run_manual_sync(db_path, exchanges=None, fetcher=None) -> dict:
    return run_sync(
        db_path,
        trigger_type="manual",
        mode="incremental",
        months=3,
        exchanges=exchanges,
        fetcher=fetcher,
    )


def run_sync(db_path, trigger_type, mode, months=3, exchanges=None, fetcher=None) -> dict:
    selected_exchanges = _normalize_exchanges(exchanges)
    fetch = fetcher or default_fetcher
    path = Path(db_path)
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)
    db.init_db(path)

    with db.connect(path) as conn:
        run = db.start_sync_run(conn, trigger_type, exchanges=selected_exchanges)
        if run["status"] == "skipped":
            return {
                "ok": False,
                "status": "skipped",
                "run_id": run["run_id"],
                "skipped_reason": run["skipped_reason"],
                "exchanges": [],
                "raw_sources_found": 0,
                "events_created": 0,
                "events_updated": 0,
            }

        run_id = run["run_id"]
        exchange_summaries = []
        total_sources = 0
        total_created = 0
        total_updated = 0
        failures = []

        for exchange in selected_exchanges:
            try:
                raw_sources = list(fetch(exchange, mode=mode, months=months))
                result = _process_exchange(conn, run_id, exchange, raw_sources)
                total_sources += result["sources_found"]
                total_created += result["events_created"]
                total_updated += result["events_updated"]
                exchange_summaries.append(result)
            except Exception as exc:
                error = str(exc)
                failures.append(f"{exchange}: {error}")
                db.record_exchange_result(
                    conn,
                    run_id,
                    exchange=exchange,
                    source_type="all",
                    status="failed",
                    error=error,
                )
                exchange_summaries.append(
                    {
                        "exchange": exchange,
                        "source_type": "all",
                        "status": "failed",
                        "sources_found": 0,
                        "events_created": 0,
                        "events_updated": 0,
                        "error": error,
                    }
                )

        final_status = "failed" if failures and len(failures) == len(selected_exchanges) else "success"
        db.finish_sync_run(
            conn,
            run_id,
            final_status,
            raw_sources_found=total_sources,
            events_created=total_created,
            events_updated=total_updated,
            error="; ".join(failures) if failures else None,
        )

    return {
        "ok": final_status == "success",
        "status": final_status,
        "run_id": run_id,
        "mode": mode,
        "months": months,
        "exchanges": exchange_summaries,
        "raw_sources_found": total_sources,
        "events_created": total_created,
        "events_updated": total_updated,
    }


def _normalize_exchanges(exchanges) -> tuple[str, ...]:
    if exchanges is None:
        return EXCHANGES
    return tuple(exchange.lower() for exchange in exchanges)


def _process_exchange(conn, run_id: int, exchange: str, raw_sources: list[dict]) -> dict:
    events_created = 0
    events_updated = 0
    source_types = []

    for raw_source in raw_sources:
        source = {**raw_source, "exchange": (raw_source.get("exchange") or exchange).lower()}
        source_types.append(source.get("source_type") or "unknown")
        raw_source_id = db.upsert_raw_source(conn, source)
        parsed_events = parse_events({**source, "id": raw_source_id})

        for event in parsed_events:
            asset_id = db.upsert_normalized_asset(
                conn,
                {
                    "token_symbol": event["token_symbol"],
                    "project_name": event.get("project_name"),
                },
            )
            event_row = {
                **event,
                "normalized_asset_id": asset_id,
                "raw_source_id": raw_source_id,
            }
            if _event_exists(conn, event_row):
                events_updated += 1
            else:
                events_created += 1
            db.upsert_listing_event(conn, event_row)

    source_type = _result_source_type(source_types)
    result = {
        "exchange": exchange,
        "source_type": source_type,
        "status": "success",
        "sources_found": len(raw_sources),
        "events_created": events_created,
        "events_updated": events_updated,
    }
    db.record_exchange_result(conn, run_id, **result)
    return result


def _event_exists(conn, event: dict) -> bool:
    row = conn.execute(
        """
        SELECT id
        FROM listing_events
        WHERE exchange = ?
          AND normalized_asset_id = ?
          AND listing_type = ?
          AND event_family = ?
        """,
        (
            event["exchange"],
            event["normalized_asset_id"],
            event["listing_type"],
            event["event_family"],
        ),
    ).fetchone()
    return row is not None


def _result_source_type(source_types: list[str]) -> str:
    unique_source_types = sorted(set(source_types))
    if not unique_source_types:
        return "all"
    if len(unique_source_types) == 1:
        return unique_source_types[0]
    return "mixed"
