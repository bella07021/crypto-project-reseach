# Request Queue Watcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let public users submit new crypto project lookup requests while RootData scraping runs from the owner's local machine.

**Architecture:** Vercel exposes a lightweight request API that writes pending requests to a GitHub JSONL file and reads completed scores from the existing GitHub score history. A local Python watcher polls GitHub every few seconds, claims pending requests, runs the existing scoring pipeline locally, writes the score history, and marks each request done or failed.

**Tech Stack:** Python stdlib HTTP server and GitHub Contents API, existing `score_project.py` scoring pipeline, vanilla JS frontend.

---

### Task 1: Queue Storage

**Files:**
- Modify: `web_app.py`
- Test: `tests/test_web_app.py`

- [ ] Add request queue read/write helpers using GitHub Contents API with `data/project_requests.jsonl` as the default path.
- [ ] Add tests for deduplicating an active request by normalized RootData URL.

### Task 2: Public Request API

**Files:**
- Modify: `web_app.py`
- Modify: `web/app.js`
- Test: `tests/test_web_app.py`

- [ ] Add `POST /api/request` to create or return a pending request.
- [ ] Add `GET /api/requests` for recent queue status.
- [ ] Update frontend submit flow to queue requests on hosted mode and show pending status.

### Task 3: Local Watcher

**Files:**
- Create: `request_watcher.py`
- Test: `tests/test_request_watcher.py`

- [ ] Poll GitHub requests every 10 seconds.
- [ ] Claim one pending request at a time by writing `processing`.
- [ ] Run `build_assessment()` locally and append GitHub history.
- [ ] Mark request `done` with score metadata or `failed` with error text.

### Task 4: Verification

**Files:**
- Existing tests.

- [ ] Run `python3 -m unittest discover -v`.
- [ ] Run `node --test tests/rootdata_browser.test.js`.
- [ ] Run `python3 -m py_compile web_app.py score_project.py live_project_fetcher.py request_watcher.py`.
