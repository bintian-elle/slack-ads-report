# Immediate Slack KOL Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate metrics immediately after Slack launch discovery in all five automated tabs, without recurring full-table polling.

**Architecture:** Resolve and parse a shared Slack snapshot into exact platform/post identities. Persist pending metric jobs separately from Slack cursors; invoke existing metric writers with explicit target identity sets. Daily writers retain their current full-table default.

**Tech Stack:** Existing Python/requests, Sheets native API, platform APIs, systemd.

**Spec:** `docs/superpowers/specs/2026-10-20-live-kol-metrics.md`

## Global Constraints

- Existing five-minute poll and 02:00 America/New_York daily timer; no Robot restart.
- Preserve manual values, formulas, native dropdowns; no duplicate post identities.
- English failure reasons, no basis labels, never invent absent metrics.
- TikTok 3–7 second spacing; stop/back off on 403/429; reuse same-day successful results.
- No new dependencies, no credential logging, no inferred cross-creator platform association.

## Review Focus

- Restart between insert and fetch: pending work survives cursor advancement.
- Repeated creator posts and manual rows: bind exact post, not just Creator.
- Tracking links: validate each redirect hop; never fetch private/unapproved hosts.
- One tab fails: continue other tabs and preserve pending work.
- Shared Ad IDs: targeted refresh still checks conflicts against every row.

### Task 1: Safe link resolution and launch identity normalization

**Files:** Create `kol_link_resolution.py`; modify Slack parsers; test `tests/test_kol_link_resolution.py`.

**Interfaces:** `resolve_post_link(url, session=None) -> str` returns a canonical
official post URL or raises a sanitized error. HTTPS tracking/short-link hosts
are explicitly allowlisted; maximum five redirects, no implicit requests redirect.

- [ ] Write failing tests: Hannah mxficus 302 resolves to TikTok video `7698432185451302175`; unknown/private redirect is rejected before fetch; 403/429 stops; wrong-platform destination is not assigned.
- [ ] Run `.venv/bin/python -m unittest tests.test_kol_link_resolution -q`; expect failures.
- [ ] Implement resolver, cache exact URL bindings in private state, and platform-fenced parsing. Existing manual links need not be rewritten to resolve metrics.
- [ ] Run focused tests; expect PASS. Commit resolver and tests.

### Task 2: Targeted writers without changing daily defaults

**Files:** Modify `update_meta_tracker.py`, `kol_tracker.py`, `kol_organic_meta_totals.py`, `kol_organic_tiktok.py`, `kol_organic_youtube.py`; add targeted-writer tests.

**Interfaces:** Metric entry points accept optional `target_keys=None`; None means
the existing daily whole-table behavior. Explicit sets select exact post/code
keys with original row coordinates preserved. Return successful keys and failure
reasons for the queue; retain complete global identity-conflict checks.

- [ ] Write failing tests that two posts by one Creator update only the selected post; unrelated metrics/reasons remain unchanged; shared ads are rejected even when only one claimant is selected.
- [ ] Run targeted tests; expect failures.
- [ ] Add scoped selection to all five writers; use current-time cutoff for immediate Ads reads, preserve daily cutoff behavior; only query bound Ad IDs where possible.
- [ ] Test public/Method A combination, TikTok cache/backoff and YouTube protected formulas. Run full unittest suite; expect PASS. Commit.

### Task 3: Durable insert-then-fetch orchestration for all tabs

**Files:** Create `kol_live_updates.py`; modify `kol_tracker.py`, Organic Slack pollers; add `tests/test_kol_live_updates.py`.

**Interfaces:** `poll(env, output, apply)` persists jobs keyed by book/tab/post in
`live-metric-jobs.json`, schedules new/recent unprocessed Slack events, calls
targeted writers, removes completed jobs, and retains failed jobs with retry time.
Organic TikTok gets identity-only insertion before immediate public metric read.

- [ ] Write failing tests: insert followed by metrics in same poll; crash after insert recovers without duplicate; failures survive restart; successful jobs do not recur; independent failures do not block other tabs.
- [ ] Run focused tests; expect failures.
- [ ] Implement shared launch snapshot, ordinary/formal notices, exact dedup, durable queue and bounded retries. Backfill missing metrics for recent manual rows such as Hannah without duplicate insertion. No YouTube insertion without its own creator/video binding.
- [ ] Run focused and full tests; expect PASS. Commit.

### Task 4: Live verification and deployment

**Files:** Update `KOL_TRACKER_DEPLOY.md` and `ANC_EC2_DEPLOY.md`.

- [ ] Preview recent Slack targets and verify nutrition/Eva remain unique; resolve Hannah's TikTok link and verify any YouTube candidate belongs to its own creator.
- [ ] Pause only KOL timers, await running KOL services, apply scoped updates and native readback checks. Record exact rows and failures.
- [ ] Run full tests on local and EC2, push main, fast-forward EC2; restore KOL timers, verify five-minute poll and Eastern 02:00 daily timer plus unchanged Robot PID.
- [ ] Report actual updated rows and remaining API limitations; do not claim missing YouTube data was fetched.
