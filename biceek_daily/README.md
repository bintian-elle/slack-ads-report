# Independent Biceek daily report

Deploy this directory alone to `/home/ubuntu/services/biceek-daily-report/`.
No existing bot modules are imported or changed. The Python interpreter may
reuse the installed Bluevua venv read-only (google-auth/requests); never
install or upgrade its dependencies. Data is read through the existing
fixed ANC platform CLIs, explicitly selecting Biceek accounts.

Private `config.json` follows `config.example.json`. Copy Sheets service
credentials into this service's own `credentials/` with mode 0600; directory
0700. Slack can use the authorized MarketingDailyBot identity through its
own private config copy. Never put secrets or the `state/` directory in Git.

The separate timer starts at **09:00 America/New_York**, matching the verified
Bluevua MarketingDailyBot schedule. It refreshes month-to-date data through
yesterday, updates/verifies Biceek's sheet, then posts the agreed bicycle
English/USD report to internal-biceek. Nothing is sent to Bluevua channels.
Metadata/timezones/attribution remain in private evidence, not Slack prose.

```
PYTHON=/home/ubuntu/work/bintian/slack-ads-report/.venv/bin/python
$PYTHON daily.py --mode preview --date 2026-10-28  # API reads; no sheet write/post
$PYTHON daily.py --mode prepare --date 2026-10-28  # sheet write/read-back; no post
bash install.sh
sudo systemctl enable --now biceek-daily-report.timer biceek-daily-report-retry.timer biceek-daily-report-watchdog.timer biceek-daily-report-alerts.timer
systemctl list-timers --all 'biceek-daily-report*'
```

Keep today's confirmed manual post in its date's state (`status:sent`, Slack
`ts`) before activation. Do not replay it. Successful dates remain deduplicated.
Network-uncertain posts stop for manual reconciliation; the current bot has
no history permission, so an unknown outcome must never be automatically
resent. Read-confirmed API errors and unavailable/partial sources fail before
writing. Failures privately notify Bintian (U0BN7GWSX0S); repeated alerts are
deduplicated. Fifteen-minute retry/alert/watchdog timers are separate from
Bluevua; the watchdog checks for a confirmed delivery after 10:00 Eastern.

Month tabs must use `YY Mon - Budget Pacing` and the validated Biceek layout.
Before each new month, create its tab with correct native dates, USD formats,
and approved paid-media budget (E34) and revenue target (F34). When sheet updates are enabled, the job refuses
missing budgets, EUR templates or incompatible layouts and
privately alerts; it never invents or copies budgets. Old-month tabs are not
modified after rollover. October's EUR budget was converted once at 1.1133;
this program never performs FX conversion.

Google/Shopify dates are Pacific, Meta Eastern. Shopify `total_sales` includes
shipping/tax/etc.; no additional test-order filter is applied. Google ROAS
uses purchase-category conversion values; Meta uses the exact website
purchase action, 7d_click + 1d_view, impression report time. Missing rows are
N/A; aggregate spend sums only returned Google/Meta amounts. Supported Google
sheet channels are Search/PMax; a new spending channel requires explicit
layout work and otherwise fails instead of dropping spend.

Configuration isolation is not OS-user isolation: services share ubuntu,
and the read-only interpreter/CLI runtime is installed outside this directory.
No shared credential values, package files, services or schedules are changed.

Stop only these Biceek timers for rollback; preserve state/credentials. This
does not stop the separate pre-existing ANC AI Biceek analysis report.

## 2026-11-03 repair

Daily headline uses Google + Meta spend, Shopify total_sales and orders,
and Shopify revenue / combined spend. MTD totals come directly from the
validated API aggregates, independently of Sheets. Orders are sales-dataset
orders. Advertising attribution remains confined to the channel rows.

Set `pacing_updates_enabled: false` to pause all sheet access/writes until
explicitly requested again. Budget/pacing rows are omitted while paused.
Even when updates are enabled, a missing monthly tab does not block the
report. Other sheet failures still alert; API failures never become zeros.
Successful sends clear stale report errors and retain delivery deduplication.

## Candidate: 2026-11-05 key changes preview

Adds day-over-day >=15% flags for total revenue, orders, spend, overall
ROAS, Google/Meta spend and ROAS. Zero and small-order baselines use
absolute changes. Uses refreshed comparison data, including a separate
previous-month collection on day one. Decomposes revenue into order-volume
and revenue-per-order contributions, spending into channels/Search/PMax,
and channel spend into click volume/CPC. Attribution remains distinct from
Shopify sales; no customer-demand causality is asserted.

