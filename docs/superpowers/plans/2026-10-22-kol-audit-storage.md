# KOL audit storage reduction

User-approved scope: remove whole-sheet disk backups, retain durable resume
state/caches and short logs, clean existing duplicate backups, push/deploy only
KOL, verify released disk space and leave Robot untouched.

Implementation:
- Removed before/after snapshot writes in all eight production KOL writer paths.
- Kept in-memory concurrency checks, formula/manual-field protections and
  post-write native readback comparisons unchanged.
- Timestamped run artifacts are now bounded count/row/reason summaries. State
  and cache files outside runs continue using the unchanged atomic serializer.
- Seven-day retention removes only valid timestamped run directories under
  the worker lock. Unknown/manual directories and symlinks are excluded.
- One-time backup cleanup is dry-run by default, locks workers and deletes only
  whitelisted snapshot names in valid timestamped run directories. It never
  traverses symlinks or cleans sibling checkpoint/cache files.

Verification: 292 offline tests pass. Independent read-only code review found
no critical/important issues; deletion and retention tests also passed in the
review. Production/deployment and Robot checks remain the main agent's work.
Remote dry-run: 2,086 snapshots, 10,016,618,882 logical bytes, no deletions.

Deployment: pause only KOL timers, allow current KOL work to finish, verify state
fingerprints before/after cleanup, restore only KOL timers. Compare Robot PID
and env hash and report actual runs directory and disk-space change. No manual
daily update or provider probe is part of this operation.
