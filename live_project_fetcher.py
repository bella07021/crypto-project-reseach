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

from project_scorer import STRONG_INVESTOR_KEYWORDS, TOP_INVESTOR_KEYWORDS, parse_followers


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
    team_members: list[dict[str, str]] = field(default_factory=list)
    team_foreign_count: int = 0
    team_chinese_count: int = 0
    team_unknown_count: int = 0
    team_known_location_count: int = 0
    team_region_summary: str = ""
    team_raw_score: float = 0.0
    team_background: str = "unknown"
    latest_funding_amount_usd: Optional[int] = None
    latest_funding_date: Optional[date] = None
    funding_total_usd: Optional[int] = None
    funding_rounds: list[dict[str, object]] = field(default_factory=list)
    investors: list[str] = field(default_factory=list)
    chains: list[str] = field(default_factory=list)
    tge_status: str = "未 TGE"
    tge_probability: int = 0
    tge_date: Optional[date] = None
    tge_method: str = ""
    tge_evidence: list[str] = field(default_factory=list)
    tge_evidence_links: list[dict[str, str]] = field(default_factory=list)
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


def canonical_investor_name(keyword: str) -> str:
    names = {
        "a16z": "a16z crypto",
        "andreessen horowitz": "a16z crypto",
        "yzi labs": "YZi Labs",
        "binance labs": "Binance Labs",
        "coinbase ventures": "Coinbase Ventures",
        "jump crypto": "Jump Crypto",
        "okx ventures": "OKX Ventures",
        "hashkey": "HashKey",
    }
    return names.get(keyword, keyword.title())


def parse_investors_from_text(text: str) -> list[str]:
    normalized = re.sub(r"\\+", "", text).lower()
    found: list[str] = []
    for keyword in sorted(TOP_INVESTOR_KEYWORDS | STRONG_INVESTOR_KEYWORDS):
        if keyword in normalized:
            name = canonical_investor_name(keyword)
            if name not in found:
                found.append(name)
    return found


def parse_investors_from_funding_rounds(rounds: Iterable[dict[str, object]]) -> list[str]:
    return parse_investors_from_text(
        " ".join(str(row.get("description") or "") for row in rounds)
    )


def parse_project_chains(values: list[str], text: str) -> list[str]:
    haystack = " ".join(values + [text])
    normalized = re.sub(r"\\+", "", haystack).lower()
    candidates = [
        ("Base", r"(?<![a-z0-9])base(?![a-z0-9])|base生态"),
        ("Solana", r"(?<![a-z0-9])solana(?![a-z0-9])|solana生态"),
        ("Sui", r"(?<![a-z0-9])sui(?![a-z0-9])|sui生态"),
        ("BNB Chain", r"bnb chain|binance smart chain|(?<![a-z0-9])bsc(?![a-z0-9])"),
        ("Ethereum", r"(?<![a-z0-9])ethereum(?![a-z0-9])|(?<![a-z0-9])eth(?![a-z0-9])"),
        ("ZK", r"(?<![a-z0-9])zk(?![a-z0-9])|zero[- ]knowledge|零知识"),
        ("Arbitrum", r"arbitrum"),
        ("Optimism", r"optimism|op mainnet"),
        ("Polygon", r"polygon"),
        ("Mantle", r"mantle"),
        ("Linea", r"linea"),
        ("Scroll", r"scroll"),
        ("Blast", r"(?<![a-z0-9])blast(?![a-z0-9])"),
        ("Avalanche", r"avalanche|(?<![a-z0-9])avax(?![a-z0-9])"),
        ("Aptos", r"aptos"),
        ("Sei", r"(?<![a-z0-9])sei(?![a-z0-9])"),
        ("Near", r"(?<![a-z0-9])near(?![a-z0-9])"),
        ("Cosmos", r"cosmos"),
        ("TON", r"(?<![a-z0-9])ton(?![a-z0-9])"),
    ]
    chains: list[str] = []
    for name, pattern in candidates:
        if re.search(pattern, normalized, re.I) and name not in chains:
            chains.append(name)
    return chains


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


