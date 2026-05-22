#!/usr/bin/env python3
import csv
import json
import math
import re
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen


BASE_URL = "https://cn.rootdata.com"
PROJECTS_URL = f"{BASE_URL}/Projects"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# RootData top-level mapping requested by the user.
CATEGORY_RULES = {
    "infra": {"基础设施", "Layer1", "Layer2", "DePIN", "zk", "跨链桥", "云计算", "隐私", "DID"},
    "defi": {"DeFi", "DEX", "借贷", "衍生品", "永续合约", "LSD", "RWA", "支付", "收益聚合器", "预测市场"},
    "nft": {"NFT", "创作者经济", "数字认证"},
    "gamefi": {"游戏", "游戏解决方案", "博彩游戏", "卡牌游戏"},
    "cefi": {"CeFi", "CEX", "钱包", "加密卡"},
    "dao": {"DAO", "DAO解决方案"},
    "tools&information": {"工具", "数据&分析", "开发者平台", "区块链API", "链上数据", "安全解决方案"},
    "social&entertainment": {"社交", "娱乐", "预测市场", "创作者经济"},
}


@dataclass
class ProjectRecord:
    primary_category: str
    matched_subtags: str
    rootdata_subtags: str
    rootdata_rank_page: int
    rootdata_rank_index: int
    project_name: str
    project_url: str
    x_url: str
    x_handle: str
    x_followers: Optional[int]
    follower_source: str


def fetch(url: str, *, method: str = "GET", data: Optional[bytes] = None, retries: int = 3) -> str:
    req = Request(
        url,
        data=data,
        method=method,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    last_error = None
    for attempt in range(retries):
        try:
            with urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"fetch failed for {url}: {last_error}")


def clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_tag_ids(index_html: str) -> Dict[str, int]:
    tag_map: Dict[str, int] = {}
    # Search-engine snapshot exposed the "sd" parameter, and the page HTML contains tag links.
    for match in re.finditer(r'href="/Projects\?sd=(\d+)&st=1"[^>]*>([^<]+)</a>', index_html):
        tag_id = int(match.group(1))
        name = clean_text(match.group(2))
        if name:
            tag_map[name] = tag_id
    return tag_map


def parse_total_pages(html: str) -> int:
    m = re.search(r"Total\s+\d+\s+前往", html)
    if not m:
        return 1
    pages = [int(x) for x in re.findall(r">\s*(\d+)\s*<", html[m.start() - 300 : m.end() + 100])]
    return max(pages) if pages else 1


def parse_project_cards(html: str) -> List[Tuple[str, str, List[str]]]:
    cards: List[Tuple[str, str, List[str]]] = []
    # Each card includes a detail link followed by a tag line.
    pattern = re.compile(
        r'href="(/Projects/detail/[^"]+)"[^>]*>\s*([^<]+?)\s*</a>.*?'
        r'(?:\n|\r|.){0,500}?'
        r'(基础设施|DeFi|CeFi|NFT|游戏|社交|工具|DAO|数据&分析|Layer1|Layer2|DePIN|RWA|LSD|DEX|借贷|衍生品|永续合约|跨链桥|钱包|创作者经济|DID|隐私|安全解决方案|区块链API|链上数据|开发者平台)[^<\n\r]*',
        re.S,
    )
    for match in pattern.finditer(html):
        project_url = urljoin(BASE_URL, unescape(match.group(1)))
        project_name = clean_text(match.group(2))
        snippet = html[match.start() : match.start() + 1200]
        tag_line_match = re.search(
            rf"{re.escape(project_name)}\s*</a>.*?<.*?>\s*([^<]+?)\s*</",
            snippet,
            re.S,
        )
        tag_line = clean_text(tag_line_match.group(1)) if tag_line_match else match.group(3)
        tags = [t.strip() for t in re.split(r"[、,，/]+", tag_line) if t.strip()]
        cards.append((project_name, project_url, tags))

    deduped = []
    seen = set()
    for item in cards:
        key = item[1]
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def map_category(tags: Iterable[str]) -> Tuple[str, List[str]]:
    tag_set = set(tags)
    matched = []
    for category, rule_tags in CATEGORY_RULES.items():
        overlap = sorted(tag_set & rule_tags)
        if overlap:
            matched.extend((category, tag) for tag in overlap)
    if matched:
        primary = matched[0][0]
        return primary, [tag for cat, tag in matched if cat == primary]
    return "others", []


def extract_x_url(detail_html: str) -> Optional[str]:
    patterns = [
        r'https://(?:www\.)?(?:twitter\.com|x\.com)/[A-Za-z0-9_]+',
        r'http://(?:www\.)?(?:twitter\.com|x\.com)/[A-Za-z0-9_]+',
    ]
    for pattern in patterns:
        m = re.search(pattern, detail_html)
        if m:
            return m.group(0).rstrip('",')
    return None


def normalize_x_handle(x_url: str) -> str:
    path = urlparse(x_url).path.strip("/")
    handle = path.split("/")[0] if path else ""
    return handle.lstrip("@")


def fetch_x_followers(handle: str) -> Tuple[Optional[int], str]:
    # Try public syndication endpoint first.
    api_url = f"https://cdn.syndication.twimg.com/widgets/followbutton/info.json?screen_names={quote(handle)}"
    try:
        raw = fetch(api_url)
        data = json.loads(raw)
        if data and isinstance(data, list):
            followers = data[0].get("followers_count")
            if isinstance(followers, int):
                return followers, "syndication.twimg.com"
    except Exception:
        pass

    # Fallback to raw x.com HTML if available.
    for candidate in (f"https://x.com/{handle}", f"https://twitter.com/{handle}"):
        try:
            html = fetch(candidate)
        except Exception:
            continue
        m = re.search(r'"followers_count"\s*:\s*(\d+)', html)
        if m:
            return int(m.group(1)), "x.com_html"
        m = re.search(r'([\d,\.]+[KMB]?)\s+Followers', html, re.I)
        if m:
            parsed = parse_compact_number(m.group(1))
            if parsed is not None:
                return parsed, "x.com_html"
    return None, "not_found"