22 regression tests pass. Actual 2026-11-04 API preview is saved under
`data/biceek-preview/2026-11-04.txt`. Candidate was executed in remote /tmp
only, without using run/send, changing the live service, Sheets or Slack.
Not yet enabled for scheduled reports or the separate AI analysis report.


## Remote AI preview requirement (2026-11-05)

User rejected deterministic narrative as the final analyst. The remote
AI Biceek must combine verified API metrics with brand/product/market
strategy, current campaign plans and recent verified team discussions.
`ai_analysis_prompt.md` records the required manual preview behavior.
The preview used DailyNative, Biceek business cwd, the existing model and
shared native backend without run_daily/publish/dispatch or Slack sending.
Thread 01a1af07-019e-7db0-b15f-78f95dd9323c, turn
01a1af07-0402-7ef0-beb2-e32aeff73364 completed; final_answer verified.
Final artifact: data/biceek-preview/2026-11-04-ai-biceek.md.
Private aggregate/API and business-context evidence remain in Biceek
.local/daily-reports/outputs/2026-11-05-daily-ai-preview.
No production scheduling or sending changes were made. Future integration
must use the remote AI analysis, never silently fall back to template prose.


## Approved remote-AI schedule (effective 2026-11-06)

The deployed ai_enabled mode uses remote AI Biceek's existing native SDK,
model, business cwd, brand/project sources and recent relevant discussions.
08:25 Eastern prepares a report for the previous native-date day; 09:00
Eastern sends the prepared message only. Generation failures notify Bintian;
there is no deterministic explanation fallback. Pacing remains paused.

Headline, channel and MTD numbers are validated by the controller. AI chooses
up to three material eligible changes and supplies one plain-English sentence
(max 22 words) each. The worker durably records thread/turn IDs; uncertain
starts reconcile only and never start a replacement turn. A fingerprint
binds analysis to its frozen source snapshot; a message hash protects the
prepared send. Missing/invalid AI provenance blocks delivery.

Private config: ai_enabled=true, send_not_before=2026-11-06,
ai_python=/home/ubuntu/work/anc-automation/.venv/bin/python. Both report and
failure notifications honor the start date. The independent alert/watchdog
paths remain enabled. Keep state/ai-*.json and state/snapshot-*.json private.
The older ANC Biceek long report timer is disabled (Biceek enabled=false);
Bluevua/RingConn/Design configuration, timers, source and runtime are untouched.

Rollback: disable only biceek-daily-report-prepare.timer, restore the saved
Biceek program/config and only the biceek key in brand-daily.json; restore
the old Biceek timer if desired. Preserve date states and do not replay sends.


## Detailed reply extension (user approval, 2026-11-05)

The SAME remote AI turn now returns both short `changes` and required
`details`. The worker reads the old ANC brand-daily prompt as long-report
guidance and uses Biceek data, brand/background and current team context.
The main nine-o'clock report remains short; details are posted with
thread_ts equal to the confirmed main message ts, never as another top-level
report. No-change days still require a detailed comment.

Main and reply delivery states are durable and independent. Confirmed
parent delivery resets the retry budget before the reply stage. A definite
reply rejection retries the reply only; unknown reply outcomes stop for
manual review. Re-preparing a sent report cannot reset reply status. The
watchdog checks both parts. Failure notices and all posts retain the
2026-11-06 start guard. No old Biceek long timer is re-enabled.


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

Formal layout (2026-11-06): main message contains the data/pacing header, all eligible colored KPI changes with bold inline Suggested cause/Next step, and confirmed Product & Business Update. Exactly one compact table reply (max 3300 characters) contains Shopify/GA4 comparison, Google/Meta metric deltas, GA4 countries, LP/PDP entrance and organic-social/tagged-influencer/email cohorts; no Sources.
Native tables (2026-11-06): format_version 4 returns structured detail_tables; the controller emits actual Slack table blocks with wrapping disabled, rather than code-block text tables. Tables and concise notes remain one thread reply. Full text fallback and structured payload are fingerprinted before delivery.

Biceek PMax Feed Only campaign 24435460967 is separated from the regular PMax line starting Nov 5, 2026. Pacing uses the existing P/Q actual columns, and month spend includes J/L/N/P exactly once. Feed Only ROAS uses Google-attributed purchase value; missing days remain N/A.

Zero-base highlights (2026-11-13): render ROAS endpoints with two decimals
(e.g. `0.00 → 0.37 (from zero)`) and monetary endpoints in USD with two
decimals. Do not invent relative percentages when the baseline is zero.
Underlying Decimal values retain full precision for calculations/thresholds.