def nearest_x_status_url(text: str, tokens: list[str], allowed_handle: str = "") -> str:
    urls = list(re.finditer(r"https?://(?:www\.)?(?:x|twitter)\.com/[A-Za-z0-9_]+/status/\d+", text, re.I))
    if allowed_handle:
        normalized_handle = allowed_handle.strip().lstrip("@").lower()
        urls = [match for match in urls if normalize_handle_from_url(match.group(0)).lower() == normalized_handle]
    if not urls:
        return ""
    lower = text.lower()
    token_positions = [lower.find(token.lower()) for token in tokens if lower.find(token.lower()) != -1]
    if not token_positions:
        return unescape(urls[0].group(0))
    best = min(urls, key=lambda match: min(abs(match.start() - position) for position in token_positions))
    return unescape(best.group(0))


def context_around_tokens(text: str, tokens: list[str], radius: int = 260) -> str:
    lower = text.lower()
    positions = [lower.find(token.lower()) for token in tokens if lower.find(token.lower()) != -1]
    if not positions:
        return text[: radius * 2]
    position = min(positions)
    start = max(0, position - radius)
    end = min(len(text), position + radius)
    return text[start:end]


def is_project_airdrop_context(detail: LiveProjectDetail, text: str) -> bool:
    context = context_around_tokens(text, ["airdrop", "points", "season", "积分", "空投", "赛季"]).lower()
    small_collab_tokens = [
        "giveaway",
        "raffle",
        "campaign with",
        "partner giveaway",
        "collab",
        "collaboration",
        "allowlist giveaway",
        "whitelist giveaway",
        "抽奖",
        "联合",
        "白名单",
    ]
    identity_tokens = [
        detail.project_name.lower(),
        detail.token_ticker.lower(),
    ]
    distribution_tokens = [
        "token",
        "points",
        "season",
        "claim",
        "eligibility",
        "eligible",
        "allocation",
        "rewards",
        "mainnet",
        "genesis",
        "airdrop checker",
        "代币",
        "积分",
        "赛季",
        "领取",
        "资格",
        "分配",
    ]
    identity_tokens = [token for token in identity_tokens if token]
    distribution_tokens = [token for token in distribution_tokens if token]
    has_distribution_context = any(token in context for token in distribution_tokens)
    if any(token in context for token in small_collab_tokens) and not has_distribution_context:
        return False
    return has_distribution_context and (not identity_tokens or any(token in context for token in identity_tokens + distribution_tokens))


def compute_tge_probability(
    detail: LiveProjectDetail,
    text: str,
    *,
    include_links: bool = True,
) -> tuple[int, list[str], list[dict[str, str]]]:
    score = 0
    evidence: list[str] = []
    evidence_links: list[dict[str, str]] = []
    lower = text.lower()
    if detail.funding_rounds:
        latest = max(detail.funding_rounds, key=lambda row: str(row.get("date", "")))
        if latest.get("date"):
            score += 20
            evidence.append(f"最近融资轮次: {latest.get('round')} {latest.get('date')}")
    tokenomics_tokens = ["tokenomics", "economic model", "代币经济", "经济模型"]
    airdrop_tokens = ["airdrop", "points", "season", "积分", "空投", "赛季"]
    ido_tokens = ["ido", "launchpad", "sale", "公售"]
    if any(token in lower for token in tokenomics_tokens):
        score += 30
        label = "出现代币经济模型相关表述"
        evidence.append(label)
        url = nearest_x_status_url(text, tokenomics_tokens, detail.x_handle) if include_links else ""
        if url:
            evidence_links.append({"text": label, "url": url})
    if any(token in lower for token in airdrop_tokens) and is_project_airdrop_context(detail, text):
        score += 30
        label = "出现积分/空投/赛季活动相关表述"
        evidence.append(label)
        url = nearest_x_status_url(text, airdrop_tokens, detail.x_handle) if include_links else ""
        if url:
            evidence_links.append({"text": label, "url": url})
    if any(token in lower for token in ido_tokens):
        score += 20
        label = "出现 IDO/Launchpad/Sale 相关表述"
        evidence.append(label)
        url = nearest_x_status_url(text, ido_tokens, detail.x_handle) if include_links else ""
        if url:
            evidence_links.append({"text": label, "url": url})
    return min(score, 95), evidence, evidence_links


