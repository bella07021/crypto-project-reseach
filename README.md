# Crypto Project Scoring

Local research dashboard for scoring crypto projects from an X handle and RootData link.

## Local run

```bash
python3 web_app.py --host 127.0.0.1 --port 8094
```

Open `http://127.0.0.1:8094/`.

## Local watcher on macOS

The local watcher processes pending project requests from GitHub and writes score results back to the dashboard.

Create the local env file first:

```bash
cp .env.watcher.example .env.watcher
```

Edit `.env.watcher` and set `GITHUB_TOKEN` to a fine-grained token with `Contents: Read and write` access to this repository. The real `.env.watcher` file is ignored by git.

Run once by hand:

```bash
launchd/run_request_watcher.sh
```

Install it as a macOS LaunchAgent:

```bash
mkdir -p logs
chmod +x launchd/run_request_watcher.sh
cp launchd/com.bella.crypto-score-watcher.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.bella.crypto-score-watcher.plist
launchctl kickstart -k gui/$(id -u)/com.bella.crypto-score-watcher
```

Check status and logs:

```bash
launchctl print gui/$(id -u)/com.bella.crypto-score-watcher
tail -f logs/request_watcher.out.log
tail -f logs/request_watcher.err.log
```

Stop or reinstall:

```bash
launchctl bootout gui/$(id -u)/com.bella.crypto-score-watcher
cp launchd/com.bella.crypto-score-watcher.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.bella.crypto-score-watcher.plist
```

## Local exchange listing sync on macOS

The local exchange listing sync writes listing signals into `data/exchange_listings.sqlite`.

Run backfill or incremental syncs by hand:

```bash
python3 exchange_listing_sync.py --mode backfill --months 3
python3 exchange_listing_sync.py --mode incremental
```

Install the daily incremental sync as a macOS LaunchAgent:

```bash
mkdir -p logs
cp launchd/com.bella.exchange-listing-sync.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.bella.exchange-listing-sync.plist
launchctl kickstart -k gui/$(id -u)/com.bella.exchange-listing-sync
```

Check status and logs:

```bash
launchctl print gui/$(id -u)/com.bella.exchange-listing-sync
tail -f logs/exchange_listing_sync.out.log
tail -f logs/exchange_listing_sync.err.log
```

Stop or reinstall:

```bash
launchctl bootout gui/$(id -u)/com.bella.exchange-listing-sync
cp launchd/com.bella.exchange-listing-sync.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.bella.exchange-listing-sync.plist
```

## Tests

```bash
python3 -m unittest discover -v
```

## Vercel

The Vercel entry point is `api/index.py`, with rewrites configured in `vercel.json`.

For persistent score history on Vercel, set these environment variables:

- `GITHUB_TOKEN`: a fine-grained token with `Contents: Read and write` for this repository.
- `GITHUB_REPO_OWNER`: defaults to `bella07021`.
- `GITHUB_REPO_NAME`: defaults to `crypto-project-reseach`.
- `GITHUB_BRANCH`: defaults to `main`.
- `GITHUB_HISTORY_PATH`: defaults to `data/project_scores.jsonl`.

When `GITHUB_TOKEN` is configured, the app stores score records in GitHub through the Contents API. Without it, Vercel can only write temporary files under `/tmp`.

CoinMarketCap market data is collected from the public Markets page. Local runs use the installed Chrome path. Vercel runs use serverless Chromium through `@sparticuz/chromium`; if the browser scrape fails, the app falls back to CoinMarketCap Pro API when `CMC_PRO_API_KEY` is configured, then RootData events.
