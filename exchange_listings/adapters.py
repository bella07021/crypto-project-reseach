import html
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser


BINANCE_LIST_URL = (
    "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query"
    "?catalogId=48&pageNo=1&pageSize=30"
)
BYBIT_LIST_URL = "https://announcements.bybit.com/en/?category=new_crypto&page=1"
OKX_LIST_URL = "https://www.okx.com/en-us/help/section/announcements-new-listings"
KUCOIN_LIST_URL = "https://www.kucoin.com/announcement/new-listings"
MEXC_LIST_URL = "https://www.mexc.fm/announcements/new-listings"
KRAKEN_LIST_URL = "https://www.kraken.com/zh-cn/listings"
COINBASE_TRANSPARENCY_URL = "https://www.coinbase.com/zh-cn/blog/increasing-transparency-for-new-asset-listings-on-coinbase"
COINBASE_X_ROADMAP_URL = "https://x.com/search?q=from%3ACoinbaseMarkets%20roadmap&src=typed_query"
UPBIT_LIST_URL = "https://www.upbit.com/service_center/notice"
BITHUMB_LIST_URL = "https://feed.bithumb.com/notice"
GATE_LIST_URL = "https://www.gate.com/zh/announcements/newlisted"
BITGET_LIST_URL = "https://www.bitget.com/zh-CN/support/sections/5955813039257"


class SourceUnavailable(RuntimeError):
    pass


def fetch_live_sources(
    exchange: str,
    *,
    mode="incremental",
    months=3,
    limit=5,
    fetch_text=None,
    fetch_browser_text=None,
    now: datetime | None = None,
    max_pages=3,
) -> list[dict]:
    del mode
    fetch = fetch_text or _fetch_text
    exchange = exchange.lower()
    cutoff = _lookback_cutoff(months, now)
    browser_fetch = fetch_browser_text or fetch_text or _fetch_browser_text
    if exchange == "binance":
        sources = _fetch_paginated(fetch, _binance_url, parse_binance_sources, limit=limit, max_pages=max_pages, cutoff=cutoff)
        sources = _enrich_binance_sources(fetch, sources)
        return _filter_recent_sources(sources, cutoff, limit)
    if exchange == "okx":
        sources = _fetch_paginated(fetch, _okx_url, parse_okx_sources, limit=limit, max_pages=max_pages, cutoff=cutoff)
        return _filter_recent_sources(sources, cutoff, limit)
    if exchange == "bybit":
        sources = _fetch_with_browser_fallback(
            fetch,
            browser_fetch,
            _bybit_url,
            parse_bybit_sources,
            limit=limit,
            max_pages=max_pages,
            cutoff=cutoff,
        )
        sources = _enrich_bybit_sources(browser_fetch, sources)
        return _filter_recent_sources(sources, cutoff, limit)
    if exchange == "kucoin":
        sources = _fetch_paginated(fetch, _kucoin_url, parse_kucoin_sources, limit=limit, max_pages=max_pages, cutoff=cutoff)
        return _filter_recent_sources(sources, cutoff, limit)
    if exchange == "mexc":
        sources = _fetch_paginated(fetch, _mexc_url, parse_mexc_sources, limit=limit, max_pages=max_pages, cutoff=cutoff)
        return _filter_recent_sources(sources, cutoff, limit)
    if exchange == "coinbase":
        try:
            sources = parse_coinbase_sources(browser_fetch(COINBASE_X_ROADMAP_URL), limit=limit)
        except Exception:
            sources = []
        if sources:
            return sources
        return parse_coinbase_sources(browser_fetch(COINBASE_TRANSPARENCY_URL), limit=limit)
    if exchange == "upbit":
        sources = _fetch_paginated(browser_fetch, _upbit_url, parse_upbit_sources, limit=limit, max_pages=max_pages, cutoff=cutoff)
        return _filter_recent_sources(sources, cutoff, limit)
    if exchange == "bithumb":
        sources = _fetch_paginated(browser_fetch, _bithumb_url, parse_bithumb_sources, limit=limit, max_pages=max_pages, cutoff=cutoff)
        return _filter_recent_sources(sources, cutoff, limit)
    if exchange == "gate":
        sources = _fetch_paginated(browser_fetch, _gate_url, parse_gate_sources, limit=limit, max_pages=max_pages, cutoff=cutoff)
        return _filter_recent_sources(sources, cutoff, limit)
    if exchange == "bitget":
        sources = _fetch_paginated(browser_fetch, _bitget_url, parse_bitget_sources, limit=limit, max_pages=max_pages, cutoff=cutoff)
        return _filter_recent_sources(sources, cutoff, limit)
    if exchange == "kraken":
        return parse_kraken_sources(fetch(KRAKEN_LIST_URL), limit=limit)
    raise SourceUnavailable(f"{exchange} live source is blocked or not implemented yet")


