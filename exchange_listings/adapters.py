import html
import json
import re
import subprocess
from datetime import datetime, timezone
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


class SourceUnavailable(RuntimeError):
    pass


def fetch_live_sources(
    exchange: str,
    *,
    mode="incremental",
    months=3,
    limit=5,
    fetch_text=None,
    now: datetime | None = None,
    max_pages=3,
) -> list[dict]:
    del mode
    fetch = fetch_text or _fetch_text
    exchange = exchange.lower()
    cutoff = _lookback_cutoff(months, now)
    if exchange == "binance":
        sources = _fetch_paginated(fetch, _binance_url, parse_binance_sources, limit=limit, max_pages=max_pages, cutoff=cutoff)
        return _filter_recent_sources(sources, cutoff, limit)
    if exchange == "okx":
        sources = _fetch_paginated(fetch, _okx_url, parse_okx_sources, limit=limit, max_pages=max_pages, cutoff=cutoff)
        return _filter_recent_sources(sources, cutoff, limit)
    if exchange == "bybit":
        sources = _fetch_paginated(fetch, _bybit_url, parse_bybit_sources, limit=limit, max_pages=max_pages, cutoff=cutoff)
        return _filter_recent_sources(sources, cutoff, limit)
    if exchange == "kucoin":
        sources = _fetch_paginated(fetch, _kucoin_url, parse_kucoin_sources, limit=limit, max_pages=max_pages, cutoff=cutoff)
        return _filter_recent_sources(sources, cutoff, limit)
    if exchange == "mexc":
        sources = _fetch_paginated(fetch, _mexc_url, parse_mexc_sources, limit=limit, max_pages=max_pages, cutoff=cutoff)
        return _filter_recent_sources(sources, cutoff, limit)
    if exchange == "kraken":
        return parse_kraken_sources(fetch(KRAKEN_LIST_URL), limit=limit)
    raise SourceUnavailable(f"{exchange} live source is blocked or not implemented yet")


def parse_binance_sources(payload: str, *, limit: int) -> list[dict]:
    data = json.loads(payload)
    articles = (data.get("data") or {}).get("articles") or []
    sources = []
    for article in articles:
        title = article.get("title") or ""
        if not _looks_like_spot_listing_title(title):
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
    if any(blocked in lowered for blocked in ("futures", "perpetual", "margin", "copy trade", "pre-market", "postpone")):
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
        )
    )


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
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', page_html)
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
