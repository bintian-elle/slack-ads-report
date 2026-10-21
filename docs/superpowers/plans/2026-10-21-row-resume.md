# KOL row-at-a-time updates

Goal: complete and verify each post/row before starting the next, with durable
daily checkpoints and automatic continuation, without touching Robot.

Approved specification: the user's confirmed workflow in this conversation.

Constraints: all five existing automated tabs; preserve exact identity checks,
manual statuses/formulas/fees, failure notes, and original metric definitions.
No production fetch/test today. Restore timers with a durable metric hold until
the next 02:00 America/New_York boundary. Slack insertion remains enabled.

Implementation tasks:
1. Add a reusable row coordinator with identity-based checkpoints scoped by
   spreadsheet/tab/reporting window. Prioritize API-confirmed active, unknown,
   recent paused, then weekly-due old paused. Resume only unfinished rows;
   save completion only after the existing writer's readback succeeds.
2. Route daily and continuation through the coordinator; make live dispatch
   single-post and checkpoint after each result. Cache shared TikTok inventory
   and report within the invocation, not across different reporting days.
3. Replace organic Meta's column lanes in production with per-row requests;
   retain its existing source calculations and native writer. Keep transport
   response checkpoints for partial rows and suppress full-stage restart.
4. Add a KOL-only pause-until-next-daily control. Poll still inserts Slack
   identities but makes no metric-provider calls during the hold. Include
   insertion enrichment in that restriction. Do not clear pending work.
5. Offline full test suite, fresh review, push main, deploy only KOL, set hold,
   restore poll/daily timers and verify Robot PID/env remain unchanged.

Review focus: reordered rows; crash after verification; unknown manual status;
quota on one of several ads belonging to one row; hold respected by insertion
and auto-resume; protected formula writes never count as complete.

Verification ledger:
- Queue regression tests exercised restart/sort, partial failure, quota stop,
  reactivation, observation-based pause grace and next-window retry reset.
- Live regression tests exercised single-post insert/write sequencing and hold.
- All five daily routes use run_kind/run_rows; bounded 20-row turns, Meta tab
  request turns capped at 20 inside the existing shared 40-request guard.
- Actual writes are paced by seven seconds; shared TikTok account reads are
  invocation-cached. Formula/manual-target safety remains in existing writers.
- Independent read-only review found daily/live cutoff confusion and repeated
  TikTok full-report queries. Both received failing regression tests and fixes.
- Reviewer declined production/deployment validation; main agent will verify
  these separately without provider metric fetches or Robot restart.
- Full local offline suite: 288 tests pass; git diff --check clean.

Operational control: `kol_tracker.py hold-until-daily --apply` atomically writes
a KOL-only metric hold until the next 02:00 America/New_York boundary. Slack
continues inserting identities and queues metrics; insertion enrichment,
daily runs, and continuation respect the same hold. It does not change env,
clear progress, or pause/restart Robot. Do not rerun this command tomorrow
unless intentionally deferring metrics for another day.
