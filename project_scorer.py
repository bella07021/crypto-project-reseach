from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping


WEIGHTS = {
    "team": 0.30,
    "funding": 0.40,
    "social": 0.30,
}

PURE_CHINESE_TEAM_MULTIPLIER = 0.3
MAX_FUNDING_USD = 500_000_000
FUNDING_RECENCY_DAYS = 365


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


def calculate_total_score(team_score: float, funding_score: float, social_score: float) -> float:
    total = (
        clamp(float(team_score)) * WEIGHTS["team"]
        + clamp(float(funding_score)) * WEIGHTS["funding"]
        + clamp(float(social_score)) * WEIGHTS["social"]
    )
    return round(total, 2)
