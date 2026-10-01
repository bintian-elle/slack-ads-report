# Meta KOL Tracker read-only audit

## One-time authorized writer

Status and Followers are user-owned fields. Never overwrite any existing
Status, including blanks, T0, T1, re-testing, testing, or paused.
Only newly inserted rows receive an initial Status: effectively ACTIVE
exact-matched ads produce testing, exclusively paused ads produce paused, and
unmatched or other states remain blank. `normalize_tracker_status.py` does not
write any existing Status cells. Followers remain manually maintained.

`update_meta_tracker.py` is an independent entry point. By default it only
generates a write plan. `--apply` explicitly enables writes to the original
Meta tab: J:S for exact Creative Ad Code matches, J1 update date in
America/Chicago, and the audited missing Lorenzo Love post. Column A is now
derived from distinct destinations of effectively ACTIVE exact-matched ads;
no-active rows retain their historical location. Newly inserted content gets
the earliest matched ad creation date in the account timezone in C; existing
Launch Dates remain unchanged. Other existing identity/manual fields are
preserved. No-match, shared-ad, and no-Insights
rows are skipped. Matched metric updates use account-local yesterday and
the existing website-purchase/all-clicks definitions.

```sh
.venv/bin/python update_meta_tracker.py
.venv/bin/python update_meta_tracker.py --apply
```

It saves private pre-write cell metadata and plans under
`data/processed/kol_tracker_writer/`, checks the sheet again before writing,
sends one atomic Sheets batch, and reads back values and formats. Existing
metric formulas within J:S are replaced by API totals as authorized; formulas
outside J:S are preserved. Undefined zero-denominator ratios become blank.
The new row's unknown business fields remain blank; Slack announcement time
and Ad Name date fragments are not used as launch dates. New-row formatting and
validation are copied without copying another creator's content or status.

This one-time writer requires the previously audited local
`data/processed/kol_tracker_audit/slack_3months/report.json` as Lorenzo's Slack
source. Do not expect a fresh Git clone to contain that private file. It is
not a production poller. Automatic discovery, deployment, and timers remain
unimplemented; rerunning the writer updates J1 but does not schedule it.

## Read-only audits and EC2 staging

### TikTok Slack-sourced new rows

`add_tiktok_tracker_rows.py` is a separate entry point; it does not change the
deployed app or Meta writer. Default mode reads Slack and the TikTok tab, then
saves a private insertion plan. `--cached` previews the previously saved
three-month sources and cannot write. `--slack-days` defaults to 7 (maximum 100).

```sh
.venv/bin/python add_tiktok_tracker_rows.py --cached
.venv/bin/python add_tiktok_tracker_rows.py --slack-days 7
# Explicitly authorized execution only:
.venv/bin/python add_tiktok_tracker_rows.py --slack-days 7 --apply
```

Only marked KOL live announcements and uniquely attributable thread replies
are accepted. New rows require a Creator, TikTok content URL, and Spark code.
Codes without links may reconcile existing rows but cannot create new rows.
Deduplication uses exact normalized codes and post IDs/short-link paths, never
Creator names alone. Short URLs are not claimed to be resolved to video IDs.
Conflicts are held for review; existing missing links and manual Status cells
are not overwritten. Unmarked code replies for multi-content threads are not
guessed. The Slack reader covers recent roots and their replies, not recent
replies to older roots; this is not yet a complete production polling cursor.

The insertion writer inserts below the current creator table rather than
overwriting pre-seeded briefs below it. It copies only formats and validation,
backs up native cells, checks concurrent edits, sends one atomic batch, and
verifies the resulting values/native structure without retrying writes.
Current TikTok Token cannot read `/ad/get/` or `/tt_video/info/`; consequently
new Launch Date, Status, and H:M metrics are deliberately blank pending exact
API identity validation. Future verified insertion enrichment must initialize
only new Status (enabled=testing, paused=paused); it must not modify any existing
Status, including blanks. This entry point does not schedule EC2 tasks or
update lifetime metrics/J1/Meta headers.

