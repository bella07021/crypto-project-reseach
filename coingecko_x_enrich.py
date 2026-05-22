#!/usr/bin/env python3
import csv
import json
import re
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote


ROOT = Path.cwd()
INPUT_PATH = ROOT / "output" / "rootdata_category_project_lists.json"
RUN_SUFFIX = __import__("os").environ.get("RUN_SUFFIX", "").strip()
SUFFIX = f"_{RUN_SUFFIX}" if RUN_SUFFIX else ""
STATE_DIR = ROOT / "output" / f"coingecko_state{SUFFIX}"
STATE_DIR.mkdir(parents=True, exist_ok=True)
TEST_LIMIT = int(__import__("os").environ.get("TEST_LIMIT", "0"))
RESET_STATE = __import__("os").environ.get("RESET_STATE", "") == "1"

SELECTED_JSON = ROOT / "output" / f"rootdata_selected_projects{SUFFIX}.json"
ENRICHED_JSON = ROOT / "output" / f"rootdata_projects_x_enriched{SUFFIX}.json"
DETAIL_CSV = ROOT / "output" / f"rootdata_projects_x_enriched{SUFFIX}.csv"
SUMMARY_CSV = ROOT / "output" / f"rootdata_projects_x_summary{SUFFIX}.csv"

COINS_LIST_CACHE = STATE_DIR / "coingecko_coins_list.json"
MATCH_CACHE = STATE_DIR / "match_cache.json"
DETAIL_CACHE = STATE_DIR / "detail_cache.json"
FOLLOWER_CACHE = STATE_DIR / "follower_cache.json"
PROGRESS_CACHE = STATE_DIR / "progress.json"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
def _open_with_retry(url: str, *, sleep_s: float = 0.0, is_json: bool = False):
    if sleep_s:
        time.sleep(sleep_s)
    last_error = None
    for attempt in range(6):
        try:
            proc = subprocess.run(
                ["curl", "-s", "-L", "--max-time", "45", "-A", USER_AGENT, url],
                capture_output=True,
                text=True,
                check=False,
            )
            body = proc.stdout
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or f"curl failed: {proc.returncode}")
            if "Too Many Requests" in body or '"status":{"error_code":429' in body:
                wait = min(20 * (attempt + 1), 120)
                time.sleep(wait)
                continue
            return json.loads(body) if is_json else body
        except Exception as exc:
            last_error = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"request failed after retries: {url}: {last_error}")


def fetch_json(url: str, *, sleep_s: float = 0.0) -> object:
    return _open_with_retry(url, sleep_s=sleep_s, is_json=True)


def fetch_text(url: str, *, sleep_s: float = 0.0) -> str:
    return _open_with_retry(url, sleep_s=sleep_s, is_json=False)


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text("utf-8"))
    return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def normalize(text: str) -> str:
    text = (text or "").lower().strip()
    text = text.replace("&", "and")
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def choose_selected_rows(rows: List[dict]) -> List[dict]:
    simple_order = ["infra", "defi", "nft", "gamefi", "cefi"]
    selected: List[dict] = []
    for bucket in simple_order:
        bucket_rows = [r for r in rows if r["bucket"] == bucket][:500]
        selected.extend(bucket_rows)

    dao_rows = [r for r in rows if r["bucket"] == "dao"]
    selected.extend(dao_rows[:500])

    def merge_bucket(bucket: str, tag_order: List[str], limit: int = 500) -> List[dict]:
        seen = set()
        out = []
        for tag in tag_order:
            for row in rows:
                if row["bucket"] != bucket or row["rootdata_tag"] != tag:
                    continue
                key = row["project_url"]
                if key in seen:
                    continue
                seen.add(key)
                out.append(row)
                if len(out) >= limit:
                    return out
        return out

    selected.extend(merge_bucket("tools&information", ["工具", "数据&分析"], 500))
    selected.extend(merge_bucket("social&entertainment", ["社交", "创作者经济"], 500))
    return selected


def build_maps(coins: List[dict]) -> Tuple[Dict[str, List[dict]], Dict[str, List[dict]]]:
    by_name: Dict[str, List[dict]] = defaultdict(list)
    by_symbol: Dict[str, List[dict]] = defaultdict(list)
    for coin in coins:
        by_name[normalize(coin.get("name", ""))].append(coin)
        by_symbol[normalize(coin.get("symbol", ""))].append(coin)
    return by_name, by_symbol