def parse_coinbase_sources(page_html: str, *, limit: int) -> list[dict]:
    sources = []
    seen = set()
    for status_id, text in _x_status_contexts(page_html, "CoinbaseMarkets"):
        lowered = text.lower()
        if status_id in seen or "roadmap" not in lowered or "not a roadmap" in lowered:
            continue
        seen.add(status_id)
        sources.append(
            {
                "exchange": "coinbase",
                "source_type": "official_x",
                "source_url": f"https://x.com/CoinbaseMarkets/status/{status_id}",
                "title": _coinbase_x_title(text),
                "raw_text": text,
                "published_at": None,
                "fetched_at": _utc_now(),
                "external_id": status_id,
                "raw_payload_json": None,
                "detection_reason": "coinbase_x_roadmap_search",
            }
        )
        if len(sources) >= limit:
            break
    if sources:
        return sources
    return _parse_coinbase_roadmap_blog_sources(page_html, limit=limit)


def parse_binance_sources(payload: str, *, limit: int) -> list[dict]:
    data = json.loads(payload)
    articles = (data.get("data") or {}).get("articles") or []
    sources = []
    for article in articles:
        title = article.get("title") or ""
        if not _looks_like_binance_listing_title(title):
            continue
        code = article.get("code") or str(article.get("id") or "")
        raw_text = _strip_html(article.get("body") or title)
        sources.append(
            _source(
                "binance",
                f"https://www.binance.com/en/support/announcement/{code}" if code else None,
                title,
                raw_text,
                external_id=code or None,
                raw_payload=article,
            )
        )
        if len(sources) >= limit:
            break
    return sources


def _enrich_binance_sources(fetch, sources: list[dict]) -> list[dict]:
    enriched = []
    for source in sources:
        code = source.get("external_id")
        if not code:
            enriched.append(source)
            continue
        try:
            detail = json.loads(fetch(_binance_detail_url(str(code))))
            detail_data = detail.get("data") or {}
            body_text = _extract_binance_article_body_text(detail_data.get("body"))
        except Exception:
            body_text = ""
            detail_data = {}
        if body_text:
            raw_payload = source.get("raw_payload_json") or {}
            if isinstance(raw_payload, dict):
                raw_payload = {**raw_payload, "detail": detail_data}
            enriched.append({**source, "raw_text": _collapse_space(body_text), "raw_payload_json": raw_payload})
        else:
            enriched.append(source)
    return enriched


def parse_okx_sources(page_html: str, *, limit: int) -> list[dict]:
    sources = []
    for title, href in _anchor_texts(page_html):
        if not href or "/help/" not in href or "Published on" not in title:
            continue
        article_title, published_at = _split_okx_title(title)
        if not _looks_like_spot_listing_title(article_title):
            continue
        sources.append(
            _source(
                "okx",
                _absolute_url("https://www.okx.com", href),
                article_title,
                article_title,
                published_at=published_at,
                external_id=href.rsplit("/", 1)[-1],
            )
        )
        if len(sources) >= limit:
            break
    return sources


def parse_kucoin_sources(page_html: str, *, limit: int) -> list[dict]:
    card_pattern = re.compile(
        r'<a[^>]+href="(?P<href>/announcement/[^"]+)"[^>]*>'
        r".{0,2000}?"
        r"<h3[^>]*>(?P<title>.*?)</h3>"
        r"(?P<body>.{0,2500}?)"
        r"</a>",
        re.DOTALL,
    )
    sources = []
    seen = set()
    for match in card_pattern.finditer(page_html):
        href = match.group("href")
        title = _strip_html(match.group("title"))
        if href in seen:
            continue
        seen.add(href)
        if not _looks_like_spot_listing_title(title):
            continue
        body_text = _strip_html(match.group("body"))
        published_at = _parse_kucoin_published_at(body_text)
        sources.append(
            _source(
                "kucoin",
                _absolute_url("https://www.kucoin.com", href),
                title,
                f"{title}. {body_text}",
                published_at=published_at,
                external_id=(href or title).rsplit("/", 1)[-1],
            )
        )
        if len(sources) >= limit:
            break
    return sources