The audit scripts below remain read-only. Automatic 5-minute polling state
and production scheduling are NOT implemented. Do not enable cron/systemd
expecting the audit scripts to update the sheet.

Two additional independent diagnostics are available:

- `audit_madison_ad_identity.py`: reads the current Madison row, recent ad
  creatives, and per-ad insights. Exact Tracker code matches are verified with
  `creative.branded_content.instagram_boost_post_access_token`. Creator-name
  candidates alone are not accepted as verified totals.
- `audit_slack_creator_history.py`: reads the July 1, 2026-to-now Slack window,
  current Tracker, and full account ad inventory; matches codes directly to
  creatives. `--cached` reprocesses saved sources without network requests.
  The start date is fixed for this historical audit, not a polling cursor.

EC2 staging should use a separate checkout and virtual environment. Replace
the placeholders with existing absolute paths; never print credential contents:

```sh
git clone --branch codex/kol-tracker-audit --single-branch \
  https://github.com/bintian-elle/slack-ads-report.git /home/ubuntu/kol-tracker-audit
cd /home/ubuntu/kol-tracker-audit
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
install -m 600 /ABSOLUTE/EXISTING/PROJECT/.env .env
mkdir -p credentials
install -m 600 /ABSOLUTE/EXISTING/PROJECT/credentials/google-service-account.json \
  credentials/google-service-account.json
.venv/bin/python -m unittest tests.test_creator_tracker
.venv/bin/python audit_slack_creator_history.py
```

Use a fresh directory: if `/home/ubuntu/kol-tracker-audit` already exists, stop
and inspect it before reusing it. Edit only this checkout's `.env` to supply
the tracker settings. If `GOOGLE_SERVICE_ACCOUNT_FILE` points elsewhere,
update it here to the copied credential. Do not restart the deployed service
or change its environment/dependencies. Private output contains Slack messages
and partnership authorization codes; never commit or publish it. Meta access
tokens are not printed, but code-bearing creative responses should also be
treated as private. Historical numeric equality is not established.

`sync_creator_tracker.py` is an independent entry point. It does not import
`app.py`, `config.py`, or the deployed service modules, and requires no changes
to them or to the installed dependencies. It has no remote write path and does
not send Slack messages. Do not deploy a writer until discrepancies are resolved.

Configuration comes from the repository `.env`:

- `KOL_TRACKER_GOOGLE_SHEETS_LINK`: existing Tracker URL.
- `KOL_TRACKER_SLACK_CHANNEL_ID`: KOL channel ID.
- Existing `META_ACCESS_TOKEN`, `META_AD_ACCOUNT_ID`, `SLACK_BOT_TOKEN`, and
  `GOOGLE_SERVICE_ACCOUNT_FILE` (default `credentials/google-service-account.json`).
- Optional `KOL_TRACKER_SLACK_BOT_TOKEN`: independent KOL bot token, preferred
  over `KOL_TRACKER_SLACK_TOKEN` and the old `SLACK_BOT_TOKEN` fallback.
  The public KOL channel requires `channels:history`; `channels:read` alone is
  insufficient. `KOL_TRACKER_SLACK_APP_TOKEN` is for Socket Mode, not history reads.
- Optional `KOL_TRACKER_META_API_VERSION`: defaults to existing app's `v23.0`.
- Optional `KOL_TRACKER_START_DATE`: explicit cumulative reporting start;
  defaults to `2025-01-01`. Earlier ad history is outside this audit window.

Run locally, without changing the deployed program:

```sh
.venv/bin/python sync_creator_tracker.py
```

Use `--slack-days 30` to request the last rolling month of channel messages.

Default reporting end is yesterday in the Meta account timezone. To compare with
a specific manual update, specify its exact date; the sheet's header alone is
not proof that every row was updated to that date:

```sh
.venv/bin/python sync_creator_tracker.py --end-date 2026-09-15
```

