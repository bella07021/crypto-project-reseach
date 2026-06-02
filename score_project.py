from __future__ import annotations

import argparse
import csv
import json
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape

from project_scorer import (
    WEIGHTS,
    calculate_funding_score,
    calculate_chain_score,
    calculate_investor_score,
    calculate_social_percentile,
    calculate_team_score,
    calculate_total_score,
    parse_followers,
)
from live_project_fetcher import fetch_live_project_detail, normalize_rootdata_url


DEFAULT_BENCHMARK_CSV = Path("output/rootdata_projects_x_enriched_fullv2.csv")
DEFAULT_FUNDRAISING_CSV = Path("output/rootdata_fundraising/rootdata_fundraising_by_sector.csv")
DEFAULT_WORKBOOK = Path("output/crypto_project_scores.xlsx")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score a crypto project research target.")
    parser.add_argument("--x-handle", required=True, help="Project Twitter/X handle.")
    parser.add_argument("--rootdata-url", required=True, help="Project RootData URL.")
    parser.add_argument("--token-ticker", default="", help="Project token symbol, for example NEX.")
    parser.add_argument("--project-name", default="", help="Project name.")
    parser.add_argument("--team-raw-score", type=float, default=0.0)
    parser.add_argument("--team-background", default="unknown")
    parser.add_argument("--funding-amount-usd", type=float, default=0.0)
    parser.add_argument("--funding-date", help="Latest funding date in YYYY-MM-DD format.")
    parser.add_argument("--bucket", help="RootData benchmark bucket, for example infra/defi/nft/gamefi.")
    parser.add_argument("--tge-signal", action="append", default=[])
    parser.add_argument("--listing-signal", action="append", default=[])
    parser.add_argument("--evidence-note", action="append", default=[])
    parser.add_argument("--benchmark-csv", type=Path, default=DEFAULT_BENCHMARK_CSV)
    parser.add_argument("--fundraising-csv", type=Path, default=DEFAULT_FUNDRAISING_CSV)
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--today", help="Override today's date in YYYY-MM-DD format.")
    parser.add_argument("--no-live", action="store_true", help="Disable live RootData/X fetching.")
    return parser.parse_args()


