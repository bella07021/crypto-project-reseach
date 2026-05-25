from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import subprocess
import time
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen

from project_scorer import parse_followers


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

ROOTDATA_BUCKET_MAP = {
    "infra": "infra",
    "infrastructure": "infra",
    "基础设施": "infra",
    "zk": "infra",
    "layer1": "infra",
    "layer2": "infra",
    "defi": "defi",
    "nft": "nft",
    "gamefi": "gamefi",
    "gaming": "gamefi",
    "游戏": "gamefi",
}

KNOWN_TGE_METHOD_OVERRIDES = {
    "NEX": "Binance Alpha",
}


@dataclass
class LiveProjectDetail:
    project_name: str = ""
    token_ticker: str = ""
    description: str = ""
    website: str = ""
    x_handle: str = ""
    x_url: str = ""
    x_followers: Optional[int] = None
    bucket: str = ""
    tags: list[str] = field(default_factory=list)
    founded: str = ""
    location: str = ""
    team_member_count: int = 0
    named_team_member_count: int = 0
    team_raw_score: float = 0.0
    team_background: str = "unknown"
    latest_funding_amount_usd: Optional[int] = None
    latest_funding_date: Optional[date] = None
    funding_total_usd: Optional[int] = None
    funding_rounds: list[dict[str, object]] = field(default_factory=list)
    tge_status: str = "未 TGE"
    tge_probability: int = 0
    tge_date: Optional[date] = None
    tge_method: str = ""
    tge_evidence: list[str] = field(default_factory=list)
    roadmap_events: list[dict[str, object]] = field(default_factory=list)
    fetch_status: str = "not_fetched"
    evidence_notes: list[str] = field(default_factory=list)


def normalize_rootdata_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    if host in {"cn.rootdata.com", "www.rootdata.com", "rootdata.com"}:
        host = "rootdata.com"
    path = parsed.path.lower().replace("/projects/detail/", "/projects/detail/")
    query = parse_qs(parsed.query)
    key = query.get("k", [""])[0]
    return f"{host}{path}?k={key}"


def rootdata_fetch_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = "www.rootdata.com" if parsed.netloc.lower().endswith("rootdata.com") else parsed.netloc
    path = parsed.path
    path = re.sub(r"^/projects/detail/", "/Projects/detail/", path, flags=re.I)
    return parsed._replace(scheme=parsed.scheme or "https", netloc=host, path=path).geturl()


def rootdata_fetch_urls(url: str) -> list[str]:
    parsed = urlparse(url.strip())
    scheme = parsed.scheme or "https"
    path_lower = re.sub(r"^/projects/detail/", "/projects/detail/", parsed.path, flags=re.I)
    path_upper = re.sub(r"^/projects/detail/", "/Projects/detail/", parsed.path, flags=re.I)
    hosts = [parsed.netloc or "www.rootdata.com"]
    for host in ("cn.rootdata.com", "www.rootdata.com"):
        if host not in hosts:
            hosts.append(host)

    urls = []
    for host in hosts:
        for path in (path_lower, path_upper):
            candidate = parsed._replace(scheme=scheme, netloc=host, path=path).geturl()
            if candidate not in urls:
                urls.append(candidate)
    return urls


def fetch_text(url: str, *, retries: int = 2, timeout: int = 25, headers: dict[str, str] | None = None) -> str:
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    }
    if headers:
        request_headers.update(headers)
    request = Request(
        url,
        headers=request_headers,
    )
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            context = ssl._create_unverified_context()
            with urlopen(request, timeout=timeout, context=context) as response:
                return response.read().decode("utf-8", errors="ignore")
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            time.sleep(1 + attempt)
    raise RuntimeError(f"fetch failed for {url}: {last_error}")


