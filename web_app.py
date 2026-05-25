from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import ssl
import subprocess
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from score_project import (
    DEFAULT_BENCHMARK_CSV,
    DEFAULT_WORKBOOK,
    append_history,
    build_assessment,
    history_path_for,
    load_benchmarks,
    write_workbook,
)
from live_project_fetcher import normalize_rootdata_url


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"


def runtime_workbook_path() -> Path:
    override = os.environ.get("CRYPTO_SCORE_WORKBOOK", "").strip()
    if override:
        return Path(override)
    if os.environ.get("VERCEL"):
        return Path("/tmp/crypto_project_scores.xlsx")
    return DEFAULT_WORKBOOK


@dataclass
class ScorePayload:
    x_handle: str
    rootdata_url: str
    team_raw_score: float = 0.0
    team_background: str = "unknown"
    funding_amount_usd: float = 0.0
    funding_date: str | None = None
    bucket: str | None = None
    tge_signal: list[str] = field(default_factory=list)
    listing_signal: list[str] = field(default_factory=list)
    evidence_note: list[str] = field(default_factory=list)
    benchmark_csv: Path = DEFAULT_BENCHMARK_CSV
    workbook: Path = field(default_factory=runtime_workbook_path)
    today: str | None = None
    no_live: bool = False


def as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    return float(value)


def parse_score_payload(data: dict[str, Any]) -> ScorePayload:
    x_handle = str(data.get("x_handle", "")).strip().lstrip("@")
    rootdata_url = str(data.get("rootdata_url", "")).strip()
    if not x_handle:
        raise ValueError("x_handle is required")
    if not rootdata_url:
        raise ValueError("rootdata_url is required")

    return ScorePayload(
        x_handle=x_handle,
        rootdata_url=rootdata_url,
        team_raw_score=as_float(data.get("team_raw_score")),
        team_background=str(data.get("team_background") or "unknown").strip(),
        funding_amount_usd=as_float(data.get("funding_amount_usd")),
        funding_date=str(data.get("funding_date") or "").strip() or None,
        bucket=str(data.get("bucket") or "").strip() or None,
        tge_signal=as_list(data.get("tge_signals") or data.get("tge_signal")),
        listing_signal=as_list(data.get("listing_signals") or data.get("listing_signal")),
        evidence_note=as_list(data.get("evidence_notes") or data.get("evidence_note")),
        benchmark_csv=Path(str(data.get("benchmark_csv") or DEFAULT_BENCHMARK_CSV)),
        workbook=Path(str(data.get("workbook") or runtime_workbook_path())),
        today=str(data.get("today") or "").strip() or None,
        no_live=bool(data.get("no_live", False)),
    )


def namespace_from_payload(payload: ScorePayload) -> argparse.Namespace:
    return argparse.Namespace(
        x_handle=payload.x_handle,
        rootdata_url=payload.rootdata_url,
        team_raw_score=payload.team_raw_score,
        team_background=payload.team_background,
        funding_amount_usd=payload.funding_amount_usd,
        funding_date=payload.funding_date,
        bucket=payload.bucket,
        tge_signal=payload.tge_signal,
        listing_signal=payload.listing_signal,
        evidence_note=payload.evidence_note,
        benchmark_csv=payload.benchmark_csv,
        workbook=payload.workbook,
        today=payload.today,
        no_live=payload.no_live,
    )


def score_payload(data: dict[str, Any]) -> dict[str, Any]:
    payload = parse_score_payload(data)
    args = namespace_from_payload(payload)
    assessment = build_assessment(args)
    history = append_history(history_path_for(payload.workbook), assessment)
    benchmarks = load_benchmarks(payload.benchmark_csv)
    write_workbook(payload.workbook, history, benchmarks)
    return {
        "ok": True,
        "assessment": assessment,
        "workbook": str(payload.workbook),
        "history_count": len(history),
    }


def read_history_rows(workbook: Path | None = None) -> list[dict[str, Any]]:
    path = history_path_for(workbook or runtime_workbook_path())
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


EXCHANGE_SCORE_RULES = [
    ("BN 现货", 9.5, ("binance spot", "binance listed", "bn 现货", "币安现货")),
    ("Coinbase", 8.0, ("coinbase",)),
    ("Upbit 韩元现货", 8.0, ("upbit",)),
    ("Bithumb 韩元现货", 6.0, ("bithumb",)),
    ("BN 合约", 5.0, ("binance futures", "binance perpetual", "bn 合约", "币安合约")),
    ("OKX", 4.5, ("okx",)),
    ("Bybit", 4.5, ("bybit",)),
    ("Gate", 4.5, ("gate",)),
]

