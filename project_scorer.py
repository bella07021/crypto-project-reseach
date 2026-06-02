from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping


WEIGHTS = {
    "team": 0.25,
    "funding": 0.20,
    "investor": 0.15,
    "social": 0.20,
    "chain": 0.05,
    "pre_tge_exchange": 0.15,
}

PURE_CHINESE_TEAM_MULTIPLIER = 0.3
MAX_FUNDING_USD = 500_000_000
FUNDING_RECENCY_DAYS = 365

TOP_INVESTOR_KEYWORDS = {
    "yzi labs",
    "binance labs",
    "coinbase ventures",
    "a16z",
    "andreessen horowitz",
    "paradigm",
    "polychain",
    "multicoin",
    "jump crypto",
    "dragonfly",
}

STRONG_INVESTOR_KEYWORDS = {
    "hashed",
    "spartan",
    "animoca",
    "delphi",
    "iosg",
    "okx ventures",
    "hashkey",
}

CHAIN_SCORE_RULES = [
    (100.0, ("base",)),
    (95.0, ("solana", " sol ", "sol生态")),
    (90.0, ("sui",)),
    (85.0, ("bnb chain", "bsc", "binance smart chain")),
    (80.0, ("ethereum", "eth", "zksync", "starknet")),
    (75.0, ("arbitrum", "optimism", "op mainnet", "polygon", "mantle", "linea", "scroll", "blast")),
    (65.0, ("avalanche", "avax", "aptos", "sei", "near", "cosmos", "ton")),
]


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def calculate_team_score(raw_score: float, background: str = "unknown") -> float:
    score = clamp(float(raw_score))
    normalized_background = (background or "unknown").strip().lower()
    if normalized_background in {"pure_chinese", "pure-chinese", "chinese", "cn"}:
        score *= PURE_CHINESE_TEAM_MULTIPLIER
    return round(score, 2)


def calculate_funding_score(
    amount_usd: float | None,
    funding_date: date | None,
    *,
    today: date | None = None,
) -> float:
    if not amount_usd or not funding_date:
        return 0.0

    today = today or date.today()
    amount_part = clamp(float(amount_usd) / MAX_FUNDING_USD, 0.0, 1.0) * 50
    days_since = max(0, (today - funding_date).days)
    recency_part = clamp(1 - days_since / FUNDING_RECENCY_DAYS, 0.0, 1.0) * 50
    return round(amount_part + recency_part, 2)


def parse_followers(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def calculate_social_percentile(
    benchmark_rows: Iterable[Mapping[str, object]],
    bucket: str,
    followers: int | float | str | None,
) -> float:
    follower_count = parse_followers(followers)
    if follower_count is None:
        return 0.0

    normalized_bucket = (bucket or "").strip().lower()
    peers = sorted(
        count
        for row in benchmark_rows
        if str(row.get("bucket", "")).strip().lower() == normalized_bucket
        for count in [parse_followers(row.get("x_followers"))]
        if count is not None
    )
    if len(peers) <= 1:
        return 100.0 if peers and follower_count >= peers[0] else 0.0

    lower_count = sum(1 for peer in peers if peer < follower_count)
    equal_count = sum(1 for peer in peers if peer == follower_count)
    midpoint_rank = lower_count + (equal_count - 1) / 2
    percentile = midpoint_rank / (len(peers) - 1) * 100
    return round(clamp(percentile), 2)


def calculate_investor_score(investors: Iterable[str] | str | None) -> float:
    if not investors:
        return 0.0
    if isinstance(investors, str):
        investor_values = [investors]
    else:
        investor_values = list(investors)
    haystack = " | ".join(str(value).lower() for value in investor_values)
    top_matches = sum(1 for keyword in TOP_INVESTOR_KEYWORDS if keyword in haystack)
    strong_matches = sum(1 for keyword in STRONG_INVESTOR_KEYWORDS if keyword in haystack)
    named_count = len([value for value in investor_values if str(value).strip()])

    if top_matches >= 2:
        return 100.0
    if top_matches == 1 and strong_matches:
        return 95.0
    if top_matches == 1:
        return 90.0
    if strong_matches >= 2:
        return 85.0
    if strong_matches == 1:
        return 70.0
    if named_count >= 3:
        return 55.0
    if named_count:
        return 45.0
    return 0.0


def calculate_chain_score(chains: Iterable[str] | str | None) -> float:
    if not chains:
        return 0.0
    if isinstance(chains, str):
        chain_values = [chains]
    else:
        chain_values = list(chains)
    best = 0.0
    for value in chain_values:
        text = f" {str(value).strip().lower()} "
        for score, keywords in CHAIN_SCORE_RULES:
            if any(keyword in text for keyword in keywords):
                best = max(best, score)
    return best or 35.0


def calculate_total_score(
    team_score: float,
    funding_score: float,
    social_score: float,
    investor_score: float = 0.0,
    chain_score: float = 0.0,
    pre_tge_exchange_score: float = 0.0,
) -> float:
    total = (
        clamp(float(team_score)) * WEIGHTS["team"]
        + clamp(float(funding_score)) * WEIGHTS["funding"]
        + clamp(float(investor_score)) * WEIGHTS["investor"]
        + clamp(float(social_score)) * WEIGHTS["social"]
        + clamp(float(chain_score)) * WEIGHTS["chain"]
        + clamp(float(pre_tge_exchange_score)) * WEIGHTS["pre_tge_exchange"]
    )
    return round(total, 2)