def load_benchmarks(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def normalize_handle(handle: str) -> str:
    return handle.strip().lstrip("@").lower()


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def find_project_row(rows: Iterable[dict[str, str]], x_handle: str, rootdata_url: str) -> dict[str, str] | None:
    wanted_handle = normalize_handle(x_handle)
    wanted_url = normalize_rootdata_url(rootdata_url)
    for row in rows:
        if normalize_handle(row.get("x_handle", "")) == wanted_handle:
            return row
    for row in rows:
        if normalize_rootdata_url(row.get("project_url", "")) == wanted_url:
            return row
    return None


def normalized_name(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def related_project_name(left: str, right: str) -> bool:
    left_name = normalized_name(left)
    right_name = normalized_name(right)
    if not left_name or not right_name:
        return False
    return left_name == right_name or left_name in right_name or right_name in left_name


def split_investors(value: str) -> list[str]:
    investors: list[str] = []
    seen: set[str] = set()
    for item in value.replace("；", ";").replace("|", ";").split(";"):
        name = item.strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        investors.append(name)
    return investors


def find_fundraising_rows(
    rows: Iterable[dict[str, str]],
    *,
    token_ticker: str,
    project_name: str,
    rootdata_url: str,
) -> list[dict[str, str]]:
    wanted_token = token_ticker.strip().upper()
    wanted_name = project_name.strip()
    wanted_url = normalize_rootdata_url(rootdata_url)
    matches: list[dict[str, str]] = []
    for row in rows:
        row_token = row.get("token_symbol", "").strip().upper()
        row_names = [row.get("project_name", ""), row.get("project_name_en", "")]
        row_url = normalize_rootdata_url(row.get("project_url", ""))
        token_match = bool(wanted_token and row_token == wanted_token)
        name_match = any(related_project_name(wanted_name, row_name) for row_name in row_names)
        url_match = bool(wanted_url and row_url == wanted_url)
        if url_match or (token_match and (name_match or not project_name.strip())):
            matches.append(row)
    return matches


def fundraising_investors(rows: Iterable[dict[str, str]]) -> list[str]:
    investors: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for investor in split_investors(row.get("investors", "")):
            key = investor.lower()
            if key in seen:
                continue
            seen.add(key)
            investors.append(investor)
    return investors


def merge_investors(primary: Iterable[str], fallback: Iterable[str]) -> tuple[list[str], list[str]]:
    investors: list[str] = []
    added_from_fallback: list[str] = []
    seen: set[str] = set()
    for source_name, names in (("primary", primary), ("fallback", fallback)):
        for name in names:
            investor = name.strip()
            key = investor.lower()
            if not investor or key in seen:
                continue
            seen.add(key)
            investors.append(investor)
            if source_name == "fallback":
                added_from_fallback.append(investor)
    return investors, added_from_fallback


def build_assessment(args: argparse.Namespace) -> dict[str, object]:
    benchmarks = load_benchmarks(args.benchmark_csv)
    project_row = find_project_row(benchmarks, args.x_handle, args.rootdata_url) or {}
    fundraising_rows = load_benchmarks(getattr(args, "fundraising_csv", DEFAULT_FUNDRAISING_CSV))
    live_detail = (
        None
        if args.no_live
        else fetch_live_project_detail(
            args.rootdata_url,
            args.x_handle,
            rootdata_html=str(getattr(args, "rootdata_html", "") or ""),
        )
    )

    bucket = args.bucket or (live_detail.bucket if live_detail else "") or project_row.get("bucket", "")
    followers = (
        (live_detail.x_followers if live_detail else None)
        or parse_followers(project_row.get("x_followers"))
    )
    today = parse_date(args.today) or date.today()
    funding_amount = float(args.funding_amount_usd) or (
        float(live_detail.latest_funding_amount_usd or 0) if live_detail else 0.0
    )
    funding_date = parse_date(args.funding_date) or (live_detail.latest_funding_date if live_detail else None)
    team_raw_score = float(args.team_raw_score) or (float(live_detail.team_raw_score) if live_detail else 0.0)
    team_background = (
        args.team_background
        if args.team_background != "unknown"
        else ((live_detail.team_background if live_detail else "") or "unknown")
    )

    team_score = calculate_team_score(team_raw_score, team_background)
    funding_score = calculate_funding_score(funding_amount, funding_date, today=today)
    social_score = calculate_social_percentile(benchmarks, bucket, followers)
    project_name_for_matching = (
        str(getattr(args, "project_name", "") or "").strip()
        or (live_detail.project_name if live_detail and live_detail.project_name else project_row.get("project_name", ""))
    )
    token_ticker_for_matching = (
        str(getattr(args, "token_ticker", "") or "").strip().upper()
        or (live_detail.token_ticker if live_detail and live_detail.token_ticker else project_row.get("token_symbol", ""))
    )
    matched_fundraising_rows = find_fundraising_rows(
        fundraising_rows,
        token_ticker=token_ticker_for_matching,
        project_name=project_name_for_matching,
        rootdata_url=args.rootdata_url,
    )
    investors, fundraising_investors_added = merge_investors(
        live_detail.investors if live_detail else [],
        fundraising_investors(matched_fundraising_rows),
    )
    chains = live_detail.chains if live_detail else []
    investor_score = calculate_investor_score(investors)
    chain_score = calculate_chain_score(chains)
    pre_tge_exchange_score = 0.0
    total_score = calculate_total_score(
        team_score,
        funding_score,
        social_score,
        investor_score,
        chain_score,
        pre_tge_exchange_score,
    )
    evidence_notes = list(args.evidence_note)
    if live_detail:
        evidence_notes.extend(live_detail.evidence_notes)
    if fundraising_investors_added:
        evidence_notes.append(f"RootData fundraising investors: {', '.join(fundraising_investors_added)}")

    return {
        "assessed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "x_handle": (live_detail.x_handle if live_detail and live_detail.x_handle else args.x_handle.strip().lstrip("@")),
        "rootdata_url": args.rootdata_url,
        "project_name": (
            project_name_for_matching
        ),
        "token_ticker": (
            token_ticker_for_matching
        ),
        "bucket": bucket,
        "x_followers": followers or 0,
        "team_raw_score": round(team_raw_score, 2),
        "team_background": team_background,
        "team_score": team_score,
        "funding_amount_usd": round(funding_amount, 2),
        "funding_total_usd": (
            round(float(live_detail.funding_total_usd), 2)
            if live_detail and live_detail.funding_total_usd is not None
            else round(funding_amount, 2)
        ),
        "funding_date": funding_date.isoformat() if funding_date else "",
        "funding_rounds": live_detail.funding_rounds if live_detail else [],
        "funding_score": funding_score,
        "investors": investors,
        "investor_score": investor_score,
        "social_score": social_score,
        "chains": chains,
        "chain_score": chain_score,
        "pre_tge_exchange_score": pre_tge_exchange_score,
        "total_score": total_score,
        "tge_signals": args.tge_signal,
        "listing_signals": args.listing_signal,
        "tge_status": live_detail.tge_status if live_detail else ("未 TGE" if not args.tge_signal else "手动信号"),
        "tge_probability": live_detail.tge_probability if live_detail else 0,
        "tge_date": live_detail.tge_date.isoformat() if live_detail and live_detail.tge_date else "",
        "tge_method": live_detail.tge_method if live_detail else "",
        "tge_evidence": live_detail.tge_evidence if live_detail else [],
        "tge_evidence_links": live_detail.tge_evidence_links if live_detail else [],
        "roadmap_events": live_detail.roadmap_events if live_detail else [],
        "evidence_notes": evidence_notes,
        "fetch_status": live_detail.fetch_status if live_detail else "disabled",
        "website": live_detail.website if live_detail else "",
        "location": live_detail.location if live_detail else "",
        "team_member_count": live_detail.team_member_count if live_detail else 0,
        "team_members": live_detail.team_members if live_detail else [],
        "team_foreign_count": live_detail.team_foreign_count if live_detail else 0,
        "team_chinese_count": live_detail.team_chinese_count if live_detail else 0,
        "team_unknown_count": live_detail.team_unknown_count if live_detail else 0,
        "team_known_location_count": live_detail.team_known_location_count if live_detail else 0,
        "team_region_summary": live_detail.team_region_summary if live_detail else "",
        "benchmark_csv": str(args.benchmark_csv),
        "workbook": str(args.workbook),
    }


def history_path_for(workbook_path: Path) -> Path:
    return workbook_path.with_suffix(".jsonl")


def append_history(history_path: Path, assessment: dict[str, object]) -> list[dict[str, object]]:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    if history_path.exists():
        with history_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
    rows.append(assessment)
    with history_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return rows


def column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def cell_xml(row_idx: int, col_idx: int, value: object) -> str:
    ref = f"{column_name(col_idx)}{row_idx}"
    if value is None:
        value = ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    text = escape(str(value))
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def sheet_xml(rows: list[list[object]]) -> str:
    body = []
    for row_idx, row in enumerate(rows, start=1):
        cells = "".join(cell_xml(row_idx, col_idx, value) for col_idx, value in enumerate(row, start=1))
        body.append(f'<row r="{row_idx}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        + "".join(body)
        + "</sheetData></worksheet>"
    )


def make_score_rows(history: list[dict[str, object]]) -> list[list[object]]:
    headers = [
        "assessed_at",
        "project_name",
        "token_ticker",
        "x_handle",
        "rootdata_url",
        "bucket",
        "x_followers",
        "team_score",
        "funding_score",
        "investor_score",
        "social_score",
        "chain_score",
        "pre_tge_exchange_score",
        "total_score",
        "team_background",
        "funding_amount_usd",
        "funding_total_usd",
        "funding_date",
        "investors",
        "chains",
        "fetch_status",
        "website",
        "location",
        "team_member_count",
    ]
    latest_by_project: dict[str, dict[str, object]] = {}
    for row in history:
        key = normalize_rootdata_url(str(row.get("rootdata_url", ""))) or normalize_handle(str(row.get("x_handle", "")))
        latest_by_project[key] = row
    latest_rows = sorted(latest_by_project.values(), key=lambda row: str(row.get("assessed_at", "")), reverse=True)
    return [headers] + [
        [
            " | ".join(row.get(header, [])) if header in {"investors", "chains"} and isinstance(row.get(header), list) else row.get(header, "")
            for header in headers
        ]
        for row in latest_rows
    ]


def make_evidence_rows(history: list[dict[str, object]]) -> list[list[object]]:
    rows = [["assessed_at", "x_handle", "evidence_notes"]]
    for row in history:
        rows.append([row.get("assessed_at", ""), row.get("x_handle", ""), " | ".join(row.get("evidence_notes", []))])
    return rows


def make_signal_rows(history: list[dict[str, object]]) -> list[list[object]]:
    rows = [["assessed_at", "x_handle", "tge_signals", "listing_signals"]]
    for row in history:
        rows.append(
            [
                row.get("assessed_at", ""),
                row.get("x_handle", ""),
                " | ".join(row.get("tge_signals", [])),
                " | ".join(row.get("listing_signals", [])),
            ]
        )
    return rows


def make_funding_round_rows(history: list[dict[str, object]]) -> list[list[object]]:
    rows = [["assessed_at", "project_name", "x_handle", "round", "amount_usd", "date", "description"]]
    for row in history:
        for funding_round in row.get("funding_rounds", []) or []:
            rows.append(
                [
                    row.get("assessed_at", ""),
                    row.get("project_name", ""),
                    row.get("x_handle", ""),
                    funding_round.get("round", ""),
                    funding_round.get("amount_usd", ""),
                    funding_round.get("date", ""),
                    funding_round.get("description", ""),
                ]
            )
    return rows


def make_roadmap_event_rows(history: list[dict[str, object]]) -> list[list[object]]:
    rows = [["assessed_at", "project_name", "x_handle", "type", "name", "date", "days_after_tge", "url"]]
    for row in history:
        for event in row.get("roadmap_events", []) or []:
            rows.append(
                [
                    row.get("assessed_at", ""),
                    row.get("project_name", ""),
                    row.get("x_handle", ""),
                    event.get("type", ""),
                    event.get("name", ""),
                    event.get("date", ""),
                    event.get("days_after_tge", ""),
                    event.get("url", ""),
                ]
            )
    return rows


def make_benchmark_rows(benchmarks: list[dict[str, str]], limit: int = 1000) -> list[list[object]]:
    headers = ["bucket", "project_name", "project_url", "x_handle", "x_followers"]
    return [headers] + [[row.get(header, "") for header in headers] for row in benchmarks[:limit]]


def make_config_rows() -> list[list[object]]:
    return [
        ["key", "value"],
        ["team_weight", WEIGHTS["team"]],
        ["funding_weight", WEIGHTS["funding"]],
        ["investor_weight", WEIGHTS["investor"]],
        ["social_weight", WEIGHTS["social"]],
        ["chain_weight", WEIGHTS["chain"]],
        ["pre_tge_exchange_weight", WEIGHTS["pre_tge_exchange"]],
        ["pure_chinese_team_multiplier", 0.3],
        ["funding_full_amount_usd", 500_000_000],
        ["funding_full_recency_days", 365],
        ["binance_alpha_affects_pre_tge_exchange_score", "false"],
        ["yzi_labs_affects_pre_tge_exchange_score", "false"],
    ]


def write_xlsx(path: Path, sheets: list[tuple[str, list[list[object]]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook_sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{idx}" r:id="rId{idx}"/>'
        for idx, (name, _) in enumerate(sheets, start=1)
    )
    workbook_rels = "".join(
        f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>'
        for idx in range(1, len(sheets) + 1)
    )
    content_types = "".join(
        f'<Override PartName="/xl/worksheets/sheet{idx}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for idx in range(1, len(sheets) + 1)
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + content_types
            + "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{workbook_sheets}</sheets></workbook>",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + workbook_rels
            + "</Relationships>",
        )
        for idx, (_, rows) in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{idx}.xml", sheet_xml(rows))


def write_workbook(path: Path, history: list[dict[str, object]], benchmarks: list[dict[str, str]]) -> None:
    sheets = [
        ("Scores", make_score_rows(history)),
        ("Evidence", make_evidence_rows(history)),
        ("Signals", make_signal_rows(history)),
        ("Funding Rounds", make_funding_round_rows(history)),
        ("Roadmap Events", make_roadmap_event_rows(history)),
        ("Social Benchmarks", make_benchmark_rows(benchmarks)),
        ("Config", make_config_rows()),
    ]
    write_xlsx(path, sheets)


def main() -> None:
    args = parse_args()
    assessment = build_assessment(args)
    history = append_history(history_path_for(args.workbook), assessment)
    benchmarks = load_benchmarks(args.benchmark_csv)
    write_workbook(args.workbook, history, benchmarks)
    print(json.dumps(assessment, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
