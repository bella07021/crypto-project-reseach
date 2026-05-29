import argparse
import json

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
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_sync(
        args.db,
        trigger_type="scheduled",
        mode=args.mode,
        months=args.months,
        exchanges=args.exchanges,
        fetcher=default_fetcher,
    )
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
