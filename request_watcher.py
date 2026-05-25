from __future__ import annotations

import argparse
import time
from typing import Any

from web_app import now_iso, read_github_requests_with_sha, score_payload, write_github_requests


def next_pending_index(rows: list[dict[str, Any]]) -> int | None:
    for index, row in enumerate(rows):
        if row.get("status") == "pending":
            return index
    return None


def process_next_request() -> bool:
    rows, sha = read_github_requests_with_sha()
    index = next_pending_index(rows)
    if index is None:
        return False

    request = dict(rows[index])
    request["status"] = "processing"
    request["updated_at"] = now_iso()
    rows[index] = request
    write_github_requests(rows, f"Process project request {request.get('request_id')}", sha)

    try:
        result = score_payload(
            {
                "x_handle": request.get("x_handle", ""),
                "rootdata_url": request.get("rootdata_url", ""),
            }
        )
        assessment = result.get("assessment", {})
        request["status"] = "done"
        request["token_ticker"] = assessment.get("token_ticker") or assessment.get("project_name") or assessment.get("x_handle")
        request["total_score"] = assessment.get("total_score", 0)
        request["completed_at"] = now_iso()
        request.pop("error", None)
    except Exception as exc:
        request["status"] = "failed"
        request["error"] = str(exc)
        request["completed_at"] = now_iso()

    request["updated_at"] = now_iso()
    latest_rows, latest_sha = read_github_requests_with_sha()
    if latest_rows and index < len(latest_rows):
        latest_rows[index] = request
        rows = latest_rows
        sha = latest_sha
    else:
        rows[index] = request
    write_github_requests(rows, f"Complete project request {request.get('request_id')}", sha)
    return True


def watch(interval_seconds: int) -> None:
    while True:
        processed = process_next_request()
        if not processed:
            time.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll GitHub project requests and score them locally.")
    parser.add_argument("--interval", type=int, default=10, help="Polling interval in seconds when no request is pending.")
    parser.add_argument("--once", action="store_true", help="Process at most one pending request and exit.")
    args = parser.parse_args()
    if args.once:
        process_next_request()
        return
    watch(max(1, args.interval))


if __name__ == "__main__":
    main()