MAINSTREAM_SPOT_EXCHANGES = {
    "okx": "OKX",
    "bybit": "Bybit",
    "gate": "Gate",
    "bitget": "Bitget",
    "kucoin": "KuCoin",
    "mexc": "MEXC",
    "kraken": "Kraken",
}

EXCHANGE_SCORE_SCALE = 100 / 30
CMC_MARKET_CACHE: dict[str, list[dict[str, Any]]] = {}


def scaled_exchange_score(raw_score: float) -> float:
    return round(min(raw_score * EXCHANGE_SCORE_SCALE, 100.0), 2)


def exchange_score_group(label: str) -> str:
    if label in set(MAINSTREAM_SPOT_EXCHANGES.values()) | {"OKX", "Bybit", "Gate"}:
        return "主流现货"
    return label


def slugify_project_name(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return slug.strip("-")


def fetch_cmc_web_market_pairs(project_name: str, token_ticker: str) -> list[dict[str, Any]]:
    if os.environ.get("VERCEL"):
        return []

    slug = slugify_project_name(project_name)
    symbol = token_ticker.upper().strip()
    if not slug:
        return []

    cache_key = f"web:{slug}:{symbol}"
    if cache_key in CMC_MARKET_CACHE:
        return CMC_MARKET_CACHE[cache_key]

    script = ROOT / "cmc_market_scrape.js"
    pairs: list[dict[str, Any]] = []
    for candidate_slug in [slug, f"{slug}-labs"]:
        try:
            result = subprocess.run(
                ["node", str(script), candidate_slug, symbol],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=25,
                check=False,
            )
            payload = json.loads(result.stdout or "{}")
        except Exception:
            continue

        candidate_pairs = payload.get("pairs") if payload.get("ok") else []
        if symbol:
            candidate_pairs = [
                pair
                for pair in candidate_pairs or []
                if str(pair.get("market_pair", "")).upper().startswith(f"{symbol}/")
            ]
        if candidate_pairs:
            pairs = candidate_pairs
            break

    CMC_MARKET_CACHE[cache_key] = pairs
    return CMC_MARKET_CACHE[cache_key]


def fetch_cmc_market_pairs(project_name: str, token_ticker: str) -> list[dict[str, Any]]:
    api_key = os.environ.get("CMC_PRO_API_KEY", "").strip()
    if not api_key:
        return []

    cache_key = f"{project_name.lower()}:{token_ticker.upper()}"
    if cache_key in CMC_MARKET_CACHE:
        return CMC_MARKET_CACHE[cache_key]

    candidates = [
        ("slug", slugify_project_name(project_name)),
        ("symbol", token_ticker.upper()),
    ]
    for key, value in candidates:
        if not value:
            continue
        params = urlencode({key: value, "start": 1, "limit": 100, "category": "all"})
        request = Request(
            f"https://pro-api.coinmarketcap.com/v2/cryptocurrency/market-pairs/latest?{params}",
            headers={"X-CMC_PRO_API_KEY": api_key, "Accept": "application/json"},
        )
        try:
            context = ssl._create_unverified_context()
            with urlopen(request, timeout=8, context=context) as response:
                payload = json.loads(response.read().decode("utf-8", errors="ignore"))
        except Exception:
            continue
        data = payload.get("data") or {}
        pairs = data.get("market_pairs") or []
        if pairs:
            CMC_MARKET_CACHE[cache_key] = pairs
            return pairs

    CMC_MARKET_CACHE[cache_key] = []
    return []


def classify_cmc_market_pair(pair: dict[str, Any]) -> tuple[str, float] | None:
    exchange = pair.get("exchange") or {}
    exchange_name = str(exchange.get("name", "")).lower()
    exchange_slug = str(exchange.get("slug", "")).lower()
    market_pair = str(pair.get("market_pair", "")).upper()
    category = str(pair.get("category", "")).lower()
    haystack = " ".join([exchange_name, exchange_slug, market_pair, category])

    if "binance alpha" in haystack:
        return None
    if "binance" in haystack and category == "spot":
        return "BN 现货", 9.5
    if "binance" in haystack and category in {"derivatives", "futures"}:
        return "BN 合约", 5.0
    if "coinbase" in haystack and category == "spot":
        return "Coinbase", 8.0
    if "upbit" in haystack and "KRW" in market_pair and category == "spot":
        return "Upbit 韩元现货", 8.0
    if "bithumb" in haystack and "KRW" in market_pair and category == "spot":
        return "Bithumb 韩元现货", 6.0
    for keyword, label in MAINSTREAM_SPOT_EXCHANGES.items():
        if keyword in haystack and category == "spot":
            return label, 4.5
    return None


def exchange_progress_from_cmc(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    matched_scores: dict[str, float] = {}
    matched_labels: dict[str, float] = {}
    for pair in pairs:
        classified = classify_cmc_market_pair(pair)
        if not classified:
            continue
        label, raw_score = classified
        group = exchange_score_group(label)
        matched_scores[group] = max(raw_score, matched_scores.get(group, 0))
        matched_labels[label] = max(raw_score, matched_labels.get(label, 0))

    exchanges = sorted(matched_labels, key=lambda label: matched_labels[label], reverse=True)
    raw_score = sum(matched_scores.values())
    score = scaled_exchange_score(raw_score)
    return {
        "exchange_score": score,
        "exchange_progress": score,
        "exchange_raw_score": round(raw_score, 2),
        "exchange_source": pairs[0].get("source", "CoinMarketCap") if pairs else "CoinMarketCap",
        "listed_exchanges": exchanges,
    }


def exchange_progress(roadmap_events: list[dict[str, Any]]) -> dict[str, Any]:
    matched_scores: dict[str, float] = {}
    matched_labels: dict[str, float] = {}
    for event in roadmap_events:
        haystack = " ".join(
            [
                str(event.get("type", "")),
                str(event.get("name", "")),
                str(event.get("url", "")),
            ]
        ).lower()
        for label, score, keywords in EXCHANGE_SCORE_RULES:
            if any(keyword in haystack for keyword in keywords):
                group = exchange_score_group(label)
                matched_scores[group] = max(score, matched_scores.get(group, 0))
                matched_labels[label] = max(score, matched_labels.get(label, 0))

    exchanges = sorted(matched_labels, key=lambda label: matched_labels[label], reverse=True)
    raw_score = sum(matched_scores.values())
    score = scaled_exchange_score(raw_score)
    return {
        "exchange_score": score,
        "exchange_progress": score,
        "exchange_raw_score": round(raw_score, 2),
        "exchange_source": "RootData",
        "listed_exchanges": exchanges,
    }


def dashboard_rows(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in history:
        key = normalize_rootdata_url(str(row.get("rootdata_url", ""))) or str(row.get("x_handle", "")).lower()
        latest[key] = row
    rows = []
    for row in latest.values():
        cmc_pairs = fetch_cmc_web_market_pairs(
            str(row.get("project_name", "")),
            str(row.get("token_ticker") or row.get("project_name") or ""),
        )
        if not cmc_pairs:
            cmc_pairs = fetch_cmc_market_pairs(
                str(row.get("project_name", "")),
                str(row.get("token_ticker") or row.get("project_name") or ""),
            )
        progress = exchange_progress_from_cmc(cmc_pairs) if cmc_pairs else exchange_progress(row.get("roadmap_events", []))
        rows.append(
            {
                "token_ticker": row.get("token_ticker") or row.get("project_name") or row.get("x_handle") or "--",
                "project_name": row.get("project_name", ""),
                "x_handle": row.get("x_handle", ""),
                "rootdata_url": row.get("rootdata_url", ""),
                "total_score": row.get("total_score", 0),
                "team_score": row.get("team_score", 0),
                "funding_score": row.get("funding_score", 0),
                "social_score": row.get("social_score", 0),
                "tge_status": row.get("tge_status", ""),
                "tge_probability": row.get("tge_probability", 0),
                "tge_date": row.get("tge_date", ""),
                "tge_method": row.get("tge_method", ""),
                "roadmap_events": row.get("roadmap_events", []),
                **progress,
                "assessment": row,
            }
        )
    return sorted(rows, key=lambda item: float(item.get("total_score") or 0), reverse=True)


class CryptoScoringHandler(BaseHTTPRequestHandler):
    server_version = "CryptoScoringWeb/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self.send_json({"ok": True})
            return
        if parsed.path == "/api/history":
            self.send_json(self.read_history())
            return
        if parsed.path == "/api/dashboard":
            self.send_json({"ok": True, "rows": dashboard_rows(read_history_rows())})
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/score":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            data = json.loads(raw or "{}")
            result = score_payload(data)
            self.send_json(result)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)

    def read_history(self) -> dict[str, Any]:
        rows = read_history_rows()
        return {"ok": True, "rows": rows[-50:][::-1]}

    def serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            path = "/index.html"
        requested = (WEB_DIR / unquote(path).lstrip("/")).resolve()
        if WEB_DIR not in requested.parents and requested != WEB_DIR:
            self.send_error(403)
            return
        if not requested.exists() or not requested.is_file():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(str(requested))[0] or "application/octet-stream"
        body = requested.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the crypto project scoring web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8094)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), CryptoScoringHandler)
    print(f"Crypto scoring web app running at http://{args.host}:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
