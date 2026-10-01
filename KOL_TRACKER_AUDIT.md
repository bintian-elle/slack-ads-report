# Meta KOL Tracker read-only audit

## Current implementation and EC2 staging

This revision is read-only. Automatic row insertion, daily metric writes,
5-minute polling state, and production scheduling are NOT implemented. Do not
enable cron/systemd expecting these scripts to update the sheet.

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
