# Biceek daily report

Deployed 2026-10-29 to `/home/ubuntu/services/biceek-daily-report` on
`EMW3_ANC_AI`. Source/operator guide: `biceek_daily/README.md`.

`biceek-daily-report.timer` runs daily at 09:00 America/New_York, including
DST. Next verified run: October 30 at 09:00 Eastern. It reads Biceek fixed
ANC APIs, updates/verifies the Biceek sheet, then posts English/USD with a
bicycle title to internal-biceek. No follower growth or Slack footnote.

Failures privately notify Bintian U0BN7GWSX0S. Notification delivery was
acknowledged. Retry/watchdog/alert timers are separate and staggered.
Unknown sends require manual reconciliation because the bot lacks history
scope; confirmed sends never repeat. Today's confirmed manual post is seeded.

```bash
ssh EMW3_ANC_AI
systemctl list-timers --all 'biceek-daily-report*' --no-pager
systemctl show biceek-daily-report.service -p Result -p ExecMainStatus
journalctl -u biceek-daily-report.service -n 30 --no-pager
```

All 14 tests and real API/sheet verification passed. Bluevua's 54 protected
files, process PID and restart count remained unchanged; no shared dependencies
were installed/upgraded. Existing ANC AI reports remain unchanged.

Keep private config, credentials and state on EC2; never commit them or remove
sent-date records. The installed interpreter/ANC CLIs are shared read-only;
configuration isolation does not imply OS identity isolation.

Prepare each new month's `YY Mon - Budget Pacing` tab with correct dates and
approved USD budgets. Missing tabs/targets trigger private failure alerts;
the service does not copy or invent budgets. October EUR inputs were converted
once, and subsequent inputs/API figures are USD.


## 2026-11-03 repair

User authorized Google+Meta total spend, Shopify total_sales/orders and
blended ROAS. Deployed daily.py with Orders in headline and API-derived MTD
totals. Missing month no longer blocks sending. Private runtime config now
has pacing_updates_enabled=false; no Sheets reads/writes until user requests
restoration/backfill. Budget/pacing rows are omitted.

17 local and remote regression tests pass, including missing-month delivery,
paused-sheet delivery, MTD totals, and Orders. Real remote prepare 2026-11-02:
Google 150.906330 + Meta 290.43 = 441.336330 USD; Shopify total_sales 573.07,
orders 8, blended ROAS 1.30. MTD spend 873.20, revenue 1496.66, ROAS 1.71.

AI Biceek shared workflow received a brand-specific authorized correction;
shared prompt's final scope restriction now explicitly routes Biceek to that
section. Fixed config identity validation passes. Existing AI schedule/state
were preserved and no model run was replayed. Bluevua main PID 3670024,
NRestarts 0, active after deployment. Protected source/platform credentials
were not changed. Remote private backups: service backups/2026-11-03/.


## 2026-11-05: approved remote AI short body + detailed comment

Effective 2026-11-06, prepare at 08:25 and send at 09:00 America/New_York
(13:25/14:00 UTC on November 6). Send only to internal-biceek. Remote AI
Biceek uses the existing native backend and Biceek business cwd; one turn
produces <=3 short >=15% reasons plus a detailed analysis using the former
long-report prompt, brand materials and recent relevant discussions.
Header/channel/MTD totals remain validated Google+Meta and Shopify USD data.
Detailed analysis posts as a reply to the confirmed main message.

The former anc-automation-daily-biceek.timer is disabled and only the
biceek enabled flag changed to false; other brand configs compare equal.
The independent service now has ai_enabled=true, send_not_before=2026-11-06,
pacing_updates_enabled=false. Parent/reply states and attempt recovery are
separate; unknown sends block, known failures notify Bintian and safely
retry. AI starts/turns are durable and reconcile-only on uncertain starts.
No deterministic explanation fallback. No Slack messages sent today.

41 local and remote tests pass, unit validation and calendars pass, real
remote generation verified a short body plus 527-word detailed analysis.
Bluevua remains active, PID3670024/NRestarts0; its configs/runtime unchanged.
Private rollback backup: service backups/2026-11-05/.


