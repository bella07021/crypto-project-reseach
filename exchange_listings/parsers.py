import re
from datetime import datetime, timezone

from exchange_listings.models import (
    EVENT_FAMILY_FUTURES_LISTING,
    EVENT_FAMILY_SPOT_LISTING,
    LISTING_TYPE_FUTURES,
    LISTING_TYPE_PERPETUAL,
    LISTING_TYPE_SPOT,
    SOURCE_PRECEDENCE_ANNOUNCEMENT,
    SOURCE_PRECEDENCE_BLOG,
    SOURCE_PRECEDENCE_X,
    STATUS_ANNOUNCED,
    STATUS_TBD,
    STATUS_TRADING_SOON,
    STATUS_TRADING_STARTED,
    STATUS_UNKNOWN,
)


PARSER_VERSION = "exchange-listings-parser-v1"

_PAREN_SYMBOL_RE = re.compile(r"[\(（]([A-Z0-9]{1,12})[\)）]")
_CASH_SYMBOL_RE = re.compile(r"(?<![A-Za-z0-9])\$([A-Z][A-Z0-9]{1,11})\b")
_UTC_TIME_RE = re.compile(
    r"\b(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}(?::\d{2})?)\s*(?:\(\s*)?(?P<zone>Z|UTC)(?:\s*\))?",
    re.IGNORECASE,
)
_MONTH_TIME_RE = re.compile(
    r"\b(?P<month>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
    r"Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+"
    r"(?P<day>\d{1,2}),\s*(?P<year>\d{4}),\s*"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>AM|PM)?\s*UTC\b",
    re.IGNORECASE,
)
_TIME_ON_MONTH_RE = re.compile(
    r"\b(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>AM|PM)?\s+on\s+"
    r"(?P<month>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
    r"Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+"
    r"(?P<day>\d{1,2}),\s*(?P<year>\d{4})\s*(?:\(\s*)?UTC(?:\s*\))?",
    re.IGNORECASE,
)
_SIMPLE_LISTING_SYMBOL_RE = re.compile(
    r"\b(?i:will list|to list|listing of|will launch)\s+([A-Z][A-Z0-9]{1,11})(?:/[A-Z]{2,6})?\b",
)
_PAIR_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,11})/(USDT|USDC|USD|BTC|ETH|KRW|EUR|TRY|FDUSD)\b")
_CONCAT_PAIR_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,11})(USDT|USDC|USD|BTC|ETH|FDUSD)\b")


def parse_events(raw_source: dict, now: datetime | None = None) -> list[dict]:
    exchange = (raw_source.get("exchange") or "").lower()
    source_type = raw_source.get("source_type")
    text = _source_text(raw_source)

    if source_type == "official_x":
        return _parse_official_x(raw_source, exchange, text)

    if source_type not in {"exchange_announcement", "official_blog"}:
        return []
    if source_type == "official_blog" and exchange == "coinbase" and _looks_like_coinbase_roadmap(text):
        return [_event_for_symbol(raw_source, symbol, STATUS_TBD, "roadmap") for symbol in _extract_symbols(text)]
    if not _looks_like_announcement_listing_signal(text):
        return []

    is_futures_listing = _looks_like_futures_listing_signal(text)
    trading_start = _extract_time_by_context(
        text,
        _looks_like_futures_trading_time_context if is_futures_listing else _looks_like_trading_time_context,
    )
    deposit_start = _extract_time_by_context(text, _looks_like_deposit_time_context)
    withdrawal_start = _extract_time_by_context(text, _looks_like_withdrawal_time_context)
    pairs = _extract_pairs(text)
    status = _announcement_status(trading_start, now)
    return [
        _event_for_symbol(
            raw_source,
            symbol,
            status,
            "trading_start" if trading_start else "listing_announcement",
            trading_start_time=trading_start,
            deposit_start_time=deposit_start,
            withdrawal_start_time=withdrawal_start,
            pairs=_pairs_for_symbol(pairs, symbol),
            listing_type=_listing_type_for_text(text),
            event_family=EVENT_FAMILY_FUTURES_LISTING if is_futures_listing else EVENT_FAMILY_SPOT_LISTING,
        )
        for symbol in _extract_symbols(text, include_concat_pairs=is_futures_listing)
    ]


def _parse_official_x(raw_source: dict, exchange: str, text: str) -> list[dict]:
    if exchange not in {"coinbase", "kraken"}:
        return []
    if not _looks_like_x_listing_signal(exchange, text):
        return []

    status = STATUS_UNKNOWN if exchange == "coinbase" and _looks_like_roadmap_removal(text) else STATUS_TBD
    return [_event_for_symbol(raw_source, symbol, status, _event_kind(exchange, text)) for symbol in _extract_symbols(text)]


def _source_text(raw_source: dict) -> str:
    return "\n".join(
        str(raw_source.get(field) or "")
        for field in ("title", "raw_text")
    )


