# Team LinkedIn Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quantify team background as foreign/Chinese/unknown counts and use LinkedIn locations when available.

**Architecture:** RootData parsing extracts structured team members with name, LinkedIn, and X links. A local-only enrichment pass optionally fetches LinkedIn public pages within a 120-second project budget, classifies each member location, and stores summary fields on the assessment for scoring and display.

**Tech Stack:** Python stdlib parsing and HTTP fetch, existing RootData parser, existing scoring output and vanilla JS frontend.

---

### Task 1: Structured Team Member Parsing

**Files:**
- Modify: `live_project_fetcher.py`
- Test: `tests/test_project_scorer.py`

- [x] Add `team_members`, `team_foreign_count`, `team_chinese_count`, `team_unknown_count`, `team_known_location_count`, and `team_region_summary` fields.
- [x] Parse RootData team member names and profile URLs.
- [x] Classify obvious names/locations without LinkedIn fetch.

### Task 2: LinkedIn Local Enrichment

**Files:**
- Modify: `live_project_fetcher.py`
- Test: `tests/test_project_scorer.py`

- [x] Add local-only LinkedIn fetch with a 120-second total budget.
- [x] Extract likely location text from LinkedIn HTML.
- [x] Ensure Vercel/cloud scoring skips LinkedIn fetch.

### Task 3: Output And UI

**Files:**
- Modify: `score_project.py`
- Modify: `web/app.js`
- Test: `tests/test_project_scorer.py`

- [x] Include team region counts and summary in assessment JSON.
- [x] Show `international, 8/10 foreign` style reason text.
- [x] Keep existing team score behavior and pure Chinese discount.

### Task 4: Verification

**Files:**
- Existing tests.

- [x] Run `python3 -m unittest discover -v`.
- [x] Run `python3 -m py_compile live_project_fetcher.py score_project.py web_app.py request_watcher.py`.
- [x] Run `node --check web/app.js`.