def parse_mexc_sources(page_html: str, *, limit: int) -> list[dict]:
    unescaped = html.unescape(page_html)
    sources = _parse_mexc_next_section_articles(unescaped, limit=limit)
    if sources:
        return sources
    pattern = re.compile(
        r'<a[^>]+title="(?P<title>[^"]+)"[^>]+href="(?P<href>/announcements/article/[^"]+)"'
        r".{0,1200}?"
        r'<time[^>]+dateTime="(?P<published>[^"]+)"',
        re.DOTALL,
    )
    sources = []
    seen = set()
    for match in pattern.finditer(unescaped):
        title = _collapse_space(match.group("title"))
        href = match.group("href")
        if href in seen or not _looks_like_spot_listing_title(title):
            continue
        seen.add(href)
        symbol = _first_parenthesized_symbol(title) or href.rsplit("-", 1)[-1]
        sources.append(
            _source(
                "mexc",
                _absolute_url("https://www.mexc.com", href),
                title,
                title,
                published_at=_normalize_iso(match.group("published")),
                external_id=symbol,
            )
        )
        if len(sources) >= limit:
            break
    return sources


def parse_bybit_sources(page_html: str, *, limit: int) -> list[dict]:
    data = _next_data(page_html)
    items = (
        ((data.get("props") or {}).get("pageProps") or {})
        .get("articleInitEntity", {})
        .get("list", [])
    )
    sources = []
    for item in items:
        title = item.get("title") or ""
        topics = item.get("topics") or []
        if "Spot" not in topics and "Spot Listings" not in topics:
            continue
        if not _looks_like_spot_listing_title(f"{title} {item.get('description') or ''}"):
            continue
        raw_text = _collapse_space(f"{title}. {item.get('description') or ''}")
        sources.append(
            _source(
                "bybit",
                _absolute_url("https://announcements.bybit.com", item.get("url")),
                title,
                raw_text,
                published_at=_iso_from_epoch(item.get("publish_time")),
                external_id=item.get("objectID"),
                raw_payload=item,
            )
        )
        if len(sources) >= limit:
            break
    return sources


def _enrich_bybit_sources(fetch, sources: list[dict]) -> list[dict]:
    enriched = []
    for source in sources:
        url = source.get("source_url")
        if not url:
            enriched.append(source)
            continue
        try:
            detail_html = fetch(str(url))
        except Exception:
            enriched.append(source)
            continue
        published_at = _bybit_detail_published_at(detail_html) or source.get("published_at")
        detail_text = _bybit_detail_text(detail_html)
        raw_text = _collapse_space(f"{source.get('raw_text') or ''} {detail_text}")
        enriched.append({**source, "published_at": published_at, "raw_text": raw_text or source.get("raw_text", "")})
    return enriched


def _bybit_detail_published_at(page_html: str) -> str | None:
    return _normalize_iso(_meta_content(page_html, "article:published_time"))


def _bybit_detail_text(page_html: str) -> str:
    text = _strip_html(page_html)
    start = text.find("Disclaimer:")
    if start == -1:
        description = _meta_content(page_html, "og:description") or _meta_content(page_html, "description") or ""
        return _collapse_space(description)
    return _collapse_space(text[start:start + 4000])


def parse_upbit_sources(page_html: str, *, limit: int) -> list[dict]:
    sources = []
    seen = set()
    for text, href in _anchor_texts(page_html):
        if not href or "/service_center/notice" not in href:
            continue
        title = _strip_upbit_category_prefix(_normalize_spaced_notice_text(text))
        if href in seen or "종료" in title or not _looks_like_spot_listing_title(title):
            continue
        seen.add(href)
        sources.append(
            _source(
                "upbit",
                _absolute_url("https://www.upbit.com", href),
                title,
                title,
                external_id=_query_id(href),
            )
        )
        if len(sources) >= limit:
            break
    return sources


