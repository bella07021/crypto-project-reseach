import argparse
import json

from exchange_listings.adapters import fetch_live_sources
from exchange_listings.sync import default_fetcher, run_sync


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync exchange listing signals into SQLite.")
    parser.add_argument(
        "--mode",
        choices=("incremental", "backfill"),
        default="incremental",
        help="Sync mode to run.",
    )
    parser.add_argument(
        "--months",
        type=int,
        default=3,
        help="Number of months to backfill.",
    )
    parser.add_argument(
        "--db",
        default="data/exchange_listings.sqlite",
        help="SQLite database path.",
    )
    parser.add_argument(
        "--exchange",
        action="append",
        dest="exchanges",
        help="Exchange to sync. Can be provided more than once.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Fetch live announcement sources instead of using the default empty fetcher.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum live source candidates to fetch per exchange.",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    trigger_type = "backfill" if args.mode == "backfill" else "scheduled"
    fetcher = _live_fetcher(args.limit) if args.live else default_fetcher
    try:
        summary = run_sync(
            args.db,
            trigger_type=trigger_type,
            mode=args.mode,
            months=args.months,
            exchanges=args.exchanges,
            fetcher=fetcher,
        )
    except ValueError as exc:
        summary = {
            "ok": False,
            "status": "failed",
            "error": str(exc),
        }
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary.get("ok") or summary.get("status") == "skipped" else 1


def _live_fetcher(limit: int):
    def fetch(exchange, *, mode, months):
        return fetch_live_sources(exchange, mode=mode, months=months, limit=limit)

    return fetch


if __name__ == "__main__":
    raise SystemExit(main())