def _looks_like_x_listing_signal(exchange: str, text: str) -> bool:
    lowered = text.lower()
    if exchange == "coinbase":
        return "roadmap" in lowered
    if exchange == "kraken":
        return "kraken" in lowered and any(keyword in lowered for keyword in ("listing", "coming", "trade", "spot"))
    return False


def _looks_like_coinbase_roadmap(text: str) -> bool:
    lowered = text.lower()
    return "coinbase" in lowered and "roadmap" in lowered


def _looks_like_roadmap_removal(text: str) -> bool:
    lowered = text.lower()
    return any(
        phrase in lowered
        for phrase in (
            "removed",
            "remove",
            "no longer planned",
            "will not list",
            "not planning to list",
        )
    )


def _looks_like_announcement_listing_signal(text: str) -> bool:
    lowered = text.lower()
    return any(
        keyword in lowered
        for keyword in (
            "will list",
            "will launch",
            "to list",
            "listing of",
            "listed on",
            "open trading",
            "spot trading",
            "start trading",
            "거래지원",
            "신규 거래",
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


def _looks_like_futures_listing_signal(text: str) -> bool:
    lowered = text.lower()
    return "binance" in lowered and any(
        phrase in lowered
        for phrase in (
            "binance futures",
            "perpetual contract",
            "usd-m perpetual",
            "coin-m perpetual",
            "usdt perpetual",
            "u-based perpetual",
            "will launch",
            "合约",
        )
    ) and any(derivative in lowered for derivative in ("futures", "perpetual", "contract", "合约"))


def _event_kind(exchange: str, text: str) -> str:
    if exchange == "coinbase" and "roadmap" in text.lower():
        return "roadmap"
    return "listing_announcement"


def _announcement_status(trading_start_time: str | None, now: datetime | None) -> str:
    if not trading_start_time:
        return STATUS_ANNOUNCED
    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    trading_start = datetime.fromisoformat(trading_start_time.replace("Z", "+00:00"))
    if trading_start > reference_time.astimezone(timezone.utc):
        return STATUS_TRADING_SOON
    return STATUS_TRADING_STARTED


def _extract_time_by_context(text: str, context_predicate) -> str | None:
    matches = []
    for match in _UTC_TIME_RE.finditer(text):
        matches.append((match.start(), _format_utc_match(match)))
    for match in _MONTH_TIME_RE.finditer(text):
        matches.append((match.start(), _format_month_time_match(match)))
    for match in _TIME_ON_MONTH_RE.finditer(text):
        matches.append((match.start(), _format_month_time_match(match)))
    for timestamp_start, timestamp in sorted(matches):
        context = _time_context_before(text, timestamp_start)
        if context_predicate(context):
            return timestamp
    return None


def _time_context_before(text: str, timestamp_start: int) -> str:
    sentence_start = max(text.rfind(".", 0, timestamp_start), text.rfind("\n", 0, timestamp_start))
    if sentence_start == -1:
        sentence_start = 0
    else:
        sentence_start += 1
    return text[sentence_start:timestamp_start]


def _looks_like_trading_time_context(context: str) -> bool:
    lowered = context.lower()
    trading_pos = _last_phrase_position(
        lowered,
        (
            "open trading",
            "trading opens",
            "trading will open",
            "trading starts",
            "trading will start",
            "spot trading",
            "start trading",
            "listing:",
            "trading:",
        ),
    )
    non_trading_pos = _last_phrase_position(
        lowered,
        (
            "deposit",
            "deposits",
            "withdrawal",
            "withdrawals",
        ),
    )
    return trading_pos >= 0 and trading_pos > non_trading_pos


def _looks_like_futures_trading_time_context(context: str) -> bool:
    lowered = context.lower()
    if not lowered.strip():
        return True
    return _looks_like_trading_time_context(context) or any(
        phrase in lowered
        for phrase in (
            "will launch",
            "launch",
            "at",
        )
    )


def _looks_like_deposit_time_context(context: str) -> bool:
    lowered = context.lower()
    return any(phrase in lowered for phrase in ("deposit", "deposits open", "deposit opens"))


def _looks_like_withdrawal_time_context(context: str) -> bool:
    lowered = context.lower()
    return any(phrase in lowered for phrase in ("withdrawal", "withdrawals open", "withdrawal opens"))


def _last_phrase_position(text: str, phrases: tuple[str, ...]) -> int:
    return max((text.rfind(phrase) for phrase in phrases), default=-1)


def _format_utc_match(match: re.Match) -> str:
    time_value = match.group("time")
    if time_value.count(":") == 1:
        time_value = f"{time_value}:00"
    parsed = datetime.fromisoformat(f"{match.group('date')}T{time_value}+00:00")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _format_month_time_match(match: re.Match) -> str:
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    ampm = (match.group("ampm") or "").upper()
    if ampm == "PM" and hour != 12:
        hour += 12
    elif ampm == "AM" and hour == 12:
        hour = 0
    value = f"{match.group('month')} {match.group('day')} {match.group('year')} {hour:02d}:{minute:02d}"
    for date_format in ("%B %d %Y %H:%M", "%b %d %Y %H:%M"):
        try:
            parsed = datetime.strptime(value, date_format).replace(tzinfo=timezone.utc)
            return parsed.isoformat().replace("+00:00", "Z")
        except ValueError:
            continue
    return ""


def _extract_symbols(text: str, *, include_concat_pairs: bool = False) -> list[str]:
    symbols = []
    for match in _PAREN_SYMBOL_RE.finditer(text):
        symbol = match.group(1).upper()
        if symbol not in {"UTC", "KST", "UTC8", "GMT"} and not symbol.isdigit():
            normalized = _normalize_concat_pair_symbol(symbol) if include_concat_pairs else symbol
            if not include_concat_pairs or not _is_quote_asset_symbol(normalized):
                symbols.append(normalized)
    for match in _CASH_SYMBOL_RE.finditer(text):
        symbol = match.group(1).upper()
        if symbol.isdigit():
            continue
        normalized = _normalize_concat_pair_symbol(symbol) if include_concat_pairs else symbol
        if not include_concat_pairs or not _is_quote_asset_symbol(normalized):
            symbols.append(normalized)
    for match in _SIMPLE_LISTING_SYMBOL_RE.finditer(text):
        symbol = match.group(1).upper()
        if symbol.isdigit():
            continue
        normalized = _normalize_concat_pair_symbol(symbol) if include_concat_pairs else symbol
        if not include_concat_pairs or not _is_quote_asset_symbol(normalized):
            symbols.append(normalized)
    if include_concat_pairs:
        for match in _CONCAT_PAIR_RE.finditer(text):
            symbols.append(match.group(1).upper())
    return list(dict.fromkeys(symbols))


def _extract_pairs(text: str) -> list[str] | None:
    pairs = [f"{match.group(1).upper()}/{match.group(2).upper()}" for match in _PAIR_RE.finditer(text)]
    unique_pairs = list(dict.fromkeys(pairs))
    return unique_pairs or None


def _pairs_for_symbol(pairs: list[str] | None, symbol: str) -> list[str] | None:
    if not pairs:
        return None
    matching_pairs = [pair for pair in pairs if pair.split("/", 1)[0] == symbol]
    return matching_pairs or None


def _normalize_concat_pair_symbol(symbol: str) -> str:
    for quote in ("FDUSD", "USDT", "USDC", "USD", "BTC", "ETH"):
        if symbol.endswith(quote) and len(symbol) > len(quote) + 1:
            return symbol[: -len(quote)]
    return symbol


def _is_quote_asset_symbol(symbol: str) -> bool:
    return symbol in {"FDUSD", "USDT", "USDC", "USD", "BTC", "ETH"}


def _listing_type_for_text(text: str) -> str:
    lowered = text.lower()
    if _looks_like_futures_listing_signal(text):
        if "perpetual" in lowered or "永续" in lowered:
            return LISTING_TYPE_PERPETUAL
        return LISTING_TYPE_FUTURES
    return LISTING_TYPE_SPOT


def _source_precedence(raw_source: dict) -> int:
    source_type = raw_source.get("source_type")
    if source_type == "exchange_announcement":
        return SOURCE_PRECEDENCE_ANNOUNCEMENT
    if source_type == "official_blog":
        return SOURCE_PRECEDENCE_BLOG
    return SOURCE_PRECEDENCE_X


def _confidence(raw_source: dict, trading_start_time: str | None = None) -> str:
    if raw_source.get("source_type") == "exchange_announcement":
        return "high" if trading_start_time else "medium"
    return "medium"


def _event_for_symbol(
    raw_source: dict,
    symbol: str,
    status: str,
    event_kind: str,
    trading_start_time: str | None = None,
    deposit_start_time: str | None = None,
    withdrawal_start_time: str | None = None,
    pairs: list[str] | None = None,
    listing_type: str = LISTING_TYPE_SPOT,
    event_family: str = EVENT_FAMILY_SPOT_LISTING,
) -> dict:
    return {
        "exchange": (raw_source["exchange"] or "").lower(),
        "project_name": raw_source.get("project_name"),
        "token_symbol": symbol,
        "listing_type": listing_type,
        "event_family": event_family,
        "event_kind": "futures_listing" if event_family == EVENT_FAMILY_FUTURES_LISTING else event_kind,
        "status": status,
        "announcement_url": raw_source.get("source_url"),
        "announcement_title": raw_source.get("title"),
        "announcement_published_at": raw_source.get("published_at"),
        "trading_start_time": trading_start_time,
        "deposit_start_time": deposit_start_time,
        "withdrawal_start_time": withdrawal_start_time,
        "pairs": pairs,
        "source_type": raw_source.get("source_type"),
        "confidence": _confidence(raw_source, trading_start_time),
        "source_precedence": _source_precedence(raw_source),
        "parser_version": PARSER_VERSION,
        "raw_source_id": raw_source.get("id"),
    }