def parse_bithumb_sources(page_html: str, *, limit: int) -> list[dict]:
    data = _next_data(page_html)
    notices = ((data.get("props") or {}).get("pageProps") or {}).get("noticeList") or []
    sources = []
    for item in notices:
        category = _collapse_space(f"{item.get('categoryName1') or ''} {item.get('categoryName2') or ''}")
        title = item.get("title") or ""
        if "마켓 추가" not in category or not _looks_like_spot_listing_title(title):
            continue
        notice_id = str(item.get("id") or "")
        sources.append(
            _source(
                "bithumb",
                f"https://feed.bithumb.com/notice/{notice_id}" if notice_id else BITHUMB_LIST_URL,
                title,
                title,
                published_at=_iso_from_datetime_text(item.get("publicationDateTime"), tz_hours=9),
                external_id=notice_id or title,
                raw_payload=item,
            )
        )
        if len(sources) >= limit:
            break
    return sources


def parse_gate_sources(page_html: str, *, limit: int) -> list[dict]:
    data = _next_data(page_html)
    articles = (((data.get("props") or {}).get("pageProps") or {}).get("listData") or {}).get("list") or []
    sources = []
    for item in articles:
        title = item.get("title") or ""
        brief = item.get("brief") or ""
        combined = f"{title}. {brief}"
        if not _looks_like_spot_listing_title(combined):
            continue
        article_id = str(item.get("id") or "")
        href = item.get("url") or (f"/announcements/article/{article_id}" if article_id else None)
        sources.append(
            _source(
                "gate",
                _absolute_url("https://www.gate.com/zh", href),
                title,
                combined,
                published_at=_iso_from_epoch(item.get("release_timestamp")),
                external_id=article_id or title,
                raw_payload=item,
            )
        )
        if len(sources) >= limit:
            break
    return sources


def parse_bitget_sources(page_html: str, *, limit: int) -> list[dict]:
    sources = []
    seen = set()
    pattern = re.compile(
        r'\{[^{}]*"contentId"\s*:\s*"(?P<id>\d+)"[^{}]*"title"\s*:\s*"(?P<title>(?:[^"\\]|\\.)*?)"[^{}]*"showTime"\s*:\s*"(?P<show_time>\d+)"[^{}]*\}',
        re.DOTALL,
    )
    for match in pattern.finditer(page_html):
        content_id = match.group("id")
        title = _decode_json_string(match.group("title"))
        if content_id in seen or not _looks_like_spot_listing_title(title):
            continue
        seen.add(content_id)
        sources.append(
            _source(
                "bitget",
                f"https://www.bitget.com/zh-CN/support/articles/{content_id}",
                title,
                title,
                published_at=_iso_from_epoch_ms(match.group("show_time")),
                external_id=content_id,
            )
        )
        if len(sources) >= limit:
            break
    if sources:
        return sources
    return _parse_bitget_anchor_sources(page_html, limit=limit)


def parse_kraken_sources(page_html: str, *, limit: int) -> list[dict]:
    parser = _KrakenCardParser()
    parser.feed(page_html)
    sources = []
    seen = set()
    for project_name, symbol in parser.items:
        if symbol in seen:
            continue
        seen.add(symbol)
        raw_text = f"Kraken will list token ({symbol}) for spot trading. Project: {project_name}."
        sources.append(
            _source(
                "kraken",
                KRAKEN_LIST_URL,
                f"Kraken upcoming listing: {project_name} ({symbol})",
                raw_text,
                external_id=symbol,
                raw_payload={"project_name": project_name, "token_symbol": symbol},
                detection_reason="kraken_listings_page_upcoming",
            )
        )
        sources[-1]["project_name"] = project_name
        if len(sources) >= limit:
            break
    return sources