Private outputs are under ignored `data/processed/kol_tracker_audit/`:

- `snapshot.json`: sheet metadata/values, Meta ads/media/insights, recent Slack
  messages, retrieval time and errors. No access tokens or pagination URLs.
- `report.json`: identity mappings, ambiguous/shared ads, candidate new rows,
  unresolved announcements and reporting parameters.
- `comparison.csv`: each existing J:S metric alongside the API value and delta.

Reprocess a snapshot without making API requests:

```sh
.venv/bin/python sync_creator_tracker.py \
  --snapshot data/processed/kol_tracker_audit/snapshot.json \
  --purchase-type omni --clicks-type link \
  --output-dir data/processed/kol_tracker_audit/omni_link
```

To refetch only insights for a different cutoff (retaining the original sheet
and identity snapshot), add `--refresh-insights --end-date YYYY-MM-DD` and use a
separate output directory. The report retains the original sheet retrieval time.

Optional `--mapping-file` accepts a local reviewed identity mapping:

```json
{"by_post":{"SOURCE_SHORTCODE":{"approved":true,"ad_ids":["ACTUAL_AD_ID"],"note":"Reason the ads belong to this source content"}}}
```

Use this only for independently reviewed identities, not to force metric matches.
The script validates IDs against the account's ad inventory. Repeated sheet codes
or shared ad assignments still prevent a passing audit. Ads' Instagram permalinks
can differ from the creator's original published link; a different permalink
alone does not prove there is new content. Ad Name candidates are narrowed by
original launch month/day when that selects a single advertised media ID, but
remain candidates until reviewed.

Metrics are ratios of totals, never averages of ad-level ROAS/CPC/CTR. Website
purchase/ATC and all clicks are defaults; omni purchases and link clicks are
alternative diagnostics. CTR uses a fraction (0.02 = 2%). Zero-denominator ratios
are unknown, not zero. Currency and attribution settings are included in the
report. Rounded values are compared with half the displayed unit as tolerance.

Identity: exact Instagram shortcode/media mapping is verified; Ad Name matches
are candidates requiring review. Different posts under the same creator are not
automatically merged. Never assign identity because metric values happen to match.

New-content rules: extract creator + IG link + code from each numbered live-content
announcement, excluding YouTube/TikTok links. Check both post and normalized code
against the whole Meta sheet. Existing posts stay existing; conflicting codes
need review; genuinely new posts produce local proposals. The announcement time
is not a paid launch date, and business fields remain unfilled in the preview.

Slack coverage includes channel roots from the last rolling seven days and their
replies. Recent replies to older roots are not covered; reply permission failures
are reported. A token unable to read history produces a failed audit, not an empty
channel conclusion. Parser misses are retained as unresolved items for review.

Exit codes: 0 only when all target metrics and identities pass; 2 for discrepancies,
missing values, ambiguous mappings or Slack errors; 1 for a failed mandatory read.
Passing an audit does not deploy anything or enable any writes.
# TikTok provisional Ad Name metrics matching

## Production supersedes the audit-only limitations below

Production deployment now uses `kol_tracker.py poll|daily|check` and
`deploy/install_kol_tracker.sh`. See `KOL_TRACKER_DEPLOY.md` for current commands.
There is no dependency on ignored historical snapshots, and no hardcoded
Lorenzo row insertion in the daily Meta writer. Generic Slack parsing and
native deduplicated insertion support both platforms. The production TikTok
writer reads N bindings, skips unbound paused rows and binds only unambiguous
new name/date groups with no cross-row ad claims. Earlier audit scripts remain
for inspection, not production scheduling. Old paragraphs saying polling is
not implemented describe earlier stages and are superseded by this section.

## Cross-verified writes and combined daily entrypoint