def compute_rootdata_tge_probability(detail: LiveProjectDetail) -> tuple[int, list[str], list[dict[str, str]]]:
    score = 0
    evidence: list[str] = []
    if detail.funding_rounds:
        latest = max(detail.funding_rounds, key=lambda row: str(row.get("date", "")))
        if latest.get("date"):
            score += 20
            evidence.append(f"最近融资轮次: {latest.get('round')} {latest.get('date')}")
    return score, evidence, []


def supplement_tge_evidence_from_x_html(detail: LiveProjectDetail, html: str) -> None:
    if not html or detail.tge_status != "未 TGE":
        return
    probability, evidence, evidence_links = compute_tge_probability(detail, html, include_links=True)
    detail.tge_probability = max(detail.tge_probability, probability)
    for item in evidence:
        if item not in detail.tge_evidence:
            detail.tge_evidence.append(item)
    for item in evidence_links:
        if item not in detail.tge_evidence_links:
            detail.tge_evidence_links.append(item)


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


def looks_like_international_name(value: str) -> bool:
    cleaned = value.strip()
    return bool(re.fullmatch(r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)+", cleaned))


CHINESE_REGION_TOKENS = {
    "china",
    "hong kong",
    "beijing",
    "shanghai",
    "shenzhen",
    "guangzhou",
    "hangzhou",
    "中国",
    "香港",
    "北京",
    "上海",
    "深圳",
    "广州",
    "杭州",
}

FOREIGN_REGION_TOKENS = {
    "united states",
    "usa",
    "singapore",
    "united kingdom",
    "uk",
    "london",
    "canada",
    "europe",
    "dubai",
    "uae",
    "germany",
    "france",
    "switzerland",
    "japan",
    "korea",
    "australia",
}


def classify_region_text(value: str) -> str:
    lower = value.lower()
    if any(token in lower for token in CHINESE_REGION_TOKENS):
        return "chinese"
    if any(token in lower for token in FOREIGN_REGION_TOKENS):
        return "foreign"
    return "unknown"


def parse_team_members(html: str) -> list[dict[str, str]]:
    members = []
    name_matches = list(re.finditer(r'\\"name\\":\{[^}]*\\"en_value\\":\\"([^\\"]+)', html))
    for index, match in enumerate(name_matches):
        end = name_matches[index + 1].start() if index + 1 < len(name_matches) else min(len(html), match.end() + 1200)
        segment = html[match.start():end]
        linkedin_match = re.search(r'\\"lyingUrl\\":\\"([^\\"]+)', segment)
        twitter_match = re.search(r'\\"twitterUrl\\":\\"([^\\"]+)', segment)
        member = {
            "name": unescape(match.group(1) or "").strip(),
            "linkedin_url": unescape(linkedin_match.group(1) if linkedin_match else "").strip(),
            "x_url": unescape(twitter_match.group(1) if twitter_match else "").strip(),
            "region": "unknown",
            "location": "",
        }
        if member["name"] and member not in members:
            members.append(member)
    if members:
        return members

    names = set(re.findall(r'\\"name\\":\{[^}]*\\"en_value\\":\\"([^\\"]+)', html))
    if not names:
        names = set(re.findall(r'"name":\{[^}]*"en_value":"([^"]+)', html))
    return [{"name": name, "linkedin_url": "", "x_url": "", "region": "unknown", "location": ""} for name in sorted(names)]


def summarize_team_regions(members: list[dict[str, str]], project_location: str = "") -> dict[str, object]:
    foreign = 0
    chinese = 0
    for member in members:
        region = member.get("region") or "unknown"
        if region == "unknown":
            region = classify_region_text(" ".join([project_location, member.get("location", ""), member.get("name", "")]))
            if region == "unknown" and looks_like_international_name(member.get("name", "")):
                region = "foreign"
            member["region"] = region
        if region == "foreign":
            foreign += 1
        elif region == "chinese":
            chinese += 1
    total = len(members)
    unknown = max(0, total - foreign - chinese)
    known = foreign + chinese
    if known == 0:
        background = "unknown"
    elif chinese > foreign:
        background = "pure_chinese"
    elif foreign > chinese:
        background = "international"
    else:
        background = "mixed"
    if foreign and not chinese:
        summary = f"{foreign}/{total} foreign"
    elif chinese and not foreign:
        summary = f"{chinese}/{total} Chinese"
    else:
        summary = f"{foreign}/{total} foreign · {chinese}/{total} Chinese · {unknown} unknown"
    return {
        "foreign": foreign,
        "chinese": chinese,
        "unknown": unknown,
        "known": known,
        "background": background,
        "summary": summary,
    }


def extract_linkedin_location(html: str) -> str:
    text = clean_html_text(html)
    patterns = [
        r"((?:San Francisco|New York|London|Singapore|Dubai|Toronto|Berlin|Paris|Shanghai|Beijing|Shenzhen|Hong Kong)[^·|,\n]{0,40}(?:,\s*(?:United States|USA|United Kingdom|UK|Singapore|China|Canada|Germany|France|UAE|Hong Kong))?)",
        r"(United States|USA|United Kingdom|UK|Singapore|China|Canada|Germany|France|UAE|Hong Kong)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).strip()
    return ""


def enrich_team_members_from_linkedin(
    members: list[dict[str, str]],
    *,
    budget_seconds: int = 120,
    fetcher=fetch_text,
) -> dict[str, object]:
    deadline = time.monotonic() + max(1, budget_seconds)
    for member in members:
        if time.monotonic() >= deadline:
            break
        linkedin_url = member.get("linkedin_url", "")
        if not linkedin_url or member.get("location"):
            continue
        remaining = max(1, int(deadline - time.monotonic()))
        try:
            html = fetcher(linkedin_url, retries=1, timeout=min(8, remaining))
        except Exception:
            continue
        location = extract_linkedin_location(html)
        if not location:
            continue
        member["location"] = location
        member["region"] = classify_region_text(location)
    return summarize_team_regions(members)


def apply_team_region_summary(detail: LiveProjectDetail, summary: dict[str, object]) -> None:
    detail.team_foreign_count = int(summary["foreign"])
    detail.team_chinese_count = int(summary["chinese"])
    detail.team_unknown_count = int(summary["unknown"])
    detail.team_known_location_count = int(summary["known"])
    detail.team_region_summary = str(summary["summary"])


def infer_team_score(team_member_count: int, location: str, member_names: set[str] | None = None, members: list[dict[str, str]] | None = None) -> tuple[float, str]:
    location_region = classify_region_text(location)
    pure_chinese = location_region == "chinese"
    region_summary = summarize_team_regions(members or [], location)
    international_members = sum(1 for name in (member_names or set()) if looks_like_international_name(name))
    if pure_chinese:
        background = "pure_chinese"
    elif region_summary["background"] in {"international", "pure_chinese", "mixed"}:
        background = str(region_summary["background"])
    elif location or international_members >= 2:
        background = "international"
    else:
        background = "unknown"
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
    detail.chains = parse_project_chains(detail.tags, " ".join([detail.description, html[:50000]]))

    detail.team_members = parse_team_members(html)
    member_names = {member.get("name", "") for member in detail.team_members if member.get("name")}
    detail.team_member_count = len(member_names)
    detail.named_team_member_count = len(member_names)
    region_summary = summarize_team_regions(detail.team_members, detail.location)
    apply_team_region_summary(detail, region_summary)
    detail.team_raw_score, detail.team_background = infer_team_score(
        detail.team_member_count,
        detail.location,
        member_names,
        detail.team_members,
    )

    total_match = re.search(r'\\"facAmountUS\\":(\d+)|"facAmountUS":(\d+)', html)
    if total_match:
        detail.funding_total_usd = int(total_match.group(1) or total_match.group(2))

    detail.funding_rounds = parse_structured_funding_rounds(html)
    detail.investors = parse_investors_from_funding_rounds(detail.funding_rounds)
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
        if str(tge_events[0].get("url", "")).startswith(("https://x.com/", "https://twitter.com/")):
            detail.tge_evidence_links.append(
                {
                    "text": str(tge_events[0].get("name", "")),
                    "url": str(tge_events[0].get("url", "")),
                }
            )
    else:
        detail.tge_status = "未 TGE"
        detail.tge_probability, detail.tge_evidence, detail.tge_evidence_links = compute_rootdata_tge_probability(detail)
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
    if detail.investors:
        detail.evidence_notes.append(f"Investors: {', '.join(detail.investors)}")
    if detail.chains:
        detail.evidence_notes.append(f"Chains: {', '.join(detail.chains)}")
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


def has_rootdata_waf_challenge(html: str) -> bool:
    return bool(re.search(r"sg\.captcha\.qcloud\.com|WafCaptcha|CaptchaScript", html, re.I))


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
            try:
                html = fetch_text_with_curl(url, timeout=12)
            except Exception:
                continue
        match = re.search(r'"followers_count"\s*:\s*(\d+)', html)
        if match:
            return int(match.group(1)), "x_html"
        follower_block = re.search(
            rf'href=["\']/{re.escape(normalized)}/(?:verified_)?followers["\'][^>]*>.*?'
            r'>\s*([\d,.]+)\s*([KMB])?\s*<.*?>\s*Followers\s*<',
            html,
            re.I | re.S,
        )
        if follower_block:
            value = float(follower_block.group(1).replace(",", ""))
            suffix = (follower_block.group(2) or "").upper()
            multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix, 1)
            return int(value * multiplier), "x_html"
    return None, "not_found"


def fetch_x_profile_html(handle: str) -> tuple[str, str]:
    normalized = handle.strip().lstrip("@")
    if not normalized:
        return "", "missing_handle"
    last_error = "not_found"
    for url in (f"https://x.com/{quote(normalized)}", f"https://twitter.com/{quote(normalized)}"):
        try:
            return fetch_text(url, retries=1, timeout=12), url
        except Exception as exc:
            last_error = str(exc)
    return "", last_error


def x_signal_urls(handle: str) -> list[str]:
    normalized = handle.strip().lstrip("@")
    if not normalized:
        return []
    return [
        f"https://x.com/{quote(normalized)}",
        f"https://x.com/search?{urlencode({'q': f'from:{normalized} tokenomics', 'src': 'typed_query'})}",
        f"https://x.com/search?{urlencode({'q': f'from:{normalized} airdrop', 'src': 'typed_query'})}",
        f"https://x.com/search?{urlencode({'q': f'from:{normalized} points OR season', 'src': 'typed_query'})}",
        f"https://x.com/search?{urlencode({'q': f'from:{normalized} IDO OR launchpad OR sale', 'src': 'typed_query'})}",
    ]


def fetch_x_signal_htmls_with_browser(urls: list[str]) -> list[str]:
    if not urls or not shutil.which("node") or os.environ.get("VERCEL"):
        return []
    result = subprocess.run(
        ["node", "x_signal_scrape.js", *urls],
        cwd=Path(__file__).resolve().parent,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    return [result.stdout]


def fetch_x_signal_htmls(handle: str) -> list[str]:
    urls = x_signal_urls(handle)
    htmls = []
    for url in urls:
        try:
            htmls.append(fetch_text(url, retries=1, timeout=12))
        except Exception:
            try:
                htmls.append(fetch_text_with_curl(url, timeout=12))
            except Exception:
                continue
    if not any(re.search(r"/status/\d+|airdrop|tokenomics|points|season|launchpad|ido", html, re.I) for html in htmls):
        htmls.extend(fetch_x_signal_htmls_with_browser(urls))
    return htmls


def fetch_live_project_detail(
    rootdata_url: str,
    x_handle: str = "",
    *,
    fetch_followers: bool = True,
    rootdata_html: str = "",
) -> LiveProjectDetail:
    detail = LiveProjectDetail(fetch_status="rootdata_incomplete")
    errors: list[str] = []
    saw_waf_challenge = False
    if rootdata_html.strip():
        saw_waf_challenge = has_rootdata_waf_challenge(rootdata_html)
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
        if has_rootdata_waf_challenge(html):
            saw_waf_challenge = True
            errors.append(f"{url}: RootData WAF captcha")
            continue
        candidate = parse_rootdata_detail_html(html)
        if not has_rootdata_detail_payload(candidate):
            try:
                curl_html = fetch_text_with_curl(url)
                if has_rootdata_waf_challenge(curl_html):
                    saw_waf_challenge = True
                    errors.append(f"{url}: RootData WAF captcha")
                    continue
                candidate = parse_rootdata_detail_html(curl_html)
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
                browser_html = fetch_text_with_vercel_browser(url)
            else:
                browser_html = fetch_text_with_browser(url)
            if has_rootdata_waf_challenge(browser_html):
                saw_waf_challenge = True
                errors.append("browser: RootData WAF captcha")
            else:
                detail = parse_rootdata_detail_html(browser_html)
        except Exception as exc:
            errors.append(f"browser: {exc}")
        if not has_rootdata_detail_payload(detail):
            detail.fetch_status = "rootdata_waf_blocked" if saw_waf_challenge else "rootdata_incomplete"
            detail.evidence_notes.append(
                "RootData WAF captcha blocked cloud fetch"
                if saw_waf_challenge
                else "RootData detail payload incomplete"
            )
            if errors:
                detail.evidence_notes.append("RootData fetch errors: " + " | ".join(errors[-2:]))
        else:
            detail.fetch_status = "ok"

    if not os.environ.get("VERCEL") and detail.team_members:
        budget = int(os.environ.get("TEAM_LINKEDIN_BUDGET_SECONDS", "120") or "120")
        summary = enrich_team_members_from_linkedin(detail.team_members, budget_seconds=budget)
        apply_team_region_summary(detail, summary)
        member_names = {member.get("name", "") for member in detail.team_members if member.get("name")}
        detail.team_raw_score, detail.team_background = infer_team_score(
            detail.team_member_count,
            detail.location,
            member_names,
            detail.team_members,
        )
        if detail.team_known_location_count:
            detail.evidence_notes.append(
                f"Team region: {detail.team_foreign_count} foreign / "
                f"{detail.team_chinese_count} Chinese / {detail.team_unknown_count} unknown"
            )

    if x_handle and normalize_handle_from_url(f"https://x.com/{x_handle}") != detail.x_handle:
        detail.x_handle = x_handle.strip().lstrip("@")
        detail.x_url = f"https://x.com/{detail.x_handle}"
    if detail.x_handle:
        for x_html in fetch_x_signal_htmls(detail.x_handle):
            supplement_tge_evidence_from_x_html(detail, x_html)
    if fetch_followers and detail.x_handle:
        followers, source = fetch_x_followers(detail.x_handle)
        if followers is not None:
            detail.x_followers = followers
            detail.evidence_notes.append(f"X followers from {source}: {followers:,}")
        else:
            detail.evidence_notes.append(f"X follower fetch failed: {source}")
    return detail
