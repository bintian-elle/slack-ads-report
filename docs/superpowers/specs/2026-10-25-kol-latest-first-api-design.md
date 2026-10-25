# KOL latest-first queries and pending separation

## Intent and approved approach

Update the existing Python KOL automation so newer launches get useful complete
rows earlier, without repeatedly reading the same creator pages or ad inventory.
Keep the existing metric definitions, native cell protections, daily schedule,
pause grace/weekly-refresh policy and Robot isolation.

The selected approach is grouped Business Discovery pagination followed by
latest-first, post-scoped completion using the saved counters and ID mappings.
Per-row independent pagination would repeat requests; fetching every historical
post regardless of tracker targets would spend unnecessary quota. Neither is used.

## Ordering and reuse

- Prioritize the latest launch dates when scheduling creators and eligible post
  updates; keep existing status/pause eligibility rules. Unknown dates sort last.
- Instagram already returns recent media first. Match all relevant posts from a
  creator on each page, persist the next cursor, and reuse pages/counters for the
  current reporting cycle. Stop once targets are found or the launch boundary is
  passed; never restart an unfinished walk just because a batch ended.
- After each bounded Views batch, prioritize completing the latest matched posts
  using existing per-post ID writers. A missing Views field does not block other
  fields or other posts. Success is recorded only after native write/readback.
- Reuse verified creator aliases, exact media/ad mappings and one canonical ad
  inventory per account/run. Same-day successful counters are not re-requested
  solely because another field failed or a new Slack post arrived.
- Explicitly unavailable outcomes do not retry until relevant inputs change or
  the existing refresh policy permits it. Only transient failures resume.

## Pending versus failure

- Queued, not-yet-attempted, quota-deferred and retryable transport outcomes stay
  in checkpoints/logs, not in `Reason for data update failure`.
- Do not write derivative CPM/CPE failures merely because their source metric is
  still pending. Preserve existing metrics while waiting.
- Permanent missing identity/permission/post/required-field outcomes may be shown
  in English. Preserve genuine manual comments and independent permanent errors.
- Recognize the precise legacy automated `remaining requests queued; ...` text
  so stale pending clauses can be removed, without treating arbitrary text as an
  automated note. Other tabs use the same pending-versus-failure policy.

## Verification and scope

Offline tests must prove latest-first ordering, grouped page reuse, cursor resume,
same-day partial success reuse, canonical inventory reuse, pending-note omission,
permanent-note retention and manual-cell preservation. No live metric requests,
Sheet writes or Robot changes are required for implementation/testing. Existing
unrelated weekly-report files remain untouched. Deployment is a separate final
step after tests and review; do not trigger a full daily run for verification.
