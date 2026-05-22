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

On Vercel, score history is written to `/tmp`, so it is ephemeral. CoinMarketCap web scraping uses local Chrome when available; in serverless environments it falls back to CoinMarketCap Pro API if `CMC_PRO_API_KEY` is configured, then RootData events.
