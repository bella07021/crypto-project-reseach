from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import re
import ssl
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
from exchange_listings.adapters import fetch_live_sources
from exchange_listings.sync import run_sync
from live_project_fetcher import clean_html_text, fetch_text, normalize_rootdata_url, parse_human_date


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
GITHUB_HISTORY_PATH = "data/project_scores.jsonl"
GITHUB_REQUESTS_PATH = "data/project_requests.jsonl"
ACTIVE_REQUEST_STATUSES = {"pending", "processing"}
ICODROPS_CACHE: dict[str, str] = {}
KNOWN_ICODROPS_AIRDROP_DATES = {
    "solstice": "2026-05-25",
}


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
    token_ticker: str = ""
    project_name: str = ""
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
    rootdata_html: str = ""


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


def normalize_x_handle(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("@"):
        return raw.lstrip("@").strip()
    candidate = raw if re.match(r"^https?://", raw, re.I) else f"https://{raw}"
    try:
        parsed = urlparse(candidate)
        host = parsed.hostname or ""
        if host.lower().removeprefix("www.") in {"x.com", "twitter.com", "mobile.twitter.com"}:
            parts = [part for part in parsed.path.split("/") if part]
            if parts and parts[0].lower() not in {"i", "intent", "search", "share", "home", "explore"}:
                return parts[0].lstrip("@")
    except Exception:
        pass
    return raw.lstrip("@")


def parse_score_payload(data: dict[str, Any]) -> ScorePayload:
    x_handle = normalize_x_handle(data.get("x_handle", ""))
    rootdata_url = str(data.get("rootdata_url", "")).strip()
    if not x_handle:
        raise ValueError("x_handle is required")
    if not rootdata_url:
        raise ValueError("rootdata_url is required")

    return ScorePayload(
        x_handle=x_handle,
        rootdata_url=rootdata_url,
        token_ticker=str(data.get("token_ticker") or data.get("token_symbol") or "").strip().upper(),
        project_name=str(data.get("project_name") or "").strip(),
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
        rootdata_html=str(data.get("rootdata_html") or ""),
    )


def namespace_from_payload(payload: ScorePayload) -> argparse.Namespace:
    return argparse.Namespace(
        x_handle=payload.x_handle,
        rootdata_url=payload.rootdata_url,
        token_ticker=payload.token_ticker,
        project_name=payload.project_name,
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
        rootdata_html=payload.rootdata_html,
    )


def score_payload(data: dict[str, Any]) -> dict[str, Any]:
    payload = parse_score_payload(data)
    args = namespace_from_payload(payload)
    assessment = build_assessment(args)
    assessment.update(exchange_progress(assessment.get("roadmap_events", [])) if payload.no_live else project_exchange_progress(assessment))
    if not payload.no_live:
        apply_icodrops_tge_signal_from_web(assessment)
    prune_foreign_project_tge_links(assessment)
    apply_tge_exchange_gate(assessment)
    if github_storage_config():
        history = append_github_history(assessment)
        workbook = github_storage_label()
    else:
        history = append_history(history_path_for(payload.workbook), assessment)
        benchmarks = load_benchmarks(payload.benchmark_csv)
        write_workbook(payload.workbook, history, benchmarks)
        workbook = str(payload.workbook)
    return {
        "ok": True,
        "assessment": assessment,
        "workbook": workbook,
        "history_count": len(history),
    }


def read_history_rows(workbook: Path | None = None) -> list[dict[str, Any]]:
    if workbook is None and github_storage_config():
        return [hydrate_cached_assessment(row) for row in read_github_history()]
    path = history_path_for(workbook or runtime_workbook_path())
    if not path.exists():
        return []
    return [hydrate_cached_assessment(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def github_storage_config(path: str | None = None) -> dict[str, str] | None:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    owner = (
        os.environ.get("GITHUB_REPO_OWNER", "").strip()
        or os.environ.get("VERCEL_GIT_REPO_OWNER", "").strip()
        or "bella07021"
    )
    repo = (
        os.environ.get("GITHUB_REPO_NAME", "").strip()
        or os.environ.get("VERCEL_GIT_REPO_SLUG", "").strip()
        or "crypto-project-reseach"
    )
    branch = os.environ.get("GITHUB_BRANCH", "").strip() or "main"
    storage_path = path or os.environ.get("GITHUB_HISTORY_PATH", "").strip() or GITHUB_HISTORY_PATH
    if not token or not owner or not repo:
        return None
    return {"token": token, "owner": owner, "repo": repo, "branch": branch, "path": storage_path}


def github_storage_label() -> str:
    config = github_storage_config()
    if not config:
        return str(runtime_workbook_path())
    return f"github://{config['owner']}/{config['repo']}/{config['path']}"


def github_contents_request(config: dict[str, str], method: str, payload: dict[str, Any] | None = None) -> Any:
    url = f"https://api.github.com/repos/{config['owner']}/{config['repo']}/contents/{config['path']}"
    if method == "GET":
        url = f"{url}?ref={config['branch']}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {config['token']}",
            "Content-Type": "application/json",
            "User-Agent": "crypto-project-scoring",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    context = ssl._create_unverified_context()
    with urlopen(request, timeout=12, context=context) as response:
        return json.loads(response.read().decode("utf-8", errors="ignore") or "{}")


def read_github_history_with_sha() -> tuple[list[dict[str, Any]], str | None]:
    config = github_storage_config()
    if not config:
        return [], None
    try:
        payload = github_contents_request(config, "GET")
    except Exception:
        return [], None
    encoded = str(payload.get("content") or "")
    text = base64.b64decode(encoded).decode("utf-8", errors="ignore") if encoded else ""
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    return rows, payload.get("sha")


def read_github_history() -> list[dict[str, Any]]:
    rows, _ = read_github_history_with_sha()
    return rows


def append_github_history(assessment: dict[str, Any]) -> list[dict[str, Any]]:
    config = github_storage_config()
    if not config:
        return [assessment]
    rows, sha = read_github_history_with_sha()
    rows.append(assessment)
    content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    payload: dict[str, Any] = {
        "message": f"Add score for {assessment.get('token_ticker') or assessment.get('project_name') or assessment.get('x_handle')}",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": config["branch"],
    }
    if sha:
        payload["sha"] = sha
    github_contents_request(config, "PUT", payload)
    return rows


def request_storage_path() -> str:
    return os.environ.get("GITHUB_REQUESTS_PATH", "").strip() or GITHUB_REQUESTS_PATH


def read_github_jsonl_with_sha(path: str) -> tuple[list[dict[str, Any]], str | None]:
    config = github_storage_config(path)
    if not config:
        return [], None
    try:
        payload = github_contents_request(config, "GET")
    except Exception:
        return [], None
    encoded = str(payload.get("content") or "")
    text = base64.b64decode(encoded).decode("utf-8", errors="ignore") if encoded else ""
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    return rows, payload.get("sha")


def write_github_jsonl(path: str, rows: list[dict[str, Any]], message: str, sha: str | None = None) -> None:
    config = github_storage_config(path)
    if not config:
        raise RuntimeError("GitHub storage is not configured")
    content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    payload: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": config["branch"],
    }
    if sha:
        payload["sha"] = sha
    github_contents_request(config, "PUT", payload)


def read_github_requests_with_sha() -> tuple[list[dict[str, Any]], str | None]:
    return read_github_jsonl_with_sha(request_storage_path())


def read_github_requests() -> list[dict[str, Any]]:
    rows, _ = read_github_requests_with_sha()
    return rows


def write_github_requests(rows: list[dict[str, Any]], message: str, sha: str | None = None) -> None:
    write_github_jsonl(request_storage_path(), rows, message, sha)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def project_request_key(rootdata_url: str, x_handle: str = "") -> str:
    normalized_url = normalize_rootdata_url(rootdata_url)
    if normalized_url:
        return normalized_url.lower()
    return normalize_x_handle(x_handle).lower()


def project_request_id(request_key: str, timestamp: str = "") -> str:
    seed = f"{request_key}|{timestamp}" if timestamp else request_key
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def create_project_request(data: dict[str, Any]) -> dict[str, Any]:
    x_handle = normalize_x_handle(data.get("x_handle", ""))
    rootdata_url = str(data.get("rootdata_url", "")).strip()
    token_ticker = str(data.get("token_ticker") or data.get("token_symbol") or "").strip().upper()
    project_name = str(data.get("project_name") or "").strip()
    if not x_handle:
        raise ValueError("x_handle is required")
    if not rootdata_url:
        raise ValueError("rootdata_url is required")

    rows, sha = read_github_requests_with_sha()
    request_key = project_request_key(rootdata_url, x_handle)
    for row in reversed(rows):
        row_keys = {
            str(row.get("request_key") or "").lower(),
            project_request_key(str(row.get("rootdata_url", "")), str(row.get("x_handle", ""))),
        }
        if request_key in row_keys and str(row.get("status", "")) in ACTIVE_REQUEST_STATUSES:
            return {"ok": True, "created": False, "request": row}

    timestamp = now_iso()
    request = {
        "request_id": project_request_id(request_key, timestamp),
        "request_key": request_key,
        "status": "pending",
        "token_ticker": token_ticker,
        "project_name": project_name,
        "x_handle": x_handle,
        "rootdata_url": rootdata_url,
        "requested_at": timestamp,
        "updated_at": timestamp,
    }
    rows.append(request)
    label = token_ticker or project_name or x_handle
    write_github_requests(rows, f"Add project request for {label}", sha)
    return {"ok": True, "created": True, "request": request}


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


def project_exchange_progress(row: dict[str, Any]) -> dict[str, Any]:
    token_ticker = str(row.get("token_ticker") or "").strip()
    cmc_pairs = fetch_cmc_web_market_pairs(
        str(row.get("project_name", "")),
        token_ticker,
    )
    if not cmc_pairs:
        cmc_pairs = fetch_cmc_market_pairs(
            str(row.get("project_name", "")),
            token_ticker,
        )
    return exchange_progress_from_cmc(cmc_pairs) if cmc_pairs else exchange_progress(row.get("roadmap_events", []))


def rootdata_tge_date(row: dict[str, Any]) -> str:
    for event in row.get("roadmap_events", []) or []:
        if str(event.get("type", "")) == "TGE" and event.get("date"):
            return str(event.get("date", ""))
    return ""


def icodrops_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def icodrops_url_for_assessment(assessment: dict[str, Any]) -> str:
    slug = icodrops_slug(str(assessment.get("project_name") or assessment.get("token_ticker") or ""))
    return f"https://icodrops.com/{slug}/" if slug else ""


def fetch_icodrops_project_html(assessment: dict[str, Any]) -> tuple[str, str]:
    url = icodrops_url_for_assessment(assessment)
    if not url:
        return "", ""
    if url not in ICODROPS_CACHE:
        try:
            ICODROPS_CACHE[url] = fetch_text(url, retries=1, timeout=12)
        except Exception:
            ICODROPS_CACHE[url] = ""
    return ICODROPS_CACHE[url], url


def icodrops_airdrop_date(html: str) -> str:
    text = clean_html_text(html)
    match = re.search(r"Active\s+from\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", text)
    parsed = parse_human_date(match.group(1)) if match else None
    return parsed.isoformat() if parsed else ""


def known_icodrops_airdrop_date(assessment: dict[str, Any], url: str) -> str:
    candidates = [
        icodrops_slug(str(assessment.get("project_name", ""))),
        icodrops_slug(str(assessment.get("token_ticker", ""))),
        urlparse(url).path.strip("/").lower(),
    ]
    for candidate in candidates:
        if candidate in KNOWN_ICODROPS_AIRDROP_DATES:
            return KNOWN_ICODROPS_AIRDROP_DATES[candidate]
    return ""


def apply_icodrops_tge_signal(assessment: dict[str, Any], html: str, url: str) -> None:
    if not html or "Binance Alpha Airdrop" not in html:
        return
    if assessment.get("tge_status") == "已 TGE":
        return
    if assessment_has_listed_exchange(assessment):
        assessment["tge_status"] = "已 TGE"
        assessment["tge_probability"] = 100
    else:
        assessment["tge_status"] = "未 TGE"
        assessment["tge_probability"] = max(int(assessment.get("tge_probability") or 0), 95)
    assessment["tge_method"] = "Binance Alpha Airdrop"
    assessment["tge_date"] = assessment.get("tge_date") or icodrops_airdrop_date(html) or known_icodrops_airdrop_date(assessment, url)
    links = assessment.setdefault("tge_evidence_links", [])
    evidence_text = "Binance Alpha Airdrop"
    if assessment["tge_date"]:
        evidence_text = f"Binance Alpha Airdrop active from {datetime.fromisoformat(assessment['tge_date']).strftime('%B %-d, %Y')}"
    evidence = {"text": evidence_text, "url": url}
    if evidence not in links:
        links.append(evidence)
    notes = assessment.setdefault("evidence_notes", [])
    note = "ICO Drops detected Binance Alpha Airdrop"
    if note not in notes:
        notes.append(note)


def apply_icodrops_tge_signal_from_web(assessment: dict[str, Any]) -> None:
    html, url = fetch_icodrops_project_html(assessment)
    apply_icodrops_tge_signal(assessment, html, url)


def assessment_has_listed_exchange(assessment: dict[str, Any]) -> bool:
    return bool(assessment.get("listed_exchanges") or [])


def apply_tge_exchange_gate(assessment: dict[str, Any]) -> dict[str, Any]:
    if assessment.get("tge_status") == "已 TGE" and not assessment_has_listed_exchange(assessment):
        assessment["tge_status"] = "未 TGE"
        if assessment.get("tge_method") or assessment.get("tge_date") or assessment.get("tge_evidence_links"):
            assessment["tge_probability"] = 95
    return assessment


def is_foreign_project_x_status(url: str, x_handle: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host not in {"x.com", "twitter.com", "mobile.twitter.com"} or "/status/" not in parsed.path:
        return False
    if not x_handle:
        return False
    handle = parsed.path.strip("/").split("/")[0].lstrip("@").lower()
    return bool(handle and handle != x_handle.strip().lstrip("@").lower())


def prune_foreign_project_tge_links(assessment: dict[str, Any]) -> dict[str, Any]:
    x_handle = str(assessment.get("x_handle", ""))
    links = []
    for link in assessment.get("tge_evidence_links", []) or []:
        if not is_foreign_project_x_status(str(link.get("url", "")), x_handle):
            links.append(link)
    assessment["tge_evidence_links"] = links
    return assessment


def has_binance_alpha_airdrop_evidence(row: dict[str, Any]) -> bool:
    method = str(row.get("tge_method", ""))
    if "Binance Alpha Airdrop" in method:
        return True
    for link in row.get("tge_evidence_links", []) or []:
        text = str(link.get("text", ""))
        url = str(link.get("url", ""))
        if "Binance Alpha Airdrop" in text or "icodrops.com" in url:
            return True
    return False


def hydrate_cached_assessment(row: dict[str, Any]) -> dict[str, Any]:
    hydrated = dict(row)
    prune_foreign_project_tge_links(hydrated)
    if (
        not hydrated.get("tge_date")
        and hydrated.get("tge_status") == "已 TGE"
        and has_binance_alpha_airdrop_evidence(hydrated)
    ):
        url = icodrops_url_for_assessment(hydrated)
        hydrated["tge_date"] = known_icodrops_airdrop_date(hydrated, url)
        if hydrated["tge_date"]:
            links = hydrated.setdefault("tge_evidence_links", [])
            for link in links:
                if str(link.get("text", "")) == "Binance Alpha Airdrop":
                    link["text"] = f"Binance Alpha Airdrop active from {datetime.fromisoformat(hydrated['tge_date']).strftime('%B %-d, %Y')}"
    return apply_tge_exchange_gate(hydrated)


def cached_exchange_progress(row: dict[str, Any]) -> dict[str, Any] | None:
    if "exchange_score" not in row:
        return None
    return {
        "exchange_score": row.get("exchange_score", 0),
        "exchange_progress": row.get("exchange_progress", row.get("exchange_score", 0)),
        "exchange_raw_score": row.get("exchange_raw_score", 0),
        "exchange_source": row.get("exchange_source", "cached"),
        "listed_exchanges": row.get("listed_exchanges", []),
    }


def dashboard_rows(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in history:
        row = hydrate_cached_assessment(row)
        key = normalize_rootdata_url(str(row.get("rootdata_url", ""))) or str(row.get("x_handle", "")).lower()
        latest[key] = row
    rows = []
    for row in latest.values():
        progress = cached_exchange_progress(row) or exchange_progress(row.get("roadmap_events", []))
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
        rows[-1]["assessment"] = {**row, **progress}
        rows[-1]["tge_status"] = rows[-1]["assessment"].get("tge_status", rows[-1]["tge_status"])
        rows[-1]["tge_probability"] = rows[-1]["assessment"].get("tge_probability", rows[-1]["tge_probability"])
        rows[-1]["tge_date"] = rows[-1]["assessment"].get("tge_date", rows[-1]["tge_date"])
        rows[-1]["tge_method"] = rows[-1]["assessment"].get("tge_method", rows[-1]["tge_method"])
    return sorted(rows, key=lambda item: float(item.get("total_score") or 0), reverse=True)


def request_dashboard_rows(requests: list[dict[str, Any]], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored_keys = {
        normalize_rootdata_url(str(row.get("rootdata_url", ""))).lower() or str(row.get("x_handle", "")).lower()
        for row in history
    }
    rows = []
    for request in requests:
        status = str(request.get("status", ""))
        if status == "done":
            continue
        request_key = project_request_key(str(request.get("rootdata_url", "")), str(request.get("x_handle", "")))
        if request_key in scored_keys:
            continue
        x_handle = str(request.get("x_handle", ""))
        rows.append(
            {
                "token_ticker": request.get("token_ticker") or x_handle or "--",
                "project_name": request.get("project_name") or "",
                "x_handle": x_handle,
                "rootdata_url": request.get("rootdata_url", ""),
                "total_score": "",
                "team_score": "",
                "funding_score": "",
                "social_score": "",
                "tge_status": status,
                "tge_probability": 0,
                "tge_date": "",
                "tge_method": "",
                "roadmap_events": [],
                "exchange_score": 0,
                "exchange_progress": 0,
                "exchange_raw_score": 0,
                "exchange_source": "request_queue",
                "listed_exchanges": [],
                "request_id": request.get("request_id", ""),
                "request_status": status,
                "requested_at": request.get("requested_at", ""),
                "error": request.get("error", ""),
                "assessment": request,
            }
        )
    return sorted(rows, key=lambda item: str(item.get("requested_at", "")), reverse=True)


def combined_dashboard_rows(history: list[dict[str, Any]], requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    request_overrides: dict[str, dict[str, str]] = {}
    for request in requests:
        request_key = project_request_key(str(request.get("rootdata_url", "")), str(request.get("x_handle", "")))
        if not request_key:
            continue
        token_ticker = str(request.get("token_ticker") or "").strip()
        project_name = str(request.get("project_name") or "").strip()
        if token_ticker or project_name:
            request_overrides[request_key] = {
                "token_ticker": token_ticker,
                "project_name": project_name,
            }

    rows = dashboard_rows(history)
    for row in rows:
        request_key = project_request_key(str(row.get("rootdata_url", "")), str(row.get("x_handle", "")))
        override = request_overrides.get(request_key)
        if not override:
            continue
        if override.get("token_ticker"):
            row["token_ticker"] = override["token_ticker"]
            row["assessment"]["token_ticker"] = override["token_ticker"]
        if override.get("project_name"):
            row["project_name"] = override["project_name"]
            row["assessment"]["project_name"] = override["project_name"]
    return request_dashboard_rows(requests, history) + rows


def find_assessment_for_request(request: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any] | None:
    request_key = project_request_key(str(request.get("rootdata_url", "")), str(request.get("x_handle", "")))
    request_handle = str(request.get("x_handle", "")).strip().lstrip("@").lower()
    requested_at = parse_iso_datetime(str(request.get("requested_at", "")))
    for row in reversed(history):
        row = hydrate_cached_assessment(row)
        assessed_at = parse_iso_datetime(str(row.get("assessed_at", "")))
        if requested_at and assessed_at and assessed_at < requested_at:
            continue
        if requested_at and not assessed_at and str(request.get("status", "")) not in {"done", "failed"}:
            continue
        row_key = project_request_key(str(row.get("rootdata_url", "")), str(row.get("x_handle", "")))
        row_handle = str(row.get("x_handle", "")).strip().lstrip("@").lower()
        if request_key and request_key == row_key:
            return apply_request_identity_override(row, request)
        if request_handle and request_handle == row_handle:
            return apply_request_identity_override(row, request)
    return None


def apply_request_identity_override(assessment: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    token_ticker = str(request.get("token_ticker") or "").strip()
    project_name = str(request.get("project_name") or "").strip()
    if not token_ticker and not project_name:
        return assessment
    result = dict(assessment)
    if token_ticker:
        result["token_ticker"] = token_ticker
    if project_name:
        result["project_name"] = project_name
    return result


def request_status_payload(
    request_id: str,
    requests: list[dict[str, Any]] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = requests if requests is not None else read_github_requests()
    request = next((row for row in rows if str(row.get("request_id", "")) == request_id), None)
    if not request:
        return {"ok": False, "error": "request not found"}
    history_rows = history if history is not None else read_history_rows()
    assessment = find_assessment_for_request(request, history_rows)
    if assessment and request.get("status") != "failed":
        request = {**request, "status": "done"}
    return {"ok": True, "request": request, "assessment": assessment}


def run_exchange_listing_manual_sync(data: dict[str, Any]) -> dict[str, Any]:
    limit = int(data.get("limit") or 30)
    max_pages = int(data.get("max_pages") or 3)

    def fetcher(exchange, *, mode, months):
        return fetch_live_sources(exchange, mode=mode, months=months, limit=limit, max_pages=max_pages)

    return run_sync(
        ROOT / "data" / "exchange_listings.sqlite",
        trigger_type="manual",
        mode="incremental",
        months=3,
        exchanges=data.get("exchanges"),
        fetcher=fetcher,
    )


def handle_post_api(path: str, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    if path == "/api/request":
        return 200, create_project_request(data)
    if path == "/api/score":
        return 200, score_payload(data)
    if path == "/api/exchange-listings/sync":
        return 200, run_exchange_listing_manual_sync(data)
    return 404, {"ok": False, "error": "not found"}


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
        if parsed.path == "/api/requests":
            self.send_json({"ok": True, "rows": read_github_requests()[-50:][::-1]})
            return
        if parsed.path == "/api/request-status":
            request_id = str(__import__("urllib.parse").parse.parse_qs(parsed.query).get("id", [""])[0])
            self.send_json(request_status_payload(request_id), status=200 if request_id else 400)
            return
        if parsed.path == "/api/dashboard":
            history = read_history_rows()
            self.send_json({"ok": True, "rows": combined_dashboard_rows(history, read_github_requests())})
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in {"/api/score", "/api/request", "/api/exchange-listings/sync"}:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            data = json.loads(raw or "{}")
            status, result = handle_post_api(parsed.path, data)
            self.send_json(result, status=status)
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
        self.send_header("Cache-Control", "no-store")
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