def _fetch_text(url: str) -> str:
    result = subprocess.run(
        [
            "curl",
            "-L",
            "--max-time",
            "25",
            "-A",
            "Mozilla/5.0 (compatible; exchange-listing-sync/1.0)",
            "-s",
            url,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _fetch_browser_text(url: str) -> str:
    script = r"""
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  });
  const page = await browser.newPage({ userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36' });
  await page.goto(process.argv[1], { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(3500);
  console.log(await page.content());
  await browser.close();
})().catch((error) => {
  console.error(error && error.message ? error.message : String(error));
  process.exit(1);
});
"""
    node = "/Users/apple/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
    node_path = "/Users/apple/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
    try:
        result = subprocess.run(
            [node, "-e", script, url],
            check=True,
            capture_output=True,
            text=True,
            timeout=45,
            env={**os.environ, "NODE_PATH": node_path},
        )
        return result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return _fetch_text(url)


def _fetch_paginated(fetch, url_for_page, parser, *, limit: int, max_pages: int, cutoff: datetime | None = None) -> list[dict]:
    sources = []
    seen_keys = set()
    for page in range(1, max_pages + 1):
        page_sources = parser(fetch(url_for_page(page)), limit=limit)
        if page > 1 and _page_is_older_than_cutoff(page_sources, cutoff):
            break
        for source in page_sources:
            key = source.get("source_url") or source.get("external_id") or source.get("title")
            if key in seen_keys:
                continue
            seen_keys.add(key)
            sources.append(source)
            if len(sources) >= limit:
                return sources
    return sources


def _fetch_with_browser_fallback(fetch, browser_fetch, url_for_page, parser, *, limit: int, max_pages: int, cutoff: datetime | None = None) -> list[dict]:
    try:
        sources = _fetch_paginated(fetch, url_for_page, parser, limit=limit, max_pages=max_pages, cutoff=cutoff)
        if sources or browser_fetch is fetch:
            return sources
    except Exception:
        if browser_fetch is fetch:
            raise
    return _fetch_paginated(browser_fetch, url_for_page, parser, limit=limit, max_pages=max_pages, cutoff=cutoff)


def _page_is_older_than_cutoff(sources: list[dict], cutoff: datetime | None) -> bool:
    if not cutoff or not sources:
        return False
    published_values = [source.get("published_at") for source in sources if source.get("published_at")]
    if not published_values:
        return False
    return all(
        datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc) < cutoff
        for value in published_values
    )


def _binance_url(page: int) -> str:
    return (
        "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query"
        f"?catalogId=48&pageNo={page}&pageSize=30"
    )


def _binance_detail_url(article_code: str) -> str:
    return f"https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query?articleCode={article_code}"


def _okx_url(page: int) -> str:
    if page == 1:
        return OKX_LIST_URL
    return f"{OKX_LIST_URL}/page/{page}"


def _bybit_url(page: int) -> str:
    return f"https://announcements.bybit.com/en/?category=new_crypto&page={page}"


def _kucoin_url(page: int) -> str:
    if page == 1:
        return KUCOIN_LIST_URL
    return f"{KUCOIN_LIST_URL}/page/{page}"


def _mexc_url(page: int) -> str:
    if page == 1:
        return MEXC_LIST_URL
    return f"{MEXC_LIST_URL}/spot-18?page={page}"


def _upbit_url(page: int) -> str:
    if page == 1:
        return UPBIT_LIST_URL
    return f"{UPBIT_LIST_URL}?page={page}"


def _bithumb_url(page: int) -> str:
    if page == 1:
        return BITHUMB_LIST_URL
    return f"{BITHUMB_LIST_URL}?page={page}"


def _gate_url(page: int) -> str:
    if page == 1:
        return GATE_LIST_URL
    return f"{GATE_LIST_URL}?page={page}"


def _bitget_url(page: int) -> str:
    if page == 1:
        return BITGET_LIST_URL
    return f"{BITGET_LIST_URL}?page={page}"


def _lookback_cutoff(months: int, now: datetime | None = None) -> datetime:
    capped_months = max(1, min(int(months or 1), 3))
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return _subtract_months(reference.astimezone(timezone.utc), capped_months)


def _subtract_months(value: datetime, months: int) -> datetime:
    month = value.month - months
    year = value.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(value.day, _days_in_month(year, month))
    return value.replace(year=year, month=month, day=day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    this_month = datetime(year, month, 1, tzinfo=timezone.utc)
    return (next_month - this_month).days


def _filter_recent_sources(sources: list[dict], cutoff: datetime, limit: int) -> list[dict]:
    filtered = []
    for source in sources:
        published_at = source.get("published_at")
        if published_at:
            published = datetime.fromisoformat(published_at.replace("Z", "+00:00")).astimezone(timezone.utc)
            if published < cutoff:
                continue
        filtered.append(source)
        if len(filtered) >= limit:
            break
    return filtered


def _extract_binance_article_body_text(body) -> str:
    if not body:
        return ""
    if isinstance(body, str):
        stripped = body.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return _extract_binance_article_body_text(json.loads(stripped))
            except json.JSONDecodeError:
                pass
        return _strip_html(stripped)
    if isinstance(body, dict):
        parts = []
        if body.get("text"):
            parts.append(str(body["text"]))
        for child in body.get("child", []) or []:
            child_text = _extract_binance_article_body_text(child)
            if child_text:
                parts.append(child_text)
        return _collapse_space(" ".join(parts))
    if isinstance(body, list):
        return _collapse_space(" ".join(_extract_binance_article_body_text(item) for item in body))
    return ""


def _source(
    exchange: str,
    source_url: str | None,
    title: str,
    raw_text: str,
    *,
    published_at: str | None = None,
    external_id: str | None = None,
    raw_payload=None,
    detection_reason="live_adapter",
) -> dict:
    return {
        "exchange": exchange,
        "source_type": "exchange_announcement",
        "source_url": source_url,
        "title": _collapse_space(title),
        "raw_text": _collapse_space(raw_text),
        "published_at": published_at,
        "fetched_at": _utc_now(),
        "external_id": external_id,
        "raw_payload_json": raw_payload,
        "detection_reason": detection_reason,
    }


def _looks_like_spot_listing_title(title: str) -> bool:
    lowered = title.lower()
    if any(
        blocked in lowered
        for blocked in (
            "futures",
            "perpetual",
            "margin",
            "copy trade",
            "pre-market",
            "postpone",
            "合约",
            "杠杆",
            "盘前交易",
            "btc/u",
            "거래지원 종료",
            "입출금",
        )
    ):
        return False
    return any(
        phrase in lowered
        for phrase in (
            "will list",
            "to list",
            "listed on",
            "spot trading",
            "spot listing",
            "new spot listing",
            "마켓 추가",
            "마켓추가",
            "디지털 자산 추가",
            "디지털자산추가",
            "将上线",
            "已上线",
            "上线",
            "上币",
            "新增",
            "现货交易",
        )
    )


def _looks_like_binance_listing_title(title: str) -> bool:
    return _looks_like_spot_listing_title(title) or _looks_like_binance_futures_listing_title(title)


def _looks_like_binance_futures_listing_title(title: str) -> bool:
    lowered = title.lower()
    return "binance" in lowered and any(
        phrase in lowered
        for phrase in (
            "futures will launch",
            "will launch",
            "perpetual contract",
            "usd-m perpetual",
            "coin-m perpetual",
            "usdt perpetual",
            "合约",
        )
    ) and any(derivative in lowered for derivative in ("futures", "perpetual", "contract", "合约"))


def _strip_upbit_category_prefix(title: str) -> str:
    title = _collapse_space(title)
    for prefix in ("거래", "입출금", "안내", "점검", "디지털 자산", "NFT", "이벤트"):
        if title.startswith(prefix) and len(title) > len(prefix):
            return title[len(prefix):].strip()
    return title


def _normalize_spaced_notice_text(title: str) -> str:
    title = _collapse_space(title)
    title = re.sub(
        r"(?:[가-힣]\s+){2,}[가-힣]",
        lambda match: "".join(match.group(0).split()),
        title,
    )
    title = re.sub(r"(?<=[A-Z0-9])\s+(?=[A-Z0-9])", "", title)
    title = re.sub(
        r"[\(（]\s*([A-Z0-9](?:\s*[A-Z0-9]){0,11})\s*[\)）]",
        lambda match: f"({''.join(match.group(1).split())})",
        title,
    )
    title = title.replace("마켓디지털자산추가", "마켓 디지털 자산 추가")
    title = re.sub(r"\s+\(", "(", title)
    return title


def _query_id(href: str) -> str:
    match = re.search(r"[?&]id=([^&]+)", href)
    return match.group(1) if match else href.rsplit("/", 1)[-1]


def _parse_bitget_anchor_sources(page_html: str, *, limit: int) -> list[dict]:
    sources = []
    seen = set()
    for title, href in _anchor_texts(page_html):
        if not href or "/support/articles/" not in href or not _looks_like_spot_listing_title(title):
            continue
        source_url = _absolute_url("https://www.bitget.com", href)
        if source_url in seen:
            continue
        seen.add(source_url)
        sources.append(
            _source(
                "bitget",
                source_url,
                title,
                title,
                external_id=href.rsplit("/", 1)[-1],
            )
        )
        if len(sources) >= limit:
            break
    return sources


def _x_status_contexts(page_html: str, handle: str) -> list[tuple[str, str]]:
    unescaped = html.unescape(page_html)
    pattern = re.compile(
        rf'(?:https://x\.com|https://twitter\.com|)\/{re.escape(handle)}\/status\/(?P<id>\d+)',
        re.IGNORECASE,
    )
    article_contexts = []
    for article in re.findall(r"<article\b[^>]*>.*?</article>", unescaped, flags=re.DOTALL | re.IGNORECASE):
        match = pattern.search(article)
        if match:
            article_contexts.append((match.group("id"), _strip_html(article)))
    if article_contexts:
        return article_contexts
    contexts = []
    for match in pattern.finditer(unescaped):
        start = max(0, match.start() - 1200)
        end = min(len(unescaped), match.end() + 2400)
        text = _strip_html(unescaped[start:end])
        contexts.append((match.group("id"), text))
    return contexts


def _coinbase_x_title(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", _collapse_space(text))
    for sentence in sentences:
        if "roadmap" in sentence.lower():
            return sentence[:180]
    return _collapse_space(text)[:180]


def _parse_coinbase_roadmap_blog_sources(page_html: str, *, limit: int) -> list[dict]:
    text = _strip_html(page_html)
    start = text.lower().find("assets on the")
    end = text.lower().find("this is not an exhaustive", start)
    if start == -1 or end == -1:
        return []
    roadmap_text = text[start:end]
    roadmap_text = re.sub(r"Contract address:\s*0x[a-fA-F0-9]{40}", "Contract address:", roadmap_text)
    roadmap_text = re.sub(
        r"\bAssets on the [A-Za-z0-9 -]+(?:network|tokens)\b(?:\s*\([^)]+\))?",
        " ",
        roadmap_text,
        flags=re.IGNORECASE,
    )
    pattern = re.compile(
        r"(?P<name>[A-Za-z0-9][A-Za-z0-9 .'&-]{0,80}?)\s*[\(（](?P<symbol>[A-Z0-9]{1,12})[\)）]\s*-\s*Contract address:",
        re.DOTALL,
    )
    sources = []
    seen = set()
    for match in pattern.finditer(roadmap_text):
        symbol = match.group("symbol").upper()
        if symbol in seen:
            continue
        seen.add(symbol)
        project_name = _collapse_space(match.group("name"))
        raw_text = f"{project_name} ({symbol}) has been added to the Coinbase listing roadmap."
        sources.append(
            {
                "exchange": "coinbase",
                "source_type": "official_blog",
                "source_url": COINBASE_TRANSPARENCY_URL,
                "title": f"Coinbase roadmap: {project_name} ({symbol})",
                "raw_text": raw_text,
                "published_at": None,
                "fetched_at": _utc_now(),
                "external_id": symbol,
                "raw_payload_json": None,
                "detection_reason": "coinbase_roadmap_blog",
                "project_name": project_name,
            }
        )
        if len(sources) >= limit:
            break
    return sources


def _anchor_texts(page_html: str) -> list[tuple[str, str | None]]:
    parser = _AnchorTextParser()
    parser.feed(page_html)
    return parser.items


class _AnchorTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
        self._in_anchor = False
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._in_anchor = True
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_endtag(self, tag):
        if tag == "a" and self._in_anchor:
            title = _collapse_space(" ".join(self._text))
            if title:
                self.items.append((title, self._href))
            self._in_anchor = False
            self._href = None
            self._text = []

    def handle_data(self, data):
        if self._in_anchor:
            self._text.append(data)


class _HeadingLinkParser(HTMLParser):
    def __init__(self, heading_tags):
        super().__init__()
        self.heading_tags = heading_tags
        self.items = []
        self._href_stack = []
        self._current_href = None
        self._current_tag = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self._href_stack.append(attrs.get("href"))
        if tag in self.heading_tags:
            self._current_tag = tag
            self._current_href = next((href for href in reversed(self._href_stack) if href), None)
            self._text = []

    def handle_endtag(self, tag):
        if tag == self._current_tag:
            title = _collapse_space(" ".join(self._text))
            if title:
                self.items.append((title, self._current_href))
            self._current_tag = None
            self._current_href = None
            self._text = []
        if tag == "a" and self._href_stack:
            self._href_stack.pop()

    def handle_data(self, data):
        if self._current_tag:
            self._text.append(data)


class _KrakenCardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
        self._current_tag = None
        self._text = []
        self._pending_project = None

    def handle_starttag(self, tag, attrs):
        if tag in {"h2", "h3"}:
            self._current_tag = tag
            self._text = []

    def handle_endtag(self, tag):
        if tag != self._current_tag:
            return
        text = _collapse_space(" ".join(self._text))
        if tag == "h2" and text:
            self._pending_project = text
        elif tag == "h3" and self._pending_project and re.fullmatch(r"[A-Z0-9]{2,12}", text):
            self.items.append((self._pending_project, text))
            self._pending_project = None
        self._current_tag = None
        self._text = []

    def handle_data(self, data):
        if self._current_tag:
            self._text.append(data)


def _next_data(page_html: str) -> dict:
    match = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', page_html)
    if not match:
        return {}
    return json.loads(html.unescape(match.group(1)))


def _split_okx_title(title: str) -> tuple[str, str | None]:
    article_title, _, date_text = title.partition(" Published on ")
    return _collapse_space(article_title), _parse_date_text(date_text.strip())


def _parse_date_text(date_text: str) -> str | None:
    if not date_text:
        return None
    parsed = datetime.strptime(date_text, "%b %d, %Y")
    return parsed.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_kucoin_published_at(text: str) -> str | None:
    match = re.search(r"\b(\d{2}/\d{2}/\d{4}),\s*(\d{2}:\d{2}:\d{2})\b", text)
    if not match:
        return None
    parsed = datetime.strptime(f"{match.group(1)} {match.group(2)}", "%m/%d/%Y %H:%M:%S")
    return parsed.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_from_epoch(value) -> str | None:
    if not value:
        return None
    return datetime.fromtimestamp(int(value), timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_iso(value: str | None) -> str | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _meta_content(page_html: str, name: str) -> str | None:
    for match in re.finditer(r"<meta\b[^>]*>", page_html, flags=re.IGNORECASE):
        tag = match.group(0)
        property_match = re.search(r"\b(?:property|name)=[\"']([^\"']+)[\"']", tag, flags=re.IGNORECASE)
        if not property_match or property_match.group(1) != name:
            continue
        content_match = re.search(r"\bcontent=[\"']([^\"']*)[\"']", tag, flags=re.IGNORECASE)
        if content_match:
            return html.unescape(content_match.group(1))
    return None


def _iso_from_datetime_text(value: str | None, *, tz_hours: int = 0) -> str | None:
    if not value:
        return None
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    offset = timezone.utc if tz_hours == 0 else timezone(timedelta(hours=tz_hours))
    return parsed.replace(tzinfo=offset).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_mexc_next_section_articles(page_html: str, *, limit: int) -> list[dict]:
    sources = []
    seen = set()
    pattern = re.compile(
        r'\{\\"id\\":(?P<id>\d+),.*?'
        r'\\"title\\":\\"(?P<title>(?:[^\\"]|\\.)*?)\\".*?'
        r'\\"displayTime\\":(?P<display_time>\d+).*?'
        r'\\"labelList\\":(?P<labels>\[.*?\])',
        re.DOTALL,
    )
    for match in pattern.finditer(page_html):
        article_id = match.group("id")
        title = _decode_json_string(match.group("title"))
        labels = match.group("labels").lower()
        if article_id in seen or '\\"name\\":\\"spot\\"' not in labels:
            continue
        if not _looks_like_spot_listing_title(title):
            continue
        seen.add(article_id)
        sources.append(
            _source(
                "mexc",
                f"https://www.mexc.com/announcements/article/first-in-market-{article_id}",
                title,
                title,
                published_at=_iso_from_epoch_ms(match.group("display_time")),
                external_id=_first_parenthesized_symbol(title) or article_id,
            )
        )
        if len(sources) >= limit:
            break
    return sources


def _decode_json_string(value: str) -> str:
    return json.loads(f'"{value}"')


def _iso_from_epoch_ms(value) -> str | None:
    if not value:
        return None
    return datetime.fromtimestamp(int(value) / 1000, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _absolute_url(base: str, href: str | None) -> str | None:
    if not href:
        return None
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return f"{base}{href}"


def _strip_html(value: str) -> str:
    return _collapse_space(re.sub(r"<[^>]+>", " ", html.unescape(value)))


def _collapse_space(value: str) -> str:
    return " ".join(str(value or "").split())


def _first_parenthesized_symbol(value: str) -> str | None:
    match = re.search(r"\(([A-Z0-9]{2,12})\)", value)
    return match.group(1) if match else None
