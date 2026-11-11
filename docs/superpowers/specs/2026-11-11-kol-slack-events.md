# Independent KOL Slack events

Approved direction: an independent KOL Socket Mode receiver; never modify or restart Robot.

Use only the configured KOL channel, bot and app tokens. Receive message, thread reply,
and edit events. Persist an inbox transaction before acknowledging; deduplicate event IDs.
Retain message revisions and thread context in SQLite. A serialized worker reuses existing
launch parsing, insertion, metric retries and task.lock. Existing posts stay deduplicated;
an edited launch can add missing code/link information without rewriting manual fields.

Normal idle operation performs no history reads. On initial migration/reconnect/restart,
reconcile channel history over the existing retention window (to discover old roots with
new replies and edits), then retrieve changed/uncached threads. Persist pagination progress,
and mark recovery complete only after every page/thread is durably ingested. A bounded
reconciliation is unavoidable: Slack history does not provide a durable event replay log.
Do not promise recovery of deleted messages or content beyond Slack retention.

Keep daily metrics and five-minute retries, but in event mode the poll command consumes
the inbox/cache, never rescans Slack. Receiver remains separate from the potentially slow
Sheets/API worker. Event arrivals wake a KOL-only worker; periodic fallback drains retries.
Raw events/message context stay private, not in per-run logs. Apply normal retention cleanup.

Alternative considered: incremental polling only, smaller change but recurring overhead and
five-minute latency. Chosen: Socket Mode plus durable recovery, to minimize idle work.

Acceptance: new roots, old-root replies, edits, duplicate/reordered delivery, restart before
ack/after write, network failure and paginated reconnect. Compare parsed jobs and idempotent
effects with existing rules; benchmark idle request counts and CPU. Real probes read only;
no Slack messages or Sheet changes during implementation acceptance.
