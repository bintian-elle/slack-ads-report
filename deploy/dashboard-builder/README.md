# Dashboard Builder weekly publication templates

These systemd files are **TEMPLATES ONLY — NOT INSTALLED OR ENABLED**. They have not been deployed to ANC or any production machine. There is **no local custom schedule, cron, launchd job, Codex automation or heartbeat** for this workflow. Scheduling is reserved for a future remote-host deployment. No Slack or email is sent.

The current user requirement supersedes the earlier 01:00 discussion: start every **Tuesday at 04:00 America/New_York**, then make bounded hourly completion attempts at 05:00–09:00 Eastern. `America/New_York` handles daylight saving automatically; do not replace it with a fixed UTC offset. No local follow-up remains active. The 04:00 start and retry slots below describe the future remote timer only.

## Publication contract

The frontend Node entry point is `scripts/pregenerate-dashboard-overviews.ts` in the Dashboard repository. A normal scheduled invocation is idempotent: if the completed target week's validated snapshot is already published, it exits without fetching APIs or republishing. New target weeks force fresh source reads before validation. `--force` on the Node entry point is a manual republish override and is intentionally **not** used by the timer.

The scheduled run prepares the most recently completed Tuesday–Monday. It must keep the previous published snapshot visible while source fetching or report generation runs. The generated overview is committed only after the new project's source identity, complete ranges, period closure and report validation pass. In production, a transaction upserts the latest generated overview for that brand; it does not accumulate dated publication-history rows. A source error, partial data, still-open account-local date, generation failure or timeout must leave the old published snapshot visible. An hourly retry uses the same idempotent entry point; no old snapshot or good input cache is deleted beforehand.

The scheduled clock is Eastern; each data source retains its actual account timezone. At 04:00 Eastern the preceding Monday has ended in Los Angeles, but source API publication/attribution delays can still occur. Unknown descriptive zones must not be silently called Los Angeles or complete. The publisher can use a conservative latest-world-timezone boundary for unverified zones, with the later retry slots providing coverage. This is why a trigger time is not a promise to publish incomplete data at that exact minute.

## Existing insight database and retention

The remote service template opts into `AI_DASHBOARD_PUBLICATION_STORE=postgres`. The publication-store adapter belongs to the Dashboard repository and uses `AI_DASHBOARD_PUBLICATION_DATABASE_URL` when supplied, otherwise the existing `KLAVIYO_DATABASE_URL` insight-database connection. Supply that variable through the remote deployment environment or its existing protected environment file; do not copy a credential value into this repository, the unit template or a command example. Strict TLS remains enabled. The template also names `AI_DASHBOARD_PUBLICATION_DATABASE_CA_FILE=/etc/emw3/supabase-prod-ca-2021.crt`; install the verified public CA at the reviewed deployment path rather than disabling certificate or hostname verification.

Production publications use the separate `dashboard_overview_publications` table in that existing database. The table migration is an **explicit setup step**, never part of the scheduled command. The user's later instruction separately authorizes initial migration and import of the three already-generated local overview payloads into the existing insight database before remote deployment. That bounded initialization has completed using one transaction for DDL, the three rows and payload-hash readback. Row-level security was then enabled on the new table only, with no browser policies; verification is recorded in `docs/dashboard-builder/publication-database-seed-2026-10-26.md` in this reporting repository. Preparing the timer templates does not itself create schema, write the database or install a job.

The generated-publication table retains the **latest validated overview per brand**, using an atomic upsert. Until a replacement succeeds, the prior committed row remains the active overview. This latest-only rule applies to generated overview publications, not source evidence: dated raw/normalized marketing caches remain available for PoP, YoY, completeness checks and source audit. Do not delete those source-period caches when a new overview is published.

The publication code defaults to the **file** provider. A database-backed local preview can be explicitly selected after the user-authorized database initialization is verified; that provider selection is separate from scheduling. Neither file preview nor a database-backed preview creates a local custom timer. The service template selects postgres for the future remote installation.

## Source refresh support

All three local collection programs honor `AI_DASHBOARD_FORCE_REFRESH=1`:

- `scripts/cache-builder-channels.py` — Bluevua official account reports.
- `scripts/export-dashboard-builder-data.cjs` — Bluevua read-only Dashboard database exports.
- `scripts/cache-builder-projects.py` — Biceek / RingConn fixed ANC read-only report CLIs; existing `--force` remains available when calling this Python collector directly.

A seven-row daily cache captured Monday is not a completed Tuesday–Monday snapshot. Stable closed-period reuse checks the **source capture time** against the reporting period end in the account timezone. Source adapters now emit `periodClosed` and `fetchedAfterPeriodEnd` for verified timezones; unknown descriptive account zones do not get a false `true` flag. The normalized ANC cache retains its source `executed_at` capture time and does not stamp an old raw report as newly fetched.

Failed refreshes preserve an existing ready cache. Failure metadata is written under `.refresh-errors/` and the old `fetchedAt` remains unchanged, so the publisher can distinguish stale input from newly verified data. The weekly published snapshot is independently retained until the entire new candidate passes validation.

## Host paths and transport

Before a future remote installation, review the service's `WorkingDirectory`, `AI_DASHBOARD_REPORT_ROOT`, Node runtime `PATH`, user/group and environment file. Current template paths refer to existing ANC checkout locations and must be adjusted when deployment paths differ. `WorkingDirectory` must be the frontend Dashboard repository root, so the relative Node command `scripts/pregenerate-dashboard-overviews.ts` resolves there; `AI_DASHBOARD_REPORT_ROOT` must point to the separate reporting repository containing the Python collectors. These are deployment configuration paths, not assumptions about the local Mac checkout.

`cache-builder-projects.py` defaults to `AI_DASHBOARD_ANC_TRANSPORT=ssh` and uses the existing local Mac SSH alias `EMW3_ANC_AI`. On ANC itself, the template explicitly sets `AI_DASHBOARD_ANC_TRANSPORT=local`, which invokes the same fixed, whitelisted CLI through the local Python interpreter instead of depending on a Mac-only SSH alias. Both transports preserve the fixed account allowlist, read-only operation whitelist and referenced-export checks. Credentials remain managed by the installed ANC CLI; no credential values belong in these unit files.

No new privileged credential, marketing source mutation, remote configuration edit or deployment is performed by this template preparation. Existing data-tool authentication refresh may update that tool's normal private auth cache during a later authorized read.

## Pre-install verification

On the future Linux deployment host, inspect both calendar expressions with `systemd-analyze calendar`, verify checkout paths and Node version, provide the existing protected database connection, and verify that the separately reviewed publication-table migration has already been applied before enabling the service. A `--cached-only` publication check is not a deployment or a migration command. No enabling/install command is run by preparing these templates.

The local regression suite covers the Monday-complete-row trap, fresh/historical force bypass, ET-versus-Pacific closure, unknown zones, capture-time retention and preserving good cache input after a forced refresh fails:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_builder_cache_policy tests.test_builder_projects tests.test_builder_tiktok
node --test tests/builder-cache-policy.test.cjs
```