def fetch_text_with_curl(url: str, *, timeout: int = 25) -> str:
    result = subprocess.run(
        [
            "curl",
            "-L",
            "--max-time",
            str(timeout),
            "-s",
            "-H",
            f"User-Agent: {USER_AGENT}",
            "-H",
            "Accept-Language: en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            url,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(result.stderr.strip() or f"curl failed with exit code {result.returncode}")
    return result.stdout


def fetch_text_with_browser(url: str, *, timeout: int = 70) -> str:
    if not shutil.which("node"):
        raise RuntimeError("node runtime unavailable")
    result = subprocess.run(
        ["node", "rootdata_browser_scrape.js", url],
        cwd=Path(__file__).resolve().parent,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(result.stderr.strip() or f"browser scrape failed with exit code {result.returncode}")
    return result.stdout


def fetch_text_with_vercel_browser(url: str) -> str:
    host = os.environ.get("VERCEL_URL", "").strip()
    if not host:
        raise RuntimeError("VERCEL_URL unavailable")
    if not host.startswith(("http://", "https://")):
        host = f"https://{host}"
    params = {"url": url}
    headers = {}
    bypass_secret = os.environ.get("VERCEL_AUTOMATION_BYPASS_SECRET", "").strip()
    if bypass_secret:
        headers["x-vercel-protection-bypass"] = bypass_secret
        headers["x-vercel-set-bypass-cookie"] = "true"
    request_url = f"{host.rstrip('/')}/api/rootdata-browser?{urlencode(params)}"
    return fetch_text(request_url, retries=1, timeout=65, headers=headers or None)


def clean_html_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_handle_from_url(url: str) -> str:
    parsed = urlparse(unescape(url))
    path = parsed.path.strip("/")
    return path.split("/")[0].lstrip("@") if path else ""


def parse_money_to_usd(amount_text: str, unit_text: str) -> Optional[int]:
    amount = float(amount_text.replace(",", ""))
    unit = unit_text.strip().lower()
    factor = 1
    if unit in {"k", "thousand"}:
        factor = 1_000
    elif unit in {"m", "mn", "million"}:
        factor = 1_000_000
    elif unit in {"b", "bn", "billion"}:
        factor = 1_000_000_000
    return int(amount * factor)


def parse_human_date(value: str) -> Optional[date]:
    value = re.sub(r"\s+", " ", value.strip())
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            from datetime import datetime

            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def extract_localized_value(block: str, key: str = "en_value") -> str:
    match = re.search(rf'\\"{re.escape(key)}\\":\\"([^\\"]+)', block)
    if not match:
        match = re.search(rf'"{re.escape(key)}":"([^"]+)', block)
    return unescape(match.group(1)).strip() if match else ""


def parse_structured_funding_rounds(html: str) -> list[dict[str, object]]:
    rounds: list[dict[str, object]] = []
    for amount_match in re.finditer(r'\\"facAmountUs\\":(\d+)|"facAmountUs":(\d+)', html):
        amount = int(amount_match.group(1) or amount_match.group(2))
        start = max(0, amount_match.start() - 700)
        end = min(len(html), amount_match.end() + 1600)
        window = html[start:end]
        window_after = html[amount_match.start():end]
        date_match = re.search(r'\\"facDate\\":\\"([^\\"]+)|"facDate":"([^"]+)', window_after)
        round_match = re.search(r'\\"roundsName\\":\{(.*?)\}', window_after, re.S)
        desc_match = re.search(r'\\"desc\\":\{(.*?)\}', window_after, re.S)
        funding_date = parse_human_date((date_match.group(1) or date_match.group(2)).split()[0]) if date_match else None
        round_name = (
            extract_localized_value(round_match.group(1)) or extract_localized_value(round_match.group(1), "cn_value")
            if round_match
            else ""
        )
        description = (
            extract_localized_value(desc_match.group(1)) or extract_localized_value(desc_match.group(1), "cn_value")
            if desc_match
            else ""
        )
        item = {
            "round": round_name,
            "amount_usd": amount,
            "date": funding_date.isoformat() if funding_date else "",
            "description": description,
        }
        if item not in rounds:
            rounds.append(item)
    return sorted(rounds, key=lambda item: str(item.get("date", "")))


def classify_event(name: str) -> str:
    lower = name.lower()
    if any(token in lower for token in ["binance alpha"]):
        return "Binance Alpha"
    if any(token in lower for token in ["binance futures", "binance perpetual", "币安合约"]):
        return "Binance 合约"
    if "coinbase" in lower:
        return "Coinbase"
    if any(token in lower for token in ["upbit", "bithumb", "korbit", "coinone", "韩国", "韩所"]):
        return "韩所"
    if any(token in lower for token in ["live for trading", "token live", "代币正式上线", "正式上线", "tge"]):
        return "TGE"
    return "Event"


def tge_method_from_event(event: dict[str, object]) -> str:
    name = str(event.get("name", ""))
    event_type = str(event.get("type", ""))
    if event_type in {"Binance Alpha", "Coinbase", "Binance 合约", "韩所"}:
        return event_type
    if "binance alpha" in name.lower():
        return "Binance Alpha"
    if "coinbase" in name.lower():
        return "Coinbase"
    return "RootData Token Live"


def parse_structured_events(html: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for match in re.finditer(r'\\"(?:hapDate|listDate)\\":\\"([^\\"]+)', html):
        start = match.start()
        next_object = html.find('},{\\', start)
        end = next_object if next_object != -1 else min(len(html), start + 1800)
        window = html[start:end]
        name_match = re.search(r'\\"oName\\":\{(.*?)\}', window, re.S)
        if not name_match:
            continue
        name = extract_localized_value(name_match.group(1)) or extract_localized_value(name_match.group(1), "cn_value")
        if not name:
            continue
        event_date = parse_human_date(match.group(1).split()[0])
        url_match = re.search(r'\\"siteUrl\\":\\"([^\\"]+)', window)
        event = {
            "type": classify_event(name),
            "name": name,
            "date": event_date.isoformat() if event_date else match.group(1),
            "url": unescape(url_match.group(1)) if url_match else "",
        }
        if event not in events:
            events.append(event)
    return sorted(events, key=lambda item: str(item.get("date", "")))


def compute_tge_probability(detail: LiveProjectDetail, text: str) -> tuple[int, list[str]]:
    score = 0
    evidence: list[str] = []
    lower = text.lower()
    if detail.funding_rounds:
        latest = max(detail.funding_rounds, key=lambda row: str(row.get("date", "")))
        if latest.get("date"):
            score += 20
            evidence.append(f"最近融资轮次: {latest.get('round')} {latest.get('date')}")
    if any(token in lower for token in ["tokenomics", "economic model", "代币经济", "经济模型"]):
        score += 30
        evidence.append("出现代币经济模型相关表述")
    if any(token in lower for token in ["airdrop", "points", "season", "积分", "空投", "赛季"]):
        score += 30
        evidence.append("出现积分/空投/赛季活动相关表述")
    if any(token in lower for token in ["ido", "launchpad", "sale", "公售"]):
        score += 20
        evidence.append("出现 IDO/Launchpad/Sale 相关表述")
    return min(score, 95), evidence


def extract_meta(html: str, name: str) -> str:
    patterns = [
        rf'<meta[^>]+name="{re.escape(name)}"[^>]+content="([^"]*)"',
        rf'<meta[^>]+property="{re.escape(name)}"[^>]+content="([^"]*)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            return unescape(match.group(1)).strip()
    return ""


def extract_label_value(html: str, label: str) -> str:
    pattern = rf"children\\\":\\\[\\\"{re.escape(label)}\\\",\\\":\\\"\\\]\}}.*?children\\\":\\\"([^\\\"]+)"
    match = re.search(pattern, html)
    if match:
        return unescape(match.group(1)).strip()
    plain = re.search(
        rf"{re.escape(label)}(?:<!--\s*-->)?\s*:</span>\s*<div[^>]*>\s*<span[^>]*>([^<]+)",
        html,
        re.I,
    )
    if plain:
        return clean_html_text(plain.group(1))
    plain = re.search(rf"{re.escape(label)}</[^>]+>\s*<[^>]+>\s*([^<]+)", html, re.I)
    return clean_html_text(plain.group(1)) if plain else ""


def extract_tags(html: str) -> list[str]:
    tags = []
    for label in re.findall(r'href="/projects\?sd=\d+"[^>]*>.*?children":"([^"]+)"', html):
        if label and label not in tags:
            tags.append(label)
    if not tags:
        for tag in ROOTDATA_BUCKET_MAP:
            if re.search(rf">\s*{re.escape(tag)}\s*<|children\\\":\\\"{re.escape(tag)}", html, re.I):
                tags.append(tag)
    return tags


def infer_bucket(tags: list[str], fallback_text: str = "") -> str:
    for tag in tags:
        mapped = ROOTDATA_BUCKET_MAP.get(tag.strip().lower()) or ROOTDATA_BUCKET_MAP.get(tag.strip())
        if mapped:
            return mapped
    lower = fallback_text.lower()
    for token, bucket in ROOTDATA_BUCKET_MAP.items():
        if token.lower() in lower:
            return bucket
    return ""


def infer_team_score(team_member_count: int, location: str) -> tuple[float, str]:
    location_lower = location.lower()
    pure_chinese = any(token in location_lower for token in ["china", "hong kong", "beijing", "shanghai", "中国", "香港"])
    background = "pure_chinese" if pure_chinese else ("international" if location else "unknown")
    if team_member_count >= 4:
        raw_score = 85
    elif team_member_count >= 2:
        raw_score = 70
    elif team_member_count == 1:
        raw_score = 55
    else:
        raw_score = 0
    return raw_score, background


def parse_rootdata_detail_html(html: str) -> LiveProjectDetail:
    detail = LiveProjectDetail(fetch_status="parsed")
    title = extract_meta(html, "og:title") or extract_meta(html, "twitter:title")
    if title:
        detail.project_name = re.split(r"\s+-\s+|\s+Project Introduction", title)[0].strip()
    if not detail.project_name:
        h1 = re.search(r"<h1[^>]*>([^<]+)</h1>", html, re.I)
        detail.project_name = clean_html_text(h1.group(1)) if h1 else ""
    ticker_match = re.search(r'<h3[^>]*>\s*([A-Z0-9]{2,12})\s*</h3>', html)
    if ticker_match:
        detail.token_ticker = ticker_match.group(1)
    if not detail.token_ticker:
        keyword_match = re.search(r"\bbuy\s+([A-Z0-9]{2,12})\b", detail.description or extract_meta(html, "keywords"), re.I)
        if keyword_match:
            detail.token_ticker = keyword_match.group(1).upper()
    if not detail.token_ticker:
        live_match = re.search(r'\b([A-Z0-9]{2,12})\s+is live for trading\b', html)
        if live_match:
            detail.token_ticker = live_match.group(1)

    detail.description = extract_meta(html, "description") or extract_meta(html, "og:description")

    links = re.findall(r'<a[^>]+href="([^"]+)"', html, re.I)
    for link in links:
        decoded = unescape(link)
        if not detail.x_url and re.match(r"https?://(?:www\.)?(?:x|twitter)\.com/[A-Za-z0-9_]+/?$", decoded, re.I):
            if "RootDataCrypto" not in decoded:
                detail.x_url = decoded
                detail.x_handle = normalize_handle_from_url(decoded)
        if not detail.website and decoded.startswith("http") and not re.search(r"rootdata|x\.com|twitter\.com|linkedin|github|discord|t\.me", decoded, re.I):
            detail.website = decoded

    detail.tags = extract_tags(html)
    detail.founded = extract_label_value(html, "Founded")
    detail.location = extract_label_value(html, "Location")
    detail.bucket = infer_bucket(detail.tags, " ".join([detail.description, html[:50000]]))

    member_names = set(re.findall(r'\\"name\\":\{[^}]*\\"en_value\\":\\"([^\\"]+)', html))
    if not member_names:
        member_names = set(re.findall(r'"name":\{[^}]*"en_value":"([^"]+)', html))
    detail.team_member_count = len(member_names)
    detail.named_team_member_count = len(member_names)
    detail.team_raw_score, detail.team_background = infer_team_score(detail.team_member_count, detail.location)

    total_match = re.search(r'\\"facAmountUS\\":(\d+)|"facAmountUS":(\d+)', html)
    if total_match:
        detail.funding_total_usd = int(total_match.group(1) or total_match.group(2))

    detail.funding_rounds = parse_structured_funding_rounds(html)
    if detail.funding_rounds:
        detail.funding_total_usd = sum(int(row.get("amount_usd", 0)) for row in detail.funding_rounds)
        latest_round = max(detail.funding_rounds, key=lambda row: str(row.get("date", "")))
        detail.latest_funding_amount_usd = int(latest_round.get("amount_usd", 0))
        detail.latest_funding_date = parse_human_date(str(latest_round.get("date", "")))

    funding_matches = []
    funding_pattern = re.compile(
        r"([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4}).{0,300}?raised\s+\$\s*([\d,.]+)\s*([KMB]|million|billion|thousand)?",
        re.I | re.S,
    )
    for match in funding_pattern.finditer(html):
        parsed_date = parse_human_date(match.group(1))
        parsed_amount = parse_money_to_usd(match.group(2), match.group(3) or "")
        if parsed_date and parsed_amount:
            funding_matches.append((parsed_date, parsed_amount))
    if funding_matches and not detail.funding_rounds:
        latest = max(funding_matches, key=lambda item: item[0])
        detail.latest_funding_date = latest[0]
        detail.latest_funding_amount_usd = latest[1]

    detail.roadmap_events = parse_structured_events(html)
    tge_events = [event for event in detail.roadmap_events if event.get("type") == "TGE"]
    if tge_events:
        detail.tge_status = "已 TGE"
        detail.tge_probability = 100
        detail.tge_date = parse_human_date(str(tge_events[0].get("date", "")))
        detail.tge_method = tge_method_from_event(tge_events[0])
        if detail.token_ticker in KNOWN_TGE_METHOD_OVERRIDES:
            detail.tge_method = KNOWN_TGE_METHOD_OVERRIDES[detail.token_ticker]
        detail.tge_evidence.append(str(tge_events[0].get("name", "")))
    else:
        detail.tge_status = "未 TGE"
        detail.tge_probability, detail.tge_evidence = compute_tge_probability(detail, html)
        detail.tge_method = "未 TGE"

    if detail.tge_date:
        for event in detail.roadmap_events:
            event_date = parse_human_date(str(event.get("date", "")))
            event["days_after_tge"] = (event_date - detail.tge_date).days if event_date else ""
    else:
        for event in detail.roadmap_events:
            event["days_after_tge"] = ""

    detail.roadmap_events = [event for event in detail.roadmap_events if event.get("type") != "Event"]

    if detail.project_name:
        detail.evidence_notes.append(f"RootData parsed project: {detail.project_name}")
    if detail.x_handle:
        detail.evidence_notes.append(f"RootData social link: @{detail.x_handle}")
    if detail.funding_rounds:
        for row in detail.funding_rounds:
            detail.evidence_notes.append(
                f"RootData funding round: {row.get('round') or 'Unknown'} "
                f"${int(row.get('amount_usd', 0)):,} {row.get('date')}"
            )
    elif detail.latest_funding_amount_usd:
        detail.evidence_notes.append(f"RootData latest funding: ${detail.latest_funding_amount_usd:,}")
    if detail.tge_status == "已 TGE" and detail.tge_date:
        detail.evidence_notes.append(f"TGE detected: {detail.tge_date.isoformat()}")
    return detail


def has_rootdata_detail_payload(detail: LiveProjectDetail) -> bool:
    return bool(
        detail.project_name
        and (
            detail.website
            or detail.funding_rounds
            or detail.team_member_count
            or detail.roadmap_events
        )
    )


def fetch_x_followers(handle: str) -> tuple[Optional[int], str]:
    normalized = handle.strip().lstrip("@")
    if not normalized:
        return None, "missing_handle"
    api_url = f"https://cdn.syndication.twimg.com/widgets/followbutton/info.json?screen_names={quote(normalized)}"
    try:
        raw = fetch_text(api_url, retries=1, timeout=12)
        data = json.loads(raw)
        if data and isinstance(data, list):
            followers = parse_followers(data[0].get("followers_count"))
            if followers is not None:
                return followers, "syndication.twimg.com"
    except Exception:
        pass
    for url in (f"https://x.com/{quote(normalized)}", f"https://twitter.com/{quote(normalized)}"):
        try:
            html = fetch_text(url, retries=1, timeout=12)
        except Exception:
            continue
        match = re.search(r'"followers_count"\s*:\s*(\d+)', html)
        if match:
            return int(match.group(1)), "x_html"
    return None, "not_found"


def fetch_live_project_detail(
    rootdata_url: str,
    x_handle: str = "",
    *,
    fetch_followers: bool = True,
    rootdata_html: str = "",
) -> LiveProjectDetail:
    detail = LiveProjectDetail(fetch_status="rootdata_incomplete")
    errors: list[str] = []
    if rootdata_html.strip():
        detail = parse_rootdata_detail_html(rootdata_html)
        if has_rootdata_detail_payload(detail):
            detail.fetch_status = "ok"

    for url in rootdata_fetch_urls(rootdata_url):
        if has_rootdata_detail_payload(detail):
            break
        try:
            html = fetch_text(url)
        except Exception:
            try:
                html = fetch_text_with_curl(url)
            except Exception as exc:
                errors.append(f"{url}: {exc}")
                continue
        candidate = parse_rootdata_detail_html(html)
        if not has_rootdata_detail_payload(candidate):
            try:
                candidate = parse_rootdata_detail_html(fetch_text_with_curl(url))
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        if has_rootdata_detail_payload(candidate):
            detail = candidate
            detail.fetch_status = "ok"
            break
        if candidate.project_name and not detail.project_name:
            detail = candidate
            detail.fetch_status = "rootdata_incomplete"

    if not has_rootdata_detail_payload(detail):
        try:
            url = rootdata_fetch_urls(rootdata_url)[0]
            if os.environ.get("VERCEL"):
                detail = parse_rootdata_detail_html(fetch_text_with_vercel_browser(url))
            else:
                detail = parse_rootdata_detail_html(fetch_text_with_browser(url))
        except Exception as exc:
            errors.append(f"browser: {exc}")
        if not has_rootdata_detail_payload(detail):
            detail.fetch_status = "rootdata_incomplete"
            detail.evidence_notes.append("RootData detail payload incomplete")
            if errors:
                detail.evidence_notes.append("RootData fetch errors: " + " | ".join(errors[-2:]))
        else:
            detail.fetch_status = "ok"

    if x_handle and normalize_handle_from_url(f"https://x.com/{x_handle}") != detail.x_handle:
        detail.x_handle = x_handle.strip().lstrip("@")
        detail.x_url = f"https://x.com/{detail.x_handle}"
    if fetch_followers and detail.x_handle:
        followers, source = fetch_x_followers(detail.x_handle)
        if followers is not None:
            detail.x_followers = followers
            detail.evidence_notes.append(f"X followers from {source}: {followers:,}")
        else:
            detail.evidence_notes.append(f"X follower fetch failed: {source}")
    return detail