Latest user policy: stop investigating unmatched historical paused rows. The
41 unresolved rows in the saved post-update TikTok snapshot all have manual
Status `paused` (not API-confirmed effective status). A complete new marked
Slack TikTok announcement does not require any Ad ID/API metric match to be
inserted. Creator, Code and Post Link are Slack-sourced; when a verified API
date is unavailable, Launch Date is the corresponding content announcement's
calendar date in America/Chicago. A reply that merely supplies a missing code
does not replace that content's announcement date. Existing dates and Status
are never rewritten; unknown new Status is blank, not falsely set to testing.
Metrics and N's Ad IDs remain blank until a binding is verified. TikTok new-row
verification includes A:AC to preserve existing N bindings when rows shift.
99 tests pass. Automatic polling/deployment is still separate, not enabled.

`record_tiktok_ad_ids.py` reverse-checks original metrics against dated TikTok
reports and records only supported Ad ID bindings in N (text, newline-separated
for multi-ad groups). It scans the 30 cutoff dates September 1–30, 2026 with
start date December 1, 2025, compares nonzero Spend and Views at display
precision, and checks same-creator candidate support and shared-ad conflicts.
It rejects duplicated historical codes, changed identity fields, conflicting N
values and constrained targets. Historical scan found no additional supported
bindings among unresolved rows; previously verified 24 bindings may be recorded.
It does not modify A:M or update remaining metrics. These bindings remain
cross-verified inference, not exact API authorization-code identity proof.

The user authorized the auxiliary cross-verification workflow. On October 1,
24 TikTok rows were written using a fresh lifetime API report and H1 updated.
41 rows were preserved (40 unresolved and one identity changed since audit).
Emily, Sadie and AmyMarietta are multi-ad groups: Spend, Purchase, Views and
Hook Rate use API totals; their ROAS and Reach remain unchanged because neither
rounded single-ad ROAS nor additive Reach is an accurate group aggregate.
Historical Spend plus at least one other metric must agree to establish a
single-ad binding; group bindings require Spend and Views agreement. Dates
alone never override contradictory metrics. Code and A:E identity anchor the
binding across row moves. Source evidence remains provisional, not exact
Ad Code-to-Ad ID verification.

Readback initially flagged row 2 totals recalculating. The totals' formulas
were not edited. Verification now allows unchanged formulas to recalculate
and separately compares native user-entered values, formats and validations.

`run_kol_tracker.py` provides a separate Meta + TikTok daily entrypoint. It
defaults to preview; `--apply` enables writes. It does not change the deployed
application or install schedules. 93 tests pass.

This is NOT yet a complete EC2 deployment package: daily cross-verification
currently needs ignored private audit state files, and the Meta writer needs
the audited Lorenzo source. Copying Git alone is insufficient. Generic Meta
Slack insertion and five-minute polling are not implemented. TikTok Slack-only
insertion is a separate script; new rows without approved metric bindings stay
unmatched. Do not schedule the combined daily command until the private state
and full Slack workflow have been packaged and tested on EC2.

User approved Ad Name matching while additional TikTok permissions await review.
`update_tiktok_tracker.py` is independent of the deployed application. Default
execution reads the native TikTok tab and `/report/integrated/get/` with
`query_lifetime=true`, and saves an ignored private preview. `--apply` explicitly
enables H:M metric updates plus H1's Chicago update date. A:G, unmatched rows,
row 2 totals, formats, and other tabs are not modified. It does not add rows.

The complete Spark/Partnership identity segment is normalized and compared with
Creator; this is provisional name evidence, not verified Ad Code/post identity.
Repeated Creator rows, multiple matching ads, and missing metrics are skipped.
Reach is never summed across ads; undefined Hook Rate is blank, not zero.
Hook Rate is 2-second video views divided by impressions. Purchase and ROAS use
TikTok's `complete_payment` and `complete_payment_roas` reporting metrics.
The live read-only preview on October 1 found 298 ads: 33 proposed row updates,
9 no-name matches, 16 multiple-ad matches, and 7 repeated-Creator rows skipped.
No Sheet changes were made during that preview. 89 unit tests pass.