2026-11-05 highlight coverage update: the three-item cap is removed.
All eligible core metrics must be covered exactly once. A shared-cause
group may use `metrics:[...]`; the renderer preserves every metric label
and its own change. Missing/duplicate/ineligible metrics are rejected.
Each explanation remains one short sentence. 45 local/remote tests pass.
Remote AI generated five eligible metrics grouped into four bullets.
The user redirected the preview to a private DM only; sent to Bintian,
D0BSGSDDR41, ts1793939672.479329. No channel preview was posted.
Formal 2026-11-06 schedule/long comment/Bluevua are unchanged.


2026-11-05 final approved color presentation deployed: colored attachment
sidebars plus green/red/orange emoji, with metric/change and explanation
on one line separated by a colon. Revenue/Orders/ROAS follow business
direction; spend defaults to attention unless AI verifies an impact.
All eligible metrics remain covered, including grouped same-cause metrics.
The structured Slack payload is hashed and verified before sending;
unknown-send reconciliation checks normalized text and attachment colors.
Details remain a reply. 50 local and remote tests pass; fresh incremental
review found no critical/important issues. Actual five-metric AI evidence
renders four inline colored highlights. No channel messages sent during
this update. First send remains 2026-11-06 09:00 Eastern, preparation
08:25; failure DM and paused pacing unchanged. Bluevua PID3670024,
NRestarts0, active; other brand configs compared equal.


## 2026-11-06 service-context runtime fix

Root cause: PrivateTmp isolated the Codex daemon socket target under
/tmp/codex-daemon-1000, although its symlink in .codex was visible.
SSH-based manual validation had not exercised that systemd namespace.
Reproduced via transient systemd units: socket false with PrivateTmp only,
true with BindReadOnlyPaths=/tmp/codex-daemon-1000. Added that read-only bind
only to Biceek prepare/retry services, preserving PrivateTmp. Installer
now persists this setting. No AI source, shared runtime or Bluevua changes.

Real biceek-daily-report-prepare.service then connected successfully and
started thread 01a1b11f-24e7-77b1-8b03-719e3fac8a32, turn
01a1b11f-2d2a-7ed2-bf4e-b75fb96bb733. 08:25 Eastern is preparation;
09:00 Eastern remains actual channel delivery. Failure DMs can occur
during preparation; no early channel post occurred.

Fix verified 2026-11-06 08:52 Eastern: actual systemd preparation service
exited success, AI status complete, report prepared with detailed reply.
No channel send occurred before 09:00. Bluevua remained active, same PID
and NRestarts0. This closes the runtime-namespace regression with real
service-context generation, rather than SSH-only validation.


## 2026-11-06 pacing restored (explicit user authorization)

November advertising-only budget: USD556.65/day x30 = USD16699.50.
Approved revenue goal: USD48094.56 at blended ROAS2.88. KOL remains
excluded and blank. D3:D32 are advertising budget inputs; E3:E32 = D,
not 80% of D. The previous 20% other-marketing allocation is not applied
to this ad-only budget. No channel shares were invented.

Actuals Nov1–5 fetched through fixed Biceek Google/Meta/Shopify CLIs,
validated against account monthly totals, written and fully read back.
MTD spend2564.13/revenue3390.57/ROAS1.32, remaining daily budget565.41,
spend pacing92.13%, revenue pacing42.30%. Future actuals stay blank.

pacing_updates_enabled=true. AI prepare now updates/verifies the sheet
from its frozen validated source rows and merges T118/T122/T123 into the
colored main message header. The model itself remains read-only. Preview
mode does not write; a missing future tab keeps daily reporting available
with N/A pacing. Other sheet failures produce sheet_update alerts.
Today's sent report was not regenerated or resent; resumed pacing appears
from the next scheduled report (Nov7, 09:00 Eastern). Detailed thread,
all >=15% highlights/colors, failure DM and native-runtime bind unchanged.
54 local/remote tests and real sheet/header integration passed. Bluevua
PID3670024/NRestarts0 stayed unchanged.

## 2026-11-13 zero-base display repair

Deployed the metric-aware zero-base formatting fix to the independent Biceek
service. Corrected the existing 11/11 report posted on 11/12 in place; no new
post, source-value change or Bluevua change. 61 local and remote tests pass.
