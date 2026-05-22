# Crypto Project Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local scoring CLI that calculates crypto project scores, records non-scoring TGE/listing signals, writes an Excel workbook, and prints JSON for Hermes integration.

**Architecture:** Keep scoring logic pure and testable in `project_scorer.py`. Keep CLI parsing and workbook persistence in `score_project.py`. Use Python standard library only, including a small `.xlsx` OOXML writer, so Telegram/Hermes usage does not depend on local package installs.

**Tech Stack:** Python 3.11 standard library, `unittest`, CSV benchmark input from `output/rootdata_projects_x_enriched_fullv2.csv`, generated XLSX workbook.

---

### Task 1: Pure Scoring Logic

**Files:**
- Create: `project_scorer.py`
- Create: `tests/test_project_scorer.py`

- [ ] **Step 1: Write failing tests**

```python
import unittest
from datetime import date

from project_scorer import (
    calculate_funding_score,
    calculate_social_percentile,
    calculate_team_score,
    calculate_total_score,
)


class ProjectScorerTests(unittest.TestCase):
    def test_team_score_applies_pure_chinese_discount(self):
        self.assertEqual(calculate_team_score(90, "pure_chinese"), 27)

    def test_funding_score_requires_size_and_recency_for_full_score(self):
        score = calculate_funding_score(250_000_000, date(2025, 11, 22), today=date(2026, 5, 22))
        self.assertAlmostEqual(score, 75.0, places=2)

    def test_social_percentile_uses_same_bucket_followers(self):
        rows = [
            {"bucket": "infra", "x_followers": "100"},
            {"bucket": "infra", "x_followers": "300"},
            {"bucket": "infra", "x_followers": "500"},
            {"bucket": "defi", "x_followers": "10000"},
        ]
        self.assertEqual(calculate_social_percentile(rows, "infra", 300), 50.0)

    def test_tge_signals_do_not_affect_total_score(self):
        total = calculate_total_score(team_score=80, funding_score=70, social_score=60)
        self.assertEqual(total, 69.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_project_scorer.py -v`
Expected: FAIL because `project_scorer` does not exist.

- [ ] **Step 3: Implement minimal scoring logic**

Create `project_scorer.py` with constants for weights, clamped funding score, team discount, percentile calculation, and total score.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_project_scorer.py -v`
Expected: PASS.

### Task 2: CLI and Workbook Writer

**Files:**
- Create: `score_project.py`
- Modify: `tests/test_project_scorer.py`

- [ ] **Step 1: Write failing integration tests**

Add tests that run the CLI in a temporary directory with a tiny benchmark CSV, assert JSON output includes `total_score`, and assert an `.xlsx` file is created.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_project_scorer.py -v`
Expected: FAIL because `score_project.py` does not exist.

- [ ] **Step 3: Implement CLI and standard-library XLSX writer**

Create `score_project.py` with argparse parsing, benchmark loading, social lookup, score calculation, JSON stdout, and workbook sheet writing.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_project_scorer.py -v`
Expected: PASS.

### Task 3: Smoke Test With Real Benchmark File

**Files:**
- Generated: `output/crypto_project_scores.xlsx`

- [ ] **Step 1: Run CLI against existing RootData/X benchmark**

Run: `python3 score_project.py --x-handle SuiNetwork --rootdata-url "https://cn.rootdata.com/Projects/detail/Sui?k=Mjc5Nw%3D%3D" --team-raw-score 85 --team-background international --funding-amount-usd 300000000 --funding-date 2025-12-01 --bucket infra --tge-signal tokenomics`

- [ ] **Step 2: Verify workbook and JSON**

Expected: command prints JSON with score fields and creates `output/crypto_project_scores.xlsx`.

- [ ] **Step 3: Run full tests again**

Run: `python3 -m unittest discover -v`
Expected: PASS.
