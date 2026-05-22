# Crypto Project Scoring Design

## Goal

Build a local automation script that scores crypto projects from a Twitter/X handle and RootData URL, stores the scoring history in an Excel workbook, and returns machine-readable JSON for later Telegram Hermes integration.

## Scoring Model

The score has three weighted components:

- Team: 30%
- Funding: 40%
- Social: 30%

TGE probability is not included in the score. It is recorded as independent signals for research context.

## Team Scoring

Team scoring accepts a raw score from 0 to 100 plus a background classification. If the team is classified as pure Chinese background, the raw score is multiplied by 0.3. The script stores both the adjusted score and a confidence/note field so manual evidence can be audited later.

## Funding Scoring

Funding uses the formula:

```text
Score = (Amount / 500M) * 50 + (1 - TimeDiff / 1 Year) * 50
```

Each part is clamped to 0-50. A project only reaches 100 when the relevant funding amount is at least 500M USD and the latest round is within one year.

## Social Scoring

Social score is computed from the project's follower percentile within the same RootData bucket using `output/rootdata_projects_x_enriched_fullv2.csv` as the benchmark input. The project is matched by X handle when possible and RootData URL/name as fallback context.

## TGE Signals

The script records TGE signals without affecting score:

- Old previous round followed by recent new round
- Official X explains tokenomics
- Points, season, airdrop, or similar activity
- IDO, launchpad, or sale event

## Workbook Output

The workbook is saved to `output/crypto_project_scores.xlsx` and includes:

- `Scores`: one latest score row per project assessment run
- `Evidence`: evidence and notes per project
- `Signals`: TGE/listing/roadmap signals
- `Social Benchmarks`: RootData/X benchmark rows used for percentile context
- `Config`: weights and key scoring thresholds

## CLI Contract

The script runs as:

```bash
python3 score_project.py --x-handle HANDLE --rootdata-url URL
```

Optional inputs allow manual scoring evidence:

```bash
--team-raw-score 80
--team-background international
--funding-amount-usd 120000000
--funding-date 2026-03-01
--bucket infra
--tge-signal tokenomics
```

The command updates the workbook and prints compact JSON for Hermes.
