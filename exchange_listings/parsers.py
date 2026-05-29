import re
from datetime import datetime, timezone

from exchange_listings.models import (
    EVENT_FAMILY_SPOT_LISTING,
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

_PAREN_SYMBOL_RE = re.compile(r"\(([A-Z0-9]{2,12})\)")
_CASH_SYMBOL_RE = re.compile(r"(?<![A-Za-z0-9])\$([A-Z][A-Z0-9]{1,11})\b")
_UTC_TIME_RE = re.compile(
    r"\b(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}(?::\d{2})?)\s*(?P<zone>Z|UTC)\b",
    re.IGNORECASE,
)
_SIMPLE_LISTING_SYMBOL_RE = re.compile(
    r"\b(?i:will list|to list|listing of)\s+([A-Z][A-Z0-9]{1,11})\b",
)
_PAIR_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,11})/(USDT|USDC|USD|BTC|ETH|KRW|EUR|TRY|FDUSD)\b")


def parse_events(raw_source: dict, now: datetime | None = None) -> list[dict]:
    exchange = (raw_source.get("exchange") or "").lower()
    source_type = raw_source.get("source_type")
    text = _source_text(raw_source)

    if source_type == "official_x":
        return _parse_official_x(raw_source, exchange, text)

    if source_type not in {"exchange_announcement", "official_blog"}:
        return []
    if not _looks_like_announcement_listing_signal(text):
        return []

    trading_start = _extract_time_by_context(text, _looks_like_trading_time_context)
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
            pairs=pairs,
        )
        for symbol in _extract_symbols(text)
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
            "to list",
            "listing of",
            "open trading",
            "spot trading",
            "start trading",
            "거래지원",
            "신규 거래",
        )
    )


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
    for match in _UTC_TIME_RE.finditer(text):
        context = _time_context_before(text, match.start())
        if context_predicate(context):
            return _format_utc_match(match)
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
    return any(
        phrase in lowered
        for phrase in (
            "open trading",
            "trading opens",
            "trading will open",
            "trading starts",
            "trading will start",
            "spot trading",
            "start trading",
        )
    )


def _looks_like_deposit_time_context(context: str) -> bool:
    lowered = context.lower()
    return any(phrase in lowered for phrase in ("deposit", "deposits open", "deposit opens"))


def _looks_like_withdrawal_time_context(context: str) -> bool:
    lowered = context.lower()
    return any(phrase in lowered for phrase in ("withdrawal", "withdrawals open", "withdrawal opens"))


def _format_utc_match(match: re.Match) -> str:
    time_value = match.group("time")
    if time_value.count(":") == 1:
        time_value = f"{time_value}:00"
    parsed = datetime.fromisoformat(f"{match.group('date')}T{time_value}+00:00")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _extract_symbols(text: str) -> list[str]:
    symbols = []
    for match in _PAREN_SYMBOL_RE.finditer(text):
        symbols.append(match.group(1).upper())
    for match in _CASH_SYMBOL_RE.finditer(text):
        symbols.append(match.group(1).upper())
    for match in _SIMPLE_LISTING_SYMBOL_RE.finditer(text):
        symbols.append(match.group(1).upper())
    return list(dict.fromkeys(symbols))


def _extract_pairs(text: str) -> list[str] | None:
    pairs = [f"{match.group(1).upper()}/{match.group(2).upper()}" for match in _PAIR_RE.finditer(text)]
    unique_pairs = list(dict.fromkeys(pairs))
    return unique_pairs or None


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
) -> dict:
    return {
        "exchange": (raw_source["exchange"] or "").lower(),
        "project_name": raw_source.get("project_name"),
        "token_symbol": symbol,
        "listing_type": LISTING_TYPE_SPOT,
        "event_family": EVENT_FAMILY_SPOT_LISTING,
        "event_kind": event_kind,
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
