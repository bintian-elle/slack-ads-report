# KOL transient-only continuation

User-approved goal: only transient failures remain in automatic continuation;
unavailable rows keep accurate English reasons and end the current cycle.
Clean existing queues offline, preserve successes/caches and Robot, test and
push/deploy only KOL. No manual metric refresh or quota probe.

Policy:
- Retry quota/rate limits, transport timeouts/connections, HTTP 408/429/5xx,
  budget interruptions and concurrent sheet edits. Meta invalid-request codes
  100/110/190/200 are not made retryable merely by a bogus HTTP 500 status.
- Missing links/IDs/exact matches, unavailable counters/posts, stable access
  refusals and protected/schema targets are unavailable this reporting cycle.
- Keep success `cycle` distinct from `unavailable_cycle`; daily checks may
  reconsider a row next cycle. Changed identity inputs rearm it immediately.
- Deduplicate posts before updating observation state/backoff. Preserve source
  math, partial usable writes, native readback and manual protections.
- Live unavailable jobs move to a separate diagnostic map, not completed or
  pending; unchanged announcements cannot reenqueue them.
- Migration reads only existing logs/state, preserves cached responses and all
  successful checkpoints, retains explicitly transient/in-flight/unattempted
  work and removes known terminal-only stage retries.

Review focus: empty set subclasses retaining outcome metadata; mixed permanent
and temporary field failures; cooldown/budget early returns; same post on two
rows with differing statuses; migration preserving cached data and completed
state; blocked rows never counted as metric success.

Tests: six initial regression cases failed on old code, then passed. Extended
policy/coordinator/migration cases cover terminal stages and mixed-field errors.
Full suite currently 301 tests passing; production migration/deployment follows
independent code review.
