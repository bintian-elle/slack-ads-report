# KOL event ingestion

This is an **opt-in KOL-only change**. Robot (`slack-ads-report.service`) is not
modified/restarted. Daily metrics remain Eastern 02:00. Existing poll timer remains
for retry/recovery; its lightweight ExecCondition skips heavy imports when idle.
If daily holds task.lock, event arrivals stay durable and do not spawn repeated
heavy poll processes; the receiver checks again after the lock becomes available.

## Required existing Slack configuration

- KOL app `A0C2JJX96KW`, Socket Mode enabled, bot event `message.channels` saved.
- KOL App-Level Token: `connections:write`; bot: existing `channels:read` and
  `channels:history`, already a member of the single configured public channel.
- `KOL_TRACKER_SLACK_BOT_TOKEN`, `KOL_TRACKER_SLACK_APP_TOKEN`,
  `KOL_TRACKER_SLACK_CHANNEL_ID` in the existing `.env`. Never fall back to Robot.
- Optional `KOL_TRACKER_SLACK_APP_ID` changes the expected paired app explicitly.
  Other-channel events are acknowledged and discarded, never stored or processed.

## Verify, then opt in (only when deployment is authorized)

```bash
cd /home/ubuntu/work/bintian/slack-ads-report
.venv-kol/bin/python -m pip install -r requirements-kol-tracker.txt
.venv-kol/bin/python -m unittest discover -s tests -q
.venv-kol/bin/python kol_slack_socket.py --probe --seconds 20
bash deploy/install_kol_socket.sh
journalctl -u kol-tracker-socket.service -u kol-tracker-poll.service -f
```

Probe connects temporarily, checks the socket app against the bot app, reports only
counts, and neither sends Slack messages nor wakes/writes Sheets. A hello confirms
connectivity, **not message subscription delivery**. A naturally arriving event in the
target channel verifies delivery. Do not run two production receivers for the same
app: Slack distributes events between connections rather than broadcasting them.

## Processing and recovery

- Receiver: SQLite FULL/WAL commit, then ack; only then wake worker. No provider
  metric calls or Sheets work on the socket thread. Duplicate event IDs are ignored.
- Worker: only dirty-thread context goes through the existing parser; enqueue jobs
  durably before marking that thread consumed. Concurrent arrivals increment its
  generation, so completing an earlier snapshot cannot erase them.
- Insert-before-fetch and existing exact-post identity protection/manual fields stay
  unchanged. Edits can retry changed inputs; a conflicting existing code remains a
  conflict rather than silently overwriting a human-entered code.
- Startup/reconnect: resume history pagination over retained roots and re-read retained
  threads once (reply edits do not necessarily change `latest_reply`). Five pages per
  worker invocation, persistent cursor and backoff; not every five minutes when idle.
  New events continue to be accepted during reconciliation.
- A missing old parent is hydrated on demand. Recovery cannot retrieve content Slack
  has deleted or made inaccessible. No claim of unconditional exactly-once remote
  writes: native row identity checks and readback make crash replays idempotent.
- Recovery threads are withheld from parsing until their last page is committed,
  preventing a root edit from being processed without an earlier code reply.
- SQLite retains current context for the configured retention (default 90 days),
  event IDs for 7 days. No full-sheet snapshots are added.

## Rollback (KOL only)

Stop `kol-tracker-events.path` and `kol-tracker-socket.service`; remove only the
`slack-events.enabled` marker under KOL state. Old polling resumes automatically;
retain SQLite, live jobs, caches and checkpoints. The drop-in condition returns true
when the marker is absent. Do not restart Robot. Existing install_kol_tracker.sh can
still update daily/poll units; this drop-in persists across those updates.

## Acceptance boundaries

Offline tests exercise new messages, old-thread replies, edits, duplicate/reordered
delivery, persistence failure, restart, paginated recovery, in-flight arrivals,
unchanged completed inputs, changed inputs, channel isolation and idle skipping.
Live probe does not fabricate Slack events or write the production Sheet. Real
end-to-end write acceptance requires an authorized genuine launch event after opt-in.

## 2026-11-11 acceptance evidence

- Existing production idle poll journal: 5.953 CPU seconds for one round (rounds
  resuming metrics are not comparable idle samples).
- Local simulated 288 empty conditions: 0.17–0.18 CPU seconds total, zero Slack API
  calls. This measures only the skip path, not the resident socket's total daily CPU.
- Local FULL/WAL ingest then ACK, 100 fixture events: median 0.091 ms, max 0.529 ms;
  excludes real Slack transit and downstream metric/Sheet processing latency.
- Actual new receiver, temporary directory on ANC: paired KOL hello verified;
  20.08 s elapsed / 0.1716 CPU seconds. No Slack sends, no Sheets writes. Old-thread
  read returned HTTP 200 / ok=true using existing permissions.
- No target-channel messages arrived in either 20-second live probe, so live event
  subscription delivery and end-to-end Sheet latency remain **not yet measured**.
- Independent review found partial recovery-thread consumption, unbounded initial
  handshake wait, and caller-relative installation paths. Each was reproduced by
  a failing test and fixed; tests also cover busy-daily skipping and metric-only hold.
- No production file replacement, receiver/timer switch, env edit or Robot restart.
  Probe verified Robot remained active with the same PID throughout.
