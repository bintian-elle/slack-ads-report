# KOL latest-first API implementation plan

> **For agentic workers:** Use superpowers:executing-plans for the user's explicitly requested inline implementation.

**Goal:** Latest eligible posts first, reused account pages and inventory, no pending messages in failure cells.
**Architecture:** Keep grouped Business Discovery and existing per-post writers. Add shared ordering/note helpers and a canonical per-run account inventory; reuse existing durable page and row checkpoints.
**Tech Stack:** Python unittest, requests, existing Sheets native writers.
**Spec:** docs/superpowers/specs/2026-10-25-kol-latest-first-api-design.md

## Global constraints
- Stop only KOL timers/services; never restart Robot.
- No live metric API calls or Sheet updates for verification.
- Preserve manual status, fees, notes, formulas and unrelated weekly-report files.
- Keep pause grace/weekly refresh and existing metric definitions.

## Review focus
- Mixed permanent and temporary errors: retain permanent clause only.
- Same creator, many rows: one account walk, no row-specific restart.
- Daily refresh versus same-day retry: successful fields cached, incomplete fields resumed.
- Unknown dates and duplicates: deterministic order, one post owner.
- Empty inventory versus failed inventory: do not cache an incomplete page collection as complete.

### Task 1: Pending-note separation — complete
- [ ] Add tests for pending-only, mixed, legacy automated prefixes, and manual comments.
- [ ] Implement shared display-reason filtering without removing checkpoint/retry reasons.
- [ ] Apply to Meta Views/engagement and generic tracker failure writers; keep raw outcomes for retry classification.
- [ ] Run full unittest suite.

### Task 2: Latest-first, canonical inventory reuse — complete
- [ ] Add failing tests for creator and eligible-row descending launch dates, unknown dates last.
- [ ] Implement shared launch conversion and scheduling order while preserving pause eligibility.
- [ ] Add failing test proving the coordinator and paid-interaction writer reuse the same complete account inventory.
- [ ] Implement canonical complete-inventory fields and per-run cache; preserve runtime page cache on restart.
- [ ] Run full suite.

### Task 3: Partial-success and page reuse — complete
- [ ] Test same-day successful Views are reused during an incomplete phase and new Slack arrivals.
- [ ] Reuse matching successful snapshots even if another post/field is pending, validating current bindings.
- [ ] Confirm cursor checkpoint tests still pass; no reset on ordinary continuation.
- [ ] Review full changes and run full suite, diff checks and compilation offline.
- [ ] Report implementation results and leave KOL tasks stopped unless explicitly asked to restore/deploy.

## Execution record

- KOL poll/daily timers and services stopped; Robot PID 3670024 unchanged.
- RED/GREEN observed for pending-only/mixed/legacy/manual notes, latest-first
  creator/row ordering, canonical inventory reuse, partial-phase seeding and
  reduced-target cursor resume. Verified usernames persist separately from Views.
- Independent read-only review found one important issue: TikTok connection
  failure presentation. Reproduced in a failing test, fixed classification and
  presentation; non-retryable HTTP 403 remains visible.
- Offline full suite: 348 tests passed; compile checks passed using a permitted
  temporary bytecode cache. No metric APIs, Sheet writes, push or
  deployment performed in this implementation turn. Unrelated weekly files left
  untouched.
- Boundary decision: retain the established status/pause priority, with newest
  launch first inside each eligible class; never alter manual Sheet state.
- Views turns use a five-request default (configurable 1–10), not a total page
  cap. This leaves invocation budget for completing the newest posts' ID metrics;
  remaining pages keep their cursors. Same-cycle successful fields seed retries,
  and a reduced remaining target set reuses its unfinished superset walk.