def rank_coin(coin: dict) -> int:
    rank = coin.get("market_cap_rank")
    if isinstance(rank, int):
        return rank
    return 10**9


def choose_coin_from_candidates(row: dict, candidates: List[dict]) -> Optional[dict]:
    if not candidates:
        return None
    symbol_norm = normalize(row.get("token_symbol", ""))
    name_norm = normalize(row.get("project_name", ""))
    exact_name = [c for c in candidates if normalize(c.get("name", "")) == name_norm]
    if symbol_norm:
        exact_symbol = [c for c in exact_name if normalize(c.get("symbol", "")) == symbol_norm]
        if exact_symbol:
            return sorted(exact_symbol, key=rank_coin)[0]
    if exact_name:
        return sorted(exact_name, key=rank_coin)[0]
    # Avoid generic one- or two-letter symbol collisions unless the name also matches.
    symbol_match = [
        c for c in candidates
        if len(symbol_norm) >= 3 and normalize(c.get("symbol", "")) == symbol_norm
    ]
    if symbol_match:
        return sorted(symbol_match, key=rank_coin)[0]
    return None


def search_coingecko(query: str) -> List[dict]:
    data = fetch_json(f"https://api.coingecko.com/api/v3/search?query={quote(query)}", sleep_s=2.2)
    return data.get("coins", [])[:10]


def resolve_match(row: dict, by_name: Dict[str, List[dict]], by_symbol: Dict[str, List[dict]], match_cache: dict) -> Optional[dict]:
    cache_key = f"{row['project_name']}|{row.get('token_symbol','')}"
    if cache_key in match_cache:
        return match_cache[cache_key]

    name_norm = normalize(row["project_name"])
    symbol_norm = normalize(row.get("token_symbol", ""))

    candidate_pool = list(by_name.get(name_norm, []))
    if symbol_norm:
        candidate_pool.extend(by_symbol.get(symbol_norm, []))

    chosen = choose_coin_from_candidates(row, candidate_pool)
    if not chosen:
        search_candidates = search_coingecko(row["project_name"])
        chosen = choose_coin_from_candidates(row, search_candidates)

    match_cache[cache_key] = chosen or None
    return chosen


def fetch_coin_detail(coin_id: str, detail_cache: dict) -> dict:
    if coin_id in detail_cache:
        return detail_cache[coin_id]
    url = (
        f"https://api.coingecko.com/api/v3/coins/{quote(coin_id)}"
        "?localization=false&tickers=false&market_data=false&community_data=false&developer_data=false&sparkline=false"
    )
    data = fetch_json(url, sleep_s=2.2)
    detail_cache[coin_id] = data
    return data


def fetch_x_followers(handle: str, follower_cache: dict) -> Optional[int]:
    handle = handle.lstrip("@")
    if not handle:
        return None
    if handle in follower_cache:
        return follower_cache[handle]
    html = fetch_text(f"https://x.com/{quote(handle)}", sleep_s=1.2)
    matches = [int(x) for x in re.findall(r'"followers_count"\s*:\s*(\d+)', html)]
    followers = max(matches) if matches else None
    follower_cache[handle] = followers
    return followers


