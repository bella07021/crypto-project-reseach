# Crypto Project Scoring

Local research dashboard for scoring crypto projects from an X handle and RootData link.

## Local run

```bash
python3 web_app.py --host 127.0.0.1 --port 8094
```

Open `http://127.0.0.1:8094/`.

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