def parse_compact_number(value: str) -> Optional[int]:
    value = value.strip().upper().replace(",", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([KMB])?", value)
    if not m:
        return None
    number = float(m.group(1))
    suffix = m.group(2)
    factor = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix, 1)
    return int(number * factor)


def write_csv(path: Path, rows: List[Dict[str, object]], fieldnames: List[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    out_dir = Path.cwd()
    index_html = fetch(PROJECTS_URL)
    tag_ids = parse_tag_ids(index_html)
    wanted_rootdata_tags = {
        "infra": ["基础设施"],
        "defi": ["DeFi"],
        "nft": ["NFT"],
        "gamefi": ["游戏"],
        "cefi": ["CeFi"],
        "dao": ["DAO"],
        "tools&information": ["工具", "数据&分析"],
        "social&entertainment": ["社交", "创作者经济"],
    }

    project_rows: List[ProjectRecord] = []
    missing_tag_names = []

    for user_category, root_tags in wanted_rootdata_tags.items():
        collected = 0
        for root_tag in root_tags:
            tag_id = tag_ids.get(root_tag)
            if not tag_id:
                missing_tag_names.append(root_tag)
                continue
            first_page_html = fetch(f"{PROJECTS_URL}?sd={tag_id}&st=1")
            total_pages = parse_total_pages(first_page_html)
            for page in range(1, total_pages + 1):
                if collected >= 500:
                    break
                page_url = f"{PROJECTS_URL}?sd={tag_id}&st=1&page={page}"
                page_html = first_page_html if page == 1 else fetch(page_url)
                cards = parse_project_cards(page_html)
                for idx, (project_name, project_url, tags) in enumerate(cards, start=1):
                    if collected >= 500:
                        break
                    primary_category, matched_subtags = map_category(tags)
                    if primary_category != user_category and user_category != "tools&information":
                        # Keep category pages aligned to the requested bucket.
                        continue
                    if user_category == "tools&information":
                        if primary_category not in {"tools&information"}:
                            continue
                    detail_html = fetch(project_url)
                    x_url = extract_x_url(detail_html) or ""
                    handle = normalize_x_handle(x_url) if x_url else ""
                    followers = None
                    source = "no_x_url"
                    if handle:
                        followers, source = fetch_x_followers(handle)
                    project_rows.append(
                        ProjectRecord(
                            primary_category=user_category,
                            matched_subtags="|".join(matched_subtags),
                            rootdata_subtags="|".join(tags),
                            rootdata_rank_page=page,
                            rootdata_rank_index=idx,
                            project_name=project_name,
                            project_url=project_url,
                            x_url=x_url,
                            x_handle=handle,
                            x_followers=followers,
                            follower_source=source,
                        )
                    )
                    collected += 1
        print(f"{user_category}: collected {collected}", file=sys.stderr)

    detail_rows = [
        {
            "primary_category": row.primary_category,
            "matched_subtags": row.matched_subtags,
            "rootdata_subtags": row.rootdata_subtags,
            "rootdata_rank_page": row.rootdata_rank_page,
            "rootdata_rank_index": row.rootdata_rank_index,
            "project_name": row.project_name,
            "project_url": row.project_url,
            "x_url": row.x_url,
            "x_handle": row.x_handle,
            "x_followers": row.x_followers if row.x_followers is not None else "",
            "follower_source": row.follower_source,
        }
        for row in project_rows
    ]
    write_csv(
        out_dir / "rootdata_projects_x_followers_detail.csv",
        detail_rows,
        [
            "primary_category",
            "matched_subtags",
            "rootdata_subtags",
            "rootdata_rank_page",
            "rootdata_rank_index",
            "project_name",
            "project_url",
            "x_url",
            "x_handle",
            "x_followers",
            "follower_source",
        ],
    )

    grouped: Dict[str, List[int]] = defaultdict(list)
    counts: Dict[str, int] = defaultdict(int)
    with_x: Dict[str, int] = defaultdict(int)
    for row in project_rows:
        counts[row.primary_category] += 1
        if row.x_followers is not None:
            with_x[row.primary_category] += 1
            grouped[row.primary_category].append(row.x_followers)

    summary_rows = []
    for category in [
        "infra",
        "defi",
        "nft",
        "gamefi",
        "cefi",
        "dao",
        "tools&information",
        "social&entertainment",
        "others",
    ]:
        vals = sorted(grouped.get(category, []))
        summary_rows.append(
            {
                "primary_category": category,
                "projects_collected": counts.get(category, 0),
                "projects_with_x_followers": with_x.get(category, 0),
                "min_followers": vals[0] if vals else "",
                "max_followers": vals[-1] if vals else "",
                "median_followers": int(statistics.median(vals)) if vals else "",
            }
        )
    write_csv(
        out_dir / "rootdata_projects_x_followers_summary.csv",
        summary_rows,
        [
            "primary_category",
            "projects_collected",
            "projects_with_x_followers",
            "min_followers",
            "max_followers",
            "median_followers",
        ],
    )

    if missing_tag_names:
        print("missing tags:", ", ".join(sorted(set(missing_tag_names))), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
