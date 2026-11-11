# KOL Slack Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace repeated KOL Slack scans with durable, independent Socket Mode ingestion.
**Architecture:** SQLite inbox and versioned message cache, lightweight receiver, existing
serialized KOL writer. Recovery pagination is resumable and runs only on connection gaps.
**Tech Stack:** Python, sqlite3, Slack SDK, existing requests/Sheets adapters, systemd.
**Spec:** docs/superpowers/specs/2026-11-11-kol-slack-events.md

## Global Constraints
- No Robot changes, restarts or shared socket connection; only configured KOL channel.
- No real Slack sends or Sheet writes in acceptance; preserve existing parsing/manual data.
- Persist before ack; idempotent retries; daily metrics and checkpoints remain intact.

## Review Focus
- Event arrival during recovery must not be overwritten by an older history page.
- An old thread with a fresh code reply needs its old link and creator context.
- An edit after a completed job must not create a duplicate or replay unchanged metrics.
- A crash cannot advance recovery watermarks or lose accepted events.
- Token/app mismatch must fail closed, not consume Robot events.

### Task 1: Durable inbox and recovery
**Files:** kol_slack_events.py, tests/test_kol_slack_events.py
**Interfaces:** EventStore.ingest(payload); EventStore.context(); EventStore.recover(fetch).
- [x] Write tests for ack ordering, dedup, edits, old replies, pagination/restart/races.
- [x] Run targeted unittest suite; verify missing-feature failures.
- [x] Implement SQLite transactions, scoped ingestion, revision merge and recovery cursors.
- [x] Run tests and measure idle CPU/request calls with local fixtures.

### Task 2: Worker integration
**Files:** kol_live_updates.py, kol_tracker.py, tests/test_kol_slack_events.py
**Interfaces:** event_snapshot(env,state,now) -> messages, oldest; poll consumes dirty roots.
- [x] Test parser output, completed-job edits and failure retry without history.
- [x] Implement event snapshot and dirty-root completion after durable job enqueue.
- [x] Run targeted and full tests; preserve legacy polling by default.

### Task 3: Receiver and operational acceptance
**Files:** kol_slack_socket.py, deploy/install_kol_socket.sh, requirements-kol-tracker.txt,
docs/KOL_SOCKET_MODE.md
- [x] Test startup isolation, reconnect recovery scheduling, ingest/ack and wake behavior.
- [x] Implement receiver, KOL-only systemd service/path worker and explicit opt-in marker.
- [x] Run full tests and read-only Socket Mode hello probe; record limits of live acceptance.
- [x] Review complete diff independently. No production switch or push until requested.

## Progress
- Preflight: auth/history permissions and target channel membership verified read-only.
- Preflight: apps.connections.open HTTP 200, ok=true. Subscription delivery still needs
  observed target-channel traffic; configuration cannot be proved by bot scope alone.
- Ruling: use current checkout and leave unrelated weekly/Biceek changes untouched, honoring
  the user's established single-repository workflow; no automatic shared-branch push.
- Final: independent review by review_kol_socket; three Important issues fixed with
  regression tests RED→GREEN (incomplete thread context, handshake deadline/readiness,
  caller-relative installer). Busy-daily and held/cooldown retry tests also RED→GREEN.
- Live acceptance: actual new receiver hello paired to KOL App; existing thread read
  HTTP 200/ok=true; target event traffic absent during probe. No real writes performed.
- Environment: local legacy .venv interpreter link is broken; tests used system Python
  with installed dependencies (Python 3.9 EOL/LibreSSL warnings), plus isolated tests on
  ANC using .venv-kol. No dependency/environment repair performed on shared projects.
