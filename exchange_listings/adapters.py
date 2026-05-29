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


def fetch_live_sources(exchange: str, *, mode="incremental", months=3, limit=5, fetch_text=None) -> list[dict]:
    del mode, months
    fetch = fetch_text or _fetch_text
    exchange = exchange.lower()
    if exchange == "binance":
        return parse_binance_sources(fetch(BINANCE_LIST_URL), limit=limit)
    if exchange == "okx":
        return parse_okx_sources(fetch(OKX_LIST_URL), limit=limit)
    if exchange == "bybit":
        return parse_bybit_sources(fetch(BYBIT_LIST_URL), limit=limit)
    if exchange == "kucoin":
        return parse_kucoin_sources(fetch(KUCOIN_LIST_URL), limit=limit)
    if exchange == "mexc":
        return parse_mexc_sources(fetch(MEXC_LIST_URL), limit=limit)
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
    parser = _HeadingLinkParser({"h3"})
    parser.feed(page_html)
    sources = []
    for title, href in parser.items:
        if not _looks_like_spot_listing_title(title):
            continue
        sources.append(
            _source(
                "kucoin",
                _absolute_url("https://www.kucoin.com", href),
                title,
                title,
                external_id=(href or title).rsplit("/", 1)[-1],
            )
        )
        if len(sources) >= limit:
            break
    return sources


def parse_mexc_sources(page_html: str, *, limit: int) -> list[dict]:
    unescaped = html.unescape(page_html)
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
        raw_text = f"Kraken will list {project_name} ({symbol}) for spot trading."
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


def _iso_from_epoch(value) -> str | None:
    if not value:
        return None
    return datetime.fromtimestamp(int(value), timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_iso(value: str | None) -> str | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