def write_csv(path: Path, rows: List[dict], fieldnames: List[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    if RESET_STATE:
        for path in [MATCH_CACHE, DETAIL_CACHE, FOLLOWER_CACHE, PROGRESS_CACHE, ENRICHED_JSON, DETAIL_CSV, SUMMARY_CSV]:
            if path.exists():
                path.unlink()

    root_rows = load_json(INPUT_PATH, [])
    if not root_rows:
        print("Missing rootdata input file", file=sys.stderr)
        return 1

    selected_rows = choose_selected_rows(root_rows)
    if TEST_LIMIT > 0:
        selected_rows = selected_rows[:TEST_LIMIT]
    save_json(SELECTED_JSON, selected_rows)

    coins = load_json(COINS_LIST_CACHE, None)
    if coins is None:
        coins = fetch_json("https://api.coingecko.com/api/v3/coins/list", sleep_s=0.0)
        save_json(COINS_LIST_CACHE, coins)
    by_name, by_symbol = build_maps(coins)

    match_cache = load_json(MATCH_CACHE, {})
    detail_cache = load_json(DETAIL_CACHE, {})
    follower_cache = load_json(FOLLOWER_CACHE, {})
    progress = load_json(PROGRESS_CACHE, {"done": 0})
    enriched = load_json(ENRICHED_JSON, [])

    start = progress.get("done", 0)
    processed = {row["project_url"] for row in enriched}
    if start == 0 and enriched:
        start = len(enriched)

    for idx, row in enumerate(selected_rows[start:], start=start):
        if row["project_url"] in processed:
            continue

        match = resolve_match(row, by_name, by_symbol, match_cache)
        cg_id = match.get("id") if match else ""
        cg_name = match.get("name") if match else ""
        cg_symbol = match.get("symbol") if match else ""

        website = ""
        x_handle = ""
        x_url = ""
        x_followers = None
        match_method = "no_match"

        if match:
            match_method = "search_or_exact"
            detail = fetch_coin_detail(cg_id, detail_cache)
            links = detail.get("links", {})
            homepage = links.get("homepage", [])
            if homepage and homepage[0]:
                website = homepage[0]
            x_handle = links.get("twitter_screen_name", "") or ""
            if x_handle:
                x_url = f"https://x.com/{x_handle}"
                try:
                    x_followers = fetch_x_followers(x_handle, follower_cache)
                except Exception:
                    x_followers = None

        enriched.append(
            {
                **row,
                "coingecko_id": cg_id,
                "coingecko_name": cg_name,
                "coingecko_symbol": cg_symbol,
                "website": website,
                "x_handle": x_handle,
                "x_url": x_url,
                "x_followers": x_followers if x_followers is not None else "",
                "match_method": match_method,
            }
        )
        progress["done"] = idx + 1

        if (idx + 1) % 5 == 0 or idx + 1 == len(selected_rows):
            save_json(MATCH_CACHE, match_cache)
            save_json(DETAIL_CACHE, detail_cache)
            save_json(FOLLOWER_CACHE, follower_cache)
            save_json(PROGRESS_CACHE, progress)
            save_json(ENRICHED_JSON, enriched)
            with_x = sum(1 for r in enriched if r.get("x_handle"))
            with_followers = sum(1 for r in enriched if r.get("x_followers") not in ("", None))
            print(
                json.dumps(
                    {
                        "processed": idx + 1,
                        "total": len(selected_rows),
                        "with_x_handle": with_x,
                        "with_followers": with_followers,
                        "last_project": row["project_name"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    detail_rows = enriched
    write_csv(
        DETAIL_CSV,
        detail_rows,
        [
            "bucket",
            "rootdata_tag",
            "bucket_rank",
            "project_name",
            "token_symbol",
            "rootdata_subtags",
            "ecosystem",
            "description",
            "rd_growth_index",
            "rd_heat_index",
            "project_url",
            "coingecko_id",
            "coingecko_name",
            "coingecko_symbol",
            "website",
            "x_handle",
            "x_url",
            "x_followers",
            "match_method",
        ],
    )

    summary_rows = []
    for bucket in ["infra", "defi", "nft", "gamefi", "cefi", "dao", "tools&information", "social&entertainment", "others"]:
        bucket_rows = [r for r in detail_rows if r["bucket"] == bucket]
        vals = sorted(int(r["x_followers"]) for r in bucket_rows if str(r.get("x_followers", "")).isdigit())
        summary_rows.append(
            {
                "bucket": bucket,
                "projects_considered": len(bucket_rows),
                "projects_with_x_handle": sum(1 for r in bucket_rows if r.get("x_handle")),
                "projects_with_followers": len(vals),
                "min_followers": vals[0] if vals else "",
                "max_followers": vals[-1] if vals else "",
                "median_followers": int(statistics.median(vals)) if vals else "",
            }
        )
    write_csv(
        SUMMARY_CSV,
        summary_rows,
        [
            "bucket",
            "projects_considered",
            "projects_with_x_handle",
            "projects_with_followers",
            "min_followers",
            "max_followers",
            "median_followers",
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
